from __future__ import annotations

import json
import time
from typing import Any

from app.controllers.explain_controller import ExplainController
from app.models.entities import MetricSnapshot
from app.models.repositories import Repository


def build_monitor_tokens(
    repo: Repository,
    detection_cfg: dict[str, Any],
    limit: int = 100,
) -> list[dict[str, Any]]:
    explain = ExplainController(detection_cfg, repo)
    base_map = repo.load_symbol_base_map()
    meta_by_symbol = {m.symbol: m for m in repo.load_all_token_metadata(500)}
    ticker24h: dict[str, float] = {}
    raw_ticker = repo.get_scan_state("ticker24h")
    if raw_ticker:
        try:
            ticker24h = json.loads(raw_ticker)
        except json.JSONDecodeError:
            ticker24h = {}
    enabled = repo.load_enabled_symbols()[:limit]
    metrics_by_symbol = {
        row["symbol"]: row
        for row in repo.load_latest_metrics_snapshot(limit, enabled_only=True)
    }
    now = int(time.time())
    since_recent = now - 6 * 3600
    result: list[dict[str, Any]] = []

    for sym in enabled:
        symbol = sym.symbol
        row = metrics_by_symbol.get(symbol)
        if not row:
            base = sym.base_asset or symbol.replace("USDT", "")
            meta = meta_by_symbol.get(symbol)
            coingecko_id = meta.coingecko_id if meta else sym.coingecko_id
            change_24h = ticker24h.get(symbol)
            result.append(
                {
                    "symbol": symbol,
                    "base": base,
                    "ts": None,
                    "time": "—",
                    "price": None,
                    "change_15m": None,
                    "change_15m_pct": None,
                    "change_24h": change_24h,
                    "change_24h_pct": round(change_24h * 100, 2)
                    if change_24h is not None
                    else None,
                    "funding_rate": None,
                    "funding_interval_hours": 8,
                    "oi": None,
                    "oi_mcap_ratio": None,
                    "whale_long_short_ratio": None,
                    "market_cap": None,
                    "market_cap_display": "—",
                    "coingecko_id": coingecko_id,
                    "tokenomist_url": _tokenomist_url(coingecko_id, base),
                    "conclusion": "等待数据采集",
                    "narrative": "",
                }
            )
            continue

        latest_ts = int(row["ts"])
        latest_price = float(row["price"])

        m15 = repo.load_metric_near_ts(symbol, latest_ts - 900)
        m24 = repo.load_metric_near_ts(symbol, latest_ts - 86400)
        m30 = repo.load_metric_near_ts(symbol, latest_ts - 1800)

        change_15m = _pct_change(latest_price, m15["price"] if m15 else None)
        change_24h = _pct_change(latest_price, m24["price"] if m24 else None)
        if change_24h is None and symbol in ticker24h:
            change_24h = ticker24h[symbol]
        oi_change_30m = _pct_change(row["oi"], m30["oi"] if m30 else None)

        funding_interval = 8
        if "funding_interval_hours" in row.keys() and row["funding_interval_hours"] is not None:
            funding_interval = int(row["funding_interval_hours"])

        latest = _metric_from_row(row)
        recent = repo.load_latest_anomaly_for_symbol(symbol, since_recent)
        if recent and recent.get("narrative"):
            narrative = recent["narrative"]
            conclusion = ExplainController.extract_conclusion(narrative)
        else:
            conclusion, narrative = explain.summarize_for_display(
                latest, change_15m, oi_change_30m
            )

        base = base_map.get(symbol) or symbol.replace("USDT", "")
        meta = meta_by_symbol.get(symbol)
        coingecko_id = meta.coingecko_id if meta else None
        mcap = row.get("market_cap")

        result.append(
            {
                "symbol": symbol,
                "base": base,
                "ts": latest_ts,
                "time": _fmt_ts(latest_ts),
                "price": latest_price,
                "change_15m": change_15m,
                "change_15m_pct": round(change_15m * 100, 2) if change_15m is not None else None,
                "change_24h": change_24h,
                "change_24h_pct": round(change_24h * 100, 2) if change_24h is not None else None,
                "funding_rate": row["funding_rate"],
                "funding_interval_hours": funding_interval,
                "oi": row["oi"],
                "oi_mcap_ratio": row["oi_mcap_ratio"],
                "whale_long_short_ratio": row["whale_long_short_ratio"],
                "market_cap": mcap,
                "market_cap_display": _fmt_mcap(mcap),
                "coingecko_id": coingecko_id,
                "tokenomist_url": _tokenomist_url(coingecko_id, base),
                "conclusion": conclusion,
                "narrative": narrative,
            }
        )

    order_raw = repo.get_scan_state("discovery:symbol_order")
    if order_raw:
        try:
            order = json.loads(order_raw)
            rank = {sym: idx for idx, sym in enumerate(order)}
            result.sort(key=lambda item: rank.get(item["symbol"], len(rank)))
        except json.JSONDecodeError:
            result.sort(
                key=lambda item: item.get("change_24h") if item.get("change_24h") is not None else -999,
                reverse=True,
            )
    else:
        result.sort(
            key=lambda item: item.get("change_24h") if item.get("change_24h") is not None else -999,
            reverse=True,
        )

    return result


