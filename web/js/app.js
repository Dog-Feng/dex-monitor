const REFRESH_MS = 10000;
let anomaliesCache = [];

function $(id) { return document.getElementById(id); }

function fmtPct(v, digits = 2) {
  if (v == null || Number.isNaN(v)) return '—';
  const n = Number(v);
  const s = n >= 0 ? '+' : '';
  return s + n.toFixed(digits) + '%';
}

function fmtFunding(r) {
  if (r == null) return '—';
  return (Number(r) * 100).toFixed(4) + '%';
}

function fmtNum(n) {
  if (n == null) return '—';
  const x = Number(n);
  if (x >= 1e9) return (x / 1e9).toFixed(2) + 'B';
  if (x >= 1e6) return (x / 1e6).toFixed(2) + 'M';
  if (x >= 1e3) return (x / 1e3).toFixed(2) + 'K';
  return x.toFixed(4);
}

function shortAddr(a) {
  if (!a || a.length < 12) return a || '—';
  return a.slice(0, 8) + '…' + a.slice(-6);
}

function tagHtml(tags) {
  if (!tags || !tags.length) return '—';
  return tags.map(t => {
    let cls = 'tag';
    if (t.includes('squeeze') || t.includes('chase')) cls += ' squeeze';
    if (t.includes('dump') || t.includes('liquidation')) cls += ' dump';
    return `<span class="${cls}">${t}</span>`;
  }).join('');
}

function setConn(ok) {
  const dot = $('conn-dot');
  const text = $('conn-text');
  dot.className = 'hdot ' + (ok ? 'ok' : 'error');
  text.textContent = ok ? '已连接' : '连接失败';
}

function updateClock() {
  const now = new Date();
  $('clock').textContent = now.toLocaleString('zh-CN', { hour12: false });
}

async function fetchJson(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(r.statusText);
  return r.json();
}

function renderOverview(ov) {
  $('ov-total').textContent = ov.total_24h ?? '0';
  $('ov-surge').textContent = ov.surge_24h ?? '0';
  $('ov-dump').textContent = ov.dump_24h ?? '0';
  $('ov-symbols').textContent = ov.symbols_count ?? '0';
  $('ov-meta').textContent = ov.metadata_count ?? '0';
  $('ov-7d').textContent = ov.events_7d ?? '0';

  const pollDot = $('poll-dot');
  const stale = !ov.last_metric_ts || (Date.now() / 1000 - ov.last_metric_ts) > 300;
  pollDot.className = 'hdot ' + (stale ? 'warn' : 'ok');
  $('poll-label').textContent = stale ? '数据延迟' : '监控中';
}

function renderEventDetail(ev) {
  if (!ev) {
    $('event-detail').innerHTML = '<div class="empty">点击左侧事件查看归因</div>';
    $('event-narrative').textContent = '选择一条事件';
    return;
  }
  const chgClass = ev.change_15m >= 0 ? 'up' : 'down';
  $('event-detail').innerHTML = `
    <div class="kv"><span class="lbl">币种</span><span class="val">${ev.symbol}</span></div>
    <div class="kv"><span class="lbl">时间</span><span class="val">${ev.detected_time}</span></div>
    <div class="kv"><span class="lbl">类型</span><span class="val">${ev.anomaly_type} · <span class="sev-${ev.severity}">${ev.severity}</span></span></div>
    <div class="kv"><span class="lbl">15m 涨跌</span><span class="val ${chgClass}">${fmtPct(ev.change_15m_pct)}</span></div>
    <div class="kv"><span class="lbl">资金费率</span><span class="val">${fmtFunding(ev.funding_rate)}</span></div>
    <div class="kv"><span class="lbl">标签</span><span class="val">${tagHtml(ev.tags)}</span></div>
  `;
  $('event-narrative').textContent = ev.narrative || '—';
}

function bindEventRows(selector, onSelect) {
  document.querySelectorAll(selector).forEach(tr => {
    tr.onclick = () => {
      document.querySelectorAll(selector).forEach(r => r.classList.remove('selected'));
      tr.classList.add('selected');
      const id = Number(tr.dataset.id);
      const ev = anomaliesCache.find(x => x.id === id);
      onSelect(ev);
    };
  });
}

