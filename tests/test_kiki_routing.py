"""키키 — 명령 라우팅(!분석오늘/!매도 자동판별/!t재시작 차단), 권한, daybot 상태 표시."""
import json
import os
import asyncio
import types
import unittest

os.environ["KIKI_ALLOWED_USER_IDS"] = "111"
from _helpers import stub, use_temp_cwd

_d = stub("discord", DMChannel=type("DM", (), {}))
_d.Intents = type("I", (), {"default": staticmethod(lambda: types.SimpleNamespace(message_content=False))})
_ext = stub("discord.ext")
_cmds = stub("discord.ext.commands")


class _Bot:
    def __init__(self, *a, **k): self.user = "kiki"
    def event(self, f): return f


_cmds.Bot = _Bot; _ext.commands = _cmds; _d.ext = _ext
stub("anthropic", Anthropic=lambda **k: None)
stub("performance", PerformanceAnalyzer=object, MultiPerformanceAnalyzer=object)

_tmp = use_temp_cwd()
import kiki_briefing  # noqa: E402
kiki_briefing._base = os.getcwd()
import kiki      # noqa: E402
import kiki_cmd  # noqa: E402


def W(name, data):
    json.dump(data, open(os.path.join(os.getcwd(), name), "w"))


class Ctx:
    def __init__(self): self.out = []
    async def send(self, m): self.out.append(m)


class KikiRouting(unittest.TestCase):
    def setUp(self):
        W("daybot_state.json", {"positions": {"0035S0": {"entry_price": 5000, "qty": 10, "buy_tag": "tier1_overlap"}},
                                "code_name_map": {"0035S0": "빅웨이브로보틱스"}})
        W("sbot_state.json", {"last_status": {"positions_detail": {"005930": {"rate": 1}},
                                              "code_name_map": {"005930": "삼성전자"}}})
        kiki_cmd.send_long = lambda ctx, t: ctx.send(t)

    def run_async(self, coro):
        return asyncio.run(coro)

    def test_allowlist_loaded(self):
        self.assertEqual(kiki.ALLOWED_USER_IDS, {111})

    def test_analysis_aliases_reach_handlers(self):
        called = []
        async def today(ctx): called.append("today")
        async def period(ctx, days=7): called.append(("period", days))
        kiki.cmd_analyze_today, kiki.cmd_analyze_period = today, period
        for c in ["!분석오늘", "!오늘분석", "!분석이번주", "!이번주분석"]:
            self.run_async(kiki.execute_command(Ctx(), c))
        self.assertEqual(called, ["today", "today", ("period", 7), ("period", 7)])

    def test_sell_auto_detects_bot(self):
        sent = []
        async def fake_sell(ctx, code, bot): sent.append((code, bot))
        orig = kiki_cmd.cmd_sell; kiki_cmd.cmd_sell = fake_sell
        try:
            self.run_async(kiki.execute_command(Ctx(), "!매도 빅웨이브로보틱스"))
            self.run_async(kiki.execute_command(Ctx(), "!매도 삼성전자"))
            c = Ctx(); self.run_async(kiki.execute_command(c, "!매도 없는종목"))
        finally:
            kiki_cmd.cmd_sell = orig
        self.assertEqual(sent, [("0035S0", "daybot"), ("005930", "sbot")])
        self.assertIn("어디에도", c.out[-1])

    def test_telegram_restart_refused(self):
        c = Ctx(); self.run_async(kiki.execute_command(c, "!t재시작"))
        self.assertIn("폐기", c.out[-1])
        self.assertNotIn("telegram", kiki_cmd.RESTART_SERVICES)

    def test_daybot_status_from_state(self):
        c = Ctx(); self.run_async(kiki_cmd.cmd_status(c, "daybot"))
        self.assertIn("빅웨이브로보틱스", c.out[-1])

    def test_status_without_state_file(self):
        os.remove("sbot_state.json")
        self.run_async(kiki_cmd.cmd_status(Ctx(), "sbot"))

    def test_wait_cmd_result_timeout_is_empty(self):
        orig = kiki_cmd.read_state; kiki_cmd.read_state = lambda b: {}
        try:
            self.assertEqual(self.run_async(kiki_cmd.wait_cmd_result("sbot", max_attempts=1, interval=0)), "")
        finally:
            kiki_cmd.read_state = orig

    def test_stranger_ignored(self):
        executed = []
        async def ex(ctx, cmd): executed.append(cmd)
        async def gc(m): return Ctx()
        orig_ex, orig_ch = kiki.execute_command, kiki.CHANNEL_ID
        kiki.execute_command, kiki.bot.get_context, kiki.CHANNEL_ID = ex, gc, 5
        try:
            def msg(uid, text):
                return types.SimpleNamespace(author=types.SimpleNamespace(id=uid, bot=False),
                                             channel=types.SimpleNamespace(id=5), content=text)
            self.run_async(kiki.on_message(msg(999, "!c전체매도")))
            self.assertEqual(executed, [])
            self.run_async(kiki.on_message(msg(111, "!c전체매도")))
            self.assertEqual(executed, ["!c전체매도"])
        finally:
            kiki.execute_command, kiki.CHANNEL_ID = orig_ex, orig_ch


if __name__ == "__main__":
    unittest.main()
