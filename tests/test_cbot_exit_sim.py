"""cbot 매도 규칙 시뮬 — 보호선·짧은 추적·시간 정리가 시세 흐름대로 걸리는지."""
import os
import sys
import datetime
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backtest"))
from _helpers import use_temp_cwd  # noqa: E402
_tmp = use_temp_cwd()
import cbot_exit_sim as S  # noqa: E402
import cbot_audit as A  # noqa: E402

T0 = datetime.datetime(2026, 10, 1, 12, 0)


def trade(net, rate, buy=T0):
    return {"market": "KRW-A", "buy": buy, "bp": 100.0, "cost": 1_000_000, "net": net, "rate": rate,
            "sells": [(buy + datetime.timedelta(hours=60), "손절")]}


def pts(prices, step_h=1.0, start=T0):
    return [(start + datetime.timedelta(hours=step_h * (i + 1)), p) for i, p in enumerate(prices)]


class Sim(unittest.TestCase):
    def test_protect(self):
        x = trade(-50000, -5)
        r = S.protect(3, 0.3)
        self.assertEqual(r(x, pts([101, 103.5, 102, 100.2, 95])), 100.2)    # +3.5% 찍고 +0.2% → 매도
        self.assertIsNone(r(x, pts([101, 102.9, 100, 95])))                  # +3% 못 찍음 → 안 걸림

    def test_trail_and_time(self):
        x = trade(-50000, -5)
        self.assertEqual(S.trail(3, 2)(x, pts([103, 106, 104, 103.8])), 103.8)   # 고점 106 -2% = 103.88 밑
        self.assertEqual(S.time_exit(24)(x, pts([100.5] * 30)), 100.5)          # 24시간째 +0.5%
        self.assertIsNone(S.time_exit(24)(x, pts([102] * 30)))                  # +2%면 그대로

    def test_simulate_and_report(self):
        lose = trade(-50000 - 1000, -5)
        big = trade(80000 - 1000, 8, buy=T0 + datetime.timedelta(days=2))
        paths = {id(lose): pts([103.5, 100.2, 95]), id(big): pts([103.5, 100.1, 110], start=big["buy"])}
        s = S.simulate([lose, big], paths, S.protect(3, 0.3))
        self.assertEqual((s["changed"], s["better"], s["worse"], s["cut_big"]), (2, 1, 1, 1))
        self.assertAlmostEqual(s["delta"], (2000 - 1000 + 51000) + (1000 - 1000 - 79000))
        msg = S.report([lose, big], paths, 30)
        self.assertIn("P. +3% 찍은 뒤 +0.3%로 내려오면", msg); self.assertIn("후보", msg)
        self.assertIsNotNone(A.FEE)


if __name__ == "__main__":
    unittest.main()
