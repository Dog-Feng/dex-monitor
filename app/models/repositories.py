from __future__ import annotations

import json
import sqlite3
import time
from typing import Any

from app.models.entities import (
    AnomalyEvent,
    MetricSnapshot,
    OnchainEvent,
    SymbolConfig,
    TokenMetadata,
    UnlockEvent,
)


class Repository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def sync_symbols(self, symbols: list[SymbolConfig]) -> None:
        for sym in symbols:
            self.conn.execute(
                """
                INSERT INTO symbols (symbol, base_asset, chain, token_contract, coingecko_id, enabled)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol) DO UPDATE SET
                    base_asset=excluded.base_asset,
                    chain=COALESCE(symbols.chain, excluded.chain),
                    token_contract=COALESCE(symbols.token_contract, excluded.token_contract),
                    coingecko_id=COALESCE(symbols.coingecko_id, excluded.coingecko_id),
                    enabled=excluded.enabled
                """,
                (
                    sym.symbol,
                    sym.base_asset,
                    sym.chain,
                    sym.token_contract,
                    sym.coingecko_id,
                    1 if sym.enabled else 0,
                ),
            )
        self.conn.commit()

    def load_enabled_symbols(self) -> list[SymbolConfig]:
        rows = self.conn.execute(
            "SELECT * FROM symbols WHERE enabled = 1 ORDER BY symbol"
        ).fetchall()
        return [_row_to_symbol(row) for row in rows]

    def load_symbol(self, symbol: str) -> SymbolConfig | None:
        row = self.conn.execute(
            "SELECT * FROM symbols WHERE symbol = ?", (symbol,)
        ).fetchone()
        return _row_to_symbol(row) if row else None

    def insert_unlock_if_new(self, unlock: UnlockEvent) -> bool:
        exists = self.conn.execute(
            """
            SELECT 1 FROM unlocks
            WHERE symbol = ? AND unlock_ts = ? AND source = ?
            LIMIT 1
            """,
            (unlock.symbol, unlock.unlock_ts, unlock.source),
        ).fetchone()
        if exists:
            return False
        self.insert_unlock(unlock)
        return True

    def has_unlock_source(self, symbol: str, source: str, max_age_hours: int = 24) -> bool:
        cutoff = int(time.time()) - max_age_hours * 3600
        row = self.conn.execute(
            """
            SELECT 1 FROM unlocks
            WHERE symbol = ? AND source = ? AND unlock_ts >= ?
            LIMIT 1
            """,
            (symbol, source, cutoff),
        ).fetchone()
        return row is not None

    def insert_metrics(self, snapshot: MetricSnapshot) -> None:
        self.conn.execute(
            """
            INSERT INTO metrics (
                ts, symbol, price, volume_5m, oi, funding_rate,
                whale_long_short_ratio, market_cap, oi_mcap_ratio,
                funding_interval_hours
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot.ts,
                snapshot.symbol,
                snapshot.price,
                snapshot.volume_5m,
                snapshot.oi,
                snapshot.funding_rate,
                snapshot.whale_long_short_ratio,
                snapshot.market_cap,
                snapshot.oi_mcap_ratio,
                snapshot.funding_interval_hours,
            ),
        )
        self.conn.commit()

    def load_recent_metrics(self, symbol: str, limit: int = 288) -> list[MetricSnapshot]:
        rows = self.conn.execute(
            """
            SELECT * FROM metrics
            WHERE symbol = ?
            ORDER BY ts DESC
            LIMIT ?
            """,
            (symbol, limit),
        ).fetchall()
        snapshots = [_row_to_metric(row) for row in reversed(rows)]
        return snapshots

    def update_market_cap(self, symbol: str, market_cap: float, ts: int) -> None:
        row = self.conn.execute(
            """
            SELECT id FROM metrics
            WHERE symbol = ?
            ORDER BY ts DESC
            LIMIT 1
            """,
            (symbol,),
        ).fetchone()
        if not row:
            return
        metric_id = row["id"]
        latest = self.conn.execute(
            "SELECT oi FROM metrics WHERE id = ?", (metric_id,)
        ).fetchone()
        oi = latest["oi"] if latest else 0.0
        oi_mcap_ratio = (oi * self._latest_price(symbol)) / market_cap if market_cap else None
        self.conn.execute(
            """
            UPDATE metrics
            SET market_cap = ?, oi_mcap_ratio = ?
            WHERE id = ?
            """,
            (market_cap, oi_mcap_ratio, metric_id),
        )
        self.conn.commit()

    def _latest_price(self, symbol: str) -> float:
        row = self.conn.execute(
            "SELECT price FROM metrics WHERE symbol = ? ORDER BY ts DESC LIMIT 1",
            (symbol,),
        ).fetchone()
        return float(row["price"]) if row else 0.0

    def cleanup_old_metrics(self, retention_days: int) -> int:
        cutoff = int(time.time()) - retention_days * 86400
        cur = self.conn.execute("DELETE FROM metrics WHERE ts < ?", (cutoff,))
        self.conn.commit()
        return cur.rowcount

    def insert_unlock(self, unlock: UnlockEvent) -> None:
        self.conn.execute(
            """
            INSERT INTO unlocks (symbol, unlock_ts, amount, pct_circulating, source, note)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                unlock.symbol,
                unlock.unlock_ts,
                unlock.amount,
                unlock.pct_circulating,
                unlock.source,
                unlock.note,
            ),
        )
        self.conn.commit()

    def load_unlocks_near(self, symbol: str, ts: int, window_hours: int = 48) -> list[UnlockEvent]:
        start = ts - window_hours * 3600
        end = ts + window_hours * 3600
        rows = self.conn.execute(
            """
            SELECT * FROM unlocks
            WHERE symbol = ? AND unlock_ts BETWEEN ? AND ?
            ORDER BY unlock_ts
            """,
            (symbol, start, end),
        ).fetchall()
        return [_row_to_unlock(row) for row in rows]

    def load_next_unlocks_map(self, now: int | None = None) -> dict[str, UnlockEvent]:
        """各 symbol 最近一条未来解锁（排除 coingecko_supply 供应快照）。"""
        now = now or int(time.time())
        rows = self.conn.execute(
            """
            SELECT * FROM unlocks
            WHERE unlock_ts > ? AND source != 'coingecko_supply'
            ORDER BY symbol, unlock_ts ASC
            """,
            (now,),
        ).fetchall()
        result: dict[str, UnlockEvent] = {}
        for row in rows:
            sym = row["symbol"]
            if sym not in result:
                result[sym] = _row_to_unlock(row)
        return result

    def load_latest_market_cap_map(self) -> dict[str, float]:
        rows = self.conn.execute(
            """
            SELECT m.symbol, m.market_cap
            FROM metrics m
            INNER JOIN (
                SELECT symbol, MAX(ts) AS max_ts FROM metrics GROUP BY symbol
            ) t ON m.symbol = t.symbol AND m.ts = t.max_ts
            WHERE m.market_cap IS NOT NULL
            """
        ).fetchall()
        return {row["symbol"]: float(row["market_cap"]) for row in rows}

    def insert_onchain_event(self, event: OnchainEvent) -> int | None:
        try:
            cur = self.conn.execute(
                """
                INSERT INTO onchain_events (
                    ts, chain, symbol, event_type, from_address, to_address,
                    amount, amount_usd, tx_hash, label, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.ts,
                    event.chain,
                    event.symbol,
                    event.event_type,
                    event.from_address.lower(),
                    event.to_address.lower(),
                    event.amount,
                    event.amount_usd,
                    event.tx_hash,
                    event.label,
                    event.raw_json,
                ),
            )
            self.conn.commit()
            return int(cur.lastrowid)
        except sqlite3.IntegrityError:
            return None

    def load_onchain_events(
        self, symbol: str, start_ts: int, end_ts: int
    ) -> list[OnchainEvent]:
        rows = self.conn.execute(
            """
            SELECT * FROM onchain_events
            WHERE symbol = ? AND ts BETWEEN ? AND ?
            ORDER BY ts
            """,
            (symbol, start_ts, end_ts),
        ).fetchall()
        return [_row_to_onchain(row) for row in rows]

    def insert_anomaly_event(self, event: AnomalyEvent) -> int:
        cur = self.conn.execute(
            """
            INSERT INTO anomaly_events (
                detected_ts, symbol, anomaly_type, severity, change_15m,
                metrics_json, tags_json, narrative, onchain_refs_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.detected_ts,
                event.symbol,
                event.anomaly_type,
                event.severity,
                event.change_15m,
                event.metrics_json(),
                event.tags_json(),
                event.narrative,
                event.onchain_refs_json(),
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def load_anomaly_events(self, since_ts: int) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT * FROM anomaly_events
            WHERE detected_ts >= ?
            ORDER BY detected_ts DESC
            """,
            (since_ts,),
        ).fetchall()
        return [dict(row) for row in rows]

    def count_anomalies_since(
        self,
        since_ts: int,
        anomaly_type: str | None = None,
        severity: str | None = None,
    ) -> int:
        sql = "SELECT COUNT(*) AS c FROM anomaly_events WHERE detected_ts >= ?"
        params: list[Any] = [since_ts]
        if anomaly_type:
            sql += " AND anomaly_type = ?"
            params.append(anomaly_type)
        if severity:
            sql += " AND severity = ?"
            params.append(severity)
        row = self.conn.execute(sql, params).fetchone()
        return int(row["c"]) if row else 0

    def count_enabled_symbols(self) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS c FROM symbols WHERE enabled = 1"
        ).fetchone()
        return int(row["c"]) if row else 0

    def count_token_metadata(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) AS c FROM token_metadata").fetchone()
        return int(row["c"]) if row else 0

    def get_latest_metric_ts(self) -> int | None:
        row = self.conn.execute("SELECT MAX(ts) AS ts FROM metrics").fetchone()
        if not row or row["ts"] is None:
            return None
        return int(row["ts"])

    def load_latest_metrics_snapshot(self, limit: int = 60) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT m.*
            FROM metrics m
            INNER JOIN (
                SELECT symbol, MAX(ts) AS max_ts
                FROM metrics
                GROUP BY symbol
            ) t ON m.symbol = t.symbol AND m.ts = t.max_ts
            ORDER BY m.symbol ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]

    def load_metric_near_ts(self, symbol: str, target_ts: int) -> dict[str, Any] | None:
        row = self.conn.execute(
            """
            SELECT * FROM metrics
            WHERE symbol = ? AND ts <= ?
            ORDER BY ts DESC
            LIMIT 1
            """,
            (symbol, target_ts),
        ).fetchone()
        return dict(row) if row else None

    def load_latest_anomaly_for_symbol(
        self, symbol: str, since_ts: int
    ) -> dict[str, Any] | None:
        row = self.conn.execute(
            """
            SELECT * FROM anomaly_events
            WHERE symbol = ? AND detected_ts >= ?
            ORDER BY detected_ts DESC
            LIMIT 1
            """,
            (symbol, since_ts),
        ).fetchone()
        return dict(row) if row else None

    def load_symbol_base_map(self) -> dict[str, str]:
        rows = self.conn.execute(
            "SELECT symbol, base_asset FROM symbols WHERE enabled = 1"
        ).fetchall()
        return {row["symbol"]: row["base_asset"] for row in rows}

    def record_alert(self, symbol: str, anomaly_type: str, sent_ts: int) -> None:
        self.conn.execute(
            """
            INSERT INTO alert_log (symbol, anomaly_type, sent_ts)
            VALUES (?, ?, ?)
            """,
            (symbol, anomaly_type, sent_ts),
        )
        self.conn.commit()

    def last_alert_ts(self, symbol: str, anomaly_type: str, cooldown_seconds: int) -> int | None:
        cutoff = int(time.time()) - cooldown_seconds
        row = self.conn.execute(
            """
            SELECT sent_ts FROM alert_log
            WHERE symbol = ? AND anomaly_type = ? AND sent_ts >= ?
            ORDER BY sent_ts DESC
            LIMIT 1
            """,
            (symbol, anomaly_type, cutoff),
        ).fetchone()
        return int(row["sent_ts"]) if row else None

    def save_token_metadata(self, meta: TokenMetadata) -> None:
        now = int(time.time())
        resolved_at = meta.resolved_at or now
        updated_at = meta.updated_at or now
        self.conn.execute(
            """
            INSERT INTO token_metadata (
                base_asset, symbol, coingecko_id, chain, platform,
                token_contract, name, market_cap_rank, resolved_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(base_asset) DO UPDATE SET
                symbol=excluded.symbol,
                coingecko_id=COALESCE(excluded.coingecko_id, token_metadata.coingecko_id),
                chain=COALESCE(excluded.chain, token_metadata.chain),
                platform=COALESCE(excluded.platform, token_metadata.platform),
                token_contract=COALESCE(excluded.token_contract, token_metadata.token_contract),
                name=COALESCE(excluded.name, token_metadata.name),
                market_cap_rank=COALESCE(excluded.market_cap_rank, token_metadata.market_cap_rank),
                updated_at=excluded.updated_at
            """,
            (
                meta.base_asset.upper(),
                meta.symbol,
                meta.coingecko_id,
                meta.chain,
                meta.platform,
                meta.token_contract,
                meta.name,
                meta.market_cap_rank,
                resolved_at,
                updated_at,
            ),
        )
        self.conn.commit()

    def load_token_metadata(self, base_asset: str) -> TokenMetadata | None:
        row = self.conn.execute(
            "SELECT * FROM token_metadata WHERE base_asset = ?",
            (base_asset.upper(),),
        ).fetchone()
        return _row_to_token_metadata(row) if row else None

    def load_all_token_metadata(self, limit: int = 200) -> list[TokenMetadata]:
        rows = self.conn.execute(
            """
            SELECT * FROM token_metadata
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [_row_to_token_metadata(row) for row in rows]

    def get_scan_state(self, key: str) -> str | None:
        row = self.conn.execute(
            "SELECT value FROM chain_scan_state WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else None

    def set_scan_state(self, key: str, value: str) -> None:
        self.conn.execute(
            """
            INSERT INTO chain_scan_state (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )
        self.conn.commit()


def _row_to_symbol(row: sqlite3.Row) -> SymbolConfig:
    return SymbolConfig(
        symbol=row["symbol"],
        base_asset=row["base_asset"],
        enabled=bool(row["enabled"]),
        chain=row["chain"],
        token_contract=row["token_contract"],
        coingecko_id=row["coingecko_id"],
    )


def _row_to_metric(row: sqlite3.Row) -> MetricSnapshot:
    keys = row.keys()
    interval = (
        int(row["funding_interval_hours"])
        if "funding_interval_hours" in keys and row["funding_interval_hours"] is not None
        else 8
    )
    return MetricSnapshot(
        ts=row["ts"],
        symbol=row["symbol"],
        price=row["price"],
        volume_5m=row["volume_5m"],
        oi=row["oi"],
        funding_rate=row["funding_rate"],
        whale_long_short_ratio=row["whale_long_short_ratio"],
        market_cap=row["market_cap"],
        oi_mcap_ratio=row["oi_mcap_ratio"],
        funding_interval_hours=interval,
    )


def _row_to_unlock(row: sqlite3.Row) -> UnlockEvent:
    return UnlockEvent(
        symbol=row["symbol"],
        unlock_ts=row["unlock_ts"],
        amount=row["amount"],
        pct_circulating=row["pct_circulating"],
        source=row["source"] or "manual",
        note=row["note"],
    )


def _row_to_onchain(row: sqlite3.Row) -> OnchainEvent:
    return OnchainEvent(
        id=row["id"],
        ts=row["ts"],
        chain=row["chain"],
        symbol=row["symbol"],
        event_type=row["event_type"],
        from_address=row["from_address"],
        to_address=row["to_address"],
        amount=row["amount"],
        amount_usd=row["amount_usd"],
        tx_hash=row["tx_hash"] or "",
        label=row["label"],
        raw_json=row["raw_json"],
    )


def _row_to_token_metadata(row: sqlite3.Row) -> TokenMetadata:
    return TokenMetadata(
        base_asset=row["base_asset"],
        symbol=row["symbol"],
        coingecko_id=row["coingecko_id"],
        chain=row["chain"],
        platform=row["platform"],
        token_contract=row["token_contract"],
        name=row["name"],
        market_cap_rank=row["market_cap_rank"],
        resolved_at=row["resolved_at"],
        updated_at=row["updated_at"],
    )
