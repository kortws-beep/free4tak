"""sbot — 손절/트레일링 실패 처리, 물타기 DB 합산, 애프터장 매도가, 키키 결과 보고."""
import os
import sqlite3
import unittest

from _helpers import stub, use_temp_cwd

for _n in ["notifier", "sbot_analyzer", "risk_manager", "candidate_pool", "account_sync", "master_db"]:
    stub(_n)
stub("notifier", Notifier=object)
stub("sbot_analyzer", SwingAnalyzer=object)
stub("risk_manager", RiskManager=object)
stub("kis_api", KisAPI=object)

_tmp = use_temp_cwd()
import sbot_strategy  # noqa: E402
import sbot_db        # noqa: E402
import sbot           # noqa: E402

sbot._master_record = sbot._master_remove = sbot._master_upsert = None
sbot.BOT_STATE_FILE = "sbot_state.json"


def tracker(stage=0, **kw):
    t = {"peak_rate": 0, "peak_price": 100, "stage": stage, "buy2_done": True, "buy1_price": 100,
         "stop_price": 95, "target1": 120, "target_next": 120, "atr_val": 2.5,
         "buy_date": "2026-10-01", "last_entry": 100}
    t.update(kw)
    return t


class StrategySellFailure(unittest.TestCase):
    def setUp(self):
        self.s = sbot_strategy.SwingStrategy()

    def _check(self, pt, price, sell_ok, losses):
        return self.s.check_sell("X", {"entry_price": 100, "qty": 10}, {"stck_prpr": str(price)},
                                 "normal", pt, False, lambda *a: None,
                                 lambda *a: sell_ok, lambda: losses.append(1))

    def test_failed_stop_loss_keeps_tracker_and_counter(self):
        pt, losses = {"X": tracker()}, []
        self.assertIsNone(self._check(pt, 94, False, losses))
        self.assertIn("X", pt); self.assertEqual(losses, [])
        self.assertEqual(self._check(pt, 94, True, losses), "손절")
        self.assertNotIn("X", pt); self.assertEqual(losses, [1])

    def test_failed_trailing_keeps_stage(self):
        pt = {"X": tracker(stage=1, peak_rate=.2, peak_price=120, stop_price=103, target_next=130)}
        self.assertIsNone(self._check(pt, 115, False, []))
        self.assertEqual(pt["X"]["stage"], 1)


class SwingDBSecondBuy(unittest.TestCase):
    def test_second_buy_merges_into_open_row(self):
        db = sbot_db.SwingDB(); db.init_db()
        db.save_buy("A", 100, 10, 50, "r")
        self.assertTrue(db.add_to_open_buy("A", 90, 10))
        con = sqlite3.connect(sbot_db.SBOT_HIST_DB)
        self.assertEqual(con.execute("select buy_price, qty from trades where code='A'").fetchall(),
                         [(95.0, 20)])
        db.save_sell("A", 110, "half", sold_qty=10); db.save_sell("A", 120, "rest")
        self.assertEqual(con.execute("select count(*) from trades where code='A' "
                                     "and sell_price is null").fetchone()[0], 0)
        self.assertFalse(db.add_to_open_buy("NONE", 1, 1))


class API:
    def __init__(self):
        self.ok, self.sells = True, []
    def sell(self, c, q, price=0):
        self.sells.append(price); return self.ok
    def get_market_data(self, c): return {"stck_prpr": "1000"}


def mk():
    b = object.__new__(sbot.SBot)
    b.api, b.db = API(), sbot_db.SwingDB()
    b.db.init_db()
    b.positions, b.peak_tracker, b.buy_context = {}, {}, {}
    b.sold_today, b.code_name_map, b._recent_sells = {}, {}, {}
    b.market_status = "normal"
    b._notify = lambda *a, **k: None
    return b


class SbotSell(unittest.TestCase):
    def test_after_regular_hours_defers_without_calling_api(self):
        # ★ 2026-10-08: 장종료동시마감/애프터마켓 모두 주문거부가 잦아
        #   15:20 이후엔 매도 시도 자체를 보류(API 호출 없음).
        sbot.now_hhmm = lambda: "1600"
        b = mk(); b.positions = {"C": {"entry_price": 900, "qty": 5}}; b.peak_tracker = {"C": {"stage": 1}}
        self.assertFalse(b._do_sell("C", 5, "트레일링", 1000))
        self.assertEqual(b.api.sells, [])
        self.assertIn("C", b.peak_tracker)   # 보류 — tracker 그대로 유지

    def test_premarket_passes_price(self):
        sbot.now_hhmm = lambda: "0830"
        b = mk(); b.positions = {"D": {"entry_price": 900, "qty": 5}}
        b._do_sell("D", 5, "x", 1000)
        self.assertEqual(b.api.sells[-1], 1000)

    def test_kiki_sell_failure_reported(self):
        res = {}
        sbot._write_cmd_result = lambda r: res.update(r=r)
        b = mk(); b.positions = {"E": {"entry_price": 900, "qty": 5}}; b.api.ok = False
        b._handle_pending_command({"pending_cmd": {"type": "sell", "code": "E"}})
        self.assertTrue(res["r"].startswith("❌"))


if __name__ == "__main__":
    unittest.main()
