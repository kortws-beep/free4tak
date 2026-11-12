"""★ 11-13 제미나이 리뷰 2번 — 한투 잔고조회 페이지 넘김(tr_cont F/M → 다음 페이지)."""
import unittest

from _helpers import use_temp_cwd
_tmp = use_temp_cwd()
import kis_api as K  # noqa: E402


class _R:
    def __init__(self, body, cont):
        self._b, self.headers = body, {"tr_cont": cont}

    def json(self):
        return self._b


class Pages(unittest.TestCase):
    def setUp(self):
        self.old = K._get

    def tearDown(self):
        K._get = self.old

    def test_two_pages(self):
        calls = []
        pages = [_R({"rt_cd": "0", "output1": [{"pdno": "000001"}], "ctx_area_fk100": "F1", "ctx_area_nk100": "N1"}, "F"),
                 _R({"rt_cd": "0", "output1": [{"pdno": "000002"}], "ctx_area_fk100": "", "ctx_area_nk100": ""}, "D")]
        K._get = lambda url, headers=None, params=None, timeout=None: (calls.append((dict(headers), dict(params))), pages.pop(0))[1]
        res, rows = K.KisAPI._balance_pages("u", {"tr_id": "x"}, {"CTX_AREA_FK100": "", "CTX_AREA_NK100": ""})
        self.assertEqual([r["pdno"] for r in rows], ["000001", "000002"])
        self.assertEqual(calls[1][1]["CTX_AREA_NK100"], "N1"); self.assertEqual(calls[1][0]["tr_cont"], "N")

    def test_fail_returns_failure(self):
        K._get = lambda *a, **k: _R({"rt_cd": "1", "msg1": "x"}, "")
        res, rows = K.KisAPI._balance_pages("u", {}, {})
        self.assertEqual((res["rt_cd"], rows), ("1", []))


if __name__ == "__main__":
    unittest.main()
