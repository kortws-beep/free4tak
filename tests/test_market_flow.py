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
        self.assertEqual(M._date("2026-10-16T15:30:00+09:00"), "2026-10-16")
        self.assertIsNone(M._num("-"))

    def test_collect_and_recent(self):
        db = os.path.abspath("log.db")

        def get(url):
            if "/price" in url:
                return PRICE if "page=1" in url else []
            return INTEG
        out = M.collect(120, db, get=get)
        self.assertIn("지수 일봉 4행", out); self.assertIn("투자자 동향 4행", out)
        r = M.recent(5, db)
        self.assertIn("KOSPI 5,412.30(+2.00%) 외 +2,345", r)

    def test_bad_response_prints(self):
        self.assertEqual(M.fetch_index("KOSPI", get=lambda u: {"weird": 1}), [])
        self.assertEqual(M.fetch_investor("KOSPI", get=lambda u: {"weird": 1}), [])


if __name__ == "__main__":
    unittest.main()
