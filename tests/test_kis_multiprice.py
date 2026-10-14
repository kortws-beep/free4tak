"""한투 복수시세(FHKST11300006) — 30개씩 묶음, 저가·시가(10-14 투매 알림용) 해석."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "core"))
from _helpers import use_temp_cwd  # noqa: E402
_tmp = use_temp_cwd()
import kis_api as K  # noqa: E402


class _R:
    def __init__(self, d): self.d = d
    def json(self): return self.d


class Multi(unittest.TestCase):
    def test_multi_price(self):
        calls = []
        old = K._get
        def fake(url, headers=None, params=None, timeout=None):
            calls.append((url, headers, params))
            return _R({"output": [
                {"inter_shrn_iscd": "005930", "inter2_prpr": "70000", "inter2_oprc": "69000", "inter2_hgpr": "71000",
                 "inter2_lwpr": "68000", "acml_tr_pbmn": "123", "prdy_ctrt": "-1.2"},
                {"inter_shrn_iscd": "000660", "inter2_prpr": "0"}]})
        K._get = fake
        try:
            api = object.__new__(K.KisAPI)
            api.base_url, api.token, api.appkey, api.secret = "https://x", "t", "a", "s"
            out = api.get_multi_price([f"{k:06d}" for k in range(35)])     # 30 + 5 두 묶음
        finally:
            K._get = old
        url, headers, params = calls[0]
        self.assertEqual(len(calls), 2)
        self.assertTrue(url.endswith("/intstock-multprice")); self.assertEqual(headers["tr_id"], "FHKST11300006")
        self.assertEqual(params["FID_INPUT_ISCD_30"], "000029"); self.assertNotIn("FID_INPUT_ISCD_31", params)
        q = out["005930"]
        self.assertEqual((q["open"], q["high"], q["low"], q["price"], q["value"], q["chg"]),
                         (69000.0, 71000.0, 68000.0, 70000.0, 123.0, -1.2))


if __name__ == "__main__":
    unittest.main()
