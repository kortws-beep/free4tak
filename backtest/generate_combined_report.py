"""
generate_combined_report.py — nbot + sbot 통합 주간 HTML 리포트
================================================================
[사용법]
  python3 generate_combined_report.py \
      --nbot  results/result_20260522.json \
      --sbot  results/sbot_result_20260522.json \
      --date  2026-05-22

[출력]
  results/weekly_report_2026-05-22.html
"""
import os
import sys
import json
import argparse
import datetime


# ============================================================
# 헬퍼
# ============================================================
def load_results(path: str) -> list:
    if not path or path == "none" or not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"⚠️ 파일 로드 실패 {path}: {e}")
        return []


def pct(v, default="—"):
    if v is None: return default
    try: return f"{float(v):+.2f}%"
    except: return default


def num(v, default="—"):
    if v is None: return default
    try: return f"{float(v):.2f}"
    except: return default


def color_pct(v):
    """수익률/MDD 색상"""
    try:
        f = float(v)
        if f > 0:  return "var(--green)"
        if f < 0:  return "var(--red)"
        return "var(--text)"
    except:
        return "var(--text)"


def verdict_badge(pf, win_rate, mdd):
    """PF/승률/MDD 기준 판단 배지"""
    try:
        pf_f  = float(pf  or 0)
        wr_f  = float(win_rate or 0)
        md_f  = float(mdd or 0)
        if pf_f >= 1.3 and wr_f >= 50 and md_f >= -10:
            return '<span class="badge green">✅ 현행 유지</span>'
        if pf_f >= 1.0:
            return '<span class="badge yellow">⚠️ 관찰 유지</span>'
        return '<span class="badge red">🔴 재검토 필요</span>'
    except:
        return '<span class="badge gray">— 데이터 없음</span>'


def best_scenario(results: list) -> dict:
    """PF 최고 시나리오 반환"""
    if not results: return {}
    return max(results, key=lambda r: r.get("metrics", {}).get("profit_factor", 0))


def scenario_rows(results: list, highlight_name: str = "") -> str:
    """시나리오 비교 테이블 행 생성"""
    if not results:
        return '<tr><td colspan="8" class="empty">데이터 없음</td></tr>'
    rows = []
    for r in results:
        m    = r.get("metrics", {})
        name = r.get("name", "")
        is_best = (name == highlight_name)
        cls  = ' class="best-row"' if is_best else ""
        star = " ⭐" if is_best else ""
        ret  = m.get("total_return_pct", 0)
        mdd  = m.get("mdd", 0)
        rows.append(f"""
        <tr{cls}>
            <td>{name}{star}</td>
            <td style="color:{color_pct(ret)}">{pct(ret)}</td>
            <td>{pct(m.get('cagr_pct'))}</td>
            <td>{num(m.get('win_rate'))}%</td>
            <td style="color:{color_pct(mdd)}">{pct(mdd)}</td>
            <td>{num(m.get('sharpe'))}</td>
            <td style="color:{'var(--green)' if float(m.get('profit_factor',0) or 0)>=1.3 else 'var(--yellow)' if float(m.get('profit_factor',0) or 0)>=1.0 else 'var(--red)'}">{num(m.get('profit_factor'))}</td>
            <td>{m.get('total_trades','—')}</td>
        </tr>""")
    return "\n".join(rows)


def equity_data(results: list, scenario_name: str) -> str:
    """equity curve를 JS 배열로 변환"""
    for r in results:
        if r.get("name") == scenario_name:
            eq = r.get("equity", [])
            if eq:
                dates  = [f'"{d}"' for d, _ in eq]
                values = [str(int(v)) for _, v in eq]
                return f"[{','.join(dates)}]", f"[{','.join(values)}]"
    return "[]", "[]"


