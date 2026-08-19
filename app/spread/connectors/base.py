"""连接器抽象基类: 统一的重连退避循环 + 向共享队列产出 Quote。

子类只需实现 `_stream()`: 建立连接、订阅、持续把 Quote 通过 `self.emit()`
推出去; 连接断开时抛异常, 由基类负责退避重连。
"""
from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod

from ..models import Quote
from ..registry import Registry

log = logging.getLogger(__name__)

_MAX_BACKOFF = 30.0


class Connector(ABC):
    name: str = "base"

    def __init__(self, registry: Registry, queue: "asyncio.Queue[Quote]",
                 venue_cfg: dict | None = None):
        self.registry = registry
        self.queue = queue
        self.venue_cfg = venue_cfg or {}

    @abstractmethod
    async def _stream(self) -> None:
        """建立连接并持续 emit Quote; 断开时正常返回或抛异常。"""

    async def emit(self, quote: Quote) -> None:
        await self.queue.put(quote)

    async def run(self) -> None:
        backoff = 1.0
        while True:
            try:
                log.info("[%s] connecting…", self.name)
                await self._stream()
                backoff = 1.0  # 正常返回 (少见) 后重置
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 - 连接层需兜底所有异常以重连
                log.warning("[%s] stream error: %r; reconnect in %.1fs",
                            self.name, e, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, _MAX_BACKOFF)
