"""Dashboard API routes — cost analysis and visualization data."""

from __future__ import annotations

from fastapi import APIRouter, Query
from fastapi.responses import HTMLResponse

from . import db

router = APIRouter(tags=["dashboard"])


# ── API endpoints ─────────────────────────────────────────────────

@router.get("/dashboard/summary")
async def summary(days: int = Query(30, ge=1, le=365)):
    """Monthly overview: total requests, cost, savings."""
    return db.query_summary(days)


@router.get("/dashboard/daily-costs")
async def daily_costs(days: int = Query(30, ge=1, le=365)):
    """Daily cost: strategy routing vs all-Opus comparison."""
    return db.query_daily_costs(days)


@router.get("/dashboard/policy-breakdown")
async def policy_breakdown(days: int = Query(30, ge=1, le=365)):
    """Cost breakdown by policy."""
    return db.query_policy_breakdown(days)


@router.get("/dashboard/cascade-stats")
async def cascade_stats(days: int = Query(30, ge=1, le=365)):
    """Cascade validation statistics."""
    return db.query_cascade_stats(days)


@router.get("/dashboard/recent")
async def recent_requests(limit: int = Query(50, ge=1, le=200)):
    """Recent request log."""
    return db.query_recent_requests(limit)


# ── Dashboard HTML page ───────────────────────────────────────────

@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page():
    """Serve the cost analysis dashboard."""
    return HTMLResponse(_DASHBOARD_HTML)


_DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>PolicyFlow Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js"></script>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         background: #0f172a; color: #e2e8f0; padding: 24px; }
  h1 { font-size: 24px; margin-bottom: 4px; }
  .subtitle { color: #94a3b8; font-size: 14px; margin-bottom: 24px; }
  .cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
           gap: 16px; margin-bottom: 24px; }
  .card { background: #1e293b; border-radius: 12px; padding: 20px; }
  .card .label { font-size: 13px; color: #94a3b8; }
  .card .value { font-size: 28px; font-weight: 700; margin-top: 4px; }
  .card .sub { font-size: 13px; margin-top: 4px; }
  .green { color: #34d399; }
  .red { color: #f87171; }
  .grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 24px; margin-bottom: 24px; }
  .panel { background: #1e293b; border-radius: 12px; padding: 20px; }
  .panel h2 { font-size: 16px; margin-bottom: 16px; color: #e2e8f0; }
  table { width: 100%; border-collapse: collapse; font-size: 14px; }
  th { text-align: left; color: #94a3b8; padding: 8px 4px; border-bottom: 1px solid #334155;
       font-weight: 500; }
  td { padding: 8px 4px; border-bottom: 1px solid #1e293b; }
  .suggestion { background: #1e293b; border-radius: 12px; padding: 20px; }
  .suggestion h2 { font-size: 16px; margin-bottom: 12px; }
  .suggestion li { padding: 6px 0; color: #94a3b8; font-size: 14px; }
  @media (max-width: 768px) { .grid-2 { grid-template-columns: 1fr; } }
</style>
</head>
<body>

<h1>PolicyFlow Dashboard</h1>
<div class="subtitle" id="date-range">Loading...</div>

<div class="cards">
  <div class="card">
    <div class="label">总请求</div>
    <div class="value" id="total-requests">-</div>
  </div>
  <div class="card">
    <div class="label">总成本</div>
    <div class="value" id="total-cost">-</div>
  </div>
  <div class="card">
    <div class="label">节省金额</div>
    <div class="value green" id="saved-amount">-</div>
  </div>
  <div class="card">
    <div class="label">节省比例</div>
    <div class="value green" id="saved-pct">-</div>
  </div>
</div>

<div class="grid-2">
  <div class="panel">
    <h2>每日成本：策略路由 vs 全用 Opus</h2>
    <canvas id="daily-chart"></canvas>
  </div>
  <div class="panel">
    <h2>按策略拆分</h2>
    <canvas id="policy-chart"></canvas>
  </div>
</div>

<div class="grid-2">
  <div class="panel">
    <h2>级联统计</h2>
    <div id="cascade-stats">Loading...</div>
  </div>
  <div class="panel">
    <h2>最近请求</h2>
    <div style="max-height:300px;overflow-y:auto;">
      <table id="recent-table"><tbody></tbody></table>
    </div>
  </div>
</div>

<div class="suggestion" style="margin-top:24px;">
  <h2>优化建议</h2>
  <ul id="suggestions"><li>Loading...</li></ul>
</div>

<script>
const DAYS = 30;

async function load() {
  const [summary, daily, policies, cascade, recent] = await Promise.all([
    fetch('/dashboard/summary?days=' + DAYS).then(r => r.json()),
    fetch('/dashboard/daily-costs?days=' + DAYS).then(r => r.json()),
    fetch('/dashboard/policy-breakdown?days=' + DAYS).then(r => r.json()),
    fetch('/dashboard/cascade-stats?days=' + DAYS).then(r => r.json()),
    fetch('/dashboard/recent?limit=20').then(r => r.json()),
  ]);

  document.getElementById('date-range').textContent =
    '最近 ' + DAYS + ' 天  |  ' + new Date().toISOString().slice(0, 10);

  // Summary cards
  document.getElementById('total-requests').textContent = summary.total_requests.toLocaleString();
  document.getElementById('total-cost').textContent = '$' + summary.total_cost.toFixed(2);
  document.getElementById('saved-amount').textContent = '$' + summary.saved_amount.toFixed(2);
  document.getElementById('saved-pct').textContent = summary.saved_pct + '%';

  // Daily costs chart
  const ctx1 = document.getElementById('daily-chart').getContext('2d');
  new Chart(ctx1, {
    type: 'line',
    data: {
      labels: daily.map(d => d.day.slice(5)),
      datasets: [
        { label: '策略路由', data: daily.map(d => d.actual_cost),
          borderColor: '#34d399', backgroundColor: 'rgba(52,211,153,0.1)',
          fill: true, tension: 0.3, pointRadius: 0 },
        { label: '全用 Opus', data: daily.map(d => d.compared_cost),
          borderColor: '#f87171', backgroundColor: 'rgba(248,113,113,0.05)',
          fill: true, tension: 0.3, pointRadius: 0,
          borderDash: [5, 5] },
      ]
    },
    options: {
      responsive: true,
      plugins: { legend: { labels: { color: '#94a3b8', usePointStyle: true } } },
      scales: {
        x: { ticks: { color: '#64748b', maxTicksLimit: 10 }, grid: { color: '#1e293b' } },
        y: { ticks: { color: '#64748b', callback: v => '$' + v.toFixed(2) },
             grid: { color: '#1e293b' } }
      }
    }
  });

  // Policy breakdown chart
  const ctx2 = document.getElementById('policy-chart').getContext('2d');
  new Chart(ctx2, {
    type: 'doughnut',
    data: {
      labels: policies.map(p => p.policy),
      datasets: [{
        data: policies.map(p => p.cost),
        backgroundColor: ['#34d399','#60a5fa','#fbbf24','#f87171','#a78bfa','#fb923c'],
        borderWidth: 0,
      }]
    },
    options: {
      responsive: true,
      plugins: { legend: { position: 'bottom', labels: { color: '#94a3b8', padding: 16,
               usePointStyle: true, pointStyleWidth: 10 } } }
    }
  });

  // Cascade stats
  document.getElementById('cascade-stats').innerHTML = `
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;font-size:14px;">
      <div>便宜模型尝试: <b>${cascade.total_requests}</b></div>
      <div>直接通过: <b>${cascade.direct_success}</b> (${cascade.direct_pct}%)</div>
      <div>级联升级: <b>${cascade.cascade_attempts}</b> (${cascade.cascade_pct}%)</div>
      <div>失败: <b>${cascade.failed}</b></div>
    </div>`;

  // Recent requests table
  const tbody = document.querySelector('#recent-table tbody');
  tbody.innerHTML = recent.slice(0, 15).map(r => `
    <tr>
      <td>${r.timestamp.slice(5,16)}</td>
      <td>${r.original_model} → <b>${r.routed_model}</b></td>
      <td style="color:#94a3b8">${r.policy_name||'-'}</td>
      <td>$${r.estimated_cost.toFixed(4)}</td>
      ${r.success ? '<td class="green">OK</td>' : '<td class="red">FAIL</td>'}
    </tr>`).join('');

  // Suggestions
  const tips = [];
  if (summary.saved_pct < 10) {
    tips.push('节省比例偏低(<10%)，建议检查策略是否过于保守（大量请求直接走默认模型）');
  }
  const cascadePct = parseFloat(cascade.cascade_pct);
  if (cascadePct > 15) {
    tips.push('级联升级率偏高(' + cascadePct + '%)，部分策略的便宜模型可能不够胜任，建议调整路由');
  }
  const highCostPolicy = policies.find(p => p.pct > 60);
  if (highCostPolicy) {
    tips.push('策略「' + highCostPolicy.policy + '」占了 ' + highCostPolicy.pct + '% 成本，建议检查是否有优化空间');
  }
  if (tips.length === 0) {
    tips.push('策略配置合理，成本控制良好。继续监控即可。');
  }
  document.getElementById('suggestions').innerHTML = tips.map(t => '<li>' + t + '</li>').join('');
}

load();
</script>
</body>
</html>"""
