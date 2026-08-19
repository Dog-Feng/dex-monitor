"""美股交易时段标签。

代币化股票永续 24/7 交易, 但标的美股有交易时段。休市时各所独立定价、
价差本就放大, 所以给每条价差打时段标签, 便于按时段解读/分档告警。

v1 按 America/New_York 的常规时段划分, 暂不含节假日日历
(接入 pandas-market-calendars 可精确化, 但那是重依赖, 先不引)。
"""
from __future__ import annotations

from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

_NY = ZoneInfo("America/New_York")

_RTH_OPEN = time(9, 30)
_RTH_CLOSE = time(16, 0)
_PRE_OPEN = time(4, 0)
_AFTER_CLOSE = time(20, 0)


def session_for(ts_unix: float) -> str:
    """返回 'RTH' | 'pre' | 'after' | 'closed'。"""
    dt = datetime.fromtimestamp(ts_unix, tz=timezone.utc).astimezone(_NY)
    # 周末: 直接休市
    if dt.weekday() >= 5:
        return "closed"
    t = dt.time()
    if _RTH_OPEN <= t < _RTH_CLOSE:
        return "RTH"
    if _PRE_OPEN <= t < _RTH_OPEN:
        return "pre"
    if _RTH_CLOSE <= t < _AFTER_CLOSE:
        return "after"
    return "closed"
