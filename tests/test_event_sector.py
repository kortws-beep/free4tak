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
        th = ["태양광", "2차전지", "항공"]
        self.assertEqual(E.parse_choice('답: {"themes": [{"theme": "태양광", "why": "블루웨이브"}, {"theme": "우주", "why": "x"}, '
                                        '{"theme": "항공", "why": "유가"}]}', th), [("태양광", "블루웨이브"), ("항공", "유가")])
        self.assertEqual(E.parse_choice('{"theme": "태양광", "why": "예전 꼴"}', th), [("태양광", "예전 꼴")])
        self.assertEqual(E.parse_choice("모르겠음", th), [])
        E.save_theme("2026-11-04", ["태양광", "항공"], "이유", db="e.db")
        self.assertEqual(E.load_theme("2026-11-04", db="e.db"), (["태양광", "항공"], "이유"))
        self.assertEqual(E.load_theme("2026-11-05", db="e.db"), ([], None))
        self.assertIn("태양광 / 2차전지", E.choose_prompt("요약", th, "2026-11-04"))

    def test_leaders_bottom(self):
        q = {"A": {"price": 10, "value": 5e9}, "B": {"price": 10, "value": 9e9}, "C": {"price": 10, "value": 1e9}}
        self.assertEqual(E.leaders(q, {"태양광": {"A": "a", "B": "b"}, "항공": {"C": "c"}}), {"B": "태양광", "C": "항공"})
        st = {}
        E.track_index(st, {"kospi": -3.1}, "1001")
        E.track_index(st, {"kospi": -5.0}, "1015")                 # 새 저가 → 시각 갱신
        E.track_index(st, {"kospi": -5.02}, "1020")                # 0.05%p 안 되는 건 무시
        self.assertEqual(st["idx"]["kospi"], (-5.0, "1015"))
        self.assertFalse(E.bottom_ok(st, {"kospi": (-3.0, 2.1)}, "1030"))   # 15분뿐
        self.assertTrue(E.bottom_ok(st, {"kospi": (-3.0, 2.1)}, "1035"))    # 20분 새 저가 없음 & +2.1%
        self.assertFalse(E.bottom_ok(st, {"kospi": (-4.5, 0.6)}, "1040"))   # 반등 +0.6%뿐

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
        import threading
        b = object.__new__(daybot.DayBot)
        b._positions_lock = threading.Lock()
        b.positions, b.sold_today, b.code_name_map, b.notes, b.bought = {}, {}, {}, [], []
        b._evt = {"date": daybot.today_str(), "theme": "태양광", "themes": {"태양광": {"A": "OCI", "B": "한화솔루션"}},
                  "why": "블루웨이브", "codes": {"A": "OCI", "B": "한화솔루션"},
                  "t0": None, "start": None, "lows": {}, "done": False, "ts": 0.0, "loaded": 9e18}
        b._notify = lambda m, critical=False: b.notes.append(m)
        b._daily_loss_hit = lambda: False
        b._market_breadth = lambda: 15.0
        b.db = type("D", (), {"log_candidate": lambda self, *a, **k: None})()
        b.prices = {"A": {"price": 100, "chg": 2, "value": 9e9}, "B": {"price": 50, "chg": 1, "value": 1e9}}
        b.api = type("A", (), {"get_multi_price": lambda s, codes, pause=0: dict(b.prices)})()
        def buy(code, name, price, tier, **k):
            b.bought.append((code, tier, k.get("amount_cap"))); b.positions[code] = {"buy_tag": tier}; return True
        b._do_buy = buy
        self._old = (daybot.now_hhmm, daybot.dip_leader.index_status, daybot.get_all_positions, daybot._lina_all_watches)
        daybot.get_all_positions, daybot._lina_all_watches = None, lambda: set()
        self._at(hhmm)
        return b

    def _at(self, hhmm, idx=None):
        daybot.now_hhmm = lambda: hhmm
        if idx is not None:
            daybot.dip_leader.index_status = lambda: idx

    def tearDown(self):
        daybot.now_hhmm, daybot.dip_leader.index_status, daybot.get_all_positions, daybot._lina_all_watches = self._old

    def test_flow(self):
        b = self._bot("1001")
        self._at("1001", {"kospi": (-2.6, 0.1)})
        b._run_event_engine(); self.assertIsNone(b._evt["t0"])          # -2.7%: 이벤트 엔진은 -3%↓만
        self._at("1001", {"kospi": (-3.1, 0.0)})
        b._run_event_engine()
        self.assertEqual((b._evt["t0"], b._evt["start"]), ("1001", "1021")); self.assertTrue(b._evt_hunting())
        self.assertIn("바닥 확인", b.notes[0])
        self._at("1022", {"kospi": (-3.3, 0.2)})                          # 20분 지났지만 지수 반등 없음 → 안 봄
        b._run_event_engine(); self.assertEqual(b._evt["lows"], {})
        self._at("1045", {"kospi": (-2.0, 1.3)})                          # 저가 -3.3%(10:22) 뒤 23분·+1.3% → 바닥 확인
        b._run_event_engine(); self.assertEqual(b.bought, []); self.assertEqual(set(b._evt["lows"]), {"A"})   # 대장(대금 1위)만
        b.prices = {"A": {"price": 101.5, "chg": 3.5, "value": 9e9}, "B": {"price": 60, "chg": 20, "value": 1e9}}
        self._at("1046", {"kospi": (-1.9, 1.4)})
        b._run_event_engine()
        self.assertEqual(b.bought, [("A", "event_sector", daybot.DOWN_AMOUNT)]); self.assertTrue(b._evt["done"])
        self.assertFalse(b._evt_hunting()); self.assertIn("대장", b.notes[-1])

    def test_resume_after_restart(self):
        # ★ 11-06 장중 재시작해도 오늘 이벤트 엔진 진행(t0·바닥 추적·끝남)을 이어받음
        b = self._bot("1100")
        b._evt = {"date": None}
        b._evt_saved = {"date": daybot.today_str(), "t0": "0901", "start": "0921", "idx": {"kospi": [-3.4, "0905"]}, "done": False}
        old = daybot.event_sector.load_theme
        daybot.event_sector.load_theme = lambda d: ([], None)
        try:
            b._evt_load()
        finally:
            daybot.event_sector.load_theme = old
        self.assertEqual((b._evt["t0"], b._evt["start"], b._evt["idx"]["kospi"][1]), ("0901", "0921", "0905"))

    def test_panic_day(self):
        b = self._bot("1001")
        self._at("1001", {"kospi": (-5.3, 0.1)})                          # ★ 11-05 저가 -5%↓ 폭락날 쉼
        b._run_event_engine()
        self.assertTrue(b._evt["done"]); self.assertIn("폭락", b.notes[0]); self.assertEqual(b.bought, [])

    def test_borrow_dip_slot(self):
        b = self._bot("1130")
        self._at("1130", {"kospi": (-2.0, 1.5)})
        b._evt.update(t0="1001", start="1021", lows={"A": 98.0}, idx={"kospi": (-3.4, "1005")})
        b.positions = {"Z": {"buy_tag": "tier2_주도주"}, "N": {"buy_tag": daybot.dip_leader.TAG}}   # 일반·음봉 둘 다 참
        b._run_event_engine()
        self.assertEqual(b.bought, []); self.assertFalse(b._evt["done"])
        b.positions = {"Z": {"buy_tag": "tier2_주도주"}}                  # ★ 11-05 음봉 슬롯 비면 빌려 씀
        b._run_event_engine()
        self.assertEqual(b.bought, [("A", "event_sector", daybot.DIP_AMOUNT)])
        self.assertEqual(b.positions["A"].get("slot"), "dip")
        self.assertEqual((b._dip_count(), b._regular_count()), (1, 1))
        self.assertIn("음봉 슬롯 빌려", b.notes[-1])


if __name__ == "__main__":
    unittest.main()
