"""리나 손절선 알림(11-27) — 손절선 닿으면 하루 한 번 알림만, 자동 매도 없음."""
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in ("lina_bot", "core"):
    sys.path.insert(0, os.path.join(_ROOT, _d))
import manual_autosell as A  # noqa: E402
import market_hours  # noqa: E402


class StopAlert(unittest.TestCase):
    def setUp(self):
        self.old = market_hours.SPECIAL_DAYS
        market_hours.SPECIAL_DAYS = {}

    def tearDown(self):
        market_hours.SPECIAL_DAYS = self.old

    def w(self):
        return {"name": "티엘비", "entry_price": 41700, "peak_price": None, "stage": 0, "qty": 30, "qty0": 30, "stop_alert": 35445}

    def test_alert_once_per_day_no_sell(self):
        w = self.w()
        out = A.handle("356860", w, 35100, "1030", "live", kiwoom=None)
        self.assertTrue(any("손절선 35,445원 도달" in m for m in out["msgs"]))
        self.assertFalse(out["deregister"])
        self.assertIsNone(out["sell_price"])
        out2 = A.handle("356860", w, 35000, "1031", "live", kiwoom=None)
        self.assertFalse(any("손절선" in m for m in out2["msgs"]))      # 같은 날 두 번 안 보냄

    def test_above_line_quiet(self):
        out = A.handle("356860", self.w(), 38000, "1030", "live", kiwoom=None)
        self.assertFalse(any("손절선" in m for m in out["msgs"]))

    def test_outside_session_quiet(self):
        out = A.handle("356860", self.w(), 35000, "0830", "live", kiwoom=None)
        self.assertEqual(out["msgs"], [])


if __name__ == "__main__":
    unittest.main()
