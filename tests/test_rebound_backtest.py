"""연속 하락 뒤 반등 검증 — 하락 연속일수·지정가 체결·리포트."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backtest"))
from _helpers import stub  # noqa: E402
stub("three_month_leader", THEME_DB="")
import rebound_backtest as R  # noqa: E402


def mk_daily():
    """60종목 · 8일: 1~5일째 매일 -1% 하락, 6일째 시가 100→저가 95→종가 99, 7일째 시가 100."""
    daily = {}
    for k in range(60):
        rows, p = [], 100.0
        for i in range(8):
            d = f"2026-10-{i + 1:02d}"
            if 1 <= i <= 5:
                p2 = p * 0.99
                rows.append((d, p, p, p2, p2, 1000 - k)); p = p2
            elif i == 6:
                rows.append((d, 100.0, 101.0, 95.0, 99.0, 1000 - k)); p = 99.0
            else:
                rows.append((d, 100.0, 100.0, 100.0, 100.0, 1000 - k))
        daily[f"종목{k}"] = rows
    daily["KODEX 200"] = daily["종목0"]
    return daily


class Rebound(unittest.TestCase):
    def test_streaks_and_trades(self):
        daily = mk_daily()
        t = R.day_table(daily)
        st = R.streaks(t)
        self.assertEqual(st["2026-10-07"], 5)
        rs = R.trades(daily, 3.0, 10, {"2026-10-07"})
        self.assertEqual(len(rs), 10)                       # 저가 95 ≤ 97 → 체결
        self.assertAlmostEqual(rs[0]["ret"], 99 / 97 - 1 - R.COST)
        self.assertAlmostEqual(rs[0]["nxt_o"], 100 / 97 - 1 - R.COST)
        self.assertEqual(R.trades(daily, 6.0, 10, {"2026-10-07"}), [])   # 6%까진 안 빠짐
        out = R.report(daily, 10)
        self.assertIn("5일+ 연속 하락 다음 날 1번", out)
        self.assertNotIn("KODEX", out)

    def test_excluded(self):
        self.assertTrue(R.excluded("TIGER 200")); self.assertTrue(R.excluded("삼성전자우"))
        self.assertFalse(R.excluded("삼성전자"))


if __name__ == "__main__":
    unittest.main()
