import os, sys, json, argparse, datetime

def load_json(path):
    if not path or path.lower()=="none" or not os.path.exists(path): return []
    try:
        with open(path, encoding="utf-8") as f: data=json.load(f)
        return data if isinstance(data,list) else [data]
    except Exception as e: print(f"⚠️ {path}: {e}"); return []

def load_json_dict(path):
    """signal_check 결과는 dict 구조(by_grade 등)를 그대로 유지해야 하므로
    list로 강제 변환하지 않는 별도 로더 (2026-06-27 추가)"""
    if not path or path.lower()=="none" or not os.path.exists(path): return None
    try:
        with open(path, encoding="utf-8") as f: return json.load(f)
    except Exception as e: print(f"⚠️ {path}: {e}"); return None

def safe(v,d=0):
    try: return float(v) if v is not None else d
    except: return d

def color(v):
    v=safe(v)
    return "#ef5350" if v>0 else "#26a69a" if v<0 else "#888"

def fmt_pct(v):
    v=safe(v); return f"{'+'if v>=0 else ''}{v:.2f}%"

def best(r):
    if not r: return {}
    return max(r,key=lambda x:safe(x.get("metrics",{}).get("profit_factor",x.get("metrics",{}).get("PF",0))))

def gm(m,*keys):
    for k in keys:
        if k in m: return safe(m[k])
    return 0

def scenario_table(results,title):
    if not results: return f'<div class="card"><h2>{title}</h2><p class="empty">결과 없음</p></div>'
    rows=""
    for r in results:
        m=r.get("metrics",{})
        ret=gm(m,"total_return","total_return_pct"); wr=gm(m,"win_rate","win_rate_pct")
        pf=gm(m,"profit_factor","PF"); mdd=gm(m,"mdd","MDD")
        cnt=int(gm(m,"total_trades","trade_count")); hold=gm(m,"avg_hold_days","avg_hold")
        rows+=f'<tr><td>{r.get("name","")}</td><td style="color:{color(ret)};font-weight:700">{fmt_pct(ret)}</td><td>{wr:.1f}%</td><td style="color:{color(pf-1)}">{pf:.2f}</td><td style="color:{color(-mdd)}">{mdd:.1f}%</td><td>{cnt}</td><td>{hold:.1f}일</td></tr>'
    b=best(results); bm=b.get("metrics",{})
    br=gm(bm,"total_return","total_return_pct"); bw=gm(bm,"win_rate","win_rate_pct")
    bp=gm(bm,"profit_factor","PF"); bd=gm(bm,"mdd","MDD")
    return f'''<div class="card"><h2>{title}</h2>
    <div class="summary">
      <div class="stat"><div class="label">최고 수익률</div><div class="value" style="color:{color(br)}">{fmt_pct(br)}</div></div>
      <div class="stat"><div class="label">승률</div><div class="value">{bw:.1f}%</div></div>
      <div class="stat"><div class="label">PF</div><div class="value" style="color:{color(bp-1)}">{bp:.2f}</div></div>
      <div class="stat"><div class="label">MDD</div><div class="value" style="color:{color(-bd)}">{bd:.1f}%</div></div>
    </div>
    <table><thead><tr><th>시나리오</th><th>수익률</th><th>승률</th><th>PF</th><th>MDD</th><th>거래수</th><th>평균보유</th></tr></thead>
    <tbody>{rows}</tbody></table></div>'''

def eq_dataset(results, label, hex_color):
    b = best(results)
    if not b: return "", []
    name = b.get("name", "")
    eq = b.get("equity", [])
    if eq:
        lbl = label + " (" + name + ")"
        ds = json.dumps({"label": lbl, "borderColor": hex_color,
                         "backgroundColor": hex_color+"22", "fill": True,
                         "tension": 0.3, "pointRadius": 0, "borderWidth": 2,
                         "data": [round(e[1]) for e in eq]})
        return ds, [e[0] for e in eq]
    trades = b.get("trades", [])
    initial = safe(b.get("metrics", {}).get("initial_cash", 5000000))
    em = {}; running = initial
    for t in sorted(trades, key=lambda x: str(x.get("exit_date", x.get("sell_time", "")))):
        dt = str(t.get("exit_date", t.get("sell_time", "")))[:10]
        running += safe(t.get("profit", t.get("profit_krw", 0)))
        if dt: em[dt] = running
    if not em: return "", []
    dates = sorted(em.keys())
    lbl = label + " (" + name + ")"
    ds = json.dumps({"label": lbl, "borderColor": hex_color,
                     "backgroundColor": hex_color+"22", "fill": True,
                     "tension": 0.3, "pointRadius": 0, "borderWidth": 2,
                     "data": [round(em[d]) for d in dates]})
    return ds, dates

