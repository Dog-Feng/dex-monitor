"""SoDEX (SoSoValue / ValueChain) 永续标记价连接器 —— WebSocket。

已按官方文档接通 (https://sodex.com/documentation/trading-api):
  WS:  wss://mainnet-gw.sodex.dev/ws/perps
  订阅: {"op":"subscribe","params":{"channel":"markPrice","symbols":["AAPL-USD",...]}}
  推送: {"channel":"markPrice","type":"update"|"snapshot",
         "data":[{"E":<ms>,"s":"AAPL-USD","p":<markPx>,"i":<indexPx>,"r":<funding>,...}]}
  订阅确认: {"op":"subscribe","success":true,...} —— 忽略。
符号为字符串 ticker (如 'AAPL-USD'), 计价 vUSDC ≈ USD。推送间隔约 1s。
"""
from __future__ import annotations

import json
import logging
import time

import websockets

from ..models import Quote
from .base import Connector

log = logging.getLogger(__name__)

_DEFAULT_WS = "wss://mainnet-gw.sodex.dev/ws/perps"


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


class SoDEXConnector(Connector):
    name = "sodex"

    async def _stream(self) -> None:
        symbols = self.registry.symbols_for(self.name)  # canonical -> "AAPL-USD"
        if not symbols:
            raise RuntimeError("SoDEX 无已配置符号, 请检查 config.yaml symbols[].sodex")

        ws_url = str(self.venue_cfg.get("ws_url", _DEFAULT_WS))
        sub_symbols = list(symbols.values())

        async with websockets.connect(ws_url, ping_interval=15, ping_timeout=15,
                                      open_timeout=10) as ws:
            await ws.send(json.dumps({
                "op": "subscribe",
                "params": {"channel": "markPrice", "symbols": sub_symbols},
            }))
            log.info("[sodex] subscribed markPrice for %d symbols", len(sub_symbols))

            async for raw in ws:
                msg = json.loads(raw)
                if msg.get("channel") != "markPrice":
                    continue  # 跳过订阅确认 {"op":"subscribe","success":true}
                for d in msg.get("data", []) or []:
                    sym = d.get("s")
                    canonical = self.registry.canonical_for(self.name, sym)
                    if canonical is None:
                        continue
                    mark = _f(d.get("p"))
                    if mark is None:
                        continue
                    now = time.time()
                    await self.emit(Quote(
                        venue=self.name,
                        canonical=canonical,
                        venue_symbol=sym,
                        mark_px=mark,
                        oracle_px=_f(d.get("i")),
                        funding=_f(d.get("r")),
                        quote_ccy="USD",
                        ts_source=d.get("E", now * 1000) / 1000.0,
                        ts_recv=now,
                    ))
