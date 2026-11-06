const REFRESH_MS = 2000;
let latest = null;
let lastTradeId = null;
let lastEntryOrderId = null;
let firstLoad = true;
let refreshTimer = null;

const $ = (id) => document.getElementById(id);

function money(value, digits = 2) {
  const n = Number(value);
  if (!Number.isFinite(n)) return '—';
  return n.toLocaleString('en-US', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function signedMoney(value, digits = 2) {
  const n = Number(value);
  if (!Number.isFinite(n)) return '—';
  return `${n >= 0 ? '+' : ''}${money(n, digits)}`;
}

function pct(value, digits = 2) {
  const n = Number(value);
  if (!Number.isFinite(n)) return '—';
  return `${(n * 100).toFixed(digits)}%`;
}

function fmtTime(value) {
  if (!value) return '—';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return String(value).replace('T', ' ').slice(0, 19);
  return d.toLocaleString('vi-VN', {
    day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit',
  });
}

function clsFor(value) {
  const n = Number(value);
  if (!Number.isFinite(n) || n === 0) return '';
  return n > 0 ? 'green' : 'red';
}

function esc(text) {
  return String(text ?? '')
    .replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;').replaceAll("'", '&#039;');
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  let data = {};
  try { data = await res.json(); } catch (_) { data = {}; }
  if (!res.ok) throw new Error(data.message || data.error || `HTTP ${res.status}`);
  return data;
}

function toast(message, kind = '') {
  const node = document.createElement('div');
  node.className = `toast ${kind}`;
  node.textContent = message;
  $('toasts').appendChild(node);
  setTimeout(() => {
    node.style.opacity = '0';
    node.style.transform = 'translateY(6px)';
    node.style.transition = 'all .2s ease';
    setTimeout(() => node.remove(), 220);
  }, 5200);
}

function statusMeta(state) {
  switch (state) {
    case 'running': return ['RUNNING', 'ok'];
    case 'stopping': return ['STOPPING', 'warn'];
    case 'force_closing': return ['FORCE CLOSING', 'danger'];
    case 'stopped_with_positions': return ['STOPPED · CÒN POSITION', 'danger'];
    case 'stopped': return ['STOPPED', 'warn'];
    default: return ['UNKNOWN', ''];
  }
}

function setOffline(error) {
  const pill = $('statusPill');
  pill.className = 'pill danger';
  pill.textContent = 'OFFLINE';
  $('updatedAt').textContent = `Mất kết nối: ${error}`;
}

function render(data) {
  latest = data;
  const p = data.portfolio || {};
  const cfg = data.config || {};
  const process = data.process || {};
  const [text, cls] = statusMeta(data.state);
  const pill = $('statusPill');
  pill.className = `pill ${cls}`;
  pill.textContent = text;

  $('marketLabel').textContent = `${data.market?.symbol || '—'} · ${data.market?.timeframe || '—'}`;
  $('symbolLabel').textContent = data.market?.symbol || '—';
  $('lastPrice').textContent = data.market?.mark_price ? money(data.market.mark_price, 2) : '—';

  $('equity').textContent = money(p.equity);
  $('equity').className = `stat-value ${clsFor(p.equity - cfg.initial_balance)}`;
  $('equitySub').textContent = `Peak ${money(p.peak_equity)} · ${signedMoney(p.equity - cfg.initial_balance)} USDT`;
  $('freeCash').textContent = money(p.free_cash);
  $('realized').textContent = signedMoney(p.realized_pnl);
  $('realized').className = `stat-value ${clsFor(p.realized_pnl)}`;
  $('winRate').textContent = `Win rate ${pct(data.stats?.win_rate || 0, 1)} · ${data.stats?.trades || 0} lệnh`;
  $('unrealized').textContent = signedMoney(p.unrealized_pnl);
  $('unrealized').className = `stat-value ${clsFor(p.unrealized_pnl)}`;
  $('openPositions').textContent = p.open_positions ?? 0;
  $('exposure').textContent = `Exposure ${money(p.exposure)} USDT`;
  $('drawdown').textContent = pct(p.drawdown_pct);
  $('drawdown').className = `stat-value ${p.drawdown_pct > 0.05 ? 'red' : p.drawdown_pct > 0.01 ? 'amber' : ''}`;
  $('peakEquity').textContent = `Peak ${money(p.peak_equity)}`;
  $('circuit').textContent = p.circuit_open ? 'HALT' : 'OK';
  $('circuit').className = `stat-value small ${p.circuit_open ? 'red' : 'green'}`;
  $('circuitSub').textContent = p.circuit_reason || '—';

  $('kvState').textContent = data.state;
  $('kvControl').textContent = process.control || '—';
  $('kvSymbol').textContent = cfg.symbol || '—';
  $('kvTimeframe').textContent = cfg.timeframe || '—';
  $('kvTranches').textContent = `${cfg.tranches || '—'} × ${pct(1 / (cfg.tranches || 1), 0)}`;
  $('kvRisk').textContent = `${pct(cfg.risk_per_trade_pct || 0, 1)} equity / lệnh`;
  $('kvThresholds').textContent = `UP ≥ ${pct(cfg.long_threshold || 0, 0)} · DOWN ≥ ${pct(1 - (cfg.short_threshold || 0), 0)}`;
  $('kvLosses').textContent = `${p.consecutive_losses ?? 0} / ${cfg.circuit_breaker_consecutive_losses || '—'}`;
  $('kvCooldown').textContent = data.risk?.cooldown_until ? fmtTime(data.risk.cooldown_until) : '—';
  $('kvMaxDD').textContent = pct(cfg.max_drawdown_pct || 0, 0);
  $('pidLabel').textContent = process.running ? `PID ${process.pid}` : 'bot không chạy';

  $('btnStart').disabled = !!process.running;
  $('btnStop').disabled = !process.running || data.state === 'force_closing';
  $('btnForce').disabled = (data.state === 'force_closing') || (!process.running && !(data.positions || []).length);
  $('btnReset').disabled = !!process.running;

  renderPositions(data.positions || []);
  renderOrders(data.recent_orders || []);
  renderLearning(data.learning || {}, cfg);
  renderTrades(data.recent_trades || []);
  renderSignals(data.recent_signals || []);
  renderLogs(data.logs || []);
  drawChart(data.equity_curve || [], cfg.initial_balance || 100);

  $('positionsHint').textContent = `${(data.positions || []).length} position`;
  $('ordersHint').textContent = `${(data.recent_orders || []).length} order · ${(data.working_orders || []).length} đang chờ`;
  $('tradesHint').textContent = `${data.stats?.trades || 0} lệnh đã đóng`;
  $('signalsHint').textContent = `${(data.recent_signals || []).length} signal gần nhất`;
  $('equityRange').textContent = `${(data.equity_curve || []).length} điểm`;
  $('updatedAt').textContent = `Cập nhật ${fmtTime(data.server_time)}`;

  const entryOrders = (data.recent_orders || []).filter(
    (o) => o.order_type === 'market_entry' && o.status === 'filled',
  );
  const latestEntry = entryOrders[entryOrders.length - 1];
  if (latestEntry && latestEntry.id !== lastEntryOrderId) {
    if (!firstLoad && lastEntryOrderId !== null) {
      toast(
        `ĐÃ KHỚP LỆNH ${String(latestEntry.side).toUpperCase()} ${latestEntry.qty} @ ${money(latestEntry.fill_price, 2)}`,
        'ok',
      );
    }
    lastEntryOrderId = latestEntry.id;
  }

  if (data.last_trade && data.last_trade.id !== lastTradeId) {
    if (!firstLoad && lastTradeId !== null) {
      const pnl = Number(data.last_trade.pnl);
      toast(
        `${pnl >= 0 ? 'LÃI' : 'LỖ'} ${signedMoney(pnl)} USDT · ${String(data.last_trade.reason || '').replaceAll('_', ' ')}`,
        pnl >= 0 ? 'ok' : 'err',
      );
    }
    lastTradeId = data.last_trade.id;
  }
  firstLoad = false;
}

function renderPositions(rows) {
  const body = $('positionsBody');
  if (!rows.length) {
    body.innerHTML = '<tr><td colspan="10" class="empty">Chưa có position nào.</td></tr>';
    return;
  }
  body.innerHTML = rows.map((r) => {
    const side = String(r.side || '').toLowerCase();
    const pnl = Number(r.unrealized_pnl);
    return `<tr>
      <td>${esc(fmtTime(r.opened_at))}</td>
      <td><span class="side ${side}">${esc(side.toUpperCase())}</span></td>
      <td>${money(r.entry_price, 2)}</td>
      <td>${money(r.mark_price, 2)}</td>
      <td>${money(r.qty, 6)}</td>
      <td>${money(r.margin, 2)}</td>
      <td class="red">${money(r.stop_loss, 2)}</td>
      <td class="green">${money(r.take_profit, 2)}</td>
      <td class="${clsFor(pnl)}">${signedMoney(pnl, 4)}</td>
      <td class="${clsFor(pnl)}">${pct(r.pnl_pct || 0)}</td>
    </tr>`;
  }).join('');
}

function orderStatusLabel(status) {
  switch (String(status || '').toLowerCase()) {
    case 'working': return 'ĐANG CHỜ SL/TP';
    case 'filled': return 'ĐÃ KHỚP';
    case 'cancelled': return 'ĐÃ HUỶ';
    case 'rejected': return 'TỪ CHỐI';
    default: return String(status || '—').toUpperCase();
  }
}

function orderTypeLabel(type) {
  switch (type) {
    case 'market_entry': return 'MARKET ENTRY';
    case 'stop_loss': return 'STOP LOSS';
    case 'take_profit': return 'TAKE PROFIT';
    case 'market_exit': return 'MARKET EXIT';
    default: return String(type || '—').toUpperCase();
  }
}

function renderOrders(rows) {
  const body = $('ordersBody');
  if (!rows.length) {
    body.innerHTML = '<tr><td colspan="9" class="empty">Chưa có order. Bot sẽ tạo market entry + SL/TP khi signal được duyệt.</td></tr>';
    return;
  }
  body.innerHTML = rows.slice().reverse().map((r) => {
    const status = String(r.status || '').toLowerCase();
    return `<tr>
      <td>${esc(fmtTime(r.created_at))}</td>
      <td class="order-type">${esc(orderTypeLabel(r.order_type))}</td>
      <td><span class="side ${String(r.side).toLowerCase()}">${esc(String(r.side || '').toUpperCase())}</span></td>
      <td>${money(r.qty, 6)}</td>
      <td>${money(r.requested_price, 2)}</td>
      <td>${r.fill_price == null ? '—' : money(r.fill_price, 2)}</td>
      <td>${money(r.fee || 0, 5)}</td>
      <td><span class="status-chip ${status}">${esc(orderStatusLabel(status))}</span></td>
      <td>${r.filled_at ? esc(fmtTime(r.filled_at)) : '—'}</td>
    </tr>`;
  }).join('');
}

function renderLearning(learning, cfg) {
  const stats = learning.stats || {};
  const cal = learning.calibration || {};
  $('learnEvaluated').textContent = stats.evaluated ?? 0;
  $('learnPending').textContent = stats.pending ?? 0;
  $('learnHitRate').textContent = stats.hit_rate == null ? '—' : pct(stats.hit_rate, 1);
  $('learnBrier').textContent = stats.brier == null ? '—' : Number(stats.brier).toFixed(4);
  $('learnMae').textContent = stats.mae == null ? '—' : pct(stats.mae, 2);
  $('learnCandles').textContent = `${learning.candles_stored ?? 0} nến · LB ${cfg.lookback_current ?? '—'}/${cfg.lookback_max ?? '—'}`;
  $('learnLong').textContent = cal.long_threshold == null ? '—' : `p_up ≥ ${pct(cal.long_threshold, 1)}`;
  $('learnShort').textContent = cal.short_threshold == null ? '—' : `p_down ≥ ${pct(1 - cal.short_threshold, 1)}`;

  const rows = learning.recent_forecasts || [];
  const body = $('forecastsBody');
  if (!rows.length) {
    body.innerHTML = '<tr><td colspan="10" class="empty">Chưa có forecast. Bot sẽ lưu mỗi lần dự đoán và đánh giá sau khi đủ horizon.</td></tr>';
    return;
  }
  body.innerHTML = rows.map((r) => {
    const evaluated = !!r.evaluated_at;
    const hit = !evaluated ? '<span class="status-chip working">CHỜ ĐÁNH GIÁ</span>'
      : (Number(r.direction_correct) === 1
        ? '<span class="status-chip filled">ĐÚNG</span>'
        : '<span class="status-chip rejected">SAI</span>');
    const actual = evaluated && r.actual_return != null
      ? `<span class="${clsFor(r.actual_return)}">${(Number(r.actual_return) * 100).toFixed(2)}%</span>`
      : '—';
    return `<tr>
      <td>${esc(fmtTime(r.created_at))}</td>
      <td>${r.lookback ?? '—'}</td>
      <td>${pct(r.p_up || 0, 1)}</td>
      <td>${pct(r.p_down || 0, 1)}</td>
      <td class="${clsFor(r.expected_return)}">${(Number(r.expected_return) * 100).toFixed(2)}%</td>
      <td><span class="side ${String(r.signal).toLowerCase()}">${esc(String(r.signal || '').toUpperCase())}</span></td>
      <td>${esc(fmtTime(r.horizon_end))}</td>
      <td>${actual}</td>
      <td>${hit}</td>
      <td>${evaluated && r.brier != null ? Number(r.brier).toFixed(4) : '—'}</td>
    </tr>`;
  }).join('');
}

function renderTrades(rows) {
  const body = $('tradesBody');
  if (!rows.length) {
    body.innerHTML = '<tr><td colspan="8" class="empty">Chưa có lệnh nào.</td></tr>';
    return;
  }
  body.innerHTML = rows.map((r) => {
    const side = String(r.side || '').toLowerCase();
    return `<tr>
      <td>${esc(fmtTime(r.closed_at))}</td>
      <td><span class="side ${side}">${esc(side.toUpperCase())}</span></td>
      <td>${money(r.entry_price, 2)}</td>
      <td>${money(r.exit_price, 2)}</td>
      <td>${money(r.qty, 6)}</td>
      <td class="${clsFor(r.pnl)}">${signedMoney(r.pnl, 4)}</td>
      <td class="${clsFor(r.pnl)}">${pct(r.return_pct || 0)}</td>
      <td class="reason" title="${esc(r.reason)}">${esc(String(r.reason || '').replaceAll('_', ' '))}</td>
    </tr>`;
  }).join('');
}

function renderSignals(rows) {
  const body = $('signalsBody');
  if (!rows.length) {
    body.innerHTML = '<tr><td colspan="6" class="empty">Chưa có signal.</td></tr>';
    return;
  }
  body.innerHTML = rows.map((r) => {
    const sig = String(r.signal || '').toLowerCase();
    const approved = String(r.decision || '').toLowerCase() === 'approved';
    return `<tr>
      <td>${esc(fmtTime(r.ts))}</td>
      <td><span class="side ${sig}">${esc(sig.toUpperCase())}</span></td>
      <td>${pct(r.p_up || 0, 1)}</td>
      <td>${pct(r.p_down || 0, 1)}</td>
      <td class="${clsFor(r.expected_return)}">${(Number(r.expected_return) * 100).toFixed(2)}%</td>
      <td><span class="decision ${approved ? 'approved' : 'rejected'}">${esc(approved ? 'APPROVED' : 'REJECTED')}</span>
        <span class="muted"> ${esc(r.decision_reason || '')}</span></td>
    </tr>`;
  }).join('');
}

function renderLogs(lines) {
  const pre = $('logs');
  if (!lines.length) {
    pre.textContent = 'Chưa có log. Bấm Start test để bắt đầu.';
    return;
  }
  const nearBottom = pre.scrollHeight - pre.scrollTop - pre.clientHeight < 60;
  pre.textContent = lines.join('\n');
  if (nearBottom) pre.scrollTop = pre.scrollHeight;
}

function drawChart(points, initial) {
  const canvas = $('equityChart');
  if (!canvas) return;
  const wrap = canvas.parentElement;
  const dpr = window.devicePixelRatio || 1;
  const width = wrap.clientWidth;
  const height = wrap.clientHeight;
  if (!width || !height) return;
  canvas.width = width * dpr;
  canvas.height = height * dpr;
  const ctx = canvas.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, width, height);

  const values = points.map((x) => Number(x.equity)).filter(Number.isFinite);
  if (!values.length) values.push(Number(initial) || 100);
  const pad = { l: 62, r: 16, t: 14, b: 26 };
  const plotW = width - pad.l - pad.r;
  const plotH = height - pad.t - pad.b;
  let min = Math.min(...values, initial);
  let max = Math.max(...values, initial);
  const span = Math.max(max - min, Math.abs(initial) * 0.002, 0.1);
  min -= span * 0.15;
  max += span * 0.15;

  const xAt = (i) => pad.l + (i / Math.max(1, values.length - 1)) * plotW;
  const yAt = (v) => pad.t + (1 - (v - min) / (max - min)) * plotH;

  // grid
  ctx.font = '10px "SFMono-Regular", Menlo, monospace';
  ctx.textAlign = 'right';
  ctx.textBaseline = 'middle';
  for (let i = 0; i <= 4; i++) {
    const value = min + ((max - min) * i) / 4;
    const y = yAt(value);
    ctx.strokeStyle = 'rgba(29,42,58,.55)';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(pad.l, y);
    ctx.lineTo(width - pad.r, y);
    ctx.stroke();
    ctx.fillStyle = '#6d8097';
    ctx.fillText(money(value), pad.l - 8, y);
  }

  // initial capital dashed line
  const initialY = yAt(initial);
  ctx.setLineDash([4, 5]);
  ctx.strokeStyle = 'rgba(128,145,167,.45)';
  ctx.beginPath();
  ctx.moveTo(pad.l, initialY);
  ctx.lineTo(width - pad.r, initialY);
  ctx.stroke();
  ctx.setLineDash([]);

  const grad = ctx.createLinearGradient(0, pad.t, 0, height - pad.b);
  grad.addColorStop(0, 'rgba(46,230,166,.30)');
  grad.addColorStop(1, 'rgba(46,230,166,0)');

  ctx.beginPath();
  values.forEach((v, i) => {
    const x = xAt(i);
    const y = yAt(v);
    if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
  });
  ctx.lineTo(xAt(values.length - 1), height - pad.b);
  ctx.lineTo(xAt(0), height - pad.b);
  ctx.closePath();
  ctx.fillStyle = grad;
  ctx.fill();

  ctx.beginPath();
  values.forEach((v, i) => {
    const x = xAt(i);
    const y = yAt(v);
    if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
  });
  ctx.strokeStyle = '#2ee6a6';
  ctx.lineWidth = 2;
  ctx.stroke();

  const lastValue = values[values.length - 1];
  const lastX = xAt(values.length - 1);
  const lastY = yAt(lastValue);
  ctx.beginPath();
  ctx.arc(lastX, lastY, 3.6, 0, Math.PI * 2);
  ctx.fillStyle = '#2ee6a6';
  ctx.fill();
  ctx.beginPath();
  ctx.arc(lastX, lastY, 7, 0, Math.PI * 2);
  ctx.fillStyle = 'rgba(46,230,166,.18)';
  ctx.fill();

  // x labels
  ctx.textAlign = 'left';
  ctx.fillStyle = '#6d8097';
  if (points.length) {
    ctx.fillText(fmtTime(points[0].ts), pad.l, height - 8);
    ctx.textAlign = 'right';
    ctx.fillText(fmtTime(points[points.length - 1].ts), width - pad.r, height - 8);
  }
}

async function poll() {
  try {
    const data = await api('/api/status');
    render(data);
  } catch (err) {
    setOffline(err.message);
  }
}

async function action(path, payload = {}, confirmText = '') {
  if (confirmText && !window.confirm(confirmText)) return;
  try {
    const data = await api(path, { method: 'POST', body: JSON.stringify(payload) });
    toast(data.message || 'OK', data.ok ? 'ok' : 'err');
  } catch (err) {
    toast(err.message, 'err');
  }
  await poll();
  if (refreshTimer) clearInterval(refreshTimer);
  refreshTimer = setInterval(poll, REFRESH_MS);
}

$('btnStart').addEventListener('click', () => action('/api/start'));
$('btnStop').addEventListener('click', () => action('/api/stop'));
$('btnForce').addEventListener('click', () => action('/api/force-close', {}, 'Chốt sổ ngay: đóng toàn bộ position tại giá thị trường?'));
$('btnReset').addEventListener('click', () => action('/api/reset', {}, 'Reset ledger về 100 USDT? Toàn bộ lịch sử paper sẽ bị xoá.'));

window.addEventListener('resize', () => {
  if (latest) drawChart(latest.equity_curve || [], latest.config?.initial_balance || 100);
});

poll();
refreshTimer = setInterval(poll, REFRESH_MS);
