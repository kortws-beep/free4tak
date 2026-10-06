"""
theme_digest.py — 최근 자주 뜬 테마와 주도 종목 요약 (관심그룹 정리용, 2026-10-07)
================================================================
[이 파일이 하는 일 — 비개발자용 설명]

sector_monitor.py가 장중 1분마다 쌓는 "키움 상위 테마 10개 + 테마별 주요
종목" 기록(sector_monitor.db)을 최근 N일로 모아서 보여준다.
증권사가 분류한 테마 중 최근 자주 상위에 오른 테마와 그 안에서 실제로
돈이 몰린 종목을 뽑아, 대장이 한투 관심그룹을 세분화할 때 단초로 쓴다.
한투 관심그룹을 읽을 수 있으면 이미 그룹에 있는 종목은 ⭕, 없는 종목은 ➕.

실행:  python theme_digest.py        (최근 10거래일)
       python theme_digest.py 20     (최근 20거래일)
"""
import os
import sqlite3
import datetime

import three_month_leader as tml

_here   = os.path.dirname(os.path.abspath(__file__))
SECTOR_DB = os.path.join(_here, "sector_monitor.db")
TOP_STOCKS_SHOW = 5


def digest(days: int = 10, db_path: str = SECTOR_DB) -> tuple:
    """최근 days거래일(기록이 있는 날 기준) 테마 요약.
    반환: (rows, 날짜수, 시작일) — rows(자주 뜬 순): [{"theme","days","minutes","avg_flu","max_flu","last",
                       "stocks": [{"code","appear","avg_amt","avg_chg"}]}]"""
    conn = sqlite3.connect(db_path, timeout=10)
    conn.execute("PRAGMA query_only = ON")
    try:
        dates = [d for (d,) in conn.execute(
            "SELECT DISTINCT substr(ts,1,10) d FROM sector_flow ORDER BY d DESC LIMIT ?", (days,))]
        if not dates:
            return [], 0, None
        since = min(dates)
        themes = conn.execute("""
            SELECT theme_nm, COUNT(DISTINCT substr(ts,1,10)), COUNT(*), AVG(flu_rt), MAX(flu_rt),
                   MAX(substr(ts,1,10))
            FROM sector_flow WHERE substr(ts,1,10) >= ?
            GROUP BY theme_nm""", (since,)).fetchall()
        stocks = conn.execute("""
            SELECT theme_nm, code, COUNT(*), AVG(trde_amt), AVG(change_rate)
            FROM stock_momentum WHERE substr(ts,1,10) >= ?
            GROUP BY theme_nm, code""", (since,)).fetchall()
    finally:
        conn.close()
    by_theme: dict = {}
    for theme, code, n, amt, chg in stocks:
        by_theme.setdefault(theme, []).append(
            {"code": code, "appear": n, "avg_amt": amt or 0.0, "avg_chg": chg or 0.0})
    out = []
    for theme, d, m, avg, mx, last in themes:
        st = sorted(by_theme.get(theme, []), key=lambda s: (-s["appear"], -s["avg_amt"]))
        out.append({"theme": theme, "days": d, "minutes": m, "avg_flu": avg or 0.0,
                    "max_flu": mx or 0.0, "last": last, "stocks": st})
    out.sort(key=lambda t: (-t["days"], -t["minutes"]))
    return out, len(dates), since


def code_names() -> dict:
    """code → 종목명 (일봉 DB의 테마 종목 + 전 상장종목 마스터)."""
    conn = sqlite3.connect(tml.THEME_DB, timeout=10)
    try:
        return {c: n for n, c in tml._name_code_map(conn).items()}
    finally:
        conn.close()


def format_digest(rows: list, n_days: int, since: str, names: dict,
                  in_groups: set = None, top: int = 30) -> str:
    lines = [f"📚 최근 {n_days}거래일({since}~) 키움 상위 테마 요약 — 자주 뜬 순"]
    if in_groups is not None:
        lines.append("   ⭕ 이미 한투 관심그룹에 있음 / ➕ 아직 없음")
    for i, t in enumerate(rows[:top], 1):
        lines.append(f"{i:>2}. {t['theme']} — {t['days']}일·{t['minutes']}분 상위권 | "
                     f"평균 {t['avg_flu']:+.2f}% (최고 {t['max_flu']:+.2f}%) | 최근 {t['last']}")
        parts = []
        for s in t["stocks"][:TOP_STOCKS_SHOW]:
            mark = "" if in_groups is None else ("⭕" if s["code"] in in_groups else "➕")
            parts.append(f"{mark}{names.get(s['code'], s['code'])}({s['avg_amt']:,.0f}억)")
        if parts:
            lines.append("      " + ", ".join(parts))
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    n = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 10
    rows, n_days, since = digest(n)
    if not rows:
        print(f"기록 없음 — {SECTOR_DB}")
        sys.exit(0)
    in_groups = None
    try:   # 한투 관심그룹을 읽을 수 있으면 이미 있는 종목 표시
        sys.path.insert(0, os.path.join(tml._BASE, "core"))
        from dotenv import load_dotenv
        for _env in (os.path.join(tml._BASE, ".env"), os.path.join(tml._BASE, "lina_bot", ".env")):
            load_dotenv(_env)
        from kis_api import KisAPI
        import sector_watch
        g = sector_watch.load_groups(KisAPI(), sector_watch.hts_id())
        in_groups = {c for stocks in g.values() for c, _ in stocks} if g else None
    except Exception as e:
        print(f"(한투 관심그룹 비교 생략: {e})")
    print(format_digest(rows, n_days, since, code_names(), in_groups))
