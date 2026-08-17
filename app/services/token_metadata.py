from __future__ import annotations

import logging
import time
from typing import Any

from app.fetchers.coingecko import CoinGeckoClient, ResolvedToken
from app.fetchers.unlock_fetcher import UnlockFetcher
from app.models.entities import SymbolConfig, TokenMetadata
from app.models.repositories import Repository

logger = logging.getLogger(__name__)


class TokenMetadataService:
    """CoinGecko 解析合约并持久化到 token_metadata 表，后续优先读库。"""

    def __init__(
        self,
        client: CoinGeckoClient,
        unlock_fetcher: UnlockFetcher | None,
        repo: Repository,
        cfg: dict[str, Any] | None = None,
    ):
        self.client = client
        self.unlock_fetcher = unlock_fetcher
        self.repo = repo
        self.cfg = cfg or {}
        self.enabled = bool(self.cfg.get("enabled", True))
        self.refresh_hours = float(self.cfg.get("metadata_refresh_hours", 24))

    def enrich(self, symbols: list[SymbolConfig]) -> list[SymbolConfig]:
        if not self.enabled:
            return symbols
        return [self._enrich_one(sym) for sym in symbols]

    def resolve_one(self, base_asset: str) -> ResolvedToken | None:
        return self.client.resolve_from_symbol(base_asset)

    def _enrich_one(self, sym: SymbolConfig) -> SymbolConfig:
        if sym.token_contract and sym.coingecko_id and sym.chain:
            self._touch_metadata(sym)
            return sym

        cached = self.repo.load_token_metadata(sym.base_asset)
        if cached and cached.token_contract and self._is_fresh(cached):
            logger.debug("使用持久化 metadata: %s@%s", sym.base_asset, cached.chain)
            return self._apply_metadata(sym, cached)

        if cached and cached.token_contract and not self._is_fresh(cached):
            sym = self._apply_metadata(sym, cached)

        resolved = self.client.resolve_from_symbol(sym.base_asset)
        if not resolved:
            if cached and cached.token_contract:
                return self._apply_metadata(sym, cached)
            logger.debug("CoinGecko 未找到 %s 的合约信息", sym.base_asset)
            return sym

        meta = self._persist_resolved(sym, resolved)
        sym = meta.to_symbol_config()

        logger.info(
            "CoinGecko 解析 %s → %s@%s (%s) [已持久化]",
            sym.base_asset,
            (sym.token_contract or "")[:10] + "...",
            sym.chain,
            sym.coingecko_id,
        )

        if self.unlock_fetcher and sym.coingecko_id:
            try:
                count = self.unlock_fetcher.sync_for_coin(sym.symbol, sym.coingecko_id)
                if count:
                    logger.info("已同步 %s 解锁/供应信息 %s 条", sym.symbol, count)
            except Exception:
                logger.exception("Unlock sync failed for %s", sym.symbol)

        return sym

    def _persist_resolved(self, sym: SymbolConfig, resolved: ResolvedToken) -> TokenMetadata:
        now = int(time.time())
        existing = self.repo.load_token_metadata(sym.base_asset)
        meta = TokenMetadata(
            base_asset=sym.base_asset.upper(),
            symbol=sym.symbol,
            coingecko_id=resolved.coingecko_id,
            chain=resolved.chain,
            platform=resolved.platform,
            token_contract=resolved.token_contract,
            name=resolved.name,
            market_cap_rank=resolved.market_cap_rank,
            resolved_at=existing.resolved_at if existing else now,
            updated_at=now,
        )
        self.repo.save_token_metadata(meta)
        return meta

    def _touch_metadata(self, sym: SymbolConfig) -> None:
        existing = self.repo.load_token_metadata(sym.base_asset)
        if not existing:
            now = int(time.time())
            self.repo.save_token_metadata(
                TokenMetadata(
                    base_asset=sym.base_asset.upper(),
                    symbol=sym.symbol,
                    coingecko_id=sym.coingecko_id,
                    chain=sym.chain,
                    token_contract=sym.token_contract,
                    resolved_at=now,
                    updated_at=now,
                )
            )
            return
        existing.symbol = sym.symbol
        existing.updated_at = int(time.time())
        if sym.coingecko_id:
            existing.coingecko_id = sym.coingecko_id
        if sym.chain:
            existing.chain = sym.chain
        if sym.token_contract:
            existing.token_contract = sym.token_contract
        self.repo.save_token_metadata(existing)

    def _apply_metadata(self, sym: SymbolConfig, meta: TokenMetadata) -> SymbolConfig:
        sym.coingecko_id = sym.coingecko_id or meta.coingecko_id
        sym.chain = sym.chain or meta.chain
        sym.token_contract = sym.token_contract or meta.token_contract
        meta.symbol = sym.symbol
        meta.updated_at = int(time.time())
        self.repo.save_token_metadata(meta)
        return sym

    def _is_fresh(self, meta: TokenMetadata) -> bool:
        if not meta.updated_at:
            return False
        age_hours = (time.time() - meta.updated_at) / 3600
        return age_hours < self.refresh_hours
