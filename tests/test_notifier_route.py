"""Notifier 경로 — 기본은 키키 채널, route="scan"은 리나 토큰으로 산야 채널(없으면 키키로)."""
import os
import unittest

from _helpers import use_temp_cwd

_tmp = use_temp_cwd()
import notifier as N  # noqa: E402


class Route(unittest.TestCase):
    def setUp(self):
        os.environ.update(DISCORD_BOT_TOKEN="kiki", DISCORD_CHANNEL_ID="1",
                          DISCORD_BOT_TOKEN_N="lina", LINA_SCAN_CHANNEL_ID="2")

    def test_routes(self):
        k, sc = N.Notifier("sbot"), N.Notifier("유튜브", route="scan")
        self.assertEqual((k.bot_token, k.channel), ("kiki", "1"))
        self.assertEqual((sc.bot_token, sc.channel), ("lina", "2"))

    def test_scan_falls_back_to_kiki(self):
        del os.environ["LINA_SCAN_CHANNEL_ID"]
        sc = N.Notifier("유튜브", route="scan")
        self.assertEqual((sc.bot_token, sc.channel), ("kiki", "1"))


if __name__ == "__main__":
    unittest.main()