def _pct_change(new_val: float | None, old_val: float | None) -> float | None:
    if new_val is None or old_val is None or old_val == 0:
        return None
    return (float(new_val) - float(old_val)) / float(old_val)


def _metric_from_row(row: dict[str, Any]) -> MetricSnapshot:
    interval = 8
    if "funding_interval_hours" in row and row["funding_interval_hours"] is not None:
        interval = int(row["funding_interval_hours"])
    return MetricSnapshot(
        ts=int(row["ts"]),
        symbol=row["symbol"],
        price=float(row["price"]),
        volume_5m=float(row["volume_5m"]),
        oi=float(row["oi"]),
        funding_rate=float(row["funding_rate"]),
        whale_long_short_ratio=row.get("whale_long_short_ratio"),
        market_cap=row.get("market_cap"),
        oi_mcap_ratio=row.get("oi_mcap_ratio"),
        funding_interval_hours=interval,
    )


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
                "conclusion": ExplainController.extract_conclusion(
                    row.get("narrative", "")
                ),
                "funding_rate": metrics.get("funding_rate"),
                "funding_interval_hours": metrics.get("funding_interval_hours", 8),
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
                "funding_interval_hours": (
                    int(row["funding_interval_hours"])
                    if "funding_interval_hours" in row.keys()
                    and row["funding_interval_hours"] is not None
                    else 8
                ),
                "oi": row["oi"],
                "oi_mcap_ratio": row["oi_mcap_ratio"],
                "volume_5m": row["volume_5m"],
                "whale_long_short_ratio": row["whale_long_short_ratio"],
            }
        )
    return result


def build_token_metadata(repo: Repository, limit: int = 100) -> list[dict[str, Any]]:
    items = repo.load_all_token_metadata(limit)
    next_unlocks = repo.load_next_unlocks_map()
    mcap_map = repo.load_latest_market_cap_map()
    result: list[dict[str, Any]] = []
    for m in items:
        unlock = next_unlocks.get(m.symbol)
        mcap = mcap_map.get(m.symbol)
        result.append(
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
                "next_unlock_ts": unlock.unlock_ts if unlock else None,
                "next_unlock_date": _fmt_date(unlock.unlock_ts) if unlock else None,
                "next_unlock_pct": unlock.pct_circulating if unlock else None,
                "next_unlock_pct_display": (
                    _fmt_unlock_pct(unlock.pct_circulating, unlock.source)
                    if unlock
                    else None
                ),
                "next_unlock_source": unlock.source if unlock else None,
                "market_cap": mcap,
                "market_cap_display": _fmt_mcap(mcap),
                "tokenomist_url": _tokenomist_url(m.coingecko_id, m.base_asset),
            }
        )
    return result


def _tokenomist_url(coingecko_id: str | None, base_asset: str) -> str:
    slug = (coingecko_id or base_asset or "").strip().lower()
    return f"https://tokenomist.ai/{slug}" if slug else ""


def _fmt_ts(ts: int | None) -> str | None:
    if not ts:
        return None
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


def _fmt_date(ts: int | None) -> str | None:
    if not ts:
        return None
    return time.strftime("%Y-%m-%d", time.localtime(ts))


def _fmt_unlock_pct(pct: float | None, source: str | None) -> str | None:
    if pct is None:
        return None
    if source == "coingecko_supply":
        return f"{pct:.2f}%"
    if abs(pct) <= 1:
        return f"{pct * 100:.2f}%"
    return f"{pct:.2f}%"


def _fmt_mcap(value: float | None) -> str | None:
    if value is None:
        return None
    v = float(value)
    if v >= 1_000_000_000:
        return f"${v / 1_000_000_000:.2f}B"
    if v >= 1_000_000:
        return f"${v / 1_000_000:.2f}M"
    if v >= 1_000:
        return f"${v / 1_000:.2f}K"
    return f"${v:.2f}"
