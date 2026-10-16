"""수동매도 패턴 — 일봉 붙이기·시각대·규칙 시뮬레이션."""
import os
import sys
import datetime
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backtest"))
from _helpers import stub  # noqa: E402
stub("three_month_leader", THEME_DB="")
import manual_sell_pattern as M  # noqa: E402

D = lambda s: datetime.datetime.fromisoformat(s)


def tr(name, bt, st, bp, sp, reason, qty=10):
    return {"name": name, "buy": D(bt), "sell": D(st), "bp": bp, "sp": sp, "qty": qty, "rate": (sp / bp - 1) * 100,
            "krw": (sp - bp) * qty - bp * qty * M.COST, "reason": reason, "hold": (D(st).date() - D(bt).date()).days}


# (date, o, h, l, c, value)
DAILY = {"가": [("2026-10-01", 100, 101, 99, 100, 1), ("2026-10-02", 100, 103, 99, 102, 1),
                ("2026-10-05", 106, 112, 105, 108, 1), ("2026-10-06", 101, 102, 88, 90, 1)]
         + [(f"2026-10-{7 + k:02d}", 90, 91, 89, 90 + k, 1) for k in range(11)],
         "나": [("2026-10-01", 100, 101, 99, 100, 1), ("2026-10-02", 108, 110, 107, 109, 1)]}


class Pattern(unittest.TestCase):
    def setUp(self):
        self.bot = tr("가", "2026-10-01 10:00:00", "2026-10-06 13:00:00", 100, 90, "손절(-10%)")
        self.man = tr("나", "2026-10-01 10:00:00", "2026-10-02 09:02:00", 100, 108, "수동매도")
        M.enrich([self.bot, self.man], DAILY)

    def test_enrich(self):
        self.assertAlmostEqual(self.bot["peak"], 12.0)
        self.assertAlmostEqual(self.bot["gap"], 101 / 108 * 100 - 100)
        self.assertAlmostEqual(self.bot["a5"], (94 / 90 - 1) * 100)          # 5거래일 뒤 10-11 종가 94
        self.assertEqual(len(self.bot["hold_rows"]), 3)       # 10-02·10-05·10-06(13시 매도라 그날 시가 포함)
        self.assertEqual(self.man["hold_rows"], [])           # 09:02 매도 — 그날 시가는 시뮬에 안 넣음
        self.assertEqual(M.slot(self.man), "시가(~09:10)"); self.assertEqual(M.slot(self.bot), "오후")

    def test_simulate_and_report(self):
        out = M.simulate([self.bot], lambda o, pc, bp, hi, n: o >= pc * 1.03 and o > bp, "A")
        self.assertIn("바뀐 매도 1건(그중 나아짐 1)", out)     # 10-05 시가 106(갭 +3.9%)에 팔았으면 손절 대신 +6%
        msg = M.report([self.bot, self.man], 30)
        self.assertIn("수동 1 · 봇 1", msg); self.assertIn("A. 시가 갭 +3%↑·수익 중: 바뀐 매도 1건", msg)
        self.assertIn("A. 시가 갭 +5%↑·수익 중: 바뀐 매도 0건", msg)
        self.assertEqual([r[2:] for r in self.bot["hold_rows"]], [(101, 1), (103, 2), (112, 3)])
        # 산 날·다음 날 고가 103 = 매수가 +3% → "못 찍음" 아님, 정리 안 됨
        self.assertIn("C. 2거래일 안에 +3% 못 찍으면 정리: 바뀐 매도 0건", msg)

    def test_fix_manual_buy_time(self):
        import sqlite3, tempfile
        db = os.path.join(tempfile.mkdtemp(), "s.db")
        c = sqlite3.connect(db)
        c.execute("CREATE TABLE trades (code TEXT, buy_time TEXT, sell_price REAL)")
        c.executemany("INSERT INTO trades VALUES (?,?,?)", [("A", "2026-09-28T10:00:00", None),
                                                            ("A", "2026-10-02T09:02:00", 108)])
        c.commit(); c.close()
        m = tr("나", "2026-10-02 09:02:00", "2026-10-02 09:02:00", 100, 108, "수동매도"); m["code"] = "A"
        o = tr("다", "2026-10-02 09:02:00", "2026-10-02 09:02:00", 100, 108, "수동매도"); o["code"] = "B"
        M.fix_manual_buy_time([m, o], db)
        self.assertEqual((m["hold"], m.get("bot_bought")), (4, True))
        self.assertEqual((o["hold"], o.get("bot_bought")), (0, None))


if __name__ == "__main__":
    unittest.main()
