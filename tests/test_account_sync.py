"""sbot 재시작 정합성 — 봇이 꺼진 사이 판 종목은 '유령'으로 지우지 말고 매도로 기록(10-28 파로스아이바이오)."""
import sqlite3
import unittest

from _helpers import use_temp_cwd

_tmp = use_temp_cwd()
import account_sync as A  # noqa: E402


class FakeApi:
    token = "t"

    def __init__(self, real, sales):
        self.real, self.sales = real, sales

    def get_current_positions(self):
        return self.real

    def get_period_trade_profit(self, start, end):
        return {"trades": self.sales}


def mkdb(path):
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE trades (id INTEGER PRIMARY KEY, code TEXT, stock_name TEXT, buy_price REAL, buy_time TEXT, "
              "sell_price REAL, sell_time TEXT, qty INTEGER, profit_rate REAL, sell_reason TEXT)")
    c.executemany("INSERT INTO trades (code, stock_name, buy_price, buy_time, qty) VALUES (?,?,?,?,?)",
                  [("388870", "파로스아이바이오", 6020, "2026-10-28T09:25", 137), ("111110", "유령", 1000, "2026-10-01", 5)])
    c.commit(); c.close()


class SyncGhost(unittest.TestCase):
    def test_sold_while_down_is_recorded_not_deleted(self):
        mkdb("t.db")
        api = FakeApi({}, [{"pdno": "388870", "sll_qty": "137", "sll_pric": "5837"},
                           {"pdno": "323280", "sll_qty": "49", "sll_pric": "54000"}])
        notes = []
        A.sync_positions(api, "t.db", lambda m, critical=False: notes.append(m))
        rows = dict(sqlite3.connect("t.db").execute("SELECT code, sell_price FROM trades").fetchall())
        self.assertEqual(rows, {"388870": 5837.0})            # 판 건 매도로 남고, 매도 기록 없는 유령만 삭제
        self.assertTrue(any("매도된 종목 기록" in n for n in notes))

    def test_recent_sales_weighted(self):
        api = FakeApi({}, [{"pdno": "A", "sll_qty": "10", "sll_pric": "100"}, {"pdno": "A", "sll_qty": "30", "sll_pric": "200"},
                           {"pdno": "B", "sll_qty": "0", "sll_pric": "0"}])
        self.assertEqual(A.recent_sales(api, ["A", "B"]), {"A": (175.0, 40)})


if __name__ == "__main__":
    unittest.main()
