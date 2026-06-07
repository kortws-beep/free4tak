"""
generate_combined_report.py — 영암9 nbot + sbot 통합 HTML 리포트 생성기
================================================================
[사용]
  python3 generate_combined_report.py \
      --nbot results/result_20260607_120000.json \
      --sbot results/sbot_result_20260607_120001.json \
      --date 2026-06-07

  # 한쪽만 있어도 동작
  python3 generate_combined_report.py --nbot results/result_xxx.json --sbot none
================================================================
"""
import os
import sys
import json
import argparse
import datetime


# ============================================================
# JSON 로드 헬퍼
# ============================================================
def load_results(path: str) -> list:
    """JSON 결과 파일 로드. 없거나 none이면 빈 리스트."""
    if not path or path.lower() == "none" or not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else [data]
    except Exception as e:
        print(f"⚠️ 로드 실패 {path}: {e}")
        return []


def safe(v, default=0):
    try:
        return float(v) if v is not None else default
    except Exception:
        return default


def fmt_pct(v):
    v = safe(v)
    sign = "+" if v >= 0 else ""
    return f"{sign}{v:.2f}%"


def fmt_won(v):
    v = safe(v)
    sign = "+" if v >= 0 else "-"
    return f"{sign}{abs(v):,.0f}원"


def color_cls(v):
    """양수=빨강(한국식), 음수=파랑"""
    v = safe(v)
    if v > 0:   return "pos"
    if v < 0:   return "neg"
    return "neu"


# ============================================================
# 시나리오별 요약 테이블 행 생성
# ============================================================
def scenario_rows(results: list, bot_label: str) -> str:
    if not results:
        return f'<tr><td colspan="8" class="neu" style="text-align:center">{bot_label} 결과 없음</td></tr>'

    rows = []
    for r in results:
        m    = r.get("metrics", {})
        name = r.get("name", "-")
        ret  = safe(m.get("total_return_pct", m.get("total_return", 0)))
        wr   = safe(m.get("win_rate", 0))
        mdd  = safe(m.get("max_drawdown_pct", m.get("mdd", 0)))
        shp  = safe(m.get("sharpe_ratio", m.get("sharpe", 0)))
        pf   = safe(m.get("profit_factor", 0))
        cnt  = int(safe(m.get("total_trades", m.get("trades", 0))))
        avg  = safe(m.get("avg_profit_pct", m.get("avg_profit", 0)))

        ret_c = color_cls(ret)
        wr_c  = "pos" if wr >= 55 else ("neg" if wr < 45 else "neu")
        mdd_c = "neg" if mdd < -15 else ("neu" if mdd < -8 else "pos")
        shp_c = "pos" if shp >= 1.5 else ("neg" if shp < 0.5 else "neu")

        rows.append(f"""
        <tr>
          <td class="name-cell">{name}</td>
          <td class="{ret_c}">{fmt_pct(ret)}</td>
          <td class="{wr_c}">{wr:.1f}%</td>
          <td class="{mdd_c}">{fmt_pct(mdd)}</td>
          <td class="{shp_c}">{shp:.2f}</td>
          <td class="neu">{pf:.2f}</td>
          <td class="neu">{cnt}건</td>
          <td class="neu">{fmt_pct(avg)}</td>
        </tr>""")
    return "\n".join(rows)


# ============================================================
# equity curve 데이터 → Chart.js용 JSON
# ============================================================
def equity_js_data(results: list, label: str, color: str) -> str:
    """가장 좋은 시나리오(수익률 1위)의 equity curve"""
    if not results:
        return ""
    best = max(results, key=lambda r: safe(r.get("metrics", {}).get(
        "total_return_pct", r.get("metrics", {}).get("total_return", 0))))
    eq = best.get("equity", [])
    if not eq:
        return ""

    labels = json.dumps([e[0] for e in eq], ensure_ascii=False)
    values = json.dumps([round(e[1]) for e in eq])
    return f"""
    {{
        label: '{label} ({best.get("name","")}) ',
        data: {{ labels: {labels}, datasets: [{{ label: '{label}', data: {values},
            borderColor: '{color}', backgroundColor: '{color}22',
            fill: true, tension: 0.3, pointRadius: 0, borderWidth: 2 }}] }},
    }},"""


