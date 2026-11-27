"""
event_noise_check.py — 이벤트 '미리 사기'가 더 큰 시장 소음(금리·젠슨황 같은 쇼크)에 묻히나 (2026-11-28 대장)
================================================================
대장: "이벤트는 있지만 더 큰 이벤트(젠슨황 이슈·금리 인상 등)에 묻혀버리는 경향 — 그래서 정교한 셋팅이 필요. 나도 너도 모르니 데이터로."
검증 통과·후보 짝 8개(CES 반도체·IT·로봇, 지스타 게임, 광군절 아모레, JPM 헬스케어, 실적시즌·GTC 반도체)를
  ① 들고 있는 동안 시장이 잔잔/요동/하락(코스피200 -3%↓)했던 해로 나눠 비교
  ② 사기 직전에 알 수 있는 상태(직전 20일 변동폭·20일선 위아래·급락 뒤)로 나눠 비교
  ③ '시장 손절'(산 날보다 코스피200이 N% 빠지면 정리) 시뮬레이션
실행: venv/bin/python backtest/event_noise_check.py
"""
import sys, datetime, statistics as st, bisect
import os; sys.path[:0]=[os.path.dirname(os.path.abspath(__file__)), os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "intelligence")]
import event_sector_validate as V, event_calendar as E
data=V.load(); bench=dict(data[V.BENCH]); bd=sorted(bench); today=datetime.date.today()
code_of={v:k for k,v in V.SECTORS.items()}
def occ(code,dates,b,s,ev):
    ser=data.get(code) or []; ds=[d for d,_ in ser]; px=dict(ser); out=[]
    for d in dates:
        i=bisect.bisect_left(ds,d.isoformat())
        if ev=="연말배당": i-=1
        if i-b<0 or i>=len(ds): continue
        a,z=ds[i-b],ds[i-s]
        if a not in bench or z not in bench: continue
        ex=(px[z]/px[a]-1)*100-(bench[z]/bench[a]-1)*100
        ja,jz=bisect.bisect_left(bd,a),bisect.bisect_left(bd,z)
        rets=[abs(bench[bd[j]]/bench[bd[j-1]]-1)*100 for j in range(ja+1,jz+1)]
        mkt=(bench[z]/bench[a]-1)*100
        out.append(dict(year=d.year,ex=ex,vol=st.mean(rets),mkt=mkt))
    return out
SET=[("CES","반도체",20,3),("CES","IT",10,1),("지스타","게임",10,1),("광군절","아모레퍼시픽",20,3),("JPM헬스케어","헬스케어",20,3),("실적시즌","반도체",15,3),("GTC","반도체",20,3),("CES","로봇(22~)",20,3)]
evs=E.events(2015,today.year)
allv=[r["vol"] for ev,sec,b,s in SET for r in occ(code_of[sec],[d for d in evs[ev] if d<today-datetime.timedelta(days=10)],b,s,ev)]
cut=st.median(allv)
print(f"요동 기준 = 보유기간 코스피200 하루 평균 변동폭 중앙값 {cut:.2f}%")
pool={"잔잔":[], "요동":[], "하락장":[], "상승장":[]}
for ev,sec,b,s in SET:
    rs=occ(code_of[sec],[d for d in evs[ev] if d<today-datetime.timedelta(days=10)],b,s,ev)
    calm=[r for r in rs if r["vol"]<=cut]; wild=[r for r in rs if r["vol"]>cut]
    f=lambda xs: f"{st.mean(x['ex'] for x in xs):+6.2f}%p 이긴해 {sum(x['ex']>0 for x in xs)}/{len(xs)}" if xs else "   -"
    print(f"{ev:8s} {sec:10s} {b}→{s} | 잔잔 {f(calm)} | 요동 {f(wild)} | 요동한 해 {','.join(str(r['year'])[2:] for r in wild)}")
    pool["잔잔"]+=calm; pool["요동"]+=wild
    pool["하락장"]+=[r for r in rs if r["mkt"]<-3]; pool["상승장"]+=[r for r in rs if r["mkt"]>=-3]
