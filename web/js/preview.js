/** 静态预览：代币维度一行 + 模拟行内动态刷新 + 列排序 */

const MOCK_TOKENS = [
  {
    symbol: 'GPSUSDT',
    base: 'GPS',
    price: 0.01234,
    change_24h: 12.6,
    change_15m: -3.52,
    funding_rate: -0.000068,
    funding_interval_hours: 8,
    oi: 12_500_000,
    oi_mcap_ratio: 0.251,
    whale_ls: 1.85,
    conclusion: '解锁窗口内，供应冲击风险偏高',
    narrative:
      'GPS 15m -3.5% | OI 30m: 0.0% | Funding: -0.007% | OI/MCap: 0.25\nTags: high_leverage, unlock_sell_pressure\n链上: 窗口内无显著充值/转出\n→ 解锁窗口内，供应冲击风险偏高',
  },
  {
    symbol: 'PORTALUSDT',
    base: 'PORTAL',
    price: 0.0892,
    change_24h: 18.4,
    change_15m: 11.25,
    funding_rate: 0.000512,
    funding_interval_hours: 4,
    oi: 8_200_000,
    oi_mcap_ratio: 0.182,
    whale_ls: 2.14,
    conclusion: '价涨+OI升+正费率，主动拉盘追多',
    narrative:
      'PORTAL 15m +11.2% | OI 30m +6.2% | Funding: +0.051%\n→ 价涨+OI升+正费率，主动拉盘追多',
  },
  {
    symbol: 'ACEUSDT',
    base: 'ACE',
    price: 1.245,
    change_24h: -15.2,
    change_15m: -9.18,
    funding_rate: 0.000891,
    funding_interval_hours: 8,
    oi: 3_100_000,
    oi_mcap_ratio: 0.328,
    whale_ls: 0.92,
    conclusion: '价跌+OI降，多头清算为主',
    narrative:
      'ACE 15m -9.2% | OI 30m -7.5% | Funding: +0.089%\n→ 价跌+OI降，多头清算为主',
  },
  {
    symbol: 'VELVETUSDT',
    base: 'VELVET',
    price: 0.00341,
    change_24h: -28.5,
    change_15m: 2.08,
    funding_rate: -0.001842,
    funding_interval_hours: 1,
    oi: 22_000_000,
    oi_mcap_ratio: 0.412,
    whale_ls: 1.12,
    conclusion: '高位极端负费率，空头极度拥挤，警惕轧空或反转',
    narrative:
      'VELVET 15m +2.1% | 费率 -0.184% 极端负值\n→ 高位极端负费率，空头极度拥挤',
  },
  {
    symbol: 'CAKEUSDT',
    base: 'CAKE',
    price: 2.156,
    change_24h: 4.2,
    change_15m: 0.65,
    funding_rate: 0.000102,
    funding_interval_hours: 8,
    oi: 45_000_000,
    oi_mcap_ratio: 0.088,
    whale_ls: 1.05,
    conclusion: '暂无明显异常结构，持续监控中',
    narrative: 'CAKE 暂无明显异常结构，持续监控中。',
  },
];

const MOCK_METADATA = [
  {
    base: 'GPS',
    symbol: 'GPSUSDT',
    chain: 'bsc',
    contract: '0x4a220e6096a25adb0e783b1a8e8c2a8893b6b9f8',
    cg: 'gps-ecosystem',
    updated: '2026-08-17 15:20',
  },
  {
    base: 'PORTAL',
    symbol: 'PORTALUSDT',
    chain: 'bsc',
    contract: '0x1bbe973bef3a40fc36f880eb7123073090ecef0',
    cg: 'portal-2',
    updated: '2026-08-17 15:18',
  },
  {
    base: 'CAKE',
    symbol: 'CAKEUSDT',
    chain: 'bsc',
    contract: '0x0e09fabb73bd3ade0a17ecc321fd13a19e81ce82',
    cg: 'pancakeswap-token',
    updated: '2026-08-17 14:02',
  },
];

let selectedSymbol = null;
let tokenState = structuredClone(MOCK_TOKENS);
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

function getConclusion(t) {
  if (t.conclusion) return t.conclusion;
  if (!t.narrative) return '—';
  const lines = t.narrative.split('\n');
  for (let i = lines.length - 1; i >= 0; i -= 1) {
    const line = lines[i].trim();
    if (line.startsWith('→')) return line.slice(1).trim();
  }
  return lines[0].trim() || '—';
}

function shortAddr(a) {
  if (!a || a.length < 12) return a || '—';
  return a.slice(0, 8) + '…' + a.slice(-6);
}

