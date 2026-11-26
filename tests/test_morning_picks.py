"""아침 '오늘의 후보'(morning_picks) — 방패 분야 추리기·문구·짧은 알림."""
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in ("intelligence", "core"):
    sys.path.insert(0, os.path.join(_ROOT, _d))
import morning_picks as M  # noqa: E402


DATA = {"sectors": [
    {"group": "방패_식음료", "chg_w": 1.2, "members": [{"name": "오리온", "code": "1", "chg": 2.0}, {"name": "농심", "code": "2", "chg": 0.4}]},
    {"group": "반도체_기판PCB", "chg_w": -4.0, "members": []},
    {"group": "방패_통신담배", "chg_w": 0.5, "members": [{"name": "KT&G", "code": "3", "chg": 0.5}]}],
    "shield": {"avg": 0.85, "ai": -3.1}}


class MorningPicks(unittest.TestCase):
    def test_shield_only_bangpae_groups_sorted(self):
        s = M.shield(DATA)
        self.assertEqual([g["group"] for g in s["groups"]], ["식음료", "통신담배"])
        self.assertEqual(s["groups"][0]["members"][0]["name"], "오리온")

    def test_format_shield_hedge_working(self):
        self.assertIn("헤지 작동 중", M.format_shield(M.shield(DATA)))

    def test_brief(self):
        d = {"at": "09:10", "rubber": [{"name": "디아이", "dd1m": -49}], "semi": {"good": [{"name": "두산", "dd": -76}]},
             "shield": M.shield(DATA)}
        b = M.brief(d)
        self.assertIn("고무공: 있음(1)", b)
        self.assertIn("반도체: 있음(1)", b)
        self.assertIn("방패: 있음(2)", b)          # 두 방패 분야 모두 AI(-3.1%)보다 강함
        self.assertIn("고무공: 없음", M.brief({"rubber": [], "semi": {}, "shield": {}}))


if __name__ == "__main__":
    unittest.main()
