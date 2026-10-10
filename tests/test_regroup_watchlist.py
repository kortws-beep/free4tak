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
        self.themes.update({f"P{k}": ["전력설비", "원전"] for k in range(4)})
        self.themes["S0"] = ["반도체", "원전"]                 # 두 테마에 걸린 종목
        self.themes["L0"] = ["원전"]

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

    def test_theme_assign(self):
        res = R.analyze(self.groups, self.prices, self.themes, max_size=12, min_corr=0.35)
        bt = {t: sorted(c for c, _ in ms) for t, ms in res["by_theme"].items()}
        self.assertEqual(bt["반도체"], ["S0", "S1", "S2", "S3", "S4"])      # S0는 원전보다 반도체와 같이 움직임
        self.assertNotIn("S0", [c for c, _ in res["by_theme"].get("원전", [])])
        self.assertIn("L0", res["loose"])                                  # 원전 테마지만 따로 놂
        self.assertIn("주달 테마 기준 재편안", R.report(res))

    def test_specific_name(self):
        themes = {"A": ["반도체", "HBM"], "B": ["반도체", "HBM"], "C": ["반도체"]}
        themes.update({f"X{k}": ["반도체"] for k in range(20)})            # 반도체는 큰 테마
        sizes = R.theme_sizes(themes)
        self.assertEqual(R.name_cluster(["A", "B", "C"], themes, {}, sizes), "HBM")   # 3개 중 2개 = 절반 이상
        self.assertEqual(R.name_cluster(["A", "C", "X0", "X1"], themes, {}, sizes), "반도체")

    def test_group_fit_and_placement(self):
        res = R.analyze(self.groups, self.prices, self.themes, max_size=12, min_corr=0.35)
        f = res["fits"]["P0"]                                   # 반도체·전력 둘 다 들어 있는 전력주
        self.assertGreater(f["업종6_전력"], f["업종2_반도체"] + 0.3)
        self.assertEqual(R.placement(f, ["업종2_반도체", "업종6_전력"]), ("주 그룹만", "업종6_전력"))
        self.assertEqual(R.placement({"a": 0.6, "b": 0.55}, ["a", "b"]), ("둘 다 유지", "a"))
        self.assertEqual(R.placement({"a": 0.2, "b": 0.5}, ["a"]), ("옮길 후보", "b"))
        self.assertEqual(R.placement({"a": 0.45, "b": 0.5}, ["a"])[0], "")
        out = R.report(res)
        self.assertIn("→ 주 그룹 업종6_전력", out)

    def test_min_days_relative(self):
        short = {k: v[:60] for k, v in self.prices.items()}       # 60일치만(일부 결측 가정)
        short["S1"] = short["S1"][:55]
        res = R.analyze(self.groups, short, self.themes)
        self.assertNotIn("S1", res["missing"])                    # 55 ≥ 60×0.8
        self.assertIn("N0", res["missing"])                        # 20개는 빠짐

    def test_proposal_and_cohesion(self):
        prop = R.parse_proposal("# 주석\n반도체: 반0, 반1, 반2\n섞임: 반3, 전1, 혼자\n오타: 없는종목\n")
        self.assertEqual(prop["반도체"], ["반0", "반1", "반2"])
        names = {c: n for v in self.groups.values() for c, n in v}
        pg, unknown = R.resolve_proposal(prop, {n: c for c, n in names.items()})
        self.assertEqual(unknown, ["오타:없는종목"])
        res = R.analyze(self.groups, self.prices, self.themes)
        rows = R.cohesion(pg, res["codes"], res["sim"])
        self.assertEqual([g for g, *_ in rows], ["섞임", "반도체"])          # 결속 낮은 순
        self.assertGreater(rows[1][2], 0.8); self.assertLess(rows[0][2], 0.4)
        self.assertIn("⚠️섞임", R.format_cohesion(rows, names, "t"))

    def test_size_cap(self):
        res = R.analyze(self.groups, self.prices, self.themes, max_size=3, min_corr=0.35)
        self.assertTrue(all(len(c["members"]) <= 3 for c in res["clusters"]))
        R.save_csv(res, "out.csv")
        with open("out.csv", encoding="utf-8-sig") as f:
            self.assertIn("묶음이름", f.readline())


if __name__ == "__main__":
    unittest.main()
