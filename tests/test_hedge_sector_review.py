"""헤지 후보 검증 — 하락 구간(알아챔/저점/끝), 쉬는 날 흉내(전날 20일선 -4%), 하루씩 보유 수익, 앞뒤 절반 판정."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backtest"))
import hedge_sector_review as H  # noqa: E402


class Episodes(unittest.TestCase):
    def test_detect_trough_end(self):
        closes = [100.0] * 25 + [95, 91, 90, 92, 97.3, 99]
        e = H.episodes(closes)[0]
        self.assertEqual((e["peak"], e["detect"], e["trough"], e["end"]), (6, 26, 27, 29))   # 고점=직전 20일 중 첫 최고, 91 ≤ 100×0.92, 90×1.08=97.2

    def test_pause_days_and_hold(self):
        closes = [100.0] * 20 + [95, 94]
        dates = [f"d{i:02d}" for i in range(len(closes))]
        self.assertEqual(H.pause_days(dates, closes), [("d20", "d21")])                       # 95가 20일 평균 100의 -5%
        self.assertAlmostEqual(H.hold_on({"d20": 100, "d21": 110}, [("d20", "d21"), ("x", "y")])[0], 10.0)

    def test_judge_halves(self):
        self.assertTrue(H.judge([1, 2, 3, 4])["ok"])
        self.assertFalse(H.judge([-5, 1, 3, 4])["ok"])
        self.assertFalse(H.judge([1, 2])["ok"])                                                # 4개 미만

    def test_ret_skips_before_listing(self):
        px = {"2020-01-02": 100, "2020-01-10": 120}
        self.assertIsNone(H.ret(px, "2019-12-01", "2020-01-10"))
        self.assertAlmostEqual(H.ret(px, "2020-01-02", "2020-01-31"), 20.0)


if __name__ == "__main__":
    unittest.main()