# ============================================================
# HTML 생성
# ============================================================
def generate_html(nbot_results: list, sbot_results: list, date: str) -> str:

    nbot_best = best_scenario(nbot_results)
    sbot_best = best_scenario(sbot_results)

    nb_m = nbot_best.get("metrics", {})
    sb_m = sbot_best.get("metrics", {})

    nb_dates, nb_values = equity_data(nbot_results, nbot_best.get("name",""))
    sb_dates, sb_values = equity_data(sbot_results, sbot_best.get("name",""))

    nbot_verdict = verdict_badge(nb_m.get("profit_factor"), nb_m.get("win_rate"), nb_m.get("mdd"))
    sbot_verdict = verdict_badge(sb_m.get("profit_factor"), sb_m.get("win_rate"), sb_m.get("mdd"))

    nbot_rows = scenario_rows(nbot_results, nbot_best.get("name",""))
    sbot_rows = scenario_rows(sbot_results, sbot_best.get("name",""))

    # 실전 설정 반영
    sbot_config_note = """
        <div class="config-note">
            💼 현재 실전 설정: <strong>자본 100만원 / 33만원 × 3종목 / 임계치 80</strong>
            <br>📌 백테스트 근거: PF 1.71 / 승률 56% / MDD -3.07% (임계치80)
        </div>"""

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>영암9 주간 리뷰 — {date}</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.0/chart.umd.min.js"></script>
<style>
  :root {{
    --bg:       #0d1117;
    --surface:  #161b22;
    --border:   #30363d;
    --text:     #e6edf3;
    --muted:    #8b949e;
    --green:    #3fb950;
    --red:      #f85149;
    --yellow:   #d29922;
    --blue:     #58a6ff;
    --purple:   #bc8cff;
    --orange:   #f0883e;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    background: var(--bg);
    color: var(--text);
    font-family: 'Pretendard', 'Noto Sans KR', sans-serif;
    font-size: 14px;
    line-height: 1.6;
  }}

  /* ── 헤더 ── */
  .header {{
    background: linear-gradient(135deg, #1a2332 0%, #0d1117 100%);
    border-bottom: 1px solid var(--border);
    padding: 24px 32px;
    display: flex;
    align-items: center;
    justify-content: space-between;
  }}
  .header h1 {{
    font-size: 22px;
    font-weight: 700;
    color: var(--blue);
    letter-spacing: -0.5px;
  }}
  .header .date {{
    color: var(--muted);
    font-size: 13px;
  }}

  /* ── 레이아웃 ── */
  .container {{ max-width: 1400px; margin: 0 auto; padding: 24px 32px; }}
  .grid-2 {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }}
  .grid-3 {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; }}
  .grid-4 {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; }}
  @media (max-width: 900px) {{
    .grid-2, .grid-3, .grid-4 {{ grid-template-columns: 1fr; }}
  }}

  /* ── 카드 ── */
  .card {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 20px;
  }}
  .card-title {{
    font-size: 13px;
    font-weight: 600;
    color: var(--muted);
    text-transform: uppercase;
    letter-spacing: 0.8px;
    margin-bottom: 16px;
    display: flex;
    align-items: center;
    gap: 8px;
  }}
  .card-title .dot {{
    width: 8px; height: 8px;
    border-radius: 50%;
    display: inline-block;
  }}
  .dot-blue   {{ background: var(--blue); }}
  .dot-purple {{ background: var(--purple); }}
  .dot-green  {{ background: var(--green); }}

  /* ── 섹션 타이틀 ── */
  .section-header {{
    display: flex;
    align-items: center;
    gap: 12px;
    margin: 32px 0 16px;
    padding-bottom: 12px;
    border-bottom: 1px solid var(--border);
  }}
  .section-header h2 {{
    font-size: 18px;
    font-weight: 700;
  }}
  .section-header .tag {{
    font-size: 11px;
    padding: 2px 8px;
    border-radius: 12px;
    font-weight: 600;
  }}
  .tag-nbot {{ background: rgba(88,166,255,0.15); color: var(--blue); }}
  .tag-sbot {{ background: rgba(188,140,255,0.15); color: var(--purple); }}

  /* ── 지표 카드 ── */
  .metric-val {{
    font-size: 28px;
    font-weight: 700;
    line-height: 1.2;
    margin-bottom: 4px;
  }}
  .metric-label {{
    font-size: 12px;
    color: var(--muted);
  }}

  /* ── 테이블 ── */
  .table-wrap {{ overflow-x: auto; }}
  table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 13px;
  }}
  th {{
    text-align: left;
    padding: 8px 12px;
    color: var(--muted);
    font-weight: 600;
    font-size: 11px;
    text-transform: uppercase;
    border-bottom: 1px solid var(--border);
  }}
  td {{
    padding: 10px 12px;
    border-bottom: 1px solid #21262d;
  }}
  tr:last-child td {{ border-bottom: none; }}
  tr:hover td {{ background: rgba(255,255,255,0.03); }}
  .best-row td {{
    background: rgba(88,166,255,0.06);
    font-weight: 600;
  }}
  .empty {{ color: var(--muted); text-align: center; padding: 24px; }}

  /* ── 배지 ── */
  .badge {{
    display: inline-block;
    padding: 3px 10px;
    border-radius: 12px;
    font-size: 12px;
    font-weight: 600;
  }}
  .badge.green  {{ background: rgba(63,185,80,0.15);  color: var(--green); }}
  .badge.yellow {{ background: rgba(210,153,34,0.15); color: var(--yellow); }}
  .badge.red    {{ background: rgba(248,81,73,0.15);  color: var(--red); }}
  .badge.gray   {{ background: rgba(139,148,158,0.15); color: var(--muted); }}

  /* ── 설정 노트 ── */
  .config-note {{
    background: rgba(188,140,255,0.08);
    border: 1px solid rgba(188,140,255,0.2);
    border-radius: 6px;
    padding: 12px 16px;
    font-size: 13px;
    line-height: 1.7;
    margin-top: 16px;
    color: #d2b4ff;
  }}

  /* ── 차트 ── */
  .chart-wrap {{ position: relative; height: 220px; }}

  /* ── 판단 박스 ── */
  .verdict-box {{
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 16px;
    border-radius: 6px;
    margin-top: 12px;
  }}
  .verdict-box.ok   {{ background: rgba(63,185,80,0.08);  border: 1px solid rgba(63,185,80,0.2); }}
  .verdict-box.warn {{ background: rgba(210,153,34,0.08); border: 1px solid rgba(210,153,34,0.2); }}
  .verdict-box.bad  {{ background: rgba(248,81,73,0.08);  border: 1px solid rgba(248,81,73,0.2); }}

  /* ── 구분선 ── */
  .divider {{ border: none; border-top: 1px solid var(--border); margin: 28px 0; }}

  /* ── 푸터 ── */
  .footer {{
    text-align: center;
    padding: 24px;
    color: var(--muted);
    font-size: 12px;
    border-top: 1px solid var(--border);
    margin-top: 40px;
  }}
