#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

from app.config import load_config
from app.controllers.alert_controller import AlertController
from app.controllers.detect_controller import DetectController
from app.controllers.explain_controller import ExplainController
from app.controllers.poll_controller import PollController
from app.fetchers.binance import BinanceFetcher
from app.fetchers.coingecko import CoinGeckoClient
from app.fetchers.chain.address_watch import AddressWatch
from app.fetchers.chain.rpc_client import RpcClient
from app.fetchers.chain.token_transfer import TokenTransferScanner
from app.fetchers.market_cap import MarketCapFetcher
from app.fetchers.unlock_fetcher import UnlockFetcher
from app.models.entities import SymbolConfig, UnlockEvent
from app.models.repositories import Repository
from app.models.sqlite import connect, init_db
from app.services.history_buffer import HistoryBuffer
from app.services.symbol_discovery import SymbolDiscovery
from app.services.token_metadata import TokenMetadataService
from app.views.console_view import ConsoleView
from app.views.export_view import ExportView
from app.views.web_server import run_web_server, start_web_server_background


def setup_logging(config: dict) -> None:
    log_cfg = config.get("logging", {})
    level = getattr(logging, log_cfg.get("level", "INFO").upper(), logging.INFO)
    log_file = log_cfg.get("file", "data/app.log")
    Path(log_file).parent.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(level)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(fmt)
    root.addHandler(stream)

    file_handler = RotatingFileHandler(log_file, maxBytes=2_000_000, backupCount=3)
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)


def build_app(config: dict) -> PollController:
    db_path = config.get("sqlite", {}).get("path", "data/monitor.db")
    conn = connect(db_path)
    init_db(conn)
    repo = Repository(conn)

    binance = BinanceFetcher(kline_interval=config.get("kline_interval", "5m"))
    cg_cfg = config.get("coingecko", {})
    coingecko = CoinGeckoClient(
        api_key=cg_cfg.get("api_key", ""),
        chain_priority=cg_cfg.get("chain_priority"),
    )
    market_cap = MarketCapFetcher(coingecko_client=coingecko)
    buffer = HistoryBuffer(maxlen=288)
    detect = DetectController(config.get("detection", {}))
    explain = ExplainController(config.get("detection", {}), repo)
    alert = AlertController(config.get("alert", {}), repo)
    view = ConsoleView()
    unlock_fetcher = UnlockFetcher(coingecko, repo, cg_cfg)
    metadata = TokenMetadataService(coingecko, unlock_fetcher, repo, cg_cfg)
    discovery = SymbolDiscovery(binance, config.get("discovery", {}))

    chain_cfg = config.get("chain", {})
    address_watch = None
    token_scanner = None
    if chain_cfg.get("enabled"):
        api_keys = {
            "ethereum": chain_cfg.get("etherscan_api_key", ""),
            "bsc": chain_cfg.get("bscscan_api_key", ""),
        }
        address_watch = AddressWatch(
            wallets_path="data/wallets.json",
            cex_wallets_path="data/cex_hot_wallets.json",
            api_keys=api_keys,
            min_transfer_usd=float(chain_cfg.get("min_transfer_usd", 10_000)),
        )
        rpc = RpcClient(chain_cfg.get("rpc", {}))
        token_scanner = TokenTransferScanner(
            rpc_client=rpc,
            repo=repo,
            cex_wallets_path="data/cex_hot_wallets.json",
            wallets_path="data/wallets.json",
            min_transfer_usd=float(chain_cfg.get("min_transfer_usd", 10_000)),
        )

    return PollController(
        config=config,
        repo=repo,
        binance=binance,
        market_cap=market_cap,
        buffer=buffer,
        detect=detect,
        explain=explain,
        alert=alert,
        view=view,
        address_watch=address_watch,
        token_scanner=token_scanner,
        discovery=discovery,
        metadata=metadata,
    )


