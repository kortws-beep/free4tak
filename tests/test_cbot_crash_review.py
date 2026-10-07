"""cbot 급락매도 사후분석 — 매도 뒤 가격 경로, 군집, 일손실 한도 도달일."""
import datetime as dt
import os
import sqlite3
import unittest

from _helpers import use_temp_cwd

_tmp = use_temp_cwd()
import sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backtest"))
import cbot_crash_review as R  # noqa: E402

T0 = dt.datetime.now().replace(microsecond=0) - dt.timedelta(days=1)


def build():
    t, k = os.path.abspath("t.db"), os.path.abspath("k.db")
    for p in (t, k):
        if os.path.exists(p):
            os.remove(p)
    c = sqlite3.connect(t)
    c.execute("CREATE TABLE trades (market TEXT, sell_time TEXT, sell_price REAL, profit_rate REAL, "
              "profit_krw REAL, sell_reason TEXT)")
    for m, mins, krw in (("KRW-A", 0, -80000), ("KRW-B", 5, -80000), ("KRW-C", 300, 20000)):
        reason = "급락감지(-6%)" if m != "KRW-C" else "트레일링"
        c.execute("INSERT INTO trades VALUES (?,?,100,-6,?,?)",
                  (m, (T0 + dt.timedelta(minutes=mins)).isoformat(), krw, reason))
    c.commit(); c.close()
    c = sqlite3.connect(k)
    c.execute("CREATE TABLE price_ticks (market TEXT, ts TEXT, price REAL)")
    for i in range(0, 260, 1):   # 판 뒤 V자 반등: 10분 95 → 60분 104
        p = 95 if i < 20 else 104
        for m in ("KRW-A", "KRW-B"):
            c.execute("INSERT INTO price_ticks VALUES (?,?,?)",
                      (m, (T0 + dt.timedelta(minutes=i)).isoformat(timespec="seconds"), p))
    c.commit(); c.close()
    return t, k


class CrashReview(unittest.TestCase):
    def test_analyze(self):
        t, k = build()
        sells = R.load_sells(t, 30)
        rows = R.analyze(sells, R.Ticks(k))
        self.assertEqual(len(rows), 2)                       # 트레일링은 제외
        a = rows[0]
        self.assertAlmostEqual(a["after"][10], -5.0); self.assertAlmostEqual(a["after"][60], 4.0)
        self.assertEqual(a["cluster"], 1)
        hits = R.loss_limit_days(sells)
        self.assertEqual(len(hits), 1); self.assertEqual(hits[0]["cum"], -160000)
        txt = R.report(rows, hits, 30)
        self.assertIn("4시간 안에 매도가보다 +3% 이상 다시 오른 경우 2/2건", txt)
        self.assertIn("[군집 2]", txt)


if __name__ == "__main__":
    unittest.main()
