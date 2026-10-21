"""봇 기능 지도 — 최근 7일 🆕 표시, 한글 변경 이력 읽기(최신 위·대상 색), 화면 이동 탭."""
import os
import sys
import tempfile
import datetime
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "interface"))
import bot_overview as B  # noqa: E402


class Overview(unittest.TestCase):
    def test_new_flag(self):
        inv = B.inventory(datetime.date(2026, 10, 22))
        sbot = next(b for b in inv if b["id"] == "sbot")
        items = {i["text"]: i for g in sbot["groups"] for i in g["items"]}
        self.assertTrue(any(i["new"] for i in items.values()))
        self.assertFalse(items["손절 ATR×2(최대 -10%) · 물타기 안 함"]["new"])
        self.assertFalse(B._is_new("2026-10-12", datetime.date(2026, 10, 22)))

    def test_changelog(self):
        p = os.path.join(tempfile.mkdtemp(), "c.md")
        with open(p, "w", encoding="utf-8") as f:
            f.write("# 제목\n- 2026-10-20 | sbot | 옛것\n잡문\n- 2026-10-22 | 리나 | 새것 | 파이프 포함\n")
        c = B.changelog(p)
        self.assertEqual([x["text"] for x in c], ["새것 | 파이프 포함", "옛것"])
        self.assertEqual(c[1]["color"], B.TARGETS["sbot"][0])

    def test_real_changelog_parses(self):
        c = B.changelog()
        self.assertTrue(c and all(x["target"] in B.TARGETS for x in c))          # 대상 이름 오타 방지

    def test_nav_links(self):
        for p in ("lina", "feed", "bots"):
            self.assertIn(f'href="{p}"', B.NAV)


if __name__ == "__main__":
    unittest.main()
