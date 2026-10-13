"""
rebound_backtest.py — 연속 하락 뒤 "아침 투매 → 오후 반등" 검증 (2026-10-13)
================================================================
[이 파일이 하는 일 — 비개발자용 설명]

대장 가설: "시장이 5일 연속 빠지면 미수·반대매매 만기가 몰려 다음 날 오전에 크게 밀렸다가
오후에 반등할 가능성이 높다(양전까진 아니어도). 5일 내내 손절당했으니 그 반등에 올라타자."
봇에 넣기 전에 리나 일봉 DB(전 종목 시·고·저·종가)로 과거에 정말 그랬는지 본다.

1. 시장 하루 모양 — 하락 연속일수(전날까지)별로 그날 전 종목 중앙값:
   시가→저가(오전 투매 크기), 저가→종가(반등 크기), 시가→종가, 전일→종가
   ※ 하락일 = 그날 전 종목 등락률 중앙값 < 0 (지수 없이 일봉 DB로 계산)
2. 매매 흉내 — 그날 거래대금 상위(전날 기준) 종목을 "시가 대비 -d%" 지정가로 매수
   (저가가 거기까지 왔으면 체결, 장중 순서 몰라도 됨) → 당일 종가 / 다음 날 시가 / 다음 날 종가 매도
   연속 하락 N일 뒤 vs 평소(모든 날) 비교. 비용 0.3%(수수료·세금·슬리피지) 차감
3. 과거 5일+ 연속 하락 다음 날 목록 — 표본이 몇 번 안 되니 하나하나 눈으로 확인
4. ★ 대장 보완(같은 날): "삼성·하이닉스는 반등 못 했는데 하락 전 주도섹터였던 2차전지는 오전 51만
   → 오후 56만. 하락 전 주도주가 매크로로 같이 빠졌을 때 된다." → 같은 날 안에서
   하락 전 주도주 vs 나머지 비교(시장 전체가 튄 날 효과가 빠짐).
   · 주도주 = 연속 하락 시작 직전 20거래일 수익률 상위 30종목(그때 거래대금 상위 300 안)
   · 주도그룹 = 같은 기간 관심그룹 평균 수익률 상위 3그룹 종목(★ 관심그룹은 지금 구성이라
     뒤늦은 선택 편향 있음 — 주도주 쪽이 더 정직한 숫자)
한계: 일봉 DB가 약 1년치라 "5일 연속"은 몇 번 없음 → 3·4일도 같이 봄. 미수 만기 데이터는 없음.
실행:  python backtest/rebound_backtest.py [--days 250] [--top 200]
"""
import os
import sys
import statistics

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "backtest"))
import swing_backtest as sb  # noqa: E402   (일봉 읽기·성적 집계 재사용)

THEME_DB = os.path.join(BASE, "lina_bot", "kr_theme_finance.db")
COST = 0.003
DIPS = (2.0, 3.0, 4.0, 5.0)
STREAKS = (3, 4, 5)
EXCLUDE_WORDS = ("KODEX", "TIGER", "KBSTAR", "ARIRANG", "HANARO", "KOSEF", "ACE ", "SOL ", "RISE ",
                 "PLUS ", "ETN", "스팩", "레버리지", "인버스")


def excluded(name: str) -> bool:
    return any(w in name for w in EXCLUDE_WORDS) or name.endswith("우") or name.endswith("우B")


def day_table(daily: dict) -> dict:
    """{날짜: {"ret","o2l","l2c","o2c"} 중앙값(%) + "n"} — 전 종목 기준."""
    per = {}
    for name, rows in daily.items():
        if excluded(name):
            continue
        for i in range(1, len(rows)):
            d, o, h, l, c, _ = rows[i]
            pc = rows[i - 1][4]
            if not (pc and o and l):
                continue
            per.setdefault(d, []).append(((c / pc - 1) * 100, (l / o - 1) * 100, (c / l - 1) * 100,
                                          (c / o - 1) * 100))
    out = {}
    for d, xs in per.items():
        if len(xs) < 50:
            continue
        out[d] = {k: statistics.median(x[j] for x in xs) for j, k in enumerate(("ret", "o2l", "l2c", "o2c"))}
        out[d]["n"] = len(xs)
    return out


def streaks(table: dict) -> dict:
    """{날짜: 전날까지 연속 하락일수}."""
    out, run = {}, 0
    for d in sorted(table):
        out[d] = run
        run = run + 1 if table[d]["ret"] < 0 else 0
    return out


