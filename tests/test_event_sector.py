"""11-04 이벤트 섹터 엔진(음봉 엔진 2) — MTN 아침 1테마 · 지수 -2% 뒤 20분 · 새 저가 대비 +1% 반등 1등 · 일반 엔진 멈춤."""
import unittest

from _helpers import stub, use_temp_cwd

for _n in ["kis_websocket", "kiwoom_api", "notifier", "master_db", "sector_monitor"]:
    stub(_n)
stub("kis_websocket", KisWebSocket=object)
stub("kiwoom_api", KiwoomAPI=object)
stub("notifier", Notifier=object)
stub("kis_api", KisAPI=object)

_tmp = use_temp_cwd()
import event_sector as E  # noqa: E402
import daybot             # noqa: E402


class Pure(unittest.TestCase):
    def test_choice_and_store(self):
        th = ["태양광", "2차전지"]
        self.assertEqual(E.parse_choice('답: {"theme": "태양광", "why": "블루웨이브"}', th), ("태양광", "블루웨이브"))
        self.assertEqual(E.parse_choice('{"theme": "우주", "why": "x"}', th)[0], None)      # 목록에 없는 이름
        self.assertEqual(E.parse_choice("모르겠음", th), (None, ""))
        E.save_theme("2026-11-04", "태양광", "블루웨이브", db="e.db")
        self.assertEqual(E.load_theme("2026-11-04", db="e.db"), ("태양광", "블루웨이브"))
        self.assertEqual(E.load_theme("2026-11-05", db="e.db"), (None, None))
        self.assertIn("태양광 / 2차전지", E.choose_prompt("요약", th, "2026-11-04"))

    def test_natural_only(self):
        # ★ 11-04 대장 "억지로 고르면 안 돼 — 자연스럽게 많이 언급될 때만": 10번↑ · 방송 셋 중 두 구간↑
        mem = [("1", "OCI홀딩스"), ("2", "한화솔루션"), ("3", "LS")]
        spread = "태양광 얘기 " * 4 + "다른 얘기 " * 60 + "OCI홀딩스 한화솔루션 " * 3 + "다른 얘기 " * 60 + "태양광 "
        self.assertEqual(E.mention_stats(spread, "태양광", mem), (11, 3))
        self.assertTrue(E.natural(spread, "태양광", mem))
        corner = "다른 얘기 " * 200 + "태양광 OCI홀딩스 " * 8                 # 끝 코너에서만 몰아서
        self.assertFalse(E.natural(corner, "태양광", mem))
        self.assertFalse(E.natural("LS LS LS " * 20, "태양광", mem))       # 'LS' 같은 짧은 이름은 안 셈

    def test_pick(self):
        self.assertEqual(E.plus_min("1105", 20), "1125")
        lows = {}
        E.update_lows(lows, {"A": {"price": 100}, "B": {"price": 200}})
        E.update_lows(lows, {"A": {"price": 98}, "B": {"price": 210}})
        q = {"A": {"price": 99.5, "chg": 3}, "B": {"price": 204, "chg": 9}, "C": {"price": 50, "chg": 16}}
        self.assertEqual(E.pick(q, lows)[0], "B")                 # B 새 저가 200 → +2% / A 98 → +1.5%
        self.assertEqual(E.pick(q, lows, exclude={"B"})[0], "A")
        self.assertIsNone(E.pick({"A": {"price": 98.5, "chg": 1}}, lows))   # +0.5%뿐
        lows["C"] = 40
        self.assertNotEqual(E.pick(q, lows)[0], "C")               # +15% 넘은 종목 제외


