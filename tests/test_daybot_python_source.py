"""데이봇 파이썬판 후보 소스 — 리나 기록 읽기, fallback(타임아웃 검색식만)/union 합치기."""
import datetime as dt
import os
import sqlite3
import threading
import unittest

from _helpers import stub, use_temp_cwd

for _n in ["kis_websocket", "kiwoom_api", "notifier", "master_db", "sector_monitor"]:
    stub(_n)
stub("kis_websocket", KisWebSocket=object)
stub("kiwoom_api", KiwoomAPI=object)
stub("notifier", Notifier=object)
stub("kis_api", KisAPI=object)

_tmp = use_temp_cwd()
import daybot_db  # noqa: E402
import daybot     # noqa: E402

NOW = dt.datetime(2026, 10, 8, 9, 40)


def build():
    db = os.path.abspath("log.db")
    if os.path.exists(db):
        os.remove(db)
    c = sqlite3.connect(db)
    for t in ("leader_obs", "danta_obs"):
        c.execute(f"CREATE TABLE {t} (date TEXT, time TEXT, code TEXT, name TEXT, passed INTEGER)")
    rows = [("leader_obs", "09:38", "111110", "주도A", 1), ("leader_obs", "09:20", "222220", "옛날", 1),
            ("leader_obs", "09:38", "333330", "탈락", 0), ("danta_obs", "09:39", "444440", "단타B", 1)]
    for t, tm, code, name, ok in rows:
        c.execute(f"INSERT INTO {t} VALUES ('2026-10-08', ?, ?, ?, ?)", (tm, code, name, ok))
    c.commit(); c.close()
    return db


class PythonSource(unittest.TestCase):
    def test_read_recent_passes(self):
        hits, names, latest = daybot_db.python_scan_hits(NOW, build())
        self.assertEqual(hits, {"111110": ["주도주검색식3"], "444440": ["단타000"]})   # 오래된·탈락 제외
        self.assertEqual(latest["3개월수급 당일주도주"], None)                          # 테이블 없음

    def _bot(self):
        b = object.__new__(daybot.DayBot)
        b.code_name_map, b._positions_lock = {}, threading.Lock()
        return b

    def test_fallback_fills_only_failed_conditions(self):
        db = build()
        daybot.python_scan_hits = lambda: daybot_db.python_scan_hits(NOW, db)
        daybot.SCAN_SOURCE = "fallback"
        tag_map, names = {"555550": ["단타000"]}, {}
        codes = self._bot()._merge_python_hits(["555550"], tag_map, names, {"단타000"})  # 주도주는 타임아웃
        self.assertEqual(codes, ["555550", "111110"])                 # 주도주만 파이썬으로, 단타는 키움 그대로
        self.assertEqual(tag_map["111110"], ["주도주검색식3"]); self.assertNotIn("444440", tag_map)
        daybot.SCAN_SOURCE = "union"
        codes = self._bot()._merge_python_hits(["555550"], {"555550": ["단타000"]}, {}, {"단타000"})
        self.assertEqual(sorted(codes), ["111110", "444440", "555550"])
        daybot.SCAN_SOURCE = "kiwoom"


if __name__ == "__main__":
    unittest.main()
