"""반등날 고점 시각 분포 — 시간대 버킷팅, 분봉에서 고가 시각 찾기."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backtest"))
import rebound_peak_time as RPT  # noqa: E402


class Bucket(unittest.TestCase):
    def test_bucket_edges(self):
        self.assertEqual(RPT._bucket("0900"), "09:45 전")
        self.assertEqual(RPT._bucket("0944"), "09:45 전")
        self.assertEqual(RPT._bucket("0945"), "09:45~10:30")
        self.assertEqual(RPT._bucket("1029"), "09:45~10:30")
        self.assertEqual(RPT._bucket("1030"), "10:30~12:00")
        self.assertEqual(RPT._bucket("1200"), "12:00~13:30")
        self.assertEqual(RPT._bucket("1330"), "13:30~15:20")
        self.assertEqual(RPT._bucket("1519"), "13:30~15:20")

    def test_high_time_picks_max_high_column(self):
        bars = [("0900", 100, 101, 99), ("0905", 102, 105, 101), ("0910", 103, 104, 102)]
        self.assertEqual(RPT.high_time(bars), "0905")
        self.assertEqual(RPT.high_time([]), "")

    def test_report_shows_distribution_and_detail(self):
        rows = [("2026-10-13", "2026-10-14", "삼성SDI", "006400", "1007", "2차전지_셀양극재"),
                ("2026-10-15", "2026-10-16", "한미약품", "128940", "1257", "바이오_대형비만")]
        out = RPT.report(rows)
        self.assertIn("2건", out); self.assertIn("09:45 전", out); self.assertIn("고점 10:07", out)
        self.assertEqual(RPT.report([]), "데이터 없음")


if __name__ == "__main__":
    unittest.main()
