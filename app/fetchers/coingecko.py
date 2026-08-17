from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

import requests

from app.services.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

COINGECKO_API = "https://api.coingecko.com/api/v3"
COINGECKO_PRO_API = "https://pro-api.coingecko.com/api/v3"

# CoinGecko platforms 字段 → 项目内部 chain 名（对应 config chain.rpc）
PLATFORM_TO_CHAIN: dict[str, str] = {
    "base": "base",
    "ethereum": "ethereum",
    "arbitrum-one": "arbitrum",
    "binance-smart-chain": "bsc",
    "polygon-pos": "polygon",
    "optimistic-ethereum": "optimism",
}

DEFAULT_CHAIN_PRIORITY = [
    "binance-smart-chain",
    "base",
    "ethereum",
    "arbitrum-one",
    "polygon-pos",
    "optimistic-ethereum",
]


@dataclass
class CoinGeckoMatch:
    coingecko_id: str
    name: str
    symbol: str
    market_cap_rank: int | None


@dataclass
class ResolvedToken:
    coingecko_id: str
    base_asset: str
    chain: str
    token_contract: str
    platform: str
    name: str
    market_cap_rank: int | None = None


class CoinGeckoClient:
    def __init__(
        self,
        api_key: str = "",
        chain_priority: list[str] | None = None,
        rate_limiter: RateLimiter | None = None,
        cache_ttl: float = 86400.0,
    ):
        self.api_key = api_key.strip()
        self.base_url = COINGECKO_PRO_API if self.api_key else COINGECKO_API
        self.chain_priority = chain_priority or DEFAULT_CHAIN_PRIORITY
        self.rate_limiter = rate_limiter or RateLimiter(1.2)
        self.cache_ttl = cache_ttl
        self.session = requests.Session()
        self._search_cache: dict[str, tuple[float, CoinGeckoMatch | None]] = {}
        self._coin_cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._resolve_cache: dict[str, tuple[float, ResolvedToken | None]] = {}

    def search_by_symbol(self, symbol: str) -> CoinGeckoMatch | None:
        key = symbol.upper()
        cached = self._search_cache.get(key)
        if cached and time.time() - cached[0] < self.cache_ttl:
            return cached[1]

        try:
            data = self._get("/search", {"query": key})
            coins = data.get("coins", [])
            exact = [c for c in coins if (c.get("symbol") or "").upper() == key]
            if not exact:
                result = None
            else:
                best = min(
                    exact,
                    key=lambda c: c.get("market_cap_rank") or 999_999,
                )
                result = CoinGeckoMatch(
                    coingecko_id=best["id"],
                    name=best.get("name", ""),
                    symbol=(best.get("symbol") or key).upper(),
                    market_cap_rank=best.get("market_cap_rank"),
                )
        except Exception as exc:
            logger.warning("CoinGecko search failed for %s: %s", symbol, exc)
            result = None

        self._search_cache[key] = (time.time(), result)
        return result

    def get_coin(self, coin_id: str) -> dict[str, Any] | None:
        cached = self._coin_cache.get(coin_id)
        if cached and time.time() - cached[0] < self.cache_ttl:
            return cached[1]

        try:
            data = self._get(
                f"/coins/{coin_id}",
                {
                    "localization": "false",
                    "tickers": "false",
                    "market_data": "true",
                    "community_data": "false",
                    "developer_data": "false",
                    "sparkline": "false",
                },
            )
        except Exception as exc:
            logger.warning("CoinGecko coin detail failed for %s: %s", coin_id, exc)
            data = None

        if data:
            self._coin_cache[coin_id] = (time.time(), data)
        return data

    def resolve_from_symbol(self, base_asset: str) -> ResolvedToken | None:
        cache_key = base_asset.upper()
        cached = self._resolve_cache.get(cache_key)
        if cached and time.time() - cached[0] < self.cache_ttl:
            return cached[1]

        match = self.search_by_symbol(cache_key)
        if not match:
            self._resolve_cache[cache_key] = (time.time(), None)
            return None

        coin = self.get_coin(match.coingecko_id)
        if not coin:
            self._resolve_cache[cache_key] = (time.time(), None)
            return None

        platforms = coin.get("platforms") or {}
        picked = self._pick_platform(platforms)
        if not picked:
            self._resolve_cache[cache_key] = (time.time(), None)
            return None

        platform, address = picked
        chain = PLATFORM_TO_CHAIN.get(platform, platform)
        result = ResolvedToken(
            coingecko_id=match.coingecko_id,
            base_asset=cache_key,
            chain=chain,
            token_contract=address.lower(),
            platform=platform,
            name=coin.get("name") or match.name,
            market_cap_rank=match.market_cap_rank,
        )
        self._resolve_cache[cache_key] = (time.time(), result)
        return result

    def get_market_caps(self, coin_ids: list[str]) -> dict[str, float]:
        if not coin_ids:
            return {}
        try:
            data = self._get(
                "/coins/markets",
                {
                    "vs_currency": "usd",
                    "ids": ",".join(coin_ids),
                    "order": "market_cap_desc",
                    "per_page": len(coin_ids),
                    "page": 1,
                },
            )
            result: dict[str, float] = {}
            for item in data:
                mcap = float(item.get("market_cap") or 0)
                if mcap > 0:
                    result[item["id"]] = mcap
            return result
        except Exception as exc:
            logger.warning("CoinGecko markets failed: %s", exc)
            return {}

    def _pick_platform(self, platforms: dict[str, str]) -> tuple[str, str] | None:
        normalized = {
            k: (v or "").strip()
            for k, v in platforms.items()
            if v and str(v).strip()
        }
        for platform in self.chain_priority:
            address = normalized.get(platform)
            if address:
                return platform, address
        if normalized:
            platform = next(iter(normalized.keys()))
            return platform, normalized[platform]
        return None

    def _get(self, path: str, params: dict[str, Any]) -> Any:
        self.rate_limiter.wait()
        headers: dict[str, str] = {}
        req_params = dict(params)
        if self.api_key:
            headers["x-cg-pro-api-key"] = self.api_key
        resp = self.session.get(
            f"{self.base_url}{path}",
            params=req_params,
            headers=headers,
            timeout=30,
        )
        if resp.status_code == 429:
            logger.warning("CoinGecko rate limited, sleeping 60s")
            time.sleep(60)
            resp = self.session.get(
                f"{self.base_url}{path}",
                params=req_params,
                headers=headers,
                timeout=30,
            )
        resp.raise_for_status()
        return resp.json()
