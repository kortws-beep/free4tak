"""
neolttwigi_validate.py — '널뛰기장' 규칙 검증: 급락일 매수 → 다음 날 대응 (2026-11-20, 대장)
================================================================
[이 파일이 하는 일 — 비개발자용 설명]
대장(11-20): "국면 35% 오르기 전까지는 널뛰기장 — 낙폭 큰 날(오후) 매수가 맞고, 반등장에선 이익 실현하고
들고 가지 않는 게 맞다. 급락 기준은 -2%부터, 외국인 매도 폭도 5천억 이하·5천억~1조·1조 이상으로 같이 대입해 봐."
→ 지난 기간에서
   · 널뛰기장 날: 그날 시장 국면(20일선 위 종목 비율, 전날 종가 기준 — 매수 시점에 알 수 있는 값) 35% 미만
   · 급락일: 코스피 등락 -2% 이하 (-2~-3 / -3~-4 / -4 이하로도 나눔)
   · 외국인(코스피 현물) 순매수: 매도 5천억 미만(또는 매수) / 5천억~1조 매도 / 1조 이상 매도
  그날 종가에 샀다고 보고 다음 날(D+1) 결과를 봄:
   ① 코스피 지수: 시가 · 장중 최고 · 종가, 이틀째(D+2) 종가(= 반등일에 안 팔고 들고 갔을 때)
   ② 관심종목 고무공형(3개월 플러스 & 1개월 고점 -15%↓, 리나 !고무공과 같은 조건) 평균: 같은 네 값
   · '반등일 당일 정리' 규칙 3가지 비교: (a) D+1 종가 정리 (b) D+1 시가가 매수가 밑이면 시가 정리, 아니면 종가
     (c) D+1 장중 +3% 닿으면 +3% 익절, 못 닿으면 종가 — 그리고 (d) D+2 종가까지 들고 감
비교군: 같은 급락일인데 국면 35% 이상이었던 날.
데이터: 지수 = 리나 로그 DB market_index(2025-07~), 외국인 = market_investor(2025-08-21~),
       종목 일봉 = kr_theme_finance.db(2024-11~), 국면 = core/market_regime.compute로 날짜별 재계산.
실행: venv/bin/python backtest/neolttwigi_validate.py
"""
import os
import sqlite3
import statistics
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(BASE, "core"), os.path.join(BASE, "intelligence")]
LOG_DB = os.path.join(BASE, "lina_bot", "three_month_leader_log.db")
THEME_DB = os.path.join(BASE, "lina_bot", "kr_theme_finance.db")
REGIME_CUT = 35.0
NOT_SECTOR = {"isee5", "끼", "v알림"}


def load():
    c = sqlite3.connect(f"file:{LOG_DB}?mode=ro", uri=True)
    idx = {d: (o, h, l, cl, ch) for d, o, h, l, cl, ch in
           c.execute("SELECT date, open, high, low, close, chg FROM market_index WHERE idx='KOSPI' ORDER BY date")}
    frg = dict(c.execute("SELECT date, foreign_ FROM market_investor WHERE market='KOSPI'"))
    c.close()
    return idx, frg


def regime_by_date(days):
    import market_regime
    res = market_regime.compute(THEME_DB, dates=days)
    return {d: v[0] for d, v in res.items()}


def stock_series(names):
    c = sqlite3.connect(f"file:{THEME_DB}?mode=ro", uri=True)
    out = {}
    for n, d, o, h, l, cl in c.execute("SELECT stock_name, date, open_price, high_price, low_price, close_price "
                                         "FROM kr_stock_daily_data WHERE date >= '2025-04-01' ORDER BY date"):
        if n in names and cl:
            out.setdefault(n, []).append((d, float(o or cl), float(h or cl), float(l or cl), float(cl)))
    c.close()
    return out


def watch_names():
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(BASE, ".env"))
        from kis_api import KisAPI
        import sector_watch as sw
        groups = sw.load_groups(KisAPI(), sw.hts_id())
        return {n for g, v in groups.items() if g.strip().lower() not in NOT_SECTOR for _, n in v}
    except Exception as e:
        print(f"⚠️ 관심그룹 못 불러옴({e}) — 종목 분석 생략")
        return set()


def exits(buy, d1, d2, tp=3.0):
    """buy=D 종가, d1/d2=(시가,고가,저가,종가). → dict of 수익률%."""
    o1, h1, l1, c1 = d1
    r = {"시가": (o1 / buy - 1) * 100, "최고": (h1 / buy - 1) * 100, "D+1종가": (c1 / buy - 1) * 100}
    r["(b)시가밑이면시가"] = r["시가"] if o1 < buy else r["D+1종가"]
    r["(c)+3%익절"] = tp if h1 >= buy * (1 + tp / 100) else r["D+1종가"]
    r["(d)D+2종가"] = (d2[3] / buy - 1) * 100 if d2 else None
    return r


