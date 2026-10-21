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
        b = MultiApi(one, multi_ok=False)                          # 응답은 왔는데 시가·저가 0(장 전) → 보충 안 함
        s = CA.snapshot(b, codes, pause=0)
        self.assertEqual(b.singles, []); self.assertEqual(s, {})

    def test_before_open(self):
        import datetime as _dt
        class _D(_dt.datetime):
            @classmethod
            def now(cls, tz=None): return cls(2026, 10, 15, 7, 20)
        old = CA.datetime.datetime
        CA.datetime.datetime = _D
        try:
            self.assertIn("장 시작 전", CA.run(None, forced=True))
            self.assertEqual(CA.run(None), "")
        finally:
            CA.datetime.datetime = old

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
        groups = {"이차_소재": [("900001", "에이"), ("900002", "비"), ("900003", "씨")],
                  "기타": [("000000", "s0"), ("000001", "s1"), ("000002", "s2")],
                  "new": [("900001", "에이"), ("000000", "s0"), ("000001", "s1")]}
        res = CA.evaluate(univ, snap, groups, code_of)
        self.assertEqual(res["share"], 60.0)
        self.assertEqual([g["group"] for g in res["groups"]], ["이차_소재", "기타"])   # new 제외, 오후반등 순
        msg = CA.format_alert(res, nl=40)
        self.assertIn("투매일 신호", msg); self.assertIn("[이차\\_소재]", msg); self.assertIn("시가 정리", msg)
        self.assertIn("장 끝까지", msg); self.assertIn("09:45 전 50%", msg)
        self.assertEqual(CA.format_alert(res, nl=70), "")                       # 기준 미달 자동 → 조용히
        self.assertIn("기준 미달", CA.format_alert(res, nl=70, forced=True))
        st = os.path.abspath("st.json")
        CA.save_picks(res, st, today="2026-10-13")
        self.assertEqual(CA.morning_reminder(st, today="2026-10-13"), "")      # 같은 날은 안 보냄
        m = CA.morning_reminder(st, today="2026-10-14")
        self.assertIn("에이", m); self.assertIn("시가에 정리", m); self.assertNotIn("승부", m)
        self.assertEqual(CA.morning_reminder(st, today="2026-10-14"), "")      # 한 번만

    def test_group_rank_median(self):
        """한 종목만 튄 그룹(평균 1등)보다 고르게 오른 그룹이 위 — 중앙값 순위(대장 2번)."""
        def q(low, price, v): return {"open": 100, "high": 110, "low": low, "price": price, "value": v, "chg": 0.0}
        snap = {"100001": q(100, 130, 3), "100002": q(100, 101, 2), "100003": q(100, 101, 1),
                "200001": q(100, 104, 3), "200002": q(100, 104, 2), "200003": q(100, 104, 1)}
        groups = {"튐": [("100001", "a"), ("100002", "b"), ("100003", "c")],
                  "고름": [("200001", "d"), ("200002", "e"), ("200003", "f")]}
        res = CA.evaluate({"a": 1}, snap, groups, {"a": "100001"})
        self.assertEqual([g["group"] for g in res["groups"]], ["고름", "튐"])
        self.assertAlmostEqual(res["groups"][1]["aft"], 1.0)

    def test_snapshot(self):
        api = Api({"000001": {"stck_prpr": "100", "stck_lwpr": "95", "stck_oprc": "98", "stck_hgpr": "101",
                              "acml_tr_pbmn": "5000", "prdy_ctrt": "-1.5"},
                   "000002": {"stck_prpr": "0"}})
        s = CA.snapshot(api, ["000001", "000002", "000003", "000001"], pause=0)
        self.assertEqual(list(s), ["000001"]); self.assertEqual(s["000001"]["chg"], -1.5)


class Retest(unittest.TestCase):
    """★ 10-21 바닥 재시험일 — 투매일 뒤 +3% 반등했다가 투매일 종가 ±2%로 내려온 하락일, 신저가 20% 미만."""
    def test_check(self):
        shares = [("2026-10-13", 47.7), ("2026-10-14", 12.9), ("2026-10-15", 55.3), ("2026-10-16", 11.1), ("2026-10-19", 14.5)]
        ks = {"2026-10-14": 6008.8, "2026-10-15": 5554.2, "2026-10-16": 5668.1, "2026-10-19": 5768.0, "2026-10-20": 5552.7}
        r = CA.retest_check(shares, ks, 16.3, "2026-10-20")
        self.assertTrue(r["ok"]); self.assertEqual(r["cap"], "2026-10-15"); self.assertAlmostEqual(r["bounce"], 3.85, 1)
        self.assertIn("신저가 22%", CA.retest_check(shares, ks, 22.0, "2026-10-20")["why"][0])
        ks2 = dict(ks, **{"2026-10-19": 5600.0})                          # 반등이 +3% 안 됨
        self.assertIn("반등 없었음", CA.retest_check(shares, ks2, 10.0, "2026-10-20")["why"])
        self.assertIsNone(CA.retest_check([("2026-10-19", 14.5)], ks, 10.0, "2026-10-20"))   # 투매일 없음
        self.assertEqual(CA.format_retest(None, []), "")
        self.assertIn("재시험일", CA.format_retest(r, [("A", "000001", 21.2, 0.5)]))

    def test_daily_shares_and_picks(self):
        db = os.path.abspath("r.db")
        c = sqlite3.connect(db)
        c.execute("CREATE TABLE kr_stock_daily_data (date TEXT, stock_name TEXT, low_price REAL)")
        for i in range(22):
            d = f"2026-09-{i + 1:02d}"
            c.execute("INSERT INTO kr_stock_daily_data VALUES (?,?,?)", (d, "깨짐", 50 if i == 21 else 100 + i))
            c.execute("INSERT INTO kr_stock_daily_data VALUES (?,?,?)", (d, "버팀", 100 + i))
        c.commit(); c.close()
        sh = CA.daily_shares(db, today="2026-09-23", days=2)
        self.assertEqual([round(v) for _, v in sh], [0, 50])                # 22일: 깨짐만 20일 신저가
        snap = {"000001": {"price": 130, "chg": 1, "value": 5e9}, "000002": {"price": 101, "chg": 0, "value": 5e9},
                "000003": {"price": 200, "chg": 0, "value": 1e8}}
        picks = CA.retest_picks("2026-09-22", [("000001", "버팀"), ("000002", "깨짐"), ("000003", "버팀")], snap, db)
        self.assertEqual([p[0] for p in picks], ["깨짐", "버팀"])           # 깨짐 저가 50 → +102%, 버팀 121 → +7.4%
        self.assertAlmostEqual(picks[1][2], (130 / 121 - 1) * 100)


if __name__ == "__main__":
    unittest.main()
