from __future__ import annotations

from app.models.entities import AnomalyEvent, AnomalyType, OnchainEvent, UnlockEvent
from app.models.repositories import Repository


class ExplainController:
    ONCHAIN_WINDOW_BEFORE = 6 * 3600
    ONCHAIN_WINDOW_AFTER = 3600

    def __init__(self, detection_cfg: dict[str, object], repo: Repository):
        self.cfg = detection_cfg
        self.repo = repo

    def fetch_related_onchain(self, event: AnomalyEvent) -> list[OnchainEvent]:
        start = event.detected_ts - self.ONCHAIN_WINDOW_BEFORE
        end = event.detected_ts + self.ONCHAIN_WINDOW_AFTER
        candidates = {event.symbol, event.symbol.replace("USDT", "")}
        events = []
        for key in candidates:
            events.extend(self.repo.load_onchain_events(key, start, end))
        seen = set()
        unique = []
        for ev in events:
            dedupe_key = ev.tx_hash or f"{ev.ts}:{ev.from_address}:{ev.to_address}"
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            unique.append(ev)
        return unique

    def enrich(
        self,
        event: AnomalyEvent,
        onchain_events: list[OnchainEvent],
        unlocks: list[UnlockEvent] | None = None,
    ) -> AnomalyEvent:
        tags: list[str] = []
        m = event.metrics
        oi_chg = event.oi_change_30m

        if event.anomaly_type in (AnomalyType.SURGE.value, AnomalyType.DUMP.value):
            tags.extend(self._deriv_tags(event.anomaly_type, m, oi_chg))

        if m.oi_mcap_ratio and m.oi_mcap_ratio >= float(self.cfg.get("oi_mcap_ratio_warn", 0.2)):
            tags.append("high_leverage")

        unlocks = unlocks or self.repo.load_unlocks_near(event.symbol, event.detected_ts)
        for unlock in unlocks:
            tags.append("unlock_sell_pressure")

        onchain_refs: list[int] = []
        chain_notes: list[str] = []
        for oc in onchain_events:
            if oc.id:
                onchain_refs.append(oc.id)
            if oc.event_type == "CEX_DEPOSIT" and oc.label in ("team_treasury", "team", "team_or_vesting"):
                tags.append("team_dump")
                chain_notes.append(
                    f"团队/金库地址向 CEX 充值 {oc.amount:.2f} ({oc.chain})"
                )
            elif oc.event_type == "UNLOCK_TRANSFER":
                tags.append("unlock_onchain")
                chain_notes.append(f"解锁相关地址转出 {oc.amount:.2f}")
            elif oc.event_type == "CEX_DEPOSIT":
                tags.append("spot_sell_pressure")
                chain_notes.append(f"大额充值 CEX {oc.amount:.2f}")

        tags = list(dict.fromkeys(tags))
        event.tags = tags
        event.onchain_refs = onchain_refs
        event.narrative = self._build_narrative(event, chain_notes, unlocks)
        return event

    def _deriv_tags(
        self, anomaly_type: str, m, oi_chg: float | None
    ) -> list[str]:
        tags: list[str] = []
        if anomaly_type == AnomalyType.SURGE.value:
            if oi_chg is not None and oi_chg < 0 and m.funding_rate <= 0:
                tags.append("short_squeeze")
            elif oi_chg is not None and oi_chg > 0 and m.funding_rate > 0:
                tags.append("long_chase")
            elif m.funding_rate <= float(self.cfg.get("funding_extreme_negative", -0.005)):
                tags.append("extreme_neg_funding_top")
            elif m.funding_rate >= float(self.cfg.get("funding_positive_threshold", 0.0005)):
                tags.append("positive_funding_pump")
        elif anomaly_type == AnomalyType.DUMP.value:
            if oi_chg is not None and oi_chg < 0 and m.funding_rate > 0:
                tags.append("long_liquidation")
            elif oi_chg is not None and oi_chg > 0 and m.funding_rate < 0:
                tags.append("short_build")
            elif m.funding_rate >= float(self.cfg.get("funding_extreme_positive", 0.001)):
                tags.append("overheated_longs")
        return tags

    def _build_narrative(
        self,
        event: AnomalyEvent,
        chain_notes: list[str],
        unlocks: list[UnlockEvent],
    ) -> str:
        m = event.metrics
        oi_part = ""
        if event.oi_change_30m is not None:
            oi_part = f"OI 30m: {event.oi_change_30m * 100:.1f}% | "

        mcap_part = ""
        if m.oi_mcap_ratio is not None:
            mcap_part = f"OI/MCap: {m.oi_mcap_ratio:.2f} | "

        head = (
            f"{event.symbol} 15m {event.change_15m * 100:+.1f}% | "
            f"{oi_part}Funding: {m.funding_rate * 100:.3f}% | {mcap_part}".rstrip(" | ")
        )

        tag_text = ", ".join(event.tags) if event.tags else "未分类"
        lines = [head, f"Tags: {tag_text}"]

        if unlocks:
            u = unlocks[0]
            lines.append(
                f"解锁: {u.amount:.0f} 枚附近窗口内 ({u.note or u.source})"
            )

        if chain_notes:
            lines.append("链上: " + "; ".join(chain_notes))
        else:
            lines.append("链上: 窗口内无显著充值/转出")

        summary = self._summary_line(event)
        lines.append(f"→ {summary}")
        return "\n".join(lines)

    def _summary_line(self, event: AnomalyEvent) -> str:
        tags = set(event.tags)
        if "team_dump" in tags:
            return "合约异动且团队地址充值 CEX，警惕拉高出货或解锁砸盘"
        if "short_squeeze" in tags:
            return "价涨+OI降+负/中性费率，典型轧空/逼空"
        if "long_chase" in tags:
            return "价涨+OI升+正费率，主动拉盘追多"
        if "extreme_neg_funding_top" in tags:
            return "高位极端负费率，空头极度拥挤，警惕末段轧空或反转"
        if "long_liquidation" in tags:
            return "价跌+OI降，多头清算为主"
        if "unlock_sell_pressure" in tags or "unlock_onchain" in tags:
            return "解锁窗口内，供应冲击风险偏高"
        if event.anomaly_type == AnomalyType.LEVERAGE_HEAT.value:
            return "OI/市值比偏高，杠杆密度大，波动易放大"
        return "已记录异常，请结合盘面进一步确认"

    @staticmethod
    def extract_conclusion(narrative: str) -> str:
        if not narrative:
            return "—"
        for line in reversed(narrative.splitlines()):
            stripped = line.strip()
            if stripped.startswith("→"):
                return stripped[1:].strip()
        return narrative.splitlines()[0].strip() if narrative else "—"

    def summarize_for_display(
        self,
        latest,
        change_15m: float | None,
        oi_change_30m: float | None,
        onchain_events: list[OnchainEvent] | None = None,
    ) -> tuple[str, str]:
        """为监控表格生成结论与完整归因（无需已入库的 anomaly 事件）。"""
        onchain_events = onchain_events or []
        tags: list[str] = []
        m = latest

        if change_15m is not None:
            if change_15m >= float(self.cfg.get("surge_pct", 0.08)):
                tags.extend(self._deriv_tags(AnomalyType.SURGE.value, m, oi_change_30m))
            elif change_15m <= -float(self.cfg.get("dump_pct", 0.08)):
                tags.extend(self._deriv_tags(AnomalyType.DUMP.value, m, oi_change_30m))

        if m.oi_mcap_ratio and m.oi_mcap_ratio >= float(self.cfg.get("oi_mcap_ratio_warn", 0.2)):
            tags.append("high_leverage")

        unlocks = self.repo.load_unlocks_near(latest.symbol, latest.ts)
        for _unlock in unlocks:
            tags.append("unlock_sell_pressure")

        chain_notes: list[str] = []
        for oc in onchain_events:
            if oc.event_type == "CEX_DEPOSIT" and oc.label in (
                "team_treasury",
                "team",
                "team_or_vesting",
            ):
                tags.append("team_dump")
                chain_notes.append(
                    f"团队/金库地址向 CEX 充值 {oc.amount:.2f} ({oc.chain})"
                )
            elif oc.event_type == "UNLOCK_TRANSFER":
                tags.append("unlock_onchain")
                chain_notes.append(f"解锁相关地址转出 {oc.amount:.2f}")
            elif oc.event_type == "CEX_DEPOSIT":
                tags.append("spot_sell_pressure")
                chain_notes.append(f"大额充值 CEX {oc.amount:.2f}")

        tags = list(dict.fromkeys(tags))

        if change_15m is not None and change_15m >= float(self.cfg.get("surge_pct", 0.08)):
            anomaly_type = AnomalyType.SURGE.value
        elif change_15m is not None and change_15m <= -float(self.cfg.get("dump_pct", 0.08)):
            anomaly_type = AnomalyType.DUMP.value
        elif m.oi_mcap_ratio and m.oi_mcap_ratio >= float(
            self.cfg.get("oi_mcap_ratio_warn", 0.2)
        ):
            anomaly_type = AnomalyType.LEVERAGE_HEAT.value
        else:
            anomaly_type = AnomalyType.SURGE.value

        event = AnomalyEvent(
            detected_ts=latest.ts,
            symbol=latest.symbol,
            anomaly_type=anomaly_type,
            severity="LOW",
            change_15m=change_15m or 0.0,
            metrics=latest,
            oi_change_30m=oi_change_30m,
            tags=tags,
        )
        event.tags = tags
        conclusion = self._summary_line(event)
        if conclusion == "已记录异常，请结合盘面进一步确认" and not tags:
            conclusion = "暂无明显异常结构，持续监控中"
        narrative = self._build_narrative(event, chain_notes, unlocks)
        return conclusion, narrative
