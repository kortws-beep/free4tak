"""세 봇 합산 손실 자동중단 — 한도 보정, 경고/위험 단계, 자동 전봇중단, 수동 재개 존중, 알림 1회."""
import datetime
import os
import unittest

from _helpers import stub, use_temp_cwd

SENT = []
stub("notifier", Notifier=lambda **k: type("N", (), {"send": lambda self, m, critical=False: SENT.append(m)})())

_tmp = use_temp_cwd()
import master_db as M      # noqa: E402
import unified_risk as U   # noqa: E402

DB = os.path.abspath("m.db")


def loss(bot, krw):
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    M.record_trade(bot, "000001", 10000, 10000 + krw / 10, 10, "t", sell_time=now, db_path=DB)


class Risk(unittest.TestCase):
    def setUp(self):
        if os.path.exists(DB):
            os.remove(DB)
        M.init_db(DB)
        SENT.clear()
        U._last_risk_level = "normal"

    def test_flow(self):
        self.assertEqual(U.ensure_limit(DB), U.DAILY_LOSS_LIMIT)                 # 기본 5만 → 26.6만
        M.set_daily_loss_limit(300_000, DB)
        self.assertEqual(U.ensure_limit(DB), 300_000)                            # 직접 정한 값은 유지
        M.set_daily_loss_limit(266_000, DB)
        loss("daybot", -100_000)
        self.assertEqual(U.check_risk(DB)["risk_level"], "normal"); self.assertEqual(SENT, [])
        loss("sbot", -100_000)
        self.assertEqual(U.check_risk(DB)["risk_level"], "warning")              # 20만 ≥ 18.6만
        self.assertEqual(len(SENT), 1); self.assertIn("리스크 경고", SENT[0])
        U.check_risk(DB); self.assertEqual(len(SENT), 1)                         # 같은 단계 반복 알림 없음
        self.assertFalse(M.is_paused_all(DB))
        loss("cbot", -80_000)
        U.check_risk(DB)
        self.assertTrue(M.is_paused_all(DB))                                     # 28만 ≥ 26.6만 → 전봇 중단
        self.assertIn("전봇 자동 중단", SENT[-1])
        M.set_pause_all(False, DB)                                               # 대장이 손으로 재개
        self.assertTrue(M.resumed_today(DB))
        U._last_risk_level = "normal"
        U.check_risk(DB)
        self.assertFalse(M.is_paused_all(DB))                                    # 같은 날엔 다시 안 멈춤

    def test_profit_not_counted_as_loss(self):
        U.ensure_limit(DB)
        loss("daybot", 300_000)
        self.assertEqual(U.check_risk(DB)["risk_level"], "normal")


if __name__ == "__main__":
    unittest.main()