# ============================================================
# 월별 수익 히트맵 데이터 (trades 기반)
# ============================================================
def monthly_pnl(results: list) -> dict:
    """best 시나리오 trades → {YYYY-MM: profit_rate 합산}"""
    if not results:
        return {}
    best = max(results, key=lambda r: safe(r.get("metrics", {}).get(
        "total_return_pct", r.get("metrics", {}).get("total_return", 0))))
    trades = best.get("trades", [])
    data = {}
    for t in trades:
        sell_time = t.get("sell_time", "") or t.get("sell_date", "")
        if not sell_time:
            continue
        ym = str(sell_time)[:7]  # YYYY-MM
        pr = safe(t.get("profit_rate", 0))
        data[ym] = data.get(ym, 0) + pr
    return data


def monthly_heatmap_js(nbot_results, sbot_results) -> str:
    nm = monthly_pnl(nbot_results)
    sm = monthly_pnl(sbot_results)
    all_ym = sorted(set(list(nm.keys()) + list(sm.keys())))
    if not all_ym:
        return "[]", "[]", "[]"

    labels = json.dumps(all_ym)
    ndata  = json.dumps([round(nm.get(ym, 0), 2) for ym in all_ym])
    sdata  = json.dumps([round(sm.get(ym, 0), 2) for ym in all_ym])
    return labels, ndata, sdata


# ============================================================
# 핵심 지표 요약 카드
# ============================================================
def summary_card(results: list, bot_label: str, emoji: str) -> str:
    if not results:
        return f"""
        <div class="card">
          <div class="card-title">{emoji} {bot_label}</div>
          <div class="no-data">결과 없음</div>
        </div>"""

    best = max(results, key=lambda r: safe(r.get("metrics", {}).get(
        "total_return_pct", r.get("metrics", {}).get("total_return", 0))))
    m    = best.get("metrics", {})
    name = best.get("name", "-")

    ret  = safe(m.get("total_return_pct", m.get("total_return", 0)))
    wr   = safe(m.get("win_rate", 0))
    mdd  = safe(m.get("max_drawdown_pct", m.get("mdd", 0)))
    shp  = safe(m.get("sharpe_ratio", m.get("sharpe", 0)))
    pf   = safe(m.get("profit_factor", 0))
    cnt  = int(safe(m.get("total_trades", m.get("trades", 0))))

    rc = color_cls(ret)
    wc = "pos" if wr >= 55 else ("neg" if wr < 45 else "neu")

    return f"""
        <div class="card">
          <div class="card-title">{emoji} {bot_label}</div>
          <div class="best-label">🏆 {name}</div>
          <div class="metrics-grid">
            <div class="metric"><span class="metric-label">수익률</span>
              <span class="metric-value {rc}">{fmt_pct(ret)}</span></div>
            <div class="metric"><span class="metric-label">승률</span>
              <span class="metric-value {wc}">{wr:.1f}%</span></div>
            <div class="metric"><span class="metric-label">MDD</span>
              <span class="metric-value neg">{fmt_pct(mdd)}</span></div>
            <div class="metric"><span class="metric-label">샤프</span>
              <span class="metric-value">{shp:.2f}</span></div>
            <div class="metric"><span class="metric-label">PF</span>
              <span class="metric-value">{pf:.2f}</span></div>
            <div class="metric"><span class="metric-label">거래수</span>
              <span class="metric-value">{cnt}건</span></div>
          </div>
        </div>"""


