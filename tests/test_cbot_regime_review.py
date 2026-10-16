"""cbot 장 상태별 성적 — 분할매도 합치기·수동 제외·비트 24시간·규칙 시뮬레이션."""
import os
import sys
import sqlite3
import datetime
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backtest"))
from _helpers import use_temp_cwd  # noqa: E402
_tmp = use_temp_cwd()
import cbot_regime_review as R  # noqa: E402

NOW = datetime.datetime.now().replace(microsecond=0)


def iso(dt): return dt.isoformat(timespec="seconds")


def make_dbs():
    t = sqlite3.connect("t.db")
    t.execute("""CREATE TABLE trades (id INTEGER PRIMARY KEY, market TEXT, buy_price REAL, buy_time TEXT,
                 sell_price REAL, sell_time TEXT, qty REAL, profit_rate REAL, profit_krw REAL, sell_reason TEXT,
                 ai_score INTEGER, ai_reason TEXT, market_status TEXT, fear_greed INTEGER)""")
    b1, b2, b3 = iso(NOW - datetime.timedelta(hours=10)), iso(NOW - datetime.timedelta(hours=5)), iso(NOW)
    rows = [  # 1건: 분할매도 2행 (+3만, -1만) → +2만 · 공포 50 · BTC 상승
        ("KRW-A", 100, b1, 110, b1, 3, 0, 30000, "1차익절", 60, "normal", 50),
        ("KRW-A", 100, b1, 95, b1, 7, 0, -10000, "트레일링", 60, "normal", 50),
        # 2건: 손절 -5만 · 공포 25 · BTC 하락
        ("KRW-B", 100, b2, 90, b2, 10, 0, -50000, "손절", 60, "weak", 25),
        # 수동매매(제외) · 아직 보유(제외)
        ("KRW-C", 100, b2, 90, b2, 10, 0, -99000, "수동매도", -1, "normal", 50),
        ("KRW-D", 100, b3, None, None, 10, None, None, None, 60, "normal", 50)]
    t.executemany("""INSERT INTO trades (market, buy_price, buy_time, sell_price, sell_time, qty, profit_rate,
                     profit_krw, sell_reason, ai_score, market_status, fear_greed) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                  rows)
    t.commit(); t.close()
    k = sqlite3.connect("k.db")
    k.execute("CREATE TABLE price_ticks (market TEXT, ts TEXT, price REAL)")
    # BTC: 34시간 전 100 → 10시간 전 102(+2%) → 5시간 전 96 (24시간 전 = 29시간 전 100 → -4%)
    pts = [(NOW - datetime.timedelta(minutes=m), 100.0) for m in range(34 * 60, 10 * 60, -10)]
    pts += [(NOW - datetime.timedelta(hours=10), 102.0)]
    pts += [(NOW - datetime.timedelta(minutes=m), 96.0) for m in range(10 * 60 - 10, 5 * 60 - 1, -10)]
    k.executemany("INSERT INTO price_ticks VALUES ('KRW-BTC', ?, ?)", [(iso(a), p) for a, p in pts])
    k.commit(); k.close()


class Regime(unittest.TestCase):
    def test_all(self):
        make_dbs()
        tr = R.load_trades("t.db", 30)
        self.assertEqual(sorted(x["market"] for x in tr), ["KRW-A", "KRW-B"])
        a = next(x for x in tr if x["market"] == "KRW-A")
        b = next(x for x in tr if x["market"] == "KRW-B")
        self.assertEqual(a["krw"], 20000); self.assertFalse(a["loss_exit"]); self.assertTrue(b["loss_exit"])
        R.enrich(tr, R.BtcTicks("k.db", min(x["buy_time"] for x in tr)))
        self.assertAlmostEqual(a["btc24"], 2.0, places=3); self.assertGreater(a["btc_ma"], 0)
        self.assertAlmostEqual(b["btc24"], -4.0, places=3); self.assertLess(b["btc_ma"], 0)
        msg = R.report(tr, 30)
        self.assertIn("끝난 매수 2건", msg)
        self.assertIn("비트 24시간 -3%↓ 쉬기: 빠지는 1건 (손절 1) · 효과 +5.0만", msg)
        self.assertIn("공포탐욕 30 미만 쉬기: 빠지는 1건", msg)

    def test_no_ticks(self):
        self.assertEqual(R.BtcTicks("없음.db", NOW).at(NOW), (None, None))


if __name__ == "__main__":
    unittest.main()
