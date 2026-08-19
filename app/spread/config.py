"""config.yaml 加载与轻量校验。"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class SymbolMap:
    canonical: str
    multiplier: float = 1.0
    # venue -> 该所符号 (空串表示该所无此标的)
    venue_symbols: dict[str, str] = field(default_factory=dict)
    market: str = "US"        # 标的本地市场: US(美股)/KR(韩股)/CN(A股)/HK(港股)


@dataclass
class SpreadCfg:
    stale_seconds: float = 5.0
    alert_bps: float = 50.0
    alert_sustain_seconds: float = 10.0
    alert_cooldown_seconds: float = 300.0


@dataclass
class TelegramCfg:
    token: str = ""
    chat_id: str = ""


@dataclass
class AlertsCfg:
    webhook_url: str = ""
    telegram: TelegramCfg = field(default_factory=TelegramCfg)


@dataclass
class StorageCfg:
    sqlite_path: str = "data/dsm.db"
    retention_days: int = 30
    persist_quotes: bool = False   # 是否落库每条原始报价 (量大, 默认只存价差)


@dataclass
class ServerCfg:
    host: str = "127.0.0.1"
    port: int = 8000


@dataclass
class DiscoveryCfg:
    enabled: bool = True        # 自动发现三方共同上市标的 (关闭则用下面 symbols)
    refresh_hours: float = 24.0 # 每天同步一次
    min_venues: int = 2         # 至少几家可达才信任发现结果, 否则回退 symbols


@dataclass
class IndexItem:
    coin: str          # Hyperliquid coin 名 (可带 dex 前缀), 如 "flx:USA100" / "BTC"
    label: str         # 展示名, 如 "纳指100"
    market: str = "US" # 时段用: US/JP/KR/CN/HK/CRYPTO


@dataclass
class Config:
    venues: dict[str, dict[str, Any]]
    symbols: list[SymbolMap]
    spread: SpreadCfg
    alerts: AlertsCfg
    storage: StorageCfg
    server: ServerCfg
    discovery: DiscoveryCfg
    indices: list[IndexItem]

    def enabled_venues(self) -> list[str]:
        return [v for v, c in self.venues.items() if c.get("enabled")]


# 已知的 venue 键 (符号映射里除 canonical/multiplier 外的字段都按 venue 解析)
KNOWN_VENUES = ("binance", "hyperliquid", "sodex")


def _parse_raw(raw: dict[str, Any]) -> Config:

    venues = raw.get("venues", {}) or {}

    symbols: list[SymbolMap] = []
    for row in raw.get("symbols", []) or []:
        canonical = row["canonical"]
        multiplier = float(row.get("multiplier", 1) or 1)
        market = str(row.get("market", "US") or "US").upper()
        venue_symbols = {
            v: str(row[v]).strip()
            for v in KNOWN_VENUES
            if row.get(v)  # 跳过空串/缺失
        }
        symbols.append(SymbolMap(canonical=canonical, multiplier=multiplier,
                                 market=market, venue_symbols=venue_symbols))

    sp = raw.get("spread", {}) or {}
    spread = SpreadCfg(
        stale_seconds=float(sp.get("stale_seconds", 5)),
        alert_bps=float(sp.get("alert_bps", 50)),
        alert_sustain_seconds=float(sp.get("alert_sustain_seconds", 10)),
        alert_cooldown_seconds=float(sp.get("alert_cooldown_seconds", 300)),
    )

    al = raw.get("alerts", {}) or {}
    tg = al.get("telegram", {}) or {}
    alerts = AlertsCfg(
        webhook_url=str(al.get("webhook_url", "") or ""),
        telegram=TelegramCfg(str(tg.get("token", "") or ""), str(tg.get("chat_id", "") or "")),
    )

    st = raw.get("storage", {}) or {}
    storage = StorageCfg(
        sqlite_path=str(st.get("sqlite_path", "data/dsm.db")),
        retention_days=int(st.get("retention_days", 30)),
        persist_quotes=bool(st.get("persist_quotes", False)),
    )

    sv = raw.get("server", {}) or {}
    server = ServerCfg(host=str(sv.get("host", "127.0.0.1")), port=int(sv.get("port", 8000)))

    dc = raw.get("discovery", {}) or {}
    discovery = DiscoveryCfg(
        enabled=bool(dc.get("enabled", True)),
        refresh_hours=float(dc.get("refresh_hours", 24)),
        min_venues=int(dc.get("min_venues", 2)),
    )

    indices: list[IndexItem] = []
    for row in raw.get("indices", []) or []:
        if not row.get("coin"):
            continue
        indices.append(IndexItem(
            coin=str(row["coin"]).strip(),
            label=str(row.get("label", row["coin"])),
            market=str(row.get("market", "US") or "US").upper(),
        ))

    return Config(
        venues=venues,
        symbols=symbols,
        spread=spread,
        alerts=alerts,
        storage=storage,
        server=server,
        discovery=discovery,
        indices=indices,
    )


def load_config(path: str | Path) -> Config:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return _parse_raw(raw)


def load_config_from_dict(raw: dict[str, Any]) -> Config:
    """从主 config.yaml 的 spread_monitor 段加载。"""
    return _parse_raw(raw or {})
