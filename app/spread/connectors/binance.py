"""币安 U 本位合约 (USDⓈ-M Futures) 代币化股票永续标记价连接器。

⚠️ 数据源说明 (P0 实测结论):
  - 币安代币化股票永续在 fapi 上 contractType = 'TRADIFI_PERPETUAL',
    underlyingType = EQUITY / KR_EQUITY / HK_EQUITY, 符号形如 AAPLUSDT。
  - 其 `<sym>@markPrice@1s` WebSocket 流在实测中不稳定/不推送 (连 BTC 亦然),
    而 REST `premiumIndex` 稳定返回 markPrice / indexPrice / lastFundingRate。
  故这里采用 REST 轮询 premiumIndex 批量端点 (一次请求取全量, 本地过滤),
  这也正好是我们要的「标记价格」权威来源。若日后 WS 恢复稳定, 可换回推送。
"""
from __future__ import annotations

import asyncio
import logging
import time

import aiohttp

from ..models import Quote
from .base import Connector

log = logging.getLogger(__name__)

# 不带 symbol 参数 -> 返回全部合约的 mark/index/funding 数组, 一次请求搞定
_PREMIUM_INDEX = "https://fapi.binance.com/fapi/v1/premiumIndex"
_FUNDING_INFO = "https://fapi.binance.com/fapi/v1/fundingInfo"
_DEFAULT_FUNDING_INTERVAL_HOURS = 8.0


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _funding_interval_hours(row: dict) -> float | None:
    hours = _f(row.get("fundingIntervalHours"))
    return hours if hours and hours > 0 else None


def _funding_per_hour(rate: str | float | None, interval_hours: float | None) -> float | None:
    """Binance lastFundingRate 是每个 funding 周期的费率; 前端统一展示 1h 费率。"""
    funding = _f(rate)
    if funding is None:
        return None
    hours = interval_hours or _DEFAULT_FUNDING_INTERVAL_HOURS
    if hours <= 0:
        hours = _DEFAULT_FUNDING_INTERVAL_HOURS
    return funding / hours


class BinanceConnector(Connector):
    name = "binance"

    async def _fetch_funding_intervals(self, session: aiohttp.ClientSession,
                                       wanted: set[str]) -> dict[str, float]:
        """返回 symbol -> funding 周期小时数。fundingInfo 不一定列全量, 缺失时按 8h 兜底。"""
        out = {sym: _DEFAULT_FUNDING_INTERVAL_HOURS for sym in wanted}
        try:
            async with session.get(
                _FUNDING_INFO, timeout=aiohttp.ClientTimeout(total=8)
            ) as resp:
                data = await resp.json(content_type=None)
        except Exception as e:  # noqa: BLE001 - 周期获取失败不影响行情轮询
            log.warning("[binance] fundingInfo fetch failed: %r; use %.0fh fallback",
                        e, _DEFAULT_FUNDING_INTERVAL_HOURS)
            return out

        for row in data or []:
            sym = row.get("symbol")
            if sym not in wanted:
                continue
            hours = _funding_interval_hours(row)
            if hours is not None:
                out[sym] = hours
        log.info("[binance] funding intervals: %s",
                 ", ".join(f"{sym}={out[sym]:g}h" for sym in sorted(out)))
        return out

    async def _stream(self) -> None:
        symbols = self.registry.symbols_for(self.name)   # canonical -> AAPLUSDT
        if not symbols:
            raise RuntimeError("币安无已配置符号, 请检查 config.yaml symbols[].binance")
        wanted = set(symbols.values())
        poll = float(self.venue_cfg.get("poll_seconds", 2))

        async with aiohttp.ClientSession() as session:
            funding_intervals = await self._fetch_funding_intervals(session, wanted)
            log.info("[binance] polling premiumIndex for %d symbols every %.1fs",
                     len(wanted), poll)
            while True:
                try:
                    async with session.get(
                        _PREMIUM_INDEX, timeout=aiohttp.ClientTimeout(total=8)
                    ) as resp:
                        data = await resp.json()
                except Exception as e:  # noqa: BLE001 - 单次拉取失败不重置连接
                    log.debug("[binance] premiumIndex fetch failed: %r", e)
                    await asyncio.sleep(poll)
                    continue

                now = time.time()
                for item in data:
                    sym = item.get("symbol")
                    if sym not in wanted:
                        continue
                    canonical = self.registry.canonical_for(self.name, sym)
                    if canonical is None:
                        continue
                    try:
                        mark = float(item["markPrice"])
                    except (KeyError, TypeError, ValueError):
                        continue
                    index = item.get("indexPrice")
                    funding = _funding_per_hour(
                        item.get("lastFundingRate"), funding_intervals.get(sym))
                    await self.emit(Quote(
                        venue=self.name,
                        canonical=canonical,
                        venue_symbol=sym,
                        mark_px=mark,
                        oracle_px=float(index) if index not in (None, "") else None,
                        funding=funding,
                        quote_ccy="USDT",
                        ts_source=item.get("time", now * 1000) / 1000.0,
                        ts_recv=now,
                    ))
                await asyncio.sleep(poll)
