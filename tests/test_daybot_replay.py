"""daybot 분봉 재현 — 손절/트레일링/기한/시초유예, 분봉 페이지 수집, 슬롯·일손실 포트폴리오."""
import os
import sys
import unittest

from _helpers import use_temp_cwd

_tmp = use_temp_cwd()
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backtest"))
import daybot_replay as R  # noqa: E402

E = 10000 / (1 + R.SLIP)          # 매수가×(1+미끄러짐) = 10000 이 되게


def bar(t, p, h=None, l=None):
    return (t, p, h if h is not None else p, l if l is not None else p)


class Simulate(unittest.TestCase):
    def test_stop_and_gap_fill(self):
        d = [("2026-10-08", [bar("091000", E), bar("091100", 9800), bar("091200", 9600, 9700, 9600)])]
        r = R.simulate(d, "091000", E, R.CURRENT)
        self.assertEqual((r["reason"], r["exit_time"]), ("손절", "091200"))
        self.assertAlmostEqual(r["ret"], 9650 * (1 - R.SLIP) / 10000 - 1 - R.COST)    # 손절선 9650
        gap = [("2026-10-08", [bar("091000", E)]), ("2026-10-09", [bar("090000", 9300, 9400, 9200)])]
        self.assertAlmostEqual(R.simulate(gap, "091000", E, R.CURRENT)["ret"],
                               9400 * (1 - R.SLIP) / 10000 - 1 - R.COST)               # 봉 전체가 아래 → 고가

    def test_trailing_tight_and_floor(self):
        d = [("2026-10-08", [bar("091000", E), bar("091100", 10300, 10300, 10200),     # +3% → 트레일링 시작
                             bar("091200", 10250, 10350, 10200), bar("091300", 10100, 10150, 10100)])]
        r = R.simulate(d, "091000", E, R.CURRENT)
        self.assertEqual(r["reason"], "트레일링")
        self.assertAlmostEqual(r["ret"], 10150 * (1 - R.SLIP) / 10000 - 1 - R.COST)   # max(10350×0.985, 10100)=10194.75 > 고가 10150

    def test_hold_limit_and_grace(self):
        flat = [bar("090000", 9900), bar("100000", 9900)]
        days = [("2026-10-05", [bar("091000", E)])] + [(f"2026-10-0{6 + i}", flat) for i in range(3)]
        r = R.simulate(days, "091000", E, R.CURRENT)
        self.assertEqual((r["reason"], r["exit_date"], r["exit_time"]), ("보유기한", "2026-10-08", "090000"))
        shake = [("2026-10-05", [bar("091000", E)]),
                 ("2026-10-06", [bar("090200", 9600, 9700, 9600), bar("091500", 10000), bar("092000", 10300)])]
        self.assertEqual(R.simulate(shake, "091000", E, R.CURRENT)["reason"], "손절")
        g = R.simulate(shake, "091000", E, R.Rule(grace=10))
        self.assertEqual(g["reason"], "보유중")                                          # 시초 10분 유예로 살아남음

    def test_afternoon_rules(self):
        # 아침에 -4%까지 밀렸다가 오후에 회복 — 현행은 손절, 대장식(-7%/13시후 +1%)은 오후정리
        d = [("2026-10-08", [bar("091000", E), bar("093000", 9600, 9700, 9580), bar("120000", 9900),
                             bar("133000", 10120, 10150, 10000), bar("151500", 10050)])]
        self.assertEqual(R.simulate(d, "091000", E, R.CURRENT)["reason"], "손절")
        r = R.simulate(d, "091000", E, R.Rule(stop=-7, pm_take=1.0, eod="151500"))
        self.assertEqual((r["reason"], r["exit_time"]), ("오후정리", "133000"))
        self.assertAlmostEqual(r["ret"], 10120 * (1 - R.SLIP) / 10000 - 1 - R.COST)
        flat = [("2026-10-08", [bar("091000", E), bar("120000", 9900), bar("151500", 9950), bar("152000", 9990)])]
        r2 = R.simulate(flat, "091000", E, R.Rule(stop=-7, pm_take=1.0, eod="151500"))
        self.assertEqual((r2["reason"], r2["exit_time"]), ("당일청산", "151500"))
        self.assertIn("13시후+1%정리", R.Rule(pm_take=1.0, eod="151500").label())

    def test_open_position(self):
        r = R.simulate([("2026-10-08", [bar("091000", E), bar("091100", 10100)])], "091000", E, R.CURRENT)
        self.assertEqual((r["reason"], r["exit_time"]), ("보유중", "091100"))


