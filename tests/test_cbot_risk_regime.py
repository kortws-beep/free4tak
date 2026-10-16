"""cbot 위험회피 — BTC 20일선 아래/위 손익 분리, 손절 몰림이 기준치 넘는지."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backtest"))
import cbot_risk_regime as CR  # noqa: E402


def _closes(prices):
    return [(f"2026-01-{i + 1:02d}", p) for i, p in enumerate(prices)]


class BelowMA(unittest.TestCase):
    def test_below_ma_map_needs_warmup_and_flags_correctly(self):
        prices = [100] * 20 + [90, 110]   # 20일 평균 100 → 90은 아래, 110은 위
        m = CR.below_ma_map(_closes(prices), ma=20)
        self.assertEqual(len(m), 2)
        dates = sorted(m)
        self.assertTrue(m[dates[0]]); self.assertFalse(m[dates[1]])


class Analyze(unittest.TestCase):
    def test_stop_loss_clustering_vs_base_rate_and_pnl_split(self):
        below_map = {"2026-01-21": True, "2026-01-22": False, "2026-01-23": True, "2026-01-24": False}
        # 손절 2건 다 MA20 아래(기준치는 2/4=50%) → 몰림, 공포 신호는 기준치 이하
        trades = [
            ("2026-01-21", -50000, -8.0, "손절(-8.0%)", "normal", 40),
            ("2026-01-23", -30000, -6.0, "손절(-6.0%)", "normal", 45),
            ("2026-01-22", 20000, 5.0, "익절(+5.0%)", "normal", 60),
            ("2026-01-24", 40000, 7.0, "익절(+7.0%)", "normal", 55),
        ]
        fng = {"2026-01-21": 40, "2026-01-22": 60, "2026-01-23": 45, "2026-01-24": 55}
        a = CR.analyze(trades, below_map, fng, fear_th=20)
        self.assertEqual(a["n_stops"], 2)
        self.assertEqual(a["stop_below_ma_pct"], 100.0)
        self.assertEqual(a["base_below_ma_pct"], 50.0)
        self.assertEqual(a["below_ma"], {"n": 2, "win_rate": 0.0, "pf": 0.0, "net": -80000})
        self.assertEqual(a["above_ma"]["win_rate"], 100.0)
        out = CR.report(a)
        self.assertIn("MA20 신호 유효", out); self.assertIn("공포탐욕 신호 약함", out)


if __name__ == "__main__":
    unittest.main()
