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

    def test_existing_position_stop_kept(self):
        pt = {"X": tracker(stop_price=80)}       # 이미 보유 중 — 손절가 그대로(대장 결정)
        self._check(pt, 95)
        self.assertEqual(pt["X"]["stop_price"], 80); self.assertEqual(self.sold, [])

    def test_hold_never_stopped_but_rides_new_take_profit(self):
        pt = {"X": tracker(hold=True)}
        self.assertIsNone(self._check(pt, 80))   # 홀드 — 손절 안 함
        self._check(pt, 108)                     # +8% — 30% 익절, 홀드 해제
        self.assertEqual(self.sold[0][1], 3); self.assertFalse(pt["X"]["hold"])

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

    def test_manual_sell_closes_adopted_open_row(self):
        """★ 10-22 대덕전자 — 재시작 때 받아둔 열린 행이 있으면 수동매도가 그 행을 닫음(유령 행 방지), 일부 매도는 떼어냄."""
        db = sbot_db.SwingDB(); db.init_db()
        con = sqlite3.connect(sbot_db.SBOT_HIST_DB)
        con.execute("insert into trades (code, buy_price, buy_time, qty) values ('M', 100, 'x', 15)"); con.commit()
        db.save_manual_trade("M", "엠", 100, 110, 15, "수동매도")
        self.assertEqual(con.execute("select count(*), sum(sell_price is null) from trades where code='M'").fetchone(), (1, 0))
        con.execute("insert into trades (code, buy_price, buy_time, qty) values ('P', 100, 'x', 10)"); con.commit()
        db.save_manual_trade("P", "피", 100, 120, 4, "수동매도")
        self.assertEqual(con.execute("select qty, sell_price from trades where code='P' order by id").fetchall(),
                         [(6, None), (4, 120.0)])
        db.save_manual_trade("N", "새것", 50, 55, 3, "수동매도")                  # 열린 행 없으면 예전처럼 새 행
        self.assertEqual(con.execute("select qty, sell_price from trades where code='N'").fetchall(), [(3, 55.0)])


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


class PoolLiquidity(unittest.TestCase):
    """★ 10-28 대장 — 통합 4소스(모멘텀·추세·완화·유튜브)도 시총 3,000억·20일 평균 거래대금 30억 이상만(파로스아이바이오 763억·4억 사례)."""
    def test_reason(self):
        R = sbot.pool_liquidity_reason
        self.assertIn("시총", R(763, 4.0))
        self.assertIn("거래대금", R(5000, 4.0))
        self.assertIn("모름", R(5000, None))
        self.assertEqual(R(5000, 45.0), "")
        self.assertEqual(sbot.POOL_GRADES, ("momentum", "trend", "light", "youtube"))

    def test_avg_value(self):
        import sqlite3 as _sq
        c = _sq.connect("daily.db")
        c.execute("CREATE TABLE kr_stock_daily_data (stock_name TEXT, date TEXT, trade_value INTEGER)")
        c.executemany("INSERT INTO kr_stock_daily_data VALUES (?,?,?)",
                      [("가", f"2026-10-{d:02d}", 4e8) for d in range(1, 25)] + [("나", "2026-10-01", 9e9)])
        c.commit(); c.close()
        self.assertAlmostEqual(sbot.avg_trade_value_eok("가", db="daily.db"), 4.0)
        self.assertIsNone(sbot.avg_trade_value_eok("나", db="daily.db"))        # 5일 미만이면 모름


