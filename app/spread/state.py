"""线程安全的价差看板状态，供 Flask REST 读取。"""
from __future__ import annotations

import threading
from typing import Any

from .models import Quote, Spread
from .registry import Registry


class SpreadState:
    """内存快照：最新报价、指数、同步元信息。"""

    def __init__(self, registry: Registry):
        self.registry = registry
        self._lock = threading.Lock()
        self.quote_snapshot: dict[tuple[str, str], dict[str, Any]] = {}
        self.index_snapshot: dict[str, dict[str, Any]] = {}
        self.sync_status: dict[str, Any] = {
            "last_sync": None,
            "count": 0,
            "venues": [],
            "source": "config",
            "symbols": [],
            "markets": {},
        }

    async def on_quote(self, q: Quote) -> None:
        d = {
            "venue": q.venue,
            "canonical": q.canonical,
            "mark_px": q.mark_px,
            "ts_recv": q.ts_recv,
            "prev_day_px": q.prev_day_px,
            "funding": q.funding,
        }
        with self._lock:
            self.quote_snapshot[(q.venue, q.canonical)] = d

    async def on_index(self, payload: dict[str, Any]) -> None:
        with self._lock:
            self.index_snapshot[payload["coin"]] = payload

    async def set_sync_status(self, status: dict[str, Any]) -> None:
        with self._lock:
            self.sync_status = status
            keep = set(status.get("symbols", []))
            if keep:
                self.quote_snapshot = {
                    k: v for k, v in self.quote_snapshot.items() if k[1] in keep
                }

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "quotes": list(self.quote_snapshot.values()),
                "indices": list(self.index_snapshot.values()),
                "sync": dict(self.sync_status),
                "markets": self.registry.markets(),
                "canonicals": list(self.registry.canonicals),
            }