function renderRecentEvents(list) {
  const tbody = $('tbl-recent').querySelector('tbody');
  if (!list.length) {
    tbody.innerHTML = '<tr><td colspan="4" class="empty">暂无异常，运行 poll 后会出现在此</td></tr>';
    return;
  }
  tbody.innerHTML = list.slice(0, 8).map(ev => {
    const cls = ev.change_15m >= 0 ? 'up' : 'down';
    return `<tr data-id="${ev.id}">
      <td>${ev.detected_time?.slice(11) || '—'}</td>
      <td>${ev.symbol}</td>
      <td>${ev.anomaly_type}</td>
      <td class="${cls}">${fmtPct(ev.change_15m_pct)}</td>
    </tr>`;
  }).join('');
  bindEventRows('#tbl-recent tbody tr', renderEventDetail);
}

function renderEventsTable(list) {
  const tbody = $('tbl-events').querySelector('tbody');
  if (!list.length) {
    tbody.innerHTML = '<tr><td colspan="7" class="empty">暂无数据</td></tr>';
    return;
  }
  tbody.innerHTML = list.map(ev => {
    const cls = ev.change_15m >= 0 ? 'up' : 'down';
    return `<tr data-id="${ev.id}">
      <td>${ev.detected_time || '—'}</td>
      <td>${ev.symbol}</td>
      <td class="sev-${ev.severity}">${ev.severity}</td>
      <td>${ev.anomaly_type}</td>
      <td class="${cls}">${fmtPct(ev.change_15m_pct)}</td>
      <td>${fmtFunding(ev.funding_rate)}</td>
      <td>${tagHtml(ev.tags)}</td>
    </tr>`;
  }).join('');
  bindEventRows('#tbl-events tbody tr', ev => {
    renderEventDetail(ev);
    document.querySelector('[data-tab="events"]').click();
  });
}

function renderMetrics(list) {
  const tbody = $('tbl-metrics').querySelector('tbody');
  if (!list.length) {
    tbody.innerHTML = '<tr><td colspan="7" class="empty">暂无指标，请先运行 poll</td></tr>';
    return;
  }
  tbody.innerHTML = list.map(m => `<tr>
    <td>${m.symbol}</td>
    <td>${m.time?.slice(11) || '—'}</td>
    <td>${fmtNum(m.price)}</td>
    <td>${fmtFunding(m.funding_rate)}</td>
    <td>${fmtNum(m.oi)}</td>
    <td>${m.oi_mcap_ratio != null ? Number(m.oi_mcap_ratio).toFixed(3) : '—'}</td>
    <td>${m.whale_long_short_ratio != null ? Number(m.whale_long_short_ratio).toFixed(2) : '—'}</td>
  </tr>`).join('');
}

function renderTokens(list) {
  const tbody = $('tbl-tokens').querySelector('tbody');
  if (!list.length) {
    tbody.innerHTML = '<tr><td colspan="6" class="empty">暂无持久化代币，discover/poll 后会自动解析</td></tr>';
    return;
  }
  tbody.innerHTML = list.map(t => `<tr>
    <td>${t.base_asset}</td>
    <td>${t.symbol}</td>
    <td>${t.chain || '—'}</td>
    <td class="contract" title="${t.token_contract || ''}">${shortAddr(t.token_contract)}</td>
    <td>${t.coingecko_id || '—'}</td>
    <td>${t.updated_time?.slice(0, 16) || '—'}</td>
  </tr>`).join('');
}

async function refresh() {
  try {
    const days = Number($('filter-days')?.value || 7);
    const [ov, anomalies, metrics, tokens] = await Promise.all([
      fetchJson('/api/overview'),
      fetchJson(`/api/anomalies?days=${days}&limit=100`),
      fetchJson('/api/metrics?limit=80'),
      fetchJson('/api/token-metadata?limit=100'),
    ]);
    anomaliesCache = anomalies;
    setConn(true);
    renderOverview(ov);
    renderRecentEvents(anomalies);
    renderEventsTable(anomalies);
    renderMetrics(metrics);
    renderTokens(tokens);
  } catch (e) {
    console.error(e);
    setConn(false);
  }
}

function initTabs() {
  document.querySelectorAll('.tab-btn[data-tab]').forEach(btn => {
    btn.addEventListener('click', () => {
      if (btn.disabled) return;
      document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
      document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
      btn.classList.add('active');
      $(`tab-${btn.dataset.tab}`).classList.add('active');
    });
  });
}

document.addEventListener('DOMContentLoaded', () => {
  initTabs();
  updateClock();
  setInterval(updateClock, 1000);
  $('filter-days')?.addEventListener('change', refresh);
  refresh();
  setInterval(refresh, REFRESH_MS);
});
