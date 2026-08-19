/** 股票价差监控 — 对接 /api/spread/board，UI 风格与主看板一致 */

const SPREAD_POLL_MS = 2000;
const STALE_MS = 12000;
const WARN_BPS = 30;

const VENUE_ORDER = ['binance', 'hyperliquid', 'sodex'];
const VENUE_LABEL = { binance: 'BN', hyperliquid: 'HL', sodex: 'SX' };
const VENUE_SORT = { binance: 0, hyperliquid: 1, sodex: 2 };

const MARKETS = {
  US: { label: '美股', tz: 'America/New_York', rth: [[570, 960]], pre: [240, 570], after: [960, 1200] },
  JP: { label: '日股', tz: 'Asia/Tokyo', rth: [[540, 690], [750, 900]] },
  KR: { label: '韩股', tz: 'Asia/Seoul', rth: [[540, 930]] },
  CN: { label: 'A股', tz: 'Asia/Shanghai', rth: [[570, 690], [780, 900]] },
  HK: { label: '港股', tz: 'Asia/Hong_Kong', rth: [[570, 720], [780, 960]] },
  CRYPTO: { label: '加密', always: true },
};
const PHASE_LABEL = { rth: '盘中', pre: '盘前', after: '盘后', closed: '休市' };
const PHASE_RANK = { rth: 0, pre: 1, after: 2, closed: 3 };

const boardStore = new Map();
const indexStore = new Map();
let marketMap = {};
let spreadActive = false;
let spreadPollTimer = null;
let spreadSortState = { field: 'maxAbs', dir: 'desc' };

function spread$(id) {
  return document.getElementById(id);
}

function marketClock(tz) {
  const p = new Intl.DateTimeFormat('en-US', {
    timeZone: tz,
    hour12: false,
    weekday: 'short',
    hour: '2-digit',
    minute: '2-digit',
  }).formatToParts(new Date());
  const get = (t) => (p.find((x) => x.type === t) || {}).value;
  const dow = { Sun: 0, Mon: 1, Tue: 2, Wed: 3, Thu: 4, Fri: 5, Sat: 6 }[get('weekday')];
  let hh = parseInt(get('hour'), 10);
  if (hh === 24) hh = 0;
  return { dow, min: hh * 60 + parseInt(get('minute'), 10) };
}

function phaseOf(marketKey) {
  const m = MARKETS[marketKey] || MARKETS.US;
  if (m.always) return 'rth';
  const { dow, min } = marketClock(m.tz);
  if (dow === 0 || dow === 6) return 'closed';
  for (const [o, c] of m.rth) if (min >= o && min < c) return 'rth';
  if (m.pre && min >= m.pre[0] && min < m.pre[1]) return 'pre';
  if (m.after && min >= m.after[0] && min < m.after[1]) return 'after';
  return 'closed';
}

const round2 = (x) => Math.round(x * 100) / 100;
const fmtAmt = (x) => (x >= 0 ? '+' : '') + x.toFixed(Math.abs(x) < 1 ? 4 : 2);
const fmtPctNum = (p) => (p >= 0 ? '+' : '') + p.toFixed(2) + '%';
const fmtPrice = (x) =>
  Number(x).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const fmtFunding1h = (x) => (x >= 0 ? '+' : '') + (x * 100).toFixed(4) + '%';
const pctClass = (v) => (v == null || v === 0 ? 'mut' : v > 0 ? 'up' : 'down');
const isStale = (t) => Date.now() - t > STALE_MS;
const marketOf = (name) => marketMap[name] || 'US';

