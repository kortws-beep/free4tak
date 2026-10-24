"""엔진 대항전 — 기간 시작일, 봇별 엔진 분류(sbot 태그·daybot 순위·cbot 매도 사유), 금액 합계·줄 세우기."""
import os
import sys
import sqlite3
import tempfile
import datetime
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "interface"))
import engine_board as E  # noqa: E402


class Board(unittest.TestCase):
    def test_period_and_classify(self):
        sat = datetime.date(2026, 10, 24)
        self.assertEqual(E.period_start("week", sat), "2026-10-19")
        self.assertEqual(E.period_start("month", sat), "2026-10-01")
        self.assertEqual(E.daybot_engine("tier4_090930"), "090930타점")
        self.assertEqual(E.daybot_engine("tier1_overlap"), "겹침(2개 이상)")
        self.assertEqual(E.cbot_engine("stage0조기익절30%(+10.40%)"), "익절")
        self.assertEqual(E.cbot_engine("보호선(+0.28%)"), "보호선")

    def test_board(self):
        d = tempfile.mkdtemp()
        dbs = {k: os.path.join(d, k + ".db") for k in ("sbot", "daybot", "cbot", "lina")}
        for k in ("sbot", "daybot"):
            c = sqlite3.connect(dbs[k])
            c.execute("CREATE TABLE trades (buy_tag TEXT, buy_price REAL, sell_price REAL, qty INT, profit_rate REAL, sell_time TEXT)")
            c.executemany("INSERT INTO trades VALUES (?,?,?,?,?,?)",
                          [("sub", 100, 110, 10, 10, "2026-10-20"), ("expert", 100, 90, 5, -10, "2026-10-21"),
                           ("expert", 100, 120, 5, 20, "2026-09-01")])          # 지난달 — 이번 주에서 빠짐
            c.commit(); c.close()
        c = sqlite3.connect(dbs["cbot"])
        c.execute("CREATE TABLE trades (sell_reason TEXT, profit_krw REAL, profit_rate REAL, sell_time TEXT)")
        c.executemany("INSERT INTO trades VALUES (?,?,?,?)", [("손절(-8%)", -80000, -8, "2026-10-22"), ("보호선(+0.3%)", 3000, 0.3, "2026-10-22")])
        c.commit(); c.close()
        b = E.board("week", datetime.date(2026, 10, 24), dbs)
        self.assertEqual(b["totals"], {"sbot": 50.0, "daybot": 50.0, "cbot": -77000.0})
        self.assertEqual(b["rows"][0]["engine"], "보호선")                   # 금액 큰 순
        self.assertEqual(b["rows"][-1]["engine"], "손절")
        sub = next(r for r in b["rows"] if r["bot"] == "sbot" and r["engine"] == "SUB 고정석")
        self.assertEqual((sub["n"], sub["wins"], sub["krw"]), (1, 1, 100.0))


if __name__ == "__main__":
    unittest.main()