def cmd_start(config_path: str) -> None:
    """默认启动：Web 看板 + 后台 poll。"""
    config = load_config(config_path)
    setup_logging(config)
    web_cfg = config.get("web", {})
    port = int(web_cfg.get("port", 8089))
    host = web_cfg.get("host", "127.0.0.1")

    # 先启动 Web，避免 bootstrap 中 CoinGecko 限流阻塞看板
    start_web_server_background(config)

    log = logging.getLogger(__name__)
    log.info("Web 看板已启动 http://%s:%s", host, port)
    if host in ("127.0.0.1", "localhost"):
        log.warning(
            "web.host=%s 仅本机可访问；公网部署请改为 0.0.0.0 并放行端口 %s",
            host,
            port,
        )

    poll = build_app(config)
    log.info("正在初始化监控列表与历史数据…")
    poll.bootstrap()

    log.info("代币异常监控系统已启动")
    log.info("采集轮询已开启，Ctrl+C 退出")
    poll.run_forever()


def cmd_web(config_path: str) -> None:
    config = load_config(config_path)
    setup_logging(config)
    run_web_server(config)


def cmd_poll(config_path: str) -> None:
    config = load_config(config_path)
    setup_logging(config)
    app = build_app(config)
    app.bootstrap()
    logging.getLogger(__name__).info("Token anomaly monitor started")
    app.run_forever()


def cmd_init_db(config_path: str) -> None:
    config = load_config(config_path)
    db_path = config.get("sqlite", {}).get("path", "data/monitor.db")
    conn = connect(db_path)
    init_db(conn)
    repo = Repository(conn)
    app = build_app(config)
    app.bootstrap()
    print(f"Database initialized at {db_path}")


def cmd_report(config_path: str, days: int) -> None:
    config = load_config(config_path)
    db_path = config.get("sqlite", {}).get("path", "data/monitor.db")
    conn = connect(db_path)
    repo = Repository(conn)
    since = int(time.time()) - days * 86400
    rows = repo.load_anomaly_events(since)
    exporter = ExportView()
    path = exporter.export_anomalies(rows, "data/reports")
    print(f"Exported {len(rows)} events to {path}")


def cmd_import_unlocks(config_path: str, json_path: str) -> None:
    config = load_config(config_path)
    db_path = config.get("sqlite", {}).get("path", "data/monitor.db")
    conn = connect(db_path)
    init_db(conn)
    repo = Repository(conn)
    data = json.loads(Path(json_path).read_text(encoding="utf-8"))
    items = data.get("unlocks", data if isinstance(data, list) else [])
    count = 0
    for item in items:
        repo.insert_unlock(
            UnlockEvent(
                symbol=item["symbol"],
                unlock_ts=int(item["unlock_ts"]),
                amount=float(item["amount"]),
                pct_circulating=item.get("pct_circulating"),
                source=item.get("source", "manual"),
                note=item.get("note"),
            )
        )
        count += 1
    print(f"Imported {count} unlock records")


def cmd_discover(config_path: str, top: int) -> None:
    config = load_config(config_path)
    setup_logging(config)
    binance = BinanceFetcher(kline_interval=config.get("kline_interval", "5m"))
    discovery = SymbolDiscovery(binance, config.get("discovery", {}))
    if not discovery.enabled:
        print("discovery.enabled 为 false，请在 config.yaml 中启用")
        return
    symbols = discovery.resolve([])
    print(f"当前分析列表: {len(symbols)} 个合约\n")
    print(f"{'SYMBOL':<16} {'15m':>8} {'24h':>8} {'Vol(USDT)':>14}")
    print("-" * 50)
    for row in discovery.last_rankings[:top]:
        print(
            f"{row['symbol']:<16} "
            f"{row['change_15m'] * 100:+7.2f}% "
            f"{row['change_24h']:+7.2f}% "
            f"{row['quote_volume']:>14,.0f}"
        )
    print(f"\n满足 min_change_15m 的已选: {', '.join(s.symbol for s in symbols[:20])}")
    if len(symbols) > 20:
        print(f"... 共 {len(symbols)} 个")


