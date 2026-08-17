from __future__ import annotations

import logging
import time
from typing import Any

import requests

from app.models.entities import AnomalyEvent
from app.models.repositories import Repository

logger = logging.getLogger(__name__)


class AlertController:
    def __init__(self, alert_cfg: dict[str, Any], repo: Repository):
        self.cfg = alert_cfg
        self.repo = repo
        self.cooldown_seconds = int(alert_cfg.get("cooldown_minutes", 30)) * 60
        self.telegram = alert_cfg.get("telegram", {})

    def should_send(self, event: AnomalyEvent) -> bool:
        last = self.repo.last_alert_ts(
            event.symbol, event.anomaly_type, self.cooldown_seconds
        )
        return last is None

    def send(self, event: AnomalyEvent) -> None:
        self.repo.record_alert(event.symbol, event.anomaly_type, int(time.time()))
        if self.telegram.get("enabled"):
            self._send_telegram(event)

    def _send_telegram(self, event: AnomalyEvent) -> None:
        token = self.telegram.get("bot_token")
        chat_id = self.telegram.get("chat_id")
        if not token or not chat_id:
            return
        text = (
            f"*{event.severity}* `{event.anomaly_type}` `{event.symbol}` "
            f"{event.change_15m * 100:+.1f}% (15m)\n"
            f"{event.narrative}"
        )
        try:
            resp = requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
                timeout=15,
            )
            resp.raise_for_status()
        except Exception as exc:
            logger.warning("Telegram send failed: %s", exc)
