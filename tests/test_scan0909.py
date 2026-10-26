"""090930타점 파이썬판 — !A(첫 분봉 음봉)·B(어제 음봉→지금 시가 위)·C(5≥20 이평)·D(1분 1억)·E(60봉 평균 이상)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "intelligence"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "core"))
import scan0909 as S  # noqa: E402


def bar(t, price, vol, acml):
    return {"time": t, "price": price, "volume": vol, "acml_value": acml}


class Checks(unittest.TestCase):
    def test_ma(self):
        self.assertTrue(S.ma_ok([100] * 19, 120)[0])                    # 지금가 오르면 5일선이 위
        self.assertFalse(S.ma_ok([120] * 15 + [100] * 4, 100)[0])
        self.assertFalse(S.ma_ok([100] * 10, 100)[0])                   # 일봉 부족

    def test_cross(self):                                               # 10-26 루닛: 시가 12,450 · 저가 12,150 · 지금 13,580
        self.assertTrue(S.cross_ok(12150, 13580, 12450))
        self.assertFalse(S.cross_ok(12500, 13580, 12450))              # 시가 밑으로 안 내려감
        self.assertFalse(S.cross_ok(12150, 12400, 12450))              # 아직 시가 아래

    def test_bars_pass(self):
        today = [bar("090000", 990, 100, 1e8), bar("090100", 995, 100, 2e8), bar("090200", 998, 100, 3e8),
                 bar("090300", 1010, 300, 4.5e8)]                        # 시가 1000: 첫 봉 990(음봉) → 지금 1010 재돌파
        prev = [bar(f"15{i:02d}00", 1000, 100, 0) for i in range(30)]
        r = S.bar_checks(today, prev, 1000)
        self.assertEqual(r["fails"], []); self.assertAlmostEqual(r["value_1m"], 1.5e8)
        self.assertAlmostEqual(r["vol_ratio"], 300.0)

    def test_bars_fail(self):
        today = [bar("090000", 1005, 100, 1e8), bar("090100", 1006, 100, 1.5e8), bar("090200", 1010, 50, 1.6e8)]
        f = " ".join(S.bar_checks(today, [], 1000)["fails"])
        self.assertIn("A첫분봉", f); self.assertIn("D1분대금", f); self.assertIn("E거래량", f)


if __name__ == "__main__":
    unittest.main()
