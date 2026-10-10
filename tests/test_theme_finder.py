"""주달 테마로 종목 찾기 — 키워드·교집합(--and), 표시·코드 목록."""
import unittest

from _helpers import use_temp_cwd

_tmp = use_temp_cwd()
import theme_finder as T  # noqa: E402

ROWS = [("000001", "가로봇", "로봇(감속기)"), ("000001", "가로봇", "현대차그룹"),
        ("000002", "나로봇", "로봇"), ("000003", "다탈모", "탈모"), ("000003", "다탈모", "바이오"),
        ("000004", "라제약", "탈모 치료제")]


def px(trend, val):
    rows, p = [], 100.0
    for i in range(30):
        p *= 1 + trend + (0.01 if i % 2 else -0.01)
        rows.append((f"d{i:02d}", p, val))
    return rows


class Finder(unittest.TestCase):
    def test_find_and(self):
        self.assertEqual(sorted(T.find(ROWS, ["탈모"])), ["000003", "000004"])
        f = T.find(ROWS, ["로봇"], ["현대"])
        self.assertEqual(list(f), ["000001"]); self.assertEqual(f["000001"]["also"], ["현대차그룹"])

    def test_report(self):
        found = T.find(ROWS, ["탈모"])
        prices = {"000003": px(0.002, 5e9), "000004": px(0.002, 9e9)}
        out = T.report(found, prices, {"000003": ["업종3_제약바이오"]}, "탈모")
        self.assertIn("2종목", out)
        self.assertLess(out.index("라제약"), out.index("다탈모"))                # 거래대금 큰 순
        self.assertIn("지금 업종3_제약바이오", out)
        self.assertTrue(out.rstrip().endswith("000004 000003"))


if __name__ == "__main__":
    unittest.main()