def trades(daily: dict, dip: float, top: int, days: set, only: dict = None, skip: dict = None) -> list:
    """days에 속한 날, 전날 거래대금 상위 top 종목을 시가-dip% 지정가로 매수 → 세 가지 매도.
    only/skip: {날짜: 종목명 set} — 그날 이 종목만(거래대금 순위 무시) / 이 종목은 빼고."""
    by_date = {}
    for name, rows in daily.items():
        if excluded(name):
            continue
        for i in range(1, len(rows)):
            d = rows[i][0]
            if d not in days:
                continue
            if only is not None and name not in only.get(d, ()):
                continue
            if skip and name in skip.get(d, ()):
                continue
            by_date.setdefault(d, []).append((rows[i - 1][5], name, i))
    out = []
    for d, xs in by_date.items():
        xs.sort(key=lambda x: -x[0])
        for _, name, i in (xs if only is not None else xs[:top]):
            rows = daily[name]
            _, o, h, l, c, _ = rows[i]
            px = o * (1 - dip / 100)
            if l > px:
                continue                       # 거기까지 안 빠짐 — 미체결
            r = {"date": d, "name": name, "days": 0, "ret": c / px - 1 - COST, "nxt_o": None, "nxt_c": None}
            if i + 1 < len(rows):
                r["nxt_o"] = rows[i + 1][1] / px - 1 - COST
                r["nxt_c"] = rows[i + 1][4] / px - 1 - COST
            out.append(r)
    return out


_CLOSES = {}


def _closes(daily: dict) -> dict:
    """{종목명: {날짜: (종가, 거래대금)}} — 같은 daily면 한 번만 만듦."""
    if _CLOSES.get("id") is not daily:
        _CLOSES["id"] = daily
        _CLOSES["v"] = {name: {r[0]: (r[4], r[5]) for r in rows}
                        for name, rows in daily.items() if not excluded(name)}
    return _CLOSES["v"]


def leaders(daily: dict, dates: list, end: str, lookback: int = 20, top: int = 30, liquid: int = 300) -> set:
    """end일 기준 직전 lookback거래일 수익률 상위 top 종목(그날 거래대금 상위 liquid 안)."""
    i = dates.index(end)
    if i < lookback:
        return set()
    start = dates[i - lookback]
    cs = _closes(daily)
    xs = [(v[end][1], n, v[end][0] / v[start][0] - 1) for n, v in cs.items()
          if end in v and start in v and v[start][0]]
    xs.sort(key=lambda x: -x[0])
    return {n for _, n, _ in sorted(xs[:liquid], key=lambda x: -x[2])[:top]}


def leader_groups(daily: dict, groups: dict, dates: list, end: str, lookback: int = 20, top: int = 3) -> tuple:
    """(상위 그룹 이름들, 그 종목명 set) — 관심그룹 평균 수익률 상위 top."""
    i = dates.index(end)
    if i < lookback or not groups:
        return [], set()
    start = dates[i - lookback]
    cs = _closes(daily)
    scored = []
    for g, members in groups.items():
        rs = [cs[n][end][0] / cs[n][start][0] - 1 for _, n in members
              if n in cs and end in cs[n] and start in cs[n] and cs[n][start][0]]
        if len(rs) >= 3:
            scored.append((sum(rs) / len(rs), g))
    scored.sort(reverse=True)
    names = {n for _, g in scored[:top] for _, n in groups[g]}
    return [g for _, g in scored[:top]], names


