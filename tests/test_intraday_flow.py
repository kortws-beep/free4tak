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

    def test_kiwoom(self):
        class KW:
            def _post(self, path, aid, body):
                if body["amt_qty_tp"] == "1":
                    return {"opmr_invsr_trde_chart": [{"tm": "090000", "frgnr_invsr": "0", "orgn": "0", "penfnd_etc": "0", "invtrt": "0"},
                                                      {"tm": "091900", "frgnr_invsr": "-39891", "orgn": "0", "penfnd_etc": "0", "invtrt": "0"},
                                                      {"tm": "141400", "frgnr_invsr": "-200142", "orgn": "79467", "penfnd_etc": "5", "invtrt": "7"}]}
                return {"opmr_invsr_trde_chart": [{"tm": "091900", "frgnr_invsr": "-179000", "orgn": "0"},
                                                  {"tm": "141400", "frgnr_invsr": "-929000", "orgn": "372000"}]}
        old = F.time.sleep
        F.time.sleep = lambda s: None
        try:
            rows = F.kiwoom_rows(KW(), "005930")
        finally:
            F.time.sleep = old
        self.assertEqual(rows, [("091900", -39891, 0, 0, 0, -179000, 0), ("141400", -200142, 79467, 5, 7, -929000, 372000)])
        F.save_kiwoom("2026-11-04", "005930", rows, db="k.db")
        self.assertEqual(sqlite3.connect("k.db").execute("SELECT count(*) FROM kflow").fetchone()[0], 2)

    def test_prices_next_day(self):
        F.save_prices("2026-11-04", {"A": {"price": 100, "open": 95, "high": 101, "low": 94, "chg": 2, "prev_close": 98}}, db="p.db")
        F.save_prices("2026-11-05", {"A": {"price": 104, "open": 102, "high": 106, "low": 100, "chg": 4}}, db="p.db")
        c = sqlite3.connect("p.db")
        self.assertEqual(c.execute("SELECT close, next_open, next_high FROM day WHERE date='2026-11-04'").fetchone(), (100, 102, 106))
        self.assertEqual(c.execute("SELECT next_open FROM day WHERE date='2026-11-05'").fetchone(), (None,))


if __name__ == "__main__":
    unittest.main()
