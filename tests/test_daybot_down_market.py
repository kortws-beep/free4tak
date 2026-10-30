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
    """★ 10-30 음봉 엔진 — 지수 -2%↓면 후보 1순위를 음봉 슬롯 금액(50만)으로.
    ★ 10-31 장중 -2%↓ 찍었으면 됨(반등 기다리지 않음, 14:30부터) · 첫 하락날/-15%↓ 무너진 장(situation)이면 안 삼."""
    def _bot(self, idx):
        b = object.__new__(daybot.DayBot)
        b.positions, b.sold_today, b.code_name_map, b.notes, b.bought = {}, {}, {}, [], []
        b._last_dip_ts, b._dip_uni, b._dip_sit = 0.0, (None, {}), ("", False, "")
        b._notify = lambda m, critical=False: b.notes.append(m)
        b._daily_loss_hit = lambda: False
        b.db = type("D", (), {"log_candidate": lambda self, *a, **k: None})()
        b.api = type("A", (), {"get_multi_price": lambda self, codes, pause=0: {
            "A": {"price": 116600, "chg": -15.6, "high": 0, "low": 115000},
            "Z": {"price": 40000, "chg": -17.5, "high": 0, "low": 37000}}})()     # 알멕이 반등은 더 크지만 NEW·SUB 아님
        self._st = idx
        b._do_buy = lambda code, name, price, tier, **k: (b.bought.append((code, tier, k.get("amount_cap"))), True)[1]
        return b

    def test_buy_on_index_down(self):
        D = daybot.dip_leader
        old = (D.universe, daybot.get_all_positions, daybot._lina_all_watches, D.index_status, D.watch_codes, D.situation)
        D.universe = lambda today: {"A": {"name": "대덕전자", "value_eok": 1566, "r60": 42, "hi5": 140100},
                                    "Z": {"name": "알멕", "value_eok": 9999, "r60": 96, "hi5": 80000}}
        daybot.get_all_positions, daybot._lina_all_watches = None, lambda: set()
        D.watch_codes = lambda api, h: {"A"}                                  # ★ NEW·SUB엔 대덕전자만(알멕 없음)
        D.situation = lambda api, st, today: (True, "kospi 연속하락 2일")
        try:
            D.index_status = lambda: {"kospi": (-5.2, 0.2), "kosdaq": (-4.4, 0.1)}    # 반등 없어도 장중 -2%↓면
            b = self._bot(None)
            b._run_dip_engine()
            self.assertEqual(b.bought, [("A", D.TAG, daybot.DOWN_AMOUNT)])
            self.assertIn("음봉 엔진 매수", b.notes[0]); self.assertIn("50% 익절", b.notes[0])
            D.index_status = lambda: {"kospi": (-1.0, 0.5), "kosdaq": (-0.5, 0.2)}    # 저가도 -1.5%: 안 빠진 날
            b = self._bot(None)
            b._run_dip_engine()
            self.assertEqual(b.bought, [])
            D.index_status = lambda: {"kospi": (-5.2, 0.6), "kosdaq": (-4.4, 0.2)}
            D.situation = lambda api, st, today: (False, "첫 하락날이라 안 삼")      # ★ 10-31
            b = self._bot(None)
            b._run_dip_engine()
            self.assertEqual(b.bought, [])
            D.situation = lambda api, st, today: (True, "")
            daybot._lina_all_watches = lambda: {"A"}                          # ★ 10-30 대장이 키움에 들고 리나 등록한 종목
            b = self._bot(None)
            b._run_dip_engine()
            self.assertEqual(b.bought, [])
        finally:
            (D.universe, daybot.get_all_positions, daybot._lina_all_watches, D.index_status, D.watch_codes, D.situation) = old


class DipEngineExit(unittest.TestCase):
    """★ 10-31 대장 — +5%에 50% → 나머지 고점 -5% 트레일링(본전 보호), 그 전 손절 -10%, 5거래일째 15:10 이후 정리."""
    def _run(self, pos, price, hhmm="1300"):
        b = object.__new__(daybot.DayBot)
        b.positions = {"X": dict({"entry_price": 10000, "qty": 10, "buy_tag": daybot.dip_leader.TAG}, **pos)}
        b.sold, b.partial = [], []
        b._get_current_price = lambda code: price
        b._do_sell = lambda code, qty, reason, cur: b.sold.append(reason)
        def part(code, rate, cur, ratio=0, label=""):
            b.partial.append((ratio, label)); b.positions[code]["partial_done"] = True; return True
        b._do_partial_sell = part
        old = daybot.now_hhmm
        daybot.now_hhmm = lambda: hhmm
        try:
            b._check_all_positions_for_exit()
        finally:
            daybot.now_hhmm = old
        return b

    def test_rules(self):
        self.assertEqual(self._run({}, 9600).sold, [])                       # -4%: 예전 -3.5% 손절선 아래지만 안 팜
        self.assertIn("음봉손절", self._run({}, 8990).sold[0])                # -10.1%
        b = self._run({}, 10520)                                              # +5.2% → 50%
        self.assertEqual(b.partial, [(0.5, "음봉익절50%")]); self.assertEqual(b.sold, [])
        self.assertEqual(self._run({"partial_done": True, "peak_price": 11000}, 10500).sold, [])        # 고점 -4.5%
        self.assertIn("음봉트레일링", self._run({"partial_done": True, "peak_price": 11000}, 10440).sold[0])  # -5.1%
        self.assertIn("음봉트레일링", self._run({"partial_done": True, "peak_price": 10300}, 10000).sold[0])  # 본전 보호
        self.assertEqual(self._run({"held_trading_days": 4}, 9800, "1500").sold, [])
        self.assertIn("음봉기한정리", self._run({"held_trading_days": 4}, 9800, "1511").sold[0])


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
