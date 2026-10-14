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
        # ★ 10-14: 수집 도중 계산된 값 — 그 뒤 종목이 더 들어오면(행 수 변화) 다시 계산
        c = sqlite3.connect(THEME)
        for k in range(60, 100):                     # 내리는 종목 40개 추가 수집
            for i in range(25):
                c.execute("INSERT INTO kr_stock_daily_data VALUES (?,?,?)", (f"2026-09-{i + 1:02d}", f"s{k}", 100 - i))
        c.commit(); c.close()
        self.assertEqual(MR.latest(THEME, LOG), ("2026-09-25", 36.0))

    def test_value_update_without_row_count_change_triggers_recompute(self):
        # ★ 2026-10-14(2차): collect_daily_data.py는 INSERT ... ON CONFLICT DO UPDATE라, 장중에
        #   먼저 써진 임시 종가를 저녁 전체수집이 "값만" 덮어써도 행 수는 그대로임 — 행 수만 보던
        #   예전 가드는 이 경우를 놓쳐 틀린 값이 하루 종일 안 풀리는 사고가 났음(10-14, 60%).
        build()
        self.assertEqual(MR.latest(THEME, LOG), ("2026-09-25", 60.0))
        c = sqlite3.connect(THEME)
        # 상승 종목 10개(s0~s9)의 마지막날 종가만 급락시킴(행 추가 없음 — UPDATE만)
        c.executemany("UPDATE kr_stock_daily_data SET close_price=10 WHERE date='2026-09-25' AND stock_name=?",
                      [(f"s{k}",) for k in range(10)])
        c.commit(); c.close()
        self.assertEqual(MR.latest(THEME, LOG), ("2026-09-25", 26 / 60 * 100))

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
        # ★ 2026-10-08 — 시장국면 알림은 산야(scan) 쪼로 분리돼 _scan_notifier.send() 사용
        b._scan_notifier = type("N", (), {"send": lambda self, m, critical=False: b.notes.append(m)})()
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
            b4 = self._bot(42.0)                                   # 약한 장 + 마지막 슬롯(현금 300만)
            b4._do_buy("111110", "가", 10000, "t", is_last_slot=True)
            self.assertEqual(b4.amounts, [daybot.BUY_AMT_PER_SLOT // 2])  # 150만이 아니라 50만
            b5 = self._bot(55.0)                                   # 정상 장 마지막 슬롯은 현금 전부
            b5._do_buy("111110", "가", 10000, "t", is_last_slot=True)
            self.assertEqual(b5.amounts, [3_000_000])
            b3 = self._bot(None)                                   # 계산 실패 → 축소 안 함
            b3._do_buy("111110", "가", 10000, "t")
            self.assertEqual(b3.amounts, [daybot.BUY_AMT_PER_SLOT])
        finally:
            daybot._master_upsert, MR.latest = old, old_latest


class DaybotLossLimit(unittest.TestCase):
    def test_limit(self):
        b = object.__new__(daybot.DayBot)
        b.notes = []
        b._notify = lambda m, critical=False: b.notes.append(m)
        b.db = type("D", (), {"get_today_realized": lambda self: -149_000})()
        self.assertFalse(b._daily_loss_hit())
        b.db = type("D", (), {"get_today_realized": lambda self: -150_000})()
        self.assertTrue(b._daily_loss_hit()); self.assertTrue(b._daily_loss_hit())
        self.assertEqual(len(b.notes), 1); self.assertIn("신규매수 중단", b.notes[0])
        b._scan_conditions = lambda: self.fail("한도 걸리면 스캔도 안 함")
        b._run_candidate_scan_and_maybe_buy()


if __name__ == "__main__":
    unittest.main()
