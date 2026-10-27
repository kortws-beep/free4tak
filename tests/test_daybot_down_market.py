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
