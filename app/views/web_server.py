from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, send_from_directory

from app.config import load_config
from app.models.repositories import Repository
from app.models.sqlite import connect, init_db
from app.views.spread_api import build_spread_board
from app.views.web_api import (
    build_anomalies,
    build_metrics,
    build_monitor_tokens,
    build_overview,
    build_token_metadata,
)

logger = logging.getLogger(__name__)

WEB_ROOT = Path(__file__).resolve().parents[2] / "web"
DEFAULT_WEB_PORT = 8089


def _monitor_token_limit(cfg: dict[str, Any]) -> int:
    """看板默认条数跟随 discovery.fixed_top_gainers，未配置时取 100。"""
    n = int(cfg.get("discovery", {}).get("fixed_top_gainers") or 0)
    return n if n > 0 else 100


def create_app(config: dict[str, Any] | None = None) -> Flask:
    cfg = config or load_config()
    db_path = cfg.get("sqlite", {}).get("path", "data/monitor.db")
    conn = connect(db_path)
    init_db(conn)
    repo = Repository(conn)

    app = Flask(__name__)

    @app.route("/")
    def index():
        return send_from_directory(WEB_ROOT, "index.html")

    @app.route("/preview")
    def preview():
        return send_from_directory(WEB_ROOT, "preview.html")

    @app.route("/spread-preview")
    def spread_preview():
        return send_from_directory(WEB_ROOT, "spread-preview.html")

    @app.route("/css/<path:filename>")
    def css(filename: str):
        return send_from_directory(WEB_ROOT / "css", filename)

    @app.route("/js/<path:filename>")
    def js(filename: str):
        return send_from_directory(WEB_ROOT / "js", filename)

    @app.route("/api/overview")
    def api_overview():
        return jsonify(build_overview(repo))

    @app.route("/api/anomalies")
    def api_anomalies():
        from flask import request

        days = int(request.args.get("days", 7))
        limit = int(request.args.get("limit", 100))
        return jsonify(build_anomalies(repo, days=days, limit=limit))

    @app.route("/api/metrics")
    def api_metrics():
        from flask import request

        limit = int(request.args.get("limit", 60))
        return jsonify(build_metrics(repo, limit=limit))

    @app.route("/api/monitor-tokens")
    def api_monitor_tokens():
        from flask import request

        default_limit = _monitor_token_limit(cfg)
        limit = int(request.args.get("limit", default_limit))
        detection_cfg = cfg.get("detection", {})
        return jsonify(build_monitor_tokens(repo, detection_cfg, limit=limit))

    @app.route("/api/token-metadata")
    def api_token_metadata():
        from flask import request

        limit = int(request.args.get("limit", 100))
        return jsonify(build_token_metadata(repo, limit=limit))

    @app.route("/favicon.ico")
    def favicon():
        return ("", 204)

    @app.route("/api/health")
    def api_health():
        return jsonify({"status": "ok", "db": db_path})

    @app.route("/api/spread/board")
    def api_spread_board():
        return jsonify(build_spread_board())

    return app


def _ensure_waitress() -> bool:
    try:
        import waitress  # noqa: F401
    except ImportError:
        logger.error(
            "Web 看板需要 waitress：请执行 pip install -r requirements.txt "
            "（或 pip install 'waitress>=3.0.0'）后重启服务"
        )
        return False
    return True


def _serve_app(app: Flask, host: str, port: int) -> None:
    if not _ensure_waitress():
        return
    logger.info("Web dashboard http://%s:%s (waitress)", host, port)
    from waitress import serve

    serve(app, host=host, port=port, threads=4)


def run_web_server(config: dict[str, Any]) -> None:
    if not _ensure_waitress():
        raise SystemExit(1)
    web_cfg = config.get("web", {})
    host = web_cfg.get("host", "127.0.0.1")
    port = int(web_cfg.get("port", DEFAULT_WEB_PORT))
    app = create_app(config)
    _serve_app(app, host, port)


def start_web_server_background(config: dict[str, Any]) -> threading.Thread | None:
    web_cfg = config.get("web", {})
    if not web_cfg.get("enabled", True):
        logger.info("Web dashboard disabled (web.enabled=false)")
        return None

    if not _ensure_waitress():
        return None

    host = web_cfg.get("host", "127.0.0.1")
    port = int(web_cfg.get("port", DEFAULT_WEB_PORT))
    app = create_app(config)

    def _serve() -> None:
        try:
            _serve_app(app, host, port)
        except Exception:
            logger.exception("Web 看板线程异常退出")

    thread = threading.Thread(target=_serve, name="web-dashboard", daemon=True)
    thread.start()
    return thread
