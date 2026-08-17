from __future__ import annotations

import logging
import time
from typing import Any

import requests

from app.models.entities import MetricSnapshot
from app.services.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

BINANCE_FAPI = "https://fapi.binance.com"
DEFAULT_FUNDING_INTERVAL_HOURS = 8


class BinanceFetcher:
    def __init__(self, kline_interval: str = "5m", rate_limiter: RateLimiter | None = None):
        self.kline_interval = kline_interval
        self.rate_limiter = rate_limiter or RateLimiter(0.12)
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "token-anomaly-monitor/0.1"})
        self._funding_interval_cache: dict[str, int] = {}
        self._funding_interval_fetched_at = 0.0

    def refresh_funding_intervals(self, force: bool = False) -> None:
        now = time.time()
        if not force and now - self._funding_interval_fetched_at < 3600:
            return
        try:
            data = self._get(f"{BINANCE_FAPI}/fapi/v1/fundingInfo", {})
            if isinstance(data, list):
                for item in data:
                    symbol = item.get("symbol")
                    hours = item.get("fundingIntervalHours")
                    if symbol and hours is not None:
                        self._funding_interval_cache[symbol] = int(hours)
            self._funding_interval_fetched_at = now
        except Exception as exc:
            logger.warning("Binance fundingInfo fetch failed: %s", exc)

    def get_funding_interval_hours(self, symbol: str) -> int:
        self.refresh_funding_intervals()
        return self._funding_interval_cache.get(symbol, DEFAULT_FUNDING_INTERVAL_HOURS)

    def fetch_snapshot(self, symbol: str) -> MetricSnapshot | None:
        try:
            kline = self._fetch_latest_kline(symbol)
            if not kline:
                return None
            oi = self._fetch_open_interest(symbol)
            funding = self._fetch_funding_rate(symbol)
            whale_ratio = self._fetch_whale_ratio(symbol)
            funding_interval = self.get_funding_interval_hours(symbol)
            ts = int(kline["close_time"] // 1000)
            price = float(kline["close"])
            volume = float(kline["volume"])
            return MetricSnapshot(
                ts=ts,
                symbol=symbol,
                price=price,
                volume_5m=volume,
                oi=oi or 0.0,
                funding_rate=funding or 0.0,
                whale_long_short_ratio=whale_ratio,
                funding_interval_hours=funding_interval,
            )
        except Exception as exc:
            logger.warning("Binance fetch failed for %s: %s", symbol, exc)
            return None

    def fetch_klines(self, symbol: str, limit: int = 288) -> list[dict[str, Any]]:
        data = self._get(
            f"{BINANCE_FAPI}/fapi/v1/klines",
            {"symbol": symbol, "interval": self.kline_interval, "limit": limit},
        )
        result = []
        for row in data:
            result.append(
                {
                    "open_time": int(row[0]),
                    "open": float(row[1]),
                    "high": float(row[2]),
                    "low": float(row[3]),
                    "close": float(row[4]),
                    "volume": float(row[5]),
                    "close_time": int(row[6]),
                }
            )
        return result

    def klines_to_snapshots(
        self,
        symbol: str,
        klines: list[dict[str, Any]],
        oi: float | None = None,
        funding_rate: float | None = None,
        whale_ratio: float | None = None,
        funding_interval_hours: int = DEFAULT_FUNDING_INTERVAL_HOURS,
    ) -> list[MetricSnapshot]:
        snapshots = []
        for k in klines:
            snapshots.append(
                MetricSnapshot(
                    ts=int(k["close_time"] // 1000),
                    symbol=symbol,
                    price=float(k["close"]),
                    volume_5m=float(k["volume"]),
                    oi=oi or 0.0,
                    funding_rate=funding_rate or 0.0,
                    whale_long_short_ratio=whale_ratio,
                    funding_interval_hours=funding_interval_hours,
                )
            )
        return snapshots

    def fetch_tickers_24hr(self) -> list[dict[str, Any]]:
        data = self._get(f"{BINANCE_FAPI}/fapi/v1/ticker/24hr", {})
        if isinstance(data, list):
            return data
        return []

    def fetch_short_term_change(self, symbol: str, bars: int = 3) -> float | None:
        """用最近 bars 根 5m K 线计算涨跌幅（默认 15 分钟）。"""
        klines = self.fetch_klines(symbol, limit=max(bars + 1, 4))
        if len(klines) <= bars:
            return None
        old_price = float(klines[-1 - bars]["close"])
        new_price = float(klines[-1]["close"])
        if old_price <= 0:
            return None
        return (new_price - old_price) / old_price

    def _fetch_latest_kline(self, symbol: str) -> dict[str, Any] | None:
        klines = self.fetch_klines(symbol, limit=2)
        return klines[-1] if klines else None

    def _fetch_open_interest(self, symbol: str) -> float | None:
        data = self._get(f"{BINANCE_FAPI}/fapi/v1/openInterest", {"symbol": symbol})
        return float(data["openInterest"])

    def _fetch_funding_rate(self, symbol: str) -> float | None:
        data = self._get(f"{BINANCE_FAPI}/fapi/v1/premiumIndex", {"symbol": symbol})
        return float(data["lastFundingRate"])

    def _fetch_whale_ratio(self, symbol: str) -> float | None:
        data = self._get(
            f"{BINANCE_FAPI}/futures/data/topLongShortAccountRatio",
            {"symbol": symbol, "period": "5m", "limit": 1},
        )
        if not data:
            return None
        return float(data[0]["longShortRatio"])

    def _get(self, url: str, params: dict[str, Any]) -> Any:
        backoff = 1.0
        for attempt in range(4):
            self.rate_limiter.wait()
            resp = self.session.get(url, params=params, timeout=20)
            if resp.status_code == 429:
                logger.warning("Rate limited by %s, sleeping %.1fs", url, backoff)
                time.sleep(backoff)
                backoff *= 2
                continue
            resp.raise_for_status()
            return resp.json()
        resp.raise_for_status()
        return None
