"""11-05 외국인·기관 장중 잠정 수급 수집 — 응답 파싱·저장(같은 날 다시 돌리면 덮어씀)."""
import sqlite3
import unittest

from _helpers import use_temp_cwd

_tmp = use_temp_cwd()
import intraday_flow as F  # noqa: E402


class Flow(unittest.TestCase):
    def test_parse_save(self):
        out = [{"bsop_hour_gb": "2", "frgn_fake_ntby_qty": "-00000000000495000", "orgn_fake_ntby_qty": "000000000000002000",
                "sum_fake_ntby_qty": "-00000000000493000"},
               {"bsop_hour_gb": "1", "frgn_fake_ntby_qty": "-00000000000334000", "orgn_fake_ntby_qty": "000000000000000000",
                "sum_fake_ntby_qty": "-00000000000334000"}, {"bad": 1}]
        rows = F.parse(out)
        self.assertEqual(rows, [(1, -334000, 0, -334000), (2, -495000, 2000, -493000)])
        F.save("2026-11-04", "005930", rows, db="f.db"); F.save("2026-11-04", "005930", rows, db="f.db")
        self.assertEqual(sqlite3.connect("f.db").execute("SELECT count(*) FROM flow").fetchone()[0], 2)
        self.assertEqual(F.parse(None), [])


if __name__ == "__main__":
    unittest.main()
