"""11-03 MTN 아침 방송 요약 — 녹음 길이 계산·요약 지시문."""
import datetime
import unittest

from _helpers import use_temp_cwd

_tmp = use_temp_cwd()
import mtn_morning_brief as M  # noqa: E402


class Brief(unittest.TestCase):
    def test_seconds_until(self):
        at = lambda h, m: datetime.datetime(2026, 11, 3, h, m)  # noqa: E731
        self.assertEqual(M.seconds_until(M.END_HHMM, at(6, 55)), 46 * 60)
        self.assertEqual(M.seconds_until("0736", at(7, 40)), 0)          # 이미 지남
        self.assertEqual(M.seconds_until("0736", at(6, 0)), M.MAX_SEC)   # 상한

    def test_prompt(self):
        p = M.build_prompt("받아쓰기", "2026-11-03")
        self.assertIn("2026-11-03", p); self.assertIn("지어내지 마", p); self.assertIn("받아쓰기", p); self.assertIn("내용", p); self.assertIn("오늘 주목", p)


    def test_split(self):
        msg = "\n\n".join(["가" * 700] * 4)                            # 2800자+ → 섹션 경계에서 나눔
        parts = M.split_msg(msg)
        self.assertEqual(len(parts), 2); self.assertTrue(all(len(p) <= 1800 for p in parts))
        self.assertEqual(M.split_msg("짧음"), ["짧음"])
        self.assertTrue(all(len(p) <= 1800 for p in M.split_msg("나" * 4000)))   # 빈 줄 없는 긴 덩어리도


    def test_save_featured(self):
        summ = "**🧭 방송의 시각**\n- 흐름\n\n**🔦 오늘 주목**\n- 전력기기: 수주잔고 역대 최고\n\n**⚠️ 오늘 일정·변수**\n- 선거"
        self.assertEqual(M.featured_section(summ), "- 전력기기: 수주잔고 역대 최고")
        self.assertEqual(M.featured_section("주목 칸 없음"), "")
        import sqlite3
        M.save_day("2026-11-03", summ, db="m.db"); M.save_day("2026-11-03", summ, db="m.db")   # 같은 날 다시 돌려도 한 줄
        rows = sqlite3.connect("m.db").execute("SELECT date, featured FROM mtn_morning").fetchall()
        self.assertEqual(rows, [("2026-11-03", "- 전력기기: 수주잔고 역대 최고")])


if __name__ == "__main__":
    unittest.main()
