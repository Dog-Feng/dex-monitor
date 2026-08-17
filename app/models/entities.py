from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class AnomalyType(StrEnum):
    SURGE = "SURGE"
    DUMP = "DUMP"
    LEVERAGE_HEAT = "LEVERAGE_HEAT"


class Severity(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


@dataclass
class SymbolConfig:
    symbol: str
    base_asset: str
    enabled: bool = True
    chain: str | None = None
    token_contract: str | None = None
    coingecko_id: str | None = None


@dataclass
class TokenMetadata:
    base_asset: str
    symbol: str
    coingecko_id: str | None = None
    chain: str | None = None
    platform: str | None = None
    token_contract: str | None = None
    name: str | None = None
    market_cap_rank: int | None = None
    resolved_at: int = 0
    updated_at: int = 0

    def to_symbol_config(self) -> SymbolConfig:
        return SymbolConfig(
            symbol=self.symbol,
            base_asset=self.base_asset,
            enabled=True,
            chain=self.chain,
            token_contract=self.token_contract,
            coingecko_id=self.coingecko_id,
        )


@dataclass
class MetricSnapshot:
    ts: int
    symbol: str
    price: float
    volume_5m: float
    oi: float
    funding_rate: float
    whale_long_short_ratio: float | None = None
    market_cap: float | None = None
    oi_mcap_ratio: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MetricSnapshot:
        return cls(**data)


@dataclass
class UnlockEvent:
    symbol: str
    unlock_ts: int
    amount: float
    pct_circulating: float | None = None
    source: str = "manual"
    note: str | None = None


@dataclass
class OnchainEvent:
    ts: int
    chain: str
    symbol: str
    event_type: str
    from_address: str
    to_address: str
    amount: float
    amount_usd: float | None = None
    tx_hash: str = ""
    label: str | None = None
    raw_json: str | None = None
    id: int | None = None


@dataclass
class AnomalyEvent:
    detected_ts: int
    symbol: str
    anomaly_type: str
    severity: str
    change_15m: float
    metrics: MetricSnapshot
    tags: list[str] = field(default_factory=list)
    narrative: str = ""
    onchain_refs: list[int] = field(default_factory=list)
    oi_change_30m: float | None = None
    id: int | None = None

    def metrics_json(self) -> str:
        return json.dumps(self.metrics.to_dict(), ensure_ascii=False)

    def tags_json(self) -> str:
        return json.dumps(self.tags, ensure_ascii=False)

    def onchain_refs_json(self) -> str:
        return json.dumps(self.onchain_refs)
