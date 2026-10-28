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
        self.assertEqual(ranked[0][2], 4.5); self.assertEqual(ranked[0][5:], ("전지4", 7.0))   # ★ 10-28 최고 1종목(7.0) 빼고 평균
        T.save("2026-10-20", "0915", ranked, "log.db")
        got = sqlite3.connect("log.db").execute("SELECT rank, theme FROM theme_open_obs ORDER BY rank").fetchall()
        self.assertEqual(got, [(1, "2차전지"), (2, "로봇")])

    def test_premarket_top_stocks(self):
        # ★ 10-26 프리장 — 상위 테마별 1~3위 종목 기록 / ★ 10-28 NXT 안 되는 종목은 0%로 넣고, 5종목 이상 테마만, 최고 1종목 빼고 평균
        themes = {"로봇": [("A", "가"), ("B", "나"), ("C", "다"), ("D", "라"), ("X", "엑스")],
                  "드론": [("E", "마"), ("F", "바"), ("G", "사"), ("H", "아"), ("I", "자")],
                  "둘": [("J", "차"), ("K", "카")]}
        q = {"A": {"chg": 2.0}, "B": {"chg": 9.0, "value": 5e8}, "C": {"chg": 5.0}, "D": {"chg": 1.0},
             "E": {"chg": 30.0}, "F": {"chg": 25.0}, "J": {"chg": 20.0}, "K": {"chg": 20.0}}
        ranked = T.rank_themes(themes, q, T.PRE_MIN_MEMBERS, fill_zero=True)
        self.assertEqual([r[1] for r in ranked], ["드론", "로봇"])                 # 2종목 테마는 빠짐
        self.assertEqual(ranked[0][2], 6.25)                                       # 드론 (25+0+0+0)/4 — 30% 1종목 뺌
        self.assertEqual(ranked[1][2], 2.0)                                        # 로봇 (5+2+1+0)/4
        top = T.pre_top(themes, q, ranked)
        self.assertEqual([(r[2], r[3]) for r in top if r[1] == "로봇"], [(1, "B"), (2, "C"), (3, "A")])
        T.save_pre_top("2026-10-27", "0845", top, "log.db")
        self.assertEqual(sqlite3.connect("log.db").execute("SELECT COUNT(*) FROM theme_pre_top").fetchone()[0], 5)
        T.save("2026-10-27", "0845", ranked, "log.db")
        m = T.premarket_message("2026-10-27", "log.db")
        self.assertIn("프리장 섹터", m); self.assertIn("08:45", m)
        self.assertIn("**로봇** +2.0%", m); self.assertIn("나 +9.0% · 다 +5.0% · 가 +2.0%", m)
        self.assertEqual(T.premarket_message("2026-10-28", "log.db"), "")      # 그날 기록 없으면 안 보냄


if __name__ == "__main__":
    unittest.main()
