"""cbot 전량매도 0주 기록 복구 스크립트."""
import os
import sqlite3
import sys
import unittest

from _helpers import use_temp_cwd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "intelligence"))
_tmp = use_temp_cwd()
import fix_cbot_master_qty as F  # noqa: E402


class FixCbotMasterQty(unittest.TestCase):
    def test_plan_and_apply(self):
        m = sqlite3.connect("m.db")
        m.execute("CREATE TABLE master_trades (id INTEGER PRIMARY KEY, bot_type TEXT, code TEXT, buy_price REAL, "
                  "sell_price REAL, sell_time TEXT, qty REAL, profit_krw REAL)")
        m.executemany("INSERT INTO master_trades VALUES (?,?,?,?,?,?,?,?)", [
            (1, "cbot", "KRW-NEAR", 1000, 902, "2026-10-13 10:00:03", 0, 0),      # 전량 — 고칠 것
            (2, "cbot", "KRW-NEAR", 1000, 1100, "2026-10-12 10:00:00", 3, 300),   # 부분 — 정상
            (3, "cbot", "KRW-SOL", 1000, 900, "2026-10-13 11:00:00", 0, 0),       # 짝 없음
            (4, "sbot", "005930", 1000, 900, "2026-10-13 10:00:03", 0, 0)])        # 다른 봇
        m.commit(); m.close()
        c = sqlite3.connect("c.db")
        c.execute("CREATE TABLE trades (market TEXT, buy_price REAL, sell_price REAL, sell_time TEXT, qty REAL)")
        c.executemany("INSERT INTO trades VALUES (?,?,?,?,?)", [
            ("KRW-NEAR", 1000, 902, "2026-10-13T10:00:01", 7),
            ("KRW-SOL", 1000, 900, "2026-10-13T11:05:00", 5)])                    # 5분 차이 — 안 맞춤
        c.commit(); c.close()
        rows = F.plan("m.db", "c.db")
        self.assertEqual([(r[0], r[3], r[4]) for r in rows], [(1, 7, -686)])
        F.apply("m.db", rows)
        m = sqlite3.connect("m.db")
        self.assertEqual(m.execute("SELECT qty, profit_krw FROM master_trades WHERE id=1").fetchone(), (7, -686))
        m.close()


if __name__ == "__main__":
    unittest.main()
