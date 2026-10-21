"""데이봇 파이썬판 후보 소스 — 리나 기록 읽기, fallback(타임아웃 검색식만)/union 합치기."""
import datetime as dt
import os
import sqlite3
import threading
import unittest

from _helpers import stub, use_temp_cwd

for _n in ["kis_websocket", "kiwoom_api", "notifier", "master_db", "sector_monitor"]:
    stub(_n)
stub("kis_websocket", KisWebSocket=object)
stub("kiwoom_api", KiwoomAPI=object)
stub("notifier", Notifier=object)
stub("kis_api", KisAPI=object)

_tmp = use_temp_cwd()
import daybot_db  # noqa: E402
import daybot     # noqa: E402

NOW = dt.datetime(2026, 10, 8, 9, 40)


def build():
    db = os.path.abspath("log.db")
    if os.path.exists(db):
        os.remove(db)
    c = sqlite3.connect(db)
    for t in ("leader_obs", "danta_obs"):
        c.execute(f"CREATE TABLE {t} (date TEXT, time TEXT, code TEXT, name TEXT, passed INTEGER)")
    rows = [("leader_obs", "09:38", "111110", "주도A", 1), ("leader_obs", "09:20", "222220", "옛날", 1),
            ("leader_obs", "09:38", "333330", "탈락", 0), ("danta_obs", "09:39", "444440", "단타B", 1)]
    for t, tm, code, name, ok in rows:
        c.execute(f"INSERT INTO {t} VALUES ('2026-10-08', ?, ?, ?, ?)", (tm, code, name, ok))
    c.commit(); c.close()
    return db


class PythonSource(unittest.TestCase):
    def test_read_recent_passes(self):
        hits, names, latest = daybot_db.python_scan_hits(NOW, build())
        self.assertEqual(hits, {"111110": ["주도주검색식3"], "444440": ["단타000"]})   # 오래된·탈락 제외
        self.assertNotIn("3개월수급 당일주도주", latest)                                # ★ 10-22 소스에서 뺌(090930으로 교체)

    def _bot(self):
        b = object.__new__(daybot.DayBot)
        b.code_name_map, b._positions_lock = {}, threading.Lock()
        return b

    def test_fallback_fills_only_failed_conditions(self):
        db = build()
        daybot.python_scan_hits = lambda: daybot_db.python_scan_hits(NOW, db)
        daybot.SCAN_SOURCE = "fallback"
        tag_map, names = {"555550": ["단타000"]}, {}
        codes = self._bot()._merge_python_hits(["555550"], tag_map, names, {"단타000"})  # 주도주는 타임아웃
        self.assertEqual(codes, ["555550", "111110"])                 # 주도주만 파이썬으로, 단타는 키움 그대로
        self.assertEqual(tag_map["111110"], ["주도주검색식3"]); self.assertNotIn("444440", tag_map)
        daybot.SCAN_SOURCE = "union"
        codes = self._bot()._merge_python_hits(["555550"], {"555550": ["단타000"]}, {}, {"단타000"})
        self.assertEqual(sorted(codes), ["111110", "444440", "555550"])
        daybot.SCAN_SOURCE = "kiwoom"