class Engine(unittest.TestCase):
    def _bot(self, hhmm):
        b = object.__new__(daybot.DayBot)
        b.positions, b.sold_today, b.code_name_map, b.notes, b.bought = {}, {}, {}, [], []
        b._evt = {"date": daybot.today_str(), "theme": "태양광", "why": "블루웨이브", "codes": {"A": "OCI", "B": "한화솔루션"},
                  "t0": None, "start": None, "lows": {}, "done": False, "ts": 0.0, "loaded": 9e18}
        b._notify = lambda m, critical=False: b.notes.append(m)
        b._daily_loss_hit = lambda: False
        b._market_breadth = lambda: 15.0
        b.db = type("D", (), {"log_candidate": lambda self, *a, **k: None})()
        b.prices = {"A": {"price": 100, "chg": 2}, "B": {"price": 50, "chg": 1}}
        b.api = type("A", (), {"get_multi_price": lambda s, codes, pause=0: dict(b.prices)})()
        b._do_buy = lambda code, name, price, tier, **k: (b.bought.append((code, tier, k.get("amount_cap"))), True)[1]
        self._old = (daybot.now_hhmm, daybot.dip_leader.index_status, daybot.get_all_positions, daybot._lina_all_watches)
        daybot.now_hhmm = lambda: hhmm
        daybot.get_all_positions, daybot._lina_all_watches = None, lambda: set()
        return b

    def tearDown(self):
        daybot.now_hhmm, daybot.dip_leader.index_status, daybot.get_all_positions, daybot._lina_all_watches = self._old

    def test_flow(self):
        b = self._bot("1105")
        daybot.dip_leader.index_status = lambda: {"kospi": (-1.5, 0.1)}
        b._run_event_engine()
        self.assertIsNone(b._evt["t0"]); self.assertFalse(b._evt_hunting())
        daybot.dip_leader.index_status = lambda: {"kospi": (-2.6, 0.1)}
        b._run_event_engine()
        self.assertIsNone(b._evt["t0"])                            # ★ -2.7%: 이벤트 엔진은 -3%↓만
        daybot.dip_leader.index_status = lambda: {"kospi": (-2.9, 0.2)}
        b._run_event_engine()                                      # 11:05 저가 -3.1% 확인 → 11:25부터
        self.assertEqual((b._evt["t0"], b._evt["start"]), ("1105", "1125")); self.assertTrue(b._evt_hunting())
        self.assertIn("1슬롯 확보", b.notes[0])
        daybot.now_hhmm = lambda: "1120"
        b._run_event_engine(); self.assertEqual(b._evt["lows"], {})  # 지켜보기만
        daybot.now_hhmm = lambda: "1125"
        b._run_event_engine(); self.assertEqual(b.bought, [])        # 첫 분은 새 저가만
        b.prices = {"A": {"price": 101.5, "chg": 3.5}, "B": {"price": 50.2, "chg": 1.4}}
        daybot.now_hhmm = lambda: "1126"
        b._run_event_engine()
        self.assertEqual(b.bought, [("A", "event_sector", daybot.DOWN_AMOUNT)]); self.assertTrue(b._evt["done"])
        self.assertFalse(b._evt_hunting())                            # 산 뒤엔 양보 끝
        self.assertIn("이벤트 섹터 매수 OCI", b.notes[-1])

    def test_slot_full(self):
        import threading
        b = self._bot("1130")
        b._positions_lock = threading.Lock()
        b._evt.update(t0="1100", start="1120", lows={"A": 98.0, "B": 50})
        b.positions = {"Z": {"buy_tag": "tier2_주도주"}, "N": {"buy_tag": daybot.dip_leader.TAG}}   # 일반·음봉 둘 다 참
        b._run_event_engine()
        self.assertEqual(b.bought, []); self.assertFalse(b._evt["done"])
        # ★ 11-05 대장 — 일반 슬롯이 찼어도 음봉 슬롯이 비었으면 빌려 씀(음봉 슬롯 금액), 그 뒤 음봉 슬롯은 찬 걸로
        b.positions = {"Z": {"buy_tag": "tier2_주도주"}}
        def buy(code, name, price, tier, **k):
            b.bought.append((code, tier, k.get("amount_cap"))); b.positions[code] = {"buy_tag": tier}; return True
        b._do_buy = buy
        b._run_event_engine()
        self.assertEqual(b.bought, [("A", "event_sector", daybot.DIP_AMOUNT)])
        self.assertEqual(b.positions["A"].get("slot"), "dip")
        self.assertEqual((b._dip_count(), b._regular_count()), (1, 1))
        self.assertIn("음봉 슬롯 빌려", b.notes[-1])


if __name__ == "__main__":
    unittest.main()
