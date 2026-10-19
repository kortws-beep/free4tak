"""장 초반 주도 테마 기록 — 테마 이름 파싱, 5종목 미만 제외, 평균 등락순 순위·대장, 저장."""
import os
import sqlite3
import sys
import unittest

from _helpers import use_temp_cwd

_tmp = use_temp_cwd()
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "intelligence"))
import theme_open_obs as T  # noqa: E402


class Theme(unittest.TestCase):
    def test_load_rank_save(self):
        db = "theme.db"
        c = sqlite3.connect(db)
        c.execute("CREATE TABLE kr_theme_stocks (id INTEGER, theme_name TEXT, stock_name TEXT, updated_at TEXT)")
        rows = [("2차전지", f"전지{i}KOSDAQ 1000{i}0") for i in range(5)] + \
               [("로봇", f"로봇{i}KOSPI 2000{i}0") for i in range(5)] + [("소수", "외톨이KOSPI 300000")]
        c.executemany("INSERT INTO kr_theme_stocks (theme_name, stock_name) VALUES (?,?)", rows)
        c.commit(); c.close()
        themes = T.load_themes(db)
        self.assertEqual(themes["2차전지"][0], ("100000", "전지0"))
        q = {f"1000{i}0": {"chg": 3.0 + i} for i in range(5)}
        q.update({f"2000{i}0": {"chg": -1.0} for i in range(5)})
        q["300000"] = {"chg": 30.0}
        ranked = T.rank_themes(themes, q)
        self.assertEqual([r[1] for r in ranked], ["2차전지", "로봇"])       # 소수(1종목)는 빠짐
        self.assertEqual(ranked[0][2], 5.0); self.assertEqual(ranked[0][5:], ("전지4", 7.0))
        T.save("2026-10-20", "0915", ranked, "log.db")
        got = sqlite3.connect("log.db").execute("SELECT rank, theme FROM theme_open_obs ORDER BY rank").fetchall()
        self.assertEqual(got, [(1, "2차전지"), (2, "로봇")])


if __name__ == "__main__":
    unittest.main()
