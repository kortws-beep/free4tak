"""테마 요약 — sector_monitor 기록에서 자주 뜬 테마·주도 종목, 관심그룹 표시."""
import os
import sqlite3
import unittest

from _helpers import use_temp_cwd

_tmp = use_temp_cwd()
import theme_digest as D  # noqa: E402


def build():
    db = os.path.abspath("sm.db")
    if os.path.exists(db):
        os.remove(db)
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE sector_flow (ts TEXT, theme_cd TEXT, theme_nm TEXT, flu_rt REAL)")
    c.execute("CREATE TABLE stock_momentum (ts TEXT, code TEXT, theme_nm TEXT, change_rate REAL, trde_amt REAL)")
    for day in ("2026-10-05", "2026-10-06"):
        for mm in range(3):
            c.execute("INSERT INTO sector_flow VALUES (?, 't1', '우주항공', 5.0)", (f"{day} 09:0{mm}",))
            c.execute("INSERT INTO stock_momentum VALUES (?, '100001', '우주항공', 8.0, 300)", (f"{day} 09:0{mm}:00",))
            c.execute("INSERT INTO stock_momentum VALUES (?, '100002', '우주항공', 4.0, 100)", (f"{day} 09:0{mm}:30",))
    c.execute("INSERT INTO sector_flow VALUES ('2026-10-06 10:00', 't2', '화장품', 2.0)")
    c.execute("INSERT INTO sector_flow VALUES ('2026-09-01 10:00', 't3', '옛날테마', 9.0)")
    c.commit(); c.close()
    return db


class Digest(unittest.TestCase):
    def test_digest(self):
        rows, n, since = D.digest(2, build())
        self.assertEqual((n, since), (2, "2026-10-05"))
        self.assertEqual([r["theme"] for r in rows], ["우주항공", "화장품"])     # 옛날 테마는 기간 밖
        self.assertEqual((rows[0]["days"], rows[0]["minutes"]), (2, 6))
        self.assertEqual(rows[0]["stocks"][0]["code"], "100001")
        txt = D.format_digest(rows, n, since, {"100001": "우주대장"}, in_groups={"100001"})
        self.assertIn("⭕우주대장(300억)", txt); self.assertIn("➕100002(100억)", txt)

    def test_empty(self):
        db = os.path.abspath("empty.db")
        c = sqlite3.connect(db)
        c.execute("CREATE TABLE IF NOT EXISTS sector_flow (ts TEXT, theme_nm TEXT, flu_rt REAL)")
        c.execute("CREATE TABLE IF NOT EXISTS stock_momentum (ts TEXT, code TEXT, theme_nm TEXT, change_rate REAL, trde_amt REAL)")
        c.commit(); c.close()
        self.assertEqual(D.digest(10, db), ([], 0, None))


if __name__ == "__main__":
    unittest.main()
