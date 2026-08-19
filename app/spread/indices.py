"""全球重要指数行情: 从免费数据源 (Yahoo Finance 非官方 chart 接口) 轮询,
取实时/近实时价格与"昨收"以计算日内涨跌。Yahoo 失败时回退 Stooq CSV。

真实指数只在各自开市时跳动 (夜盘/周末停在收盘价), 与 24/7 的链上永续不同。
指数不参与跨所价差, 独立于价差管道, 直接把快照推给仪表盘右侧区域。

无需 API key。Yahoo 非官方接口有隐性限流 (429), 故:
  - 一次请求批量取多个 symbol (spark 接口支持逗号分隔);
  - 轮询间隔默认 10s, 服务端缓存;
  - 失败按指数退避重试, 不影响主监控。
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Awaitable, Callable

import aiohttp

from .config import IndexItem

log = logging.getLogger(__name__)

# Yahoo 非官方批量报价 (spark) 接口: 一次取多个 symbol 的最新价与昨收
_YAHOO_SPARK = "https://query1.finance.yahoo.com/v7/finance/spark"
# 单 symbol 兜底 (spark 偶发字段缺失时用 chart 补)
_YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
# Stooq CSV 兜底 (延迟/日终, 无 key)
_STOOQ = "https://stooq.com/q/l/?s={sym}&f=sd2t2ohlcv&e=csv"

_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; dsm/1.0)"}

# canonical index label 用 config 提供; 这里只做 Stooq 符号映射 (Yahoo 用 config 的 symbol)
_STOOQ_MAP = {"^NDX": "^ndx", "^N225": "^nkx", "^KS11": "^kospi", "BTC-USD": "btcusd"}

IndexCallback = Callable[[dict], Awaitable[None]]
_MAX_BACKOFF = 60.0


def _f(v):
    try:
        f = float(v)
        return f if f == f else None  # 过滤 NaN
    except (TypeError, ValueError):
        return None


class IndicesStreamer:
    """按 IndexItem.coin 作为数据源 symbol (如 '^NDX'), label 作为展示名。"""

    def __init__(self, items: list[IndexItem], poll_seconds: float = 10.0,
                 callbacks: list[IndexCallback] | None = None):
        self.items = items
        self.poll = poll_seconds
        self._by_symbol = {it.coin: it for it in items}
        self.callbacks: list[IndexCallback] = list(callbacks or [])

    def on_index(self, cb: IndexCallback) -> None:
        self.callbacks.append(cb)

    async def _emit(self, it: IndexItem, price: float, prev: float | None,
                    src: str) -> None:
        payload = {
            "coin": it.coin,           # 保持字段名不变, 前端按此作 key
            "label": it.label,
            "market": it.market,
            "mark_px": price,
            "prev_day_px": prev,
            "source": src,
            "ts_recv": time.time(),
        }
        for cb in self.callbacks:
            await cb(payload)

    async def _fetch_one_yahoo(self, session: aiohttp.ClientSession, sym: str) -> bool:
        """用 chart 接口取单个 symbol (返回结构在本会话已实测: meta.regularMarketPrice
        / meta.chartPreviousClose)。成功 emit 并返回 True。"""
        it = self._by_symbol[sym]
        url = _YAHOO_CHART.format(sym=sym)
        async with session.get(url, params={"range": "1d", "interval": "1d"},
                               timeout=aiohttp.ClientTimeout(total=8)) as r:
            r.raise_for_status()
            data = await r.json(content_type=None)
        result = (((data or {}).get("chart") or {}).get("result") or [None])[0]
        if not result:
            return False
        meta = result.get("meta", {}) or {}
        price = _f(meta.get("regularMarketPrice"))
        prev = _f(meta.get("chartPreviousClose")) or _f(meta.get("previousClose"))
        if price is None:                       # 用 close 数组末位兜底
            closes = (((result.get("indicators") or {}).get("quote") or [{}])[0]
                      .get("close") or [])
            closes = [c for c in closes if c is not None]
            if closes:
                price = _f(closes[-1])
        if price is None:
            return False
        await self._emit(it, price, prev, "yahoo")
        return True

    async def _fetch_yahoo(self, session: aiohttp.ClientSession) -> set[str]:
        """并发拉取所有 symbol 的 Yahoo chart, 返回成功的 symbol 集合。"""
        syms = list(self._by_symbol)
        results = await asyncio.gather(
            *(self._fetch_one_yahoo(session, s) for s in syms),
            return_exceptions=True)
        got: set[str] = set()
        for s, ok in zip(syms, results):
            if ok is True:
                got.add(s)
            elif isinstance(ok, Exception):
                log.debug("[indices] yahoo %s failed: %r", s, ok)
        if not got:
            raise RuntimeError("Yahoo 全部 symbol 拉取失败")
        return got

    async def _fetch_stooq(self, session: aiohttp.ClientSession, sym: str) -> bool:
        it = self._by_symbol[sym]
        s = _STOOQ_MAP.get(sym, sym.lower().lstrip("^"))
        async with session.get(_STOOQ.format(sym=s),
                               timeout=aiohttp.ClientTimeout(total=8)) as r:
            txt = await r.text()
        # header: Symbol,Date,Time,Open,High,Low,Close,Volume
        lines = txt.strip().splitlines()
        if len(lines) < 2:
            return False
        row = lines[-1].split(",")
        if len(row) < 7:
            return False
        price = _f(row[6]); opn = _f(row[3])
        if price is None:
            return False
        await self._emit(it, price, opn, "stooq")  # Stooq 无昨收, 用开盘价近似
        return True

    async def run(self) -> None:
        if not self.items:
            return
        backoff = 1.0
        async with aiohttp.ClientSession(headers=_HEADERS) as session:
            log.info("[indices] polling %d real indices: %s", len(self.items),
                     ", ".join(f"{it.label}({it.coin})" for it in self.items))
            while True:
                try:
                    got = await self._fetch_yahoo(session)
                    backoff = 1.0
                except asyncio.CancelledError:
                    raise
                except Exception as e:  # noqa: BLE001
                    log.warning("[indices] yahoo failed: %r; backoff %.0fs", e, backoff)
                    got = set()
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, _MAX_BACKOFF)

                # 对 Yahoo 未覆盖的 symbol 尝试 Stooq 兜底
                for sym in self._by_symbol:
                    if sym in got:
                        continue
                    try:
                        await self._fetch_stooq(session, sym)
                    except Exception as e:  # noqa: BLE001
                        log.debug("[indices] stooq %s failed: %r", sym, e)

                await asyncio.sleep(self.poll)