def equity_chart(sbot,sbo2):
    ds1,lb1=eq_dataset(sbot,"sbot","#2196F3")
    ds2,lb2=eq_dataset(sbo2,"sbo2","#FF5722")
    datasets=[d for d in [ds1,ds2] if d]
    labels=lb1 if lb1 else lb2
    if not datasets: return ""
    return f'''<div class="card"><h2>📈 자산 곡선 비교</h2><canvas id="ec" height="80"></canvas></div>
    <script>new Chart(document.getElementById("ec"),{{type:"line",data:{{labels:{json.dumps(labels)},datasets:[{",".join(datasets)}]}},options:{{responsive:true,plugins:{{legend:{{labels:{{color:"#ccc"}}}}}},scales:{{x:{{ticks:{{color:"#888",maxTicksLimit:12}},grid:{{color:"#333"}}}},y:{{ticks:{{color:"#888",callback:v=>v.toLocaleString()+"원"}},grid:{{color:"#333"}}}}}}}}}});</script>'''

def signal_check_card(data):
    """sbo2_signal_check_*.json (run_sbo2_signal_check.py 결과)을
    슬롯별(swing/trend/tele) 카드로 표시 — 다른 결과(list)와 형태가
    달라(dict) 별도 렌더링 함수로 분리 (2026-06-27 추가)"""
    if not data:
        return '<div class="card"><h2>🔍 sbo2 실거래 신호 사후검증 (VCP/추세/텔레)</h2><p class="empty">결과 없음</p></div>'
    by_grade = data.get("by_grade", {}) if isinstance(data, dict) else {}
    if not by_grade:
        return '<div class="card"><h2>🔍 sbo2 실거래 신호 사후검증 (VCP/추세/텔레)</h2><p class="empty">결과 없음</p></div>'

    label_map = {"swing": "스윙(VCP)", "trend": "추세", "tele": "텔레스윙"}
    rows = ""
    for grade, g in by_grade.items():
        total = g.get("total", 0)
        win_pct = (g.get("목표1도달", 0) / total * 100) if total else 0
        loss_pct = (g.get("손절", 0) / total * 100) if total else 0
        undecided_pct = (g.get("미결", 0) / total * 100) if total else 0
        ret = g.get("avg_return_pct", 0)
        hold = g.get("avg_hold_days", 0)
        rows += (f'<tr><td>{label_map.get(grade, grade)}</td>'
                 f'<td>{total}</td>'
                 f'<td style="color:{color(ret)};font-weight:700">{fmt_pct(ret)}</td>'
                 f'<td>{win_pct:.1f}%</td><td>{loss_pct:.1f}%</td><td>{undecided_pct:.1f}%</td>'
                 f'<td>{hold:.1f}일</td></tr>')

    params = data.get("params", {})
    skipped = data.get("skipped", 0)
    note = (f'<p style="color:#888;font-size:.85rem;margin-bottom:12px">'
            f'추적기간 {params.get("hold_days", "?")}일 | 분석제외(데이터부족) {skipped}건 | '
            f'※ 종가기반 근사ATR 사용, 미결=관찰기간 내 손절·목표 미도달</p>')

    return f'''<div class="card"><h2>🔍 sbo2 실거래 신호 사후검증 (VCP/추세/텔레)</h2>
    {note}
    <table><thead><tr><th>슬롯</th><th>건수</th><th>평균수익률</th><th>목표1도달</th><th>손절</th><th>미결</th><th>평균보유</th></tr></thead>
    <tbody>{rows}</tbody></table></div>'''


