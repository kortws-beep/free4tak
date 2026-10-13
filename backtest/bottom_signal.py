"""
bottom_signal.py — 시장 하락 마무리(추세전환) 신호 찾기 (2026-10-13)
================================================================
[이 파일이 하는 일 — 비개발자용 설명]

대장: "이 일의 핵심은 시장이 하락을 마무리하고, 반등까지는 아니어도 추세전환 신호를 잡는 것.
그 신호를 잡으면 경험상 거의 어떤 종목이든 기술적 반등·정상화로 5% 이상 오른다."
→ 리나 일봉 DB(전 종목)로 '시장 바닥 신호' 후보를 정의하고, 신호가 뜬 날 종가에 샀다면
   이후 5거래일 안에 +5%를 찍은 종목 비율이 평소보다 얼마나 높은지 본다.

신호 후보 (모두 그날 장 마감 때 알 수 있음 → 종가 매수 가정):
  A 망치형 바닥   : 2일+ 연속 하락 뒤, 그날 종목 중앙값 시가→저가 -2% 이하인데 저가→종가 +2% 이상
                   (장중 투매를 받아냄 — 엘앤에프 10-13 모양의 시장판)
  B 과매도        : 20일선 위 종목 비율 20% 이하
  C 과매도 첫 반등 : 20일선 위 비율 30% 이하였다가 그날 중앙값 등락 플러스
  D 신저가 정점   : 전날 20일 신저가 종목 비율 15% 이상 → 그날 절반 이하로 줄어듦(투매 소진)
  E 연속하락 끝 양봉: 3일+ 연속 하락 뒤 그날 중앙값 시가→종가 플러스
  비교: 평소(모든 날)
결과 (거래대금 상위 300종목, 비용 0.3% 차감):
  +5% 도달률(5일 안 고가 기준) · 3% 도달률 · 5일 뒤 종가 수익률 평균·PF · 시장(중앙값) 5일 수익률 플러스였던 날 비율
  그리고 신호 날짜 목록(그날 뒤 시장 5일 수익률) — 헛신호가 얼마나 되는지 눈으로
한계: 지수 아닌 전 종목 중앙값, 코스피·코스닥 구분 없음(다음 단계), 일봉 약 1년치.
실행:  python backtest/bottom_signal.py [--days 300] [--top 300]
"""
import os
import sys
import statistics

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "backtest"))
import swing_backtest as sb     # noqa: E402
import rebound_backtest as rb   # noqa: E402

COST = 0.003
HORIZON = 5


def new_low_share(daily: dict, n: int = 20) -> dict:
    """{날짜: 20일 신저가(저가가 직전 n일 최저 이하) 종목 비율 %}."""
    hit, tot = {}, {}
    for name, rows in daily.items():
        if rb.excluded(name):
            continue
        for i in range(n, len(rows)):
            d = rows[i][0]
            tot[d] = tot.get(d, 0) + 1
            if rows[i][3] <= min(r[3] for r in rows[i - n:i]):
                hit[d] = hit.get(d, 0) + 1
    return {d: hit.get(d, 0) / t * 100 for d, t in tot.items() if t >= 50}


def outcomes(daily: dict, top: int, horizon: int = HORIZON) -> dict:
    """{날짜: [(최대상승%, 5일뒤 수익률)]} — 그날 거래대금 상위 top 종목을 종가에 샀다면."""
    by_date = {}
    for name, rows in daily.items():
        if rb.excluded(name):
            continue
        for i in range(len(rows) - horizon):
            d, o, h, l, c, val = rows[i]
            if not c:
                continue
            nxt = rows[i + 1:i + 1 + horizon]
            by_date.setdefault(d, []).append((val, max(r[2] for r in nxt) / c - 1, nxt[-1][4] / c - 1 - COST))
    out = {}
    for d, xs in by_date.items():
        xs.sort(key=lambda x: -x[0])
        out[d] = [(mx, r) for _, mx, r in xs[:top]]
    return out


