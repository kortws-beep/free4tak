"""11-03 MTN 아침 방송 요약 — 녹음 길이 계산·요약 지시문."""
import datetime
import unittest

from _helpers import use_temp_cwd

_tmp = use_temp_cwd()
import mtn_morning_brief as M  # noqa: E402


class Brief(unittest.TestCase):
    def test_seconds_until(self):
        at = lambda h, m: datetime.datetime(2026, 11, 3, h, m)  # noqa: E731
        self.assertEqual(M.seconds_until("0736", at(6, 55)), 41 * 60)
        self.assertEqual(M.seconds_until("0736", at(7, 40)), 0)          # 이미 지남
        self.assertEqual(M.seconds_until("0736", at(6, 0)), M.MAX_SEC)   # 상한

    def test_prompt(self):
        p = M.build_prompt("받아쓰기", "2026-11-03")
        self.assertIn("2026-11-03", p); self.assertIn("지어내지 마", p); self.assertIn("받아쓰기", p); self.assertIn("내용", p)


if __name__ == "__main__":
    unittest.main()
