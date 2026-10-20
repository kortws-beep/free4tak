"""cbot 한 달 해부 — 보호선 경로 재현, 펀딩비 시점 조회, 비트 24시간."""
import os
import sys
import sqlite3
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backtest"))
import cbot_month_autopsy as A  # noqa: E402


class Autopsy(unittest.TestCase):
    def test_protect_exit(self):
        path = [("t1", 101.0), ("t2", 103.5), ("t3", 101.0), ("t4", 100.2), ("t5", 92.0)]
        self.assertEqual(A.protect_exit(path, 100.0, "t9"), ("t4", A.PROTECT_FLOOR))      # +3.5% 뒤 +0.2%
        self.assertIsNone(A.protect_exit(path, 100.0, "t3"))                               # 실제로 먼저 팔았으면 없음
        self.assertIsNone(A.protect_exit([("t1", 102.9), ("t2", 90.0)], 100.0, "t9"))     # +3% 못 감

    def test_funding_and_btc24(self):
        fund = (["2026-10-01T01:00:00", "2026-10-01T09:00:00"], [0.001, 0.008])
        self.assertIsNone(A.funding_at(fund, "2026-09-30T23:00:00"))
        self.assertEqual(A.funding_at(fund, "2026-10-01T08:59:59"), 0.001)
        self.assertEqual(A.funding_at(fund, "2026-10-01T12:00:00"), 0.008)
        db = os.path.join(tempfile.mkdtemp(), "t.db")
        c = sqlite3.connect(db)
        c.execute("CREATE TABLE price_ticks (market TEXT, ts TEXT, price REAL)")
        c.executemany("INSERT INTO price_ticks VALUES ('KRW-BTC',?,?)",
                      [("2026-10-01T12:00:00", 100.0), ("2026-10-02T12:00:00", 98.0)])
        c.commit(); c.close()
        self.assertAlmostEqual(A.btc_24h(A.Ticks(db), "2026-10-02T12:05:00"), -2.0)
        self.assertIsNone(A.btc_24h(A.Ticks(db), "2026-10-02T13:00:00"))                # 10분 넘게 빈 시세는 안 씀


if __name__ == "__main__":
    unittest.main()
