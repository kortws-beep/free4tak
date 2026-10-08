"""관심그룹 스윙 백테스트 — 일봉 손절/트레일/기한, 회복 판정, 신호 생성, 범위 분류."""
import os
import sys
import unittest

from _helpers import use_temp_cwd

_tmp = use_temp_cwd()
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backtest"))
import swing_backtest as S  # noqa: E402

E = 10000 / (1 + S.SLIP)


def day(i, o, h, l, c):
    return (f"2026-09-{i:02d}", o, h, l, c)


class Sim(unittest.TestCase):
    def test_gap_stop_and_no_stop(self):
        after = [day(1, 9500, 9600, 9050, 9100), day(2, 8000, 8200, 7900, 8100)]
        r = S.simulate(E, after, S.SwingRule("x", stop=-10.0))
        self.assertEqual((r["reason"], r["days"]), ("손절", 2))
        self.assertAlmostEqual(r["ret"], 8000 * (1 - S.SLIP) / 10000 - 1 - S.COST)   # 갭하락은 시가
        r2 = S.simulate(E, after, S.SwingRule("x", stop=None, max_days=2))
        self.assertEqual(r2["reason"], "기한"); self.assertAlmostEqual(r2["mae"], -21.0)

    def test_trailing_and_open(self):
        after = [day(1, 10100, 10600, 10000, 10500), day(2, 10500, 11000, 10400, 10900),
                 day(3, 10800, 10850, 10500, 10600)]
        r = S.simulate(E, after, S.SwingRule("x", stop=-7.0, tp=5.0, trail=4.0))
        self.assertEqual((r["reason"], r["days"]), ("트레일링", 3))
        self.assertAlmostEqual(r["ret"], 10560 * (1 - S.SLIP) / 10000 - 1 - S.COST)   # 11000×0.96
        self.assertEqual(S.simulate(E, after[:1], S.SwingRule("x"))["reason"], "보유중")

    def test_recovery(self):
        base = 10000 / (1 + S.SLIP) / (1 + S.COST)
        self.assertIsNone(S.recovery(base, [day(1, 1, 10100, 9800, 1)], 5))
        self.assertTrue(S.recovery(base, [day(1, 1, 9800, 9400, 1), day(2, 1, 10050, 9500, 1)], 5))
        self.assertFalse(S.recovery(base, [day(1, 1, 9800, 9400, 1), day(2, 1, 9900, 9300, 1)], 5))


class Signals(unittest.TestCase):
    def test_make_signals_and_universe(self):
        daily = {"가": [("d1", 1, 1, 1, 100, 10), ("d2", 1, 1, 1, 106, 50)],
                 "나": [("d1", 1, 1, 1, 100, 10), ("d2", 1, 1, 1, 104, 90)],
                 "다": [("d1", 1, 1, 1, 100, 10), ("d2", 1, 1, 1, 110, 5)],
                 "KODEX 레버리지": [("d1", 1, 1, 1, 100, 10), ("d2", 1, 1, 1, 120, 99)]}
        sig = S.make_signals(daily, {"가": "000010", "나": "000020", "다": "000030"}, top=2,
                             exclude=lambda c, n: "KODEX" in n)
        self.assertEqual([(s["name"], s["rank"]) for s in sig], [("가", 2)])   # 나 +4%, 다 순위밖, ETF 제외
        g = {"new": [("000010", "가")], "업종2_반도체": [("000010", "가"), ("000020", "나")]}
        self.assertEqual(S.universe("000010", g), "NEW그룹")
        self.assertEqual(S.universe("000020", g), "관심그룹(NEW외)")
        self.assertEqual(S.universe("000030", g), "관심그룹 밖")


if __name__ == "__main__":
    unittest.main()
