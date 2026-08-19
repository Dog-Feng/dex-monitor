"""SQLite (aiosqlite, WAL) 持久化: 写 quotes / spreads, 查历史, 清理过期。

v1 单进程、每秒数十条写入, per-insert commit 在 WAL 下足够。历史量增大后
可无缝换成 TimescaleDB —— 只需替换本模块的实现, 接口不变。
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

import aiosqlite

from .models import Quote, Spread

log = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS quotes (
    ts REAL, venue TEXT, canonical TEXT, venue_symbol TEXT,
    mark_px REAL, oracle_px REAL, mid_px REAL, funding REAL, quote_ccy TEXT
);
CREATE TABLE IF NOT EXISTS spreads (
    ts REAL, canonical TEXT, venue_a TEXT, venue_b TEXT,
    mark_a REAL, mark_b REAL, spread_abs REAL, spread_bps REAL,
    stale INTEGER, market_session TEXT, funding_a REAL, funding_b REAL
);
CREATE INDEX IF NOT EXISTS idx_spreads_canon_ts ON spreads (canonical, ts);
CREATE INDEX IF NOT EXISTS idx_spreads_pair_ts
    ON spreads (canonical, venue_a, venue_b, ts);
CREATE INDEX IF NOT EXISTS idx_quotes_canon_ts ON quotes (canonical, ts);
"""


class Store:
    def __init__(self, sqlite_path: str, retention_days: int = 30):
        self.path = sqlite_path
        self.retention_days = retention_days
        self._db: aiosqlite.Connection | None = None

    async def open(self) -> None:
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self.path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL;")
        await self._db.execute("PRAGMA synchronous=NORMAL;")
        await self._db.executescript(_SCHEMA)
        await self._db.commit()
        log.info("store opened at %s", self.path)

    async def write_quote(self, q: Quote) -> None:
        assert self._db is not None
        await self._db.execute(
            "INSERT INTO quotes (ts, venue, canonical, venue_symbol, mark_px, "
            "oracle_px, mid_px, funding, quote_ccy) VALUES (?,?,?,?,?,?,?,?,?)",
            (q.ts_recv, q.venue, q.canonical, q.venue_symbol, q.mark_px,
             q.oracle_px, q.mid_px, q.funding, q.quote_ccy),
        )
        await self._db.commit()

    async def write_spread(self, s: Spread) -> None:
        assert self._db is not None
        await self._db.execute(
            "INSERT INTO spreads (ts, canonical, venue_a, venue_b, mark_a, mark_b, "
            "spread_abs, spread_bps, stale, market_session, funding_a, funding_b) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (s.ts, s.canonical, s.venue_a, s.venue_b, s.mark_a, s.mark_b,
             s.spread_abs, s.spread_bps, int(s.stale), s.market_session,
             s.funding_a, s.funding_b),
        )
        await self._db.commit()

    async def history(self, canonical: str, venue_a: str | None = None,
                      venue_b: str | None = None, since: float | None = None,
                      limit: int = 5000) -> list[dict]:
        assert self._db is not None
        sql = "SELECT * FROM spreads WHERE canonical = ?"
        args: list = [canonical]
        if venue_a and venue_b:
            va, vb = sorted([venue_a, venue_b])
            sql += " AND venue_a = ? AND venue_b = ?"
            args += [va, vb]
        if since is not None:
            sql += " AND ts >= ?"
            args.append(since)
        sql += " ORDER BY ts DESC LIMIT ?"
        args.append(limit)
        async with self._db.execute(sql, args) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in reversed(rows)]  # 时间升序返回

    async def prune(self) -> None:
        if self.retention_days <= 0 or self._db is None:
            return
        cutoff = time.time() - self.retention_days * 86400
        await self._db.execute("DELETE FROM spreads WHERE ts < ?", (cutoff,))
        await self._db.execute("DELETE FROM quotes WHERE ts < ?", (cutoff,))
        await self._db.commit()

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None
