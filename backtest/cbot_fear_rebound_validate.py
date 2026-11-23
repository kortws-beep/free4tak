"""cbot 야간 매수 금지(공포탐욕<50) 검증 — 11-23 대장 "매번 한 타임씩 느리니 봐보자".
1) 수집: python backtest/cbot_fear_rebound_validate.py fetch  → backtest/data/fng_coins.json
2) 분석: python backtest/cbot_fear_rebound_validate.py backtest/data/fng_coins.json
결론(11-23): 극단공포 속 BTC 급반등일 매수는 이후 수익이 기준선 수준·2023~ 마이너스, 탐욕≥50 급등일이 훨씬 좋음 → 규칙 유지."""
import sys
if len(sys.argv) > 1 and sys.argv[1] == "fetch":
    import requests, time, json, datetime, sys
    OUT="backtest/data/"
    def days(mk, since="2018-02-01"):
        rows, to = {}, None
        while True:
            p={"market":mk,"count":200}
            if to: p["to"]=to
            r=requests.get("https://api.upbit.com/v1/candles/days",params=p,timeout=10).json()
            if not isinstance(r,list) or not r: break
            for x in r: rows[x["candle_date_time_kst"][:10]]=(x["opening_price"],x["high_price"],x["low_price"],x["trade_price"])
            last=r[-1]["candle_date_time_utc"]
            if last[:10]<since: break
            to=last.replace("T"," "); time.sleep(0.12)
        return dict(sorted(rows.items()))
    data={}
    for mk in ["KRW-BTC","KRW-ETH","KRW-XRP","KRW-ADA","KRW-DOGE","KRW-SOL","KRW-LINK","KRW-TRX","KRW-XLM","KRW-ETC","KRW-BCH","KRW-SAND","KRW-AVAX","KRW-DOT"]:
        data[mk]=days(mk); print(mk,len(data[mk]),flush=True)
    fng={datetime.datetime.utcfromtimestamp(int(d["timestamp"])).strftime("%Y-%m-%d"):int(d["value"]) for d in requests.get("https://api.alternative.me/fng/?limit=0",timeout=15).json()["data"]}
    json.dump({"px":data,"fng":fng},open(OUT+"fng_coins.json","w"))
    print("fng",len(fng),min(fng),max(fng))
    sys.exit(0)
import json, statistics as st, sys
D=json.load(open(sys.argv[1])); px=D["px"]; fng=D["fng"]
btc=px["KRW-BTC"]; dates=list(btc)
def fwd(c, d, n):
    s=px[c]; ks=list(s); 
    if d not in s: return None
    i=ks.index(d) if False else None
    return None
idx={c:{d:i for i,d in enumerate(px[c])} for c in px}
keys={c:list(px[c]) for c in px}
def stats(c,d):
    i=idx[c].get(d); k=keys[c]; s=px[c]
    if i is None or i<1 or i+7>=len(k): return None
    e=s[k[i]][3]
    r=lambda n: s[k[i+n]][3]/e-1
    mh=max(s[k[i+j]][1] for j in (1,2,3))/e-1; ml=min(s[k[i+j]][2] for j in (1,2,3))/e-1
    return r(1),r(3),r(7),mh,ml
def chg(d):
    i=idx["KRW-BTC"][d]; k=keys["KRW-BTC"]
    return btc[d][3]/btc[k[i-1]][3]-1 if i>0 else None
def group(name, cond, since="2018-02-01", coins=("KRW-BTC",)):
    rows=[]
    for d in dates[1:]:
        if d<since or d not in fng: continue
        c=chg(d)
        if c is None or not cond(c,fng[d]): continue
        vals=[stats(x,d) for x in coins]; vals=[v for v in vals if v]
        if not vals: continue
        rows.append([st.mean(v[j] for v in vals) for j in range(5)])
    if not rows: print(f"{name:34s} n=0"); return
    m=lambda j: st.mean(r[j] for r in rows)*100
    w=lambda j: sum(r[j]>0 for r in rows)/len(rows)*100
    hit=sum(r[3]>=0.03 for r in rows)/len(rows)*100; bad=sum(r[4]<=-0.05 for r in rows)/len(rows)*100
    print(f"{name:34s} n={len(rows):4d} | D+1 {m(0):+5.2f}%({w(0):3.0f}%) D+3 {m(1):+5.2f}%({w(1):3.0f}%) D+7 {m(2):+5.2f}%({w(2):3.0f}%) | 3일내 +3%도달 {hit:3.0f}% · -5%터치 {bad:3.0f}%")
ALTS=tuple(c for c in px if c!="KRW-BTC")
for since in ("2018-02-01","2023-01-01"):
  for label,coins in (("BTC",("KRW-BTC",)),("알트13",ALTS)):
    print(f"\n=== {label} · {since[:4]}~ ===")
    group("전체(기준)", lambda c,f: True, since, coins)
    group("BTC+1%↑ · 탐욕≥50 (지금 야간 허용)", lambda c,f: c>=0.01 and f>=50, since, coins)
    group("BTC+1%↑ · 탐욕<50 (지금 야간 금지)", lambda c,f: c>=0.01 and f<50, since, coins)
    group("  └ BTC+3%↑ · 탐욕<50", lambda c,f: c>=0.03 and f<50, since, coins)
    group("  └ BTC+3%↑ · 탐욕≤25(극단공포)", lambda c,f: c>=0.03 and f<=25, since, coins)
    group("  └ BTC+5%↑ · 탐욕≤25", lambda c,f: c>=0.05 and f<=25, since, coins)
    group("BTC+3%↑ · 탐욕≥50", lambda c,f: c>=0.03 and f>=50, since, coins)

import datetime as _dt
def prevd(d,n=1): return (_dt.date.fromisoformat(d)-_dt.timedelta(days=n)).isoformat()
print("\n=== 보강: '한 타임 늦게' 들어가면? (알트13) ===")
for since in ("2018-02-01","2023-01-01"):
    print(f"-- {since[:4]}~")
    def c2(c,f,d=None): return True
    rows=[]
    for name,cond in (
        ("극단공포 급반등 다음날(전날 BTC+3%·탐욕≤25, 오늘도 BTC+)", lambda d,c: d and fng.get(prevd(d),99)<=25 and chg(prevd(d)) is not None and chg(prevd(d))>=0.03 and c>0),
        ("탐욕지수 하루 +10↑ 반등 · 여전히 <50", lambda d,c: fng.get(d,0)<50 and fng.get(d,0)-fng.get(prevd(d),fng.get(d,0))>=10),
    ):
        sel=[]
        for d in dates[2:]:
            if d<since or d not in fng: continue
            c=chg(d)
            if c is None or prevd(d) not in idx["KRW-BTC"]: continue
            if cond(d,c):
                v=[stats(x,d) for x in ALTS]; v=[x for x in v if x]
                if v: sel.append([st.mean(x[j] for x in v) for j in range(5)])
        if sel:
            m=lambda j: st.mean(r[j] for r in sel)*100
            print(f"{name:44s} n={len(sel):3d} | D+1 {m(0):+5.2f}% D+3 {m(1):+5.2f}% D+7 {m(2):+5.2f}%")
