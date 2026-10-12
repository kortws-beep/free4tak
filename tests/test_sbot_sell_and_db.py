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
        self.assertIsNone(self._check(pt, 113, False, []))
        self.assertEqual(pt["X"]["stage"], 1)


class StrategyOct12(unittest.TestCase):
    """2026-10-12 대장 결정 — 손절 상한 -10%, 물타기 끔, +8%에 30%, 고점 -5% 트레일링."""
    def setUp(self):
        self.s = sbot_strategy.SwingStrategy()
        self.sold, self.bought = [], []

    def _check(self, pt, price, qty=10):
        return self.s.check_sell("X", {"entry_price": 100, "qty": qty}, {"stck_prpr": str(price)},
                                 "normal", pt, False, lambda *a: self.bought.append(a),
                                 lambda *a: self.sold.append(a) or True, lambda: None, ma20=90)

    def test_stop_capped_for_new_levels(self):
        self.assertEqual(self.s.calc_atr_levels(100, 0.08)["stop_price"], 90)   # ATR×2=-16% → -10%
        self.assertEqual(self.s.calc_atr_levels(100, 0.02)["stop_price"], 96)   # 얕으면 그대로

    def test_old_deep_stop_raised_only_if_price_above_cap(self):
        pt = {"X": tracker(stop_price=80)}
        self._check(pt, 95)
        self.assertEqual(pt["X"]["stop_price"], 90)
        pt = {"X": tracker(stop_price=80)}
        self._check(pt, 88)                      # 이미 -12% — 재시작하자마자 던지지 않음
        self.assertEqual(pt["X"]["stop_price"], 80); self.assertEqual(self.sold, [])

    def test_no_averaging_down(self):
        self._check({"X": tracker(buy2_done=False)}, 96)
        self.assertEqual(self.bought, [])

    def test_first_take_profit_sells_30pct(self):
        pt = {"X": tracker()}
        self._check(pt, 108)
        self.assertEqual(self.sold[0][1], 3); self.assertIn("30%", self.sold[0][2])
        self.assertEqual(pt["X"]["stage"], 1)
        self.sold.clear()
        self._check({"X": tracker()}, 108, qty=2)
        self.assertEqual(self.sold[0][1], 1)    # 최소 1주, 나머지 1주

    def test_trailing_5pct_from_peak(self):
        pt = {"X": tracker(stage=1, peak_rate=.2, peak_price=120)}
        self.assertIsNone(self._check(pt, 115))  # 고점 -4.2%
        self.assertEqual(self._check(pt, 114, qty=7), "트레일링")
        self.assertEqual(self.sold[-1][1], 7)


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


class SbotLossLimit(unittest.TestCase):
    def test_amount_limit(self):
        b = object.__new__(sbot.SBot)
        b.positions, b.peak_tracker, b.daily_loss_count, b.notes = {}, {}, 0, []
        b._notify = lambda m, critical=False: b.notes.append(m)
        b.risk = type("R", (), {"should_stop_trading": lambda self, n: (True, "손절 9회")})()   # 횟수 한도는 안 씀
        b.db = type("D", (), {"get_today_realized": lambda self: -500_000})()
        boom = [{"code": "X"}]                         # 한도에 걸리면 후보를 아예 안 봄
        b._execute_buys_unified(boom, "1000", 10_000_000)
        self.assertEqual(len(b.notes), 1); self.assertIn("신규매수 중단", b.notes[0])
        b._execute_buys_unified(boom, "1010", 10_000_000)
        self.assertEqual(len(b.notes), 1)
        self.assertNotIn("손절 9회", " ".join(b.notes))


class SbotWeakMarket(unittest.TestCase):
    """2026-10-12 — 국면 50% 미만이면 매수금 절반(마지막 슬롯도 목표금액 한도 뒤 절반)."""
    def _bot(self, breadth):
        b = object.__new__(sbot.SBot)
        b.notes = []
        b._notify = lambda m, critical=False: b.notes.append(m)
        sbot.market_regime.latest = lambda: ("2026-10-08", breadth)
        return b

    def test_half(self):
        old = sbot.market_regime.latest
        try:
            base = sbot.BUY_1ST_AMT_BASE
            self.assertEqual(self._bot(48.0)._regime_amount(base), base // 2)
            self.assertEqual(self._bot(48.0)._regime_amount(3_000_000), base // 2)   # 마지막 슬롯(현금 전부)
            self.assertEqual(self._bot(48.0)._regime_amount(400_000), 200_000)        # 켈리로 작게 잡힌 경우
            self.assertEqual(self._bot(55.0)._regime_amount(3_000_000), 3_000_000)
            self.assertEqual(self._bot(None)._regime_amount(base), base)
            b = self._bot(48.0); b._regime_amount(base)
            self.assertIn("약한 장", b.notes[0]); b._regime_amount(base); self.assertEqual(len(b.notes), 1)
        finally:
            sbot.market_regime.latest = old


if __name__ == "__main__":
    unittest.main()
