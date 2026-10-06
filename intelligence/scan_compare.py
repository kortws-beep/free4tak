"""
scan_compare.py — 키움 조건검색 vs 파이썬판 검색식 하루 대조 (2026-10-07)
================================================================
[이 파일이 하는 일 — 비개발자용 설명]

데이봇이 4분마다 받은 키움 조건검색 원본(kiwoom_cond_log)과 리나의 파이썬판
검색식 기록(leader_obs / danta_obs / tml_obs)을 같은 날짜로 맞춰 본다.
"데이봇을 파이썬판으로 바꿔도 되나"(3번)를 대장이 자리에 없는 날에도
숫자로 판단하려는 용도.

공정하게 비교하려고 서로 "볼 수 있었던 시각"만 센다:
  · 키움에만 있는 종목 — 그 시각 ±5분 안에 파이썬 검사가 있었을 때만 셈
  · 파이썬에만 있는 종목 — 그 시각 ±5분 안에 키움 스캔이 있었을 때만 셈
    (데이봇이 매수를 멈춰 키움 스캔을 안 하는 시간대는 비교에서 빠짐)
키움에만 있는 종목은 파이썬이 그 무렵 왜 떨어뜨렸는지(근접 후보 사유)도 보여준다.

실행:  python scan_compare.py              (오늘)
       python scan_compare.py 2026-10-08   (그날)
       python scan_compare.py week         (최근 5거래일 요약)
"""
import os
import sqlite3
import datetime

import three_month_leader as tml

WINDOW_MIN = 5
# (키움 검색식명, 파이썬 기록 테이블, 보기 좋은 이름)
PAIRS = [
    ("주도주검색식3", "leader_obs", "주도주검색식3"),
    ("단타000", "danta_obs", "단타000"),
    ("3개월수급 당일주도주", "tml_obs", "3개월수급"),
]


def _mins(hhmm: str) -> int:
    return int(hhmm[:2]) * 60 + int(hhmm[3:5])


def _near(t: int, times: list) -> bool:
    return any(abs(t - x) <= WINDOW_MIN for x in times)


def compare_day(date: str, db_path: str = tml.LOG_DB) -> list:
    conn = sqlite3.connect(db_path, timeout=10)
    try:
        def q(sql, *a):
            try:
                return conn.execute(sql, a).fetchall()
            except sqlite3.OperationalError:
                return []
        k_scans = sorted({_mins(t) for (t,) in q(
            "SELECT time FROM kiwoom_cond_log WHERE date=? AND tag='__scan__'", date)})
        out = []
        for k_tag, table, label in PAIRS:
            k_hits: dict = {}
            for t, code, name in q("SELECT time, code, name FROM kiwoom_cond_log "
                                   "WHERE date=? AND tag=? ORDER BY time", date, k_tag):
                k_hits.setdefault(code, {"name": name, "times": []})["times"].append(_mins(t))
            p_scans = sorted({_mins(t) for (t,) in q(f"SELECT DISTINCT time FROM {table} WHERE date=?", date)})
            p_hits: dict = {}
            p_last_fail: dict = {}
            for t, code, name, passed, fails in q(
                    f"SELECT time, code, name, passed, fails FROM {table} WHERE date=? ORDER BY time", date):
                if passed:
                    p_hits.setdefault(code, {"name": name, "times": []})["times"].append(_mins(t))
                else:
                    p_last_fail.setdefault(code, []).append((_mins(t), fails))
            both = sorted(set(k_hits) & set(p_hits))
            k_only = [c for c in k_hits if c not in p_hits
                      and any(_near(t, p_scans) for t in k_hits[c]["times"])]
            p_only = [c for c in p_hits if c not in k_hits
                      and any(_near(t, k_scans) for t in p_hits[c]["times"])]

            def why(code):
                """키움에 걸린 시각에 가장 가까운 파이썬 탈락 사유."""
                ks = k_hits[code]["times"]
                cand = [(min(abs(t - k) for k in ks), f) for t, f in p_last_fail.get(code, [])]
                if not cand:
                    return "파이썬 기록 없음(앞 단계에서 빠졌거나 후보 풀 밖)"
                gap, fails = min(cand)
                return fails if gap <= WINDOW_MIN else f"{fails} (가장 가까운 기록 {gap}분 차)"
            denom = len(both) + len(k_only) + len(p_only)
            out.append({
                "label": label, "k_scans": len(k_scans), "p_scans": len(p_scans),
                "both": [(c, k_hits[c]["name"] or p_hits[c]["name"]) for c in both],
                "k_only": [(c, k_hits[c]["name"], why(c)) for c in k_only],
                "p_only": [(c, p_hits[c]["name"]) for c in p_only],
                "match_pct": (len(both) / denom * 100) if denom else None,
            })
        return out
    finally:
        conn.close()


def format_day(date: str, res: list) -> str:
    lines = [f"🔍 키움 vs 파이썬 검색식 대조 — {date}"]
    if res and res[0]["k_scans"] == 0:
        lines.append("   ⚠️ 이날 키움 스캔 기록이 없음(데이봇 미가동/키움 오류) — 비교 불가")
        return "\n".join(lines)
    for r in res:
        pct = f"{r['match_pct']:.0f}%" if r["match_pct"] is not None else "-"
        lines.append(f"■ {r['label']} — 일치율 {pct} | 둘 다 {len(r['both'])} · 키움만 {len(r['k_only'])} · "
                     f"파이썬만 {len(r['p_only'])}  (키움 스캔 {r['k_scans']}회 / 파이썬 {r['p_scans']}회)")
        if r["both"]:
            lines.append("   ✅ " + ", ".join(n or c for c, n in r["both"]))
        for c, n, why in r["k_only"]:
            lines.append(f"   🟦 키움만: {n or c}({c}) — 파이썬 판정: {why}")
        if r["p_only"]:
            lines.append("   🟧 파이썬만: " + ", ".join(f"{n or c}({c})" for c, n in r["p_only"]))
    return "\n".join(lines)


def recent_dates(n: int = 5, db_path: str = tml.LOG_DB) -> list:
    try:
        conn = sqlite3.connect(db_path, timeout=10)
        try:
            return [d for (d,) in conn.execute(
                "SELECT DISTINCT date FROM kiwoom_cond_log ORDER BY date DESC LIMIT ?", (n,))]
        finally:
            conn.close()
    except sqlite3.OperationalError:
        return []


if __name__ == "__main__":
    import sys
    arg = sys.argv[1] if len(sys.argv) > 1 else datetime.date.today().isoformat()
    if arg == "week":
        dates = sorted(recent_dates(5))
        if not dates:
            print("키움 조건검색 기록이 아직 없음 — 데이봇 재시작 후 장중부터 쌓임")
        for d in dates:
            print(format_day(d, compare_day(d)) + "\n")
    else:
        print(format_day(arg, compare_day(arg)))