def sshow_card(data):
    """run_sshow_backtest.py 결과(체크인 알림/통계/pending)를 카드로 표시
    (2026-06-30 추가 — 생쇼 전문가4인 추천 적중률 백테스터)"""
    if not data:
        return '<div class="card"><h2>📺 생쇼(전문가4인) 추천 결과 체크인</h2><p class="empty">결과 없음</p></div>'

    stats = data.get("stats", {}) or {}
    total = stats.get("total", 0)
    hit   = stats.get("hit", 0)
    stop  = stats.get("stop", 0)
    hold  = stats.get("hold", 0)
    hit_rate = stats.get("hit_rate", 0) * 100
    sample_ok = stats.get("sample_size_ok", False)

    summary = f'''<div class="summary">
      <div class="stat"><div class="label">판정 건수</div><div class="value">{total}</div></div>
      <div class="stat"><div class="label">적중률</div><div class="value" style="color:{color(hit_rate-50)}">{hit_rate:.1f}%</div></div>
      <div class="stat"><div class="label">적중/손절/보합</div><div class="value" style="font-size:1.1rem">{hit}/{stop}/{hold}</div></div>
      <div class="stat"><div class="label">표본 신뢰도</div><div class="value" style="font-size:1.1rem;color:{"#26a69a" if sample_ok else "#888"}">{"충분(20+)" if sample_ok else "부족(20미만)"}</div></div>
    </div>'''

    # 이번 체크인에서 새로 판정/알림된 건들
    notis = data.get("checkin_notifications", []) or []
    noti_rows = ""
    kind_label = {"hit": "🎯 적중", "stop": "🛑 손절", "hold": "⏱️ 보합", "progress": "📍 진행중"}
    for n in notis:
        noti_rows += (f'<tr><td>{n.get("name","")}</td>'
                      f'<td>{n.get("stage","")}일</td>'
                      f'<td>{kind_label.get(n.get("kind",""), n.get("kind",""))}</td>'
                      f'<td style="text-align:left;color:#ccc">{n.get("text","")}</td></tr>')
    noti_table = (f'<table><thead><tr><th>종목</th><th>경과</th><th>구분</th><th style="text-align:left">내용</th></tr></thead>'
                  f'<tbody>{noti_rows}</tbody></table>') if noti_rows else '<p class="empty">이번 체크인 신규 판정 없음</p>'

    # 현재 미확정(pending) 추천 목록 — 현재가/현재수익률 포함
    pending = data.get("pending", []) or []
    pend_rows = ""
    for p in pending:
        valid_tag = "" if p.get("price_valid", True) else ' style="color:#ef5350"'
        src_tag = "ATR" if p.get("price_source") == "atr" else "원문"
        cur_price = p.get("current_price")
        cur_pct = p.get("current_pct")
        cur_price_str = f'{cur_price:,.0f}' if cur_price is not None else "-"
        cur_pct_str = fmt_pct(cur_pct) if cur_pct is not None else "-"
        pct_color = color(cur_pct) if cur_pct is not None else "#888"
        pend_rows += (f'<tr{valid_tag}><td>{p.get("date","")}</td><td>{p.get("name","")}</td>'
                      f'<td>{safe(p.get("buy_price")):,.0f}</td>'
                      f'<td>{safe(p.get("stop_price")):,.0f}</td>'
                      f'<td>{safe(p.get("tgt_price")):,.0f}</td>'
                      f'<td>{cur_price_str}</td>'
                      f'<td style="color:{pct_color};font-weight:700">{cur_pct_str}</td>'
                      f'<td>{p.get("checkin_label", "")}</td>'
                      f'<td>{src_tag}</td></tr>')
    pend_table = (f'<table><thead><tr><th>추천일</th><th>종목</th><th>매수가</th><th>손절가</th><th>목표가</th>'
                  f'<th>현재가</th><th>현재수익률</th><th>경과</th><th>가격출처</th></tr></thead>'
                  f'<tbody>{pend_rows}</tbody></table>') if pend_rows else '<p class="empty">미확정 추천 없음</p>'

    note = ('<p style="color:#888;font-size:.85rem;margin:12px 0">'
            '※ 매수가는 mbn 원문 대신 kr_theme_finance.db 실제 종가 기준, '
            '목표/손절가는 ATR(stop×2.0/target×3.0) 재계산값 사용 — '
            '액면분할 등으로 원문 가격이 오염되는 문제 방지. '
            '적중률 = 적중/(적중+손절), 보합은 무승부로 분모 제외. '
            '7/14일 역일 기준 체크인, pending은 실행 시점 현재가/수익률 항상 표시.</p>')

    return f'''<div class="card"><h2>📺 생쇼(전문가4인) 추천 결과 체크인</h2>
    {summary}{note}
    <h3 style="color:#90CAF9;margin:16px 0 10px;font-size:1.1rem">이번 체크인 신규 판정</h3>
    {noti_table}
    <h3 style="color:#90CAF9;margin:20px 0 10px;font-size:1.1rem">미확정(pending) 추천 목록</h3>
    {pend_table}
    </div>'''


