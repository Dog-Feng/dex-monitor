from __future__ import annotations

import logging
import time
from typing import Any

from app.fetchers.coingecko import CoinGeckoClient
from app.models.entities import SymbolConfig
from app.services.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)


class MarketCapFetcher:
    def __init__(
        self,
        coingecko_client: CoinGeckoClient | None = None,
        rate_limiter: RateLimiter | None = None,
    ):
        self.client = coingecko_client or CoinGeckoClient(
            rate_limiter=rate_limiter or RateLimiter(1.5)
        )
        self._cache: dict[str, tuple[float, float]] = {}
        self.cache_ttl = 3600.0

    def fetch_market_caps(self, symbols: list[SymbolConfig]) -> dict[str, float]:
        ids = [s.coingecko_id for s in symbols if s.coingecko_id]
        if not ids:
            return {}

        now = time.time()
        result: dict[str, float] = {}
        missing_ids: list[str] = []
        id_to_symbol = {s.coingecko_id: s.symbol for s in symbols if s.coingecko_id}

        for cg_id in ids:
            cached = self._cache.get(cg_id)
            if cached and now - cached[0] < self.cache_ttl:
                symbol = id_to_symbol[cg_id]
                result[symbol] = cached[1]
            else:
                missing_ids.append(cg_id)

        if not missing_ids:
            return result

        try:
            caps = self.client.get_market_caps(missing_ids)
            for cg_id, mcap in caps.items():
                self._cache[cg_id] = (now, mcap)
                symbol = id_to_symbol.get(cg_id)
                if symbol:
                    result[symbol] = mcap
        except Exception as exc:
            logger.warning("CoinGecko market cap fetch failed: %s", exc)

        return result
