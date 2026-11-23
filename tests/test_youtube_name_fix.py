"""유튜브 종목명 보정 — 영문 약칭은 편집거리로 딴 종목이 되면 안 됨(11-23: HVM→HMM 해운으로 오보정)."""
import os
import sqlite3
import sys
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in ("intelligence", "core"):
    sys.path.insert(0, os.path.join(_ROOT, _d))
import stock_name_alias as A  # noqa: E402
import youtube_stock_monitor as Y  # noqa: E402


class AutoAlias(unittest.TestCase):
    def test_to_alias(self):
        self.assertEqual(A.to_alias("에이치브이엠"), "HVM")
        self.assertEqual(A.to_alias("케이엠더블유"), "KMW")
        self.assertEqual(A.to_alias("한국피아이엠"), "한국PIM")
        self.assertEqual(A.to_alias("삼성에스디에스"), "삼성SDS")
        self.assertEqual(A.to_alias("바이오니아"), "")          # 모음만 덩어리(EO)는 안 바꿈

    def test_build_real_name_wins_and_dups_dropped(self):
        t = A.build(["에이치엠엠", "HMM", "에이치브이엠", "에스케이", "에스캐이"])
        self.assertNotIn("HMM", t)                              # 진짜 HMM이 있으니 자동 약칭 버림
        self.assertEqual(t["HVM"], "에이치브이엠")


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
        self.old = (Y.THEME_DB, Y._get_all_stock_names, Y._AUTO_ALIAS_CACHE)
        Y._AUTO_ALIAS_CACHE = None
        Y.THEME_DB = self.db
        Y._get_all_stock_names = lambda: ["에이치브이엠", "HMM", "비나텍"]

    def tearDown(self):
        Y.THEME_DB, Y._get_all_stock_names, Y._AUTO_ALIAS_CACHE = self.old
        os.remove(self.db)

    def test_hvm_alias(self):
        self.assertEqual(Y.validate_stock_name("HVM"), "에이치브이엠")

    def test_auto_alias_from_master(self):
        c = sqlite3.connect(self.db)
        c.execute("INSERT INTO kr_stock_master VALUES ('케이엠더블유')")
        c.execute("INSERT INTO kr_theme_stocks VALUES ('케이엠더블유KOSDAQ 통신')")
        c.commit()
        c.close()
        self.assertEqual(Y.validate_stock_name("KMW"), "케이엠더블유")

    def test_ascii_no_fuzzy(self):
        self.assertEqual(Y.validate_stock_name("HNM"), "")      # HMM으로 바꾸면 안 됨

    def test_korean_fuzzy_kept(self):
        self.assertEqual(Y.validate_stock_name("비나택"), "비나텍")


if __name__ == "__main__":
    unittest.main()