def leader_section(daily: dict, table: dict, st: dict, groups: dict, top: int) -> list:
    dates = sorted(table)
    L = ["■ 4. 같은 날 비교 — 하락 전 주도주 vs 나머지 (연속 하락 시작 직전 20거래일 기준)"]
    for n in STREAKS:
        days = [d for d in dates if st[d] >= n]
        lead, lgrp, lg_names = {}, {}, {}
        for d in days:
            end = dates[dates.index(d) - st[d] - 1] if dates.index(d) - st[d] - 1 >= 0 else None
            if end:
                lead[d] = leaders(daily, dates, end)
                lgrp[d], lg_names[d] = leader_groups(daily, groups, dates, end)
        ds = set(lead)
        L.append(f"   [{n}일+ 연속 하락 다음 날 — {len(ds)}일]")
        for dip in (0.0, 3.0, 5.0):
            tag = "시가 매수" if not dip else f"-{dip:.0f}% 매수"
            a = trades(daily, dip, top, ds, only=lead)
            b = trades(daily, dip, top, ds, skip=lead)
            L.append(f"     {tag:<7} 주도주    → 당일종가 {_stats(a, 'ret')}")
            if groups:
                g = trades(daily, dip, top, ds, only=lg_names)
                L.append(f"     {'':<7} 주도그룹  → 당일종가 {_stats(g, 'ret')}")
            L.append(f"     {'':<7} 나머지    → 당일종가 {_stats(b, 'ret')}")
        if n == 5:
            L.append("     날짜별(시가 매수→당일종가, 평균) — 주도주 / 나머지")
            for d in sorted(ds):
                a = [r["ret"] for r in trades(daily, 0.0, top, {d}, only=lead)]
                b = [r["ret"] for r in trades(daily, 0.0, top, {d}, skip=lead)]
                f = lambda xs: f"{sum(xs) / len(xs) * 100:+.2f}%({len(xs)})" if xs else "-"
                gs = f" · 주도그룹 {', '.join(lgrp.get(d, []))}" if lgrp.get(d) else ""
                L.append(f"       {d}  {f(a)} / {f(b)}{gs}")
    return L


def _stats(rs: list, key: str) -> str:
    xs = [dict(r, ret=r[key], days=0) for r in rs if r[key] is not None]
    return sb.fmt(sb.stats(xs)).split(" 보유")[0] if xs else "     0건"


def report(daily: dict, top: int, groups: dict = None) -> str:
    table = day_table(daily)
    st = streaks(table)
    L = [f"📉 연속 하락 뒤 반등 검증 — {min(table)}~{max(table)} {len(table)}거래일, 종목 중앙값 기준"]
    L.append("■ 1. 하락 연속일수(전날까지)별 그날 시장 모양 (중앙값 %의 평균)")
    L.append("      연속    일수  시가→저가  저가→종가  시가→종가  전일→종가  반등일(종가>시가)")
    for k, label in ((0, "0일"), (1, "1일"), (2, "2일"), (3, "3일"), (4, "4일"), (5, "5일+")):
        ds = [d for d in table if (st[d] >= 5 if k == 5 else st[d] == k)]
        if not ds:
            continue
        avg = lambda f: sum(table[d][f] for d in ds) / len(ds)
        up = sum(table[d]["o2c"] > 0 for d in ds)
        L.append(f"      {label:<5} {len(ds):>5}   {avg('o2l'):+6.2f}    {avg('l2c'):+6.2f}    "
                 f"{avg('o2c'):+6.2f}    {avg('ret'):+6.2f}    {up}/{len(ds)}")
    L.append(f"■ 2. 매매 흉내 — 전날 거래대금 상위 {top}종목, 시가 대비 -d% 지정가 매수 (비용 {COST:.1%} 차감)")
    alld = set(table)
    for n in (0,) + STREAKS:
        ds = alld if n == 0 else {d for d in table if st[d] >= n}
        L.append(f"   [{'평소(모든 날)' if n == 0 else f'{n}일+ 연속 하락 다음 날'} — {len(ds)}일]")
        for dip in DIPS:
            rs = trades(daily, dip, top, ds)
            L.append(f"     -{dip:.0f}% 매수 → 당일종가 {_stats(rs, 'ret')}")
            L.append(f"               → 다음날시가 {_stats(rs, 'nxt_o')}")
            L.append(f"               → 다음날종가 {_stats(rs, 'nxt_c')}")
    big = [d for d in sorted(table) if st[d] >= 5]
    L.append(f"■ 3. 5일+ 연속 하락 다음 날 {len(big)}번 (종목 중앙값)")
    for d in big:
        t = table[d]
        L.append(f"   {d} (연속 {st[d]}일 뒤) 시가→저가 {t['o2l']:+.2f}% · 저가→종가 {t['l2c']:+.2f}% · "
                 f"시가→종가 {t['o2c']:+.2f}% · 전일→종가 {t['ret']:+.2f}%")
    if len(big) < 5:
        L.append("   ※ 5번 미만 — 이것만으로 봇 규칙을 정하기엔 표본이 너무 적음, 3·4일 결과와 같이 볼 것")
    L += leader_section(daily, table, st, groups or {}, top)
    return "\n".join(L)


if __name__ == "__main__":
    a = sys.argv[1:]
    days = int(a[a.index("--days") + 1]) if "--days" in a else 250
    top = int(a[a.index("--top") + 1]) if "--top" in a else 200
    print(report(sb.load_daily(THEME_DB, days + 30), top, sb.load_groups(False, False)))
