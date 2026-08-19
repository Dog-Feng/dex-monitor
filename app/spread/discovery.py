"""标的发现: 拉三家永续清单, 求「共同上市」交集, 生成 SymbolMap 列表。

被 app 的每日同步和 scripts/discover.py 复用。市场归属 (US/KR/CN/HK) 取自
币安 fapi 的 underlyingType (EQUITY→US, KR_EQUITY→KR, HK_EQUITY→HK)。

容错: 某家拉取失败/为空则从交集中剔除, 用「可达到的场所」求交集 —— 这样
SoDEX 被区域限制时仍能按 币安∩Hyperliquid 继续同步 (状态里会标明来源)。
"""
from __future__ import annotations

import asyncio
import logging

import aiohttp

from .config import SymbolMap

log = logging.getLogger(__name__)

BINANCE_EXINFO = "https://fapi.binance.com/fapi/v1/exchangeInfo"
HL_INFO = "https://api.hyperliquid.xyz/info"
SODEX_SYMBOLS = "https://mainnet-gw.sodex.dev/api/v1/perps/markets/symbols"

_EQUITY_UNDERLYING = {"EQUITY", "KR_EQUITY", "HK_EQUITY"}
_MARKET_BY_UNDERLYING = {"KR_EQUITY": "KR", "HK_EQUITY": "HK", "EQUITY": "US"}


async def _fetch_binance(session: aiohttp.ClientSession) -> tuple[dict[str, str], dict[str, str]]:
    """返回 (base->symbol, base->market)。仅代币化股票永续 (TRADIFI_PERPETUAL)。"""
    async with session.get(BINANCE_EXINFO) as r:
        data = await r.json()
    catalog, markets = {}, {}
    for s in data.get("symbols", []):
        if (s.get("status") == "TRADING" and s.get("quoteAsset") == "USDT"
                and s.get("underlyingType") in _EQUITY_UNDERLYING):
            base = s["baseAsset"]
            catalog[base] = s["symbol"]
            markets[base] = _MARKET_BY_UNDERLYING.get(s["underlyingType"], "US")
    return catalog, markets


async def _fetch_hyperliquid(session: aiohttp.ClientSession, dex: str = "xyz") -> dict[str, str]:
    """base -> 'xyz:BASE' (承载代币化股票的 builder dex)。"""
    payload: dict = {"type": "meta"}
    if dex:
        payload["dex"] = dex
    async with session.post(HL_INFO, json=payload) as r:
        meta = await r.json()
    out = {}
    for a in meta.get("universe", []) or []:
        full = a.get("name")
        if full:
            out[full.split(":")[-1]] = full
    return out


async def _fetch_sodex(session: aiohttp.ClientSession) -> dict[str, str]:
    """base -> 'BASE-USD'。"""
    async with session.get(SODEX_SYMBOLS, headers={"Accept": "application/json"}) as r:
        body = await r.json()
    data = body.get("data", body) if isinstance(body, dict) else body
    out = {}
    for d in data or []:
        name = d.get("name") or d.get("symbol")
        if name:
            out[name.split("-")[0]] = name
    return out


def build_symbols(catalogs: dict[str, dict[str, str]],
                  markets: dict[str, str] | None = None) -> list[SymbolMap]:
    """对若干场所目录求交集, 生成 SymbolMap 列表 (纯函数, 便于单测)。"""
    markets = markets or {}
    if not catalogs:
        return []
    common = set.intersection(*(set(c) for c in catalogs.values()))
    out: list[SymbolMap] = []
    for base in sorted(common):
        venue_symbols = {v: catalogs[v][base] for v in catalogs}
        out.append(SymbolMap(canonical=base, multiplier=1.0,
                             venue_symbols=venue_symbols, market=markets.get(base, "US")))
    return out


async def discover_symbols(enabled_venues: list[str], min_venues: int = 2,
                           hl_dex: str = "xyz", timeout: float = 15.0
                           ) -> tuple[list[SymbolMap], dict]:
    """拉取并求交集。返回 (symbols, meta)。meta 含 venues(来源)/count/source。"""
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session:
        jobs: dict[str, asyncio.Future] = {}
        if "binance" in enabled_venues:
            jobs["binance"] = asyncio.ensure_future(_fetch_binance(session))
        if "hyperliquid" in enabled_venues:
            jobs["hyperliquid"] = asyncio.ensure_future(_fetch_hyperliquid(session, hl_dex))
        if "sodex" in enabled_venues:
            jobs["sodex"] = asyncio.ensure_future(_fetch_sodex(session))
        results = await asyncio.gather(*jobs.values(), return_exceptions=True)

    catalogs: dict[str, dict[str, str]] = {}
    markets: dict[str, str] = {}
    for venue, res in zip(jobs.keys(), results):
        if isinstance(res, Exception):
            log.warning("discovery: %s 拉取失败: %r", venue, res)
            continue
        if venue == "binance":
            cat, mk = res
            if cat:
                catalogs["binance"] = cat
                markets.update(mk)
        elif res:
            catalogs[venue] = res
        else:
            log.warning("discovery: %s 返回空", venue)

    meta = {"venues": list(catalogs.keys()), "source": "discovery"}
    if len(catalogs) < min_venues:
        log.warning("discovery: 可达场所 %d < min_venues %d, 交集不足",
                    len(catalogs), min_venues)
        return [], meta

    symbols = build_symbols(catalogs, markets)
    meta["count"] = len(symbols)
    return symbols, meta
