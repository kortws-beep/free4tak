"""관심그룹 재편 — 상관계수, 평균연결 군집(크기 상한·상관 하한), 중복·따로 노는 종목."""
import random
import unittest

from _helpers import use_temp_cwd

_tmp = use_temp_cwd()
import regroup_watchlist as R  # noqa: E402


def series(factor, noise, seed, n=100):
    rnd = random.Random(seed)
    px, out = 100.0, []
    for i in range(n):
        px *= 1 + factor[i] + rnd.gauss(0, noise)
        out.append((f"d{i:03d}", px, 1e9 * (1 + seed % 5)))
    return out


class Regroup(unittest.TestCase):
    def setUp(self):
        rnd = random.Random(7)
        semi = [rnd.gauss(0, 0.02) for _ in range(100)]
        power = [rnd.gauss(0, 0.02) for _ in range(100)]
        self.prices = {f"S{k}": series(semi, 0.006, k) for k in range(5)}
        self.prices.update({f"P{k}": series(power, 0.006, 10 + k) for k in range(4)})
        self.prices["L0"] = series([0] * 100, 0.02, 99)                   # 혼자 움직임
        self.prices["N0"] = series(semi, 0.006, 50)[:20]                  # 일봉 부족
        self.groups = {"업종2_반도체": [("S0", "반0"), ("S1", "반1"), ("S2", "반2"), ("P0", "전0"), ("L0", "혼자")],
                       "업종6_전력": [("P0", "전0"), ("P1", "전1"), ("P2", "전2"), ("P3", "전3")],
                       "new": [("S3", "반3"), ("S4", "반4"), ("N0", "신규")]}
        self.themes = {f"S{k}": ["반도체"] for k in range(5)}

    def _r(self, k):
        return R.returns([(d, c) for d, c, _ in self.prices[k]])

    def test_corr(self):
        a, b, c = self._r("S0"), self._r("S1"), self._r("P0")
        self.assertGreater(R.corr(a, b), 0.8)
        self.assertLess(abs(R.corr(a, c)), 0.4)
        self.assertIsNone(R.corr(a, self._r("N0")))       # 겹치는 날 부족

    def test_analyze(self):
        res = R.analyze(self.groups, self.prices, self.themes, max_size=12, min_corr=0.35)
        sets = [set(c["members"]) for c in res["clusters"] if len(c["members"]) >= 2]
        self.assertIn({"S0", "S1", "S2", "S3", "S4"}, sets)
        self.assertIn({"P0", "P1", "P2", "P3"}, sets)
        semi = next(c for c in res["clusters"] if "S0" in c["members"])
        self.assertTrue(semi["name"].startswith("반도체")); self.assertIn("지금 업종2_반도체", semi["name"])
        self.assertIn("P0", res["dups"]); self.assertNotIn("S3", res["dups"])      # NEW 중복은 안 셈
        self.assertEqual(res["missing"], ["N0"])
        self.assertIn(("업종2_반도체", "L0"), [(g, c) for g, c, _ in res["loners"]])
        out = R.report(res)
        self.assertIn("혼자 움직이는 종목 1개", out); self.assertIn("👑", out)

    def test_size_cap(self):
        res = R.analyze(self.groups, self.prices, self.themes, max_size=3, min_corr=0.35)
        self.assertTrue(all(len(c["members"]) <= 3 for c in res["clusters"]))
        R.save_csv(res, "out.csv")
        with open("out.csv", encoding="utf-8-sig") as f:
            self.assertIn("묶음이름", f.readline())


if __name__ == "__main__":
    unittest.main()
