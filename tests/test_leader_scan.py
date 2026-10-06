"""주도주검색식3(파이썬판) — 풀 구성, 거래대금 순위(B), (D·E) or F, 10분봉(C), 시총(A)."""
import datetime
import os
import sqlite3
import unittest

from _helpers import use_temp_cwd

_tmp = use_temp_cwd()
import leader_scan as L  # noqa: E402

NOW = datetime.datetime(2026, 10, 6, 9, 37, 30)


def build_db():
    db = os.path.abspath("lead.db")
    if os.path.exists(db):
        os.remove(db)
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE kr_theme_stocks (theme_name TEXT, stock_name TEXT)")
    con.execute("CREATE TABLE kr_stock_daily_data (date TEXT, stock_name TEXT, close_price INT, "
                "volume INT, trade_value INT)")
    for i in range(5):
        con.execute("INSERT INTO kr_theme_stocks VALUES ('t', ?)", (f"종목{i}KOSDAQ 00000{i}",))
        con.execute("INSERT INTO kr_stock_daily_data VALUES ('2026-10-02', ?, 1000, 1, ?)",
                    (f"종목{i}", 10 ** 9 * (5 - i)))
    con.commit(); con.close()
    return db


def q(price, chg, high, value):
    return {"name": "", "price": price, "chg": chg, "high": high,
            "prev_close": 10000, "value": value, "volume": 1}


def bars(cur_value, start_acml=1e10):
    # 09:30 봉 시작 직전(09:29) 누적 1e10, 이후 누적이 cur_value만큼 증가
    return [{"time": "093700", "price": 1, "volume": 1, "acml_value": start_acml + cur_value},
            {"time": "093000", "price": 1, "volume": 1, "acml_value": start_acml + 1},
            {"time": "092900", "price": 1, "volume": 1, "acml_value": start_acml}]


class API:
    def __init__(self):
        self.quotes = {
            "000000": q(10800, 8.0, 11000, 9e10),   # 급등·10분봉 60억 → 통과
            "000001": q(10400, 4.0, 10400, 8e10),   # 고가 +4% → D 탈락(제외)
            "000002": q(9400, -6.0, 10100, 7e10),   # 급락 F · 10분봉 10억 → C 탈락
            "000003": q(10900, 9.0, 11000, 1e8),    # 거래대금 꼴찌(순위 밖 가정)
            "999999": q(11000, 10.0, 11200, 6e10),  # 순위 API로만 들어온 종목, 시총 과대
        }
        self.bar_map = {"000000": 6e9, "000002": 1e9, "999999": 7e9, "000003": 9e9}
    def get_value_rank(self, blng): return [("999999", "신규급등")] if blng == "3" else []
    def get_multi_price(self, codes): return {c: self.quotes[c] for c in codes if c in self.quotes}
    def get_minute_bars(self, code, hhmmss): return bars(self.bar_map.get(code, 0))
    def get_market_data(self, code):
        return {"hts_avls": "2000000" if code == "999999" else "5000"}


class LeaderScan(unittest.TestCase):
    def test_scan(self):
        api = API()
        pool = L.build_pool(api, build_db(), today="2026-10-06")
        self.assertEqual(set(pool), {"000000", "000001", "000002", "000003", "000004", "999999"})
        L.B_RANK = 4
        try:
            out = L.scan(api, pool, NOW)
        finally:
            L.B_RANK = 200
        by = {r["code"]: r for r in out["results"]}
        self.assertNotIn("000001", by)              # (D·E) or F 불충족
        self.assertNotIn("000003", by)              # B 순위 밖
        self.assertTrue(by["000000"]["passed"]); self.assertEqual(by["000000"]["path"], "급등(D·E)")
        self.assertEqual(by["000002"]["fails"], ["C10분봉10억"]); self.assertEqual(by["000002"]["path"], "급락(F)")
        self.assertEqual(by["999999"]["fails"], ["A시총2,000,000억"])
        self.assertIn("000000", L.format_hit(by["000000"]))
        log = os.path.abspath("leadlog.db")
        self.assertEqual(L.log_scan(out, NOW, log), 3)

    def test_bar_value_fallback_without_cumulative(self):
        b = [{"time": "093700", "price": 100, "volume": 10, "acml_value": 0},
             {"time": "093000", "price": 100, "volume": 5, "acml_value": 0},
             {"time": "092900", "price": 100, "volume": 99, "acml_value": 0}]
        self.assertEqual(L.bar_value(b, "0937"), 1500)


if __name__ == "__main__":
    unittest.main()