def build_html(sbot,sbo2,date_str,signal_check=None,sshow=None):
    return f'''<!DOCTYPE html><html lang="ko"><head><meta charset="UTF-8">
    <title>영암9 주간 백테스트 {date_str}</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>*{{box-sizing:border-box;margin:0;padding:0}}body{{background:#1a1a2e;color:#eee;font-family:Segoe UI,sans-serif;padding:20px}}h1{{text-align:center;color:#90CAF9;margin:20px 0 10px;font-size:1.8rem}}.sub{{text-align:center;color:#888;margin-bottom:30px}}.card{{background:#16213e;border-radius:12px;padding:24px;margin-bottom:24px}}h2{{color:#90CAF9;margin-bottom:16px}}.summary{{display:flex;gap:16px;margin-bottom:20px;flex-wrap:wrap}}.stat{{background:#0f3460;border-radius:8px;padding:16px 24px;flex:1;min-width:120px;text-align:center}}.label{{color:#888;font-size:.85rem;margin-bottom:6px}}.value{{font-size:1.6rem;font-weight:700}}table{{width:100%;border-collapse:collapse}}th{{background:#0f3460;color:#90CAF9;padding:10px 14px;text-align:right;font-size:.85rem}}th:first-child{{text-align:left}}td{{padding:10px 14px;border-bottom:1px solid #2a2a4a;text-align:right;font-size:.9rem}}td:first-child{{text-align:left;color:#ccc}}tr:hover td{{background:#1a2a4a}}.empty{{color:#666;padding:20px;text-align:center}}</style>
    </head><body>
    <h1>🏆 영암9 주간 백테스트 리뷰</h1><p class="sub">생성일: {date_str}</p>
    {scenario_table(sbot,"📊 스윙봇 (sbot)")}
    {scenario_table(sbo2,"📊 리나 스윙봇 (sbo2)")}
    {equity_chart(sbot,sbo2)}
    {signal_check_card(signal_check)}
    {sshow_card(sshow)}
    </body></html>'''

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--sbot",default="none")
    parser.add_argument("--sbo2",default="none")
    parser.add_argument("--signal-check",default="none",
                         help="run_sbo2_signal_check.py 결과 JSON (sbo2 실거래 신호 사후검증)")
    parser.add_argument("--sshow",default="none",
                         help="run_sshow_backtest.py 결과 JSON (생쇼 전문가추천 적중률)")
    parser.add_argument("--date",default=datetime.date.today().strftime("%Y-%m-%d"))
    parser.add_argument("--out",default="")
    args=parser.parse_args()
    sbot=load_json(args.sbot); sbo2=load_json(args.sbo2)
    signal_check=load_json_dict(args.signal_check)
    sshow=load_json_dict(args.sshow)
    if not sbot and not sbo2: print("❌ 결과 없음"); sys.exit(1)
    print(f"✅ sbot 시나리오: {len(sbot)}개")
    print(f"✅ sbo2 시나리오: {len(sbo2)}개")
    print(f"✅ sbo2 신호검증: {'있음' if signal_check else '없음'}")
    print(f"✅ 생쇼 체크인: {'있음' if sshow else '없음'}")
    html=build_html(sbot,sbo2,args.date,signal_check,sshow)
    rd=os.path.join(os.path.dirname(__file__),"results"); os.makedirs(rd,exist_ok=True)
    out=args.out or os.path.join(rd,f"weekly_report_{args.date}.html")
    with open(out,"w",encoding="utf-8") as f: f.write(html)
    print(f"📊 통합 리포트 저장: {out}")

if __name__=="__main__": main()
