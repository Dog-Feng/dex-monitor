"""在后台线程运行 asyncio 价差监控管道。"""
from __future__ import annotations

import asyncio
import logging
import threading
from datetime import datetime, timezone
from typing import Any

from .alerts import Alerter
from .config import Config, load_config_from_dict
from .connectors import BinanceConnector, HyperliquidConnector, SoDEXConnector
from .discovery import discover_symbols
from .engine import Engine
from .indices import IndicesStreamer
from .models import Quote
from .registry import Registry
from .state import SpreadState
from .store import Store

log = logging.getLogger(__name__)

_CONNECTOR_CLASSES = {
    "binance": BinanceConnector,
    "hyperliquid": HyperliquidConnector,
    "sodex": SoDEXConnector,
}

_state: SpreadState | None = None
_thread: threading.Thread | None = None


def get_spread_state() -> SpreadState | None:
    return _state


def start_spread_monitor(raw_cfg: dict[str, Any]) -> threading.Thread | None:
    """启动价差监控后台线程；raw_cfg 为 config.yaml 的 spread_monitor 段。"""
    global _state, _thread
    if not raw_cfg.get("enabled", False):
        log.info("股票价差监控未启用 (spread_monitor.enabled=false)")
        return None
    if _thread and _thread.is_alive():
        return _thread

    cfg = load_config_from_dict(raw_cfg)
    registry = Registry(cfg.symbols)
    _state = SpreadState(registry)

    def _run() -> None:
        try:
            asyncio.run(_async_main(cfg, registry, _state))
        except Exception:
            log.exception("股票价差监控线程异常退出")

    _thread = threading.Thread(target=_run, name="spread-monitor", daemon=True)
    _thread.start()
    log.info("股票价差监控后台线程已启动")
    return _thread


def _venues_to_start(cfg: Config, registry: Registry) -> list[str]:
    out = []
    for venue in cfg.enabled_venues():
        if venue not in _CONNECTOR_CLASSES:
            log.warning("未知 venue '%s', 跳过", venue)
            continue
        if not registry.symbols_for(venue):
            log.info("%s 当前无监控符号, 暂不启动", venue)
            continue
        out.append(venue)
    return out


def _start_connectors(
    cfg: Config, registry: Registry, queue: asyncio.Queue[Quote]
) -> dict[str, asyncio.Task]:
    tasks: dict[str, asyncio.Task] = {}
    for venue in _venues_to_start(cfg, registry):
        conn = _CONNECTOR_CLASSES[venue](registry, queue, cfg.venues.get(venue, {}))
        tasks[venue] = asyncio.create_task(conn.run(), name=f"spread-conn-{venue}")
    log.info("价差连接器运行中: %s", ", ".join(tasks) or "(无)")
    return tasks


def _symbol_keys(registry: Registry) -> set[tuple[str, str, str]]:
    keys: set[tuple[str, str, str]] = set()
    for canon in registry.canonicals:
        for venue in _CONNECTOR_CLASSES:
            sym = registry.venue_symbol(venue, canon)
            if sym:
                keys.add((canon, venue, sym))
    return keys


def _make_status(cfg: Config, registry: Registry, meta: dict[str, Any]) -> dict[str, Any]:
    return {
        "last_sync": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "count": len(registry.canonicals),
        "venues": meta.get("venues", cfg.enabled_venues()),
        "source": meta.get("source", "config"),
        "symbols": sorted(registry.canonicals),
        "markets": registry.markets(),
    }


async def _sync_once(cfg: Config, registry: Registry) -> dict[str, Any]:
    hl_dex = str(cfg.venues.get("hyperliquid", {}).get("dex", "xyz"))
    symbols, meta = await discover_symbols(
        cfg.enabled_venues(), min_venues=cfg.discovery.min_venues, hl_dex=hl_dex
    )
    if symbols:
        registry.replace(symbols)
        log.info("价差标的同步: %d 个 (来源 %s)", len(symbols), "/".join(meta["venues"]))
    else:
        log.warning("价差同步未得到足够标的, 沿用现有 %d 个", len(registry.canonicals))
        meta.setdefault("venues", cfg.enabled_venues())
        meta["source"] = "config-fallback"
    return meta


async def _prune_loop(store: Store, interval: float = 3600.0) -> None:
    while True:
        await asyncio.sleep(interval)
        try:
            await store.prune()
        except Exception:
            log.exception("spread store prune failed")


async def _sync_loop(
    cfg: Config,
    registry: Registry,
    queue: asyncio.Queue[Quote],
    conn_tasks: dict[str, asyncio.Task],
    state: SpreadState,
) -> None:
    interval = max(cfg.discovery.refresh_hours, 0.1) * 3600
    while True:
        await asyncio.sleep(interval)
        prev = _symbol_keys(registry)
        try:
            meta = await _sync_once(cfg, registry)
        except Exception:
            log.exception("价差每日同步失败")
            continue
        if _symbol_keys(registry) != prev:
            log.info("价差标的集合变化, 重启连接器")
            for t in conn_tasks.values():
                t.cancel()
            conn_tasks.clear()
            conn_tasks.update(_start_connectors(cfg, registry, queue))
        await state.set_sync_status(_make_status(cfg, registry, meta))


async def _async_main(cfg: Config, registry: Registry, state: SpreadState) -> None:
    queue: asyncio.Queue[Quote] = asyncio.Queue(maxsize=10000)
    store = Store(cfg.storage.sqlite_path, cfg.storage.retention_days)
    await store.open()

    alerter = Alerter(cfg.alerts, cfg.spread)
    engine = Engine(registry, cfg.spread)
    engine.on_quote(state.on_quote)
    if cfg.storage.persist_quotes:
        engine.on_quote(store.write_quote)
    engine.on_spread(store.write_spread)
    engine.on_spread(alerter.on_spread)

    meta: dict[str, Any] = {"venues": cfg.enabled_venues(), "source": "config"}
    if cfg.discovery.enabled:
        log.info("价差监控启动发现…")
        try:
            meta = await _sync_once(cfg, registry)
        except Exception:
            log.exception("价差启动发现失败, 沿用 config symbols")
    await state.set_sync_status(_make_status(cfg, registry, meta))

    if not registry.canonicals:
        log.error("价差监控: 没有可监控的标的")
        await store.close()
        return

    conn_tasks = _start_connectors(cfg, registry, queue)
    if not conn_tasks:
        log.error("价差监控: 没有可用的连接器")
        await store.close()
        return

    bg = [
        asyncio.create_task(engine.run(queue), name="spread-engine"),
        asyncio.create_task(_prune_loop(store), name="spread-prune"),
    ]
    if cfg.discovery.enabled:
        bg.append(
            asyncio.create_task(
                _sync_loop(cfg, registry, queue, conn_tasks, state), name="spread-sync"
            )
        )
    if cfg.indices:
        indices = IndicesStreamer(cfg.indices)
        indices.on_index(state.on_index)
        bg.append(asyncio.create_task(indices.run(), name="spread-indices"))
        log.info("指数区域: %s", ", ".join(it.label for it in cfg.indices))

    try:
        await asyncio.gather(*bg, *conn_tasks.values())
    finally:
        for t in [*bg, *conn_tasks.values()]:
            t.cancel()
        await alerter.close()
        await store.close()
