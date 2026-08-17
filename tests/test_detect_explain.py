from __future__ import annotations

import sqlite3

import pytest

from app.controllers.detect_controller import DetectController
from app.models.entities import MetricSnapshot
from app.models.repositories import Repository
from app.models.sqlite import init_db
from app.services.history_buffer import HistoryBuffer


def _detection_cfg() -> dict:
    return {
        "surge_pct": 0.08,
        "dump_pct": 0.08,
        "vol_multiplier": 3.0,
        "oi_drop_pct": 0.05,
        "oi_rise_pct": 0.05,
        "funding_positive_threshold": 0.0005,
        "funding_extreme_positive": 0.001,
        "funding_extreme_negative": -0.005,
        "oi_mcap_ratio_warn": 0.2,
        "oi_mcap_ratio_high": 0.3,
    }


def _push_bars(buffer: HistoryBuffer, symbol: str, prices: list[float], volumes: list[float], oi: float = 1000.0):
    for i, (price, vol) in enumerate(zip(prices, volumes)):
        buffer.push(
            MetricSnapshot(
                ts=1_700_000_000 + i * 300,
                symbol=symbol,
                price=price,
                volume_5m=vol,
                oi=oi,
                funding_rate=-0.001,
            )
        )


def test_detect_surge_short_squeeze():
    buffer = HistoryBuffer()
    symbol = "TESTUSDT"
    base_vol = 100.0
    prices = [1.0] * 14 + [1.09]
    volumes = [base_vol] * 12 + [base_vol * 20] * 3
    _push_bars(buffer, symbol, prices, volumes, oi=1000.0)
    latest = buffer.latest(symbol)
    assert latest is not None
    latest.oi = 900.0
    latest.ts += 1
    buffer.push(latest)

    detect = DetectController(_detection_cfg())
    event = detect.evaluate(symbol, buffer)
    assert event is not None
    assert event.anomaly_type == "SURGE"
    assert event.change_15m >= 0.08


def test_detect_dump_long_liquidation():
    buffer = HistoryBuffer()
    symbol = "TESTUSDT"
    base_vol = 100.0
    prices = [1.0] * 14 + [0.9]
    volumes = [base_vol] * 12 + [base_vol * 20] * 3
    _push_bars(buffer, symbol, prices, volumes, oi=1000.0)
    latest = buffer.latest(symbol)
    assert latest is not None
    latest.oi = 900.0
    latest.funding_rate = 0.002
    latest.ts += 1
    buffer.push(latest)

    detect = DetectController(_detection_cfg())
    event = detect.evaluate(symbol, buffer)
    assert event is not None
    assert event.anomaly_type == "DUMP"


def test_explain_adds_short_squeeze_tag():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    repo = Repository(conn)

    from app.controllers.explain_controller import ExplainController

    explain = ExplainController(_detection_cfg(), repo)
    event = __import__("app.models.entities", fromlist=["AnomalyEvent"]).AnomalyEvent(
        detected_ts=1_700_000_000,
        symbol="TESTUSDT",
        anomaly_type="SURGE",
        severity="HIGH",
        change_15m=0.11,
        metrics=MetricSnapshot(
            ts=1_700_000_000,
            symbol="TESTUSDT",
            price=1.1,
            volume_5m=500,
            oi=900,
            funding_rate=-0.006,
            oi_mcap_ratio=0.35,
        ),
        oi_change_30m=-0.08,
    )
    enriched = explain.enrich(event, [])
    assert "short_squeeze" in enriched.tags
    assert "high_leverage" in enriched.tags
    assert "轧空" in enriched.narrative or "逼空" in enriched.narrative
