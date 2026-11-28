"""11-28 이벤트 허브 — 세이브티커 [경제지표] 해석, MTN 추출(가짜 AI), 출처 없으면 저장 안 함, 미리 사기 창 날짜, 달력 데이터."""
import datetime
import json
import os
import tempfile
import unittest

from _helpers import use_temp_cwd

_td = use_temp_cwd()
_tmp = _td.name
import money_weather  # noqa: E402
import event_hub      # noqa: E402

money_weather.DB_PATH = os.path.join(_tmp, "mw.db")
event_hub.STATE = os.path.join(_tmp, "state.json")

PDF_TXT = """[에너지]
WTI 81.69 → 84.15 ▲

[경제지표]
22:30 - 미국 - 10월 PCE가격지수 ★★★ 3.8% = (예상: 3.8% 이전: 3.6%)
22:30 – 미국 – 신규실업수당청구건수★★★ 189K ▼ (예상: 200K 이전: 203K)
00:00 – 미국 – 10월 신규주택판매★★★701K ▲ (예상: 650K 이전: 676K)
02:00 – 미국 – EIA 천연가스 재고 ★ - 24 ▼
04:00 - 미국 - 연준 베이지북 ★★★
 전일 요약
"""


class Hub(unittest.TestCase):
    def test_parse_saveticker(self):
        rows = event_hub.parse_saveticker(PDF_TXT, datetime.date(2026, 11, 27))
        self.assertEqual(rows[0][:4], ("2026-11-26", "22:30", "미국 10월 PCE가격지수", 3))      # 22:30 = PDF 전날 밤
        self.assertEqual(rows[2][:2], ("2026-11-27", "00:00"))                                  # 자정 넘으면 PDF 날
        self.assertEqual(len(rows), 5)
        self.assertEqual(event_hub._econ_rec("미국 신규실업수당청구건수"), "weekly")
        self.assertEqual(event_hub._econ_rec("미국 10월 PCE가격지수"), "monthly")
        self.assertEqual(event_hub._econ_rec("미국 3분기 실질GDP성장률"), "quarterly")

    def test_add_needs_source_and_fills(self):
        self.assertFalse(event_hub.add("2026-12-01", "기타", "제목", ""))
        self.assertTrue(event_hub.add("2026-12-01", "기타", "제목", "출처"))
        self.assertFalse(event_hub.add("2026-12-01", "기타", "제목", "출처", recurrence="oneoff"))  # 중복 → 빈 칸만 채움
        e = [x for x in event_hub.calendar_data("2026-11-01", "2026-12-31")["events"] if x["title"] == "제목"][0]
        self.assertEqual(e["recurrence"], "oneoff")

    def test_mtn_fake_ai(self):
        d = os.path.join(_tmp, "mtn")
        os.makedirs(d, exist_ok=True)
        event_hub.MTN_DIR = d
        with open(os.path.join(d, "2026-11-27.txt"), "w", encoding="utf-8") as f:
            f.write("방송 " * 1500)
        with open(os.path.join(d, "2026-11-27_전문가.txt"), "w", encoding="utf-8") as f:
            f.write("x")
        fake = lambda p: "설명\n" + json.dumps([
            {"date": "2026-12-09", "title": "美 12월 FOMC", "kind": "통화정책", "recurrence": "oneoff", "sectors": "", "quote": "12월 9일 FOMC"},
            {"date": None, "title": "젠슨 황 청문회", "kind": "돌발", "recurrence": "oneoff", "sectors": "반도체", "quote": "청문회"},
            {"date": "2026-12-01", "title": "출처 없는 것", "kind": "기타", "recurrence": "x"}], ensure_ascii=False)
        self.assertEqual(event_hub.pending_mtn_days(), ["2026-11-27"])
        self.assertEqual(event_hub.ingest_mtn("2026-11-27", ask=fake), 2)          # quote 없는 건 버림
        self.assertEqual(event_hub.pending_mtn_days(), [])
        ev = {e["title"]: e for e in event_hub.calendar_data("2026-11-01", "2026-12-31")["events"]}
        self.assertEqual(ev["美 12월 FOMC"]["recurrence"], "monthly")              # AI '돌발' 실수 바로잡음
        self.assertEqual(ev["젠슨 황 청문회"]["date"], "2026-11-27")                 # 날짜 없으면 방송 날
        self.assertIn("MTN 아침 2026-11-27", ev["젠슨 황 청문회"]["source"])

    def test_windows_ces(self):
        w = [x for x in event_hub.windows(datetime.date(2026, 11, 28)) if x["event"] == "CES" and x["sector"] == "반도체"][0]
        self.assertEqual((w["date"], w["buy"], w["sell"], w["verdict"]), ("2027-01-05", "2026-12-03", "2026-12-29", "ok"))


    def test_cluster_same_events(self):
        E = lambda d, t, o="MTN": {"date": d, "title": t, "origin": o, "sectors": "반도체", "recurrence": "oneoff", "impact": "중간", "kind": "돌발"}
        ev = [E("2026-11-30", "美 사이버먼데이"), E("2026-11-30", "미국 사이버먼데이"),
              E("2026-12-01", "美 하원 젠슨황 비공개 조사"), E("2026-12-01", "젠슨 황 美 상하원 청문회"),
              E("2026-12-01", "젠슨황 하원 청문회"), E("2026-12-03", "美 상원은행위원회 젠슨황 청문회"),
              E("2026-12-01", "엔비디아 GTC 행사 개막"), E("2026-12-09", "젠슨황 하원 청문회")]   # 6일 뒤는 다른 묶음
        g = event_hub.cluster(ev)
        self.assertEqual([x["n"] for x in g], [2, 4, 1, 1])
        jh = g[1]
        self.assertEqual((jh["start"], jh["end"]), ("2026-12-01", "2026-12-03"))
        self.assertIn("젠슨", jh["title"])


    def test_stars_scale(self):
        self.assertEqual([event_hub._stars(z) for z in (0.5, 1.2, 1.9, 2.6)], [0, 1, 2, 3])


if __name__ == "__main__":
    unittest.main()
