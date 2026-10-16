"""하락 구간 전환점 — 구간 찾기·설명·리포트."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backtest"))
from _helpers import stub  # noqa: E402
stub("three_month_leader", THEME_DB="")
import turning_point as T  # noqa: E402


def series():
    """25일 100 → 6일 하락(저점 70, 5일째 투매) → 반등(5일째에 76=+8.6%) → 한 번 더 낮게(재시험 71) → 상승."""
    cl = [100.0] * 25 + [95, 90, 82, 75, 70, 72, 74, 76, 73, 71, 74, 78, 80, 82, 84, 86, 88, 90, 91, 92, 93]
    return [(f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}", c) for i, c in enumerate(cl)]


class TP(unittest.TestCase):
    def test_episode(self):
        s = series()
        eps = T.episodes(s)
        self.assertEqual(len(eps), 1)
        ep = eps[0]
        self.assertEqual(s[ep["trough_i"]][1], 70)
        self.assertEqual(s[ep["end_i"]][1], 76)               # 저점 대비 +8% 첫날
        lows = {s[28][0]: 60.0, s[29][0]: 40.0}                # 저점 하루 전이 최대 투매
        x = T.describe(s, ep, lows, {s[29][0]: 12.0})
        self.assertEqual(x["cap_to_trough"], 1); self.assertTrue(x["retest"])
        self.assertAlmostEqual(x["dd"], -30.0)
        out = T.report(s, lows, {s[29][0]: 12.0})
        self.assertIn("최대 투매일", out); self.assertIn("저점 날 20일선 위 12%", out); self.assertIn("요약(끝난 1구간)", out)

    def test_ongoing_and_short(self):
        s = series()[:31]                                       # 저점 뒤 반등 전 — 진행 중
        self.assertIn("진행 중", T.report(s, {}, {}))
        self.assertIn("모자라", T.report(s[:10], {}, {}))


if __name__ == "__main__":
    unittest.main()
