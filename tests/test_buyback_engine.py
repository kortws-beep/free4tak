"""11-07 자사주 엔진 — 본문 파싱·판정(주주가치·1%↑) · 데이봇 매수·전용 슬롯·5거래일/손절 매도."""
import sqlite3
import unittest

from _helpers import stub, use_temp_cwd

for _n in ["kis_websocket", "kiwoom_api", "notifier", "master_db", "sector_monitor"]:
    stub(_n)
stub("kis_websocket", KisWebSocket=object)
stub("kiwoom_api", KiwoomAPI=object)
stub("notifier", Notifier=object)
stub("kis_api", KisAPI=object)

_tmp = use_temp_cwd()
import buyback_signal as B  # noqa: E402
import daybot               # noqa: E402

DOC = ("자기주식 취득 결정 1. 취득예정주식(주) 보통주식 200,462 기타주식 - 2. 취득예정금액(원) 보통주식 5,693,131,798 "
       "기타주식 - 3. 취득예상기간 … 5. 취득목적 주가안정 및 주주가치 제고 6. 취득방법 유가증권시장을 통한 장내 매수")


class Signal(unittest.TestCase):
    def test_parse_judge(self):
        d = B.parse_doc(DOC, "취득결정")
        self.assertEqual((d["shares"], d["amount"], d["purpose"]), (200462, 5693131798, "주가안정 및 주주가치 제고"))
        self.assertEqual(B.kind_of("자기주식 취득 결정"), "취득결정")
        self.assertIsNone(B.kind_of("[정정] 자기주식 취득 결정"))
        self.assertTrue(B.judge(d, "취득결정", listed=10_000_000, price=28400)[0])                # 2.0%
        self.assertFalse(B.judge(d, "취득결정", listed=50_000_000, price=28400)[0])               # 0.4%
        self.assertFalse(B.judge(dict(d, purpose="임직원 주식보상"), "취득결정", 10_000_000, 28400)[0])
        self.assertAlmostEqual(B.ratio_pct({"amount": 1e10}, 10_000_000, 50000), 2.0)            # 신탁: 금액 ÷ 시총

    def test_events(self):
        c = sqlite3.connect("k.db")
        c.execute("CREATE TABLE disc (acptno TEXT, date TEXT, time TEXT, company TEXT, code TEXT, title TEXT, submitter TEXT, kind TEXT)")
        c.executemany("INSERT INTO disc VALUES (?,?,?,?,?,?,'','')", [
            ("1", "2026-11-09", "10:00", "가", "A", "자기주식 취득 결정"),
            ("2", "2026-11-09", "10:05", "나", "B", "주식 소각 결정"),
            ("3", "2026-11-06", "17:00", "다", "C", "자기주식취득 신탁계약 체결 결정"),
            ("4", "2026-11-06", "11:00", "라", "D", "자기주식 취득 결정")])
        c.commit(); c.close()
        ev = B.todays_events("2026-11-09", "2026-11-06", db="k.db")
        self.assertEqual([e[0] for e in ev], ["3", "1"])                 # 전날 장후 + 오늘(소각·전날 장중 제외)


class Engine(unittest.TestCase):
    def setUp(self):
        self._old = (daybot.now_hhmm, B.todays_events, B.detail, daybot.get_all_positions, daybot._lina_all_watches)

    def _bot(self, hhmm, evs, det):
        import threading
        b = object.__new__(daybot.DayBot)
        b._positions_lock = threading.Lock()
        b.positions, b.sold_today, b.code_name_map, b.notes, b.bought = {}, {}, {}, [], []
        b._bb = {"date": daybot.today_str(), "done": [], "ts": 0.0, "kind_ts": 9e18}
        b._notify = lambda m, critical=False: b.notes.append(m)
        b._daily_loss_hit = lambda: False
        b._name = lambda c: c
        b.db = type("D", (), {"log_candidate": lambda self, *a, **k: None})()
        b.api = type("A", (), {"get_market_data": lambda s, c: {"stck_prpr": "28400", "lstn_stcn": "10000000", "prdy_ctrt": "1.5"}})()
        b._do_buy = lambda code, name, price, tier, **k: (b.bought.append((code, tier, k.get("amount_cap"))), True)[1]
        daybot.now_hhmm = lambda: hhmm
        B.todays_events = lambda d, p=None: evs
        B.detail = lambda a, k: det
        daybot.get_all_positions, daybot._lina_all_watches = None, lambda: set()
        return b

    def tearDown(self):
        daybot.now_hhmm, B.todays_events, B.detail, daybot.get_all_positions, daybot._lina_all_watches = self._old

    def test_buy_flow(self):
        det = B.parse_doc(DOC, "취득결정")
        today = daybot.today_str()
        b = self._bot("1001", [("1", today, "10:00", "A", "가", "취득결정")], det)
        b._run_buyback_engine()
        self.assertEqual(b.bought, [])                                   # 공시 2분 전 — 아직
        daybot.now_hhmm = lambda: "1002"
        b._run_buyback_engine()
        self.assertEqual(b.bought, [("A", B.TAG, B.AMOUNT)]); self.assertIn("자사주 엔진 매수", b.notes[0])
        b._run_buyback_engine(); self.assertEqual(len(b.bought), 1)       # 같은 공시 다시 안 봄
        b2 = self._bot("0901", [("9", "2026-11-06", "17:00", "C", "다", "취득결정")], det)
        b2._run_buyback_engine(); self.assertEqual(b2.bought, [])         # 전날 장후 공시는 09:05부터
        daybot.now_hhmm = lambda: "0905"
        b2._run_buyback_engine(); self.assertEqual(len(b2.bought), 1)
        b3 = self._bot("1100", [("5", today, "10:00", "A", "가", "취득결정")], dict(det, purpose="임직원 보상"))
        b3._run_buyback_engine(); self.assertEqual(b3.bought, []); self.assertIn("5", b3._bb["done"])

    def test_slot_and_exit(self):
        b = object.__new__(daybot.DayBot)
        b.positions = {"A": {"buy_tag": B.TAG}, "Z": {"buy_tag": "tier2_주도주"}}
        self.assertEqual((b._bb_count(), b._regular_count()), (1, 1))
        b.positions = {"X": {"entry_price": 10000, "qty": 10, "buy_tag": B.TAG, "held_trading_days": 4}}
        b.sold = []
        b._get_current_price = lambda c: 9500
        b._do_sell = lambda code, qty, reason, cur: b.sold.append(reason)
        old = daybot.now_hhmm
        try:
            daybot.now_hhmm = lambda: "1400"; b._check_all_positions_for_exit(); self.assertEqual(b.sold, [])   # -5%: 손절 아님
            daybot.now_hhmm = lambda: "1511"; b._check_all_positions_for_exit(); self.assertIn("자사주기한정리", b.sold[0])
            b.sold = []; b._get_current_price = lambda c: 8900
            daybot.now_hhmm = lambda: "1000"; b._check_all_positions_for_exit(); self.assertIn("자사주손절", b.sold[0])
        finally:
            daybot.now_hhmm = old


if __name__ == "__main__":
    unittest.main()
