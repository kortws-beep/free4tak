"""
rebound_case.py — 반등 선취매 사례 뜯어보기 (2026-10-13)
================================================================
[이 파일이 하는 일 — 비개발자용 설명]

대장 전략(선취매, 5:5로 보고 50%만): "하락이 나올 때 주도섹터였고, 그 섹터 1·2·3등주이고,
하락폭이 5% 이내고, 반등할 때 급격히 꺾여 올라온다." 며칠 연속이냐는 그때그때 다름(3일·4일…),
코스피·코스닥도 갈림. → 규칙을 숫자로 정하기 전에 대장이 고른 사례(예: 엘앤에프 — 하락 첫날
+9%, 그 뒤 -5·-1·-7%)를 하나씩 숫자로 본다.
  1. 최근 N거래일 일봉: 등락, 시가→저가(오전 투매), 저가→종가(반등), 그날 시장(전 종목 중앙값)
  2. 하락 구간 직전 20일: 이 종목 수익률·순위, 속한 관심그룹과 그룹 안 순위(몇 등주였나)
  3. 하락 구간 동안 이 종목이 빠진 폭(하락 직전 종가 대비, 종가·저가 기준)
  4. 장중 모양(분봉, 최근 2거래일 또는 --date): 저점 시각·깊이, 저점 뒤 15·30·60분 반등폭,
     +2%·+3% 반등에 걸린 시간 — "급격히 꺾여 올라온다"를 숫자로
실행:  python backtest/rebound_case.py 엘앤에프 [--days 12] [--date 2026-10-13] [--no-api]
"""
import os
import sys
import sqlite3

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in ("backtest", "intelligence"):
    sys.path.insert(0, os.path.join(BASE, _d))
import swing_backtest as sb      # noqa: E402
import rebound_backtest as rb    # noqa: E402

LOOKBACK = 20


def _m(t: str) -> int:
    return int(t[:2]) * 60 + int(t[2:4])


def intraday_shape(bars: list) -> dict:
    """bars [(time HHMMSS, price, high, low)] 오래된→최신 → 저점·반등 속도."""
    if not bars:
        return {}
    o = bars[0][1]
    li = min(range(len(bars)), key=lambda i: bars[i][3])
    low, lt = bars[li][3], bars[li][0]
    after = bars[li:]
    out = {"open": o, "low": low, "low_time": lt, "o2l": (low / o - 1) * 100, "close": bars[-1][1],
           "l2c": (bars[-1][1] / low - 1) * 100, "high_after": max(b[2] for b in after)}
    for w in (15, 30, 60):
        hi = max((b[2] for b in after if _m(b[0]) - _m(lt) <= w), default=low)
        out[f"up{w}"] = (hi / low - 1) * 100
    for p in (2, 3):
        hit = next((b[0] for b in after if b[2] >= low * (1 + p / 100)), None)
        out[f"t{p}"] = _m(hit) - _m(lt) if hit else None
    return out


def decline_start(table: dict, dates: list, last: str) -> str:
    """last 이전(포함) 가장 최근 시장 하락 구간의 첫날 — 시장 하락일 = 전 종목 중앙값 < 0."""
    i = dates.index(last)
    while i >= 0 and table[dates[i]]["ret"] >= 0:
        i -= 1
    if i < 0:
        return last
    while i > 0 and table[dates[i - 1]]["ret"] < 0:
        i -= 1
    return dates[i]


def rank_in(cs: dict, names: list, start: str, end: str) -> list:
    """[(종목명, 수익률%)] 수익률 큰 순 — start→end 종가."""
    xs = [(n, (cs[n][end][0] / cs[n][start][0] - 1) * 100) for n in names
          if n in cs and start in cs[n] and end in cs[n] and cs[n][start][0]]
    return sorted(xs, key=lambda x: -x[1])


