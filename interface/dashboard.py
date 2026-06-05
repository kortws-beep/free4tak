"""
dashboard.py — 영암9 통합 대시보드 (Flask + 모바일)
================================================================
[접근 방법]
  http://서버IP:5000        ← PC/모바일 브라우저
  http://localhost:5000     ← 로컬

[실행]
  cd /home/free4tak/k-bot/stock_bot
  source venv/bin/activate
  python3 interface/dashboard.py

[기능]
  - 실시간 세 봇 합산 손익 (5초 갱신)
  - 포지션 통합 현황
  - 리스크 한도 게이지
  - sector_monitor 테마 TOP5
  - 긴급 전봇 중단/재개 버튼
  - 일일 손실 한도 설정
================================================================
"""
import os
import sys
import json
import datetime
import sqlite3

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in ["core", "intelligence", "interface", "bots", ""]:
    _p = os.path.join(_BASE, _d)
    if _p not in sys.path:
        sys.path.insert(0, _p)

from flask import Flask, jsonify, request, render_template_string
from dotenv import load_dotenv
load_dotenv(os.path.join(_BASE, ".env"))

from master_db import get_all_positions, get_today_summary, get_performance
from unified_risk import get_status, pause_all, resume_all, set_daily_loss_limit

app = Flask(__name__)

SECTOR_DB = os.path.join(_BASE, "sector_monitor.db")

