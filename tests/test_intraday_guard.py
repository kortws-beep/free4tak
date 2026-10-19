"""장중 하락 확대 감지기 — 시간창·-1.5% 기준·그날 유지·재시작 뒤 유지·조회 실패면 안 켬."""
import os
import sys
import unittest

from _helpers import use_temp_cwd

_tmp = use_temp_cwd()
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "core"))
import intraday_guard as G  # noqa: E402


class Api:
    def __init__(self, px):
        self.px, self.calls = px, 0

    def get_market_data(self, code):
        self.calls += 1
        if self.px.get(code) is None:
            raise ConnectionError("down")
        return {"stck_prpr": str(self.px[code]), "stck_oprc": "10000"}


class Guard(unittest.TestCase):
    def setUp(self):
        G.time.time = lambda: 1e9
        self.path = "guard_test.json"
        if os.path.exists(self.path):
            os.remove(self.path)

    def test_window_threshold_and_persist(self):
        api = Api({"069500": 9860, "229200": 10000})            # 코스피200 -1.4%
        g = G.IntradayGuard(api, self.path)
        self.assertEqual(g.check("2026-10-19", "0955"), ("", False))   # 10시 전엔 안 봄
        self.assertEqual(api.calls, 0)
        self.assertEqual(g.check("2026-10-19", "1005"), ("", False))
        api.px["229200"] = 9840                                    # 코스닥150 -1.6%
        G.time.time = lambda: 1e9 + 61
        reason, new = g.check("2026-10-19", "1042")
        self.assertTrue(new); self.assertIn("코스닥150", reason); self.assertIn("10:42", reason)
        api.px["229200"] = 10100                                   # 회복해도 그날은 유지
        self.assertEqual(g.check("2026-10-19", "1300")[1], False)
        self.assertTrue(g.check("2026-10-19", "1300")[0])
        g2 = G.IntradayGuard(Api({}), self.path)                    # 재시작 뒤에도 파일로 유지
        self.assertIn("코스닥150", g2.check("2026-10-19", "1400")[0])
        self.assertEqual(g2.check("2026-10-20", "1005"), ("", False))  # 다음 날은 꺼짐(조회 실패면 안 켬)

    def test_rate_limited_checks(self):
        api = Api({"069500": 10000, "229200": 10000})
        g = G.IntradayGuard(api, self.path)
        g.check("2026-10-19", "1001"); g.check("2026-10-19", "1001")
        self.assertEqual(api.calls, 2)                             # 60초 안 재조회 안 함(ETF 2개 1번)


if __name__ == "__main__":
    unittest.main()