</style>
</head>
<body>

<div class="header">
  <div>
    <h1>🏭 영암9 주간 백테스트 리뷰</h1>
    <div class="date">생성일: {date} &nbsp;|&nbsp; nbot + sbot 통합</div>
  </div>
  <div style="text-align:right">
    <div style="font-size:13px;color:var(--muted)">단타봇 <span style="color:var(--blue)">■</span>
    &nbsp; 스윙봇 <span style="color:var(--purple)">■</span></div>
  </div>
</div>

<div class="container">

  <!-- ── 요약 카드 4개 ── -->
  <div class="grid-4" style="margin-top:20px">
    <div class="card">
      <div class="card-title"><span class="dot dot-blue"></span>단타봇 수익률</div>
      <div class="metric-val" style="color:{color_pct(nb_m.get('total_return_pct'))}">{pct(nb_m.get('total_return_pct'))}</div>
      <div class="metric-label">CAGR {pct(nb_m.get('cagr_pct'))}</div>
    </div>
    <div class="card">
      <div class="card-title"><span class="dot dot-blue"></span>단타봇 PF / 승률</div>
      <div class="metric-val">{num(nb_m.get('profit_factor'))}</div>
      <div class="metric-label">승률 {num(nb_m.get('win_rate'))}% &nbsp;|&nbsp; MDD {pct(nb_m.get('mdd'))}</div>
    </div>
    <div class="card">
      <div class="card-title"><span class="dot dot-purple"></span>스윙봇 수익률</div>
      <div class="metric-val" style="color:{color_pct(sb_m.get('total_return_pct'))}">{pct(sb_m.get('total_return_pct'))}</div>
      <div class="metric-label">CAGR {pct(sb_m.get('cagr_pct'))}</div>
    </div>
    <div class="card">
      <div class="card-title"><span class="dot dot-purple"></span>스윙봇 PF / 승률</div>
      <div class="metric-val">{num(sb_m.get('profit_factor'))}</div>
      <div class="metric-label">승률 {num(sb_m.get('win_rate'))}% &nbsp;|&nbsp; MDD {pct(sb_m.get('mdd'))}</div>
    </div>
  </div>

  <!-- ── Equity Curve 차트 ── -->
  <div class="grid-2" style="margin-top:20px">
    <div class="card">
      <div class="card-title"><span class="dot dot-blue"></span>단타봇 자산 곡선</div>
      <div class="chart-wrap"><canvas id="nbotChart"></canvas></div>
    </div>
    <div class="card">
      <div class="card-title"><span class="dot dot-purple"></span>스윙봇 자산 곡선</div>
      <div class="chart-wrap"><canvas id="sbotChart"></canvas></div>
    </div>
  </div>

  <!-- ── nbot 시나리오 ── -->
  <div class="section-header">
    <h2>📈 단타봇 (nbot) 시나리오 비교</h2>
    <span class="tag tag-nbot">BUY_SCORE_ENTER 비교</span>
    {nbot_verdict}
  </div>
  <div class="card">
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>시나리오</th><th>수익률</th><th>CAGR</th>
            <th>승률</th><th>MDD</th><th>샤프</th><th>PF</th><th>거래수</th>
          </tr>
        </thead>
        <tbody>{nbot_rows}</tbody>
      </table>
    </div>
  </div>

  <!-- ── sbot 시나리오 ── -->
  <div class="section-header">
    <h2>📊 스윙봇 (sbot) 시나리오 비교</h2>
    <span class="tag tag-sbot">임계치 75~90 비교</span>
    {sbot_verdict}
  </div>
  <div class="card">
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>시나리오</th><th>수익률</th><th>CAGR</th>
            <th>승률</th><th>MDD</th><th>샤프</th><th>PF</th><th>거래수</th>
          </tr>
        </thead>
        <tbody>{sbot_rows}</tbody>
      </table>
    </div>
    {sbot_config_note}
  </div>

  <!-- ── 판단 ── -->
  <div class="section-header">
    <h2>🎯 이번 주 판단</h2>
  </div>
  <div class="grid-2">
    <div class="card">
      <div class="card-title"><span class="dot dot-blue"></span>단타봇 판단</div>
      <div style="margin-bottom:8px">최적 시나리오: <strong>{nbot_best.get('name','—')}</strong></div>
      <table style="width:100%">
        <tr><td style="color:var(--muted)">수익률</td><td style="color:{color_pct(nb_m.get('total_return_pct'))}">{pct(nb_m.get('total_return_pct'))}</td>
            <td style="color:var(--muted)">승률</td><td>{num(nb_m.get('win_rate'))}%</td></tr>
        <tr><td style="color:var(--muted)">PF</td><td>{num(nb_m.get('profit_factor'))}</td>
            <td style="color:var(--muted)">MDD</td><td style="color:{color_pct(nb_m.get('mdd'))}">{pct(nb_m.get('mdd'))}</td></tr>
        <tr><td style="color:var(--muted)">샤프</td><td>{num(nb_m.get('sharpe'))}</td>
            <td style="color:var(--muted)">거래수</td><td>{nb_m.get('total_trades','—')}</td></tr>
      </table>
      <div style="margin-top:12px">{nbot_verdict}</div>
    </div>
    <div class="card">
      <div class="card-title"><span class="dot dot-purple"></span>스윙봇 판단</div>
      <div style="margin-bottom:8px">최적 시나리오: <strong>{sbot_best.get('name','—')}</strong></div>
      <table style="width:100%">
        <tr><td style="color:var(--muted)">수익률</td><td style="color:{color_pct(sb_m.get('total_return_pct'))}">{pct(sb_m.get('total_return_pct'))}</td>
            <td style="color:var(--muted)">승률</td><td>{num(sb_m.get('win_rate'))}%</td></tr>
        <tr><td style="color:var(--muted)">PF</td><td>{num(sb_m.get('profit_factor'))}</td>
            <td style="color:var(--muted)">MDD</td><td style="color:{color_pct(sb_m.get('mdd'))}">{pct(sb_m.get('mdd'))}</td></tr>
        <tr><td style="color:var(--muted)">샤프</td><td>{num(sb_m.get('sharpe'))}</td>
            <td style="color:var(--muted)">거래수</td><td>{sb_m.get('total_trades','—')}</td></tr>
      </table>
      <div style="margin-top:12px">{sbot_verdict}</div>
    </div>
  </div>