# ============================================================
# HTML 템플릿 (모바일 반응형)
# ============================================================
HTML = """<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0">
<title>영암9 대시보드</title>
<style>
  :root {
    --bg: #0f1117; --card: #1a1d27; --border: #2a2d3a;
    --text: #e2e8f0; --sub: #94a3b8; --green: #ef4444;
    --red: #4dabf7; --yellow: #f59e0b; --blue: #3b82f6;
    --purple: #8b5cf6;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--bg); color: var(--text); font-family: -apple-system, sans-serif;
         font-size: 14px; padding: 12px; }
  h1 { font-size: 18px; font-weight: 700; margin-bottom: 12px;
       display: flex; align-items: center; gap: 8px; }
  .grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 10px; margin-bottom: 12px; }
  .grid-3 { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; margin-bottom: 12px; }
  .card { background: var(--card); border: 1px solid var(--border);
          border-radius: 12px; padding: 14px; }
  .card-title { font-size: 11px; color: var(--sub); margin-bottom: 6px; text-transform: uppercase; letter-spacing: 0.5px; }
  .card-value { font-size: 22px; font-weight: 700; }
  .card-sub { font-size: 11px; color: var(--sub); margin-top: 4px; }
  .green { color: var(--green); }
  .red { color: var(--red); }
  .yellow { color: var(--yellow); }
  .blue { color: var(--blue); }
  .section { margin-bottom: 14px; }
  .section-title { font-size: 13px; font-weight: 600; color: var(--sub);
                   margin-bottom: 8px; padding-bottom: 6px;
                   border-bottom: 1px solid var(--border); }
  /* 리스크 게이지 */
  .gauge-wrap { background: var(--border); border-radius: 99px; height: 10px;
                overflow: hidden; margin: 8px 0; }
  .gauge-bar { height: 100%; border-radius: 99px; transition: width 0.5s; }
  /* 포지션 테이블 */
  .pos-table { width: 100%; border-collapse: collapse; }
  .pos-table th { font-size: 10px; color: var(--sub); text-align: left;
                  padding: 6px 4px; border-bottom: 1px solid var(--border); }
  .pos-table td { padding: 8px 4px; border-bottom: 1px solid var(--border);
                  font-size: 13px; }
  .badge { display: inline-block; padding: 2px 7px; border-radius: 99px;
           font-size: 10px; font-weight: 600; }
  .badge-n { background: #1d4ed820; color: var(--blue); }
  .badge-s { background: #7c3aed20; color: var(--purple); }
  .badge-c { background: #d9770620; color: var(--yellow); }
  /* 버튼 */
  .btn { width: 100%; padding: 12px; border: none; border-radius: 10px;
         font-size: 14px; font-weight: 600; cursor: pointer; margin-bottom: 8px; }
  .btn-red { background: #ef444420; color: var(--red); border: 1px solid #ef444440; }
  .btn-green { background: #10b98120; color: var(--green); border: 1px solid #10b98140; }
  .btn-blue { background: #3b82f620; color: var(--blue); border: 1px solid #3b82f640; }
  /* 테마 */
  .theme-row { display: flex; justify-content: space-between; align-items: center;
               padding: 7px 0; border-bottom: 1px solid var(--border); }
  .theme-name { font-size: 13px; }
  .theme-rate { font-size: 13px; font-weight: 600; }
  /* 상태 점 */
  .dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; margin-right: 5px; }
  .dot-green { background: var(--green); box-shadow: 0 0 6px var(--green); }
  .dot-yellow { background: var(--yellow); box-shadow: 0 0 6px var(--yellow); }
  .dot-red { background: var(--red); box-shadow: 0 0 6px var(--red); animation: blink 1s infinite; }
  @keyframes blink { 50% { opacity: 0.3; } }
  .limit-input { background: var(--border); border: 1px solid var(--border);
                 color: var(--text); border-radius: 8px; padding: 8px 12px;
                 width: 100%; font-size: 14px; margin-bottom: 8px; }
  .update-time { font-size: 10px; color: var(--sub); text-align: right; margin-top: 8px; }
  @media (min-width: 600px) {
    body { max-width: 700px; margin: 0 auto; padding: 20px; }
    .grid-3 { grid-template-columns: repeat(3, 1fr); }
  }
</style>
</head>
<body>
<h1>
  <span id="risk-dot" class="dot dot-green"></span>
  영암9 대시보드
</h1>

<!-- 합산 손익 -->
<div class="grid-3 section" id="summary-cards">
  <div class="card">
    <div class="card-title">nbot 손익</div>
    <div class="card-value" id="nbot-pnl">—</div>
    <div class="card-sub" id="nbot-trade">—</div>
  </div>
  <div class="card">
    <div class="card-title">sbot 손익</div>
    <div class="card-value" id="sbot-pnl">—</div>
    <div class="card-sub" id="sbot-trade">—</div>
  </div>
  <div class="card">
    <div class="card-title">cbot 손익</div>
    <div class="card-value" id="cbot-pnl">—</div>
    <div class="card-sub" id="cbot-trade">—</div>
  </div>
</div>

<!-- 합산 총손익 -->
<div class="card section" style="text-align:center;">
  <div class="card-title">당일 합산 손익</div>
  <div class="card-value" id="total-pnl" style="font-size:28px;">—</div>
  <div class="card-sub" id="total-sub">—</div>
</div>

<!-- 리스크 게이지 -->
<div class="card section">
  <div class="card-title">리스크 한도 <span id="risk-level-badge">—</span></div>
  <div style="display:flex; justify-content:space-between; margin-bottom:4px;">
    <span id="risk-loss">손실 —</span>
    <span id="risk-limit">한도 —</span>
  </div>
  <div class="gauge-wrap">
    <div class="gauge-bar" id="gauge-bar" style="width:0%; background:var(--green);"></div>
  </div>
  <div style="display:flex; justify-content:space-between; font-size:11px; color:var(--sub);">
    <span>0</span>
    <span id="gauge-warn">경고 —</span>
    <span id="gauge-limit-end">한도 —</span>
  </div>
</div>

<!-- 보유 포지션 -->
<div class="section">
  <div class="section-title">보유 포지션 <span id="pos-count" style="color:var(--blue);">0</span>개</div>
  <div class="card">
    <table class="pos-table">
      <thead>
        <tr>
          <th>봇</th><th>종목</th><th>수익률</th><th>손익</th><th>수량</th>
        </tr>
      </thead>
      <tbody id="pos-tbody">
        <tr><td colspan="5" style="text-align:center;color:var(--sub);padding:16px;">보유 없음</td></tr>
      </tbody>
    </table>
  </div>
</div>

<!-- 테마 TOP5 -->
<div class="section">
  <div class="section-title">🔥 테마 TOP5</div>
  <div class="card" id="theme-list">
    <div style="text-align:center;color:var(--sub);padding:16px;">로딩 중...</div>
  </div>
</div>

<!-- 긴급 제어 -->
<div class="section">
  <div class="section-title">긴급 제어</div>
  <button class="btn btn-red" onclick="pauseAll()">🚨 전봇 긴급중단</button>
  <button class="btn btn-green" onclick="resumeAll()">✅ 전봇 재개</button>
</div>

<!-- 한도 설정 -->
<div class="section">
  <div class="section-title">손실 한도 설정</div>
  <div class="card">
    <input class="limit-input" type="number" id="limit-input" placeholder="266000">
    <button class="btn btn-blue" onclick="setLimit()">한도 변경</button>
  </div>
</div>

<div class="update-time" id="update-time">—</div>

<script>
function fmtWon(v) {
  if (v === null || v === undefined) return '—';
  const abs = Math.abs(v);
  const s = abs >= 10000 ? (abs/10000).toFixed(1)+'만원' : abs.toLocaleString()+'원';
  return (v >= 0 ? '+' : '-') + s;
}
function fmtPct(v) {
  if (!v && v !== 0) return '';
  return (v >= 0 ? '+' : '') + v.toFixed(1) + '%';
}

async function fetchData() {
  try {
    const [summary, positions, risk, themes] = await Promise.all([
      fetch('/api/summary').then(r=>r.json()),
      fetch('/api/positions').then(r=>r.json()),
      fetch('/api/risk').then(r=>r.json()),
      fetch('/api/themes').then(r=>r.json()),
    ]);
    updateSummary(summary);
    updatePositions(positions);
    updateRisk(risk);
    updateThemes(themes);
    document.getElementById('update-time').textContent =
      '업데이트: ' + new Date().toLocaleTimeString('ko-KR');
  } catch(e) { console.error(e); }
}

function updateSummary(data) {
  let total = 0;
  let totalTrades = 0, totalWins = 0;
  ['nbot','sbot','cbot'].forEach(bot => {
    const d = data[bot] || {};
    const pnl = d.pnl || 0;
    total += pnl;
    totalTrades += d.count || 0;
    totalWins += Math.round((d.win_rate||0)/100*(d.count||0));
    const el = document.getElementById(bot+'-pnl');
    el.textContent = fmtWon(pnl);
    el.className = 'card-value ' + (pnl >= 0 ? 'green' : 'red');
    document.getElementById(bot+'-trade').textContent =
      (d.count||0) + '건 ' + (d.win_rate||0).toFixed(0) + '%';
  });
  const tel = document.getElementById('total-pnl');
  tel.textContent = fmtWon(total);
  tel.className = 'card-value ' + (total >= 0 ? 'green' : 'red');
  const wr = totalTrades > 0 ? (totalWins/totalTrades*100).toFixed(0) : 0;
  document.getElementById('total-sub').textContent =
    '총 ' + totalTrades + '건 | 승률 ' + wr + '%';
}

function updatePositions(data) {
  const tbody = document.getElementById('pos-tbody');
  document.getElementById('pos-count').textContent = data.length;
  if (!data.length) {
    tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;color:var(--sub);padding:16px;">보유 없음</td></tr>';
    return;
  }
  tbody.innerHTML = data.map(p => {
    const badge = {nbot:'badge-n',sbot:'badge-s',cbot:'badge-c'}[p.bot_type]||'badge-n';
    const pr = p.profit_rate || 0;
    const cls = pr >= 0 ? 'green' : 'red';
    return `<tr>
      <td><span class="badge ${badge}">${p.bot_type}</span></td>
      <td>${p.stock_name || p.code}<br><span style="font-size:11px;color:var(--sub)">${p.code}</span></td>
      <td class="${cls}">${fmtPct(pr)}</td>
      <td class="${cls}">${fmtWon(p.profit_krw)}</td>
      <td>${p.qty}</td>
    </tr>`;
  }).join('');
}

function updateRisk(data) {
  const level = data.risk_level || 'normal';
  const loss  = data.total_loss_krw || 0;
  const limit = data.daily_loss_limit || 266000;
  const warn  = data.warn_line || limit * 0.7;
  const ratio = Math.min(loss / limit * 100, 100);

  // 게이지 색상
  let color = 'var(--green)';
  if (ratio >= 100) color = 'var(--red)';
  else if (ratio >= 70) color = 'var(--yellow)';
  document.getElementById('gauge-bar').style.width = ratio + '%';
  document.getElementById('gauge-bar').style.background = color;

  // 리스크 점
  const dot = document.getElementById('risk-dot');
  dot.className = 'dot ' + (level==='danger'?'dot-red':level==='warning'?'dot-yellow':'dot-green');

  document.getElementById('risk-loss').textContent = '손실 ' + loss.toLocaleString() + '원';
  document.getElementById('risk-limit').textContent = '한도 ' + limit.toLocaleString() + '원';
  document.getElementById('gauge-warn').textContent = '경고 ' + warn.toLocaleString();
  document.getElementById('gauge-limit-end').textContent = '한도 ' + limit.toLocaleString();

  const badges = {normal:'🟢 정상', warning:'🟡 경고', danger:'🔴 위험'};
  document.getElementById('risk-level-badge').textContent = badges[level] || level;

  if (data.paused_all) {
    document.getElementById('risk-dot').className = 'dot dot-red';
  }
}

function updateThemes(data) {
  const el = document.getElementById('theme-list');
  if (!data.length) {
    el.innerHTML = '<div style="text-align:center;color:var(--sub);padding:16px;">데이터 없음</div>';
    return;
  }
  el.innerHTML = data.map(t => {
    const r = parseFloat(t.flu_rt || 0);
    const cls = r >= 0 ? 'green' : 'red';
    return `<div class="theme-row">
      <span class="theme-name">${t.theme_nm}</span>
      <span class="theme-rate ${cls}">${r>=0?'+':''}${r.toFixed(2)}%</span>
    </div>`;
  }).join('');
}

async function pauseAll() {
  if (!confirm('전봇을 긴급중단 하시겠습니까?')) return;
  const r = await fetch('/api/pause', {method:'POST'}).then(r=>r.json());
  alert(r.msg || '완료');
  fetchData();
}

async function resumeAll() {
  if (!confirm('전봇을 재개 하시겠습니까?')) return;
  const r = await fetch('/api/resume', {method:'POST'}).then(r=>r.json());
  alert(r.msg || '완료');
  fetchData();
}

async function setLimit() {
  const v = parseInt(document.getElementById('limit-input').value);
  if (!v || v < 10000) { alert('올바른 금액을 입력하세요 (최소 10,000원)'); return; }
  const r = await fetch('/api/set_limit', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({limit: v})
  }).then(r=>r.json());
  alert(r.msg || '완료');
  fetchData();
}

// 초기 로드 + 5초 자동 갱신
fetchData();
setInterval(fetchData, 5000);
</script>
</body>
</html>"""


