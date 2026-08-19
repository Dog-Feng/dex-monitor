from __future__ import annotations

from typing import Any

from app.spread.runner import get_spread_state


def build_spread_board() -> dict[str, Any]:
    state = get_spread_state()
    if state is None:
        return {
            "enabled": False,
            "quotes": [],
            "indices": [],
            "sync": {},
            "markets": {},
            "canonicals": [],
        }
    data = state.snapshot()
    data["enabled"] = True
    return data
