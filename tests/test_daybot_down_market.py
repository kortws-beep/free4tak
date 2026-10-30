"""daybot 하락장 쉬기(10-19 대장 결정) — 전날 국면 35% 미만이면 신규매수 안 함, 국면 모르면 막지 않음."""
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


class _Note:
    def __init__(self): self.msgs = []
    def send(self, m, critical=False): self.msgs.append(m)


class DownMarket(unittest.TestCase):
    def test_threshold_and_fail_open(self):
        f = daybot.DayBot._down_market
        self.assertTrue(f(24.4))
        self.assertFalse(f(35.0))
        self.assertFalse(f(None))                      # 국면 못 구하면 막지 않음
        old, daybot.PAUSE_BELOW_PCT = daybot.PAUSE_BELOW_PCT, 0
        try:
            self.assertFalse(f(10))                    # 0이면 끔
        finally:
            daybot.PAUSE_BELOW_PCT = old

    def test_morning_message(self):
        b = object.__new__(daybot.DayBot)
        b._regime, b._scan_notifier = (None, None), _Note()
        daybot.market_regime.latest = lambda: ("2026-10-16", 24.4)
        self.assertEqual(b._market_breadth(), 24.4)
        self.assertIn("하락장", b._scan_notifier.msgs[0]); self.assertIn("1종목·50만만 운영", b._scan_notifier.msgs[0])  # 10-26 하락장 1슬롯
        b._regime = (None, None)
        daybot.market_regime.latest = lambda: ("2026-10-16", 42.0)
        b._market_breadth()
        self.assertIn("약한 장", b._scan_notifier.msgs[-1])


class DipEngineBuy(unittest.TestCase):
    """★ 10-30 음봉 엔진 — 지수 -2%↓면 후보 1순위를 하락장 슬롯 금액(50만)으로, 지수 괜찮으면 안 삼."""
    def _bot(self, idx):
        b = object.__new__(daybot.DayBot)
        b.positions, b.sold_today, b.code_name_map, b.notes, b.bought = {}, {}, {}, [], []
        b._last_dip_ts, b._dip_uni = 0.0, (None, {})
        b._notify = lambda m, critical=False: b.notes.append(m)
        b._daily_loss_hit = lambda: False
        b.db = type("D", (), {"log_candidate": lambda self, *a, **k: None})()
        b.api = type("A", (), {"get_market_index": lambda self: idx,
                               "get_multi_price": lambda self, codes, pause=0: {"A": {"price": 116600, "chg": -15.6, "high": 0}}})()
        b._do_buy = lambda code, name, price, tier, **k: (b.bought.append((code, tier, k.get("amount_cap"))), True)[1]
        return b

    def test_buy_on_index_down(self):
        old = daybot.dip_leader.universe, daybot.get_all_positions, daybot._lina_daybot_watches
        daybot.dip_leader.universe = lambda today: {"A": {"name": "대덕전자", "value_eok": 1566, "r60": 42, "hi5": 140100}}
        daybot.get_all_positions, daybot._lina_daybot_watches = None, lambda: set()
        try:
            b = self._bot({"kospi": -5.2, "kosdaq": -4.4})
            b._run_dip_engine()
            self.assertEqual(b.bought, [("A", daybot.dip_leader.TAG, daybot.DOWN_AMOUNT)])
            self.assertIn("음봉 엔진 매수", b.notes[0])
            b = self._bot({"kospi": -1.0, "kosdaq": -0.5})
            b._run_dip_engine()
            self.assertEqual(b.bought, [])
        finally:
            daybot.dip_leader.universe, daybot.get_all_positions, daybot._lina_daybot_watches = old


class ManualAdd(unittest.TestCase):
    """★ 10-27 대장이 daybot 한미반도체 2주에 1주 더 삼 → daybot이 3주·잔고 평단으로 같이 관리."""
    def _bot(self):
        import threading
        b = object.__new__(daybot.DayBot)
        b._positions_lock = threading.Lock()
        b.positions = {"042700": {"qty": 2, "entry_price": 195300, "buy_ts": 0, "buy_tag": "tier2_주도주"}}
        b._pending_orders, b._sell_verify, b._cancel_reconcile = {}, {}, {}
        b.code_name_map, b.notes, b.updated = {"042700": "한미반도체"}, [], []
        b._notify = lambda m, critical=False: b.notes.append(m)
        b.db = type("D", (), {"update_open_buy": lambda s, code, qty, buy_price=None: b.updated.append((qty, buy_price))})()
        return b

    def test_adopt_added_qty(self):
        old = daybot._master_upsert
        daybot._master_upsert = None
        try:
            b = self._bot()
            b._check_manual_sells({"042700": {"qty": 3, "entry_price": 195500}})
            self.assertEqual((b.positions["042700"]["qty"], b.positions["042700"]["entry_price"]), (3, 195500))
            self.assertEqual(b.updated, [(3, 195500)]); self.assertIn("2→3주", b.notes[0])
            b = self._bot()
            b._pending_orders["042700"] = ("o", "n", 1, False)                 # daybot 자기 주문이 걸려 있으면 안 건드림
            b._check_manual_sells({"042700": {"qty": 3, "entry_price": 195500}})
            self.assertEqual(b.positions["042700"]["qty"], 2)
            b = self._bot()
            b._partial_verify = {"042700": {"qty": 1}}                      # 1차익절 체결 확인 중(잔고가 커 보임)도 안 건드림
            b._check_manual_sells({"042700": {"qty": 3, "entry_price": 195500}})
            self.assertEqual(b.positions["042700"]["qty"], 2)
        finally:
            daybot._master_upsert = old


if __name__ == "__main__":
    unittest.main()
