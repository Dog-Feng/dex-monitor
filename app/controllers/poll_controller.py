from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from app.controllers.alert_controller import AlertController
from app.controllers.detect_controller import DetectController
from app.controllers.explain_controller import ExplainController
from app.fetchers.binance import BinanceFetcher
from app.fetchers.chain.address_watch import AddressWatch
from app.fetchers.chain.rpc_client import RpcClient
from app.fetchers.chain.token_transfer import TokenTransferScanner
from app.fetchers.market_cap import MarketCapFetcher
from app.models.entities import AnomalyType, MetricSnapshot, SymbolConfig
from app.models.repositories import Repository
from app.services.history_buffer import HistoryBuffer
from app.services.symbol_discovery import SymbolDiscovery
from app.services.token_metadata import TokenMetadataService
from app.views.console_view import ConsoleView

logger = logging.getLogger(__name__)


class PollController:
    def __init__(
        self,
        config: dict[str, Any],
        repo: Repository,
        binance: BinanceFetcher,
        market_cap: MarketCapFetcher,
        buffer: HistoryBuffer,
        detect: DetectController,
        explain: ExplainController,
        alert: AlertController,
        view: ConsoleView,
        address_watch: AddressWatch | None = None,
        token_scanner: TokenTransferScanner | None = None,
        discovery: SymbolDiscovery | None = None,
        metadata: TokenMetadataService | None = None,
    ):
        self.config = config
        self.repo = repo
        self.binance = binance
        self.market_cap = market_cap
        self.buffer = buffer
        self.detect = detect
        self.explain = explain
        self.alert = alert
        self.view = view
        self.address_watch = address_watch
        self.token_scanner = token_scanner
        self.discovery = discovery
        self.metadata = metadata
        self._last_mcap_fetch = 0.0
        self._last_l1_scan = 0.0
        self._seeded_symbols: set[str] = set()

    def bootstrap(self) -> None:
        # 启动阶段跳过 CoinGecko 解析，避免限流阻塞 Web 看板就绪
        symbols = self._resolve_active_symbols(enrich_metadata=False)
        self.repo.sync_active_symbols(symbols)
        for sym in symbols:
            if sym.enabled:
                history = self.repo.load_recent_metrics(sym.symbol)
                if history:
                    self.buffer.load(sym.symbol, history)
                    self._seeded_symbols.add(sym.symbol)
                else:
                    self._ensure_history(sym.symbol)

    def run_forever(self) -> None:
        interval = int(self.config.get("poll_interval_seconds", 60))
        logger.info("Polling every %ss", interval)
        while True:
            started = time.time()
            try:
                self.run_once()
            except Exception:
                logger.exception("Poll cycle failed")
            elapsed = time.time() - started
            sleep_for = max(1.0, interval - elapsed)
            time.sleep(sleep_for)

    def run_once(self) -> None:
        symbols = self._resolve_active_symbols()
        if not symbols:
            logger.warning("No symbols to monitor (check discovery or static watchlist)")
            return

        self.repo.sync_active_symbols(symbols)

        self.binance.refresh_ticker_24h(force=False)
        self.repo.set_scan_state(
            "ticker24h",
            json.dumps(self.binance._ticker_24h_pct, ensure_ascii=False),
        )

        for sym in symbols:
            self._ensure_history(sym.symbol)
            snapshot = self.binance.fetch_snapshot(sym.symbol)
            if not snapshot:
                continue
            self._apply_market_cap(sym, snapshot)
            self.repo.insert_metrics(snapshot)
            self.buffer.push(snapshot)

        self._refresh_market_caps(symbols)
        self._run_chain_l1()
        chain_cfg = self.config.get("chain", {})
        if chain_cfg.get("enabled") and chain_cfg.get("l2_mode") == "always":
            self._run_chain_l2(symbols)

        retention = int(self.config.get("sqlite", {}).get("metrics_retention_days", 30))
        removed = self.repo.cleanup_old_metrics(retention)
        if removed:
            logger.info("Cleaned %s old metric rows", removed)

        for sym in symbols:
            event = self.detect.evaluate(sym.symbol, self.buffer)
            if not event:
                continue

            if (
                self.token_scanner
                and self.config.get("chain", {}).get("enabled")
                and self.config.get("chain", {}).get("l2_mode") == "lazy"
                and event.anomaly_type in (AnomalyType.SURGE.value, AnomalyType.DUMP.value)
            ):
                sym_cfg = self._find_symbol_config(sym.symbol)
                if sym_cfg:
                    new_events = self.token_scanner.scan_symbol(sym_cfg, hours=24)
                    for oc in new_events:
                        self.repo.insert_onchain_event(oc)

            onchain = self.explain.fetch_related_onchain(event)
            unlocks = self.repo.load_unlocks_near(event.symbol, event.detected_ts)
            self.explain.enrich(event, onchain, unlocks)
            event.id = self.repo.insert_anomaly_event(event)

            if self.alert.should_send(event):
                self.alert.send(event)
                self.view.render(event)
            else:
                logger.debug(
                    "Anomaly suppressed by cooldown: %s %s",
                    event.symbol,
                    event.anomaly_type,
                )

    def _ensure_history(self, symbol: str) -> None:
        if symbol in self._seeded_symbols:
            return
        if len(self.buffer.get(symbol)) >= 12:
            self._seeded_symbols.add(symbol)
            return
        self._seed_history(symbol)
        self._seeded_symbols.add(symbol)

    def _resolve_active_symbols(self, enrich_metadata: bool = True) -> list[SymbolConfig]:
        static = self._symbol_configs()
        if self.discovery:
            symbols = self.discovery.resolve(static)
        else:
            symbols = [s for s in static if s.enabled]
        if enrich_metadata and self.metadata:
            symbols = self.metadata.enrich(symbols)
        elif self.metadata:
            symbols = [self._apply_cached_metadata(sym) for sym in symbols]
        return symbols

    def _apply_cached_metadata(self, sym: SymbolConfig) -> SymbolConfig:
        if sym.token_contract and sym.coingecko_id and sym.chain:
            return sym
        cached = self.repo.load_token_metadata(sym.base_asset)
        if not cached:
            return sym
        sym.coingecko_id = sym.coingecko_id or cached.coingecko_id
        sym.chain = sym.chain or cached.chain
        sym.token_contract = sym.token_contract or cached.token_contract
        return sym

    def _seed_history(self, symbol: str) -> None:
        klines = self.binance.fetch_klines(symbol, limit=288)
        if not klines:
            return
        oi = self.binance._fetch_open_interest(symbol)
        funding = self.binance._fetch_funding_rate(symbol)
        whale = self.binance._fetch_whale_ratio(symbol)
        interval_h = self.binance.get_funding_interval_hours(symbol)
        snapshots = self.binance.klines_to_snapshots(
            symbol, klines, oi, funding, whale, interval_h
        )
        for snap in snapshots[:-1]:
            self.buffer.push(snap)
            if not self.repo.metric_exists(symbol, snap.ts):
                self.repo.insert_metrics(snap)
        if snapshots:
            last = snapshots[-1]
            self.buffer.push(last)
            if not self.repo.metric_exists(symbol, last.ts):
                self.repo.insert_metrics(last)

    def _apply_market_cap(self, sym: SymbolConfig, snapshot: MetricSnapshot) -> None:
        if snapshot.market_cap:
            return
        cached = self.market_cap._cache.get(sym.coingecko_id or "")
        if cached:
            mcap = cached[1]
            snapshot.market_cap = mcap
            if mcap > 0:
                oi_usd = snapshot.oi * snapshot.price
                snapshot.oi_mcap_ratio = oi_usd / mcap

    def _refresh_market_caps(self, symbols: list[SymbolConfig]) -> None:
        now = time.time()
        if now - self._last_mcap_fetch < 3600:
            return
        caps = self.market_cap.fetch_market_caps(symbols)
        for sym in symbols:
            mcap = caps.get(sym.symbol)
            if not mcap:
                continue
            latest = self.buffer.latest(sym.symbol)
            if latest:
                latest.market_cap = mcap
                oi_usd = latest.oi * latest.price
                latest.oi_mcap_ratio = oi_usd / mcap if mcap else None
            self.repo.update_market_cap(sym.symbol, mcap, int(now))
        self._last_mcap_fetch = now

    def _run_chain_l1(self) -> None:
        chain_cfg = self.config.get("chain", {})
        if not chain_cfg.get("enabled") or not self.address_watch:
            return
        interval = int(chain_cfg.get("l1_interval_seconds", 300))
        now = time.time()
        if now - self._last_l1_scan < interval:
            return
        events = self.address_watch.scan()
        for event in events:
            self.repo.insert_onchain_event(event)
        self._last_l1_scan = now
        if events:
            logger.info("L1 onchain: ingested %s events", len(events))

    def _run_chain_l2(self, symbols: list[SymbolConfig]) -> None:
        if not self.token_scanner:
            return
        events = self.token_scanner.scan_enabled_symbols(symbols, hours=24)
        for event in events:
            self.repo.insert_onchain_event(event)

    def _symbol_configs(self) -> list[SymbolConfig]:
        result: list[SymbolConfig] = []
        for item in self.config.get("symbols", []):
            result.append(
                SymbolConfig(
                    symbol=item["symbol"],
                    base_asset=item["base_asset"],
                    enabled=item.get("enabled", True),
                    chain=item.get("chain"),
                    token_contract=item.get("token_contract"),
                    coingecko_id=item.get("coingecko_id"),
                )
            )
        return result

    def _find_symbol_config(self, symbol: str) -> SymbolConfig | None:
        db_sym = self.repo.load_symbol(symbol)
        if db_sym:
            return db_sym
        for item in self.config.get("symbols", []):
            if item["symbol"] == symbol:
                return SymbolConfig(
                    symbol=item["symbol"],
                    base_asset=item["base_asset"],
                    enabled=item.get("enabled", True),
                    chain=item.get("chain"),
                    token_contract=item.get("token_contract"),
                    coingecko_id=item.get("coingecko_id"),
                )
        return None
