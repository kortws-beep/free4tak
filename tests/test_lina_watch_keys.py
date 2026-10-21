"""리나등록 키 — 같은 종목 키움·데이봇 따로, 옛 데이봇 키 옮기기."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lina_bot"))
import ast  # noqa: E402

SRC = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lina_bot", "lina_bot.py"),
           encoding="utf-8").read()


def _load(names):
    """lina_bot.py는 디스코드 등 무거운 의존성이 있어 필요한 함수만 떼어 실행."""
    tree = ast.parse(SRC)
    ns = {}
    code = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    exec(compile(ast.Module(body=code, type_ignores=[]), "lina_bot", "exec"), ns)
    return ns


class Keys(unittest.TestCase):
    def test_keys(self):
        ns = _load({"_wkey", "_wcode", "_load_manual_watches"})
        self.assertEqual(ns["_wkey"]("005930"), "005930")
        self.assertEqual(ns["_wkey"]("005930", "daybot"), "005930@d")
        self.assertEqual(ns["_wcode"]("005930@d", {}), "005930")
        self.assertEqual(ns["_wcode"]("x@d", {"code": "005930"}), "005930")

    def test_split_account(self):
        ns = _load({"_split_account"})
        ns["_ACCT_TOKENS"] = {"k": "kiwoom", "키움": "kiwoom", "d": "daybot", "데이봇": "daybot", "daybot": "daybot", "한투": "daybot"}
        self.assertEqual(ns["_split_account"](["226176", "17주", "K"]), ("kiwoom", ["226176", "17주"]))
        self.assertEqual(ns["_split_account"](["225875", "8주", "d"]), ("daybot", ["225875", "8주"]))
        self.assertEqual(ns["_split_account"](["225875", "8주"]), (None, ["225875", "8주"]))

    def test_parse_scalp_args(self):
        ns = _load({"parse_scalp_args"})
        import re
        ns["re"] = re
        self.assertEqual(ns["parse_scalp_args"](["10주"]), (10, None, None))
        self.assertEqual(ns["parse_scalp_args"](["136,400", "1,000주"]), (1000, 136400.0, None))
        self.assertEqual(ns["parse_scalp_args"]([]), (None, None, None))
        self.assertEqual(ns["parse_scalp_args"](["100만"]), (None, None, 1000000))          # ★ 10-21 금액
        self.assertEqual(ns["parse_scalp_args"](["150만원"]), (None, None, 1500000))
        self.assertEqual(ns["parse_scalp_args"](["1,000,000원"]), (None, None, 1000000))

    def test_scalp_order_amount(self):
        ns = _load({"scalp_order_amount"})
        f = ns["scalp_order_amount"]
        amt = f(10, None, 243000, 500)                      # 지정가 243,500
        self.assertEqual(int(amt / (243500 * 1.00015)), 10)  # KisAPI.buy()와 같은 계산 → 정확히 10주
        self.assertEqual(f(None, 1000000, 243000, 500), 1000000)

    def test_migrate_legacy(self):
        ns = _load({"_load_manual_watches"})
        store = {"005930": {"account": "daybot", "qty": 8}, "003160": {"account": "kiwoom"}}
        saved = []
        import types
        cu = types.ModuleType("common_utils")
        cu.read_state = lambda path, default=None: dict(store)
        sys.modules["common_utils"] = cu
        ns["MANUAL_WATCH_STATE_FILE"] = "x"
        ns["_save_manual_watches"] = lambda w: saved.append(dict(w))
        w = ns["_load_manual_watches"]()
        self.assertEqual(set(w), {"005930@d", "003160"})
        self.assertEqual(w["005930@d"]["code"], "005930"); self.assertEqual(len(saved), 1)


if __name__ == "__main__":
    unittest.main()
