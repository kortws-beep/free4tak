"""
generate_report.py — 백테스트 결과 JSON → 시각적 HTML 리포트
사용: python3 generate_report.py results/result_YYYYMMDD_HHMMSS.json 2026-05-10
"""
import sys
import json
import os
from datetime import datetime


def make_color(val, good_positive=True):
    """값에 따라 색상 클래스 반환"""
    if val is None:
        return "neutral"
    if good_positive:
        return "positive" if val > 0 else ("negative" if val < 0 else "neutral")
    else:
        return "negative" if val > 0 else ("positive" if val < 0 else "neutral")


def fmt(val, suffix="%", decimals=2, plus=True):
    if val is None:
        return "—"
    sign = "+" if plus and val > 0 else ""
    return f"{sign}{val:.{decimals}f}{suffix}"


def make_bar(val, max_val=30, good_positive=True):
    """수평 막대 HTML"""
    if val is None or max_val == 0:
        return ""
    pct = min(abs(val) / max_val * 100, 100)
    color = "#26a69a" if (good_positive and val > 0) or (not good_positive and val < 0) else "#ef5350"
    return f'<div class="bar-wrap"><div class="bar" style="width:{pct:.1f}%;background:{color}"></div><span>{fmt(val)}</span></div>'


def generate_html(results: list, date_str: str) -> str:
    scenarios = []
    for r in results:
        m = r.get("metrics", {})
        if m.get("trade_count", 0) == 0:
            continue
        scenarios.append({
            "name":         r["name"],
            "return":       m.get("total_return", 0),
            "cagr":         m.get("cagr", 0),
            "win_rate":     m.get("win_rate", 0),
            "mdd":          m.get("mdd", 0),
            "sharpe":       m.get("sharpe", 0),
            "pf":           m.get("profit_factor") or 0,
            "trades":       m.get("trade_count", 0),
            "wins":         m.get("win_count", 0),
            "losses":       m.get("loss_count", 0),
            "avg_win":      m.get("avg_win", 0),
            "avg_loss":     m.get("avg_loss", 0),
            "max_consec":   m.get("max_consec_loss", 0),
            "initial":      m.get("initial_cash", 5000000),
            "final":        m.get("final_value", 5000000),
            "pnl":          m.get("total_pnl_krw", 0),
            "sortino":      m.get("sortino", 0),
            "calmar":       m.get("calmar", 0),
        })

    if not scenarios:
        return "<html><body>결과 없음</body></html>"

    # 최고 시나리오 판별
    best = max(scenarios, key=lambda x: x["return"])

    # 시나리오 카드 HTML
    cards_html = ""
    for s in scenarios:
        is_best = s["name"] == best["name"]
        ret_color = make_color(s["return"])
        pf_color = "positive" if s["pf"] >= 1.0 else "negative"
        badge = '<span class="badge">⭐ 최우수</span>' if is_best else ""
        pnl_sign = "+" if s["pnl"] >= 0 else ""

        cards_html += f"""
        <div class="card {'card-best' if is_best else ''}">
            <div class="card-header">
                <h3>{s['name']}</h3>
                {badge}
            </div>
            <div class="big-return {ret_color}">
                {fmt(s['return'])}
            </div>
            <div class="pnl {ret_color}">
                순손익 {pnl_sign}{s['pnl']:,.0f}원
            </div>
            <div class="metrics-grid">
                <div class="metric">
                    <div class="metric-label">승률</div>
                    <div class="metric-value {'positive' if s['win_rate']>=50 else 'negative'}">{fmt(s['win_rate'], '%')}</div>
                </div>
                <div class="metric">
                    <div class="metric-label">MDD</div>
                    <div class="metric-value negative">{fmt(s['mdd'], '%')}</div>
                </div>
                <div class="metric">
                    <div class="metric-label">샤프</div>
                    <div class="metric-value {'positive' if s['sharpe']>0 else 'negative'}">{fmt(s['sharpe'], '', plus=True)}</div>
                </div>
                <div class="metric">
                    <div class="metric-label">PF</div>
                    <div class="metric-value {pf_color}">{fmt(s['pf'], '', plus=False)}</div>
                </div>
                <div class="metric">
                    <div class="metric-label">평균익절</div>
                    <div class="metric-value positive">{fmt(s['avg_win'], '%')}</div>
                </div>
                <div class="metric">
                    <div class="metric-label">평균손절</div>
                    <div class="metric-value negative">{fmt(s['avg_loss'], '%')}</div>
                </div>
                <div class="metric">
                    <div class="metric-label">거래수</div>
                    <div class="metric-value neutral">{s['trades']}건</div>
                </div>
                <div class="metric">
                    <div class="metric-label">최대연속손절</div>
                    <div class="metric-value {'negative' if s['max_consec']>=5 else 'neutral'}">{s['max_consec']}건</div>
                </div>
            </div>
            <div class="win-loss-bar">
                <div class="wl-win" style="width:{s['wins']/(s['trades'] or 1)*100:.0f}%">
                    승 {s['wins']}
                </div>
                <div class="wl-loss" style="width:{s['losses']/(s['trades'] or 1)*100:.0f}%">
                    패 {s['losses']}
                </div>
            </div>
        </div>"""

    # 비교 테이블
    table_rows = ""
    for s in scenarios:
        is_best = s["name"] == best["name"]
        ret_color = "#26a69a" if s["return"] > 0 else "#ef5350"
        table_rows += f"""
            <tr {'class="best-row"' if is_best else ''}>
                <td>{'⭐ ' if is_best else ''}{s['name']}</td>
                <td style="color:{ret_color};font-weight:700">{fmt(s['return'])}</td>
                <td style="color:{ret_color}">{fmt(s['cagr'])}</td>
                <td style="color:{'#26a69a' if s['win_rate']>=50 else '#ef5350'}">{fmt(s['win_rate'], '%')}</td>
                <td style="color:#ef5350">{fmt(s['mdd'], '%')}</td>
                <td style="color:{'#26a69a' if s['sharpe']>0 else '#ef5350'}">{fmt(s['sharpe'], '', plus=True)}</td>
                <td style="color:{'#26a69a' if s['pf']>=1 else '#ef5350'}">{fmt(s['pf'], '', plus=False)}</td>
                <td>{s['trades']}</td>
            </tr>"""

    # 조치 추천
    best_return = best["return"]
    best_pf = best["pf"]
    best_wr = best["win_rate"]

    if best_return > 5 and best_pf > 1.5:
        action = "✅ <strong>현행 유지</strong> — 전략이 잘 작동하고 있습니다."
        action_class = "action-good"
    elif best_return > 0 and best_pf > 1.0:
        action = "⚠️ <strong>소폭 조정 검토</strong> — 흑자이나 개선 여지 있습니다."
        action_class = "action-warn"
    elif best_return < 0 and best_pf < 1.0:
        action = "🔴 <strong>임계치 상향 검토</strong> — 모든 시나리오 적자. 시장 약세 가능성."
        action_class = "action-bad"
    else:
        action = "🔍 <strong>추가 관찰 필요</strong> — 수급 데이터 누적 후 재판단."
        action_class = "action-neutral"

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>영암9 주간 리뷰 — {date_str}</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@300;400;500;700;900&family=JetBrains+Mono:wght@400;700&display=swap');

  :root {{
    --bg: #0d1117;
    --surface: #161b22;
    --surface2: #21262d;
    --border: #30363d;
    --text: #e6edf3;
    --muted: #8b949e;
    --positive: #26a69a;
    --negative: #ef5350;
    --accent: #f0a500;
    --best: #1a2a1a;
    --best-border: #26a69a;
  }}

  * {{ box-sizing: border-box; margin: 0; padding: 0; }}

  body {{
    background: var(--bg);
    color: var(--text);
    font-family: 'Noto Sans KR', sans-serif;
    min-height: 100vh;
    padding: 32px 24px;
  }}

  .header {{
    text-align: center;
    margin-bottom: 48px;
    padding-bottom: 32px;
    border-bottom: 1px solid var(--border);
  }}

  .header-label {{
    font-size: 12px;
    letter-spacing: 4px;
    text-transform: uppercase;
    color: var(--accent);
    font-family: 'JetBrains Mono', monospace;
    margin-bottom: 12px;
  }}

  .header h1 {{
    font-size: 36px;
    font-weight: 900;
    letter-spacing: -1px;
    margin-bottom: 8px;
  }}

  .header-date {{
    color: var(--muted);
    font-size: 14px;
    font-family: 'JetBrains Mono', monospace;
  }}

  .section-title {{
    font-size: 13px;
    letter-spacing: 3px;
    text-transform: uppercase;
    color: var(--muted);
    margin-bottom: 20px;
    font-family: 'JetBrains Mono', monospace;
  }}

  /* 카드 그리드 */
  .cards {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
    gap: 20px;
    margin-bottom: 48px;
  }}

  .card {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 24px;
    transition: transform 0.2s;
  }}

  .card:hover {{ transform: translateY(-2px); }}

  .card-best {{
    background: var(--best);
    border-color: var(--best-border);
    box-shadow: 0 0 24px rgba(38,166,154,0.15);
  }}

  .card-header {{
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 16px;
  }}

  .card-header h3 {{
    font-size: 15px;
    font-weight: 700;
    color: var(--text);
  }}

  .badge {{
    font-size: 11px;
    background: var(--positive);
    color: #000;
    padding: 2px 8px;
    border-radius: 20px;
    font-weight: 700;
  }}

  .big-return {{
    font-size: 42px;
    font-weight: 900;
    font-family: 'JetBrains Mono', monospace;
    letter-spacing: -2px;
    line-height: 1;
    margin-bottom: 6px;
  }}

  .pnl {{
    font-size: 13px;
    font-family: 'JetBrains Mono', monospace;
    margin-bottom: 20px;
    opacity: 0.8;
  }}

  .positive {{ color: var(--positive); }}
  .negative {{ color: var(--negative); }}
  .neutral  {{ color: var(--muted); }}

  .metrics-grid {{
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 12px;
    margin-bottom: 16px;
  }}

  .metric {{ background: var(--surface2); border-radius: 8px; padding: 10px 12px; }}
  .metric-label {{ font-size: 10px; color: var(--muted); letter-spacing: 1px; text-transform: uppercase; margin-bottom: 4px; }}
  .metric-value {{ font-size: 16px; font-weight: 700; font-family: 'JetBrains Mono', monospace; }}

  /* 승/패 바 */
  .win-loss-bar {{
    display: flex;
    height: 28px;
    border-radius: 6px;
    overflow: hidden;
    font-size: 11px;
    font-weight: 700;
  }}
  .wl-win {{
    background: var(--positive);
    color: #000;
    display: flex;
    align-items: center;
    justify-content: center;
    min-width: 30px;
  }}
  .wl-loss {{
    background: var(--negative);
    color: #fff;
    display: flex;
    align-items: center;
    justify-content: center;
    flex: 1;
    min-width: 30px;
  }}

  /* 비교 테이블 */
  .table-wrap {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 12px;
    overflow: hidden;
    margin-bottom: 48px;
  }}

  table {{
    width: 100%;
    border-collapse: collapse;
    font-family: 'JetBrains Mono', monospace;
    font-size: 13px;
  }}

  thead {{ background: var(--surface2); }}

  th {{
    padding: 14px 16px;
    text-align: right;
    color: var(--muted);
    font-size: 11px;
    letter-spacing: 1px;
    text-transform: uppercase;
    font-weight: 500;
  }}
  th:first-child {{ text-align: left; }}

  td {{
    padding: 14px 16px;
    text-align: right;
    border-top: 1px solid var(--border);
  }}
  td:first-child {{ text-align: left; font-family: 'Noto Sans KR', sans-serif; }}

  tr:hover td {{ background: var(--surface2); }}
  .best-row td {{ background: var(--best); }}
  .best-row:hover td {{ background: #1e3a1e; }}

  /* 조치 카드 */
  .action-card {{
    border-radius: 12px;
    padding: 24px 28px;
    margin-bottom: 32px;
    font-size: 15px;
    line-height: 1.6;
  }}
  .action-good    {{ background: #0d2818; border: 1px solid #26a69a; }}
  .action-warn    {{ background: #2a1f00; border: 1px solid #f0a500; }}
  .action-bad     {{ background: #2a0d0d; border: 1px solid #ef5350; }}
  .action-neutral {{ background: var(--surface); border: 1px solid var(--border); }}

  .footer {{
    text-align: center;
    color: var(--muted);
    font-size: 12px;
    font-family: 'JetBrains Mono', monospace;
    margin-top: 48px;
    padding-top: 24px;
    border-top: 1px solid var(--border);
  }}
</style>
</head>
<body>

<div class="header">
  <div class="header-label">Weekly Review</div>
  <h1>영암9 전략 리포트</h1>
  <div class="header-date">{date_str} · 백테스트 기준일 2025-08-01 ~</div>
</div>

<div class="section-title">시나리오별 성과</div>
<div class="cards">
{cards_html}
</div>

<div class="section-title">비교 테이블</div>
<div class="table-wrap">
  <table>
    <thead>
      <tr>
        <th>시나리오</th>
        <th>수익률</th>
        <th>CAGR</th>
        <th>승률</th>
        <th>MDD</th>
        <th>샤프</th>
        <th>PF</th>
        <th>거래수</th>
      </tr>
    </thead>
    <tbody>
      {table_rows}
    </tbody>
  </table>
</div>

<div class="section-title">이번 주 조치</div>
<div class="action-card {action_class}">
  {action}
</div>

<div class="footer">
  generated by 영암9 backtest engine · {datetime.now().strftime('%Y-%m-%d %H:%M')}
</div>

</body>
</html>"""


def main():
    if len(sys.argv) < 2:
        print("사용: python3 generate_report.py result_*.json [날짜]")
        sys.exit(1)

    json_path = sys.argv[1]
    date_str  = sys.argv[2] if len(sys.argv) > 2 else datetime.now().strftime("%Y-%m-%d")

    with open(json_path, encoding="utf-8") as f:
        results = json.load(f)

    html = generate_html(results, date_str)

    out_dir = os.path.dirname(json_path)
    out_path = os.path.join(out_dir, f"weekly_report_{date_str}.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"✅ 리포트 저장: {out_path}")


if __name__ == "__main__":
    main()
