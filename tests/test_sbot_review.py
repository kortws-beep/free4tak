"""sbot 실매매 점검 — 사유·출처·단계·보유기간 분류, 국면 칸, 한도 걸린 날, 손절 뒤 주가."""
import datetime
import os
import sqlite3
import sys
import unittest

from _helpers import use_temp_cwd

_tmp = use_temp_cwd()
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backtest"))
import sbot_review as R  # noqa: E402

D = datetime.date.today()


def build():
    c = sqlite3.connect("s.db")
    c.execute("""CREATE TABLE trades (id INTEGER PRIMARY KEY, code TEXT, stock_name TEXT, buy_price REAL,
        buy_time TEXT, sell_price REAL, sell_time TEXT, qty INTEGER, profit_rate REAL, sell_reason TEXT,
        ai_score INTEGER, ai_reason TEXT, buy_tag TEXT, hold_days INTEGER, grade TEXT,
        stage_reached INTEGER, atr_val_at_entry REAL)""")
    b = (D - datetime.timedelta(days=3)).isoformat()
    rows = [("A", "가", 10000, b, 11000, 1500, "트레일링(…)", 72, "momentum", 3, 2),
            ("B", "나", 10000, b, 7000, 2000, "손절(-30%)", 55, "kisnew", 3, 0),
            ("C", "다", 10000, b, 9500, 100, "본절청산", 81, "youtube", 3, 1)]
    c.executemany("""INSERT INTO trades (code, stock_name, buy_price, buy_time, sell_price, qty, sell_reason,
        ai_score, buy_tag, hold_days, stage_reached, sell_time) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                  [r + (f"{D}T10:00:00",) for r in rows])
    c.commit(); c.close()


class Review(unittest.TestCase):
    def test_report(self):
        build()
        ts = R.load_trades("s.db", 10)
        self.assertEqual([R.reason_group(t["reason"]) for t in ts], ["트레일링", "손절", "본절"])
        self.assertEqual(R.hold_bucket(0), "당일"); self.assertEqual(R.hold_bucket(7), "6~10일")
        self.assertEqual(R.ai_bucket(81), "80+")
        regimes = {ts[0]["buy"].date().isoformat(): 42.0}
        out = R.report(ts, 10, regimes, [(ts[1], -5.0, 3.0)])
        self.assertIn("50% 미만(약한 장)", out)
        self.assertIn("일손실 한도 -500,000원 걸렸을 날: 1일", out)       # -600만+... 하루 -60만대
        self.assertIn("5거래일 뒤 평균 -5.00%", out)
        self.assertIn("단계2", out); self.assertIn("momentum", out)


if __name__ == "__main__":
    unittest.main()