def signals(table: dict, st: dict, breadth: dict, lows: dict) -> dict:
    """{신호 이름: [날짜]}."""
    dates = sorted(table)
    sig = {k: [] for k in ("A 망치형 바닥", "B 과매도(20%↓)", "C 과매도 첫 반등", "D 신저가 정점", "E 연속하락 끝 양봉")}
    for i, d in enumerate(dates):
        t, b = table[d], breadth.get(d)
        if st[d] >= 2 and t["o2l"] <= -2.0 and t["l2c"] >= 2.0:
            sig["A 망치형 바닥"].append(d)
        if b is not None and b <= 20:
            sig["B 과매도(20%↓)"].append(d)
        if i and breadth.get(dates[i - 1]) is not None and breadth[dates[i - 1]] <= 30 and t["ret"] > 0:
            sig["C 과매도 첫 반등"].append(d)
        if i and lows.get(dates[i - 1], 0) >= 15 and lows.get(d, 100) <= lows[dates[i - 1]] / 2:
            sig["D 신저가 정점"].append(d)
        if st[d] >= 3 and t["o2c"] > 0:
            sig["E 연속하락 끝 양봉"].append(d)
    return sig


def summarize(days: list, outs: dict) -> dict:
    xs = [x for d in days for x in outs.get(d, [])]
    if not xs:
        return {"days": len(days), "n": 0}
    rets = [r for _, r in xs]
    w, l = [r for r in rets if r > 0], [r for r in rets if r <= 0]
    mkt = [statistics.median(r for _, r in outs[d]) for d in days if outs.get(d)]
    return {"days": len(days), "n": len(xs),
            "hit5": sum(mx >= 0.05 for mx, _ in xs) / len(xs) * 100,
            "hit3": sum(mx >= 0.03 for mx, _ in xs) / len(xs) * 100,
            "avg": sum(rets) / len(rets) * 100, "pf": sum(w) / -sum(l) if l and sum(l) < 0 else float("inf"),
            "mkt_up": sum(m > 0 for m in mkt) / len(mkt) * 100 if mkt else 0}


def fmt(s: dict) -> str:
    if not s["n"]:
        return f"{s['days']:>3}일  (결과 없음)"
    pf = f"{s['pf']:.2f}" if s["pf"] != float("inf") else "∞"
    return (f"{s['days']:>3}일 {s['n']:>6}건 · +5%도달 {s['hit5']:4.0f}% · +3%도달 {s['hit3']:4.0f}% · "
            f"5일뒤 평균 {s['avg']:+.2f}% PF {pf:>4} · 시장 5일 플러스 {s['mkt_up']:3.0f}%")


def report(daily: dict, top: int) -> str:
    table = rb.day_table(daily)
    st = rb.streaks(table)
    breadth = sb.market_breadth(daily)
    lows = new_low_share(daily)
    outs = outcomes(daily, top)
    dates = [d for d in sorted(table) if d in outs]
    L = [f"🔄 시장 바닥 신호 검증 — {dates[0]}~{dates[-1]} {len(dates)}거래일 · 신호 날 종가 매수 → "
         f"{HORIZON}거래일 (거래대금 상위 {top}종목, 비용 {COST:.1%})"]
    L.append(f"   평소(모든 날)        {fmt(summarize(dates, outs))}")
    sig = signals(table, st, breadth, lows)
    for k, ds in sig.items():
        L.append(f"   {k:<16} {fmt(summarize([d for d in ds if d in outs], outs))}")
    L.append("■ 신호 날짜 (그 뒤 시장 5일 중앙값 수익률 · 그날 20일선 위 비율 · 신저가 비율)")
    for k, ds in sig.items():
        if not ds:
            continue
        L.append(f"   [{k}]")
        for d in ds[-15:]:
            m = statistics.median(r for _, r in outs[d]) * 100 if outs.get(d) else None
            mtxt = f"{m:+.2f}%" if m is not None else "(아직)"
            L.append(f"     {d}  5일 뒤 {mtxt:>7} · 20일선 위 {breadth.get(d, 0):4.0f}% · 신저가 {lows.get(d, 0):4.1f}%")
    L.append("■ 최근 10거래일 시장 상태 (지금 어디쯤인지)")
    for d in sorted(table)[-10:]:
        t = table[d]
        on = [k.split()[0] for k, ds in sig.items() if d in ds]
        L.append(f"   {d} 등락 {t['ret']:+.2f}% · 시가→저가 {t['o2l']:+.2f}% · 저가→종가 {t['l2c']:+.2f}% · "
                 f"20일선 위 {breadth.get(d, 0):3.0f}% · 신저가 {lows.get(d, 0):4.1f}% · 연속하락 {st[d]}일"
                 + (f" · 신호 {','.join(on)}" if on else ""))
    return "\n".join(L)


if __name__ == "__main__":
    a = sys.argv[1:]
    days = int(a[a.index("--days") + 1]) if "--days" in a else 300
    top = int(a[a.index("--top") + 1]) if "--top" in a else 300
    print(report(sb.load_daily(rb.THEME_DB, days), top))
