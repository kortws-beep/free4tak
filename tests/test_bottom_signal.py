"""시장 바닥 신호 검증 — 신저가 비율·결과 집계·신호·리포트."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backtest"))
from _helpers import stub  # noqa: E402
stub("three_month_leader", THEME_DB="")
import bottom_signal as B  # noqa: E402


def mk_daily():
    """60종목 · 40일: 25일 상승, 26~32일 하락(장중 투매), 33일째 망치형(시가-3%→종가 +1%), 이후 +2%씩."""
    dates = [f"2026-08-{i:02d}" if i <= 31 else f"2026-09-{i - 31:02d}" for i in range(1, 41)]
    daily = {}
    for k in range(60):
        p, rows = 100.0, []
        for i, d in enumerate(dates):
            if i < 25:
                o = p; p *= 1.01; rows.append((d, o, p, o * 0.995, p, 1000 - k))
            elif i < 32:
                o = p; p *= 0.97; rows.append((d, o, o, p * 0.99, p, 1000 - k))
            elif i == 32:
                o = p; lo = o * 0.97; p = o * 1.01; rows.append((d, o, p, lo, p, 1000 - k))
            else:
                o = p; p *= 1.02; rows.append((d, o, p, o, p, 1000 - k))
        daily[f"종목{k}"] = rows
    return daily, dates


class Bottom(unittest.TestCase):
    def test_pipeline(self):
        daily, dates = mk_daily()
        lows = B.new_low_share(daily)
        self.assertEqual(lows[dates[25]], 0.0); self.assertEqual(lows[dates[31]], 100.0)
        outs = B.outcomes(daily, 10)
        self.assertEqual(len(outs[dates[32]]), 10)
        mx, r, tp, tpsl = outs[dates[32]][0]
        self.assertAlmostEqual(mx, 1.02 ** 5 - 1); self.assertAlmostEqual(r, 1.02 ** 5 - 1 - B.COST)
        self.assertAlmostEqual(tp, 0.05 - B.COST)          # 3일째 고가 1.0612 ≥ +5%
        nxt = [("d", 100, 101, 94, 95, 0), ("d", 95, 120, 95, 110, 0)]
        self.assertAlmostEqual(B.tp_exit(100, nxt), 0.05)
        self.assertAlmostEqual(B.tp_exit(100, nxt, sl=0.05), -0.05)
        self.assertAlmostEqual(B.tp_exit(100, [("d", 107, 108, 106, 107, 0)]), 0.07)   # 갭 위 시가
        self.assertAlmostEqual(B.tp_exit(100, [("d", 100, 103, 99, 102, 0)]), 0.02)    # 못 닿음
        import rebound_backtest as rb, swing_backtest as sb
        table = rb.day_table(daily)
        sig = B.signals(table, rb.streaks(table), sb.market_breadth(daily), lows)
        self.assertIn(dates[32], sig["A 망치형 바닥"])
        self.assertIn(dates[32], sig["E 연속하락 끝 양봉"])
        s = B.summarize([dates[32]], outs)
        self.assertEqual(s["hit5"], 100.0); self.assertEqual(s["mkt_up"], 100.0)
        out = B.report(daily, 10)
        self.assertIn("A 망치형 바닥", out); self.assertIn("최근 10거래일", out)


if __name__ == "__main__":
    unittest.main()
