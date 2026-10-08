"""시장 국면(20일선 위 종목 비율) — 계산·기록·재사용, 브리핑 문구, 데이봇 약한 장 매수금 절반."""
import os
import sqlite3
import threading
import unittest

from _helpers import stub, use_temp_cwd

for _n in ["kis_websocket", "kiwoom_api", "notifier", "master_db", "sector_monitor"]:
    stub(_n)
stub("kis_websocket", KisWebSocket=object)
stub("kiwoom_api", KiwoomAPI=object)
stub("notifier", Notifier=object)
stub("kis_api", KisAPI=object)

_tmp = use_temp_cwd()
import market_regime as MR  # noqa: E402
import daybot               # noqa: E402

THEME, LOG = os.path.abspath("theme.db"), os.path.abspath("log.db")


def build(n_up=36, n_dn=24, days=25):
    for p in (THEME, LOG):
        if os.path.exists(p):
            os.remove(p)
    c = sqlite3.connect(THEME)
    c.execute("CREATE TABLE kr_stock_daily_data (date TEXT, stock_name TEXT, close_price REAL)")
    for k in range(n_up + n_dn):
        for i in range(days):
            px = 100 + i if k < n_up else 100 - i
            c.execute("INSERT INTO kr_stock_daily_data VALUES (?,?,?)", (f"2026-09-{i + 1:02d}", f"s{k}", px))
    c.commit(); c.close()


class Regime(unittest.TestCase):
    def test_compute_save_latest(self):
        build()
        res = MR.compute(THEME)
        self.assertEqual(list(res), ["2026-09-25"]); self.assertAlmostEqual(res["2026-09-25"][0], 60.0)
        self.assertEqual(MR.latest(THEME, LOG), ("2026-09-25", 60.0))
        self.assertEqual(MR.history(5, LOG), [("2026-09-25", 60.0)])
        c = sqlite3.connect(LOG); c.execute("UPDATE market_regime SET breadth=77"); c.commit(); c.close()
        self.assertEqual(MR.latest(THEME, LOG), ("2026-09-25", 77.0))   # 같은 날짜면 다시 계산 안 함
        self.assertTrue(MR.is_strong(60.0)); self.assertFalse(MR.is_strong(49.9)); self.assertFalse(MR.is_strong(None))

    def test_backfill_and_line(self):
        build(n_up=20, n_dn=40)
        MR.save(MR.compute(THEME, ["2026-09-23", "2026-09-24", "2026-09-25"]), LOG)
        self.assertEqual([d for d, _ in MR.history(5, LOG)], ["2026-09-23", "2026-09-24", "2026-09-25"])
        line = MR.format_line(THEME, LOG)
        self.assertIn("20일선 위 종목 33%", line); self.assertIn("추격 불리", line)
        self.assertIsNone(MR.compute(THEME).get("x"))
        self.assertEqual(MR.compute(THEME, ["2026-09-05"]), {})          # 20일 안 된 날은 계산 안 함


class DaybotHalf(unittest.TestCase):
    def _bot(self, breadth):
        b = object.__new__(daybot.DayBot)
        b.positions, b.code_name_map, b._positions_lock = {}, {}, threading.Lock()
        b._pending_orders, b.notes, b.amounts = {}, [], []
        b._regime = ("x", None)
        b._notify = lambda m, critical=False: b.notes.append(m)
        b._ws = type("W", (), {"subscribe_price": lambda self, c: None})()
        b.db = type("D", (), {"save_buy": lambda self, *a, **k: None})()

        class Api:
            def get_psbl_order_cash(self, *a): return 3_000_000
            def buy(self, code, price, amount, **k):
                b.amounts.append(amount); return True, "o", "n", 10
        b.api = Api()
        daybot.market_regime.latest = lambda: ("2026-10-08", breadth)
        return b

    def test_weak_market_halves(self):
        old, old_latest = daybot._master_upsert, MR.latest
        daybot._master_upsert = None
        try:
            b = self._bot(42.0)
            self.assertTrue(b._do_buy("111110", "가", 10000, "t"))
            self.assertEqual(b.amounts, [daybot.BUY_AMT_PER_SLOT // 2])
            self.assertIn("약한 장", b.notes[0])
            b2 = self._bot(55.0)
            b2._do_buy("111110", "가", 10000, "t")
            self.assertEqual(b2.amounts, [daybot.BUY_AMT_PER_SLOT])
            b3 = self._bot(None)                                   # 계산 실패 → 축소 안 함
            b3._do_buy("111110", "가", 10000, "t")
            self.assertEqual(b3.amounts, [daybot.BUY_AMT_PER_SLOT])
        finally:
            daybot._master_upsert, MR.latest = old, old_latest


if __name__ == "__main__":
    unittest.main()
