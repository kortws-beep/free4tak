"""cbot 전면 재점검 — 묶기·수동 제외·사유 그룹·MFE/MAE·매수/매도 문제 분류."""
import os
import sys
import sqlite3
import datetime
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backtest"))
from _helpers import use_temp_cwd  # noqa: E402
_tmp = use_temp_cwd()
import cbot_audit as A  # noqa: E402

NOW = datetime.datetime.now().replace(microsecond=0)
iso = lambda d: d.isoformat(timespec="seconds")


def make():
    t = sqlite3.connect("t.db")
    t.execute("""CREATE TABLE trades (id INTEGER PRIMARY KEY, market TEXT, buy_price REAL, buy_time TEXT, sell_price REAL,
                 sell_time TEXT, qty REAL, profit_rate REAL, profit_krw REAL, sell_reason TEXT, ai_score INTEGER)""")
    b1, s1 = NOW - datetime.timedelta(hours=10), NOW - datetime.timedelta(hours=8)
    b2, s2 = NOW - datetime.timedelta(hours=6), NOW - datetime.timedelta(hours=5)
    rows = [  # A: +5% 찍고 -3% 손절 (매도 문제) · B: 분할 익절 +3만·트레일링 +1만 · C: 바로 -2% 손절 (매수 문제)
        ("KRW-A", 100, iso(b1), 97, iso(s1), 10000, -3, -30000, "손절(-3.00%)", 60),
        ("KRW-B", 100, iso(b1), 106, iso(b1 + datetime.timedelta(hours=1)), 5000, 6, 30000, "1차익절", 70),
        ("KRW-B", 100, iso(b1), 102, iso(s1), 5000, 2, 10000, "트레일링", 70),
        ("KRW-C", 100, iso(b2), 98, iso(s2), 10000, -2, -20000, "급락감지(-2%)", 50),
        ("KRW-D", 100, iso(b2), 90, iso(s2), 10000, -10, -99999, "수동매도", -1),       # 수동 제외
        ("KRW-E", 100, iso(b2), None, None, 10000, None, None, None, 60)]               # 보유 중 제외
    t.executemany("INSERT INTO trades (market, buy_price, buy_time, sell_price, sell_time, qty, profit_rate, profit_krw,"
                  " sell_reason, ai_score) VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
    t.execute("CREATE TABLE dip_trades (market TEXT, buy_time TEXT, sell_time TEXT, profit_krw REAL, profit_rate REAL,"
              " amount REAL, sell_reason TEXT)")
    t.execute("INSERT INTO dip_trades VALUES ('KRW-XRP', ?, ?, 25000, 5, 500000, '익절')", (iso(b1), iso(s1)))
    t.commit(); t.close()
    k = sqlite3.connect("k.db")
    k.execute("CREATE TABLE price_ticks (market TEXT, ts TEXT, price REAL)")
    ticks = [("KRW-A", b1 + datetime.timedelta(minutes=30), 105), ("KRW-A", s1, 97),
             ("KRW-B", b1 + datetime.timedelta(minutes=30), 108), ("KRW-B", s1, 101),
             ("KRW-C", b2 + datetime.timedelta(minutes=10), 100.5), ("KRW-C", s2, 98)]
    k.executemany("INSERT INTO price_ticks VALUES (?,?,?)", [(m, iso(t_), p) for m, t_, p in ticks])
    k.commit(); k.close()


class Audit(unittest.TestCase):
    def test_all(self):
        make()
        tr = A.load("t.db", 30)
        self.assertEqual([x["market"] for x in tr], ["KRW-A", "KRW-B", "KRW-C"])
        b = tr[1]
        self.assertEqual((b["krw"], b["reason"], b["partial"]), (40000, "트레일링", True))
        self.assertAlmostEqual(b["net"], 40000 - 1_000_000 * A.FEE)
        A.add_excursion(tr, A.Ticks("k.db"))
        self.assertAlmostEqual(tr[0]["mfe"], 5.0); self.assertAlmostEqual(tr[0]["mae"], -3.0)
        msg = A.report(tr, 30, A.load_dip("t.db", 30), btc=-2.5)
        self.assertIn("끝난 봇 매수 3건", msg)
        self.assertIn("한 번이라도 +2%↑ 찍었던 것 1건", msg)
        self.assertIn("+1%도 못 찍은 것 1건", msg)
        self.assertIn("비트코인 -2.5%", msg)
        self.assertIn("눌림목 주머니(별도 100만): 끝난 1건", msg)
        self.assertIn("급락감지", msg)

    def test_reason_and_no_ticks(self):
        self.assertEqual(A.reason_group("보호선(+1.0%)"), "보호선(올린 손절)")
        self.assertEqual(A.reason_group("정체교체(+0.5%)"), "정체교체")
        self.assertIsNone(A.Ticks("없음.db").range("KRW-A", NOW, NOW))


if __name__ == "__main__":
    unittest.main()
