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


class FakeBroker:
    label = "키움"

    def __init__(self, avail=10, ok=True):
        self.avail, self.ok, self.sold = avail, ok, []

    def holdings(self):
        return {"052710": {"qty": self.avail, "avail": self.avail}}

    def sell_market(self, code, qty):
        self.sold.append((code, qty))
        return self.ok, "주문번호 1" if self.ok else "거부"


class Act(unittest.TestCase):
    def setUp(self):
        import json
        V.STATE = os.path.join(_tmp.name, "manual_watch_state.json")
        with open(V.STATE, "w", encoding="utf-8") as f:
            json.dump({"052710": {"name": "아모텍", "entry_price": 100, "qty": 5},
                       "005930@d": {"name": "삼성전자", "code": "005930", "account": "daybot", "entry_price": 100}}, f)
        os.environ["DASHBOARD_PIN"] = "1234"
        self.sent = []
        V._notify, V._log = self.sent.append, lambda *a: None

    def keys(self):
        from common_utils import read_state
        return set(read_state(V.STATE, default={}))

    def test_pin_and_session_guard(self):
        b = {"kiwoom": FakeBroker()}
        self.assertFalse(V.act("052710", "sell", "0000", "1000", b)["ok"])
        self.assertIn("정규장", V.act("052710", "sell", "1234", "1525", b)["msg"])
        self.assertEqual(b["kiwoom"].sold, [])

    def test_sell_all_then_deregister(self):
        b = {"kiwoom": FakeBroker(avail=10)}
        r = V.act("052710", "sell", "1234", "1000", b)
        self.assertTrue(r["ok"]); self.assertEqual(b["kiwoom"].sold, [("052710", 5)])   # 등록 수량만(장기보유분 보호)
        self.assertNotIn("052710", self.keys()); self.assertIn("대시보드 매도", self.sent[-1])
        self.assertIn("이미 해제", V.act("052710", "sell", "1234", "1000", b)["msg"])     # 두 번 눌러도 한 번만

    def test_limited_by_avail_and_failed_order_keeps_watch(self):
        self.assertEqual(V.act("052710", "sell", "1234", "1000", {"kiwoom": FakeBroker(avail=3, ok=False)})["ok"], False)
        self.assertIn("052710", self.keys())
        b = {"kiwoom": FakeBroker(avail=3)}
        V.act("052710", "sell", "1234", "1000", b)
        self.assertEqual(b["kiwoom"].sold, [("052710", 3)]); self.assertIn("3주만", self.sent[-1])

    def test_no_registered_qty_refuses_release_works(self):
        b = {"daybot": FakeBroker()}
        self.assertIn("등록 수량이 없어", V.act("005930@d", "sell", "1234", "1000", b)["msg"])
        self.assertTrue(V.act("005930@d", "release", "1234", "1000", b)["ok"])
        self.assertEqual(self.keys(), {"052710"}); self.assertEqual(b["daybot"].sold, [])


if __name__ == "__main__":
    unittest.main()
