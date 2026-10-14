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
★ 10-13 대장 방식 그대로: "+5% 찍으면 팔고 나온다" — 5일 안 고가가 +5% 닿으면 그 값에 매도
  (시가가 이미 넘었으면 시가), 못 닿으면 5일째 종가. 손절 -5%를 같이 둔 규칙도(같은 날 둘 다
  닿으면 손절이 먼저라고 불리하게 가정). 평소에도 '5일 안 +5% 찍는 종목'이 절반이라 도달률만으론
  부족 — 실제로 챙기는 돈(PF)으로 비교.
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


def new_low_share(daily: dict, n: int = 20, top: int = None) -> dict:
    """{날짜: 20일 신저가(저가가 직전 n일 최저 이하) 종목 비율 %}.
    top: 전날 거래대금 상위 top 종목만(★ 10-14 리나 투매 알림이 장중 상위 400종목으로 계산 — 기준 맞춰보기용)."""
    per = {}
    for name, rows in daily.items():
        if rb.excluded(name):
            continue
        for i in range(n, len(rows)):
            hit = rows[i][3] <= min(r[3] for r in rows[i - n:i])
            per.setdefault(rows[i][0], []).append((rows[i - 1][5] or 0, hit))
    out = {}
    for d, xs in per.items():
        if top:
            xs = sorted(xs, key=lambda x: -x[0])[:top]
        if len(xs) >= 50:
            out[d] = sum(h for _, h in xs) / len(xs) * 100
    return out


def tp_exit(c: float, nxt: list, tp: float = 0.05, sl: float = None, close_sl: bool = False,
            next_open: bool = False) -> float:
    """종가 c에 산 뒤 nxt 일봉으로 +tp 익절(·-sl 손절) — 못 닿으면 마지막 종가. 비용 차감 전.
    close_sl=False: 장중 손절(같은 날 익절·손절 둘 다 닿으면 손절 먼저 — 불리하게)
    close_sl=True : 종가 손절(장중엔 익절만 보고, 종가가 손절선 아래면 그 종가에 매도)"""
    if next_open:
        return nxt[0][1] / c - 1
    for d, o, h, l, cl, _ in nxt:
        if sl is not None and not close_sl and l <= c * (1 - sl):
            return min(o / c - 1, -sl)
        if h >= c * (1 + tp):
            return max(o / c - 1, tp)
        if sl is not None and close_sl and cl <= c * (1 - sl):
            return cl / c - 1
    return nxt[-1][4] / c - 1


# ★ 10-13 대장: "맞으면 하락장 손실 회복, 틀려도 손절 3.5%" — 장중 -5% 손절을 붙이면 전부 PF<1로
#   떨어져서(바닥 근처는 흔들림이 커 반등 전에 털림) 손절 방식을 나눠 비교.
# ★ 10-13 대장: "반등매매는 오후에 사서 아침 프리장에 승부 — 수익 줄 때 먹고 빠진다"(다음 날 daybot
#   시드도 돌려줘야 함) → 다음 날 시가 매도(프리장 가격은 일봉에 없어 시가로 대신)를 맨 앞에.
EXIT_RULES = [("다음날시가", {"next_open": True}), ("익절만", {}),
              ("장중-3.5", {"sl": 0.035}), ("장중-5", {"sl": 0.05}), ("장중-7", {"sl": 0.07}),
              ("종가-3.5", {"sl": 0.035, "close_sl": True}), ("종가-5", {"sl": 0.05, "close_sl": True})]


def outcomes(daily: dict, top: int, horizon: int = HORIZON) -> dict:
    """{날짜: [(최대상승%, 5일뒤 수익률, *EXIT_RULES 수익률)]} — 그날 거래대금 상위 top 종목을 종가에 샀다면."""
    by_date = {}
    for name, rows in daily.items():
        if rb.excluded(name):
            continue
        for i in range(len(rows) - horizon):
            d, o, h, l, c, val = rows[i]
            if not c:
                continue
            nxt = rows[i + 1:i + 1 + horizon]
            by_date.setdefault(d, []).append((val, max(r[2] for r in nxt) / c - 1, nxt[-1][4] / c - 1 - COST,
                                              *[tp_exit(c, nxt, **kw) - COST for _, kw in EXIT_RULES]))
    out = {}
    for d, xs in by_date.items():
        xs.sort(key=lambda x: -x[0])
        out[d] = [x[1:] for x in xs[:top]]
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

    def ap(k):
        rets = [x[k] for x in xs]
        w, l = [r for r in rets if r > 0], [r for r in rets if r <= 0]
        return sum(rets) / len(rets) * 100, (sum(w) / -sum(l) if l and sum(l) < 0 else float("inf"))
    mkt = [statistics.median(x[1] for x in outs[d]) for d in days if outs.get(d)]
    avg, pf = ap(1)
    return {"days": len(days), "n": len(xs),
            "hit5": sum(x[0] >= 0.05 for x in xs) / len(xs) * 100,
            "hit3": sum(x[0] >= 0.03 for x in xs) / len(xs) * 100,
            "avg": avg, "pf": pf, "rules": [ap(2 + k) for k in range(len(EXIT_RULES))],
            "mkt_up": sum(m > 0 for m in mkt) / len(mkt) * 100 if mkt else 0}


def fmt(s: dict) -> str:
    if not s["n"]:
        return f"{s['days']:>3}일  (결과 없음)"
    f = lambda v: f"{v:.2f}" if v != float("inf") else "∞"
    return (f"{s['days']:>3}일 {s['n']:>6}건 · +5%도달 {s['hit5']:4.0f}% · +3%도달 {s['hit3']:4.0f}% · "
            f"5일뒤 평균 {s['avg']:+.2f}% PF {f(s['pf']):>4} · 시장 5일 플러스 {s['mkt_up']:3.0f}%\n"
            f"{'':<24}→ " + " · ".join(f"{lab} {a:+.2f}%/{f(p)}" for (lab, _), (a, p)
                                                  in zip(EXIT_RULES, s["rules"])))


def report(daily: dict, top: int) -> str:
    table = rb.day_table(daily)
    st = rb.streaks(table)
    breadth = sb.market_breadth(daily)
    lows = new_low_share(daily)
    outs = outcomes(daily, top)
    dates = [d for d in sorted(table) if d in outs]
    L = [f"🔄 시장 바닥 신호 검증 — {dates[0]}~{dates[-1]} {len(dates)}거래일 · 신호 날 종가 매수 → "
         f"{HORIZON}거래일 (거래대금 상위 {top}종목, 비용 {COST:.1%})"]
    L.append("   (→ 줄: 매도 규칙별 평균%/PF — 다음날시가 = 오후 매수·아침 매도, 나머지는 5일 안 +5% 익절 +손절 방식 · 장중 손절은 같은 날 익절·손절 둘 다 닿으면 손절 먼저로 불리하게 계산)")
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
            m = statistics.median(x[1] for x in outs[d]) * 100 if outs.get(d) else None
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
