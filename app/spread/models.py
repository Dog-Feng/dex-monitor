"""热路径数据结构: 归一化报价 Quote 与两两价差 Spread。

用 dataclass 而非 pydantic —— 这两个对象每秒会产生很多, 走轻量路径;
配置校验的活交给 config.py。
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Optional


@dataclass(slots=True)
class Quote:
    """某交易所某标的永续合约在某时刻的一条归一化报价。"""

    venue: str                 # "binance" | "hyperliquid" | "sodex"
    canonical: str             # 统一标的代码, 如 "AAPL"
    venue_symbol: str          # 该所内部符号, 如 "AAPLUSDT" / "xyz:AAPL"
    mark_px: float             # 标记价格 (归一到计价单位)
    ts_source: float           # 交易所给出的时间戳 (unix 秒)
    ts_recv: float             # 本地接收时间 (unix 秒), 时效判断用这个
    quote_ccy: str = "USD"     # 计价币 USDT/USDC/...
    oracle_px: Optional[float] = None
    mid_px: Optional[float] = None
    funding: Optional[float] = None
    prev_day_px: Optional[float] = None   # 24h 前价格, 用于计算 24 小时涨跌

    def to_row(self) -> dict:
        return asdict(self)


@dataclass(slots=True)
class Spread:
    """一个场所对 (venue_a, venue_b) 在某标的上的标记价差。

    方向固定: spread = a - b, 正值表示 venue_a 的标记价更高。
    """

    canonical: str
    venue_a: str
    venue_b: str
    mark_a: float
    mark_b: float
    spread_abs: float          # mark_a - mark_b
    spread_bps: float          # 相对两腿均值的基点差
    ts: float                  # 计算时刻 (unix 秒)
    stale: bool                # 任一腿报价过期
    market_session: str        # RTH / pre / after / closed
    funding_a: Optional[float] = None
    funding_b: Optional[float] = None

    def to_row(self) -> dict:
        return asdict(self)


def compute_spread_bps(mark_a: float, mark_b: float) -> float:
    """以两腿均值为基准的基点价差。均值为 0 时返回 0 避免除零。"""
    mid = (mark_a + mark_b) / 2.0
    if mid == 0:
        return 0.0
    return (mark_a - mark_b) / mid * 1e4
