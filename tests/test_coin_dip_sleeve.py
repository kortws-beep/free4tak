"""cbot 눌림목 주머니 — 진입/익절/기한/재진입대기 판단, 실매매 흐름, 본체와 분리."""
import datetime
import json
import sqlite3
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
import coin_dip_sleeve as D  # noqa: E402
import cbot  # noqa: E402

cbot._master_record = cbot._master_upsert = cbot._master_remove = None


def rows(n_up=210, last_close=100.0, ref_high=100.0, today=("2026-10-08", 95, 96, 88, 89)):
    """완만히 오르는 210일(200일선 아래로 안 감) + 최근 7일 고가 ref_high + 오늘 봉."""
    base = datetime.date(2026, 10, 8) - datetime.timedelta(days=n_up)
    out = []
    for i in range(n_up):
        d = (base + datetime.timedelta(days=i)).isoformat()
        c = 50 + i * 0.2 if i < n_up - 7 else last_close
        out.append((d, c, ref_high if i >= n_up - 7 else c, c, c))
    return out + [today]


class Decide(unittest.TestCase):
    def test_context_and_buy(self):
        ctx = D.daily_context(rows())
        self.assertEqual(ctx["ref"], 100.0); self.assertAlmostEqual(ctx["trig"], 90.0)
        self.assertTrue(ctx["trend_ok"])
        st = {}
        self.assertEqual(D.decide(st, ctx, 91), "")
        self.assertEqual(D.decide(st, ctx, 89.9), "buy")

    def test_trend_filter_blocks(self):
        ctx = D.daily_context(rows(last_close=40.0))      # 어제 종가가 200일선 아래
        self.assertFalse(ctx["trend_ok"])
        self.assertEqual(D.decide({}, ctx, 80), "")

    def test_short_history_none(self):
        self.assertIsNone(D.daily_context(rows()[-100:]))

    def test_tp_and_expire(self):
        ctx = D.daily_context(rows())
        st = {"held": True, "entry": 90.0, "entry_day": "2026-10-01"}
        self.assertEqual(D.decide(st, ctx, 97.1), "")
        self.assertEqual(D.decide(st, ctx, 97.2), "tp")
        st["entry_day"] = "2026-09-18"                    # 20일째 → 아직
        self.assertEqual(D.decide(st, ctx, 91), "")
        st["entry_day"] = "2026-09-17"                    # 21일째 09시 = 20일째 종가 무렵
        self.assertEqual(D.decide(st, ctx, 91), "expire")

    def test_wait_new_high_then_no_buy_that_day(self):
        ctx = D.daily_context(rows(today=("2026-10-08", 95, 96, 88, 89)))
        st = {"blocked": True, "exit_day": "2026-10-08"}
        self.assertEqual(D.decide(st, ctx, 89), "")       # 판 날은 해제 판단도 안 함
        st["exit_day"] = "2026-10-05"
        self.assertEqual(D.decide(st, ctx, 89), "")
        self.assertTrue(st["blocked"])                    # 새 고가 아직
        ctx_hi = D.daily_context(rows(today=("2026-10-08", 95, 101, 88, 89)))
        self.assertEqual(D.decide(st, ctx_hi, 89), "")    # 해제됐지만 해제한 날은 안 삼
        self.assertFalse(st["blocked"]); self.assertEqual(st["no_buy_day"], "2026-10-08")
        nxt = dict(ctx_hi, day="2026-10-09")
        self.assertEqual(D.decide(st, nxt, 89), "buy")


class _Resp:
    def __init__(self, d): self.d = d
    def json(self): return self.d


class FakeBot:
    def __init__(self, prices, balances):
        self.prices, self.balances, self.orders, self.notes = prices, balances, [], []
        bot = self

        class S:
            def post(self, url, headers=None, json=None, timeout=None):
                bot.orders.append(json); return _Resp({"uuid": "u"})
        self.session = S()

    def _get_headers(self, q=None): return {}
    def get_balances(self): return self.balances
    def get_current_price(self, ms): return {m: self.prices[m] for m in ms if m in self.prices}
    def notify(self, m, critical=False): self.notes.append(m)