function maxSpread(marks) {
  const legs = [...marks.entries()].filter(([, d]) => d.mark != null);
  let best = null;
  for (let i = 0; i < legs.length; i++) {
    for (let j = i + 1; j < legs.length; j++) {
      let [va, da] = legs[i];
      let [vb, db] = legs[j];
      if ((VENUE_SORT[vb] ?? 99) < (VENUE_SORT[va] ?? 99)) [va, da, vb, db] = [vb, db, va, da];
      const pa = round2(da.mark);
      const pb = round2(db.mark);
      const diff = round2(pa - pb);
      const mid = (pa + pb) / 2;
      const bps = mid === 0 ? 0 : ((pa - pb) / mid) * 1e4;
      const stale = isStale(da.t) || isStale(db.t);
      if (best === null || Math.abs(diff) > Math.abs(best.diff)) {
        best = { diff, bps, a: va, b: vb, stale };
      }
    }
  }
  return best;
}

function buildBoardRow(name) {
  const e = boardStore.get(name);
  const ms = maxSpread(e.marks);
  const mk = marketOf(name);
  const ph = phaseOf(mk);
  const m = MARKETS[mk] || MARKETS.US;
  const open = ph === 'rth';

  const venues = {};
  for (const v of VENUE_ORDER) {
    const d = e.marks.get(v);
    if (d) venues[v] = { v: d.mark, stale: isStale(d.t) };
  }

  let spread = null;
  if (ms) {
    spread = {
      main: (ms.diff >= 0 ? '+' : '') + ms.diff.toFixed(2),
      sub: `${VENUE_LABEL[ms.a]}/${VENUE_LABEL[ms.b]} · ${ms.bps >= 0 ? '+' : ''}${ms.bps.toFixed(1)}bps`,
      cls: ms.stale ? 'mut' : Math.abs(ms.bps) >= WARN_BPS ? 'warn-spread' : pctClass(ms.diff),
    };
  }

  const cd = e.prevVenue ? e.marks.get(e.prevVenue) : null;
  let chg = null;
  if (cd && cd.mark != null && e.prevDay) {
    const amt = cd.mark - e.prevDay;
    const pct = (amt / e.prevDay) * 100;
    chg = { text: `${fmtAmt(amt)} / ${fmtPctNum(pct)}`, cls: isStale(cd.t) ? 'mut' : pctClass(amt), pct };
  }

  const funding = VENUE_ORDER.map((v) => {
    const d = e.marks.get(v);
    if (!d || d.funding == null) return null;
    return {
      text: `${VENUE_LABEL[v]} ${fmtFunding1h(d.funding)}`,
      cls: isStale(d.t) ? 'mut' : pctClass(d.funding),
    };
  }).filter(Boolean);

  const fundingAbs = funding.length
    ? Math.max(...VENUE_ORDER.map((v) => {
        const d = e.marks.get(v);
        return d && d.funding != null ? Math.abs(d.funding) : -Infinity;
      }))
    : -Infinity;

  return {
    canonical: name,
    venues,
    spread,
    chg,
    funding,
    sessionRank: PHASE_RANK[ph],
    sessionLabel: PHASE_LABEL[ph],
    sessionOpen: open,
    marketText: m.label + (open ? '交易中' : '休市'),
    marketOpen: open,
    maxAbs: ms ? Math.abs(ms.diff) : -1,
    chgSort: chg ? chg.pct : -Infinity,
    fundingAbs,
  };
}

function buildIndexRow(it) {
  const stale = isStale(it.t);
  let chg = null;
  if (it.mark_px != null && it.prev_day_px) {
    const amt = it.mark_px - it.prev_day_px;
    const pct = (amt / it.prev_day_px) * 100;
    chg = { text: `${fmtAmt(amt)} / ${fmtPctNum(pct)}`, cls: stale ? 'mut' : pctClass(amt) };
  }
  const ph = phaseOf(it.market);
  const m = MARKETS[it.market] || MARKETS.US;
  const open = ph === 'rth';
  return {
    label: it.label,
    priceText: it.mark_px != null ? fmtPrice(it.mark_px) : '—',
    stale,
    chg,
    sessionLabel: PHASE_LABEL[ph],
    sessionOpen: open,
    marketText: m.label + (open ? '交易中' : '休市'),
    marketOpen: open,
  };
}

