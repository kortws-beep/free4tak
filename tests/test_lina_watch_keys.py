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
