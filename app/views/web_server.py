from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, send_from_directory

from app.config import load_config
from app.models.repositories import Repository
from app.models.sqlite import connect, init_db
from app.views.web_api import (
    build_anomalies,
    build_metrics,
    build_overview,
    build_token_metadata,
)

logger = logging.getLogger(__name__)

WEB_ROOT = Path(__file__).resolve().parents[2] / "web"
DEFAULT_WEB_PORT = 8089


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

    @app.route("/api/token-metadata")
    def api_token_metadata():
        from flask import request

        limit = int(request.args.get("limit", 100))
        return jsonify(build_token_metadata(repo, limit=limit))

    @app.route("/api/health")
    def api_health():
        return jsonify({"status": "ok", "db": db_path})

    return app


def run_web_server(config: dict[str, Any]) -> None:
    web_cfg = config.get("web", {})
    host = web_cfg.get("host", "127.0.0.1")
    port = int(web_cfg.get("port", DEFAULT_WEB_PORT))
    app = create_app(config)
    logger.info("Web dashboard http://%s:%s", host, port)
    app.run(host=host, port=port, debug=False, threaded=True, use_reloader=False)


def start_web_server_background(config: dict[str, Any]) -> threading.Thread | None:
    web_cfg = config.get("web", {})
    if not web_cfg.get("enabled", True):
        logger.info("Web dashboard disabled (web.enabled=false)")
        return None

    host = web_cfg.get("host", "127.0.0.1")
    port = int(web_cfg.get("port", DEFAULT_WEB_PORT))
    app = create_app(config)

    def _serve() -> None:
        logger.info("Web dashboard http://%s:%s", host, port)
        app.run(host=host, port=port, debug=False, threaded=True, use_reloader=False)

    thread = threading.Thread(target=_serve, name="web-dashboard", daemon=True)
    thread.start()
    return thread
