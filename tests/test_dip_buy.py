"""고정 4종목 눌림목 백테스트 — 진입(N일 고가 -X%), 익절/손절/기한, 같은 날 둘 다면 손절."""
import os
import sys
import unittest

from _helpers import use_temp_cwd

_tmp = use_temp_cwd()
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backtest"))
import dip_buy_backtest as D  # noqa: E402


def day(i, o, h, l, c):
    return (f"2026-01-{i + 1:02d}", o, h, l, c)


class DipBuy(unittest.TestCase):
    def test_entry_and_take_profit(self):
        rows = [day(i, 100, 100, 100, 100) for i in range(7)]
        rows += [day(7, 95, 95, 89, 90),     # 7일 고가 100 → 90 이하 → 90에 진입
                 day(8, 90, 95, 89, 94),
                 day(9, 94, 98, 93, 97)]     # +8% = 97.2 → 98 닿음 → 익절
        t = D.simulate(rows, 7, 0.10, 0.08, 0.0, 10)
        self.assertEqual(len(t), 1); self.assertEqual(t[0]["reason"], "익절")
        self.assertAlmostEqual(t[0]["ret"], 1.08 * (1 - D.SLIP) - 1 - 2 * D.FEE, places=6)

    def test_stop_wins_same_day_and_time_exit(self):
        rows = [day(i, 100, 100, 100, 100) for i in range(7)]
        rows += [day(7, 95, 95, 89, 90), day(8, 90, 99, 80, 85)]   # 같은 날 +8%·-8% 둘 다 → 손절
        t = D.simulate(rows, 7, 0.10, 0.08, 0.08, 10)
        self.assertEqual(t[0]["reason"], "손절")
        rows2 = [day(i, 100, 100, 100, 100) for i in range(7)] + [day(7, 95, 95, 89, 90)] + \
                [day(8 + k, 91, 92, 90, 91) for k in range(3)]
        t2 = D.simulate(rows2, 7, 0.10, 0.08, 0.0, 2)
        self.assertEqual(t2[0]["reason"], "기한")
        s = D.summarize(t + t2)
        self.assertEqual([x["reason"] for x in t2], ["기한", "보유중"])   # 기한 청산 뒤 다음날 다시 조건 충족 → 재진입
        self.assertEqual(s["n"], 3); self.assertLess(s["mdd"], 0); self.assertEqual(s["open"], 1)


if __name__ == "__main__":
    unittest.main()