def summarize(label, rows):
    if not rows:
        print(f"  {label}: 표본 없음")
        return
    keys = ["시가", "최고", "D+1종가", "(b)시가밑이면시가", "(c)+3%익절", "(d)D+2종가"]
    parts = []
    for k in keys:
        v = [x[k] for x in rows if x.get(k) is not None]
        if v:
            parts.append(f"{k} {statistics.mean(v):+.2f}%(+{sum(1 for x in v if x > 0) / len(v) * 100:.0f}%)")
    print(f"  {label} [{len(rows)}일] " + " · ".join(parts))


def main():
    idx, frg = load()
    days = sorted(idx)
    reg = regime_by_date(days)
    names = watch_names()
    ser = stock_series(names) if names else {}
    pos = {n: {d: i for i, (d, *_rest) in enumerate(s)} for n, s in ser.items()}

    def fbucket(v):
        if v is None:
            return "외국인 자료 없음"
        if v > -5000:
            return "외국인 매도 5천억 미만/매수"
        if v > -10000:
            return "외국인 매도 5천억~1조"
        return "외국인 매도 1조 이상"

    def fbucket2(v):                       # 1조 이상을 더 쪼갬(올해 시장 규모가 커서 1조↑가 대부분)
        if v is None or v > -10000:
            return None
        return "  ㄴ1조~3조" if v > -30000 else "  ㄴ3조 이상"

    def dbucket(ch):
        return "-2~-3%" if ch > -3 else ("-3~-4%" if ch > -4 else "-4% 이하")

    groups = {}
    for i in range(1, len(days) - 2):
        d, prev, n1, n2 = days[i], days[i - 1], days[i + 1], days[i + 2]
        o, h, l, cl, ch = idx[d]
        if ch is None or ch > -2 or not cl:
            continue
        r_prev = reg.get(prev)
        if r_prev is None:
            continue
        mode = "널뛰기장(국면<35%)" if r_prev < REGIME_CUT else "비교군(국면≥35%)"
        ix = exits(cl, idx[n1][:4], idx[n2][:4])
        # 고무공형 관심종목
        st = []
        for n, s in ser.items():
            p = pos[n].get(d)
            if p is None or p < 64 or p + 2 >= len(s):
                continue
            close_d = s[p][4]
            r3m = close_d / s[p - 63][4] - 1
            hi21 = max(x[2] for x in s[p - 20:p + 1])
            if r3m <= 0 or close_d / hi21 - 1 > -0.15:
                continue
            if s[p + 1][0] != n1:
                continue
            st.append(exits(close_d, s[p + 1][1:5], s[p + 2][1:5] if s[p + 2][0] == n2 else None))
        stock_avg = None
        if st:
            stock_avg = {k: statistics.mean([x[k] for x in st if x.get(k) is not None])
                         for k in st[0] if any(x.get(k) is not None for x in st)}
            stock_avg["_n"] = len(st)
        rec = (d, ch, r_prev, frg.get(d), ix, stock_avg)
        for key in (mode, (mode, dbucket(ch)), (mode, fbucket(frg.get(d))), (mode, fbucket2(frg.get(d)))):
            groups.setdefault(key, []).append(rec)

    print(f"기간 {days[0]}~{days[-1]} · 관심종목 {len(ser)}개 · 국면 계산 {len(reg)}일")
    for mode in ("널뛰기장(국면<35%)", "비교군(국면≥35%)"):
        recs = groups.get(mode, [])
        print(f"\n■ {mode} — 코스피 -2%↓ 급락일 {len(recs)}일")
        for d, ch, r, f, *_ in recs:
            print(f"    {d} 코스피 {ch:+.2f}% · 국면(전날) {r:.0f}% · 외국인 {f if f is not None else '-'}억")
        print(" [코스피 지수] 급락일 종가 매수 →")
        summarize("전체", [x[4] for x in recs])
        for b in ("-2~-3%", "-3~-4%", "-4% 이하"):
            summarize(b, [x[4] for x in groups.get((mode, b), [])])
        for b in ("외국인 매도 5천억 미만/매수", "외국인 매도 5천억~1조", "외국인 매도 1조 이상", "  ㄴ1조~3조", "  ㄴ3조 이상", "외국인 자료 없음"):
            summarize(b, [x[4] for x in groups.get((mode, b), [])])
        print(" [고무공형 관심종목 평균] 급락일 종가 매수 →")
        summarize("전체", [x[5] for x in recs if x[5]])
        for b in ("-2~-3%", "-3~-4%", "-4% 이하"):
            summarize(b, [x[5] for x in groups.get((mode, b), []) if x[5]])
        for b in ("외국인 매도 5천억 미만/매수", "외국인 매도 5천억~1조", "외국인 매도 1조 이상", "  ㄴ1조~3조", "  ㄴ3조 이상", "외국인 자료 없음"):
            summarize(b, [x[5] for x in groups.get((mode, b), []) if x[5]])
    print("\n(괄호 % = 플러스로 끝난 날 비율. 시가·최고는 참고값 — 최고가에 파는 건 불가능. "
          "(b)(c)(d)가 실제로 고를 수 있는 규칙. 고무공형 종목 수가 날마다 달라 그날 평균을 한 표본으로 셈.)")


if __name__ == "__main__":
    main()