class NewBuyCutoff(unittest.TestCase):
    """2026-10-08 — 신규매수는 09:40까지만(분봉 재현 백테스트 결과)."""
    def _scan_bot(self, bought):
        b = object.__new__(daybot.DayBot)
        b.positions, b.sold_today, b._sell_verify, b._cancel_reconcile = {}, {}, {}, {}
        b._recent_tags, b.code_name_map = {}, {}
        b._scan_conditions = lambda: (["111110"], {"111110": ["주도주검색식3"]})
        b._rank_candidates = lambda codes, tags: (codes, {"111110": "tier2_주도주"})
        b._name = lambda c: c

        class Api:
            def get_market_data(self, c): return {"stck_prpr": "10000", "prdy_ctrt": "5.0"}
            def get_hoga(self, c): return {"ask_bid_ratio": 5.0}
            def get_psbl_order_cash(self, *a): return 0
        b.api = Api()
        b.db = type("D", (), {"log_candidate": lambda self, *a, **k: None,
                              "get_today_realized": lambda self: 0})()
        b._do_buy = lambda code, name, price, tier, **k: bought.append(code)
        b._market_breadth = lambda: None          # 국면 모름 → 하락장 쉬기 안 걸림(10-19)
        return b

    def test_no_buy_after_cutoff(self):
        old = daybot.now_hhmm, daybot.get_all_positions
        daybot.get_all_positions = None
        try:
            bought = []
            daybot.now_hhmm = lambda: "0930"
            self._scan_bot(bought)._run_candidate_scan_and_maybe_buy()
            self.assertEqual(bought, ["111110"])
            bought.clear()
            daybot.now_hhmm = lambda: "0940"
            self._scan_bot(bought)._run_candidate_scan_and_maybe_buy()
            self.assertEqual(bought, [])
            self.assertEqual(daybot.NEW_BUY_END_TIME, "0940")
        finally:
            daybot.now_hhmm, daybot.get_all_positions = old

    def test_down_market_skips_buy_but_logs(self):
        # 10-19 대장 결정 — 전날 국면 35% 미만이면 신규매수 쉬기, 후보는 '하락장쉬기'로 기록
        old = daybot.now_hhmm, daybot.get_all_positions
        daybot.get_all_positions = None
        try:
            daybot.now_hhmm = lambda: "0930"
            bought, logged = [], []
            b = self._scan_bot(bought)
            b._market_breadth = lambda: 24.4
            b._regime = ("2026-10-19", 24.4)
            b.db = type("D", (), {"log_candidate": lambda self, *a, **k: logged.append(k.get("skip_reason")),
                                  "get_today_realized": lambda self: 0})()
            b._run_candidate_scan_and_maybe_buy()
            self.assertEqual(bought, [])
            self.assertIn("하락장쉬기", logged)
            b._market_breadth = lambda: 40.0
            b._run_candidate_scan_and_maybe_buy()
            self.assertEqual(bought, ["111110"])
        finally:
            daybot.now_hhmm, daybot.get_all_positions = old


class KiwoomOnly(unittest.TestCase):
    """★ 10-22 파이썬판 모드에서도 파이썬판 없는 090930타점만 키움에 2분마다."""
    def test_python_mode_asks_kiwoom_for_0909_only(self):
        b = object.__new__(daybot.DayBot)
        b.code_name_map, b._positions_lock, b._recent_tags, b._last_kiwoom_only = {}, threading.Lock(), {}, 0.0
        asked = []

        class K:
            enabled = True
            async def get_condition_codes(self, use_keywords=None, code_name_map=None, code_multi_tag_map=None,
                                          cond_ok=None, max_retry=None, **kw):
                asked.append((list(use_keywords), max_retry))
                code_multi_tag_map["555550"] = [daybot.COND_0909]; code_name_map["555550"] = "타점C"
                return ["555550"]
            def reset_token(self): pass
        b.kiwoom = K()
        orig, daybot.SCAN_SOURCE = daybot.SCAN_SOURCE, "python"
        orig_log, daybot.log_kiwoom_hits = daybot.log_kiwoom_hits, lambda *a, **k: None
        orig_merge = daybot.DayBot._merge_python_hits
        daybot.DayBot._merge_python_hits = lambda self, codes, tm, nm, ok: codes
        try:
            codes, tags = b._scan_conditions()
            self.assertEqual(asked, [([daybot.COND_0909], 0)])
            self.assertIn("555550", codes)
            b._scan_conditions()                                    # 2분 안 됐으면 안 물어봄
            self.assertEqual(len(asked), 1)
        finally:
            daybot.SCAN_SOURCE, daybot.log_kiwoom_hits = orig, orig_log
            daybot.DayBot._merge_python_hits = orig_merge


class EarlyMin0909(unittest.TestCase):
    def test_lower_bound(self):
        self.assertEqual(daybot.early_min_change({daybot.COND_0909}), 1.0)
        self.assertEqual(daybot.early_min_change({daybot.COND_0909, "주도주검색식3"}), 1.0)
        self.assertEqual(daybot.early_min_change({"주도주검색식3"}), 3.0)


if __name__ == "__main__":
    unittest.main()
