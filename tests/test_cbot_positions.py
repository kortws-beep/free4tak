"""cbot — 수동매도 감지용 sold_today 정리, 전량매도 시 메모리 포지션 제거,
잔고조회 실패(None) 시 저장상태로 복구."""
import json
import time
import unittest

from _helpers import stub, use_temp_cwd

for _n in ["websockets", "notifier", "master_db", "account_sync", "yfinance"]:
    stub(_n)
stub("jwt", encode=lambda *a, **k: "tok")
stub("notifier", Notifier=lambda **k: None)
stub("anthropic", Anthropic=lambda **k: None)
stub("backtestc")
stub("backtestc.strategy_coin", CoinStrategy=object)

_tmp = use_temp_cwd()
import cbot  # noqa: E402

cbot._master_record = cbot._master_upsert = cbot._master_remove = None
cbot.BOT_STATE_FILE = "cbot_state.json"


class _Resp:
    def __init__(self, d): self.d = d
    def json(self): return self.d


class _Sess:
    def __init__(self, d): self.d = d
    def post(self, *a, **k): return _Resp(self.d)


def mk():
    b = object.__new__(cbot.CBot)
    b.positions, b.peak_tracker, b.sold_today, b._buy_sync_guard, b._ws_prices = {}, {}, {}, {}, {}
    b.daily_loss_count = b.daily_pnl = 0
    b.market_status, b.fear_greed, b.btc_rate = "normal", 50, 0
    b.notify = lambda *a, **k: None
    b._get_headers = lambda q=None: {}
    b.session = _Sess({"uuid": "x"})
    return b


class CbotPositions(unittest.TestCase):
    def test_buy_clears_stale_sold_today_key(self):
        b = mk(); b.sold_today = {"KRW-A": None}; b.get_krw_balance = lambda: 5_000_000
        self.assertTrue(b.buy("KRW-A", 1_000_000))
        self.assertNotIn("KRW-A", b.sold_today)

    def test_full_sell_removes_position_partial_does_not(self):
        b = mk(); b.positions = {"KRW-A": {"qty": 2.0, "entry_price": 100, "current": 110}}
        b._save_sell_history = lambda *a, **k: 10.0
        self.assertTrue(b.sell("KRW-A", 1.0, "half", sell_price=110000))
        self.assertIn("KRW-A", b.positions)
        self.assertTrue(b.sell("KRW-A", 2.0, "all", sell_price=110000))
        self.assertNotIn("KRW-A", b.positions)

    def test_restore_with_balance_failure_and_prune(self):
        now = time.time()
        json.dump({"positions": {"KRW-A": {"qty": 1, "entry_price": 100}},
                   "peak_tracker": {"KRW-A": {"stage": 2}},
                   "sold_today": {"KRW-B": None, "KRW-C": now - 10 * 3600, "KRW-D": now - 60}},
                  open("cbot_state.json", "w"))
        b = mk(); b.get_balances = lambda: None
        b._restore_positions()
        self.assertEqual(b.positions, {"KRW-A": {"qty": 1, "entry_price": 100}})
        self.assertEqual(b.peak_tracker["KRW-A"]["stage"], 2)
        self.assertEqual(list(b.sold_today), ["KRW-D"])


class CbotLossLimitPause(unittest.TestCase):
    def _state(self):
        return json.load(open("cbot_state.json"))

    def test_loss_limit_pause_auto_resumes_at_midnight_manual_does_not(self):
        b = mk(); notes = []
        b.notify = lambda m, critical=False: notes.append(m)
        b.daily_pnl = -160_000
        b._check_daily_loss_limit()
        self.assertEqual((self._state()["paused"], self._state()["pause_reason"]), (True, "loss_limit"))
        b._daily_reset("2026-10-08")
        self.assertFalse(self._state()["paused"]); self.assertFalse(b._is_paused)
        self.assertTrue(any("자동 재개" in n for n in notes))
        # 대장이 직접 멈춘 건(pause_reason=manual) 자정에도 그대로
        cbot._update_state(paused=True, pause_reason="manual")
        b._daily_reset("2026-10-09")
        self.assertTrue(self._state()["paused"])


if __name__ == "__main__":
    unittest.main()
