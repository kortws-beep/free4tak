"""10-30 데이봇 — 하락장 슬롯 주도 섹터 관문(sector_gate) · 음봉 엔진 계산(dip_leader)."""
import datetime
import sqlite3
import unittest

from _helpers import use_temp_cwd

_tmp = use_temp_cwd()
import sector_gate as G  # noqa: E402
import dip_leader as D   # noqa: E402


class Gate(unittest.TestCase):
    def test_pick_time(self):
        times = ["0830", "0845", "0855", "0915", "0930", "1000"]
        self.assertEqual(G.pick_time(times, "0905"), "0855")      # 09:15 전 = 프리장 마지막
        self.assertEqual(G.pick_time(times, "0920"), "0915")
        self.assertEqual(G.pick_time(times, "0959"), "0930")      # 지금까지의 본장 마지막(미래 기록 안 씀)
        self.assertEqual(G.pick_time(["0915"], "0910"), None)      # 프리장 기록도 없으면 None
        self.assertEqual(G.pick_time(["0855"], "1100"), "0855")    # 본장 기록 없으면 프리장

    def test_allows(self):
        c = sqlite3.connect("log.db")
        c.execute("CREATE TABLE theme_open_obs (date TEXT, time TEXT, rank INTEGER, theme TEXT, avg_chg REAL, up_ratio REAL, "
                  "n INTEGER, leader TEXT, leader_chg REAL)")
        d = "2026-11-02"
        c.executemany("INSERT INTO theme_open_obs (date, time, rank, theme) VALUES (?,?,?,?)",
                      [(d, "0855", 1, "소캠"), (d, "0855", 6, "드론"), (d, "0930", 1, "로봇")])
        c.commit(); c.close()
        g = G.SectorGate("log.db", themes_loader=lambda: {"소캠": [("A", "가")], "드론": [("B", "나")], "로봇": [("C", "다")]})
        at = lambda hm: datetime.datetime(2026, 11, 2, int(hm[:2]), int(hm[2:]))  # noqa: E731
        self.assertTrue(g.allows("A", at("0905"))[0])
        self.assertFalse(g.allows("B", at("0905"))[0])            # 6위는 상위 5 밖
        ok, why = g.allows("A", at("0935"))
        self.assertFalse(ok); self.assertIn("로봇", why)          # 09:30 본장 순위로 바뀜
        self.assertTrue(g.allows("Z", datetime.datetime(2026, 11, 3, 9, 5))[0])   # 기록 없는 날은 막지 않음


