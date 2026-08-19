"""价差引擎: 消费 Quote 队列, 维护每 (venue, canonical) 最新报价,
每来一条报价就增量重算「与该更新场所相关」的两两永续标记价差。

设计要点:
  - 只重算涉及刚更新场所的场所对, 避免无关对的冗余写入。
  - 场所对方向固定 (venue_a, venue_b 按字典序), spread = a - b。
  - 时效护栏: 任一腿 ts_recv 超过 stale_seconds 即标记 stale, 不参与告警。
  - 每条价差打美股时段标签。
`process_quote` 是同步纯函数 (除时钟外无副作用), 便于单测; `run` 是异步驱动。
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Awaitable, Callable

from .config import SpreadCfg
from .market_clock import session_for
from .models import Quote, Spread, compute_spread_bps
from .normalize import to_usd
from .registry import Registry

log = logging.getLogger(__name__)

SpreadCallback = Callable[[Spread], Awaitable[None]]


class Engine:
    def __init__(self, registry: Registry, spread_cfg: SpreadCfg,
                 callbacks: list[SpreadCallback] | None = None):
        self.registry = registry
        self.cfg = spread_cfg
        self.callbacks: list[SpreadCallback] = list(callbacks or [])
        self.quote_callbacks: list[Callable[[Quote], Awaitable[None]]] = []
        self.latest: dict[tuple[str, str], Quote] = {}

    def on_spread(self, cb: SpreadCallback) -> None:
        self.callbacks.append(cb)

    def on_quote(self, cb: Callable[[Quote], Awaitable[None]]) -> None:
        self.quote_callbacks.append(cb)

    def process_quote(self, q: Quote, now: float | None = None) -> list[Spread]:
        now = time.time() if now is None else now
        self.latest[(q.venue, q.canonical)] = q

        others = [
            v for (v, c) in self.latest
            if c == q.canonical and v != q.venue
        ]
        session = session_for(now)
        out: list[Spread] = []
        for other in others:
            va, vb = sorted([q.venue, other])
            qa = self.latest[(va, q.canonical)]
            qb = self.latest[(vb, q.canonical)]
            ma = to_usd(qa.mark_px, qa.quote_ccy)
            mb = to_usd(qb.mark_px, qb.quote_ccy)
            stale = (now - qa.ts_recv > self.cfg.stale_seconds
                     or now - qb.ts_recv > self.cfg.stale_seconds)
            out.append(Spread(
                canonical=q.canonical,
                venue_a=va, venue_b=vb,
                mark_a=ma, mark_b=mb,
                spread_abs=ma - mb,
                spread_bps=compute_spread_bps(ma, mb),
                ts=now, stale=stale, market_session=session,
                funding_a=qa.funding, funding_b=qb.funding,
            ))
        return out

    async def run(self, queue: "asyncio.Queue[Quote]") -> None:
        log.info("engine started")
        while True:
            q = await queue.get()
            try:
                for cb in self.quote_callbacks:
                    await cb(q)
                for s in self.process_quote(q):
                    for cb in self.callbacks:
                        await cb(s)
            except Exception:  # noqa: BLE001 - 单条报价失败不应拖垮引擎
                log.exception("engine failed on quote %r", q)
            finally:
                queue.task_done()
