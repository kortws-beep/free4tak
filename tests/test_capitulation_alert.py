"""투매일 반등 섹터 알림 — 유니버스·평가·문구·아침 알림."""
import os
import sqlite3
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "intelligence"))
from _helpers import use_temp_cwd  # noqa: E402
_tmp = use_temp_cwd()
import capitulation_alert as CA  # noqa: E402


class Api:
    def __init__(self, d): self.d = d
    def get_market_data(self, c): return self.d.get(c)


class MultiApi(Api):
    def __init__(self, d, multi_ok=True):
        super().__init__(d); self.multi_ok, self.multi_calls, self.singles = multi_ok, [], []
    def get_multi_price(self, codes, pause=0.0):
        self.multi_calls.append(len(codes))
        q = {"open": 1, "high": 1, "low": 1, "price": 1, "value": 0, "chg": 0} if self.multi_ok else \
            {"high": 1, "price": 1, "value": 0, "chg": 0, "low": 0, "open": 0}
        return {c: dict(q) for c in codes if c != "000005"}
    def get_market_data(self, c):
        self.singles.append(c); return super().get_market_data(c)


class Alert(unittest.TestCase):
    def test_snapshot_multi(self):
        codes = [f"{k:06d}" for k in range(65)]
        one = {"000005": {"stck_prpr": "10", "stck_lwpr": "9", "stck_oprc": "9.5"}}
        a = MultiApi(one)
        s = CA.snapshot(a, codes, pause=0)
        self.assertEqual(a.multi_calls, [65]); self.assertEqual(len(s), 65)
        self.assertEqual(a.singles, ["000005"])                    # 복수시세에서 빠진 것만 한 종목씩
        b = MultiApi(one, multi_ok=False)                          # 저가·시가 못 읽음 → 한 종목씩
        s = CA.snapshot(b, codes, pause=0)
        self.assertEqual(len(b.singles), 65); self.assertEqual(list(s), ["000005"])

    def test_universe(self):
        db = os.path.abspath("t.db")
        c = sqlite3.connect(db)
        c.execute("CREATE TABLE kr_stock_daily_data (date TEXT, stock_name TEXT, low_price REAL, trade_value REAL)")
        for i in range(25):
            d = f"2026-09-{i + 1:02d}"
            c.execute("INSERT INTO kr_stock_daily_data VALUES (?,?,?,?)", (d, "큰거", 100 + i, 900))
            c.execute("INSERT INTO kr_stock_daily_data VALUES (?,?,?,?)", (d, "작은거", 50, 10))
            c.execute("INSERT INTO kr_stock_daily_data VALUES (?,?,?,?)", (d, "KODEX 200", 10, 9999))
        c.execute("INSERT INTO kr_stock_daily_data VALUES (?,?,?,?)", ("2026-09-26", "큰거", 1, 99999))  # 오늘 — 제외
        c.commit(); c.close()
        u = CA.load_universe(db, top=1, today="2026-09-26")
        self.assertEqual(u, {"큰거": 105})                       # 최근 20일(6~25일) 최저, ETF 제외
        self.assertEqual(CA.load_universe(db, top=0, today="2026-09-26"), {"큰거": 105, "작은거": 50})  # 0 = 전 종목

    def test_evaluate_and_format(self):
        univ = {f"s{k}": 100 for k in range(10)}
        code_of = {f"s{k}": f"{k:06d}" for k in range(10)}
        snap = {f"{k:06d}": {"open": 110, "high": 112, "low": 99 if k < 6 else 105, "price": 104, "value": k,
                             "chg": -1.0} for k in range(10)}
        snap["900001"] = {"open": 100, "high": 110, "low": 90, "price": 99, "value": 5, "chg": 2.0}
        snap["900002"] = {"open": 100, "high": 110, "low": 95, "price": 100, "value": 4, "chg": 1.0}
        snap["900003"] = {"open": 100, "high": 110, "low": 95, "price": 101, "value": 3, "chg": 1.0}
        groups = {"이차": [("900001", "에이"), ("900002", "비"), ("900003", "씨")],
                  "기타": [("000000", "s0"), ("000001", "s1"), ("000002", "s2")],
                  "new": [("900001", "에이"), ("000000", "s0"), ("000001", "s1")]}
        res = CA.evaluate(univ, snap, groups, code_of)
        self.assertEqual(res["share"], 60.0)
        self.assertEqual([g["group"] for g in res["groups"]], ["이차", "기타"])   # new 제외, 오후반등 순
        msg = CA.format_alert(res, nl=40)
        self.assertIn("투매일 신호", msg); self.assertIn("[이차]", msg); self.assertIn("시가 정리", msg)
        self.assertEqual(CA.format_alert(res, nl=70), "")                       # 기준 미달 자동 → 조용히
        self.assertIn("기준 미달", CA.format_alert(res, nl=70, forced=True))
        st = os.path.abspath("st.json")
        CA.save_picks(res, st, today="2026-10-13")
        self.assertEqual(CA.morning_reminder(st, today="2026-10-13"), "")      # 같은 날은 안 보냄
        m = CA.morning_reminder(st, today="2026-10-14")
        self.assertIn("에이", m); self.assertIn("시가에 정리", m)
        self.assertEqual(CA.morning_reminder(st, today="2026-10-14"), "")      # 한 번만

    def test_snapshot(self):
        api = Api({"000001": {"stck_prpr": "100", "stck_lwpr": "95", "stck_oprc": "98", "stck_hgpr": "101",
                              "acml_tr_pbmn": "5000", "prdy_ctrt": "-1.5"},
                   "000002": {"stck_prpr": "0"}})
        s = CA.snapshot(api, ["000001", "000002", "000003", "000001"], pause=0)
        self.assertEqual(list(s), ["000001"]); self.assertEqual(s["000001"]["chg"], -1.5)


if __name__ == "__main__":
    unittest.main()
