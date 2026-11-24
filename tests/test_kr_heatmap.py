"""우리 섹터맵(kr_heatmap) — 네이버 시세 해석·분야 합계(시총 가중)·분야 아닌 그룹 분리."""
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "intelligence"))
import kr_heatmap as H  # noqa: E402


class Heatmap(unittest.TestCase):
    def test_parse_quote_units(self):
        q = H.parse_quote({"stockName": "티엘비", "closePriceRaw": "40700", "fluctuationsRatioRaw": "-3.10",
                           "marketValueFullRaw": "964641282000", "accumulatedTradingValueRaw": "64849000000"})
        self.assertEqual(q["chg"], -3.10)
        self.assertEqual(q["value"], 64_849_000_000)        # 648억 — 원 단위 그대로
        self.assertEqual(q["cap"], 964_641_282_000)

    def test_summarize(self):
        groups = {"반도체_기판PCB": [("1", "A"), ("2", "B")], "new": [("1", "A")], "빈그룹": [("9", "Z")]}
        q = {"1": {"name": "A", "price": 100, "chg": 10.0, "cap": 300, "value": 5},
             "2": {"name": "B", "price": 100, "chg": -2.0, "cap": 100, "value": 5}}
        d = H.summarize(groups, q)
        self.assertEqual([s["group"] for s in d["sectors"]], ["반도체_기판PCB"])   # 시세 없는 그룹은 빠짐
        self.assertEqual([w["group"] for w in d["watch"]], ["new"])                 # 분야 아닌 그룹은 따로
        s = d["sectors"][0]
        self.assertAlmostEqual(s["chg_w"], 7.0)            # (10×300 + -2×100) / 400
        self.assertAlmostEqual(s["chg"], 4.0)
        self.assertEqual((s["up"], s["down"]), (1, 1))
        self.assertEqual(d["breadth"], {"n": 2, "up": 50, "flat": 0, "down": 50})

    def test_quotes_chunked(self):
        calls = []

        def get(url):
            codes = url.rsplit("/", 1)[1].split(",")
            calls.append(len(codes))
            return {"datas": [{"itemCode": c, "stockName": c, "closePriceRaw": "1", "fluctuationsRatioRaw": "0"} for c in codes]}
        out = H.quotes([f"{i:06d}" for i in range(250)], get=get, now=1)
        self.assertEqual(len(out), 250)
        self.assertEqual(calls, [100, 100, 50])


if __name__ == "__main__":
    unittest.main()