def sleeve(bot, saved=None):
    store = {"dip": saved}
    s = D.DipSleeve(bot, "trade.db", lambda: store["dip"], lambda st: store.update(dip=json.loads(json.dumps(st))))
    r = rows()
    s._candles = {m: (9e18, r) for m in D.DIP_COINS}   # 캐시 고정(네트워크 없음)
    return s, store


class Step(unittest.TestCase):
    def test_buy_skip_when_cbot_holds_and_paused(self):
        bot = FakeBot({m: 89.0 for m in D.DIP_COINS}, {})
        s, store = sleeve(bot)
        self.assertEqual(s.reserved_krw(), 1_000_000)
        acted = s.step({"KRW-BTC": {"qty": 1000, "current": 89}}, krw=600_000, allow_buy=True)
        self.assertTrue(acted)
        bought = [o["market"] for o in bot.orders]
        self.assertEqual(bought, ["KRW-ETH", "KRW-XRP"])   # BTC=본체 보유, SOL=잔고 부족(60만→25만×2)
        self.assertEqual(s.held(), {"KRW-ETH", "KRW-XRP"})
        self.assertEqual(s.reserved_krw(), 500_000)
        self.assertTrue(store["dip"]["KRW-ETH"]["held"])
        bot2 = FakeBot({m: 89.0 for m in D.DIP_COINS}, {})
        s2, _ = sleeve(bot2)
        self.assertFalse(s2.step({}, krw=2_000_000, allow_buy=False)); self.assertEqual(bot2.orders, [])

    def test_take_profit_sells_full_balance_and_logs(self):
        bot = FakeBot({"KRW-ETH": 98.0}, {"ETH": {"balance": 2500.0, "avg_buy_price": 90.0}})
        saved = {"KRW-ETH": {"held": True, "entry": 89.0, "qty": 2500.0, "entry_day": "2026-10-02", "row": None}}
        s, store = sleeve(bot, saved)
        s._db("INSERT INTO dip_trades (market, buy_price, qty) VALUES (?,?,?)", ("KRW-ETH", 90.0, 2500.0))
        s.state["KRW-ETH"]["row"] = 1
        self.assertTrue(s.step({}, krw=0))
        self.assertEqual(bot.orders[-1], {"market": "KRW-ETH", "side": "ask", "volume": "2500.00000000",
                                          "ord_type": "market"})
        self.assertEqual(store["dip"]["KRW-ETH"], {"blocked": True, "exit_day": "2026-10-08"})
        r = sqlite3.connect("trade.db").execute(
            "SELECT sell_reason, round(profit_rate, 2), hold_days FROM dip_trades").fetchone()
        self.assertEqual(r, ("익절+8%", round((98 / 90 - 1 - 0.001) * 100, 2), 6))   # 평균단가는 잔고 기준

    def test_external_sell_detected(self):
        bot = FakeBot({"KRW-SOL": 95.0}, {})
        s, store = sleeve(bot, {"KRW-SOL": {"held": True, "entry": 90.0, "qty": 1.0,
                                            "entry_day": "2026-10-05", "buy_ts": 0}})
        s.step({}, krw=0, allow_buy=False)
        self.assertEqual(bot.orders, [])
        self.assertTrue(store["dip"]["KRW-SOL"]["blocked"])
        self.assertIn("외부매도", bot.notes[-1])


class CbotSeparation(unittest.TestCase):
    def test_get_current_positions_excludes_dip_coins(self):
        b = object.__new__(cbot.CBot)
        b._dip = type("X", (), {"held": lambda self: {"KRW-BTC"}})()
        b.get_balances = lambda: {"KRW": {"balance": 1e6, "avg_buy_price": 0},
                                  "BTC": {"balance": 0.01, "avg_buy_price": 1e8},
                                  "ETH": {"balance": 0.1, "avg_buy_price": 5e6}}
        b.get_current_price = lambda ms: {m: 1 for m in ms}
        self.assertEqual(list(b.get_current_positions()), ["KRW-ETH"])

    def test_disabled_reserves_nothing(self):
        s = D.DipSleeve(FakeBot({}, {}), "trade.db", lambda: None, lambda st: None, enabled=False)
        self.assertEqual(s.reserved_krw(), 0)
        self.assertFalse(s.step({}, 1e7))


if __name__ == "__main__":
    unittest.main()
