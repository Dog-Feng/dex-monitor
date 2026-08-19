"""计价币归一化钩子。

v1: USDT / USDC / USD 一律视作 ≈ 1.0 美元, 直接返回原价。
若未来需要精确处理 USDC/USDT 脱锚, 在这里接入稳定币价源即可 ——
接口保持不变, 引擎/连接器无需改动。
"""
from __future__ import annotations

# 视作等值于 1 美元的稳定币
_PEGGED = {"USD", "USDT", "USDC", "USDⓈ", "USDS"}


def to_usd(price: float, quote_ccy: str) -> float:
    """把某计价币下的价格折算成美元。当前对锚定稳定币按 1:1。"""
    if quote_ccy.upper() in _PEGGED:
        return price
    # 非锚定计价币: v1 暂不支持, 原样返回并留待接入价源
    return price