function onQuote(d) {
  let e = boardStore.get(d.canonical);
  if (!e) {
    e = { marks: new Map(), prevDay: null, prevVenue: null };
    boardStore.set(d.canonical, e);
  }
  e.marks.set(d.venue, { mark: d.mark_px, funding: d.funding, t: Date.now() });
  if (d.prev_day_px != null) {
    e.prevDay = d.prev_day_px;
    e.prevVenue = d.venue;
  }
}

function onIndex(d) {
  indexStore.set(d.coin, { ...d, t: Date.now() });
}

function onSync(s) {
  if (s.markets) marketMap = s.markets;
  window.__lastSpreadSync = s;
  const keep = new Set(s.symbols || []);
  if (keep.size) {
    for (const n of [...boardStore.keys()]) {
      if (!keep.has(n)) boardStore.delete(n);
    }
  }
}

function applySpreadSort(rows) {
  const { field, dir } = spreadSortState;
  rows.sort((a, b) => {
    const va = a[field] ?? -Infinity;
    const vb = b[field] ?? -Infinity;
    if (va === vb) return a.canonical < b.canonical ? -1 : 1;
    const diff = Number(va) - Number(vb);
    return dir === 'asc' ? diff : -diff;
  });
}

function sessTag(label, open) {
  return `<span class="sess-tag${open ? ' open' : ''}">${label}</span>`;
}

function marketTag(text, open) {
  return `<span class="market-tag${open ? ' open' : ''}">${text}</span>`;
}

function renderSpreadBoard() {
  const tbody = spread$('spread-body');
  if (!tbody) return;

  const rows = [...boardStore.keys()].map(buildBoardRow);
  applySpreadSort(rows);

  if (!rows.length) {
    tbody.innerHTML = '<tr><td colspan="9" class="empty-row">等待行情…</td></tr>';
    spread$('spread-count').textContent = '0';
    return;
  }

  tbody.innerHTML = rows
    .map((r) => {
      const venueCells = VENUE_ORDER.map((v) => {
        const d = r.venues[v];
        if (!d) return '<td class="num mut">—</td>';
        const cls = d.stale ? 'num stale' : 'num';
        return `<td class="${cls}">${d.v.toFixed(2)}</td>`;
      }).join('');

      const spreadCell = r.spread
        ? `<td class="num"><span class="${r.spread.cls}">${r.spread.main}</span><span class="spread-sub">${r.spread.sub}</span></td>`
        : '<td class="num mut">—</td>';

      const chgCell = r.chg
        ? `<td class="num ${r.chg.cls}">${r.chg.text}</td>`
        : '<td class="num mut">—</td>';

      const fundCell =
        r.funding.length > 0
          ? `<td class="num">${r.funding.map((f) => `<span class="spread-fund-item ${f.cls}">${f.text}</span>`).join('')}</td>`
          : '<td class="num mut">—</td>';

      return `<tr data-canonical="${r.canonical}">
        <td><b>${r.canonical}</b></td>
        ${venueCells}
        ${spreadCell}
        ${chgCell}
        ${fundCell}
        <td>${sessTag(r.sessionLabel, r.sessionOpen)}</td>
        <td>${marketTag(r.marketText, r.marketOpen)}</td>
      </tr>`;
    })
    .join('');

  spread$('spread-count').textContent = String(rows.length);
}

function renderSpreadIndices() {
  const tbody = spread$('spread-index-body');
  if (!tbody) return;

  const rows = [...indexStore.values()].map(buildIndexRow);
  if (!rows.length) {
    tbody.innerHTML = '<tr><td colspan="5" class="empty-row">等待指数数据…</td></tr>';
    return;
  }

  tbody.innerHTML = rows
    .map(
      (r) => `<tr>
        <td>${r.label}</td>
        <td class="num${r.stale ? ' stale' : ''}">${r.priceText}</td>
        <td class="num ${r.chg ? r.chg.cls : 'mut'}">${r.chg ? r.chg.text : '—'}</td>
        <td>${sessTag(r.sessionLabel, r.sessionOpen)}</td>
        <td>${marketTag(r.marketText, r.marketOpen)}</td>
      </tr>`,
    )
    .join('');
}

