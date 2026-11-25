"""weather_control — 날씨판 → 봇 상태(쉼=정지, 방어=태세, 해제는 날씨판이 건 정지만)."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _helpers  # noqa: F401,E402

sys.path.insert(0, os.path.join(_helpers.REPO, "core"))
import weather_control as wc  # noqa: E402
from common_utils import read_state  # noqa: E402


def board(stock, coin, day="2026-11-17"):
    return {"date": day, "stock_total": (-8 if "쉼" in stock else 0, stock), "coin_total": (0, coin)}


class WeatherControlTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        wc._BASE = self.d
        for f in wc.STATE_FILES.values():
            json.dump({"paused": False}, open(os.path.join(self.d, f), "w"))

    def st(self, bot):
        return read_state(os.path.join(self.d, wc.STATE_FILES[bot]), default={})

    def test_rest_pauses_and_next_day_resumes(self):
        wc.apply(board("🔴 쉼", "🟠 방어"))
        self.assertTrue(self.st("sbot")["paused"])
        self.assertEqual(self.st("sbot")["pause_reason"], wc.PAUSE_REASON)
        self.assertFalse(self.st("cbot")["paused"])
        self.assertEqual(wc.today_weather(self.st("cbot"), "2026-11-17"), "방어")
        wc.apply(board("🟡 보통", "🟡 보통", "2026-11-18"))
        self.assertFalse(self.st("sbot")["paused"])
        self.assertEqual(wc.today_weather(self.st("sbot"), "2026-11-18"), "보통")

    def test_manual_pause_untouched(self):
        p = os.path.join(self.d, wc.STATE_FILES["daybot"])
        json.dump({"paused": True, "pause_reason": "manual"}, open(p, "w"))
        wc.apply(board("🟢 공격", "🟢 공격"))
        self.assertTrue(self.st("daybot")["paused"])          # 대장이 건 정지는 그대로
        self.assertEqual(self.st("daybot")["pause_reason"], "manual")

    def test_override_releases_weather_pause(self):
        wc.apply(board("🔴 쉼", "🔴 쉼"))
        wc.apply(board("🔴 쉼", "🔴 쉼"), override="보통")
        self.assertFalse(self.st("cbot")["paused"])
        self.assertTrue(self.st("cbot")["weather"]["override"])

    def test_override_stock_only_keeps_coin_paused(self):
        """★ 11-25 `!날씨해제 주식` — sbot·daybot만 풀고 cbot은 쉼 그대로(코인만도 반대로)."""
        wc.apply(board("🔴 쉼", "🔴 쉼"))
        wc.apply(board("🔴 쉼", "🔴 쉼"), override="보통", bots=wc.RELEASE_WORDS["주식"])
        self.assertFalse(self.st("sbot")["paused"])
        self.assertFalse(self.st("daybot")["paused"])
        self.assertTrue(self.st("cbot")["paused"])
        wc.apply(board("🔴 쉼", "🔴 쉼"), override="보통", bots=wc.RELEASE_WORDS["코인"])
        self.assertFalse(self.st("cbot")["paused"])

    def test_recompute_same_day_keeps_override(self):
        """★ 11-25 재부팅 뒤 07:10 계산이 다시 돌아도 대장 해제는 유지."""
        wc.apply(board("🔴 쉼", "🔴 쉼"))
        wc.apply(board("🔴 쉼", "🔴 쉼"), override="보통")
        wc.apply(board("🔴 쉼", "🔴 쉼"))                      # 재시작으로 다시 계산
        self.assertFalse(self.st("sbot")["paused"])
        self.assertTrue(self.st("sbot")["weather"]["override"])

    def test_stale_weather_ignored(self):
        wc.apply(board("🟠 방어", "🟠 방어"))
        self.assertEqual(wc.today_weather(self.st("sbot"), "2026-11-18"), "")


if __name__ == "__main__":
    unittest.main()
