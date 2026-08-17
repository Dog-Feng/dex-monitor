from __future__ import annotations

from collections import deque

from app.models.entities import MetricSnapshot


class HistoryBuffer:
    def __init__(self, maxlen: int = 288):
        self.maxlen = maxlen
        self._buffers: dict[str, deque[MetricSnapshot]] = {}

    def push(self, snapshot: MetricSnapshot) -> None:
        buf = self._buffers.setdefault(snapshot.symbol, deque(maxlen=self.maxlen))
        if buf and buf[-1].ts == snapshot.ts:
            buf[-1] = snapshot
            return
        buf.append(snapshot)

    def load(self, symbol: str, snapshots: list[MetricSnapshot]) -> None:
        buf = deque(maxlen=self.maxlen)
        for snap in snapshots:
            buf.append(snap)
        self._buffers[symbol] = buf

    def get(self, symbol: str) -> list[MetricSnapshot]:
        buf = self._buffers.get(symbol)
        return list(buf) if buf else []

    def latest(self, symbol: str) -> MetricSnapshot | None:
        buf = self._buffers.get(symbol)
        if not buf:
            return None
        return buf[-1]

    def change_pct(self, symbol: str, bars: int) -> float | None:
        buf = self.get(symbol)
        if len(buf) <= bars:
            return None
        old_price = buf[-1 - bars].price
        new_price = buf[-1].price
        if old_price <= 0:
            return None
        return (new_price - old_price) / old_price

    def volume_sum(self, symbol: str, bars: int) -> float | None:
        buf = self.get(symbol)
        if len(buf) < bars:
            return None
        return sum(s.volume_5m for s in buf[-bars:])

    def avg_volume(self, symbol: str) -> float | None:
        buf = self.get(symbol)
        if len(buf) < 12:
            return None
        return sum(s.volume_5m for s in buf) / len(buf)

    def oi_change_pct(self, symbol: str, bars: int) -> float | None:
        buf = self.get(symbol)
        if len(buf) <= bars:
            return None
        old_oi = buf[-1 - bars].oi
        new_oi = buf[-1].oi
        if old_oi <= 0:
            return None
        return (new_oi - old_oi) / old_oi
