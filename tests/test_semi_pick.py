"""반도체 소부장 선별(semi_pick) — 낙폭 구간·영업이익 파싱·증가 판정·보유 판정."""
import json
import os
import sys
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "intelligence"))
import semi_pick as S  # noqa: E402


def fin(keys, vals):
    return {"financeInfo": {"trTitleList": [{"key": k, "isConsensus": "Y" if c else "N"} for k, c in keys],
                            "rowList": [{"title": "영업이익", "columns": {k: {"value": v} for k, v in vals.items()}}]}}


class SemiPick(unittest.TestCase):
    def test_bucket(self):
        self.assertEqual([S.bucket(x) for x in (-60, -50, -40, -20, -5)], ["💎", "💎", "👀", "🌱", "·"])

    def test_parse_fin(self):
        a = fin([("202412", 0), ("202512", 0), ("202612", 1)], {"202412": "106", "202512": "738", "202612": "1,702"})
        q = fin([("202506", 0), ("202606", 0), ("202609", 1)], {"202506": "365", "202606": "184", "202609": "589"})
        f = S.parse_fin(a, q)
        self.assertEqual((f["op_last"], f["op_this"], f["op_diff"], f["y_this"]), (738, 1702, 964, "2026"))
        self.assertAlmostEqual(f["op_yoy"], 130.6, places=1)
        self.assertEqual(f["q_label"], "26.06")
        self.assertAlmostEqual(f["q_yoy"], -49.6, places=1)

    def test_grow_ok_turnaround(self):
        self.assertTrue(S.grow_ok({"op_this": 50, "op_last": -100, "op_diff": 150}))
        self.assertFalse(S.grow_ok({"op_this": -10, "op_last": -100, "op_diff": 90}))   # 적자 축소는 아직 아님
        self.assertFalse(S.grow_ok({"op_this": 80, "op_last": 100, "op_diff": -20}))

    def test_candidates_and_holdings(self):
        groups = {"반도체_전공정장비": [("1", "가"), ("005930", "삼성전자")], "화장품": [("9", "나")]}
        quotes = {"1": {"price": 40.0, "chg": 1.0}, "005930": {"price": 100.0, "chg": 0}}
        rows = S.candidates(groups=groups, quotes=quotes, fin={"1": {"op_this": 10, "op_last": 5, "op_diff": 5}}, hi={"가": 100.0})
        self.assertEqual([r["name"] for r in rows], ["가"])                 # S7·다른 분야 제외
        self.assertAlmostEqual(rows[0]["dd"], -60.0)
        fd, p = tempfile.mkstemp()
        os.close(fd)
        with open(p, "w") as f:
            json.dump({"1": {"entry_price": 100, "account": "kiwoom"}}, f)
        h = S.holdings_view(rows, watch_path=p)
        os.remove(p)
        self.assertEqual(h[0][:2], ("가", "K"))
        self.assertEqual(h[0][5], "💎")
        self.assertIn("추가매수 검토", S.format_rows(rows, h))


if __name__ == "__main__":
    unittest.main()
