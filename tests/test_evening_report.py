"""저녁 봇 성적표 — 봇별 매도·승패·손절·실현, 이달 누적, 한도 경고, 눌림목."""
import datetime
import json
import os
import sqlite3
import unittest

from _helpers import stub, use_temp_cwd

_tmp = use_temp_cwd()
stub("market_regime", latest=lambda: ("2026-10-08", 42.0), is_strong=lambda b: b >= 50)
import master_db as M       # noqa: E402
import evening_report as E  # noqa: E402

DB = os.path.abspath("m.db")
D = datetime.date.today().isoformat()


def trade(bot, name, buy, sell, qty, reason, day=D):
    M.record_trade(bot, "000001", buy, sell, qty, reason, stock_name=name, sell_time=f"{day} 10:00:00", db_path=DB)


class Report(unittest.TestCase):
    def setUp(self):
        if os.path.exists(DB):
            os.remove(DB)
        M.init_db(DB)
        E._BASE = os.path.abspath(".")
        trade("daybot", "가", 10000, 9650, 100, "손절(-3.5%)")
        trade("daybot", "나", 10000, 10300, 100, "트레일링청산")
        trade("sbot", "다", 10000, 7000, 200, "손절")                     # -60만 → 한도 초과
        trade("cbot", "KRW-ETH", 100, 105, 1000, "익절")
        trade("daybot", "옛날", 10000, 11000, 10, "익절", day=D[:8] + "01" if D[8:] != "01" else D)
        with open("cbot_state.json", "w") as f:
            json.dump({"paused": True, "pause_reason": "loss_limit",
                       "dip": {"KRW-XRP": {"held": True}, "KRW-SOL": {"held": True}, "KRW-BTC": {}}}, f)

    def test_day_and_month(self):
        day = E.bot_day(D, DB)
        self.assertEqual((day["daybot"]["n"], day["daybot"]["win"], day["daybot"]["stops"]), (2, 1, 1))
        self.assertAlmostEqual(day["daybot"]["krw"], -35_000 + 30_000)
        self.assertEqual(day["sbot"]["worst"][0], "다")
        self.assertEqual(E.bot_month(D, DB)["daybot"][1], 3 if D[8:] != "01" else 3)

    def test_build(self):
        out = E.build(D, DB)
        self.assertIn("데이봇", out); self.assertIn("1승1패", out)
        self.assertIn("sbot 일손실 한도 도달", out)
        self.assertIn("cbot 일손실 한도로 멈춤", out)
        self.assertIn("눌림목 보유 2", out); self.assertIn("XRP", out)
        self.assertIn("국면 42%", out); self.assertIn("내일 데이봇·sbot 매수금 절반", out)

    def test_cbot_resumed_after_loss_limit(self):
        with open("cbot_state.json", "w") as f:
            json.dump({"paused": False, "loss_base": -152362, "loss_base_date": D}, f)
        try:
            out = E.build(D, DB)
        finally:
            with open("cbot_state.json", "w") as f:
                json.dump({"paused": True, "pause_reason": "loss_limit",
                           "dip": {"KRW-XRP": {"held": True}, "KRW-SOL": {"held": True}, "KRW-BTC": {}}}, f)
        self.assertIn("cbot 일손실 한도로 멈췄다가 시장점검 뒤 재개", out)


class OpenPositions(unittest.TestCase):
    def test_stale_rows_excluded(self):
        # ★ 2026-10-13 — sbot에서 06월 매도분 4건이 master_positions에서 안 지워진 채
        #   몇 달째 남아 보유 6인데 10으로 집계되던 버그. 봇은 매루프 갱신하므로
        #   STALE_DAYS보다 오래 안 갱신된 행은 유령 행으로 보고 제외해야 함.
        db = os.path.abspath("p.db")
        if os.path.exists(db):
            os.remove(db)
        M.init_db(db)
        M.upsert_position("sbot", "000001", db_path=db)              # 방금 갱신(최신)
        conn = sqlite3.connect(db)
        conn.execute("INSERT INTO master_positions (bot_type, code, updated_at) VALUES (?,?,?)",
                     ("sbot", "999999", "2026-01-01 00:00:00"))      # 묵은 유령 행
        conn.commit(); conn.close()
        self.assertEqual(E.open_positions(db), {"sbot": 1})


if __name__ == "__main__":
    unittest.main()