</div><!-- /container -->

<div class="footer">
  영암9 백테스터 v1.2 &nbsp;|&nbsp; {date} &nbsp;|&nbsp;
  nbot: {len(nbot_results)}개 시나리오 &nbsp;|&nbsp; sbot: {len(sbot_results)}개 시나리오
</div>

<script>
// ── Chart.js 공통 설정 ──
Chart.defaults.color = '#8b949e';
Chart.defaults.borderColor = '#30363d';

function makeChart(id, dates, values, color, label) {{
  if (!dates || dates.length === 0) return;
  const ctx = document.getElementById(id).getContext('2d');
  const initial = values[0] || 10000000;
  const pcts = values.map(v => ((v - initial) / initial * 100).toFixed(2));

  new Chart(ctx, {{
    type: 'line',
    data: {{
      labels: dates,
      datasets: [{{
        label: label,
        data: pcts,
        borderColor: color,
        backgroundColor: color + '18',
        borderWidth: 2,
        pointRadius: 0,
        fill: true,
        tension: 0.3,
      }}]
    }},
    options: {{
      responsive: true,
      maintainAspectRatio: false,
      interaction: {{ mode: 'index', intersect: false }},
      plugins: {{
        legend: {{ display: false }},
        tooltip: {{
          callbacks: {{
            label: ctx => ` ${{ctx.parsed.y > 0 ? '+' : ''}}${{ctx.parsed.y}}%`
          }}
        }}
      }},
      scales: {{
        x: {{
          ticks: {{
            maxTicksLimit: 6,
            maxRotation: 0,
          }}
        }},
        y: {{
          ticks: {{
            callback: v => (v > 0 ? '+' : '') + v + '%'
          }}
        }}
      }}
    }}
  }});
}}