# ============================================================
# API 엔드포인트
# ============================================================
@app.route("/")
def index():
    return render_template_string(HTML)


@app.route("/api/summary")
def api_summary():
    """당일 봇별 손익 요약"""
    return jsonify(get_today_summary())


@app.route("/api/positions")
def api_positions():
    """보유중 포지션 전체"""
    return jsonify(get_all_positions())


@app.route("/api/risk")
def api_risk():
    """리스크 상태"""
    return jsonify(get_status())


@app.route("/api/themes")
def api_themes():
    """sector_monitor 테마 TOP5"""
    try:
        if not os.path.exists(SECTOR_DB):
            return jsonify([])
        conn = sqlite3.connect(SECTOR_DB, timeout=3)
        conn.execute("PRAGMA query_only=ON")
        rows = conn.execute("""
            SELECT theme_nm, flu_rt, trde_amt
            FROM sector_flow
            WHERE ts >= datetime('now', '-10 minutes', 'localtime')
            GROUP BY theme_nm
            ORDER BY CAST(flu_rt AS REAL) DESC
            LIMIT 5
        """).fetchall()
        conn.close()
        return jsonify([
            {"theme_nm": r[0], "flu_rt": r[1], "trde_amt": r[2]}
            for r in rows
        ])
    except Exception as e:
        return jsonify([])


@app.route("/api/pause", methods=["POST"])
def api_pause():
    ok = pause_all("대시보드")
    return jsonify({"ok": ok, "msg": "🚨 전봇 긴급중단 완료"})


@app.route("/api/resume", methods=["POST"])
def api_resume():
    ok = resume_all()
    return jsonify({"ok": ok, "msg": "✅ 전봇 재개 완료"})


@app.route("/api/set_limit", methods=["POST"])
def api_set_limit():
    data  = request.get_json()
    limit = int(data.get("limit", 266000))
    ok    = set_daily_loss_limit(limit)
    return jsonify({"ok": ok, "msg": f"✅ 한도 {limit:,}원 설정 완료"})


@app.route("/api/performance")
def api_performance():
    """30일 성과"""
    return jsonify(get_performance(days=30))


# ============================================================
# 실행
# ============================================================
if __name__ == "__main__":
    print("🌐 영암9 대시보드 시작")
    print(f"   접속: http://0.0.0.0:5000")
    print(f"   모바일: http://서버IP:5000")
    app.run(host="0.0.0.0", port=5000, debug=False)
