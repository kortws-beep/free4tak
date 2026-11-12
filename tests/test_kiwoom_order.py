"""키움 잔고(kt00018)·시장가 매도(kt10001) — 요청 모양과 응답 해석."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "core"))
import kiwoom_api as K  # noqa: E402


class _R:
    def __init__(self, d): self.d = d
    def json(self): return self.d


class KiwoomOrder(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.old = K.requests.post
        self.reply = {}
        def post(url, headers=None, json=None, timeout=None):
            self.calls.append((url, headers, json)); return _R(self.reply)
        K.requests.post = post
        self.api = K.KiwoomAPI(); self.api.token, self.api.token_at = "t", 9e18

    def tearDown(self):
        K.requests.post = self.old

    def test_holdings(self):
        self.reply = {"acnt_evlt_remn_indv_tot": [
            {"stk_cd": "A111111", "stk_nm": "한선", "rmnd_qty": "000000000100", "trde_able_qty": "000000000060"},
            {"stk_cd": "A222222", "stk_nm": "삼미", "rmnd_qty": "50"}]}
        h = self.api.holdings()
        self.assertEqual(h["111111"], {"qty": 100, "avail": 60, "name": "한선", "credit": 0})
        self.assertEqual(h["222222"]["avail"], 50)
        self.assertTrue(self.calls[0][0].endswith("/acnt")); self.assertEqual(self.calls[0][1]["api-id"], "kt00018")

    def test_holdings_credit_rows_merged(self):
        # ★ 11-12 같은 종목 현금·융자 두 줄 → 합산, 융자분은 credit
        self.reply = {"acnt_evlt_remn_indv_tot": [
            {"stk_cd": "A222800", "stk_nm": "*심텍", "rmnd_qty": "25", "trde_able_qty": "25", "crd_tp": "33", "crd_tp_nm": "융자"},
            {"stk_cd": "A222800", "stk_nm": "심텍", "rmnd_qty": "5", "trde_able_qty": "5", "crd_tp": "00", "crd_tp_nm": ""}]}
        h = self.api.holdings()["222800"]
        self.assertEqual((h["qty"], h["avail"], h["credit"]), (30, 30, 25))

    def test_credit_sell(self):
        # ★ 11-12 융자분은 kt10007(crdordr) 융자합 99 시장가
        self.reply = {"return_code": 0, "ord_no": "0002"}
        ok, msg = self.api.sell_market("222800", 25, credit=25)
        url, headers, body = self.calls[0]
        self.assertTrue(ok); self.assertTrue(url.endswith("/crdordr")); self.assertEqual(headers["api-id"], "kt10007")
        self.assertEqual((body["crd_deal_tp"], body["crd_loan_dt"], body["trde_tp"], body["ord_qty"]), ("99", "", "3", "25"))

    def test_sell_market(self):
        self.reply = {"return_code": 0, "ord_no": "0001"}
        ok, msg = self.api.sell_market("111111", 30)
        url, headers, body = self.calls[0]
        self.assertTrue(ok); self.assertIn("0001", msg)
        self.assertTrue(url.endswith("/ordr")); self.assertEqual(headers["api-id"], "kt10001")
        self.assertEqual((body["stk_cd"], body["ord_qty"], body["trde_tp"]), ("111111", "30", "3"))
        self.reply = {"return_code": 20, "return_msg": "매도가능수량 부족"}
        self.assertEqual(self.api.sell_market("111111", 30), (False, "매도가능수량 부족"))


if __name__ == "__main__":
    unittest.main()
