from __future__ import annotations

import json
import time
from typing import Any

from app.models.repositories import Repository


def build_overview(repo: Repository) -> dict[str, Any]:
    now = int(time.time())
    since_24h = now - 86400
    since_7d = now - 7 * 86400

    total_24h = repo.count_anomalies_since(since_24h)
    surge_24h = repo.count_anomalies_since(since_24h, "SURGE")
    dump_24h = repo.count_anomalies_since(since_24h, "DUMP")
    high_24h = repo.count_anomalies_since(since_24h, severity="HIGH")
    symbols_count = repo.count_enabled_symbols()
    metadata_count = repo.count_token_metadata()
    last_metric_ts = repo.get_latest_metric_ts()

    return {
        "total_24h": total_24h,
        "surge_24h": surge_24h,
        "dump_24h": dump_24h,
        "high_24h": high_24h,
        "symbols_count": symbols_count,
        "metadata_count": metadata_count,
        "last_metric_ts": last_metric_ts,
        "last_metric_time": _fmt_ts(last_metric_ts),
        "events_7d": repo.count_anomalies_since(since_7d),
        "server_time": now,
    }


def build_anomalies(repo: Repository, days: int = 7, limit: int = 100) -> list[dict[str, Any]]:
    since = int(time.time()) - days * 86400
    rows = repo.load_anomaly_events(since)[:limit]
    result = []
    for row in rows:
        tags = []
        try:
            tags = json.loads(row.get("tags_json") or "[]")
        except json.JSONDecodeError:
            pass
        metrics = {}
        try:
            metrics = json.loads(row.get("metrics_json") or "{}")
        except json.JSONDecodeError:
            pass
        result.append(
            {
                "id": row["id"],
                "detected_ts": row["detected_ts"],
                "detected_time": _fmt_ts(row["detected_ts"]),
                "symbol": row["symbol"],
                "anomaly_type": row["anomaly_type"],
                "severity": row["severity"],
                "change_15m": row["change_15m"],
                "change_15m_pct": round(row["change_15m"] * 100, 2),
                "tags": tags,
                "narrative": row.get("narrative", ""),
                "funding_rate": metrics.get("funding_rate"),
                "oi_mcap_ratio": metrics.get("oi_mcap_ratio"),
                "price": metrics.get("price"),
            }
        )
    return result


def build_metrics(repo: Repository, limit: int = 60) -> list[dict[str, Any]]:
    rows = repo.load_latest_metrics_snapshot(limit)
    result = []
    for row in rows:
        result.append(
            {
                "symbol": row["symbol"],
                "ts": row["ts"],
                "time": _fmt_ts(row["ts"]),
                "price": row["price"],
                "funding_rate": row["funding_rate"],
                "funding_rate_pct": round((row["funding_rate"] or 0) * 100, 4),
                "oi": row["oi"],
                "oi_mcap_ratio": row["oi_mcap_ratio"],
                "volume_5m": row["volume_5m"],
                "whale_long_short_ratio": row["whale_long_short_ratio"],
            }
        )
    return result


def build_token_metadata(repo: Repository, limit: int = 100) -> list[dict[str, Any]]:
    items = repo.load_all_token_metadata(limit)
    return [
        {
            "base_asset": m.base_asset,
            "symbol": m.symbol,
            "chain": m.chain,
            "platform": m.platform,
            "token_contract": m.token_contract,
            "coingecko_id": m.coingecko_id,
            "name": m.name,
            "market_cap_rank": m.market_cap_rank,
            "updated_at": m.updated_at,
            "updated_time": _fmt_ts(m.updated_at),
        }
        for m in items
    ]


def _fmt_ts(ts: int | None) -> str | None:
    if not ts:
        return None
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))
