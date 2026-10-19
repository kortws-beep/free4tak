"""daybot 하락장 쉬기(10-19 대장 결정) — 전날 국면 35% 미만이면 신규매수 안 함, 국면 모르면 막지 않음."""
import unittest

from _helpers import stub, use_temp_cwd

for _n in ["kis_websocket", "kiwoom_api", "notifier", "master_db", "sector_monitor"]:
    stub(_n)
stub("kis_websocket", KisWebSocket=object)
stub("kiwoom_api", KiwoomAPI=object)
stub("notifier", Notifier=object)
stub("kis_api", KisAPI=object)

_tmp = use_temp_cwd()
import daybot  # noqa: E402


class _Note:
    def __init__(self): self.msgs = []
    def send(self, m, critical=False): self.msgs.append(m)


class DownMarket(unittest.TestCase):
    def test_threshold_and_fail_open(self):
        f = daybot.DayBot._down_market
        self.assertTrue(f(24.4))
        self.assertFalse(f(35.0))
        self.assertFalse(f(None))                      # 국면 못 구하면 막지 않음
        old, daybot.PAUSE_BELOW_PCT = daybot.PAUSE_BELOW_PCT, 0
        try:
            self.assertFalse(f(10))                    # 0이면 끔
        finally:
            daybot.PAUSE_BELOW_PCT = old

    def test_morning_message(self):
        b = object.__new__(daybot.DayBot)
        b._regime, b._scan_notifier = (None, None), _Note()
        daybot.market_regime.latest = lambda: ("2026-10-16", 24.4)
        self.assertEqual(b._market_breadth(), 24.4)
        self.assertIn("하락장", b._scan_notifier.msgs[0]); self.assertIn("신규매수 쉬기", b._scan_notifier.msgs[0])
        b._regime = (None, None)
        daybot.market_regime.latest = lambda: ("2026-10-16", 42.0)
        b._market_breadth()
        self.assertIn("약한 장", b._scan_notifier.msgs[-1])


if __name__ == "__main__":
    unittest.main()
