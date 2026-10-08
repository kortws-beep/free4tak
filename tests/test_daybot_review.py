"""daybot 실매매 점검 — 손익 집계, 사유/시각 분류, 하루 한도 시뮬, 증액 점검, 매도 뒤 분봉."""
import os
import sqlite3
import sys
import unittest

from _helpers import use_temp_cwd

_tmp = use_temp_cwd()
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backtest"))
import daybot_review as R  # noqa: E402


def mkdb():
    if os.path.exists("d.db"):
        os.remove("d.db")
    conn = sqlite3.connect("d.db")
    conn.execute("""CREATE TABLE trades (id INTEGER PRIMARY KEY, code TEXT, stock_name TEXT, buy_price REAL,
        buy_time TEXT, sell_price REAL, sell_time TEXT, qty INTEGER, profit_rate REAL, sell_reason TEXT,
        buy_tag TEXT, hold_days INTEGER)""")
    conn.execute("""CREATE TABLE candidate_log (id INTEGER PRIMARY KEY, ts TEXT, code TEXT, stock_name TEXT,
        source_tier TEXT, price REAL, change_rate REAL, ask_bid_ratio REAL, bought INTEGER, skip_reason TEXT,
        raw_market_data TEXT, raw_hoga_data TEXT)""")
    import datetime
    d = datetime.date.today().isoformat()
    rows = [("A", 10000, f"{d}T09:10:00", 10500, f"{d}T10:00:00", 100, "트레일링청산(…)", "tier1_overlap", 0),
            ("B", 10000, f"{d}T09:50:00", 9300, f"{d}T10:30:00", 100, "손절(-7.00%)", "tier2_danta000", 0),
            ("C", 20000, f"{d}T13:10:00", 19300, f"{d}T14:00:00", 50, "손절(-3.50%)", "tier2_danta000", 0),
            ("D", 10000, f"{d}T09:20:00", None, None, 100, None, "x", 0)]
    conn.executemany("INSERT INTO trades (code, buy_price, buy_time, sell_price, sell_time, qty, sell_reason, "
                     "buy_tag, hold_days) VALUES (?,?,?,?,?,?,?,?,?)", rows)
    conn.execute("INSERT INTO candidate_log (ts, code, change_rate, ask_bid_ratio, bought) VALUES (?,?,?,?,1)",
                 (f"{d}T09:09:30", "A", 5.2, 3.4))
    conn.commit(); conn.close()


class Review(unittest.TestCase):
    def setUp(self):
        mkdb()
        self.ts = R.load_trades("d.db", 5)

    def test_load_and_stats(self):
        self.assertEqual([t["code"] for t in self.ts], ["A", "B", "C"])
        a = self.ts[0]
        self.assertAlmostEqual(a["krw"], 500 * 100 - 1_000_000 * R.COST)
        self.assertEqual((a["chg"], a["abr"]), (5.2, 3.4))
        s = R.stats(self.ts)
        self.assertEqual(s["n"], 3); self.assertAlmostEqual(s["win"], 100 / 3)
        self.assertEqual(R.max_streak(self.ts), 2)

    def test_buckets(self):
        self.assertEqual(R.reason_group("트레일링청산(고점…)"), "트레일링")
        self.assertEqual(R.reason_group("보유기한청산(3영업일)"), "보유기한")
        self.assertEqual(R.time_bucket(self.ts[1]["buy"]), "09:40~11:00")
        self.assertEqual(R.chg_bucket(None), "기록없음"); self.assertEqual(R.chg_bucket(5.2), "3~8%")

    def test_daily_limit_and_report(self):
        d = R.daily(self.ts)
        self.assertEqual(len(d), 1)
        self.assertEqual(d[0]["hit"], {})                          # +4.8만 → -2.4만 → -6.1만
        self.assertAlmostEqual(d[0]["low"], -61_000)
        out = R.report(self.ts, 5)
        self.assertIn("손절이 -3.5%보다 1%p 넘게 깊었던 것 1건", out)
        self.assertIn("증액 준비 점검", out)

    def test_after_sell(self):
        class Api:
            def get_minute_bars_by_date(self, code, d, hh):
                if hh == "153000":
                    return [{"time": "153000", "price": 9500, "high": 9500}]
                return [{"time": "103500", "price": 9200, "high": 9250},
                        {"time": "110000", "price": 9400, "high": 9600},
                        {"time": "113100", "price": 9300, "high": 9300}]
        a = R.after_sell(Api(), self.ts[1])                       # 10:30 매도 @9300
        self.assertAlmostEqual(a["30분"], 9400 / 9300 * 100 - 100)
        self.assertAlmostEqual(a["1시간"], 0.0)
        self.assertAlmostEqual(a["2시간최고"], 9600 / 9300 * 100 - 100)
        self.assertAlmostEqual(a["종가"], 9500 / 9300 * 100 - 100)
        self.assertIn("30분", R.report(self.ts, 5, [(self.ts[1], a)]))


if __name__ == "__main__":
    unittest.main()
