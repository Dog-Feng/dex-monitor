"""Hyperliquid (含 HIP-3 builder 永续) 标记价连接器。

WS: wss://api.hyperliquid.xyz/ws
订阅: {"method":"subscribe","subscription":{"type":"activeAssetCtx","coin":"xyz:AAPL"}}
返回: {"channel":"activeAssetCtx","data":{"coin":"xyz:AAPL",
        "ctx":{"markPx":..,"oraclePx":..,"funding":..,"midPx":..,"openInterest":..}}}
HIP-3 builder-dex 的币名带 dex 前缀 (如 "xyz:AAPL")，直接作为 coin 传入即可。
Hyperliquid 会关闭空闲连接, 故额外维持应用层 ping。
"""
from __future__ import annotations

import asyncio
import json
import logging
import time

import websockets

from ..models import Quote
from .base import Connector

log = logging.getLogger(__name__)

_WS = "wss://api.hyperliquid.xyz/ws"
_PING_INTERVAL = 50.0


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


class HyperliquidConnector(Connector):
    name = "hyperliquid"

    async def _ping(self, ws) -> None:
        while True:
            await asyncio.sleep(_PING_INTERVAL)
            await ws.send(json.dumps({"method": "ping"}))

    async def _stream(self) -> None:
        symbols = self.registry.symbols_for(self.name)
        if not symbols:
            raise RuntimeError("Hyperliquid 无已配置符号, 请检查 config.yaml symbols[].hyperliquid")

        async with websockets.connect(_WS, ping_interval=20, ping_timeout=20) as ws:
            for coin in symbols.values():
                await ws.send(json.dumps({
                    "method": "subscribe",
                    "subscription": {"type": "activeAssetCtx", "coin": coin},
                }))
            log.info("[hyperliquid] subscribed %d activeAssetCtx", len(symbols))

            ping_task = asyncio.create_task(self._ping(ws))
            try:
                async for raw in ws:
                    msg = json.loads(raw)
                    if msg.get("channel") != "activeAssetCtx":
                        continue
                    data = msg.get("data") or {}
                    coin = data.get("coin")
                    canonical = self.registry.canonical_for(self.name, coin)
                    if canonical is None:
                        continue
                    ctx = data.get("ctx") or {}
                    mark = _f(ctx.get("markPx"))
                    if mark is None:
                        continue
                    now = time.time()
                    await self.emit(Quote(
                        venue=self.name,
                        canonical=canonical,
                        venue_symbol=coin,
                        mark_px=mark,
                        oracle_px=_f(ctx.get("oraclePx")),
                        mid_px=_f(ctx.get("midPx")),
                        funding=_f(ctx.get("funding")),
                        prev_day_px=_f(ctx.get("prevDayPx")),
                        quote_ccy="USDC",
                        ts_source=now,   # activeAssetCtx 不带时间戳, 用本地接收时刻
                        ts_recv=now,
                    ))
            finally:
                ping_task.cancel()
