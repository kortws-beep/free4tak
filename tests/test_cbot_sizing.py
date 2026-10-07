"""cbot 백테스트 — 위험 기준 매수금액(손절 1회 손실 고정)과 하루 손익 집계."""
import os
import sys
import unittest

from _helpers import stub, use_temp_cwd

_tmp = use_temp_cwd()
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backtest"))
stub("metrics", calc_metrics=None, format_report=None, format_comparison=None)
stub("pandas", DataFrame=object); stub("numpy")   # 계산 경로만 테스트 — 데이터프레임 불필요
import cbot_backtest_engine as E  # noqa: E402
import run_cbot_backtest as R     # noqa: E402


def eng(risk):
    e = object.__new__(E.CBotBacktestEngine)
    e.config = E.CBotBacktestConfig(risk_per_trade=risk)
    e.cash = 3_000_000
    return e


class Sizing(unittest.TestCase):
    def test_buy_amount(self):
        self.assertEqual(eng(0)._buy_amount(0.05), 1_000_000)               # 현행: 고정
        self.assertAlmostEqual(eng(50_000)._buy_amount(0.025), 1_000_000)   # 손절 5% → 100만(상한)
        self.assertAlmostEqual(eng(50_000)._buy_amount(0.04), 625_000)      # 손절 8% → 62.5만
        self.assertAlmostEqual(eng(50_000)._buy_amount(0.05), 500_000)      # 손절 10% → 50만
        self.assertAlmostEqual(eng(50_000)._buy_amount(0), 50_000 / 0.07)   # ATR 없음 → 폴백 7%

    def test_day_stats(self):
        t = [{"sell_date": "2026-10-07 11:00:00", "profit_krw": -80_000},
             {"sell_date": "2026-10-07 11:01:00", "profit_krw": -90_000},
             {"sell_date": "2026-10-08 10:00:00", "profit_krw": 30_000}]
        self.assertEqual(R.day_stats(t), {"worst_day": -170_000, "limit_days": 1})
        self.assertEqual([s["name"] for s in R.get_sizing_scenarios(E.CBotBacktestConfig())][:2],
                         ["기본(100만 고정)", "위험기준(3만/회)"])
        sc = R.get_positions_scenarios(E.CBotBacktestConfig(), 50_000, [4, 6])
        self.assertEqual([(x["name"], x["config"]["max_positions"], x["config"]["risk_per_trade"]) for x in sc],
                         [("기본(100만 고정·3종목)", 3, 0), ("위험5만·4종목", 4, 50_000), ("위험5만·6종목", 6, 50_000)])


if __name__ == "__main__":
    unittest.main()
