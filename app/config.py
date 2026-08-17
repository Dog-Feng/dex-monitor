from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from app.models.entities import SymbolConfig


def load_config(path: str | Path = "config.yaml") -> dict[str, Any]:
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(
            f"Config not found: {config_path}. Copy config.example.yaml to config.yaml"
        )
    with config_path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def parse_symbols(config: dict[str, Any]) -> list[SymbolConfig]:
    symbols: list[SymbolConfig] = []
    for item in config.get("symbols", []):
        symbols.append(
            SymbolConfig(
                symbol=item["symbol"],
                base_asset=item["base_asset"],
                enabled=item.get("enabled", True),
                chain=item.get("chain"),
                token_contract=item.get("token_contract"),
                coingecko_id=item.get("coingecko_id"),
            )
        )
    return symbols
