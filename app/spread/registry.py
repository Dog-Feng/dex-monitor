"""符号注册表: canonical <-> 各所永续符号 的双向映射。"""
from __future__ import annotations

from .config import SymbolMap


class Registry:
    def __init__(self, symbols: list[SymbolMap]):
        self.replace(symbols)

    def replace(self, symbols: list[SymbolMap]) -> None:
        """整体替换标的集合 (每日同步用), 重建正/反向索引。"""
        self._by_canonical: dict[str, SymbolMap] = {s.canonical: s for s in symbols}
        # (venue, venue_symbol) -> canonical, 供 connector 反查
        self._reverse: dict[tuple[str, str], str] = {}
        for s in symbols:
            for venue, sym in s.venue_symbols.items():
                self._reverse[(venue, sym)] = s.canonical

    def symbols_for(self, venue: str) -> dict[str, str]:
        """返回该 venue 下 {canonical: venue_symbol}, 仅包含配置了符号的标的。"""
        return {
            s.canonical: s.venue_symbols[venue]
            for s in self._by_canonical.values()
            if venue in s.venue_symbols
        }

    def venue_symbol(self, venue: str, canonical: str) -> str | None:
        s = self._by_canonical.get(canonical)
        if s is None:
            return None
        return s.venue_symbols.get(venue)

    def canonical_for(self, venue: str, venue_symbol: str) -> str | None:
        return self._reverse.get((venue, venue_symbol))

    def multiplier(self, canonical: str) -> float:
        s = self._by_canonical.get(canonical)
        return s.multiplier if s else 1.0

    def market(self, canonical: str) -> str:
        s = self._by_canonical.get(canonical)
        return s.market if s else "US"

    def markets(self) -> dict[str, str]:
        return {c: s.market for c, s in self._by_canonical.items()}

    @property
    def canonicals(self) -> list[str]:
        return list(self._by_canonical.keys())
