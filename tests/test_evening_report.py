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


if __name__ == "__main__":
    unittest.main()
