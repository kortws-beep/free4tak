"""투매일 섹터 고르기 — 그룹 점수(오후반등·낙폭), 목표 도달, 리포트."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backtest"))
from _helpers import stub  # noqa: E402
stub("three_month_leader", THEME_DB="")
import capitulation_sector as CS  # noqa: E402


def mk():
    """25일 상승 → 26~29일 하락(29일 투매: 저가 깊게), 30일 반등.
    그룹 '이차': 덜 빠지고 투매날 저가→종가 +8%, 다음날 고가 +9%. 그룹 '부품': 많이 빠지고 +2%, 다음날 +1%."""
    dates = [f"2026-08-{i:02d}" for i in range(1, 31)]
    daily = {}

    def series(drop, aft, nxt_hi, val):
        p, rows = 100.0, []
        for i, d in enumerate(dates):
            o = p
            if i < 25:
                p *= 1.01; rows.append((d, o, p, o * 0.995, p, val))
            elif i < 28:
                p *= 1 + drop; rows.append((d, o, o, p * 0.99, p, val))
            elif i == 28:
                lo = o * 0.9; p = lo * (1 + aft); rows.append((d, o, o, lo, p, val))
            else:
                rows.append((d, p, p * (1 + nxt_hi), p * 0.99, p * (1 + nxt_hi / 2), val))
        return rows
    for k in range(60):
        daily[f"시장{k}"] = series(-0.03, 0.03, 0.02, 1000 + k)
    for k in range(3):
        daily[f"이차{k}"] = series(-0.01, 0.08, 0.09, 5000 + k)
        daily[f"부품{k}"] = series(-0.05, 0.02, 0.01, 5000 + k)
    groups = {"이차": [(str(k), f"이차{k}") for k in range(3)], "부품": [(str(k), f"부품{k}") for k in range(3)],
              "new": [("9", "이차0"), ("8", "부품0"), ("7", "시장0")]}
    return daily, groups, dates


class CapSector(unittest.TestCase):
    def test_pipeline(self):
        daily, groups, dates = mk()
        rows = {n: {r[0]: r[1:6] for r in rs} for n, rs in daily.items()}
        sc = CS.score_groups(groups, rows, dates[24], dates[28], dates[29])
        self.assertEqual([x["group"] for x in sc], ["이차", "부품"])          # new는 제외
        self.assertAlmostEqual(sc[0]["aft"], 8.0, places=4)
        h = CS.hits(sc[0]["members"])
        self.assertEqual((h[3], h[5], h[7]), (100.0, 100.0, 100.0))
        self.assertEqual(CS.hits(sc[1]["members"])[3], 0.0)
        out = CS.report(daily, groups, nl=30)
        self.assertIn("1등 [이차]", out); self.assertIn("꼴찌 [부품]", out)
        self.assertIn("결합 1등", out)
        self.assertIn("관심그룹(", CS.report(daily, {}, nl=30))


if __name__ == "__main__":
    unittest.main()
