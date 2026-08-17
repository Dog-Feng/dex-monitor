from __future__ import annotations

import logging
import time
from typing import Any

from app.fetchers.coingecko import CoinGeckoClient
from app.models.entities import UnlockEvent
from app.models.repositories import Repository

logger = logging.getLogger(__name__)


class UnlockFetcher:
    """从 CoinGecko 拉取代币供应/锁定信息，并写入 unlocks 表。

    说明：CoinGecko 免费 API 不提供完整解锁日程，仅能提供：
    - 总供应 / 流通供应 → 推算未释放量
    - 若配置 Pro API Key，会尝试额外端点（可能仍不可用）
    """

    def __init__(
        self,
        client: CoinGeckoClient,
        repo: Repository,
        cfg: dict[str, Any] | None = None,
    ):
        self.client = client
        self.repo = repo
        self.cfg = cfg or {}
        self.lookahead_days = int(self.cfg.get("unlock_lookahead_days", 30))

    def sync_for_coin(self, symbol: str, coingecko_id: str) -> int:
        if self.repo.has_unlock_source(symbol, "coingecko_supply", max_age_hours=24):
            return 0

        coin = self.client.get_coin(coingecko_id)
        if not coin:
            return 0

        inserted = 0
        inserted += self._store_supply_overhang(symbol, coingecko_id, coin)
        inserted += self._store_pro_unlocks_if_any(symbol, coingecko_id)
        return inserted

    def _store_supply_overhang(
        self, symbol: str, coingecko_id: str, coin: dict[str, Any]
    ) -> int:
        md = coin.get("market_data") or {}
        circulating = _float(md.get("circulating_supply"))
        total = _float(md.get("total_supply")) or _float(md.get("max_supply"))
        if not total or circulating is None or total <= circulating:
            return 0

        locked = total - circulating
        pct = locked / total * 100 if total else None
        note = (
            f"CoinGecko 供应：流通 {_fmt(circulating)} / 总量 {_fmt(total)}，"
            f"未释放约 {_fmt(locked)} ({pct:.1f}%)"
        )
        event = UnlockEvent(
            symbol=symbol,
            unlock_ts=int(time.time()),
            amount=locked,
            pct_circulating=pct,
            source="coingecko_supply",
            note=note,
        )
        return 1 if self.repo.insert_unlock_if_new(event) else 0

    def _store_pro_unlocks_if_any(self, symbol: str, coingecko_id: str) -> int:
        if not self.client.api_key:
            return 0
        # Pro 端点因套餐而异；失败则静默跳过
        try:
            data = self.client._get(f"/coins/{coingecko_id}/token_unlocks", {})
            events = data if isinstance(data, list) else data.get("unlock_events", [])
        except Exception:
            return 0

        inserted = 0
        now = int(time.time())
        horizon = now + self.lookahead_days * 86400
        for item in events:
            ts = int(item.get("timestamp") or item.get("unlock_date") or 0)
            if ts > 1_000_000_000_000:
                ts //= 1000
            if not ts or ts < now - 86400 or ts > horizon:
                continue
            amount = _float(item.get("amount") or item.get("tokens"))
            if not amount:
                continue
            pct = _float(item.get("pct_circulating") or item.get("percentage"))
            event = UnlockEvent(
                symbol=symbol,
                unlock_ts=ts,
                amount=amount,
                pct_circulating=pct,
                source="coingecko_pro",
                note=item.get("label") or "CoinGecko Pro unlock schedule",
            )
            if self.repo.insert_unlock_if_new(event):
                inserted += 1
        return inserted


def _float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _fmt(n: float) -> str:
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.2f}B"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f}M"
    if n >= 1_000:
        return f"{n / 1_000:.2f}K"
    return f"{n:.2f}"