def cmd_resolve(config_path: str, base_asset: str) -> None:
    config = load_config(config_path)
    setup_logging(config)
    db_path = config.get("sqlite", {}).get("path", "data/monitor.db")
    conn = connect(db_path)
    init_db(conn)
    repo = Repository(conn)

    cg_cfg = config.get("coingecko", {})
    coingecko = CoinGeckoClient(
        api_key=cg_cfg.get("api_key", ""),
        chain_priority=cg_cfg.get("chain_priority"),
    )
    unlock_fetcher = UnlockFetcher(coingecko, repo, cg_cfg)
    metadata = TokenMetadataService(coingecko, unlock_fetcher, repo, cg_cfg)

    sym = SymbolConfig(
        symbol=f"{base_asset.upper()}USDT",
        base_asset=base_asset.upper(),
        enabled=True,
    )
    enriched = metadata.enrich([sym])[0]
    repo.sync_symbols([enriched])

    print(f"Symbol:     {enriched.symbol}")
    print(f"CoinGecko:  {enriched.coingecko_id or '-'}")
    print(f"Chain:      {enriched.chain or '-'}")
    print(f"Contract:   {enriched.token_contract or '-'}")

    meta = repo.load_token_metadata(enriched.base_asset)
    if meta:
        print(f"Persisted:  token_metadata.base_asset={meta.base_asset} updated_at={meta.updated_at}")

    unlocks = repo.load_unlocks_near(enriched.symbol, int(time.time()), window_hours=8760)
    if unlocks:
        print("\nUnlock / 供应信息:")
        for u in unlocks[:5]:
            print(f"  [{u.source}] amount={u.amount:.4g} note={u.note}")
    else:
        print("\n暂无解锁记录（可能该币无额外锁定供应）")


def cmd_metadata(config_path: str, limit: int) -> None:
    config = load_config(config_path)
    db_path = config.get("sqlite", {}).get("path", "data/monitor.db")
    conn = connect(db_path)
    init_db(conn)
    repo = Repository(conn)
    rows = repo.load_all_token_metadata(limit=limit)
    if not rows:
        print("token_metadata 表为空，可先运行 poll 或 resolve")
        return
    print(f"{'BASE':<10} {'SYMBOL':<14} {'CHAIN':<10} {'CONTRACT':<44} {'CG_ID'}")
    print("-" * 100)
    for m in rows:
        contract = m.token_contract or "-"
        if len(contract) > 42:
            contract = contract[:20] + "..." + contract[-18:]
        print(
            f"{m.base_asset:<10} {m.symbol:<14} {(m.chain or '-'):<10} "
            f"{contract:<44} {m.coingecko_id or '-'}"
        )
    print(f"\n共 {len(rows)} 条（表：token_metadata）")


def cmd_once(config_path: str) -> None:
    config = load_config(config_path)
    setup_logging(config)
    app = build_app(config)
    app.bootstrap()
    app.run_once()
    print("Single poll cycle completed")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Token Anomaly Monitor — 默认启动 poll + Web 看板",
    )
    parser.add_argument("-c", "--config", default="config.yaml", help="Config file path")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("start", help="启动系统（poll + Web，默认）")
    sub.add_parser("poll", help="仅采集轮询")
    sub.add_parser("web", help="仅 Web 看板")
    sub.add_parser("init-db", help="Initialize database and sync symbols")
    sub.add_parser("once", help="Run a single poll cycle")
    discover_p = sub.add_parser("discover", help="Show Binance gainers/losers shortlist")
    discover_p.add_argument("--top", type=int, default=30, help="Rows to display")

    resolve_p = sub.add_parser("resolve", help="Resolve contract via CoinGecko by base asset")
    resolve_p.add_argument("base_asset", help="e.g. ARB, PORTAL")

    metadata_p = sub.add_parser("metadata", help="List persisted token_metadata")
    metadata_p.add_argument("--limit", type=int, default=50)

    report_p = sub.add_parser("report", help="Export anomaly events to CSV")
    report_p.add_argument("--days", type=int, default=7)

    unlock_p = sub.add_parser("import-unlocks", help="Import unlock calendar JSON")
    unlock_p.add_argument("json_path", help="Path to unlocks JSON file")

    args = parser.parse_args()
    command = args.command or "start"

    if command == "start":
        cmd_start(args.config)
    elif command == "poll":
        cmd_poll(args.config)
    elif command == "web":
        cmd_web(args.config)
    elif command == "init-db":
        cmd_init_db(args.config)
    elif command == "once":
        cmd_once(args.config)
    elif command == "discover":
        cmd_discover(args.config, args.top)
    elif command == "resolve":
        cmd_resolve(args.config, args.base_asset)
    elif command == "metadata":
        cmd_metadata(args.config, args.limit)
    elif command == "report":
        cmd_report(args.config, args.days)
    elif command == "import-unlocks":
        cmd_import_unlocks(args.config, args.json_path)


if __name__ == "__main__":
    main()
