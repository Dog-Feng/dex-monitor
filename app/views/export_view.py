from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path


class ExportView:
    def export_anomalies(self, rows: list[dict], output_dir: str | Path) -> Path:
        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d")
        path = out_dir / f"events_{stamp}.csv"
        fieldnames = [
            "detected_ts",
            "symbol",
            "anomaly_type",
            "severity",
            "change_15m",
            "tags",
            "narrative",
        ]
        with path.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                tags = row.get("tags_json") or "[]"
                try:
                    tags_list = json.loads(tags)
                except json.JSONDecodeError:
                    tags_list = []
                writer.writerow(
                    {
                        "detected_ts": datetime.fromtimestamp(
                            row["detected_ts"]
                        ).strftime("%Y-%m-%d %H:%M:%S"),
                        "symbol": row["symbol"],
                        "anomaly_type": row["anomaly_type"],
                        "severity": row["severity"],
                        "change_15m": f"{row['change_15m'] * 100:.2f}%",
                        "tags": ",".join(tags_list),
                        "narrative": row.get("narrative", ""),
                    }
                )
        return path
