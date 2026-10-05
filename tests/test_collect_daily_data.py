"""일봉 수집기 — 실제 거래일 저장, 거래대금, 공휴일/휴장일 가짜행 정리, backfill."""
import datetime as dt
import os
import sqlite3
import unittest

from _helpers import use_temp_cwd

_tmp = use_temp_cwd()
import collect_daily_data as C  # noqa: E402


class FakeDT(dt.datetime):
    @classmethod
    def today(cls): return cls(2026, 10, 6, 8, 10)


C.datetime = FakeDT


def fresh_db(rows):
    C.DB_PATH = os.path.abspath("t.db")
    if os.path.exists(C.DB_PATH):
        os.remove(C.DB_PATH)
    con = sqlite3.connect(C.DB_PATH)
    con.execute("CREATE TABLE kr_stock_daily_data (date TEXT, stock_name TEXT, close_price INTEGER, "
                "volume INTEGER, foreign_net_buy INTEGER, institution_net_buy INTEGER, "
                "updated_at TEXT, PRIMARY KEY(date, stock_name))")
    for r in rows:
        con.execute("INSERT INTO kr_stock_daily_data VALUES (?,?,?,?,0,0,NULL)", r)
    con.commit(); con.close()
    C.ensure_ohlc_columns()


def candle(d, close, tv):
    return {"date": d, "open": 1, "high": 2, "low": 1, "close": close, "volume": 10, "trade_value": tv}


class API:
    def __init__(self, ohlc):
        self.ohlc, self.calls = ohlc, []
    def get_daily_ohlc(self, code, days, end_date=None):
        self.calls.append(end_date); return self.ohlc
    def get_investor_trend(self, code, cache): return {"foreign_today": 5, "orgn_today": 7}


def dates():
    return sqlite3.connect(C.DB_PATH).execute(
        "select date, close_price, trade_value, foreign_net_buy from kr_stock_daily_data order by date").fetchall()


class Collector(unittest.TestCase):
    def test_real_dates_value_and_holiday_ghost_in_range(self):
        fresh_db([("2026-10-02", "A", 999, 1)])      # 10-02를 휴장일로 가정한 가짜행
        api = API([candle("2026-10-05", 110, 1100), candle("2026-10-01", 100, 1000),
                   candle("2026-09-30", 90, 900)])
        C.collect_stock(api, "A", "000001", 3)
        rows = dates()
        self.assertEqual([r[0] for r in rows], ["2026-09-30", "2026-10-01", "2026-10-05"])
        self.assertEqual(rows[-1][2], 1100)
        self.assertEqual(rows[-1][3], 5, "수급은 오늘 이전 최근 거래일에")

    def test_tail_ghost_after_latest_candle_removed(self):
        fresh_db([("2026-10-05", "A", 276000, 11501250), ("2026-05-11", "A", 1, 1)])
        api = API([candle("2026-10-02", 276000, 3167442236500)])
        C.collect_stock(api, "A", "005930", 1)
        self.assertEqual([r[0] for r in dates()], ["2026-05-11", "2026-10-02"])

    def test_backfill_passes_end_date_and_keeps_tail(self):
        fresh_db([("2026-10-05", "A", 1, 1)])
        api = API([candle("2026-05-14", 50, 500)])
        C.collect_stock(api, "A", "005930", 100, end_date="20260515")
        self.assertEqual(api.calls, ["20260515"])
        self.assertIn("2026-10-05", [r[0] for r in dates()])


if __name__ == "__main__":
    unittest.main()
