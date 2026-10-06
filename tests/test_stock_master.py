"""전 상장종목 마스터 — KIS 고정폭 파일 파싱, 테마 목록과 합치기, 신규 종목 수집 대상."""
import os
import sqlite3
import unittest

from _helpers import use_temp_cwd

_tmp = use_temp_cwd()
import update_stock_master as M  # noqa: E402
import collect_daily_data as C   # noqa: E402
import three_month_leader as T   # noqa: E402


def line(code, name, group, tail_len):
    return f"{code:<9}{'KR7' + code + '000':<12}{name}" + group + "0" * (tail_len - 3) + "\n"


FAKE = {
    "KOSPI":  [("005930", "삼성전자", "ST"), ("069500", "KODEX 200", "EF")],
    "KOSDAQ": [("452280", "한선엔지니어링", "ST"), ("999990", "케이스팩10호", "ST"),
               ("123456", "케이엔안시스템", "ST")],
}


def fake_fetch(market):
    tail = M.MASTER_URLS[market][1]
    return M.parse_master("".join(line(c, n, g, tail) for c, n, g in FAKE[market]), tail)


class StockMaster(unittest.TestCase):
    def setUp(self):
        self.db = os.path.abspath("m.db")
        if os.path.exists(self.db):
            os.remove(self.db)
        con = sqlite3.connect(self.db)
        con.execute("CREATE TABLE kr_theme_stocks (theme_name TEXT, stock_name TEXT)")
        con.execute("INSERT INTO kr_theme_stocks VALUES ('반도체', '삼성전자KOSPI 005930')")
        con.execute("CREATE TABLE kr_stock_daily_data (date TEXT, stock_name TEXT)")
        con.execute("INSERT INTO kr_stock_daily_data VALUES ('2026-10-02', '삼성전자')")
        con.commit(); con.close()

    def test_parse_and_update(self):
        self.assertEqual(fake_fetch("KOSDAQ")[0], ("452280", "한선엔지니어링", "ST"))
        r = M.update(self.db, fetch=fake_fetch)
        self.assertEqual(r, {"total": 3, "added": 3, "removed": 0, "not_in_theme": 2})  # ETF·스팩 제외
        con = sqlite3.connect(self.db)
        raws = C.all_raw_names(con)
        self.assertEqual(sorted(raws), sorted(["삼성전자KOSPI 005930", "한선엔지니어링KOSDAQ 452280",
                                               "케이엔안시스템KOSDAQ 123456"]))
        self.assertEqual(C.parse_stock("케이엔안시스템KOSDAQ 123456"), ("케이엔안시스템", "123456"))
        self.assertEqual(T._name_code_map(con)["한선엔지니어링"], "452280")
        # 상장폐지(목록에서 사라짐) 반영
        FAKE["KOSDAQ"].pop()
        try:
            self.assertEqual(M.update(self.db, fetch=fake_fetch)["removed"], 1)
        finally:
            FAKE["KOSDAQ"].append(("123456", "케이엔안시스템", "ST"))

    def test_empty_download_keeps_old_list(self):
        with self.assertRaises(RuntimeError):
            M.update(self.db, fetch=lambda m: [])


if __name__ == "__main__":
    unittest.main()
