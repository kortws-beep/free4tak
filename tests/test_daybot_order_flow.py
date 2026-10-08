"""daybot 주문 흐름 — "접수≠체결" 대응(2026-10-06) 시나리오.

매도 미체결/부분체결/거부, 매수 취소 후 부분체결, 수동 일부매도,
잔고 {} 오류, 키키 매도 결과 보고를 가짜 API로 재현한다."""
import threading
import time
import unittest

from _helpers import stub, use_temp_cwd

for _n in ["kis_websocket", "kiwoom_api", "notifier", "master_db", "sector_monitor"]:
    stub(_n)
stub("kis_websocket", KisWebSocket=object)
stub("kiwoom_api", KiwoomAPI=object)
stub("notifier", Notifier=object)
stub("kis_api", KisAPI=object)

_tmp = use_temp_cwd()
import daybot  # noqa: E402

daybot.BOT_STATE_FILE = "test_state.json"
daybot._master_record = daybot._master_remove = daybot._master_upsert = None


class WS:
    def __init__(self):
        self.live_prices, self.positions = {}, {}
    def subscribe_price(self, c): pass
    def unsubscribe_price(self, c): pass


class DB:
    def __init__(self):
        self.calls = []
    def __getattr__(self, n):
        return lambda *a, **k: self.calls.append((n, a, k))


class API:
    def __init__(self):
        self.sell_ok, self.real, self.cancel_ok, self.sells = True, {}, True, []
    def sell(self, code, qty, price=0):
        self.sells.append((code, qty, price)); return self.sell_ok
    def get_current_positions(self, force=False): return self.real
    def cancel_order(self, *a): return self.cancel_ok
    def get_market_data(self, c): return {"stck_prpr": "10000"}
    def get_period_trade_profit(self, a, b): return {"trades": []}


def mk():
    b = object.__new__(daybot.DayBot)
    b.api, b.db, b._ws, b.notes = API(), DB(), WS(), []
    b._notify = lambda m, critical=False: b.notes.append((critical, m))
    b.positions, b.code_name_map, b.sold_today, b._pending_orders = {}, {}, {}, {}
    b._sell_verify, b._cancel_reconcile, b._sell_fail = {}, {}, {}
    b._empty_balance_streak = 0
    b._positions_lock = threading.Lock()
    b._last_manual_check_ts = 0
    return b


def pos(q=10, entry=10000, age=1000):
    return {"entry_price": entry, "qty": q, "buy_time": "x", "source_tier": "t", "buy_tag": "t",
            "peak_price": None, "buy_ts": time.time() - age, "held_trading_days": 0}


def expire(d, code):
    d[code]["ts"] -= 200


def booked(b):
    return [c for c in b.db.calls if c[0] == "save_sell"]