# ============================================================
# HTML 생성
# ============================================================
def build_html(nbot_results, sbot_results, date_str: str) -> str:

    # 시나리오 테이블 행
    nbot_rows = scenario_rows(nbot_results, "📈 단타봇(nbot)")
    sbot_rows = scenario_rows(sbot_results, "📊 스윙봇(sbot)")

    # 요약 카드
    ncard = summary_card(nbot_results, "단타봇 (nbot)", "📈")
    scard = summary_card(sbot_results, "스윙봇 (sbot)", "📊")

    # Equity curve JS 데이터
    neq = equity_js_data(nbot_results, "단타봇", "#ef4444")
    seq = equity_js_data(sbot_results, "스윙봇", "#3b82f6")

    # 월별 히트맵
    hm_labels, hm_ndata, hm_sdata = monthly_heatmap_js(nbot_results, sbot_results)

    # 시나리오 수
    n_cnt = len(nbot_results)
    s_cnt = len(sbot_results)

    # nbot/sbot 기간
    def period(results):
        if not results:
            return "-"
        cfg = results[0].get("config", {})
        # config에 start_date/end_date 없으면 trades에서 추출
        trades = results[0].get("trades", [])
        if trades:
            dates = [t.get("buy_time","") or t.get("buy_date","") for t in trades if t.get("buy_time") or t.get("buy_date")]
            if dates:
                return f"{min(dates)[:10]} ~ {max(dates)[:10]}"
        return "-"

    n_period = period(nbot_results)
    s_period = period(sbot_results)

    html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>영암9 주간 리뷰 — {date_str}</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
  :root {{
    --bg: #0f1117; --card: #1a1d27; --card2: #20243a;
    --border: #2a2d3a; --text: #e2e8f0; --sub: #94a3b8;
    --pos: #ef4444; --neg: #4dabf7; --neu: #94a3b8;
    --green: #22c55e; --yellow: #f59e0b;
    --nbot: #ef4444; --sbot: #3b82f6;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    background: var(--bg); color: var(--text);
    font-family: -apple-system, 'Malgun Gothic', sans-serif;
    font-size: 14px; padding: 16px;
  }}
  h1 {{ font-size: 22px; font-weight: 700; margin-bottom: 4px; }}
  h2 {{ font-size: 16px; font-weight: 600; margin: 20px 0 10px;
        padding-left: 10px; border-left: 3px solid var(--yellow); }}
  .subtitle {{ color: var(--sub); font-size: 12px; margin-bottom: 20px; }}

  /* 카드 그리드 */
  .card-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-bottom: 20px; }}
  @media (max-width: 600px) {{ .card-grid {{ grid-template-columns: 1fr; }} }}
  .card {{
    background: var(--card); border: 1px solid var(--border);
    border-radius: 10px; padding: 16px;
  }}
  .card-title {{ font-size: 15px; font-weight: 700; margin-bottom: 8px; }}
  .best-label {{ font-size: 11px; color: var(--yellow); margin-bottom: 10px; }}
  .no-data {{ color: var(--sub); font-size: 13px; padding: 20px 0; text-align: center; }}

  .metrics-grid {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; }}
  .metric {{ background: var(--card2); border-radius: 6px; padding: 8px; text-align: center; }}
  .metric-label {{ font-size: 10px; color: var(--sub); display: block; margin-bottom: 4px; }}
  .metric-value {{ font-size: 15px; font-weight: 700; }}

  /* 색상 */
  .pos {{ color: var(--pos); }}
  .neg {{ color: var(--neg); }}
  .neu {{ color: var(--neu); }}

  /* 차트 영역 */
  .chart-wrap {{
    background: var(--card); border: 1px solid var(--border);
    border-radius: 10px; padding: 16px; margin-bottom: 20px;
  }}
  .chart-title {{ font-size: 13px; color: var(--sub); margin-bottom: 12px; }}
  .chart-container {{ position: relative; height: 260px; }}

  /* 시나리오 테이블 */
  .table-wrap {{
    background: var(--card); border: 1px solid var(--border);
    border-radius: 10px; padding: 16px; margin-bottom: 20px;
    overflow-x: auto;
  }}
  .tab-header {{
    display: flex; gap: 8px; margin-bottom: 12px;
  }}
  .tab-btn {{
    padding: 5px 14px; border-radius: 6px; border: 1px solid var(--border);
    background: var(--card2); color: var(--sub); cursor: pointer; font-size: 12px;
  }}
  .tab-btn.active {{ background: var(--yellow); color: #000; font-weight: 700; border-color: var(--yellow); }}

  table {{ width: 100%; border-collapse: collapse; font-size: 12px; }}
  thead th {{
    background: var(--card2); color: var(--sub);
    padding: 8px 10px; text-align: right; white-space: nowrap;
  }}
  thead th:first-child {{ text-align: left; }}
  tbody tr {{ border-bottom: 1px solid var(--border); }}
  tbody tr:hover {{ background: var(--card2); }}
  tbody td {{ padding: 8px 10px; text-align: right; white-space: nowrap; }}
  .name-cell {{ text-align: left; color: var(--text); max-width: 200px;
                overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}

  /* 탭 패널 */
  .tab-panel {{ display: none; }}
  .tab-panel.active {{ display: block; }}

  /* 배지 */
  .badge {{
    display: inline-block; padding: 2px 8px; border-radius: 999px;
    font-size: 11px; font-weight: 600;
  }}
  .badge-nbot {{ background: #ef444422; color: var(--nbot); border: 1px solid #ef444444; }}
  .badge-sbot {{ background: #3b82f622; color: var(--sbot); border: 1px solid #3b82f644; }}

  footer {{ text-align: center; color: var(--sub); font-size: 11px; margin-top: 24px; padding-top: 16px;
            border-top: 1px solid var(--border); }}
</style>
</head>
<body>

<h1>📊 영암9 주간 백테스트 리뷰</h1>
<div class="subtitle">
  생성일: {date_str} &nbsp;|&nbsp;
  <span class="badge badge-nbot">단타봇 {n_cnt}개 시나리오</span> &nbsp;
  <span class="badge badge-sbot">스윙봇 {s_cnt}개 시나리오</span>
</div>

<!-- ① 요약 카드 -->
<h2>🏆 최고 시나리오 요약</h2>
<div class="card-grid">
  {ncard}
  {scard}
</div>

<!-- ② Equity Curve (통합) -->
<h2>📈 자산 곡선 비교 (최고 시나리오)</h2>
<div class="chart-wrap">
  <div class="chart-title">단타봇(빨강) vs 스윙봇(파랑) — 초기자본 대비 자산가치</div>
  <div class="chart-container">
    <canvas id="eqChart"></canvas>
  </div>
</div>

<!-- ③ 월별 수익 히트맵 -->
<h2>📅 월별 누적 수익률</h2>
<div class="chart-wrap">
  <div class="chart-title">월별 손익 합산 (막대: 단타봇 / 선: 스윙봇)</div>
  <div class="chart-container">
    <canvas id="monthChart"></canvas>
  </div>
</div>

<!-- ④ 시나리오 비교 테이블 -->
<h2>📋 전체 시나리오 비교</h2>
<div class="table-wrap">
  <div class="tab-header">
    <button class="tab-btn active" onclick="switchTab('nbot', this)">📈 단타봇 ({n_cnt})</button>
    <button class="tab-btn"        onclick="switchTab('sbot', this)">📊 스윙봇 ({s_cnt})</button>
  </div>

  <!-- nbot 테이블 -->
  <div id="tab-nbot" class="tab-panel active">
    <table>
      <thead><tr>
        <th>시나리오</th><th>수익률</th><th>승률</th>
        <th>MDD</th><th>샤프</th><th>PF</th><th>거래수</th><th>평균수익</th>
      </tr></thead>
      <tbody>{nbot_rows}</tbody>
    </table>
  </div>

  <!-- sbot 테이블 -->
  <div id="tab-sbot" class="tab-panel">
    <table>
      <thead><tr>
        <th>시나리오</th><th>수익률</th><th>승률</th>
        <th>MDD</th><th>샤프</th><th>PF</th><th>거래수</th><th>평균수익</th>
      </tr></thead>
      <tbody>{sbot_rows}</tbody>
    </table>
  </div>
</div>

<!-- ⑤ 기간 정보 -->
<div class="card-grid">
  <div class="card">
    <div class="card-title">📈 단타봇 백테스트 기간</div>
    <div style="color:var(--sub); font-size:13px; margin-top:8px">{n_period}</div>
  </div>
  <div class="card">
    <div class="card-title">📊 스윙봇 백테스트 기간</div>
    <div style="color:var(--sub); font-size:13px; margin-top:8px">{s_period}</div>
  </div>
</div>

<footer>영암9 자동매매 시스템 — 백테스트 결과는 미래 수익을 보장하지 않습니다</footer>

<script>
// ── 탭 전환 ──────────────────────────────────────────
function switchTab(name, btn) {{
  document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  document.getElementById('tab-' + name).classList.add('active');
  btn.classList.add('active');
}}

// ── Equity Curve Chart ────────────────────────────────
(function() {{
  const datasets = [];

  const eqData = [{neq}{seq}];
  eqData.forEach(d => {{
    if (!d || !d.data) return;
    datasets.push({{
      label: d.label,
      data: d.data.datasets[0].data,
      borderColor: d.data.datasets[0].borderColor,
      backgroundColor: d.data.datasets[0].backgroundColor,
      fill: true, tension: 0.3, pointRadius: 0, borderWidth: 2,
    }});
  }});

  // labels: 가장 긴 것 사용
  let labels = [];
  eqData.forEach(d => {{
    if (d && d.data && d.data.labels && d.data.labels.length > labels.length)
      labels = d.data.labels;
  }});

  if (datasets.length === 0 || labels.length === 0) {{
    document.getElementById('eqChart').parentElement.innerHTML =
      '<div style="color:#94a3b8;text-align:center;padding:40px">데이터 없음</div>';
    return;
  }}

  new Chart(document.getElementById('eqChart'), {{
    type: 'line',
    data: {{ labels, datasets }},
    options: {{
      responsive: true, maintainAspectRatio: false,
      interaction: {{ mode: 'index', intersect: false }},
      plugins: {{
        legend: {{ labels: {{ color: '#e2e8f0', font: {{ size: 12 }} }} }},
        tooltip: {{
          callbacks: {{
            label: ctx => ` ${{ctx.dataset.label}}: ${{ctx.parsed.y.toLocaleString()}}원`
          }}
        }}
      }},
      scales: {{
        x: {{ ticks: {{ color: '#94a3b8', maxTicksLimit: 10 }}, grid: {{ color: '#2a2d3a' }} }},
        y: {{ ticks: {{ color: '#94a3b8',
                        callback: v => v.toLocaleString() + '원' }},
               grid: {{ color: '#2a2d3a' }} }}
      }}
    }}
  }});
}})();

// ── Monthly Chart ─────────────────────────────────────
(function() {{
  const labels = {hm_labels};
  const ndata  = {hm_ndata};
  const sdata  = {hm_sdata};

  if (!labels || labels.length === 0) {{
    document.getElementById('monthChart').parentElement.innerHTML =
      '<div style="color:#94a3b8;text-align:center;padding:40px">데이터 없음</div>';
    return;
  }}

  const bgColors = ndata.map(v => v >= 0 ? '#ef444466' : '#4dabf766');
  const bdColors = ndata.map(v => v >= 0 ? '#ef4444' : '#4dabf7');

  new Chart(document.getElementById('monthChart'), {{
    type: 'bar',
    data: {{
      labels,
      datasets: [
        {{
          label: '단타봇 월수익률(%)',
          data: ndata,
          backgroundColor: bgColors,
          borderColor: bdColors,
          borderWidth: 1,
          yAxisID: 'y',
        }},
        {{
          label: '스윙봇 월수익률(%)',
          data: sdata,
          type: 'line',
          borderColor: '#3b82f6',
          backgroundColor: '#3b82f622',
          fill: false, tension: 0.3, pointRadius: 3,
          yAxisID: 'y',
        }},
      ]
    }},
    options: {{
      responsive: true, maintainAspectRatio: false,
      interaction: {{ mode: 'index', intersect: false }},
      plugins: {{
        legend: {{ labels: {{ color: '#e2e8f0', font: {{ size: 12 }} }} }},
        tooltip: {{
          callbacks: {{
            label: ctx => ` ${{ctx.dataset.label}}: ${{ctx.parsed.y.toFixed(2)}}%`
          }}
        }}
      }},
      scales: {{
        x: {{ ticks: {{ color: '#94a3b8' }}, grid: {{ color: '#2a2d3a' }} }},
        y: {{
          ticks: {{ color: '#94a3b8', callback: v => v.toFixed(1) + '%' }},
          grid: {{ color: '#2a2d3a' }},
          position: 'left',
        }},
      }}
    }}
  }});
}})();
</script>
</body>
</html>"""
    return html


# ============================================================
# 메인
# ============================================================
def main():
    parser = argparse.ArgumentParser(description="nbot+sbot 통합 HTML 리포트 생성")
    parser.add_argument("--nbot",  default="none", help="nbot JSON 결과 경로")
    parser.add_argument("--sbot",  default="none", help="sbot JSON 결과 경로")
    parser.add_argument("--date",  default=datetime.date.today().strftime("%Y-%m-%d"))
    parser.add_argument("--out",   default="", help="출력 경로 (기본: results/weekly_report_DATE.html)")
    args = parser.parse_args()

    nbot_results = load_results(args.nbot)
    sbot_results = load_results(args.sbot)

    if not nbot_results and not sbot_results:
        print("❌ nbot/sbot 결과 모두 없음 — JSON 경로 확인")
        sys.exit(1)

    print(f"✅ nbot 시나리오: {len(nbot_results)}개")
    print(f"✅ sbot 시나리오: {len(sbot_results)}개")

    html = build_html(nbot_results, sbot_results, args.date)

    # 출력 경로
    if args.out:
        out_path = args.out
    else:
        results_dir = os.path.join(os.path.dirname(__file__), "results")
        os.makedirs(results_dir, exist_ok=True)
        out_path = os.path.join(results_dir, f"weekly_report_{args.date}.html")

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"📊 통합 리포트 저장: {out_path}")
    return out_path


if __name__ == "__main__":
    main()
