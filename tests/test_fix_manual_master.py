"""수동매도 master 누락 채우기 스크립트."""
import os
import sqlite3
import sys
import unittest

from _helpers import use_temp_cwd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "intelligence"))
_tmp = use_temp_cwd()
import fix_manual_master as F  # noqa: E402


class FixManualMaster(unittest.TestCase):
    def test_plan_and_apply(self):
        m = sqlite3.connect("m.db")
        m.execute("CREATE TABLE master_trades (id INTEGER PRIMARY KEY, bot_type TEXT, code TEXT, stock_name TEXT, "
                  "buy_price REAL, buy_time TEXT, sell_price REAL, sell_time TEXT, qty REAL, profit_rate REAL, "
                  "profit_krw REAL, sell_reason TEXT, is_partial INTEGER)")
        m.execute("INSERT INTO master_trades (bot_type, code, sell_time) VALUES ('daybot','B','2026-10-13 10:00:30')")
        m.commit(); m.close()
        d = sqlite3.connect("d.db")
        d.execute("CREATE TABLE trades (code TEXT, stock_name TEXT, buy_price REAL, buy_time TEXT, sell_price REAL, "
                  "sell_time TEXT, qty INTEGER, sell_reason TEXT)")
        d.executemany("INSERT INTO trades VALUES (?,?,?,?,?,?,?,?)", [
            ("A", "가온전선", 10000, "2026-10-13T09:05:00", 10500, "2026-10-13T13:00:00", 10, "수동매도"),
            ("B", "이미있음", 10000, "x", 9000, "2026-10-13T10:00:00", 10, "수동매도"),      # master에 있음
            ("C", "봇매도", 10000, "x", 9000, "2026-10-13T10:00:00", 10, "손절(-3.5%)"),   # 수동 아님
            ("D", "일부", 10000, "x", 11000, "2026-10-13T11:00:00", 3, "수동일부매도")])
        d.commit(); d.close()
        rows = F.plan("m.db", {"daybot": ("d.db", "code", "stock_name"), "sbot": ("없음.db", "code", "stock_name")})
        self.assertEqual([(r["code"], r["krw"]) for r in rows], [("D", 3000), ("A", 5000)])
        F.apply("m.db", rows)
        m = sqlite3.connect("m.db")
        self.assertEqual(m.execute("SELECT code, qty, profit_krw, is_partial, sell_time FROM master_trades "
                                   "WHERE code='A'").fetchone(), ("A", 10, 5000, 0, "2026-10-13 13:00:00"))
        self.assertEqual(m.execute("SELECT is_partial FROM master_trades WHERE code='D'").fetchone(), (1,))
        m.close()
        self.assertEqual(F.plan("m.db", {"daybot": ("d.db", "code", "stock_name")}), [])   # 두 번 돌려도 중복 X


if __name__ == "__main__":
    unittest.main()