print("--- 8개 짝 합산 ---")
for k,xs in pool.items():
    print(f"{k:4s} n={len(xs):3d} 평균 {st.mean(x['ex'] for x in xs):+.2f}%p 이긴 비율 {sum(x['ex']>0 for x in xs)/len(xs)*100:.0f}%")
print("\n=== 사기 직전에 알 수 있는 것으로 나누기 ===")
def pre_state(code,dates,b,s,ev):
    ser=data.get(code) or []; ds=[d for d,_ in ser]; px=dict(ser); out=[]
    for d in dates:
        i=bisect.bisect_left(ds,d.isoformat())
        if ev=="연말배당": i-=1
        if i-b<21 or i>=len(ds): continue
        a,z=ds[i-b],ds[i-s]
        if a not in bench or z not in bench: continue
        ex=(px[z]/px[a]-1)*100-(bench[z]/bench[a]-1)*100
        ja=bisect.bisect_left(bd,a)
        prev=[abs(bench[bd[j]]/bench[bd[j-1]]-1)*100 for j in range(ja-19,ja+1)]
        ma20=st.mean(bench[bd[j]] for j in range(ja-19,ja+1))
        out.append(dict(ex=ex,pvol=st.mean(prev),above=bench[a]>ma20,ret20=(bench[a]/bench[bd[ja-20]]-1)*100))
    return out
rs=[r for ev,sec,b,s in SET for r in pre_state(code_of[sec],[d for d in evs[ev] if d<today-datetime.timedelta(days=10)],b,s,ev)]
cut2=st.median(r["pvol"] for r in rs)
for lbl,xs in (("직전 20일 잔잔",[r for r in rs if r["pvol"]<=cut2]),("직전 20일 요동",[r for r in rs if r["pvol"]>cut2]),
               ("코스피200 20일선 위",[r for r in rs if r["above"]]),("코스피200 20일선 아래",[r for r in rs if not r["above"]]),
               ("직전 20일 -5%↓ 급락 뒤",[r for r in rs if r["ret20"]<=-5]),("나머지",[r for r in rs if r["ret20"]>-5])):
    print(f"{lbl:20s} n={len(xs):3d} 평균 {st.mean(x['ex'] for x in xs):+.2f}%p 이긴 비율 {sum(x['ex']>0 for x in xs)/len(xs)*100:.0f}%")
print("\n=== 들고 있는 동안 '시장 손절' 규칙 시뮬레이션 (섹터 절대수익 기준) ===")
def sim(code,dates,b,s,ev,mstop):
    ser=data.get(code) or []; ds=[d for d,_ in ser]; px=dict(ser); out=[]
    for d in dates:
        i=bisect.bisect_left(ds,d.isoformat())
        if ev=="연말배당": i-=1
        if i-b<0 or i>=len(ds): continue
        ia,iz=i-b,i-s
        a=ds[ia]
        if a not in bench: continue
        res=None
        for j in range(ia+1,iz+1):
            dj=ds[j]
            if dj not in bench: continue
            if mstop is not None and (bench[dj]/bench[a]-1)*100<=mstop:
                res=(px[dj]/px[a]-1)*100; break
        if res is None: res=(px[ds[iz]]/px[a]-1)*100
        out.append(res)
    return out
for mstop in (None,-2,-3,-4):
    v=[x for ev,sec,b,s in SET for x in sim(code_of[sec],[d for d in evs[ev] if d<today-datetime.timedelta(days=10)],b,s,ev,mstop)]
    print(f"시장 손절 {str(mstop)+'%' if mstop else '없음':6s} n={len(v)} 평균 {st.mean(v):+.2f}% 이긴 비율 {sum(x>0 for x in v)/len(v)*100:.0f}% 최악 {min(v):+.1f}%")