class Dip(unittest.TestCase):
    def test_index_down(self):
        self.assertTrue(D.index_down({"kospi": -5.2, "kosdaq": -1.0}))
        self.assertTrue(D.index_down({"kospi": -0.5, "kosdaq": -2.0}))
        self.assertFalse(D.index_down({"kospi": -1.9, "kosdaq": -1.0}))
        self.assertFalse(D.index_down({"kospi": 0.0, "kosdaq": 0.0}))     # 조회 실패(0)는 하락 아님

    def test_situation_1031(self):
        # ★ 10-31 대장 — 장중 -2%↓ 찍었나(반등 무관) · 전날까지 연속하락 ≥1일 · 20일 고점 -15% 이내
        self.assertTrue(D.index_hit({"kospi": (-1.0, 1.2), "kosdaq": (-4.5, 0.1)}))
        self.assertTrue(D.index_hit({"kospi": (-0.9, 1.2)}))                # 지금 -0.9%여도 저가는 -2.1%
        self.assertFalse(D.index_hit({"kospi": (-1.0, 0.5)}))
        self.assertEqual(D.trend_tags([100] * 18 + [105, 104, 103])["streak"], 2)
        self.assertAlmostEqual(D.trend_tags([100] * 18 + [105, 104, 103])["dd20"], -1.905, places=2)
        rows = [{"date": f"2026-10-{d:02d}", "close": c} for d, c in zip(range(1, 23), [100] * 20 + [99, 98])]
        api = type("A", (), {"get_daily_ohlc": lambda self, code, days=30: rows + [{"date": "2026-10-23", "close": 90}]})()
        ok, why = D.situation(api, {"kospi": (-3.0, 0.5), "kosdaq": (-1.0, 0.1)}, "2026-10-23")
        self.assertTrue(ok); self.assertIn("연속하락 2일", why)              # 오늘(10-23) 봉은 빼고
        rows[-1]["close"] = 101
        self.assertFalse(D.situation(api, {"kospi": (-3.0, 0.5)}, "2026-10-23")[0])   # 첫 하락날
        rows[-1]["close"], rows[-2]["close"] = 80, 84
        ok, why = D.situation(api, {"kospi": (-3.0, 0.5)}, "2026-10-23")
        self.assertFalse(ok); self.assertIn("스윙 몫", why)                 # 20일 고점 -20%

    def test_index_ready(self):
        # ★ 10-30 대장 — 빠진 지수가 당일 저가 대비 +0.5% 반등해야(종가배팅 아님)
        self.assertTrue(D.index_ready({"kospi": (-5.4, 0.6), "kosdaq": (-4.5, 0.1)}))
        self.assertFalse(D.index_ready({"kospi": (-5.4, 0.3), "kosdaq": (-4.5, 0.2)}))
        self.assertFalse(D.index_ready({"kospi": (-1.5, 1.2)}))          # -2%까진 안 빠짐
        st = D.index_status(get=lambda u: (
            {"totalInfos": [{"code": "lastClosePrice", "value": "5,937.66"}, {"code": "lowPrice", "value": "5,599.42"}]}
            if u.endswith("integration") else {"closePrice": "5,627.42"}))
        self.assertAlmostEqual(st["kospi"][0], -5.22, places=2); self.assertAlmostEqual(st["kospi"][1], 0.50, places=2)

    def test_pick(self):
        uni = {"A": {"name": "대덕전자", "value_eok": 1566, "r60": 42, "hi5": 140100},
               "B": {"name": "티엘비", "value_eok": 273, "r60": 53, "hi5": 42700},
               "C": {"name": "덜빠짐", "value_eok": 900, "r60": 40, "hi5": 10000}}
        q = {"A": {"price": 116600, "chg": -15.6, "high": 130000, "low": 115000},     # 저가 대비 +1.4%
             "B": {"price": 36850, "chg": -9.5, "low": 35000},                         # 저가 대비 +5.3%
             "C": {"price": 9500, "chg": -6.0, "low": 9000}}                           # C는 5일 고점 대비 -5%뿐
        got = D.pick(uni, q)
        self.assertEqual([g[0] for g in got], ["B", "A"])                  # ★ 저가 대비 가장 많이 오른 순(대장)
        self.assertAlmostEqual(got[0][5], 5.29, places=1)
        self.assertEqual(D.pick(uni, q, exclude={"B"})[0][0], "A")

    def test_watch_codes(self):
        # ★ 10-30 대장 — NEW·SUB 관심그룹 종목만(대소문자·이름 변형 허용), 다른 그룹은 제외
        api = type("A", (), {"get_watchlist_groups": lambda self, h: {"011": "new", "030": "SUB", "001": "반도체_HBM"},
                             "get_watchlist_stocks": lambda self, gc, h: {"011": [("A", "대덕전자")], "030": [("B", "현대건설")],
                                                                          "001": [("C", "기타")]}[gc]})()
        self.assertEqual(D.watch_codes(api, "id"), {"A", "B"})
        self.assertEqual(D.watch_codes(api, ""), set())

    def test_universe(self):
        c = sqlite3.connect("daily.db")
        c.execute("CREATE TABLE kr_stock_daily_data (stock_name TEXT, date TEXT, close_price REAL, high_price REAL, trade_value REAL)")
        c.execute("CREATE TABLE kr_stock_master (code TEXT, name TEXT)")
        c.executemany("INSERT INTO kr_stock_master VALUES (?,?)", [("A", "주도"), ("B", "약함")])
        base = datetime.date(2026, 7, 1)
        rows = []
        for i in range(62):
            d = (base + datetime.timedelta(days=i)).isoformat()
            rows.append(("주도", d, 100 + i, 101 + i, 2e10))              # 60일 +60%↑, 대금 200억
            rows.append(("약함", d, 100, 101, 2e10))
        c.executemany("INSERT INTO kr_stock_daily_data VALUES (?,?,?,?,?)", rows)
        c.commit(); c.close()
        uni = D.universe((base + datetime.timedelta(days=62)).isoformat(), db="daily.db")
        self.assertEqual(list(uni), ["A"])
        self.assertEqual(uni["A"]["hi5"], 101 + 61)


if __name__ == "__main__":
    unittest.main()
