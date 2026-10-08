"""키움 vs 파이썬 대조 — 둘 다/키움만/파이썬만, 서로 볼 수 있던 시각만 비교, 탈락 사유."""
import os
import sqlite3
import unittest

from _helpers import use_temp_cwd

_tmp = use_temp_cwd()
import scan_compare as S  # noqa: E402
import daybot_db           # noqa: E402

D = "2026-10-07"


def build():
    db = os.path.abspath("log.db")
    if os.path.exists(db):
        os.remove(db)
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE leader_obs (date TEXT, time TEXT, code TEXT, name TEXT, rank INT, price REAL, "
              "chg REAL, high_pct REAL, value REAL, bar_value REAL, path TEXT, passed INT, fails TEXT)")
    rows = [("09:30", "A", "공통", 1, ""), ("09:30", "B", "키움만", 0, "C10분봉30억"),
            ("09:33", "P", "파이썬만", 1, ""), ("14:00", "L", "키움꺼진뒤", 1, "")]
    for t, code, name, ok, f in rows:
        c.execute("INSERT INTO leader_obs VALUES (?,?,?,?,1,1,1,1,1,1,'급등',?,?)", (D, t, code, name, ok, f))
    c.commit(); c.close()
    return db


class Compare(unittest.TestCase):
    def test_day(self):
        db = build()
        daybot_db.datetime = type("DT", (), {"datetime": type("X", (), {
            "now": staticmethod(lambda: __import__("datetime").datetime(2026, 10, 7, 9, 31))})})
        try:
            daybot_db.log_kiwoom_hits({"A": ["주도주검색식3"], "B": ["주도주검색식3", "단타000"]},
                                      {"A": "공통", "B": "키움만"}, db)
        finally:
            daybot_db.datetime = __import__("datetime")
        r = {x["label"]: x for x in S.compare_day(D, db)}["주도주검색식3"]
        self.assertEqual([c for c, _ in r["both"]], ["A"])
        self.assertEqual(r["k_only"], [("B", "키움만", "C10분봉30억")])
        self.assertEqual(r["p_only"], [("P", "파이썬만", "1회 09:33")])   # 14:00 'L'은 키움 스캔 없던 시각 → 제외
        # 09:31 키움 스캔 ↔ 09:30 파이썬 검사: 키움{A,B} vs 파이썬{A} → 1/2
        self.assertEqual((r["snaps"], r["snap_pct"]), (1, 50.0))
        self.assertAlmostEqual(r["match_pct"], 100 / 3)
        txt = S.format_day(D, S.compare_day(D, db))
        self.assertIn("키움만: 키움만(B) — 파이썬 판정: C10분봉30억", txt)
        self.assertIn("■ 단타000", txt)
        self.assertEqual(S.recent_dates(5, db), [D])

    def test_timeout_scans_not_counted(self):
        # 09:31 스캔: 주도주는 타임아웃(cond_ok에 없음), 단타만 성공 → 주도주 비교 대상 스캔 0회
        db = build()
        daybot_db.datetime = type("DT", (), {"datetime": type("X", (), {
            "now": staticmethod(lambda: __import__("datetime").datetime(2026, 10, 7, 9, 31))})})
        try:
            daybot_db.log_kiwoom_hits({}, {}, db, cond_ok={"단타000"})
        finally:
            daybot_db.datetime = __import__("datetime")
        r = {x["label"]: x for x in S.compare_day(D, db)}
        self.assertEqual((r["주도주검색식3"]["k_scans"], r["주도주검색식3"]["k_all"]), (0, 1))
        self.assertEqual(r["주도주검색식3"]["p_only"], [])            # 키움이 못 본 순간이라 '파이썬만'으로 안 셈
        self.assertEqual(r["단타000"]["k_scans"], 1)

    def test_no_kiwoom_data(self):
        db = build()
        self.assertIn("비교 불가", S.format_day(D, S.compare_day(D, db)))


if __name__ == "__main__":
    unittest.main()
