"""
rebound_peak_time.py — 반등날(다음 날) 고점이 보통 언제 찍히나 (2026-10-16)
================================================================
[이 파일이 하는 일 — 비개발자용 설명]

SCENARIOS.md 시나리오1 다듬기 ②: "반등 고점 시각은 날마다 다름(10-14는 아침, 10-15 서킷
다음 날은 14시) → 분봉으로 고점 시각 분포 확인 필요". 과거 투매일(capitulation_sector.py와
동일 기준)마다 1등 그룹 대표주의 반등날(다음 날) 분봉을 받아 그날 고가가 찍힌 시각을 모아
분포를 본다 — "09:45 전이 많다"는 감이 맞는지 숫자로.

실행:  python backtest/rebound_peak_time.py [--nl 40] [--days 300]
"""
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in ("backtest", "intelligence", "core"):
    sys.path.insert(0, os.path.join(BASE, _d))
import swing_backtest as sb       # noqa: E402
import rebound_backtest as rb     # noqa: E402
import rebound_case as rc         # noqa: E402
import capitulation_sector as cs  # noqa: E402

BUCKETS = [("09:45 전", "0900", "0945"), ("09:45~10:30", "0945", "1030"), ("10:30~12:00", "1030", "1200"),
           ("12:00~13:30", "1200", "1330"), ("13:30~15:20", "1330", "1520")]


def _bucket(hhmm: str) -> str:
    for label, lo, hi in BUCKETS:
        if lo <= hhmm < hi:
            return label
    return "13:30~15:20"


def high_time(bars: list) -> str:
    """[(time, price, high, low)] → 그날 고가(high열 최대치)가 찍힌 시각 HHMM."""
    if not bars:
        return ""
    t = max(bars, key=lambda b: b[2])[0]
    return t[:4]


def collect(nl: float = 40.0, days: int = 300, top: int = 3) -> list:
    """[(바닥날, 반등날, 종목명, 코드, 고점시각, 그룹)] — 1등 그룹 대표주 상위 top개."""
    daily = sb.load_daily(rb.THEME_DB, days)
    groups = sb.load_groups(False, False)
    table = rb.day_table(daily)
    dates = sorted(table)
    lows = __import__("bottom_signal").new_low_share(daily)
    days_list = cs.cap_days(table, lows, nl)
    rows = {n: {r[0]: r[1:6] for r in rs} for n, rs in daily.items() if not rb.excluded(n)}
    import three_month_leader as tml
    import sqlite3
    conn = sqlite3.connect(tml.THEME_DB)
    try:
        name_code = tml._name_code_map(conn)
    finally:
        conn.close()
    from dotenv import load_dotenv
    load_dotenv(os.path.join(BASE, ".env"))
    from kis_api import KisAPI
    import daybot_replay as dr
    store = dr.MinuteStore(KisAPI())
    out = []
    for Bd in days_list:
        R = dates[dates.index(Bd) + 1]
        S = rc.decline_start(table, dates, Bd)
        si = dates.index(S)
        if si == 0:
            continue
        P = dates[si - 1]
        sc = cs.score_groups(groups, rows, P, Bd, R)
        if not sc:
            continue
        best = sc[0]
        for name, _r in best["members"][:top]:
            code = name_code.get(name)
            if not code:
                continue
            bars = store.day(code, R)
            t = high_time(bars)
            if t:
                out.append((Bd, R, name, code, t, best["group"]))
    return out


def report(rows: list) -> str:
    if not rows:
        return "데이터 없음"
    from collections import Counter
    cnt = Counter(_bucket(t) for *_, t, _ in rows)
    L = [f"🕐 반등날(다음 날) 고점 시각 분포 — {len(rows)}건(투매일별 1등 그룹 대표주)"]
    for label, *_ in BUCKETS:
        n = cnt.get(label, 0)
        L.append(f"   {label:<12} {n:>3}건 ({n / len(rows) * 100:4.0f}%) {'█' * n}")
    L.append("■ 상세")
    for Bd, R, name, code, t, g in rows:
        L.append(f"   {Bd}→{R} [{g}] {name} 고점 {t[:2]}:{t[2:]}")
    return "\n".join(L)


if __name__ == "__main__":
    a = sys.argv[1:]
    nl = float(a[a.index("--nl") + 1]) if "--nl" in a else 40.0
    days = int(a[a.index("--days") + 1]) if "--days" in a else 300
    print(report(collect(nl, days)))
