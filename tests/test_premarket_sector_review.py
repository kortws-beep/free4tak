"""프리장 섹터 대조 — ±3% 유지 판정, +2.5/-3.5 먼저 닿기, 시각 기준 가격."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backtest"))
import premarket_sector_review as P  # noqa: E402


def bar(t, price, high=None, low=None):
    return {"time": t, "price": price, "high": high or price, "low": low or price}


class Review(unittest.TestCase):
    def test_hold_state(self):
        self.assertEqual(P.hold_state(10000, 10200)[1], "유지")
        self.assertEqual(P.hold_state(10000, 9600)[1], "내려앉음")     # 프리장에서만 뜨고 본장에 내려앉음
        self.assertEqual(P.hold_state(10000, 10400)[1], "더 감")
        self.assertEqual(P.hold_state(0, 10000), (None, "모름"))

    def test_first_touch_and_price_at(self):
        bars = [bar("090100", 100), bar("090500", 101), bar("091000", 102)]
        self.assertEqual(P.price_at(bars, "090500"), 101)
        self.assertEqual(P.first_touch(100, [bar("091100", 101, high=102.6)]), "win")
        self.assertEqual(P.first_touch(100, [bar("091100", 97, low=96.4), bar("091200", 103, high=103)]), "loss")
        self.assertEqual(P.first_touch(100, [bar("091100", 101)]), "none")


if __name__ == "__main__":
    unittest.main()
