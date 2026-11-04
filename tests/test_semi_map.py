"""11-05 반도체 공급망 지도(부산달팽이 14부) — 이름→코드, 뉴스→종목, 종목→그룹."""
import unittest

import _helpers  # noqa: F401  (경로 설정)
import semi_map as S  # noqa: E402

M = {"SK하이닉스": "000660", "한미반도체": "042700", "네오셈": "253590", "브이엠": "089970"}   # _master() 꼴: 이름→코드


class Map(unittest.TestCase):
    def test_lookup(self):
        self.assertEqual(S.codes(["SK하이닉스", "없는회사"], M), {"000660": "SK하이닉스"})
        self.assertIn("042700", S.news_codes("HBM 증설", M))
        self.assertEqual(S.group_codes("후공정 장비·Test", M), {"042700": "한미반도체", "253590": "네오셈"})
        self.assertEqual(S.find("네오셈")[0][0], "후공정 장비·Test")
        self.assertIn("089970", S.news_codes("NAND 고단화", M))      # 사명 변경(에이피티씨→브이엠)
        self.assertEqual(S.check({k: v for k, v in M.items()})[:1] != [], True)


if __name__ == "__main__":
    unittest.main()
