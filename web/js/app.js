/** 代币监控看板 — 对接 /api/monitor-tokens */

const REFRESH_MS = 10000;

let tokenState = [];
let tokenStatePrev = {};
let selectedSymbol = null;
let sortState = { field: 'change_15m', dir: 'desc' };

function $(id) {
  return document.getElementById(id);
}

function fmtPct(v, d = 2) {
  if (v == null || Number.isNaN(v)) return '—';
  const n = Number(v);
  return (n >= 0 ? '+' : '') + n.toFixed(d) + '%';
}

function pctClass(v) {
  if (v == null || v === 0) return 'mut';
  return v > 0 ? 'up' : 'down';
}

function fmtFunding(r, intervalHours = 8) {
  if (r == null) return '—';
  const pct = (Number(r) * 100).toFixed(4) + '%';
  const h = intervalHours != null ? intervalHours : 8;
  return `${pct}<span class="fund-interval">（${h}h）</span>`;
}

function fmtNum(n) {
  if (n == null) return '—';
  const x = Number(n);
  if (x >= 1e9) return (x / 1e9).toFixed(2) + 'B';
  if (x >= 1e6) return (x / 1e6).toFixed(2) + 'M';
  if (x >= 1e3) return (x / 1e3).toFixed(2) + 'K';
  if (x >= 1) return x.toFixed(4);
  return x.toFixed(6);
}

function shortAddr(a) {
  if (!a || a.length < 12) return a || '—';
  return a.slice(0, 8) + '…' + a.slice(-6);
}

function setConn(ok) {
  $('conn-dot').className = 'hdot ' + (ok ? 'ok' : 'error');
  $('conn-text').textContent = ok ? '已连接' : '连接失败';
}

function updatePollStatus(overview) {
  const stale =
    !overview?.last_metric_ts ||
    Date.now() / 1000 - overview.last_metric_ts > 300;
  $('poll-dot').className = 'hdot ' + (stale ? 'warn' : 'ok');
  $('poll-label').textContent = stale ? '数据延迟' : '监控中';
}

function applySort() {
  if (!sortState.field) return;
  const { field, dir } = sortState;
  tokenState.sort((a, b) => {
    const va = a[field] ?? -Infinity;
    const vb = b[field] ?? -Infinity;
    if (va === vb) return 0;
    if (va == null) return 1;
    if (vb == null) return -1;
    const diff = Number(va) - Number(vb);
    return dir === 'asc' ? diff : -diff;
  });
}

function updateSortIndicators() {
  document.querySelectorAll('#tbl-monitor th.sortable').forEach((th) => {
    const icon = th.querySelector('.sort-icon');
    if (!icon) return;
    if (th.dataset.sort === sortState.field) {
      th.classList.add('sorted');
      icon.textContent = sortState.dir === 'desc' ? '▼' : '▲';
    } else {
      th.classList.remove('sorted');
      icon.textContent = '↕';
    }
  });
}

function onSortClick(field) {
  if (sortState.field === field) {
    sortState.dir = sortState.dir === 'desc' ? 'asc' : 'desc';
  } else {
    sortState.field = field;
    sortState.dir = 'desc';
  }
  applySort();
  renderMonitorTable(false);
  updateSortIndicators();
}

function renderRow(t) {
  const sel = selectedSymbol === t.symbol ? ' selected' : '';
  const c15 = t.change_15m ?? (t.change_15m_pct != null ? t.change_15m_pct / 100 : null);
  const c24 = t.change_24h ?? (t.change_24h_pct != null ? t.change_24h_pct / 100 : null);
  return `<tr data-symbol="${t.symbol}" class="${sel}">
    <td data-field="base">${t.base || t.symbol.replace('USDT', '')}</td>
    <td class="num" data-field="price">${fmtNum(t.price)}</td>
    <td class="num ${pctClass(c15)}" data-field="change_15m">${fmtPct(c15 != null ? c15 * 100 : null)}</td>
    <td class="num ${pctClass(c24)}" data-field="change_24h">${fmtPct(c24 != null ? c24 * 100 : null)}</td>
    <td class="num" data-field="funding_rate">${fmtFunding(t.funding_rate, t.funding_interval_hours)}</td>
    <td class="num" data-field="oi">${fmtNum(t.oi)}</td>
    <td class="num" data-field="oi_mcap_ratio">${t.oi_mcap_ratio != null ? Number(t.oi_mcap_ratio).toFixed(3) : '—'}</td>
    <td class="num" data-field="whale_ls">${t.whale_long_short_ratio != null ? Number(t.whale_long_short_ratio).toFixed(2) : '—'}</td>
    <td class="cell-conclusion" data-field="conclusion">${t.conclusion || '—'}</td>
  </tr>`;
}