function applySort() {
  if (!sortState.field) return;
  const { field, dir } = sortState;
  tokenState.sort((a, b) => {
    const va = Number(a[field] ?? 0);
    const vb = Number(b[field] ?? 0);
    return dir === 'asc' ? va - vb : vb - va;
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
  renderMonitorTable();
  updateSortIndicators();
}

function renderRow(t) {
  const sel = selectedSymbol === t.symbol ? ' selected' : '';
  return `<tr data-symbol="${t.symbol}" class="${sel}">
    <td data-field="base">${t.base}</td>
    <td class="num" data-field="price">${fmtNum(t.price)}</td>
    <td class="num ${pctClass(t.change_15m)}" data-field="change_15m">${fmtPct(t.change_15m)}</td>
    <td class="num ${pctClass(t.change_24h)}" data-field="change_24h">${fmtPct(t.change_24h)}</td>
    <td class="num" data-field="funding_rate">${fmtFunding(t.funding_rate, t.funding_interval_hours)}</td>
    <td class="num" data-field="oi">${fmtNum(t.oi)}</td>
    <td class="num" data-field="oi_mcap_ratio">${Number(t.oi_mcap_ratio).toFixed(3)}</td>
    <td class="num" data-field="whale_ls">${Number(t.whale_ls).toFixed(2)}</td>
    <td class="cell-conclusion" data-field="conclusion">${getConclusion(t)}</td>
  </tr>`;
}

function renderMonitorTable() {
  const tbody = $('monitor-body');
  tbody.innerHTML = tokenState.map(renderRow).join('');
  $('token-count').textContent = String(tokenState.length);

  tbody.querySelectorAll('tr').forEach((tr) => {
    tr.onclick = () => selectToken(tr.dataset.symbol);
  });
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

function setPctCell(tr, field, value) {
  const td = tr.querySelector(`[data-field="${field}"]`);
  if (!td) return;
  const html = fmtPct(value);
  const flash = value >= 0 ? 'up' : 'down';
  if (td.innerHTML !== html) {
    td.innerHTML = html;
    td.classList.remove('cell-flash-up', 'cell-flash-down', 'up', 'down', 'mut');
    td.classList.add('num', pctClass(value));
    void td.offsetWidth;
    td.classList.add(flash === 'up' ? 'cell-flash-up' : 'cell-flash-down');
  } else {
    td.classList.remove('up', 'down', 'mut');
    td.classList.add(pctClass(value));
  }
}

function tickMockUpdate() {
  const now = new Date();
  $('last-refresh').textContent = now.toLocaleTimeString('zh-CN', { hour12: false });
  $('clock').textContent = now.toLocaleString('zh-CN', { hour12: false });

  tokenState.forEach((t) => {
    const drift = (Math.random() - 0.5) * 0.012;
    const oldPrice = t.price;
    t.price = Math.max(0.000001, t.price * (1 + drift));
    t.change_15m += (Math.random() - 0.5) * 0.35;
    t.change_24h += (Math.random() - 0.5) * 0.08;
    t.funding_rate += (Math.random() - 0.5) * 0.00005;
    t.oi = Math.max(1000, t.oi * (1 + (Math.random() - 0.5) * 0.02));
    t.whale_ls = Math.max(0.5, t.whale_ls + (Math.random() - 0.5) * 0.06);

    const tr = document.querySelector(`#monitor-body tr[data-symbol="${t.symbol}"]`);
    if (!tr) return;

    const priceFlash = t.price >= oldPrice ? 'up' : 'down';
    updateCell(tr, 'price', fmtNum(t.price), priceFlash);
    setPctCell(tr, 'change_15m', t.change_15m);
    setPctCell(tr, 'change_24h', t.change_24h);
    updateCell(tr, 'funding_rate', fmtFunding(t.funding_rate, t.funding_interval_hours), null);
    updateCell(tr, 'oi', fmtNum(t.oi), priceFlash);
    updateCell(tr, 'whale_ls', Number(t.whale_ls).toFixed(2), null);
  });

  if (sortState.field) {
    applySort();
    renderMonitorTable();
    updateSortIndicators();
    if (selectedSymbol) {
      document.querySelectorAll('#monitor-body tr').forEach((tr) => {
        tr.classList.toggle('selected', tr.dataset.symbol === selectedSymbol);
      });
    }
  }
}

function renderTokensTable() {
  $('tokens-body').innerHTML = MOCK_METADATA.map(
    (m) => `<tr>
      <td>${m.base}</td>
      <td>${m.symbol}</td>
      <td>${m.chain}</td>
      <td class="contract" title="${m.contract}">${shortAddr(m.contract)}</td>
      <td>${m.cg}</td>
      <td class="num">${m.updated.slice(5, 16)}</td>
    </tr>`,
  ).join('');
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

document.addEventListener('DOMContentLoaded', () => {
  initTabs();
  initSortHeaders();
  applySort();
  renderMonitorTable();
  renderTokensTable();
  updateSortIndicators();
  selectToken(tokenState[0].symbol);
  tickMockUpdate();
  setInterval(tickMockUpdate, 3000);
});
