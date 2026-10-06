"""단타000(파이썬판) — 체결건수 추정, 5분봉 CCI, 1·2·3단계 판정, 겹침 태그."""
import datetime
import os
import sqlite3
import unittest

from _helpers import use_temp_cwd

_tmp = use_temp_cwd()
import danta_scan as D  # noqa: E402

NOW = datetime.datetime(2026, 10, 6, 10, 30, 0)


def q(price, chg, value, volume, ask=900, bid=1000):
    return {"name": "", "price": price, "chg": chg, "high": price, "prev_close": price,
            "value": value, "volume": volume, "ask_rsqn": ask, "bid_rsqn": bid}


def rising_bars(n=520, date="20261006"):
    out, t = [], datetime.datetime(2026, 10, 6, 9, 0)
    for i in range(n):
        p = 1000 + (i * 2 if i > n - 15 else (i % 7))   # 횡보하다 마지막에 급등
        out.append({"date": date, "time": t.strftime("%H%M%S"), "price": p, "high": p + 1,
                    "low": p - 1, "volume": 10, "acml_value": 0})
        t += datetime.timedelta(minutes=1)
    return out


class API:
    def __init__(self):
        self.quotes = {
            "111111": q(10000, 8.0, 6e9, 1_000_000),           # 시총 2000억, 회전율 5% → 끝까지 검사
            "222222": q(10000, 5.0, 6e9, 600_000, ask=1500),   # 잔량비 150% → 1단계 탈락
            "333333": q(10000, 5.0, 1e9, 100_000),             # 거래대금 10억 → 사전필터 탈락
            "444444": q(100000, 5.0, 6e9, 60_000),             # 시총 5조 → G 탈락
        }
        self.strength = 120.0
        self.minute_calls = 0
    def get_multi_price(self, codes): return {c: self.quotes[c] for c in codes if c in self.quotes}
    def get_market_data(self, code): return {"lstn_stcn": "20000000"}
    def get_ccnl(self, code):
        return {"strength": self.strength, "ticks": [f"1029{59 - i // 2:02d}" for i in range(30)]}
    def get_minute_bars_by_date(self, code, date, hhmmss):
        self.minute_calls += 1
        return rising_bars() if self.minute_calls == 1 else []
    def get_minute_bars(self, code, hhmmss): return []


def scanner(api):
    s = D.DantaScanner(api)
    s._pool = (D.time.time() + 1e6, {c: f"종목{c[0]}" for c in api.quotes})
    return s


class DantaScan(unittest.TestCase):
    def test_tick_rate(self):
        self.assertAlmostEqual(D.tick_rate([f"1029{59 - i // 2:02d}" for i in range(30)], "103000"), 120.0)
        self.assertEqual(D.tick_rate(["102959", "102950", "102800"], "103000"), 2)

    def test_cci_flat_then_spike(self):
        cci, n = D.five_min_cci(rising_bars())
        self.assertGreater(n, 100); self.assertGreater(cci, 100)

    def test_flow(self):
        api, clock = API(), [1000.0]
        D.time.time = lambda: clock[0]
        s = scanner(api)
        out = s.scan(NOW)
        self.assertEqual(out["stage1"], 2)                        # 111111, 444444
        self.assertEqual([r["code"] for r in out["results"]], ["111111"])
        self.assertIn("E1분순매수 측정중", out["results"][0]["fails"])
        clock[0] += 60
        api.quotes["111111"]["volume"] = 1_060_000                 # 1분간 6만주, 강도 120% → 순매수 +5,454
        r = s.scan(NOW)["results"][0]
        self.assertTrue(r["passed"], r["fails"])
        self.assertGreater(r["netbuy_1m"], 100)
        api.strength = 101.0; clock[0] += 60
        r = s.scan(NOW)["results"][0]
        self.assertIn("B체결강도101%", r["fails"]); self.assertIn("H매수비율50%", r["fails"])

    def test_overlap_tag(self):
        db = os.path.abspath("log.db")
        con = sqlite3.connect(db)
        con.execute("CREATE TABLE leader_obs (date TEXT, code TEXT, passed INTEGER)")
        con.execute("INSERT INTO leader_obs VALUES ('2026-10-06', '111111', 0)")
        con.commit(); con.close()
        self.assertEqual(D.overlap_today("111111", "2026-10-06", db), "주도주 근접")
        self.assertEqual(D.overlap_today("999999", "2026-10-06", db), "")


if __name__ == "__main__":
    unittest.main()
