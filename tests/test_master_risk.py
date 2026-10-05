"""통합 리스크 — 오늘 켠 긴급중단만 유효, daybot 손실 집계, 자정해제 후 레벨."""
import os
import sqlite3
import unittest

from _helpers import use_temp_cwd

_tmp = use_temp_cwd()
import master_db as M  # noqa: E402

DB = os.path.abspath("m.db")


class MasterRisk(unittest.TestCase):
    def setUp(self):
        for f in (DB, DB + "-wal", DB + "-shm"):
            if os.path.exists(f):
                os.remove(f)
        M.init_db(DB)

    def test_pause_only_valid_today(self):
        self.assertFalse(M.is_paused_all(DB))
        M.set_pause_all(True, DB)
        self.assertTrue(M.is_paused_all(DB))
        con = sqlite3.connect(DB); con.execute("UPDATE master_risk SET date='2026-01-01'"); con.commit(); con.close()
        self.assertFalse(M.is_paused_all(DB))

    def test_daybot_loss_counted_and_level_after_midnight_reset(self):
        M.set_pause_all(True, DB)
        con = sqlite3.connect(DB); con.execute("UPDATE master_risk SET date='2026-01-01'"); con.commit(); con.close()
        M.record_trade(bot_type="daybot", code="X", stock_name="X", buy_price=100,
                       sell_price=90, qty=1000, sell_reason="손절", db_path=DB)
        st = M.update_risk(DB)
        self.assertEqual(st["daybot_loss_krw"], 10000.0)
        self.assertEqual(st["total_loss_krw"], 10000.0)
        self.assertFalse(st["paused_all"])
        self.assertEqual(st["risk_level"], "normal")


if __name__ == "__main__":
    unittest.main()
