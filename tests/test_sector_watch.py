"""한투 관심그룹 섹터 감시 — 그룹 읽기, 강도 순위, 대장·2등주 알림, NEW 표시, 중복방지."""
import datetime
import os
import unittest

from _helpers import use_temp_cwd

_tmp = use_temp_cwd()
import sector_watch as S  # noqa: E402


def q(chg, value, price=10000):
    return {"name": "", "price": price, "chg": chg, "value": value, "volume": 1, "high": price,
            "prev_close": price}


class API:
    groups = {"01": "우주", "02": "반도체소부장", "03": "NEW", "04": "바이오", "05": "혼자"}
    stocks = {"01": [("100001", "우주대장"), ("100002", "우주2등"), ("100003", "우주3등")],
              "02": [("200001", "소부장대장"), ("200002", "소부장2등")],
              "03": [("100002", "우주2등"), ("300001", "바이오대장")],
              "04": [("300001", "바이오대장"), ("300002", "바이오2등")],
              "05": [("400001", "혼자종목")]}
    quotes = {"100001": q(12.0, 9e10), "100002": q(8.0, 5e10), "100003": q(2.0, 1e10),
              "200001": q(2.5, 8e10), "200002": q(1.0, 2e10),
              "300001": q(-2.0, 3e10), "300002": q(-1.0, 1e10), "400001": q(30.0, 1e10)}
    def get_watchlist_groups(self, uid): return dict(self.groups)
    def get_watchlist_stocks(self, code, uid): return list(self.stocks[code])
    def get_multi_price(self, codes, pause=0): return {c: self.quotes[c] for c in codes if c in self.quotes}


class SectorWatch(unittest.TestCase):
    def test_rank_and_moves(self):
        w = S.SectorWatcher(API(), "id")
        out = w.scan(datetime.datetime(2026, 10, 6, 10, 0))
        self.assertEqual([s["group"] for s in out["sectors"]], ["우주", "반도체소부장", "바이오"])  # NEW·1종목 제외
        self.assertEqual(out["sectors"][0]["members"][0]["name"], "우주대장")
        hits = S.leader_moves(out["sectors"], out["new_codes"], set())
        self.assertEqual([(h["name"], h["role"], h["is_new"]) for h in hits],
                         [("우주대장", "대장", False), ("우주2등", "2등", True)])  # 소부장 평균 1.75%지만 대장 +2.5% <3%
        self.assertIn("⭐NEW", S.format_move(hits[1], "주도주 근접"))
        self.assertIn("우주", S.format_ranking(out))
        self.assertIn("우주2등 +8.00% · 대금 500억 ⭐", S.format_group(out, "우주"))
        db = os.path.abspath("log.db")
        S.save_alerts(hits, db_path=db)
        self.assertEqual(S.alerted_today(datetime.datetime.now(S.KST).strftime("%Y-%m-%d"), db),
                         {"100001", "100002"})
        self.assertEqual(S.leader_moves(out["sectors"], out["new_codes"], {"100001", "100002"}), [])
        self.assertEqual(S.log_scan(out, db_path=db), 3)

    def test_failed_reload_keeps_previous_groups(self):
        api = API(); w = S.SectorWatcher(api, "id")
        self.assertTrue(w.groups())
        api.groups = {}; w._groups = (0.0, w._groups[1])
        self.assertTrue(w.groups())


if __name__ == "__main__":
    unittest.main()
