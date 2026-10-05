"""3개월수급 당일주도주(파이썬판) — 후보선정(B·E)과 장중 조건(F·G·H·I·A)."""
import datetime
import os
import sqlite3
import unittest

from _helpers import use_temp_cwd

_tmp = use_temp_cwd()
import three_month_leader as T  # noqa: E402

TODAY = datetime.date(2026, 10, 5)


def build_db():
    db = os.path.abspath("t.db")
    if os.path.exists(db):
        os.remove(db)
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE kr_theme_stocks (theme_name TEXT, stock_name TEXT)")
    con.execute("CREATE TABLE kr_stock_daily_data (date TEXT, stock_name TEXT, close_price INT, "
                "volume INT, trade_value INT, high_price INT, low_price INT, PRIMARY KEY(date, stock_name))")
    for raw in ["좋은종목KOSDAQ 111111", "시끄러운종목KOSPI 222222", "스파이크없음KOSDAQ 0035S0",
                "설거지종목KOSPI 333333", "약한상승종목KOSPI 444444"]:
        con.execute("INSERT INTO kr_theme_stocks VALUES ('t', ?)", (raw,))
    days, d = [], TODAY - datetime.timedelta(days=1)
    while len(days) < 130:
        if d.weekday() < 5:
            days.append(d.isoformat())
        d -= datetime.timedelta(days=1)
    ins = "INSERT INTO kr_stock_daily_data VALUES (?,?,?,?,?,?,?)"
    for i, day in enumerate(days):   # i=0 = 어제
        spike = i == 10
        good = 250_000_000_000 if spike else 10_000_000_000
        noisy = 250_000_000_000 if i in (10, 80) else 50_000_000_000
        # 스파이크 전(i>10) 종가 9000 → 스파이크일 10000(+11%), 이후 10000
        c = 9000 if i > 10 else 10000
        con.execute(ins, (day, "좋은종목", c, 1_000_000, good, 10100 if spike else c, 9200 if spike else c))
        con.execute(ins, (day, "시끄러운종목", 10000, 1_000_000, noisy, None, None))
        con.execute(ins, (day, "스파이크없음", 10000, 1_000_000, None, None, None))
        # 설거지: 스파이크일 고가 12000까지 갔다가 10000에 마감(종가위치 29%)
        con.execute(ins, (day, "설거지종목", c, 1_000_000, good, 12000 if spike else c, 9200 if spike else c))
        # 대금만 터지고 주가 +2%
        c2 = 9800 if i > 10 else 10000
        con.execute(ins, (day, "약한상승종목", c2, 1_000_000, good, 10000 if spike else c2, 9800 if spike else c2))
    # 재수집 안 된 다른 종목에 남은 공휴일 가짜행(전 종목 DISTINCT 날짜를 늘리는 원인)
    for k in range(1, 6):
        con.execute(ins,
                    ((TODAY - datetime.timedelta(days=7 * k + 2)).isoformat(), "유령종목", 1, 1, None, None, None))  # 토요일
    con.commit(); con.close()
    return db


class API:
    def __init__(self, md): self.md = md
    def get_market_data(self, c): return self.md
    def get_execution_strength(self, c): return 130.0


class ThreeMonthLeader(unittest.TestCase):
    def setUp(self):
        self.u = T.build_universe(build_db(), today=TODAY.isoformat())

    def test_universe_keeps_only_quiet_then_spiked(self):
        self.assertEqual(self.u["scanned"], 5)
        self.assertEqual([i["name"] for i in self.u["items"]], ["좋은종목"])

    def test_fake_spikes_filtered(self):
        why = dict(self.u["fakes"])
        self.assertTrue(why["설거지종목"].startswith("가짜:종가위치"), why)
        self.assertTrue(why["약한상승종목"].startswith("가짜:상승률"), why)
        self.assertEqual(self.u["skip"]["가짜(설거지)"], 2)

    def test_retrace_to_pre_spike_level_fails(self):
        # 스파이크 전 5일 평균 9000 → 9450 이하면 되돌림 탈락
        r = T.check_candidates(API({"stck_prpr": "9400", "prdy_ctrt": "5.0",
                                    "acml_tr_pbmn": "5000000000", "acml_vol": "900000"}), self.u)
        self.assertTrue(any(f.startswith("가짜:되돌림") for f in r[0]["fails"]), r)

    def test_intraday_pass(self):
        r = T.check_candidates(API({"stck_prpr": "10700", "prdy_ctrt": "7.0",
                                    "acml_tr_pbmn": "5000000000", "acml_vol": "900000"}), self.u)
        self.assertTrue(r[0]["passed"], r)
        self.assertIn("좋은종목", T.format_hit(r[0]))

    def test_intraday_fail_reasons(self):
        r = T.check_candidates(API({"stck_prpr": "10200", "prdy_ctrt": "2.0",
                                    "acml_tr_pbmn": "1000000000", "acml_vol": "500000"}), self.u)
        self.assertFalse(r[0]["passed"])
        self.assertEqual(len(r[0]["fails"]), 3)


if __name__ == "__main__":
    unittest.main()