// nbot chart
const nbotDates  = {nb_dates};
const nbotValues = {nb_values};
makeChart('nbotChart', nbotDates, nbotValues, '#58a6ff', '단타봇');

// sbot chart
const sbotDates  = {sb_dates};
const sbotValues = {sb_values};
makeChart('sbotChart', sbotDates, sbotValues, '#bc8cff', '스윙봇');
</script>
</body>
</html>"""


# ============================================================
# 메인
# ============================================================
def main():
    parser = argparse.ArgumentParser(description="nbot+sbot 통합 HTML 리포트")
    parser.add_argument("--nbot",  default="none", help="nbot 결과 JSON 경로")
    parser.add_argument("--sbot",  default="none", help="sbot 결과 JSON 경로")
    parser.add_argument("--date",  default=datetime.date.today().strftime("%Y-%m-%d"))
    parser.add_argument("--out",   default="", help="출력 경로 (기본: results/weekly_report_날짜.html)")
    args = parser.parse_args()

    nbot_results = load_results(args.nbot)
    sbot_results = load_results(args.sbot)

    if not nbot_results and not sbot_results:
        print("❌ nbot/sbot 결과 파일 모두 없음")
        sys.exit(1)

    html = generate_html(nbot_results, sbot_results, args.date)

    out_dir = os.path.join(os.path.dirname(__file__), "results")
    os.makedirs(out_dir, exist_ok=True)
    out_path = args.out or os.path.join(out_dir, f"weekly_report_{args.date}.html")

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"✅ 통합 리포트 생성: {out_path}")
    return out_path


if __name__ == "__main__":
    main()
