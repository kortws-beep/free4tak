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
    """일손실 한도 멈춤 → 4시간 뒤 시장 점검으로 재개/유지 (2026-10-07 대장 결정)."""
    def _state(self):
        return json.load(open("cbot_state.json"))

    def _bot(self, prices):
        b = mk(); b.notes = []
        b.notify = lambda m, critical=False: b.notes.append(m)
        b.coin_pool = ["KRW-A", "KRW-B", "KRW-C"]
        b.prices = prices
        b.get_current_price = lambda ms: {m: b.prices[m] for m in ms if m in b.prices}
        cbot.cmc.fetch_krw_prices = lambda session, markets=None: dict(b.prices)
        cbot.cmc.fetch_market_flags = lambda session: {"KRW-A": {"warning": False, "cautions": ["가격급등락"]}}
        cbot.cmc.fetch_headlines = lambda session, hours=6, limit=6: []
        b._update_market_status = lambda: None
        b._last_market_check = 0
        b._is_paused = True
        return b

    def test_pause_snapshot_then_resume_when_market_recovers(self):
        b = self._bot({"KRW-BTC": 100, "KRW-A": 10, "KRW-B": 10, "KRW-C": 10})
        b.daily_pnl = -160_000
        b._check_daily_loss_limit()
        st = self._state()
        self.assertEqual((st["paused"], st["pause_reason"]), (True, "loss_limit"))
        self.assertEqual(st["pause_snapshot"]["KRW-BTC"], 100)
        self.assertGreater(st["next_review_at"], time.time() + 3.9 * 3600)
        # 4시간 뒤: BTC +1%, 코인 중앙값 +2% → 재개, 같은 날은 -7.5만까지만 더 허용
        b.prices = {"KRW-BTC": 101, "KRW-A": 10.2, "KRW-B": 10.2, "KRW-C": 9.9}
        self.assertTrue(b._review_loss_pause(self._state()))
        self.assertFalse(self._state()["paused"]); self.assertFalse(b._is_paused)
        b.daily_pnl = -200_000
        self.assertFalse(b._loss_limit_hit())
        b.daily_pnl = -240_000
        self.assertTrue(b._loss_limit_hit())

    def test_keep_paused_when_trend_continues(self):
        b = self._bot({"KRW-BTC": 100, "KRW-A": 10, "KRW-B": 10})
        b.sold_today = {"KRW-A": time.time()}
        b.daily_pnl = -160_000
        b._check_daily_loss_limit()
        b.prices = {"KRW-BTC": 97, "KRW-A": 9.5, "KRW-B": 9.4}
        self.assertFalse(b._review_loss_pause(self._state()))
        self.assertTrue(self._state()["paused"])
        self.assertTrue(any("유지" in n for n in b.notes))
        # 최근 손실매도 코인의 업비트 경보가 점검 알림에 참고로 붙음
        self.assertTrue(any("털린 코인 업비트 경보: A(가격급등락)" in n for n in b.notes), b.notes)

    def test_weak_market_keeps_pause_and_midnight_does_not_resume(self):
        b = self._bot({"KRW-BTC": 100, "KRW-A": 10})
        b.daily_pnl = -160_000
        b._check_daily_loss_limit()
        b.market_status = "weak"
        b.prices = {"KRW-BTC": 102, "KRW-A": 10.5}
        self.assertFalse(b._review_loss_pause(self._state()))
        b._daily_reset("2026-10-08")
        self.assertTrue(self._state()["paused"])          # 자정에 자동으로 풀지 않음
        self.assertEqual(b._loss_base, 0.0)


class CbotRiskSizing(unittest.TestCase):
    def test_amount_by_stop_width(self):
        b = mk()
        for atr, want in ((0.025, 1_000_000), (0.04, 625_000), (0.05, 500_000), (0, int(50_000 / 0.07))):
            b.get_atr_rate = lambda m, a=atr: a
            self.assertEqual(b._risk_sized_amount("KRW-X", 1_000_000), want)
        b.get_atr_rate = lambda m: 0.05
        self.assertEqual(b._risk_sized_amount("KRW-X", 300_000), 300_000)   # 마지막 슬롯 잔액이 더 작으면 그대로


if __name__ == "__main__":
    unittest.main()
