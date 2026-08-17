from __future__ import annotations

import logging
import time
from typing import Any

from app.fetchers.binance import BinanceFetcher
from app.models.entities import SymbolConfig

logger = logging.getLogger(__name__)

DEFAULT_EXCLUDE = {
    "USDCUSDT",
    "BUSDUSDT",
    "TUSDUSDT",
    "FDUSDUSDT",
    "USDPUSDT",
}


class SymbolDiscovery:
    """从 Binance 永续 24h ticker 粗筛，再用 5m K 线计算短周期涨跌幅排序。"""

    def __init__(self, binance: BinanceFetcher, discovery_cfg: dict[str, Any]):
        self.binance = binance
        self.cfg = discovery_cfg
        self._cache: list[SymbolConfig] = []
        self._rankings: list[dict[str, Any]] = []
        self._last_refresh = 0.0

    @property
    def enabled(self) -> bool:
        return bool(self.cfg.get("enabled", False))

    @property
    def mode(self) -> str:
        return self.cfg.get("mode", "dynamic")

    @property
    def last_rankings(self) -> list[dict[str, Any]]:
        return list(self._rankings)

    def resolve(self, static_symbols: list[SymbolConfig]) -> list[SymbolConfig]:
        mode = self.mode
        if mode == "static" or not self.enabled:
            return [s for s in static_symbols if s.enabled]

        now = time.time()
        interval = int(self.cfg.get("refresh_interval_seconds", 300))
        if not self._cache or now - self._last_refresh >= interval:
            self._cache = self._discover_from_binance()
            self._last_refresh = now
            logger.info(
                "Discovery refreshed: %s symbols (mode=%s)",
                len(self._cache),
                mode,
            )

        if mode == "hybrid":
            merged: dict[str, SymbolConfig] = {s.symbol: s for s in self._cache}
            for s in static_symbols:
                if s.enabled:
                    merged[s.symbol] = s
            return list(merged.values())

        return self._cache

    def _discover_from_binance(self) -> list[SymbolConfig]:
        tickers = self.binance.fetch_tickers_24hr()
        if not tickers:
            return []

        min_volume = float(self.cfg.get("min_quote_volume_usdt", 5_000_000))
        exclude = set(self.cfg.get("exclude_symbols") or []) | DEFAULT_EXCLUDE
        top_gainers = int(self.cfg.get("top_gainers", 20))
        top_losers = int(self.cfg.get("top_losers", 20))
        min_change_15m = float(self.cfg.get("min_change_15m", 0.03))
        bars_15m = int(self.cfg.get("bars_15m", 3))

        candidates: list[dict[str, Any]] = []
        for row in tickers:
            symbol = row.get("symbol", "")
            if not symbol.endswith("USDT"):
                continue
            if symbol in exclude:
                continue
            quote_volume = float(row.get("quoteVolume") or 0)
            if quote_volume < min_volume:
                continue
            change_24h = float(row.get("priceChangePercent") or 0)
            candidates.append(
                {
                    "symbol": symbol,
                    "change_24h": change_24h,
                    "quote_volume": quote_volume,
                    "last_price": float(row.get("lastPrice") or 0),
                }
            )

        if not candidates:
            return []

        by_24h_gain = sorted(candidates, key=lambda x: x["change_24h"], reverse=True)
        by_24h_loss = sorted(candidates, key=lambda x: x["change_24h"])
        shortlist: dict[str, dict[str, Any]] = {}
        for row in by_24h_gain[:top_gainers]:
            shortlist[row["symbol"]] = row
        for row in by_24h_loss[:top_losers]:
            shortlist[row["symbol"]] = row

        rankings: list[dict[str, Any]] = []
        selected: list[SymbolConfig] = []

        for symbol, row in shortlist.items():
            change_15m = self.binance.fetch_short_term_change(symbol, bars=bars_15m)
            if change_15m is None:
                continue
            item = {
                **row,
                "change_15m": change_15m,
                "base_asset": symbol.replace("USDT", ""),
            }
            rankings.append(item)
            if abs(change_15m) >= min_change_15m:
                selected.append(
                    SymbolConfig(
                        symbol=symbol,
                        base_asset=item["base_asset"],
                        enabled=True,
                    )
                )

        rankings.sort(key=lambda x: x["change_15m"], reverse=True)
        self._rankings = rankings

        if not selected:
            fallback_n = int(self.cfg.get("fallback_top_n", 10))
            for item in rankings[:fallback_n]:
                selected.append(
                    SymbolConfig(
                        symbol=item["symbol"],
                        base_asset=item["base_asset"],
                        enabled=True,
                    )
                )
            logger.info(
                "No symbol met min_change_15m=%.1f%%, fallback to top %s by 15m move",
                min_change_15m * 100,
                fallback_n,
            )

        return selected
