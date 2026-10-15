"""리나등록 자동 익절 — +3% 추적, 고점-4% 전량 / +5% 30% → 고점-3% 나머지, 모드별 동작."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lina_bot"))
import manual_autosell as A  # noqa: E402


class Kiwoom:
    def __init__(self, avail=100, ok=True):
        self.avail, self.ok, self.orders = avail, ok, []
    def holdings(self):
        return {"111111": {"qty": self.avail, "avail": self.avail}}
    def sell_market(self, code, qty):
        self.orders.append((code, qty)); return self.ok, "x"


def w(qty=100):
    return {"name": "테스트", "entry_price": 10000, "peak_price": None, "qty": qty}


class Step(unittest.TestCase):
    def test_rules(self):
        x = w()
        self.assertIsNone(A.step(x, 10200))                        # +2% — 아무것도
        self.assertEqual(A.step(x, 10300)["type"], "start")       # +3% 추적 시작
        self.assertIsNone(A.step(x, 10450))
        self.assertEqual(A.step(x, 10030)["type"], "final")       # 고점 10450 -4% = 10032 이하
        y = w()
        A.step(y, 10300)
        self.assertEqual(A.step(y, 10500)["type"], "partial")      # +5%
        y["stage"] = 2
        A.step(y, 11000)
        self.assertIsNone(A.step(y, 10700))                        # 고점 -2.7%
        self.assertEqual(A.step(y, 10670)["type"], "final")       # 고점 -3%
        self.assertEqual(A.part_qty(100), 30); self.assertEqual(A.part_qty(2), 1)
        self.assertEqual(A.part_qty(1), 0); self.assertEqual(A.part_qty(None), 0)

    def test_legacy_watch_without_stage(self):
        x = {"entry_price": 10000, "peak_price": 10400}            # 예전 등록(단계 없음, 이미 추적 중)
        self.assertEqual(A.step(x, 9980)["type"], "final")


class Handle(unittest.TestCase):
    def test_paper_never_orders(self):
        x, k = w(), Kiwoom()
        A.handle("111111", x, 10300, "1000", "paper", k)
        r = A.handle("111111", x, 10500, "1000", "paper", k)
        self.assertIn("연습", r["msgs"][0]); self.assertEqual(k.orders, [])
        self.assertEqual((x["stage"], x["qty"]), (2, 70)); self.assertFalse(r["deregister"])

    def test_live_partial_then_final(self):
        x, k = w(), Kiwoom()
        A.handle("111111", x, 10300, "1000", "live", k)
        r = A.handle("111111", x, 10500, "1000", "live", k)
        self.assertEqual(k.orders, [("111111", 30)]); self.assertEqual(x["qty"], 70)
        self.assertFalse(r["deregister"]); self.assertEqual(r["sell_price"], 10500)
        A.handle("111111", x, 11000, "1000", "live", k)
        k.avail = 70
        r = A.handle("111111", x, 10600, "1000", "live", k)
        self.assertEqual(k.orders[-1], ("111111", 70)); self.assertTrue(r["deregister"])

    def test_live_guards(self):
        x, k = w(qty=None), Kiwoom(avail=40)
        A.handle("111111", x, 10300, "1000", "live", k)
        A.handle("111111", x, 10450, "1000", "live", k)
        self.assertEqual(A.handle("111111", x, 10000, "1600", "live", k)["msgs"], [])   # 장 끝 — 다음에
        self.assertEqual(k.orders, [])
        r = A.handle("111111", x, 10000, "1000", "live", k)
        self.assertEqual(k.orders, []); self.assertIn("등록 수량이 없어", r["msgs"][0])   # 수량 없으면 안 팜(10-16)
        x2, k4 = w(qty=10), Kiwoom(avail=4)
        A.handle("111111", x2, 10300, "1000", "live", k4)
        A.handle("111111", x2, 9800, "1000", "live", k4)
        self.assertEqual(k4.orders, [("111111", 4)])                                     # 등록 10주 > 잔고 4주 → 4주
        y, k2 = w(qty=100), Kiwoom(avail=0)
        A.handle("111111", y, 10300, "1000", "live", k2)
        r = A.handle("111111", y, 9800, "1000", "live", k2)
        self.assertIn("매도가능수량 0", r["msgs"][0]); self.assertEqual(k2.orders, [])
        z, k3 = w(), Kiwoom(ok=False)
        A.handle("111111", z, 10300, "1000", "live", k3)
        r = A.handle("111111", z, 9800, "1000", "live", k3)
        self.assertIn("실패", r["msgs"][0]); self.assertEqual(z.get("stage"), 1)       # 실패하면 상태 그대로

    def test_premarket_ignored(self):
        x, k = w(), Kiwoom()
        r = A.handle("111111", x, 10500, "0830", "live", k)          # 프리장 +5% — 무시
        self.assertEqual((r["msgs"], x["peak_price"]), ([], None))
        r = A.handle("111111", x, 9600, "0900", "live", k)           # 정규장 -4% 시작 — 추적 전이라 안 팜
        self.assertEqual((r["msgs"], k.orders), ([], []))
        r = A.handle("111111", x, 10500, "1730", "paper", k)         # 시간외도 무시
        self.assertEqual(r["msgs"], [])

    def test_off_alert_only(self):
        x = w()
        A.handle("111111", x, 10300, "1000", "off")
        r = A.handle("111111", x, 9800, "1000", "off")
        self.assertIn("매도 신호", r["msgs"][0]); self.assertEqual(x["stage"], 3)

    def test_mode_env(self):
        old = os.environ.get("LINA_AUTOSELL")
        try:
            os.environ["LINA_AUTOSELL"] = "LIVE"; self.assertEqual(A.mode(), "live")
            os.environ["LINA_AUTOSELL"] = "이상한값"; self.assertEqual(A.mode(), "paper")
            del os.environ["LINA_AUTOSELL"]; self.assertEqual(A.mode(), "paper")
        finally:
            if old is not None:
                os.environ["LINA_AUTOSELL"] = old


if __name__ == "__main__":
    unittest.main()
