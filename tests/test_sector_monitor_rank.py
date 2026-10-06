"""sector_monitor — 테마 구성 전체를 거래대금 순으로 정렬해 상위 5종목 기록(순위 포함)."""
import os
import sqlite3
import unittest

from _helpers import use_temp_cwd

_tmp = use_temp_cwd()
import sector_monitor as M  # noqa: E402


class Kiwoom:
    def get_theme_top(self, top_n=10):
        return [{"thema_grp_cd": "T1", "thema_nm": "우주", "flu_rt": "4.0", "rising_stk_num": "5", "stk_num": "6"}]
    def get_theme_stocks(self, cd, code_name_map=None):
        return [(f"00000{i}", f"종목{i}") for i in range(6)]   # 목록 순서 ≠ 거래대금 순서


class API:
    def get_multi_price(self, codes, pause=0):
        return {c: {"value": float(int(c[-1]) * 10)} for c in codes}      # 000005가 거래대금 1위
    def get_market_data(self, code):
        return {"prdy_ctrt": "3", "acml_tr_pbmn": str(int(code[-1]) * 1e8), "vol_tnrt": "1"}


class SectorMonitorRank(unittest.TestCase):
    def test_top5_by_value_with_rank(self):
        M.time.sleep = lambda s: None
        conn = M.init_db(os.path.abspath("sm.db"))
        M.collect_once(API(), Kiwoom(), conn, {}, {})
        rows = conn.execute("SELECT code, rank_in_theme FROM stock_momentum ORDER BY rank_in_theme").fetchall()
        self.assertEqual(rows, [("000005", 1), ("000004", 2), ("000003", 3), ("000002", 4), ("000001", 5)])
        amt = conn.execute("SELECT trde_amt FROM sector_flow").fetchone()[0]
        self.assertEqual(amt, 5 + 4 + 3)        # 테마 합산은 상위 3종목만(기존 규모 유지)

    def test_old_db_gets_rank_column(self):
        db = os.path.abspath("old.db")
        c = sqlite3.connect(db)
        c.execute("CREATE TABLE stock_momentum (id INTEGER PRIMARY KEY, ts TEXT, code TEXT, theme_cd TEXT, "
                  "theme_nm TEXT, change_rate REAL, vol_ratio REAL, trde_amt REAL, cntg_str REAL, accel REAL)")
        c.commit(); c.close()
        conn = M.init_db(db)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(stock_momentum)")]
        self.assertIn("rank_in_theme", cols)


if __name__ == "__main__":
    unittest.main()
