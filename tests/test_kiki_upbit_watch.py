"""키키 업비트 점검 알림 — 공지 고르기/시간 뽑기, 미리알림·1시간전·시작/끝, 재실행 시 중복 없음."""
import datetime
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "interface"))
import kiki_upbit_watch as W  # noqa: E402

BODY = "<p>점검 시간</p><p>서버 점검 시간 : 2026-10-19(월) 02:00 ~ 08:00 (약 6시간 예정)</p>"


def notices(*arts):
    return lambda url, params, text=False: {"articles": list(arts)}


def art(i, title, body=BODY, ver="2026-10-16T02:17:00Z"):
    return {"id": i, "title": title, "body": body, "edited_at": ver, "html_url": f"https://x/{i}"}


class Parse(unittest.TestCase):
    def test_window_and_filter(self):
        s, e = W.parse_window(W.text_of(BODY))
        self.assertEqual((s, e), (datetime.datetime(2026, 10, 19, 2), datetime.datetime(2026, 10, 19, 8)))
        self.assertEqual(W.fmt_window((s, e)), "10/19(월) 02:00 ~ 08:00 (약 6시간)")
        s, e = W.parse_window("점검 시간: 2026-10-25(토) 23:00 ~ 2026-10-26(일) 03:30")
        self.assertEqual(e, datetime.datetime(2026, 10, 26, 3, 30))
        self.assertTrue(W.is_server_notice("서버 점검 안내 (10/19 02:00 ~)"))
        self.assertTrue(W.is_server_notice("[긴급] 긴급 점검 안내"))
        self.assertFalse(W.is_server_notice("리플(XRP) 입출금 일시 중단 및 지갑 점검"))
        self.assertFalse(W.is_server_notice("투자노트 이용 방법"))


class Notices(unittest.TestCase):
    def test_first_run_skips_past_then_alerts_new_and_edit(self):
        now = datetime.datetime(2026, 10, 16, 12, 0)
        st = {}
        old = art(1, "서버 점검 안내 (09/01)", "점검 시간 : 2026-09-01(월) 02:00 ~ 04:00")
        self.assertEqual(W.check_notices(st, notices(old), now), [])          # 처음 실행: 지난 점검은 조용히
        new = art(2, "서버 점검 안내 (10/19 02:00 ~)")
        out = W.check_notices(st, notices(new, old), now)
        self.assertEqual(len(out), 1); self.assertIn("점검 예정", out[0]); self.assertIn("02:00 ~ 08:00", out[0])
        self.assertEqual(W.check_notices(st, notices(new, old), now), [])     # 같은 버전 다시 안 알림
        edited = art(2, "서버 점검 안내 (10/19 02:00 ~)", ver="2026-10-17T00:00:00Z")
        self.assertEqual(len(W.check_notices(st, notices(edited), now)), 1)   # 수정되면 다시

    def test_started_window_left_to_status_switch(self):
        st = {}
        out = W.check_notices(st, notices(art(2, "서버 점검 안내")), datetime.datetime(2026, 10, 19, 6, 30))
        self.assertEqual(out, [])                                             # 02:00에 이미 시작 → 공지로는 안 알림
        self.assertIn("2", st["schedule"])

    def test_reminder_once_one_hour_before(self):
        st = {}
        W.check_notices(st, notices(art(2, "서버 점검 안내")), datetime.datetime(2026, 10, 16, 12))
        self.assertEqual(W.due_reminders(st, datetime.datetime(2026, 10, 19, 0, 30)), [])
        r = W.due_reminders(st, datetime.datetime(2026, 10, 19, 1, 10))
        self.assertEqual(len(r), 1); self.assertIn("50분 전", r[0])
        self.assertEqual(W.due_reminders(st, datetime.datetime(2026, 10, 19, 1, 20)), [])


class Status(unittest.TestCase):
    def test_start_end_and_quiet_first_off(self):
        cur = {"d": {"maintenance": False}}
        f = lambda url, params, text=False: "안내 ※점검 시간: 02:00 ~ 08:00" if text else cur["d"]
        st = {}
        self.assertEqual(W.check_status(st, f), [])                          # 처음 보는데 꺼져 있음 → 조용히
        cur["d"] = {"maintenance": True, "title": "예정된 서비스 점검이 진행중입니다.", "content1_url": "u"}
        out = W.check_status(st, f)
        self.assertIn("점검 시작", out[0]); self.assertIn("02:00 ~ 08:00", out[0])
        self.assertEqual(W.check_status(st, f), [])                          # 계속 켜짐 → 반복 안 함
        cur["d"] = {"maintenance": False}
        self.assertIn("점검 끝", W.check_status(st, f)[0])


if __name__ == "__main__":
    unittest.main()
