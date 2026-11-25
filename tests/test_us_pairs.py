"""미국 짝꿍(us_pairs) — 마지막 두 종가 등락·⭐/🔻 기준·분야 평균·같은 미국 날짜만."""
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "intelligence"))
import us_pairs as U  # noqa: E402


def chart(closes, ts0=1763942400):   # 2025-11-24 00:00 UTC 근처, 하루 간격
    return {"chart": {"result": [{"meta": {"gmtoffset": -18000}, "timestamp": [ts0 + 86400 * i + 14 * 3600 for i in range(len(closes))],
                                  "indicators": {"quote": [{"close": closes}]}}]}}


class Pairs(unittest.TestCase):
    def test_mark(self):
        self.assertEqual((U.mark(2.0), U.mark(-2.0), U.mark(1.9), U.mark(None)), ("⭐", "🔻", "", ""))

    def test_last_move(self):
        self.assertIsNone(U.last_move([("d", 1.0)]))
        d, p = U.last_move([("a", 100.0), ("b", 95.0)])
        self.assertEqual(d, "b")
        self.assertAlmostEqual(p, -5.0)

    def test_signals_group_avg(self):
        px = {"NVDA": [100, 90], "MU": [100, 80], "SKHY": [100, 110]}
        def get(url):
            t = url.split("/chart/")[1].split("?")[0]
            return chart(px.get(t, [100, 100]))
        old = U.PAIRS
        U.PAIRS = {"S7 대형주": ["NVDA", "MU", "SKHY"]}
        try:
            s = U.signals(get=get)
        finally:
            U.PAIRS = old
        g = s["groups"]["S7 대형주"]
        self.assertAlmostEqual(g["avg"], -6.67, places=2)
        self.assertEqual(g["mark"], "🔻")
        self.assertTrue(g["reliable"])

    def test_daily_fills_missing_last_close(self):
        c = chart([100.0, None])
        r = c["chart"]["result"][0]
        r["meta"]["regularMarketPrice"] = 90.0
        r["meta"]["regularMarketTime"] = r["timestamp"][-1] + 3600     # 같은 날 장 마감 시각
        rows = U.daily("X", get=lambda url: c)
        self.assertEqual([p for _, p in rows], [100.0, 90.0])
        self.assertAlmostEqual(U.last_move(rows)[1], -10.0)

    def test_pairs_groups_exist_in_reliable(self):
        self.assertTrue(U.RELIABLE <= set(U.PAIRS))


if __name__ == "__main__":
    unittest.main()
