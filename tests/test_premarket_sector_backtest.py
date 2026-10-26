"""프리장 섹터 백테스트 — 갭 구간, 테마 상위 대장~3등주 고르기, 본장 결과 계산."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backtest"))
import premarket_sector_backtest as P  # noqa: E402


class Backtest(unittest.TestCase):
    def test_bucket(self):
        self.assertEqual(P.bucket(-0.5), (-1, 1))
        self.assertEqual(P.bucket(-3.0), (-3, -1))
        self.assertEqual(P.bucket(-7), (-99, -5))

    def test_day_picks(self):
        themes = {"로봇": [("A", "가"), ("B", "나"), ("C", "다"), ("D", "라")],
                  "전지": [("E", "마"), ("F", "바"), ("G", "사")], "둘": [("H", "아"), ("I", "자")]}
        chg = {"A": 5, "B": 9, "C": -1, "D": 2, "E": 1, "F": 0.5, "G": -3, "H": 30, "I": 30}
        picks = P.day_picks(themes, chg, 2)
        self.assertEqual([(c, tr, sr) for c, _, tr, sr in picks],
                         [("B", 1, 1), ("A", 1, 2), ("D", 1, 3), ("E", 2, 1), ("F", 2, 2)])   # 내린 C·G 빠짐, 2종목 테마 빠짐

    def test_outcome_and_stats(self):
        o = P.outcome(10000, 9800, 10100, 9700, 10050)
        self.assertAlmostEqual(o["gap"], -2.0)
        self.assertTrue(o["up"]); self.assertTrue(o["hit"]); self.assertTrue(o["above_pre"])
        s = P.stats([o, P.outcome(10000, 10000, 10100, 9500, 9600)])
        self.assertEqual((s["n"], s["up"], s["hit"]), (2, 50.0, 50.0))


if __name__ == "__main__":
    unittest.main()
