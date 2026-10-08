"""4번 프로토타입 — 수동매매(리나 등록/해제)와 검색식·섹터·데이봇 후보 대조."""
import datetime
import os
import sqlite3
import unittest

from _helpers import use_temp_cwd

_tmp = use_temp_cwd()
import three_month_leader as tml  # noqa: E402

tml.LOG_DB = os.path.abspath("log.db")
import manual_vs_signal as M  # noqa: E402

D = datetime.date.today().isoformat()


def build():
    if os.path.exists(tml.LOG_DB):
        os.remove(tml.LOG_DB)
    c = sqlite3.connect(tml.LOG_DB)
    c.execute("CREATE TABLE manual_watch_log (ts TEXT, event TEXT, code TEXT, name TEXT, entry_price REAL, "
              "sell_price REAL, profit_rate REAL, peak_price REAL, registered_at TEXT)")
    c.executemany("INSERT INTO manual_watch_log VALUES (?,?,?,?,?,?,?,?,?)", [
        (f"{D} 09:30:00", "register", "111110", "가종목", 10000, None, None, None, None),
        (f"{D} 10:00:00", "register", "222220", "나종목", 5000, None, None, None, None),
        (f"{D} 10:20:00", "register", "222220", "나종목", 5200, None, None, None, None),
        (f"{D} 11:00:00", "deregister", "111110", "가종목", 10000, 10300, 3.0, None, None),
        (f"{D} 13:00:00", "deregister", "333330", "다종목", 2000, 1950, -2.5, None, f"{D}T09:05:00"),
        (f"{D} 13:10:00", "register", "444440", "라종목", 1000, None, None, None, None),
        (f"{D} 14:00:00", "deregister", "444440", "라종목", 1000, None, None, None, None)])
    c.execute("CREATE TABLE leader_obs (date TEXT, time TEXT, code TEXT, name TEXT, price REAL, chg REAL, "
              "passed INTEGER, fails TEXT)")
    c.executemany("INSERT INTO leader_obs VALUES (?,?,?,?,?,?,?,?)", [
        (D, "09:03", "111110", "가종목", 9900, 4, 0, "10분봉대금 미달"),
        (D, "09:06", "111110", "가종목", 9950, 5, 1, ""),
        (D, "09:40", "222220", "나종목", 5000, 3, 0, "고가대비 이탈")])
    c.execute("CREATE TABLE sector_obs (date TEXT, time TEXT, grp TEXT, rank INTEGER, avg_chg REAL, up_ratio REAL, "
              "value REAL, leader TEXT, leader_chg REAL, second TEXT, second_chg REAL)")
    c.execute("INSERT INTO sector_obs (date, time, grp, rank, leader, second) VALUES (?,?,?,?,?,?)",
              (D, "09:00", "반도체", 1, "가종목", "x"))
    c.commit()
    return c


class Manual(unittest.TestCase):
    def test_pairs(self):
        c = build()
        t = M.load_manual(c, 3)
        self.assertEqual([x["code"] for x in t], ["111110", "222220", "333330", "444440"])
        self.assertEqual((t[0]["rate"], t[1]["dereg"], t[2]["reg"]), (3.0, None, f"{D} 09:05:00"))
        self.assertEqual((t[1]["entry0"], t[1]["entry"], len(t[1]["adds"])), (5000, 5200, 1))   # 재등록=추가매수

    def test_buy_time_estimate(self):
        bars = [("090000", 9800, 9850, 9750), ("091000", 9990, 10010, 9980), ("093500", 10000, 10000, 10000)]
        self.assertEqual(M.estimate_buy_time(bars, 10000, "093000"), "091000")
        self.assertIsNone(M.estimate_buy_time(bars, 12000, "093000"))

    def test_analyze_and_report(self):
        c = build()

        class Store:
            def day(self, code, date):
                if code == "444440":
                    return [("131000", 1000, 1000, 1000), ("135900", 1040, 1040, 1040), ("140500", 900, 900, 900)]
                return [("090800", 9990, 10010, 9980), ("100000", 10300, 10300, 10300)] if code == "111110" else []

            def days_from(self, code, date, n):
                return [(date, self.day(code, date))]
        tr = M.analyze(M.load_manual(c, 3), c, Store(), {"반도체관심": [("111110", "가종목")]})
        a = tr[0]
        self.assertEqual((a["buy_t"], a["buy_est"], a["sig_before"], a["lead_min"]), ("090800", True, "주도주", 2))
        self.assertEqual(a["sector"], "상위섹터1·2등"); self.assertEqual(a["groups"], ["반도체관심"])
        self.assertEqual(a["bot"]["reason"], "보유중")                    # 09:06 신호 매수 → 10300 보유
        b = tr[1]
        self.assertIsNone(b["sig_before"]); self.assertEqual(b["sig"]["주도주"]["near"], "고가대비 이탈")
        d = tr[3]
        self.assertTrue(d["rate_est"]); self.assertAlmostEqual(d["rate"], 4.0)        # 14:00 직전 1040
        out = M.report(tr, 3)
        self.assertIn("+4.00%(추정) 해제 14:00", out)
        self.assertIn("사기 전에 파이썬 검색식이 잡았음 1/4", out)
        self.assertIn("주도주 09:06✅", out); self.assertIn("주도주 ✗(고가대비 이탈)", out)
        self.assertIn("관심:반도체관심", out)
        self.assertIn("추가매수 1회→평단 5,200", out)


if __name__ == "__main__":
    unittest.main()
