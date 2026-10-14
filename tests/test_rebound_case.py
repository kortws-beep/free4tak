"""반등 선취매 사례 도구 — 장중 모양·하락 구간·리포트."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backtest"))
from _helpers import stub  # noqa: E402
stub("three_month_leader", THEME_DB="")
import rebound_case as C  # noqa: E402


class Case(unittest.TestCase):
    def test_intraday_shape(self):
        bars = [("090000", 100, 100, 99), ("093000", 96, 96, 95), ("094000", 97, 97.5, 96),
                ("100000", 99, 99, 98), ("150000", 98, 98, 97)]
        s = C.intraday_shape(bars)
        self.assertEqual(s["low_time"], "093000"); self.assertAlmostEqual(s["o2l"], -5.0)
        self.assertAlmostEqual(s["up15"], (97.5 / 95 - 1) * 100)
        self.assertEqual(s["t2"], 10); self.assertEqual(s["t3"], 30)
        self.assertEqual(C.intraday_shape([]), {})

    def test_report(self):
        dates = [f"2026-09-{i:02d}" for i in range(1, 31)]
        daily = {}
        for k in range(60):     # 시장: 25일까지 상승, 26~30일 하락
            p, rows = 100.0, []
            for i, d in enumerate(dates):
                p *= 1.01 if i < 25 else 0.98
                rows.append((d, p, p * 1.01, p * 0.97, p, 1000))
            daily[f"종목{k}"] = rows
        daily["엘앤에프"] = [(d, 100 + i, 102 + i, 98 + i, 100 + 2 * i, 5000) for i, d in enumerate(dates)]
        groups = {"2차전지": [("1", "엘앤에프"), ("2", "종목1"), ("3", "종목2")]}
        out = C.report("엘앤에프", daily, groups, 6, {"2026-09-30": [("090000", 100, 100, 95),
                                                                  ("091500", 97, 99, 96)]})
        self.assertIn("시장 하락 구간: 2026-09-26부터", out)
        self.assertIn("그룹 안 1/3등", out)
        self.assertIn("직전 1일(2026-09-25) 엘앤에프", out); self.assertIn("[2차전지] 1/3등", out)
        self.assertIn("■ 4. 장중 2026-09-30", out)
        self.assertIn("일봉이 없어", C.report("없는종목", daily, {}))


class Compare(unittest.TestCase):
    def test_compare(self):
        # 시장: 1~25일 상승, 26~28일 하락(바닥 28일), 29일 반등. '주도'는 25일에 +9%
        dates = [f"2026-09-{i:02d}" for i in range(1, 30)]
        daily = {}
        for k in range(60):
            p, rows = 100.0, []
            for i, d in enumerate(dates):
                o = p
                p = p * (1.01 if i < 25 else 0.98 if i < 28 else 1.02)
                rows.append((d, o, max(o, p) * 1.01, min(o, p) * 0.99, p, 1000 + k))
            daily[f"종목{k}"] = rows
        rows, p = [], 100.0
        for i, d in enumerate(dates):
            o = p
            p = p * (1.09 if i == 24 else 1.0 if i < 25 else 0.95 if i < 28 else 1.08)
            rows.append((d, o, max(o, p), min(o, p), p, 5000))
        daily["롯데에너지머티리얼즈"] = rows
        self.assertEqual(C.resolve("롯데에너지머트리얼", daily), "롯데에너지머티리얼즈")
        self.assertIsNone(C.resolve("없는회사이름입니다", daily))
        out = C.compare(["롯데에너지머트리얼", "없는회사"], daily, top=50)
        self.assertIn("하락 2026-09-26~2026-09-28", out)
        self.assertIn("롯데에너지머티리얼즈*", out)
        self.assertIn("(일봉에서 못 찾음)", out)
        self.assertIn("직전1일 상위30", out)
        line = next(x for x in out.splitlines() if x.strip().startswith("롯데"))
        self.assertIn("+9.0", line)                  # 직전1일
        self.assertIn("+8.0", line)                  # 반등날 종가(바닥날 종가 대비)
        self.assertIn("반등날 고가 목표 도달", out)
        tgt = next(x for x in out.splitlines() if x.strip().startswith("대장 선택") and "/" in x)
        self.assertIn("1/1(100%)", tgt)              # 고가 +8% → 3·5·7% 모두 도달


if __name__ == "__main__":
    unittest.main()