function updateSpreadSyncText() {
  const s = window.__lastSpreadSync;
  const el = spread$('spread-sync-text');
  if (!el || !s) return;
  const SRC = { discovery: '自动发现', config: '配置清单', 'config-fallback': '配置兜底' };
  const when = s.last_sync ? new Date(s.last_sync).toLocaleString('zh-CN', { hour12: false }) : '—';
  const vs = (s.venues || []).join(' ∩ ');
  el.textContent = `来源 ${SRC[s.source] || s.source || '—'}${vs ? ` (${vs})` : ''} · 同步 ${when}`;
}

function updateSpreadSortIndicators() {
  document.querySelectorAll('#tbl-spread th.sortable').forEach((th) => {
    const icon = th.querySelector('.sort-icon');
    if (!icon) return;
    if (th.dataset.sort === spreadSortState.field) {
      th.classList.add('sorted');
      icon.textContent = spreadSortState.dir === 'desc' ? '▼' : '▲';
    } else {
      th.classList.remove('sorted');
      icon.textContent = '↕';
    }
  });
}

function onSpreadSortClick(field) {
  if (spreadSortState.field === field) {
    spreadSortState.dir = spreadSortState.dir === 'desc' ? 'asc' : 'desc';
  } else {
    spreadSortState.field = field;
    spreadSortState.dir = field === 'sessionRank' ? 'asc' : 'desc';
  }
  renderSpreadBoard();
  updateSpreadSortIndicators();
}

function setSpreadUiEnabled(enabled) {
  const disabled = spread$('spread-disabled');
  const content = spread$('spread-content');
  if (disabled) disabled.hidden = enabled;
  if (content) content.hidden = !enabled;
}

async function refreshSpread() {
  try {
    const res = await fetch('/api/spread/board');
    if (!res.ok) throw new Error(res.statusText);
    const data = await res.json();

    if (!data.enabled) {
      setSpreadUiEnabled(false);
      return;
    }

    setSpreadUiEnabled(true);
    if (data.markets) marketMap = data.markets;
    if (data.sync) onSync(data.sync);
    (data.quotes || []).forEach(onQuote);
    (data.indices || []).forEach(onIndex);

    renderSpreadBoard();
    renderSpreadIndices();
    updateSpreadSyncText();
    updateSpreadSortIndicators();

    const now = new Date();
    const el = spread$('spread-refresh');
    if (el) {
      el.textContent = now.toLocaleTimeString('zh-CN', { hour12: false });
    }
  } catch (e) {
    console.error('spread refresh failed', e);
  }
}

function startSpreadPolling() {
  if (spreadPollTimer) return;
  spreadActive = true;
  refreshSpread();
  spreadPollTimer = setInterval(() => {
    if (spreadActive) refreshSpread();
  }, SPREAD_POLL_MS);
}

function stopSpreadPolling() {
  spreadActive = false;
  if (spreadPollTimer) {
    clearInterval(spreadPollTimer);
    spreadPollTimer = null;
  }
}

function initSpreadSortHeaders() {
  document.querySelectorAll('#tbl-spread th.sortable .sort-btn').forEach((btn) => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      onSpreadSortClick(btn.closest('th').dataset.sort);
    });
  });
  updateSpreadSortIndicators();
}

function initSpreadTab() {
  initSpreadSortHeaders();

  document.querySelectorAll('.tab-btn[data-tab]').forEach((btn) => {
    btn.addEventListener('click', () => {
      if (btn.dataset.tab === 'spread') {
        startSpreadPolling();
      } else {
        stopSpreadPolling();
      }
    });
  });

  if (document.querySelector('.tab-btn.active')?.dataset.tab === 'spread') {
    startSpreadPolling();
  }
}

document.addEventListener('DOMContentLoaded', initSpreadTab);

window.SpreadMonitor = { activate: startSpreadPolling, deactivate: stopSpreadPolling, refresh: refreshSpread };
