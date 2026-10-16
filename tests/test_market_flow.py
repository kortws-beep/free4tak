"""지수 일봉·투자자 동향 수집 — 응답 해석(형식 흔들려도), 저장, 출력."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "intelligence"))
from _helpers import use_temp_cwd  # noqa: E402
_tmp = use_temp_cwd()
import market_flow as M  # noqa: E402


PRICE = [{"localTradedAt": "2026-10-16", "closePrice": "5,412.30", "openPrice": "5,300.00",
          "highPrice": "5,450.10", "lowPrice": "5,280.00", "fluctuationsRatio": "2.00"},
         {"localTradedAt": "2026-10-15", "closePrice": "5,306.18", "fluctuationsRatio": "-8.10"}]
INTEG = {"stockName": "코스피", "dealTrendInfos": [
    {"bizdate": "20261016", "personalValue": "-1,234", "foreignValue": "+2,345", "institutionalValue": "-1,000"},
    {"bizdate": "20261015", "personalValue": "+9,000", "foreignValue": "-7,500", "institutionalValue": "-1,200"}]}


class Flow(unittest.TestCase):
    def test_parse(self):
        rows = M.parse_index_rows(PRICE)
        self.assertEqual(rows[0], ("2026-10-16", 5300.0, 5450.1, 5280.0, 5412.3, 2.0))
        self.assertEqual(rows[1][1], None)
        self.assertEqual(M.parse_index_rows({"result": PRICE})[0][0], "2026-10-16")
        inv = M.parse_investor_rows(INTEG)
        self.assertEqual(inv[0], ("2026-10-16", -1234.0, 2345.0, -1000.0))
        self.assertEqual(M.parse_investor_rows({"result": INTEG})[1][2], -7500.0)
        self.assertEqual(M.parse_investor_rows({"x": 1}), [])
        one = {"dealTrendInfo": {"bizdate": "20261016", "personalValue": "-1", "foreignValue": "+2", "institutionalValue": "0"}}
        self.assertEqual(M.parse_investor_rows(one), [("2026-10-16", -1.0, 2.0, 0.0)])
        self.assertEqual(M._date("2026-10-16T15:30:00+09:00"), "2026-10-16")
        self.assertIsNone(M._num("-"))

    def test_collect_and_recent(self):
        db = os.path.abspath("log.db")

        def get(url):
            if "/price" in url:
                return PRICE if "page=1" in url else []
            return INTEG
        out = M.collect(120, db, get=get, post=lambda b: {})
        self.assertIn("지수 일봉 4행", out); self.assertIn("투자자 동향 6행", out)     # KOSPI·KOSDAQ·FUT
        self.assertEqual(M.show_raw(db), "(원문 없음)")              # INTEG엔 원문 키 없음
        integ2 = dict(INTEG, programTrendInfo={"bizdate": "20261016", "indexDifferenceReal": "-1,709",
                                               "indexBiDifferenceReal": "+5,994", "indexTotalReal": "+4,286"},
                      upDownStockInfo={"upperCount": "2", "riseCount": "759", "lowerCount": "0", "fallCount": "135",
                                       "steadyCount": "22"})
        M.collect(0, db, get=lambda u: PRICE if "/price" in u else integ2, post=lambda b: {})
        self.assertIn("KOSPI programTrendInfo]", M.show_raw(db))
        r = M.recent(5, db)
        self.assertIn("프로그램 차익 -1,709 비차익 +5,994", r); self.assertIn("상승759:하락135", r)
        self.assertEqual(M.parse_program({"x": 1}), None); self.assertEqual(M.parse_updown({}), None)
        r = M.recent(5, db)
        self.assertIn("KOSPI 5,412.30(+2.00%) 외 +2,345", r)

    def test_bad_response_prints(self):
        self.assertEqual(M.fetch_index("KOSPI", get=lambda u: {"weird": 1}), [])
        self.assertEqual(M.fetch_investor("KOSPI", get=lambda u: {"weird": 1}), [])


FUT_RES = {"output1": {"hts_kor_isnm": "F 202612", "futs_prpr": "881.05", "basis": "3.74", "hts_thpr": "881.90",
                       "dprt": "-0.10", "hts_otst_stpl_qty": "148415", "otst_stpl_qty_icdc": "-6004"}}
FUNDS = {"ds1": [{"TMPV1": "20261015", "TMPV2": 96912122, "TMPV3": 37644385, "TMPV4": 107748834,
                  "TMPV5": 1392561, "TMPV6": 80498, "TMPV7": 5.9}]}
CREDIT = {"ds1": [{"TMPV1": "20261015", "TMPV2": 32749897, "TMPV3": 25532078, "TMPV4": 7217819, "TMPV5": 26124,
                   "TMPV6": 21544, "TMPV7": 4581, "TMPV8": 0, "TMPV9": 26963273}]}


class Extra(unittest.TestCase):
    def test_futures_kofia(self):
        self.assertEqual(M.parse_futures(FUT_RES), ("F 202612", 881.05, 3.74, 881.9, -0.1, 148415.0, -6004.0))
        self.assertIsNone(M.parse_futures({"output1": {}}))
        self.assertEqual(M.parse_kofia(FUNDS, M.KOFIA_FUNDS_COLS)[0][:7],
                         ("2026-10-15", 96912122.0, 37644385.0, 107748834.0, 1392561.0, 80498.0, 5.9))
        db = os.path.abspath("x.db")
        post = lambda body: FUNDS if body["dmSearch"]["OBJ_NM"] == M.KOFIA_FUNDS else CREDIT

        class Api:
            base_url, token, appkey, secret = "u", "t", "a", "s"
        old = M.fetch_futures
        M.fetch_futures = lambda api: M.parse_futures(FUT_RES)
        try:
            out = M.collect(0, db, get=lambda u: PRICE if "/price" in u else INTEG, api=Api(), post=post)
        finally:
            M.fetch_futures = old
        self.assertIn("선물 1행", out); self.assertIn("증시자금 1행", out); self.assertIn("신용 1행", out)
        r = M.recent(5, db)
        self.assertIn("선물 베이시스 +3.74 · 괴리율 -0.10% · 미결제 148,415(-6,004)", r)
        self.assertIn("미수 13,926 · 반대매매 805(5.9%) · 신용 327,499", r)


class Morning(unittest.TestCase):
    def test_line(self):
        import sqlite3
        db = os.path.abspath("m.db")
        conn = sqlite3.connect(db)
        M._table(conn)
        conn.executemany("INSERT INTO kofia_funds VALUES (?,?,?,?,?,?,?)", [
            ("2026-10-13", 1, 0, 0, 1241525, 59350, 5.6), ("2026-10-14", 1, 0, 0, 1357473, 89336, 7.2),
            ("2026-10-15", 1, 0, 0, 1392561, 80498, 5.9)])
        conn.executemany("INSERT INTO kofia_credit VALUES (?,?,?,?,?,?,?,?,?)", [
            ("2026-10-12", 34288699, 0, 0, 0, 0, 0, 0, 0), ("2026-10-15", 32749897, 0, 0, 0, 0, 0, 0, 0)])
        conn.executemany("INSERT INTO market_investor VALUES (?,?,?,?,?)", [
            ("2026-10-16", "KOSPI", 0, 3637, 0), ("2026-10-16", "FUT", 0, -9540, 0)])
        conn.commit(); conn.close()
        line = M.morning_line(db)
        self.assertIn("미수 13,926억(↑)", line); self.assertIn("최근 정점 10-14 893억", line)
        self.assertIn("신용 32.7조(20일 고점 대비 -4.5%)", line); self.assertIn("미수 증가 중", line)
        self.assertIn("코스피 현물 +3,637 · 선물 -9,540", line)
        conn = sqlite3.connect(db)
        conn.execute("INSERT INTO kofia_funds VALUES ('2026-10-16',1,0,0,1200000,40000,3.3)")
        conn.commit(); conn.close()
        self.assertIn("반대매매 정점 지나 꺾임", M.morning_line(db))
        self.assertEqual(M.morning_line(os.path.abspath("empty.db")), "")


if __name__ == "__main__":
    unittest.main()
