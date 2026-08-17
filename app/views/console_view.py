from __future__ import annotations

from datetime import datetime

from app.models.entities import AnomalyEvent


class ConsoleView:
    def render(self, event: AnomalyEvent) -> None:
        ts = datetime.fromtimestamp(event.detected_ts).strftime("%Y-%m-%d %H:%M:%S")
        m = event.metrics
        oi_text = ""
        if event.oi_change_30m is not None:
            oi_text = f"  OI 30m: {event.oi_change_30m * 100:.1f}% |"
        mcap_text = ""
        if m.oi_mcap_ratio is not None:
            mcap_text = f" OI/MCap: {m.oi_mcap_ratio:.2f}"
        tags = ", ".join(event.tags) if event.tags else "-"

        print()
        print(
            f"[{ts}] {event.severity} | {event.anomaly_type} | "
            f"{event.symbol} | {event.change_15m * 100:+.1f}% (15m)"
        )
        print(
            f"{oi_text} Funding: {m.funding_rate * 100:.3f}%{mcap_text}".strip()
        )
        print(f"  Tags: {tags}")
        for line in event.narrative.split("\n"):
            if line.startswith("→") or line.startswith("链上"):
                print(f"  {line}")
        print()
