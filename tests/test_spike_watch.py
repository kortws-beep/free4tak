"""11-07 대장 '끼' 관심그룹 급등 알림 — A 급등 시작형 · B 계단식→돌파형."""
import unittest

from _helpers import use_temp_cwd

_tmp = use_temp_cwd()
import spike_watch as S  # noqa: E402


def hm(m):
    return f"{m // 60:02d}{m % 60:02d}"


class Spike(unittest.TestCase):
    def test_a_sudden(self):
        w, val, got = S.SpikeWatch(), 0.0, []
        for i in range(30):                                  # 09:00~09:29 조용히 1분 1억씩
            val += 1e8
            got += w.update("2026-11-09", hm(540 + i), {"X": {"price": 10000, "value": val}})
        self.assertEqual(got, [])
        for k, p in enumerate([10100, 10200, 10300, 10400]):   # 4분 만에 +4% · 분당 5억
            val += 5e8
            got += w.update("2026-11-09", hm(570 + k), {"X": {"price": p, "value": val}})
        self.assertEqual([g[1] for g in got], ["A"])
        val += 5e8
        self.assertEqual(w.update("2026-11-09", hm(574), {"X": {"price": 10500, "value": val}}), [])   # 30분 안 다시 안 울림

    def test_a_needs_volume(self):
        w, val, got = S.SpikeWatch(), 0.0, []
        for i in range(30):
            val += 1e8
            got += w.update("2026-11-09", hm(540 + i), {"X": {"price": 10000, "value": val}})
        for k, p in enumerate([10100, 10200, 10300, 10400]):   # 가격만 오르고 거래대금은 평소 그대로
            val += 1e8
            got += w.update("2026-11-09", hm(570 + k), {"X": {"price": p, "value": val}})
        self.assertEqual(got, [])

    def test_b_staircase_breakout(self):
        w, val, got = S.SpikeWatch(), 0.0, []
        p = 10000.0
        for i in range(75):                                  # 75분 동안 15분마다 +0.8%씩 계단
            if i and i % 15 == 0:
                p *= 1.008
            val += 1e8
            got += w.update("2026-11-09", hm(540 + i), {"X": {"price": round(p), "value": val}})
        self.assertEqual(got, [])
        for k in range(3):                                   # 3분 +2% 돌파 · 분당 4억
            p *= 1.007
            val += 4e8
            got += w.update("2026-11-09", hm(615 + k), {"X": {"price": round(p), "value": val}})
        self.assertIn("B", [g[1] for g in got])
        self.assertEqual(S.SpikeWatch().update("2026-11-09", "0900", {"X": {"price": 0, "value": 0}}), [])

    def test_group_codes(self):
        api = type("A", (), {"get_watchlist_groups": lambda s, h: {"1": "끼", "2": "new"},
                             "get_watchlist_stocks": lambda s, g, h: {"1": [("030530", "원익홀딩스")], "2": [("A", "a")]}[g]})()
        self.assertEqual(S.group_codes(api, "id"), {"030530": "원익홀딩스"})


if __name__ == "__main__":
    unittest.main()
