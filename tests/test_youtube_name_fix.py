"""유튜브 종목명 보정 — 영문 약칭은 편집거리로 딴 종목이 되면 안 됨(11-23: HVM→HMM 해운으로 오보정)."""
import os
import sqlite3
import sys
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in ("intelligence", "core"):
    sys.path.insert(0, os.path.join(_ROOT, _d))
import youtube_stock_monitor as Y  # noqa: E402


class NameFix(unittest.TestCase):
    def setUp(self):
        fd, self.db = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        c = sqlite3.connect(self.db)
        c.execute("CREATE TABLE kr_theme_stocks (stock_name TEXT)")
        c.execute("CREATE TABLE kr_stock_master (name TEXT)")
        c.executemany("INSERT INTO kr_theme_stocks VALUES (?)",
                      [("에이치브이엠KOSDAQ 우주",), ("HMMKOSPI 해운",), ("비나텍KOSDAQ 2차전지",)])
        c.executemany("INSERT INTO kr_stock_master VALUES (?)", [("에이치브이엠",), ("HMM",), ("비나텍",)])
        c.commit()
        c.close()
        self.old = (Y.THEME_DB, Y._get_all_stock_names)
        Y.THEME_DB = self.db
        Y._get_all_stock_names = lambda: ["에이치브이엠", "HMM", "비나텍"]

    def tearDown(self):
        Y.THEME_DB, Y._get_all_stock_names = self.old
        os.remove(self.db)

    def test_hvm_alias(self):
        self.assertEqual(Y.validate_stock_name("HVM"), "에이치브이엠")

    def test_ascii_no_fuzzy(self):
        self.assertEqual(Y.validate_stock_name("HNM"), "")      # HMM으로 바꾸면 안 됨

    def test_korean_fuzzy_kept(self):
        self.assertEqual(Y.validate_stock_name("비나택"), "비나텍")


if __name__ == "__main__":
    unittest.main()