class Store(unittest.TestCase):
    def test_paging_regular_hours_and_holiday(self):
        class Api:
            def __init__(self): self.calls = []
            def get_minute_bars_by_date(self, code, ymd, t):
                self.calls.append(t)
                if ymd == "20261005":
                    return []
                allm = [f"{h:02d}{m:02d}00" for h in range(8, 16) for m in range(60) if "080000" <= f"{h:02d}{m:02d}00" <= "153000"]
                upto = [x for x in allm if x <= t][-120:]
                return [{"date": ymd, "time": x, "price": 1.0, "high": 1.0, "low": 1.0} for x in reversed(upto)]
        api = Api()
        st = R.MinuteStore(api, path="m.db", pause=0)
        bars = st.day("A", "2026-10-07")
        self.assertEqual((bars[0][0], bars[-1][0], len(bars)), ("090000", "153000", 391))
        self.assertEqual(api.calls[0], "153000"); self.assertEqual(len(api.calls), 4)
        n = len(api.calls); st.day("A", "2026-10-07"); self.assertEqual(len(api.calls), n)   # 캐시
        days = st.days_from("A", "2026-10-02", 2)
        self.assertEqual([d for d, _ in days], ["2026-10-02", "2026-10-06"])           # 주말·10-05(분봉없음) 건너뜀


class Portfolio(unittest.TestCase):
    def r(self, code, t, ret, exit_t, d="2026-10-08"):
        return {"code": code, "date": d, "time": t, "ret": ret, "exit_date": d, "exit_time": exit_t}

    def test_slots_and_loss_modes(self):
        res = [self.r("A", "090500", -0.06, "091000"), self.r("B", "091100", -0.05, "092000"),
               self.r("C", "093000", 0.04, "100000"), self.r("D", "093100", 0.02, "100000"),
               self.r("E", "093200", 0.02, "100000"), self.r("F", "093300", 0.02, "100000")]
        allw = R.WINDOWS["전체"]
        s = R.portfolio(res, allw, "없음")
        self.assertEqual(s["n"], 5)                                                     # F는 슬롯 3개 꽉 참
        self.assertAlmostEqual(s["krw"], (-0.06 - 0.05 + 0.04 + 0.02 + 0.02) * R.AMT)
        stop = R.portfolio(res, allw, "-10만 매수중단")
        self.assertEqual(stop["n"], 2)                                                  # -11만 뒤 매수 없음
        half = R.portfolio(res, allw, "-10만 절반매수")
        self.assertAlmostEqual(half["krw"], (-0.11 + (0.04 + 0.02 + 0.02) / 2) * R.AMT)
        early = R.portfolio(res, R.WINDOWS["09:40 이전만"], "없음")
        self.assertEqual(early["n"], 5)


class Sector(unittest.TestCase):
    def test_tags_new_and_top_sector(self):
        import sqlite3
        c = sqlite3.connect("s.db")
        c.execute("CREATE TABLE sector_obs (date TEXT, time TEXT, grp TEXT, rank INTEGER, avg_chg REAL, "
                  "up_ratio REAL, value REAL, leader TEXT, leader_chg REAL, second TEXT, second_chg REAL)")
        c.execute("CREATE TABLE sector_alerts (date TEXT, time TEXT, code TEXT, name TEXT, grp TEXT, role TEXT, "
                  "chg REAL, is_new INTEGER)")
        d = "2026-10-08"
        c.executemany("INSERT INTO sector_obs (date, time, grp, rank, leader, second) VALUES (?,?,?,?,?,?)", [
            (d, "09:10", "반도체", 1, "가", "나"), (d, "09:10", "로봇", 5, "다", "라"),
            (d, "10:00", "로봇", 2, "다", "라"), (d, "10:00", "반도체", 1, "가", "나")])
        c.execute("INSERT INTO sector_alerts VALUES (?,?,?,?,?,?,?,?)", (d, "10:20", "009", "마", "x", "대장", 4, 0))
        c.commit(); c.close()
        sig = [{"date": d, "time": "100500", "code": "003", "name": "다"},     # 로봇 5위→2위 = 새섹터
               {"date": d, "time": "100500", "code": "001", "name": "가"},     # 반도체 처음부터 1위
               {"date": d, "time": "100500", "code": "009", "name": "마"},     # 알림은 10:20 — 아직
               {"date": d, "time": "103000", "code": "009", "name": "마"},
               {"date": d, "time": "090500", "code": "003", "name": "다"}]     # 첫 기록 전
        R.sector_tags(sig, "s.db")
        self.assertEqual([x["sector"] for x in sig], ["새섹터1·2등", "상위섹터1·2등", "", "상위섹터1·2등", ""])
        w = R.WINDOWS["09:40 이전+이후엔 새섹터만"]
        self.assertTrue(w(sig[0])); self.assertFalse(w(sig[1])); self.assertTrue(w(sig[4]))


if __name__ == "__main__":
    unittest.main()
