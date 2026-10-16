"""상황 판별기 — 상승장/박스권/하락장/투매 분류, 봇 매도를 상황별로 나눠 승률·PF."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backtest"))
import situation_classifier as SC  # noqa: E402


class Classify(unittest.TestCase):
    def test_capitulation_overrides_breadth(self):
        self.assertEqual(SC.classify(80.0, 45.0), "투매")   # 신저가 40%+ 면 지수 좋아도 투매

    def test_breadth_bands(self):
        self.assertEqual(SC.classify(20.0, 5.0), "하락장")
        self.assertEqual(SC.classify(50.0, 5.0), "박스권")
        self.assertEqual(SC.classify(70.0, 5.0), "상승장")
        self.assertEqual(SC.classify(34.9, 0.0), "하락장")
        self.assertEqual(SC.classify(35.0, 0.0), "박스권")
        self.assertEqual(SC.classify(64.9, 0.0), "박스권")
        self.assertEqual(SC.classify(65.0, 0.0), "상승장")

    def test_missing_breadth_is_unknown(self):
        self.assertEqual(SC.classify(None, 10.0), "모름")


class Pnl(unittest.TestCase):
    def test_pnl_stats(self):
        rows = [("2026-01-01", 10000, 5.0), ("2026-01-02", -4000, -3.0), ("2026-01-03", -6000, -4.0)]
        s = SC.pnl_stats(rows)
        self.assertEqual(s["n"], 3); self.assertEqual(s["pf"], 1.0); self.assertEqual(s["net"], 0)
        self.assertAlmostEqual(s["win_rate"], 100 / 3)

    def test_empty(self):
        self.assertEqual(SC.pnl_stats([]), {"n": 0, "win_rate": 0.0, "pf": 0.0, "net": 0.0})

    def test_by_situation_groups_by_label(self):
        sells = [("2026-01-01", 10000, 5.0), ("2026-01-02", -4000, -3.0)]
        labels = {"2026-01-01": "상승장", "2026-01-02": "하락장"}
        out = SC.by_situation(sells, labels)
        self.assertEqual(set(out), {"상승장", "하락장"})
        self.assertEqual(out["상승장"]["net"], 10000)


if __name__ == "__main__":
    unittest.main()
