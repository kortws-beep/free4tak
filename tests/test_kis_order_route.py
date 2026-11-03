"""11-04 한투 주문 신TR·거래소 칸 — 프리장 NXT 지정가, 정규장 KRX(신TR 거부 시 구TR), 08:50~09:00 보류."""
import json
import unittest

from _helpers import stub

stub("kis_websocket", KisWebSocket=object)
import kis_api  # noqa: E402


class Route(unittest.TestCase):
    def setUp(self):
        self._old = (kis_api._post, kis_api.KisAPI.__dict__["_now_hhmm"])

    def _api(self, hhmm, nx=23300.0, reject_new=False):
        a = object.__new__(kis_api.KisAPI)
        a.base_url, a.cano, a.acnt, a.token, a.appkey, a.secret = "u", "c", "01", "t", "k", "s"
        a.get_hashkey = lambda d: "h"
        a.get_multi_price = lambda codes, pause=0, market="J": {codes[0]: {"price": nx}}
        a.get_market_data = lambda code: {"stck_prpr": "23000"}
        a.sent = []

        def post(url, headers=None, data=None, timeout=10):
            body = json.loads(data)
            a.sent.append((headers["tr_id"], body))
            ok = not (reject_new and headers["tr_id"].startswith("TTTC00"))
            return type("R", (), {"json": lambda self: {"rt_cd": "0" if ok else "1", "msg1": "x", "output": {}}})()
        kis_api._post = post
        kis_api.KisAPI._now_hhmm = staticmethod(lambda: hhmm)
        return a

    def tearDown(self):
        kis_api._post, kis_api.KisAPI._now_hhmm = self._old

    def test_exchange(self):
        f = kis_api.KisAPI.order_exchange
        self.assertEqual((f("0759"), f("0800"), f("0849"), f("0855"), f("0900"), f("1529"), f("1530"), f("1959"), f("2000")),
                         ("", "NXT", "NXT", "", "KRX", "KRX", "NXT", "NXT", ""))

    def test_premarket_sell_nxt(self):
        a = self._api("0825")
        self.assertTrue(a.sell("011200", 1, price=23300))
        tr, body = a.sent[0]
        self.assertEqual((tr, body["EXCG_ID_DVSN_CD"], body["ORD_DVSN"], body["ORD_UNPR"]), ("TTTC0011U", "NXT", "00", "23200"))
        a = self._api("0825", nx=0)                       # NXT에 없는 종목 → 보류(주문 안 냄)
        self.assertFalse(a.sell("253590", 1, price=15000)); self.assertEqual(a.sent, [])
        a = self._api("1700")                           # ★ 애프터장도 NXT 지정가
        self.assertTrue(a.sell("011200", 2, price=0))
        self.assertEqual((a.sent[0][0], a.sent[0][1]["EXCG_ID_DVSN_CD"], a.sent[0][1]["ORD_DVSN"]), ("TTTC0011U", "NXT", "00"))
        a = self._api("0855")
        self.assertFalse(a.sell("011200", 1)); self.assertEqual(a.sent, [])

    def test_regular_fallback_old_tr(self):
        a = self._api("1000")
        self.assertTrue(a.sell("011200", 3))
        self.assertEqual([(t, b.get("EXCG_ID_DVSN_CD"), b["ORD_DVSN"]) for t, b in a.sent], [("TTTC0011U", "KRX", "01")])
        a = self._api("1000", reject_new=True)
        self.assertTrue(a.sell("011200", 3))
        self.assertEqual(a.sent[1][0], "TTTC0801U"); self.assertNotIn("EXCG_ID_DVSN_CD", a.sent[1][1])
        self.assertNotIn("SLL_TYPE", a.sent[1][1])

    def test_buy_and_cancel(self):
        a = self._api("0830")
        ok = a.buy("011200", 23300, 100000, psbl_cash=100000)
        self.assertTrue(ok[0]); self.assertEqual((a.sent[0][0], a.sent[0][1]["EXCG_ID_DVSN_CD"]), ("TTTC0012U", "NXT"))
        self.assertTrue(a.cancel_order("91257", "1", "011200", 1))
        self.assertEqual((a.sent[1][0], a.sent[1][1]["EXCG_ID_DVSN_CD"]), ("TTTC0013U", "NXT"))


if __name__ == "__main__":
    unittest.main()
