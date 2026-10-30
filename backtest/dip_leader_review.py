"""
dip_leader_review.py — 하락일 '유망주 급락' 음봉 매수 검증 (2026-10-30, 대장 아이디어)
================================================================
[이 파일이 하는 일 — 비개발자용 설명]
대장: "하락장에서는 종합주가 추이를 보면서 유망했던 종목이 많이 빠졌으면 -이더라도 하나 정도는 매수해서 기다려야 —
       오늘이 아니라 내일이라도 회복하면서 수익을 줌. 단타는 음봉 영역에서도 들어갈 자리가 보이면 들어가야."
10-30(코스피 -5.2%) 대장 픽: 티엘비·대덕전자(소캠/PCB 대장, 오늘 -9~-16%, 5일 고점 대비 -14~-17%)·현대건설(원전, 5일 고점 -11%).
규칙으로: 지수(코스피 또는 코스닥) DROP% 이상 빠진 날 D,
  '유망' = 직전 5거래일 수익률로 매긴 테마 순위(최고 1종목 빼고 평균, 5종목 이상) 상위 TOP_THEMES 소속,
  '급락' = D 등락 ≤ -DAY_DROP% 그리고 D 종가 ≤ 5일 고가 × (1 - FROM_HIGH%),
  유동성 = 20일 평균 거래대금 30억 이상.
  D 종가에 샀다 치고 → 다음 날 고가 +3%/+5% 닿음, 다음 날 저가 -5% 닿음, 다음 날 종가, 이틀째 종가.
  비교: 같은 조건으로 빠졌지만 유망 섹터가 아닌 종목. 앞 절반·뒤 절반.
일봉이라 다음 날 +3%와 -5% 중 어느 쪽이 먼저인지는 모름(둘 다 닿은 날은 따로 셈).
실행: venv/bin/python backtest/dip_leader_review.py [지수낙폭%=2]
"""
import os
import sys
import pickle
import sqlite3

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(BASE, "intelligence")]
DAILY_DB = os.path.join(BASE, "lina_bot", "kr_theme_finance.db")
TOP_THEMES, DAY_DROP, FROM_HIGH, MIN_VALUE_EOK = 5, 5.0, 10.0, 30


def theme_rank_5d(themes: dict, ret5: dict, top: int = TOP_THEMES) -> set:
    """직전 5일 수익률 {코드: %} → 상위 테마 이름(최고 1종목 빼고 평균, 5종목 이상)."""
    rows = []
    for th, ms in themes.items():
        xs = [ret5[c] for c, _ in ms if c in ret5]
        if len(xs) < 5:
            continue
        rows.append(((sum(xs) - max(xs)) / (len(xs) - 1), th))
    rows.sort(reverse=True)
    return {th for _, th in rows[:top]}


def outcome(buy: float, nxt: tuple, nxt2_close) -> dict:
    """nxt = (시, 고, 저, 종) 다음 날."""
    o, h, lo, c = nxt
    return {"hit3": h >= buy * 1.03, "hit5": h >= buy * 1.05, "stop5": lo <= buy * 0.95,
            "c1": (c / buy - 1) * 100, "c2": (nxt2_close / buy - 1) * 100 if nxt2_close else None,
            "gap": (o / buy - 1) * 100}


def load():
    import theme_open_obs as T
    themes = T.load_themes()
    name2code = {n: c for ms in themes.values() for c, n in ms}
    conn = sqlite3.connect(DAILY_DB)
    data = {}
    for n, d, o, h, lo, c, v in conn.execute("SELECT stock_name, date, open_price, high_price, low_price, close_price, "
                                             "trade_value FROM kr_stock_daily_data ORDER BY date"):
        code = name2code.get(n)
        if code and c and o and h and lo:
            data.setdefault(code, {})[d] = (o, h, lo, c, v or 0)
    conn.close()
    with open(os.path.join(BASE, "backtest", "data", "index_ks_kq.pkl"), "rb") as f:
        idx = pickle.load(f)
    return themes, data, idx