class SbotPauseDownMarket(unittest.TestCase):
    """2026-10-17 — 전날 하락장(국면<35%)·전날 투매(리나 15:40 기록)면 신규매수 쉬기."""
    def test_reason(self):
        import json as _j
        R = sbot.SBot._down_market_reason
        self.assertIn("하락장", R(("2026-10-16", 30.0), "없음.json"))
        self.assertEqual(R(("2026-10-16", 40.0), "없음.json"), "")
        self.assertEqual(R(None, "없음.json"), "")                     # 국면 못 구하면 막지 않음
        with open("cap.json", "w", encoding="utf-8") as f:
            _j.dump({"date": "2026-10-16", "share": 54.2}, f)
        self.assertIn("전날 투매(신저가 54%)", R(("2026-10-16", 40.0), "cap.json"))
        self.assertEqual(R(("2026-10-17", 40.0), "cap.json"), "")       # 투매 기록이 그 전날 것이 아니면 무시
        old = sbot.PAUSE_BELOW_PCT
        try:
            sbot.PAUSE_BELOW_PCT = 0
            self.assertEqual(R(("2026-10-16", 30.0), "cap.json"), "")   # .env로 끄기
        finally:
            sbot.PAUSE_BELOW_PCT = old

    def test_buys_skipped(self):
        old, old_slots = sbot.market_regime.latest, sbot.DOWN_SLOTS
        try:
            sbot.DOWN_SLOTS = 0                                         # 10-27 하락장 1슬롯을 끈 경우 = 예전처럼 완전 쉬기
            sbot.market_regime.latest = lambda: ("2026-10-16", 30.0)
            b = object.__new__(sbot.SBot)
            b.positions, b.peak_tracker, b.notes = {}, {}, []
            b._notify = lambda m, critical=False: b.notes.append(m)
            b.db = type("D", (), {"get_today_realized": lambda self: 0})()
            b._execute_buys_unified([{"code": "X"}], "1000", 10_000_000)   # 후보를 보면 KeyError(grade) — 안 봐야 함
            self.assertIn("신규매수 쉬기", b.notes[0])
        finally:
            sbot.market_regime.latest, sbot.DOWN_SLOTS = old, old_slots

    def test_down_slot(self):
        # ★ 10-27 대장 — 하락장에도 1슬롯은 운영. 하락장에 산 종목(상태파일)이 아직 보유 중이면 안 삼, 팔렸으면 정리 후 다시 1자리
        st = {"down_slot_codes": ["A"]}
        old = sbot._read_state, sbot._update_state, sbot.market_regime.latest
        try:
            sbot._read_state = lambda: dict(st)
            sbot._update_state = lambda **kw: st.update(kw)
            sbot.market_regime.latest = lambda: ("2026-10-16", 30.0)
            b = object.__new__(sbot.SBot)
            b.positions, b.peak_tracker, b.notes = {"A": {}}, {}, []
            b._notify = lambda m, critical=False: b.notes.append(m)
            b.db = type("D", (), {"get_today_realized": lambda self: 0})()
            self.assertEqual(b._down_slots_left(), 0)
            b._execute_buys_unified([{"code": "X"}], "1000", 10_000_000)   # 슬롯 찼으면 후보를 안 봄
            self.assertIn("1슬롯만 운영", b.notes[0])
            b.positions = {}                                                # A가 팔림
            self.assertEqual(b._down_slots_left(), 1)
            self.assertEqual(st["down_slot_codes"], [])
            b._regime = ("x", None)
            sbot.market_regime.latest = lambda: ("2026-10-16", 30.0)
            b._pause_reason = ""
            b.market_status = "normal"
            reached = []
            b._intraday_guard = type("G", (), {"check": lambda self, d, t: (reached.append(t) or "멈춤", False)})()
            b._execute_buys_unified([{"code": "X"}], "1000", 10_000_000)   # 자리가 있으면 하락장 관문을 지나 다음 점검까지 감
            self.assertEqual(reached, ["1000"])
        finally:
            sbot._read_state, sbot._update_state, sbot.market_regime.latest = old


class SubGroup(unittest.TestCase):
    """★ 10-21 관심그룹 SUB — new와 합치되 중복 빼고, sub에만 있는 종목은 따로 표시."""
    def test_merge(self):
        pool, only = sbot.merge_groups(["A", "B"], ["B", "C", "C", "D"])
        self.assertEqual(pool, ["A", "B", "C", "D"]); self.assertEqual(only, {"C", "D"})
        self.assertEqual(sbot.merge_groups(["A"], []), (["A"], set()))

    def test_load_with_sub_group(self):
        b = object.__new__(sbot.SBot)
        b.code_name_map, b.new_codes_list, b.sub_codes = {}, [], set()

        class Api:
            def get_watchlist_groups(self, hts_id):
                return {"001": "NEW", "002": "SUB", "003": "반도체"}

            def get_watchlist_stocks(self, grp, hts_id, cmap):
                return {"001": [("111111", "a"), ("222222", "b")], "002": [("222222", "b"), ("333333", "c")]}[grp]
        b.api = Api()
        os.environ["KIS_HTS_ID2"] = "x"
        b._load_new_codes()
        self.assertEqual(b.new_codes_list, ["111111", "222222", "333333"]); self.assertEqual(b.sub_codes, {"333333"})


class MarketStatusGate(unittest.TestCase):
    """★ 10-22 장 상태 판단(코스피 -2% 약세·-4.5% 중단) 끔 — 상태는 늘 normal, 장 초반 급락 강제 중단도 안 함."""
    def test_off_by_default(self):
        self.assertFalse(sbot.MARKET_STATUS_GATE)
        b = object.__new__(sbot.SBot)
        b.market_status, b._notify = "normal", lambda *a, **k: None

        class Api:
            def get_market_index(self): return {"kospi": -5.0, "kosdaq": -6.0}
        b.api = Api()
        b._update_market_status()
        self.assertEqual((b.market_status, b.market_rate), ("normal", -5.0))
        b._prev_kospi = -3.5
        self.assertFalse(b._check_opening_crash("0910"))


if __name__ == "__main__":
    unittest.main()
