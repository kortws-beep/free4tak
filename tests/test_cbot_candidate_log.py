"""11-28 cbot 후보 기록(candidate_log) — 테이블 생성·한 줄 기록·실패해도 예외 안 냄."""
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
import cbot  # noqa: E402


class CandLog(unittest.TestCase):
    def test_log(self):
        b = object.__new__(cbot.CBot)
        b._init_trade_db()
        b.fear_greed, b.btc_rate, b.btc_24h, b.btc_funding, b.market_status = 12, 0.5, -0.3, 0.004, "normal"
        b._log_candidate("KRW-MOCA", {"current": 100.0, "rsi": 61.0, "vol_ratio": 1.8}, 72, 3.0, 75.0, 60, "buy")
        b._log_candidate("KRW-X", None, 40, 0, 40, 60, "avoid")
        rows = sqlite3.connect(cbot.TRADE_HIST_DB).execute(
            "SELECT market, price, ai_raw, ai_final, fear_greed, decision FROM candidate_log ORDER BY id").fetchall()
        self.assertEqual(rows, [("KRW-MOCA", 100.0, 72, 75.0, 12, "buy"), ("KRW-X", None, 40, 40.0, 12, "avoid")])

    def test_log_never_raises(self):
        b = object.__new__(cbot.CBot)       # 속성 없음 → 내부 예외, 밖으로 안 나와야
        b._log_candidate("KRW-Y", {}, 1, 0, 1, 60, "ai_low")


if __name__ == "__main__":
    unittest.main()