def run(drop: float = 2.0):
    themes, data, idx = load()
    names = {c: n for ms in themes.values() for c, n in ms}
    dates = sorted(set(idx["KS11"]) & set(idx["KQ11"]))
    res = []
    for i in range(6, len(dates) - 2):
        d, p = dates[i], dates[i - 1]
        ks = idx["KS11"][d] / idx["KS11"][p] - 1
        kq = idx["KQ11"][d] / idx["KQ11"][p] - 1
        if min(ks, kq) * 100 > -drop:
            continue
        d5, n1, n2 = dates[i - 6], dates[i + 1], dates[i + 2]
        ret5 = {c: (s[p][3] / s[d5][3] - 1) * 100 for c, s in data.items() if p in s and d5 in s}
        top = theme_rank_5d(themes, ret5)
        members = {c for th in top for c, _ in themes[th]}
        for c, s in data.items():
            if d not in s or p not in s or n1 not in s:
                continue
            o, h, lo, cl, v = s[d]
            chg = (cl / s[p][3] - 1) * 100
            prev5 = [s[x][1] for x in dates[i - 5:i] if x in s] + [h]
            hi5 = max(prev5)
            vals = [s[x][4] for x in dates[max(0, i - 20):i] if x in s and s[x][4]]
            if chg > -DAY_DROP or cl > hi5 * (1 - FROM_HIGH / 100) or not vals or sum(vals) / len(vals) / 1e8 < MIN_VALUE_EOK:
                continue
            oc = outcome(cl, s[n1][:4], s[n2][3] if n2 in s else None)
            res.append(dict(oc, date=d, code=c, name=names.get(c, c), promising=c in members, chg=chg))
    return res, dates


def summary(xs: list) -> str:
    n = len(xs)
    if not n:
        return "0건"
    c2 = [x["c2"] for x in xs if x["c2"] is not None]
    return (f"{n:>4}건 · 다음날 +3% 닿음 {sum(x['hit3'] for x in xs) / n * 100:3.0f}% · +5% {sum(x['hit5'] for x in xs) / n * 100:3.0f}% · "
            f"-5% 닿음 {sum(x['stop5'] for x in xs) / n * 100:3.0f}% · 시가 {sum(x['gap'] for x in xs) / n:+.1f}% · "
            f"다음날 종가 {sum(x['c1'] for x in xs) / n:+.2f}% · 이틀째 종가 {sum(c2) / len(c2) if c2 else 0:+.2f}%")


def main():
    drop = next((float(a) for a in sys.argv[1:] if a.replace(".", "").isdigit()), 2.0)
    res, _ = run(drop)
    days = sorted({x["date"] for x in res})
    if not days:
        print("해당 날 없음"); return
    mid = days[len(days) // 2]
    print(f"📉 지수(코스피·코스닥 중 하나) -{drop:g}% 이상 하락일 {len(days)}일({days[0]}~{days[-1]}) · 앞/뒤 경계 {mid}")
    print(f"   조건: 오늘 ≤ -{DAY_DROP:g}% & 5일 고점 대비 ≤ -{FROM_HIGH:g}% & 20일 평균 거래대금 {MIN_VALUE_EOK}억↑ · 종가 매수")
    for lab, f in (("유망(최근 5일 상위 5 섹터)", lambda x: x["promising"]), ("유망 아님", lambda x: not x["promising"])):
        ys = [x for x in res if f(x)]
        print(f"\n■ {lab}\n   전체 {summary(ys)}")
        print(f"   앞   {summary([x for x in ys if x['date'] < mid])}")
        print(f"   뒤   {summary([x for x in ys if x['date'] >= mid])}")
    picks = [x for x in res if x["promising"]]
    print("\n유망 종목 예(최근):", " · ".join(f"{x['date'][5:]} {x['name']}({x['chg']:+.0f}%→{x['c1']:+.1f}%)" for x in picks[-12:]))


if __name__ == "__main__":
    main()
