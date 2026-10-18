"""cbot — 비트 24시간(롤링) -1% 미만이면 신규 매수 쉬기 (10-17 대장 결정)."""
import unittest

from _helpers import stub, use_temp_cwd

for _n in ["websockets", "notifier", "master_db", "account_sync", "yfinance"]:
    stub(_n)
stub("jwt", encode=lambda *a, **k: "tok")
stub("notifier", Notifier=lambda **k: None)
stub("anthropic", Anthropic=lambda **k: None)
stub("backtestc")
stub("backtestc.strategy_coin", CoinStrategy=object)

_tmp = use_temp_cwd()
import cbot  # noqa: E402


class _Resp:
    def __init__(self, d): self.d = d
    def json(self): return self.d


class _Sess:
    def __init__(self, now, ago, n=145): self.now, self.ago, self.n, self.params = now, ago, n, None
    def get(self, url, params=None, timeout=None):
        self.params = params
        return _Resp([{"trade_price": self.now}] + [{"trade_price": 1.0}] * (self.n - 2) + [{"trade_price": self.ago}])


def mk(now, ago, n=145):
    b = object.__new__(cbot.CBot)
    b.session, b.btc_24h, b.btc_funding, b.notes = _Sess(now, ago, n), None, None, []
    b.notify = lambda m, **k: b.notes.append(m)
    b.market_status, b.fear_greed, b.btc_rate = "normal", 50, 0.0
    b._is_night = lambda: False
    b.get_indicators = lambda m: {"current": 110, "ma5": 105, "ma20": 100, "rsi": 55, "vol_ratio": 2.0,
                                  "candle_rate": 0.0}
    return b


class Btc24(unittest.TestCase):
    def test_update_and_notify_once(self):
        b = mk(98.5, 100.0)
        b._update_btc_24h()
        self.assertAlmostEqual(b.btc_24h, -1.5)
        self.assertEqual(b.session.params, {"market": "KRW-BTC", "count": 145})
        b._update_btc_24h()
        self.assertEqual(len(b.notes), 1)                     # 쉬기 알림은 들어갈 때 한 번만
        b.session.now = 100.5
        b._update_btc_24h()
        self.assertIn("재개", b.notes[-1])

    def test_short_or_failed_keeps_value(self):
        b = mk(90.0, 100.0, n=10); b.btc_24h = 0.5
        b._update_btc_24h()
        self.assertEqual(b.btc_24h, 0.5)

    def test_buy_gate(self):
        b = mk(0, 0); b.btc_24h = -1.2
        ok, _, why = b.check_buy_signal("KRW-A")
        self.assertFalse(ok); self.assertIn("비트 24시간", why)
        b.btc_24h, b.market_status = None, "stop"            # 조회 실패면 막지 않음 → 다음 조건(BTC 중단)으로 진행
        ok, _, why = b.check_buy_signal("KRW-A")
        self.assertIn("BTC 중단", why)
        b.btc_24h = -0.5
        self.assertIn("BTC 중단", b.check_buy_signal("KRW-A")[2])


class _FundSess:
    def __init__(self, rate): self.rate, self.params = rate, None
    def get(self, url, params=None, timeout=None):
        self.params = params
        if self.rate is None:
            raise ConnectionError("down")
        return _Resp([{"symbol": "BTCUSDT", "fundingRate": str(self.rate)}])


class Funding(unittest.TestCase):
    """10-18 대장 결정 — 바이낸스 비트 펀딩비(직전 정산) 0.003%/8h 미만이면 신규 매수 쉬기."""
    def test_update_notify_and_fail_keeps(self):
        b = mk(0, 0); b.session = _FundSess(0.00002)          # 0.002%
        b._update_btc_funding()
        self.assertAlmostEqual(b.btc_funding, 0.002)
        self.assertEqual(b.session.params, {"symbol": "BTCUSDT", "limit": 1})
        b._update_btc_funding()
        self.assertEqual(len(b.notes), 1); self.assertIn("쉬기", b.notes[0])   # 들어갈 때 한 번만
        b.session.rate = 0.0001                                # 0.01% 회복
        b._update_btc_funding()
        self.assertIn("재개", b.notes[-1])
        b.session.rate = None                                  # 조회 실패 → 기존값 유지
        b._update_btc_funding()
        self.assertAlmostEqual(b.btc_funding, 0.01)

    def test_buy_gate(self):
        b = mk(0, 0); b.btc_funding = 0.0025
        ok, _, why = b.check_buy_signal("KRW-A")
        self.assertFalse(ok); self.assertIn("펀딩비", why)
        b.btc_funding, b.market_status = None, "stop"         # 값 없으면 막지 않음
        self.assertIn("BTC 중단", b.check_buy_signal("KRW-A")[2])
        b.btc_funding = 0.004
        self.assertIn("BTC 중단", b.check_buy_signal("KRW-A")[2])


if __name__ == "__main__":
    unittest.main()
