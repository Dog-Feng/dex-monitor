"""价差告警: 阈值 + 持续时长 + 冷却去抖。

规则: |spread_bps| ≥ alert_bps 且持续 ≥ alert_sustain_seconds 且未处于冷却期,
且该价差不 stale, 则触发一次告警。sink: 日志(总是) + webhook + Telegram(可选)。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import aiohttp

from .config import AlertsCfg, SpreadCfg
from .models import Spread

log = logging.getLogger(__name__)


@dataclass
class _PairState:
    over_since: float | None = None   # 首次超阈时刻
    last_alert: float = 0.0           # 上次告警时刻 (冷却用)


class Alerter:
    def __init__(self, alerts_cfg: AlertsCfg, spread_cfg: SpreadCfg):
        self.acfg = alerts_cfg
        self.scfg = spread_cfg
        self._state: dict[tuple[str, str, str], _PairState] = {}
        self._session: aiohttp.ClientSession | None = None

    async def on_spread(self, s: Spread) -> None:
        key = (s.canonical, s.venue_a, s.venue_b)
        st = self._state.setdefault(key, _PairState())

        # 陈旧或未超阈: 重置持续计时
        if s.stale or abs(s.spread_bps) < self.scfg.alert_bps:
            st.over_since = None
            return

        if st.over_since is None:
            st.over_since = s.ts
        sustained = s.ts - st.over_since
        if sustained < self.scfg.alert_sustain_seconds:
            return
        if s.ts - st.last_alert < self.scfg.alert_cooldown_seconds:
            return

        st.last_alert = s.ts
        await self._fire(s, sustained)

    async def _fire(self, s: Spread, sustained: float) -> None:
        text = (f"⚠️ 价差告警 {s.canonical} {s.venue_a}/{s.venue_b}: "
                f"{s.spread_bps:+.1f} bps ({s.mark_a:.2f} vs {s.mark_b:.2f}), "
                f"持续 {sustained:.0f}s, 时段 {s.market_session}")
        log.warning(text)

        if self.acfg.webhook_url:
            await self._post_json(self.acfg.webhook_url, {"text": text, "spread": s.to_row()})

        tg = self.acfg.telegram
        if tg.token and tg.chat_id:
            url = f"https://api.telegram.org/bot{tg.token}/sendMessage"
            await self._post_json(url, {"chat_id": tg.chat_id, "text": text})

    async def _post_json(self, url: str, payload: dict) -> None:
        if self._session is None:
            self._session = aiohttp.ClientSession()
        try:
            async with self._session.post(
                url, json=payload, timeout=aiohttp.ClientTimeout(total=5)
            ) as resp:
                if resp.status >= 400:
                    log.warning("alert POST %s -> HTTP %d", url, resp.status)
        except Exception as e:  # noqa: BLE001 - 告警失败不影响监控主流程
            log.warning("alert POST %s failed: %r", url, e)

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None
