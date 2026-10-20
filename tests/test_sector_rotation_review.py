"""반도체 쉼 구간 순환 섹터 — 섹터 평균(상장 전 제외), 구간별 1등·섹터 점수."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backtest"))
import sector_rotation_review as S  # noqa: E402


class Rotation(unittest.TestCase):
    def test_sector_ret_and_table(self):
        S.SECTORS = {"A": {"a1": "", "a2": ""}, "B": {"b1": ""}}
        data = {"KS11": {"d1": 100, "d2": 95}, "091160": {"d1": 100, "d2": 80},
                "a1": {"d1": 10, "d2": 11}, "a2": {"d2": 50},            # a2는 d1에 상장 전 → 빠짐
                "b1": {"d1": 10, "d2": 9}}
        self.assertAlmostEqual(S.sector_ret(data, S.SECTORS["A"], "d1", "d2"), 10.0)
        t = S.table(data, [("d1", "d2")])
        self.assertEqual(t["rows"][0][-1], "A")
        self.assertEqual(t["score"]["A"][:1] + t["score"]["B"][:1], [1, 0])
        self.assertEqual((t["score"]["A"][2], t["score"]["B"][2]), (100.0, 0.0))     # 코스피(-5%)보다 나음 비율


if __name__ == "__main__":
    unittest.main()
