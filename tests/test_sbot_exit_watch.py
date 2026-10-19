"""sbot 기존 보유종목 탈출 감시 — +5% 반등 알림, 고점 -4% 꺾임 뒤 새 기준가, -5%씩 추가 하락 알림(반복 없음)."""
import os
import sys
import unittest

from _helpers import use_temp_cwd

_tmp = use_temp_cwd()
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "intelligence"))
import sbot_exit_watch as W  # noqa: E402


class Evaluate(unittest.TestCase):
    def test_rebound_trail_rebase(self):
        it = {"base": 10000, "armed": False, "peak": None, "drop_lv": 0}
        self.assertEqual(W.evaluate(it, "HL만도", 10400, 13000, 10), [])
        m = W.evaluate(it, "HL만도", 10500, 13000, 10)
        self.assertIn("30% 정리 검토", m[0]); self.assertIn("-25,000원", m[0])         # (10500-13000)*10
        self.assertEqual(W.evaluate(it, "HL만도", 11000, 13000, 10), [])              # 고점 갱신
        m = W.evaluate(it, "HL만도", 10560, 13000, 10)                                  # 11000×0.96
        self.assertIn("반등 꺾임", m[0])
        self.assertEqual((it["base"], it["armed"]), (10560, False))                  # 새 기준가로 다시 지켜봄

    def test_drop_levels_once_each(self):
        it = {"base": 10000, "armed": False, "peak": None, "drop_lv": 0}
        self.assertIn("추가 하락", W.evaluate(it, "로보티즈", 9500, 13000, 5)[0])
        self.assertEqual(W.evaluate(it, "로보티즈", 9400, 13000, 5), [])               # 같은 단계 반복 안 함
        self.assertIn("-10.0%", W.evaluate(it, "로보티즈", 9000, 13000, 5)[0])


if __name__ == "__main__":
    unittest.main()
