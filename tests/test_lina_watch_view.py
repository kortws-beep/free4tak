"""리나 등록종목 모바일 보기 — 단계별 상태·다음 매도선·근접 표시, 합계."""
import os
import sys
import unittest

from _helpers import use_temp_cwd

_tmp = use_temp_cwd()
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "interface"))
import lina_watch_view as V  # noqa: E402


class Row(unittest.TestCase):
    def test_before_tracking(self):
        r = V.row("052710", {"name": "아모텍", "entry_price": 10000, "peak_price": None, "qty": 10}, {"price": 10100, "chg": 1.0})
        self.assertAlmostEqual(r["rate"], 1.0); self.assertEqual(r["pnl"], 1000)
        self.assertIn("추적 전", r["status"]); self.assertEqual(round(r["line"]), 10300); self.assertFalse(r["near"])

    def test_tracking_near_sell_line(self):
        w = {"name": "리노", "entry_price": 10000, "peak_price": 10450, "stage": 1, "qty": 5}
        r = V.row("058470", w, {"price": 10100})
        self.assertAlmostEqual(r["line"], 10450 * 0.96)          # 1차 트레일 -4%
        self.assertTrue(r["near"]); self.assertIn("30% 익절", r["status"])

    def test_after_partial_uses_tight_trail_and_daybot_key(self):
        w = {"name": "삼성전자", "code": "005930", "account": "daybot", "entry_price": 10000, "peak_price": 11000, "stage": 2, "qty": 3}
        r = V.row("005930@d", w, {"price": 10900})
        self.assertEqual((r["code"], r["account"]), ("005930", "데이봇"))
        self.assertAlmostEqual(r["line"], 11000 * 0.97); self.assertIn("30% 익절 뒤", r["status"])

    def test_no_price_or_qty(self):
        self.assertEqual(V.row("1", {"entry_price": 100}, {})["status"], "시세 없음")
        r = V.row("2", {"entry_price": 100, "qty": None}, {"price": 90})
        self.assertIsNone(r["pnl"])
        s = V.summary([r, V.row("3", {"entry_price": 100, "qty": 2}, {"price": 110})])
        self.assertEqual((s["count"], s["pnl"]), (2, 20)); self.assertAlmostEqual(s["rate"], 10.0)


if __name__ == "__main__":
    unittest.main()