def report(name: str, daily: dict, groups: dict, days: int = 12, minutes: dict = None) -> str:
    if name not in daily:
        return f"'{name}' 일봉이 없어 — 이름을 리나 일봉 DB에 있는 그대로 넣어줘"
    table = rb.day_table(daily)
    dates = sorted(table)
    rows = [r for r in daily[name] if r[0] in table]
    L = [f"🔍 {name} 사례 — 최근 {min(days, len(rows) - 1)}거래일"]
    L.append("■ 1. 일봉          등락   시가→저가 저가→종가 | 시장(중앙값) 등락")
    for i in range(max(1, len(rows) - days), len(rows)):
        d, o, h, l, c, _ = rows[i]
        pc = rows[i - 1][4]
        L.append(f"   {d}  {(c / pc - 1) * 100:+6.2f}%  {(l / o - 1) * 100:+6.2f}%  {(c / l - 1) * 100:+6.2f}%"
                 f"  |  {table[d]['ret']:+.2f}%{'  ▼' if table[d]['ret'] < 0 else ''}")
    last = rows[-1][0]
    start = decline_start(table, dates, last)
    si = dates.index(start)
    L.append(f"■ 2. 시장 하락 구간: {start}부터 (▼ 연속). 그 직전 {LOOKBACK}거래일 주도 여부")
    cs = rb._closes(daily)
    if si - 1 - LOOKBACK >= 0:
        b0, b1 = dates[si - 1 - LOOKBACK], dates[si - 1]
        everyone = rank_in(cs, list(cs), b0, b1)
        pos = next((k for k, (n, _) in enumerate(everyone, 1) if n == name), None)
        mine = next((r for n, r in everyone if n == name), None)
        if mine is not None:
            L.append(f"   {name} {b0}→{b1} {mine:+.1f}% · 전 종목 {pos}/{len(everyone)}위")
        for g, members in groups.items():
            names = [n for _, n in members]
            if name not in names:
                continue
            rk = rank_in(cs, names, b0, b1)
            avg = sum(r for _, r in rk) / len(rk) if rk else 0
            me = next((k for k, (n, _) in enumerate(rk, 1) if n == name), None)
            top3 = ", ".join(f"{n} {r:+.0f}%" for n, r in rk[:3])
            L.append(f"   [{g}] 그룹 평균 {avg:+.1f}% · {name} 그룹 안 {me}/{len(rk)}등 · 1~3등: {top3}")
        L.append("■ 3. 하락 구간 동안 빠진 폭 (하락 직전 종가 대비)")
        base = cs[name].get(b1, (None,))[0]
        if base:
            for d, o, h, l, c, _ in daily[name]:
                if start <= d <= last:
                    L.append(f"   {d}  종가 {(c / base - 1) * 100:+6.2f}% · 저가 {(l / base - 1) * 100:+6.2f}%")
    else:
        L.append("   (일봉이 모자라 직전 20일을 못 봄)")
    for d, bars in sorted((minutes or {}).items()):
        s = intraday_shape(bars)
        if not s:
            continue
        f = lambda v: f"{v}분" if v is not None else "-"
        L.append(f"■ 4. 장중 {d}: 시가→저점 {s['o2l']:+.2f}% ({s['low_time'][:2]}:{s['low_time'][2:4]}) · "
                 f"저점→종가 {s['l2c']:+.2f}% · 저점→고점 {(s['high_after'] / s['low'] - 1) * 100:+.2f}%")
        L.append(f"      저점 뒤 반등: 15분 {s['up15']:+.2f}% · 30분 {s['up30']:+.2f}% · 60분 {s['up60']:+.2f}% · "
                 f"+2%까지 {f(s['t2'])} · +3%까지 {f(s['t3'])}")
    return "\n".join(L)


def main():
    a = sys.argv[1:]
    if not a:
        print(__doc__)
        return
    name = a[0]
    days = int(a[a.index("--days") + 1]) if "--days" in a else 12
    daily = sb.load_daily(rb.THEME_DB, days + LOOKBACK + 40)
    minutes = {}
    if "--no-api" not in a:
        import three_month_leader as tml
        conn = sqlite3.connect(rb.THEME_DB)
        try:
            code = tml._name_code_map(conn).get(name)
        finally:
            conn.close()
        if code:
            from dotenv import load_dotenv
            load_dotenv(os.path.join(BASE, ".env"))
            sys.path.insert(0, os.path.join(BASE, "core"))
            from kis_api import KisAPI
            import daybot_replay as dr
            store = dr.MinuteStore(KisAPI())
            want = [a[a.index("--date") + 1]] if "--date" in a else [r[0] for r in daily.get(name, [])[-2:]]
            import datetime
            if "--date" not in a and datetime.datetime.now().strftime("%H%M") >= "0900":
                want.append(datetime.date.today().isoformat())   # 오늘 장중(일봉 수집 전)도
            for d in dict.fromkeys(want):
                minutes[d] = store.day(code, d)
        else:
            print(f"⚠️ {name} 종목코드를 못 찾아 분봉은 건너뜀")
    print(report(name, daily, sb.load_groups(False, False), days, minutes))


if __name__ == "__main__":
    main()
