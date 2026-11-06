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


class UpbitGhost(unittest.TestCase):
    """★ 11-06 — 코인봇도 봇 꺼진 사이 판 코인은 지우지 말고 매도로 기록(업비트 체결 매도로 확인)."""
    def test_recorded_not_deleted(self):
        import datetime
        now = datetime.datetime.now().isoformat(timespec="seconds")
        c = sqlite3.connect("c.db")
        c.execute("CREATE TABLE trades (id INTEGER PRIMARY KEY, market TEXT, buy_price REAL, buy_time TEXT, sell_price REAL, "
                  "sell_time TEXT, qty REAL, profit_rate REAL, profit_krw REAL, sell_reason TEXT)")
        c.executemany("INSERT INTO trades (market, buy_price, buy_time, qty) VALUES (?,?,?,?)",
                      [("KRW-SUI", 1000, now, 10), ("KRW-OLD", 50, now, 2), ("KRW-BTC", 1e8, now, 0.001)])
        c.commit(); c.close()
        orders = [{"market": "KRW-SUI", "side": "ask", "executed_volume": "10", "executed_funds": "11000", "created_at": now},
                  {"market": "KRW-SUI", "side": "bid", "executed_volume": "10", "executed_funds": "9000", "created_at": now},
                  {"market": "KRW-OLD", "side": "ask", "executed_volume": "2", "executed_funds": "100", "created_at": "2020-01-01T00:00:00"}]
        self.assertEqual(A.recent_sales_upbit(None, ["KRW-SUI", "KRW-OLD"], get=lambda: orders), {"KRW-SUI": (1100.0, 10.0)})

        class Bot:
            def get_current_positions(self):
                return {"KRW-BTC": {"entry_price": 1e8, "qty": 0.001}}
        notes = []
        old = A.recent_sales_upbit
        A.recent_sales_upbit = lambda cbot, markets, days=3: old(cbot, markets, days, get=lambda: orders)
        try:
            A.sync_positions_upbit(Bot(), "c.db", lambda m, critical=False: notes.append(m))
        finally:
            A.recent_sales_upbit = old
        c = sqlite3.connect("c.db")
        self.assertEqual(c.execute("SELECT sell_price, profit_krw, sell_reason FROM trades WHERE market='KRW-SUI'").fetchone(),
                         (1100.0, 1000.0, "수동매도(봇 꺼진 사이 감지)"))
        self.assertIsNone(c.execute("SELECT 1 FROM trades WHERE market='KRW-OLD'").fetchone())     # 매도 기록 없으면 예전처럼 정리
        self.assertTrue(any("봇 꺼진 사이" in n for n in notes))


if __name__ == "__main__":
    unittest.main()
