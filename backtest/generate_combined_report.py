import os, sys, json, argparse, datetime

def load_json(path):
    if not path or path.lower()=="none" or not os.path.exists(path): return []
    try:
        with open(path, encoding="utf-8") as f: data=json.load(f)
        return data if isinstance(data,list) else [data]
    except Exception as e: print(f"⚠️ {path}: {e}"); return []

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

def build_html(sbot,sbo2,date_str):
    return f'''<!DOCTYPE html><html lang="ko"><head><meta charset="UTF-8">
    <title>영암9 주간 백테스트 {date_str}</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>*{{box-sizing:border-box;margin:0;padding:0}}body{{background:#1a1a2e;color:#eee;font-family:Segoe UI,sans-serif;padding:20px}}h1{{text-align:center;color:#90CAF9;margin:20px 0 10px;font-size:1.8rem}}.sub{{text-align:center;color:#888;margin-bottom:30px}}.card{{background:#16213e;border-radius:12px;padding:24px;margin-bottom:24px}}h2{{color:#90CAF9;margin-bottom:16px}}.summary{{display:flex;gap:16px;margin-bottom:20px;flex-wrap:wrap}}.stat{{background:#0f3460;border-radius:8px;padding:16px 24px;flex:1;min-width:120px;text-align:center}}.label{{color:#888;font-size:.85rem;margin-bottom:6px}}.value{{font-size:1.6rem;font-weight:700}}table{{width:100%;border-collapse:collapse}}th{{background:#0f3460;color:#90CAF9;padding:10px 14px;text-align:right;font-size:.85rem}}th:first-child{{text-align:left}}td{{padding:10px 14px;border-bottom:1px solid #2a2a4a;text-align:right;font-size:.9rem}}td:first-child{{text-align:left;color:#ccc}}tr:hover td{{background:#1a2a4a}}.empty{{color:#666;padding:20px;text-align:center}}</style>
    </head><body>
    <h1>🏆 영암9 주간 백테스트 리뷰</h1><p class="sub">생성일: {date_str}</p>
    {scenario_table(sbot,"📊 스윙봇 (sbot)")}
    {scenario_table(sbo2,"📊 리나 스윙봇 (sbo2)")}
    {equity_chart(sbot,sbo2)}
    </body></html>'''

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--sbot",default="none")
    parser.add_argument("--sbo2",default="none")
    parser.add_argument("--date",default=datetime.date.today().strftime("%Y-%m-%d"))
    parser.add_argument("--out",default="")
    args=parser.parse_args()
    sbot=load_json(args.sbot); sbo2=load_json(args.sbo2)
    if not sbot and not sbo2: print("❌ 결과 없음"); sys.exit(1)
    print(f"✅ sbot 시나리오: {len(sbot)}개")
    print(f"✅ sbo2 시나리오: {len(sbo2)}개")
    html=build_html(sbot,sbo2,args.date)
    rd=os.path.join(os.path.dirname(__file__),"results"); os.makedirs(rd,exist_ok=True)
    out=args.out or os.path.join(rd,f"weekly_report_{args.date}.html")
    with open(out,"w",encoding="utf-8") as f: f.write(html)
    print(f"📊 통합 리포트 저장: {out}")

if __name__=="__main__": main()
