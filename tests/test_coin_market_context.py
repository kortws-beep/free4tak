"""코인 시장판단 재료 — 업비트 경보 파싱(신·구 형식), 전체 흐름, RSS, AI 한 줄."""
import datetime as dt
import types
import unittest

from _helpers import use_temp_cwd

_tmp = use_temp_cwd()
import coin_market_context as C  # noqa: E402

RSS = """<rss><channel>
<item><title>Bitcoin plunges after tariff remarks</title><pubDate>{new}</pubDate></item>
<item><title>Old news</title><pubDate>{old}</pubDate></item>
</channel></rss>"""


class Ctx(unittest.TestCase):
    def test_flags_both_formats(self):
        f = C.parse_market_flags([
            {"market": "KRW-A", "market_warning": "CAUTION"},
            {"market": "KRW-B", "market_event": {"warning": False,
                                                  "caution": {"PRICE_FLUCTUATIONS": True, "TRADING_VOLUME_SOARING": False}}},
            {"market": "BTC-X", "market_warning": "CAUTION"},
        ])
        self.assertEqual(f, {"KRW-A": {"warning": True, "cautions": []},
                             "KRW-B": {"warning": False, "cautions": ["가격급등락"]}})

    def test_breadth(self):
        b = C.breadth({"A": 100, "B": 100, "C": 100}, {"A": 103, "B": 99, "C": 101})
        self.assertEqual((round(b["median"], 6), round(b["up_pct"]), b["n"]), (1.0, 67, 3))
        self.assertEqual(C.breadth({}, {})["n"], 0)

    def test_rss_since(self):
        now = dt.datetime(2026, 10, 7, 12, 0, tzinfo=dt.timezone.utc)
        fmt = lambda d: d.strftime("%a, %d %b %Y %H:%M:%S +0000")
        xml = RSS.format(new=fmt(now - dt.timedelta(hours=1)), old=fmt(now - dt.timedelta(hours=30)))
        items = C.parse_rss(xml, "CT", dt.datetime(2026, 10, 7, 6, 0))
        self.assertEqual([t for _, t, _ in items], ["Bitcoin plunges after tariff remarks"])
        self.assertEqual(C.parse_rss("not xml", "CT", dt.datetime(2026, 1, 1)), [])

    def test_ai_judgement(self):
        llm = types.SimpleNamespace(messages=types.SimpleNamespace(
            create=lambda **k: types.SimpleNamespace(content=[types.SimpleNamespace(text="이벤트성 순간 급락으로 보임\n추가")])))
        self.assertEqual(C.ai_judgement(llm, "m", "BTC -3%", []), "이벤트성 순간 급락으로 보임")
        self.assertEqual(C.ai_judgement(None, "m", "x", []), "")


if __name__ == "__main__":
    unittest.main()
