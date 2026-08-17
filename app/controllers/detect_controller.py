from __future__ import annotations

from typing import Any

from app.models.entities import AnomalyEvent, AnomalyType, MetricSnapshot, Severity
from app.services.history_buffer import HistoryBuffer


class DetectController:
    def __init__(self, detection_cfg: dict[str, Any]):
        self.cfg = detection_cfg
        self.bars_15m = 3
        self.bars_30m = 6

    def evaluate(self, symbol: str, buffer: HistoryBuffer) -> AnomalyEvent | None:
        latest = buffer.latest(symbol)
        if not latest:
            return None

        change_15m = buffer.change_pct(symbol, self.bars_15m)
        if change_15m is None:
            return None

        volume_15m = buffer.volume_sum(symbol, self.bars_15m) or 0.0
        avg_volume = buffer.avg_volume(symbol) or 0.0
        oi_change_30m = buffer.oi_change_pct(symbol, self.bars_30m)

        vol_ok = avg_volume > 0 and volume_15m >= self.cfg["vol_multiplier"] * avg_volume * self.bars_15m

        surge = self._check_surge(change_15m, vol_ok, oi_change_30m, latest)
        if surge:
            return self._build_event(
                latest, AnomalyType.SURGE, change_15m, oi_change_30m
            )

        dump = self._check_dump(change_15m, vol_ok, oi_change_30m, latest)
        if dump:
            return self._build_event(
                latest, AnomalyType.DUMP, change_15m, oi_change_30m
            )

        heat = self._check_leverage_heat(latest)
        if heat:
            return self._build_event(
                latest, AnomalyType.LEVERAGE_HEAT, change_15m or 0.0, oi_change_30m
            )

        return None

    def _check_surge(
        self,
        change_15m: float,
        vol_ok: bool,
        oi_change_30m: float | None,
        latest: MetricSnapshot,
    ) -> bool:
        if change_15m < self.cfg["surge_pct"]:
            return False
        if not vol_ok:
            return False
        structural = False
        if oi_change_30m is not None and oi_change_30m <= -self.cfg["oi_drop_pct"]:
            structural = True
        if latest.funding_rate >= self.cfg["funding_positive_threshold"]:
            structural = True
        if oi_change_30m is not None and oi_change_30m >= self.cfg["oi_rise_pct"]:
            structural = True
        return structural

    def _check_dump(
        self,
        change_15m: float,
        vol_ok: bool,
        oi_change_30m: float | None,
        latest: MetricSnapshot,
    ) -> bool:
        if change_15m > -self.cfg["dump_pct"]:
            return False
        if not vol_ok:
            return False
        structural = False
        if oi_change_30m is not None and oi_change_30m <= -self.cfg["oi_drop_pct"]:
            structural = True
        if latest.funding_rate >= self.cfg["funding_extreme_positive"]:
            structural = True
        return structural

    def _check_leverage_heat(self, latest: MetricSnapshot) -> bool:
        ratio = latest.oi_mcap_ratio
        if ratio is None:
            return False
        return ratio >= self.cfg["oi_mcap_ratio_warn"]

    def _build_event(
        self,
        latest: MetricSnapshot,
        anomaly_type: AnomalyType,
        change_15m: float,
        oi_change_30m: float | None,
    ) -> AnomalyEvent:
        severity = self._severity(anomaly_type, change_15m, latest)
        return AnomalyEvent(
            detected_ts=latest.ts,
            symbol=latest.symbol,
            anomaly_type=anomaly_type.value,
            severity=severity.value,
            change_15m=change_15m,
            metrics=latest,
            oi_change_30m=oi_change_30m,
        )

    def _severity(
        self, anomaly_type: AnomalyType, change_15m: float, latest: MetricSnapshot
    ) -> Severity:
        if anomaly_type == AnomalyType.LEVERAGE_HEAT:
            return Severity.MEDIUM

        severity = Severity.MEDIUM
        if abs(change_15m) >= 0.12:
            severity = Severity.HIGH

        ratio = latest.oi_mcap_ratio or 0
        if ratio >= self.cfg.get("oi_mcap_ratio_high", 0.3):
            severity = Severity.HIGH

        fr = latest.funding_rate
        if fr <= self.cfg.get("funding_extreme_negative", -0.005) or fr >= 0.01:
            severity = Severity.HIGH

        return severity
