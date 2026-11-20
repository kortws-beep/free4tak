"""널뛰기장(11-20 대장 규칙) — `널` 등록 종목 다음 날 15:10 전량 정리 · 최저점 통과/종가 최저점 알림 타이밍."""
import datetime
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in ("lina_bot", "intelligence", "core"):
    sys.path.insert(0, os.path.join(_ROOT, _d))
import manual_autosell as A  # noqa: E402
import market_hours  # noqa: E402
import neolttwigi as N  # noqa: E402


class Broker:
    label = "한투(데이봇 계좌)"

    def __init__(self):
        self.orders = []

    def holdings(self):
        return {"111111": {"qty": 10, "avail": 10}}

    def sell_market(self, code, qty, credit=None):
        self.orders.append((code, qty))
        return True, "ok"


def _w(reg_day, nul=True):
    x = {"name": "테스트", "entry_price": 10000, "peak_price": None, "qty": 10, "qty0": 10,
         "registered_at": f"{reg_day} 15:25:00"}
    if nul:
        x["nul"] = True
    return x


class ForcedExit(unittest.TestCase):
    def setUp(self):
        self._saved = market_hours.SPECIAL_DAYS
        market_hours.SPECIAL_DAYS = {}
        self.addCleanup(setattr, market_hours, "SPECIAL_DAYS", self._saved)
        self.yday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
        self.today = datetime.date.today().isoformat()

    def test_next_day_1510_sells_all_even_at_loss(self):
        b = Broker()
        out = A.handle("111111", _w(self.yday), 9800, "1510", "live", b)   # -2% 손실이어도
        self.assertTrue(out["deregister"]); self.assertEqual(b.orders, [("111111", 10)])
        self.assertIn("널뛰기장", out["msgs"][0])

    def test_before_1510_no_forced_exit(self):
        b = Broker()
        out = A.handle("111111", _w(self.yday), 10100, "1509", "live", b)
        self.assertFalse(out["deregister"]); self.assertEqual(b.orders, [])

    def test_same_day_registration_not_forced(self):          # 급락일 당일(산 날)은 정리 안 함
        b = Broker()
        out = A.handle("111111", _w(self.today), 10100, "1515", "live", b)
        self.assertFalse(out["deregister"]); self.assertEqual(b.orders, [])

    def test_plain_registration_unaffected(self):
        b = Broker()
        out = A.handle("111111", _w(self.yday, nul=False), 10100, "1515", "live", b)
        self.assertFalse(out["deregister"]); self.assertEqual(b.orders, [])


class Alert(unittest.TestCase):
    def setUp(self):
        self._snap, self._reg, self._cand = N.snapshot, N.regime, N.candidates_text
        self.addCleanup(self._restore)
        N.regime = lambda: 20.0
        N.candidates_text = lambda top=5: "   1. 테스트"
        self.snap = {}
        N.snapshot = lambda: dict(self.snap)

    def _restore(self):
        N.snapshot, N.regime, N.candidates_text = self._snap, self._reg, self._cand

    def _at(self, hhmm):
        return datetime.datetime.combine(datetime.date(2026, 11, 23), datetime.time(int(hhmm[:2]), int(hhmm[2:])))

    def test_intraday_low_passed_after_hold(self):
        st = {}
        self.snap = {"close": 4900, "chg": -3.5, "low": 4880, "low_chg": -3.9, "foreign": -12000}
        self.assertEqual(N.check(st, self._at("1300")), "")           # 저가 막 찍음
        self.snap["close"] = 4900
        self.assertEqual(N.check(st, self._at("1310")), "")           # 10분 — 아직
        msg = N.check(st, self._at("1316"))                           # 16분, 저가 대비 +0.4%
        self.assertIn("최저점 통과", msg)
        self.assertEqual(N.check(st, self._at("1330")), "")           # 하루 1번

    def test_new_low_resets_clock(self):
        st = {}
        self.snap = {"close": 4900, "chg": -3.5, "low": 4880, "low_chg": -3.9}
        N.check(st, self._at("1300"))
        self.snap.update(low=4860, low_chg=-4.3, close=4880)
        self.assertEqual(N.check(st, self._at("1316")), "")           # 새 저가 → 다시 15분
        self.assertIn("최저점 통과", N.check(st, self._at("1332")))

    def test_not_crash_day_or_not_volatile_regime(self):
        st = {}
        self.snap = {"close": 4950, "chg": -2.0, "low": 4930, "low_chg": -2.4}
        N.check(st, self._at("1300"))
        self.assertEqual(N.check(st, self._at("1330")), "")           # 저가 -2.4% > -3%
        N.regime = lambda: 40.0
        self.snap = {"close": 4900, "chg": -3.5, "low": 4880, "low_chg": -3.9}
        st2 = {}
        N.check(st2, self._at("1300"))
        self.assertEqual(N.check(st2, self._at("1330")), "")          # 국면 40% — 널뛰기장 아님

    def test_close_at_low_after_market(self):
        st = {}
        self.snap = {"close": 4870, "chg": -5.2, "low": 4860, "low_chg": -5.4}
        N.check(st, self._at("1525"))
        msg = N.check(st, self._at("1531"))
        self.assertIn("종가가 최저점", msg)
        self.assertEqual(N.check(st, self._at("1533")), "")


if __name__ == "__main__":
    unittest.main()
