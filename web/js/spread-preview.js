/** 静态预览：拦截 /api/spread/board，返回 Mock 数据并模拟抖动 */

(function () {
  const BASE = {
    AAPL: { bn: 227.42, hl: 227.88, sx: 226.95, prev: 224.1, fund: [0.000012, 0.000018, -0.000008] },
    TSLA: { bn: 348.5, hl: 349.12, sx: 347.2, prev: 342.0, fund: [0.000045, 0.000038, 0.000052] },
    NVDA: { bn: 132.18, hl: 131.85, sx: 132.55, prev: 128.4, fund: [0.000022, 0.000019, 0.000025] },
    HOOD: { bn: 52.34, hl: 52.68, sx: 51.9, prev: 50.2, fund: [0.000088, 0.000095, 0.000072] },
    MSTR: { bn: 385.2, hl: 386.5, sx: 383.1, prev: 378.0, fund: [0.000156, 0.000142, 0.000168] },
  };

  const INDICES = [
    { coin: '^NDX', label: '纳指100 (NDX)', market: 'US', px: 21456.32, prev: 21280.5 },
    { coin: '^N225', label: '日经225', market: 'JP', px: 39842.1, prev: 39650.0 },
    { coin: '^KS11', label: '韩国综合 (KOSPI)', market: 'KR', px: 2654.8, prev: 2641.2 },
    { coin: 'BTC-USD', label: 'BTC', market: 'CRYPTO', px: 98432.5, prev: 97120.0 },
  ];

  function jitter(v, pct = 0.0015) {
    return v * (1 + (Math.random() - 0.5) * 2 * pct);
  }

  function buildMockBoard() {
    const now = Date.now() / 1000;
    const quotes = [];
    for (const [canonical, row] of Object.entries(BASE)) {
      quotes.push(
        {
          venue: 'binance',
          canonical,
          mark_px: jitter(row.bn),
          ts_recv: now,
          prev_day_px: row.prev,
          funding: row.fund[0],
        },
        {
          venue: 'hyperliquid',
          canonical,
          mark_px: jitter(row.hl),
          ts_recv: now,
          prev_day_px: row.prev,
          funding: row.fund[1],
        },
        {
          venue: 'sodex',
          canonical,
          mark_px: jitter(row.sx),
          ts_recv: now,
          prev_day_px: row.prev,
          funding: row.fund[2],
        },
      );
    }

    const indices = INDICES.map((it) => ({
      coin: it.coin,
      label: it.label,
      market: it.market,
      mark_px: jitter(it.px, 0.0008),
      prev_day_px: it.prev,
      ts_recv: now,
    }));

    return {
      enabled: true,
      quotes,
      indices,
      sync: {
        last_sync: new Date().toISOString(),
        count: Object.keys(BASE).length,
        venues: ['binance', 'hyperliquid', 'sodex'],
        source: 'discovery',
        symbols: Object.keys(BASE),
        markets: Object.fromEntries(Object.keys(BASE).map((k) => [k, 'US'])),
      },
      markets: Object.fromEntries(Object.keys(BASE).map((k) => [k, 'US'])),
      canonicals: Object.keys(BASE),
    };
  }

  const origFetch = window.fetch.bind(window);
  window.fetch = function mockFetch(input, init) {
    const url = typeof input === 'string' ? input : input.url;
    if (url.includes('/api/spread/board')) {
      return Promise.resolve({
        ok: true,
        status: 200,
        statusText: 'OK',
        json: () => Promise.resolve(buildMockBoard()),
      });
    }
    return origFetch(input, init);
  };

  function updateClock() {
    const el = document.getElementById('clock');
    if (el) el.textContent = new Date().toLocaleString('zh-CN', { hour12: false });
  }

  document.addEventListener('DOMContentLoaded', () => {
    updateClock();
    setInterval(updateClock, 1000);
    setTimeout(() => {
      if (window.SpreadMonitor) window.SpreadMonitor.activate();
    }, 50);
  });
})();