function renderMonitorTable(flash) {
  const tbody = $('monitor-body');
  if (!tokenState.length) {
    tbody.innerHTML =
      '<tr><td colspan="9" class="empty-row">暂无监控数据，请确认 poll 已运行</td></tr>';
    $('token-count').textContent = '0';
    return;
  }

  tbody.innerHTML = tokenState.map(renderRow).join('');
  $('token-count').textContent = String(tokenState.length);

  tbody.querySelectorAll('tr').forEach((tr) => {
    tr.onclick = () => selectToken(tr.dataset.symbol);
  });

  if (flash) {
    tokenState.forEach((t) => {
      const prev = tokenStatePrev[t.symbol];
      if (!prev) return;
      const row = tbody.querySelector(`tr[data-symbol="${t.symbol}"]`);
      if (!row) return;
      flashCell(row, 'price', t.price, prev.price, fmtNum);
      flashPctCell(row, 'change_15m', t.change_15m, prev.change_15m);
      flashPctCell(row, 'change_24h', t.change_24h, prev.change_24h);
      if (t.funding_rate !== prev.funding_rate) {
        updateCell(
          row,
          'funding_rate',
          fmtFunding(t.funding_rate, t.funding_interval_hours),
          t.funding_rate >= (prev.funding_rate ?? 0) ? 'up' : 'down',
        );
      }
      flashCell(row, 'oi', t.oi, prev.oi, fmtNum);
    });
  }

  if (selectedSymbol) {
    tbody.querySelectorAll('tr').forEach((tr) => {
      tr.classList.toggle('selected', tr.dataset.symbol === selectedSymbol);
    });
  }
}

function flashCell(tr, field, val, prevVal, fmt) {
  if (prevVal == null || val === prevVal) return;
  updateCell(tr, field, fmt(val), val >= prevVal ? 'up' : 'down');
}

function flashPctCell(tr, field, val, prevVal) {
  if (val == null) return;
  const pct = val * 100;
  const td = tr.querySelector(`[data-field="${field}"]`);
  if (!td) return;
  const html = fmtPct(pct);
  if (prevVal != null && val !== prevVal) {
    updateCell(tr, field, html, val >= prevVal ? 'up' : 'down');
  }
  td.className = `num ${pctClass(pct)}`;
}

function updateCell(tr, field, html, flash) {
  const td = tr.querySelector(`[data-field="${field}"]`);
  if (!td || td.innerHTML === html) return;
  td.innerHTML = html;
  if (flash) {
    td.classList.remove('cell-flash-up', 'cell-flash-down');
    void td.offsetWidth;
    td.classList.add(flash === 'up' ? 'cell-flash-up' : 'cell-flash-down');
  }
}

function selectToken(symbol) {
  selectedSymbol = symbol;
  const t = tokenState.find((x) => x.symbol === symbol);
  $('detail-symbol').textContent = symbol;
  $('narrative').textContent = t?.narrative || '—';
  document.querySelectorAll('#monitor-body tr').forEach((tr) => {
    tr.classList.toggle('selected', tr.dataset.symbol === symbol);
  });
}

function renderTokensTable(list) {
  if (!list.length) {
    $('tokens-body').innerHTML =
      '<tr><td colspan="6" class="empty-row">暂无持久化代币，poll 后会自动解析</td></tr>';
    return;
  }
  $('tokens-body').innerHTML = list
    .map(
      (m) => `<tr>
      <td>${m.base_asset}</td>
      <td>${m.symbol}</td>
      <td>${m.chain || '—'}</td>
      <td class="contract" title="${m.token_contract || ''}">${shortAddr(m.token_contract)}</td>
      <td>${m.coingecko_id || '—'}</td>
      <td class="num">${m.updated_time?.slice(5, 16) || '—'}</td>
    </tr>`,
    )
    .join('');
}

async function fetchJson(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(r.statusText);
  return r.json();
}

async function refresh() {
  try {
    const [overview, tokens, metadata] = await Promise.all([
      fetchJson('/api/overview'),
      fetchJson('/api/monitor-tokens?limit=100'),
      fetchJson('/api/token-metadata?limit=100'),
    ]);

    const hadData = tokenState.length > 0;
    tokenStatePrev = Object.fromEntries(tokenState.map((t) => [t.symbol, { ...t }]));

    tokenState = tokens;
    applySort();
    renderMonitorTable(hadData);
    updateSortIndicators();
    renderTokensTable(metadata);

    if (!selectedSymbol && tokenState.length) {
      selectToken(tokenState[0].symbol);
    } else if (selectedSymbol) {
      selectToken(selectedSymbol);
    }

    setConn(true);
    updatePollStatus(overview);

    const ts = overview.last_metric_time;
    $('last-refresh').textContent = ts ? ts.slice(11) : '—';
  } catch (e) {
    console.error(e);
    setConn(false);
  }
}

function initSortHeaders() {
  document.querySelectorAll('#tbl-monitor th.sortable .sort-btn').forEach((btn) => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      onSortClick(btn.closest('th').dataset.sort);
    });
  });
}

function initTabs() {
  document.querySelectorAll('.tab-btn[data-tab]').forEach((btn) => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.tab-btn').forEach((b) => b.classList.remove('active'));
      document.querySelectorAll('.tab-panel').forEach((p) => p.classList.remove('active'));
      btn.classList.add('active');
      $(`tab-${btn.dataset.tab}`).classList.add('active');
    });
  });
}

function updateClock() {
  $('clock').textContent = new Date().toLocaleString('zh-CN', { hour12: false });
}

document.addEventListener('DOMContentLoaded', () => {
  initTabs();
  initSortHeaders();
  updateSortIndicators();
  updateClock();
  setInterval(updateClock, 1000);
  refresh();
  setInterval(refresh, REFRESH_MS);
});