class DaybotOrderFlow(unittest.TestCase):
    def setUp(self):
        daybot.now_hhmm = lambda: "1000"   # 정규장

    def test_after_regular_hours_defers_without_calling_api(self):
        # ★ 2026-10-08: 장종료동시마감/애프터마켓 모두 주문거부가 잦아
        #   15:20 이후엔 매도 시도 자체를 보류(API 호출 없음).
        daybot.now_hhmm = lambda: "1600"
        b = mk(); b.positions["0035S0"] = pos()
        self.assertFalse(b._do_sell("0035S0", 10, "손절", 9600))
        self.assertEqual(b.api.sells, [])
        self.assertIn("0035S0", b.positions)

    def test_unfilled_sell_is_readopted_not_booked(self):
        b = mk(); b.positions["0035S0"] = pos()
        self.assertTrue(b._do_sell("0035S0", 10, "손절", 9600))
        self.assertNotIn("0035S0", b.positions)
        self.assertEqual(b.api.sells[-1][2], 0, "정규장도 price=0(daybot._do_sell 관례)")
        expire(b._sell_verify, "0035S0")
        b.api.real = {"0035S0": {"qty": 10, "entry_price": 10000}}
        self.assertTrue(b._reconcile_due())
        b._reconcile_with_account()
        self.assertEqual(b.positions["0035S0"]["qty"], 10)
        self.assertFalse(booked(b))
        self.assertTrue(any(c for c, _ in b.notes))

    def test_premarket_sell_passes_price(self):
        daybot.now_hhmm = lambda: "0830"
        b = mk(); b.positions["A"] = pos()
        b._do_sell("A", 10, "x", 9600)
        self.assertEqual(b.api.sells[-1][2], 9600)

    def test_filled_sell_is_booked(self):
        b = mk(); b.positions["005930"] = pos()
        b._do_sell("005930", 10, "트레일링", 10500); expire(b._sell_verify, "005930")
        b.api.real = {"999999": {"qty": 1, "entry_price": 1}}
        b._reconcile_with_account()
        self.assertNotIn("005930", b.positions)
        self.assertEqual(booked(b)[0][2]["sold_qty"], 0)

    def test_partial_sell(self):
        b = mk(); b.positions["005930"] = pos()
        b._do_sell("005930", 10, "손절", 9600); expire(b._sell_verify, "005930")
        b.api.real = {"005930": {"qty": 4, "entry_price": 10000}}
        b._reconcile_with_account()
        self.assertEqual(b.positions["005930"]["qty"], 4)
        self.assertEqual(booked(b)[0][2]["sold_qty"], 6)

    def test_rejected_sell_backs_off(self):
        b = mk(); b.positions["005930"] = pos(); b.api.sell_ok = False
        for _ in range(5):
            b._do_sell("005930", 10, "손절", 9600)
        self.assertEqual(len(b.api.sells), 1)
        self.assertEqual(len(b.notes), 1); self.assertTrue(b.notes[0][0])
        self.assertIn("005930", b.positions)

    def test_cancelled_buy_partial_fill_kept(self):
        b = mk(); b.positions["005930"] = pos(age=40)
        b._pending_orders["005930"] = ("o", "d", 10, time.time() - 60)
        b._check_pending_orders()
        self.assertIn("005930", b._cancel_reconcile)
        expire(b._cancel_reconcile, "005930")
        b.api.real = {"005930": {"qty": 3, "entry_price": 10010}}
        b._reconcile_with_account()
        self.assertEqual(b.positions["005930"]["qty"], 3)
        self.assertEqual(b.positions["005930"]["entry_price"], 10010)
        names = [c[0] for c in b.db.calls]
        self.assertIn("update_open_buy", names); self.assertNotIn("void_buy", names)

    def test_cancelled_buy_unfilled_voided(self):
        b = mk(); b.positions["005930"] = pos(age=40)
        b._pending_orders["005930"] = ("o", "d", 10, time.time() - 60); b._check_pending_orders()
        expire(b._cancel_reconcile, "005930"); b.api.real = {"999999": {"qty": 1}}
        b._reconcile_with_account()
        self.assertNotIn("005930", b.positions)
        self.assertIn("void_buy", [c[0] for c in b.db.calls])

    def test_manual_partial_sell_syncs_qty(self):
        b = mk(); b.positions["005930"] = pos(q=10)
        b.api.real = {"005930": {"qty": 6, "entry_price": 10000}}
        b._reconcile_with_account()
        self.assertEqual(b.positions["005930"]["qty"], 6)

    def test_empty_balance_glitch_ignored(self):
        b = mk(); b.positions["005930"] = pos(); b.api.real = {}
        b._reconcile_with_account(); b._reconcile_with_account()
        self.assertIn("005930", b.positions)

    def test_kiki_sell_failure_reported(self):
        st = {}
        daybot.update_state = lambda f, **k: st.update(k)
        b = mk(); b.positions["005930"] = pos(); b.api.sell_ok = False
        b._get_current_price = lambda c: 10000.0
        b._handle_pending_command({"pending_cmd": {"type": "sell", "code": "005930"}})
        self.assertTrue(st["cmd_result"].startswith("❌"))


if __name__ == "__main__":
    unittest.main()
