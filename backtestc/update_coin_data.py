"""
update_coin_data.py — 코인 백테스트 시세를 매일 최신까지 이어 받기 (2026-10-18)
================================================================
[이 파일이 하는 일 — 비개발자용 설명]
업비트 공개 시세(키 불필요)로 두 DB를 마지막 저장 시점부터 지금까지 채운다.
  - coin_backtest.db  daily_ohlcv : 원화마켓 전 코인 4시간봉 (cbot 백테스터용)
  - coin_daily.db     daily       : 이미 받아둔 코인들 일봉 (dip_buy_backtest용)
아직 안 끝난(진행 중) 봉은 저장하지 않는다 — 덜 찬 봉이 박제되는 걸 막기 위해.
마지막 저장 봉은 다시 받아 덮어쓴다(예전 수집기가 진행 중 봉을 저장했을 수 있어서).
cron 매일 09:05(업비트 일봉 마감 09:00 직후). 주문·봇 영향 없음.
실행:  venv/bin/python backtestc/update_coin_data.py
"""
import os
import sys
import time
import sqlite3
import datetime

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
DB_4H = os.path.join(HERE, "coin_backtest.db")
DB_DAY = os.path.join(HERE, "coin_daily.db")
API = "https://api.upbit.com/v1"
KST = datetime.timezone(datetime.timedelta(hours=9))
S = requests.Session()


def _get(url: str, params: dict) -> list:
    for i in range(4):
        r = S.get(url, params=params, timeout=10)
        if r.status_code == 429:
            time.sleep(1 + i)
            continue
        r.raise_for_status()
        return r.json()
    r.raise_for_status()
    return []


def krw_markets() -> list:
    return [m["market"] for m in _get(f"{API}/market/all", {}) if m["market"].startswith("KRW-")]


def candles_since(market: str, unit: str, since_kst: str, span: datetime.timedelta) -> list:
    """since_kst(포함) 이후 끝난 봉만, 오래된 순. since_kst가 빈 문자열이면 최근 200개."""
    now = datetime.datetime.now(KST)
    out, to = [], None
    while True:
        params = {"market": market, "count": 200}
        if to:
            params["to"] = to
        rows = _get(f"{API}/candles/{unit}", params)
        time.sleep(0.12)
        if not rows:
            break
        for c in rows:
            start = datetime.datetime.fromisoformat(c["candle_date_time_kst"]).replace(tzinfo=KST)
            if start + span <= now and c["candle_date_time_kst"] >= since_kst:
                out.append(c)
        oldest = rows[-1]["candle_date_time_kst"]
        if not since_kst or oldest <= since_kst or len(rows) < 200:
            break
        to = rows[-1]["candle_date_time_utc"] + "Z"
    return sorted(out, key=lambda c: c["candle_date_time_kst"])


def update_4h() -> tuple:
    conn = sqlite3.connect(DB_4H, timeout=30)
    conn.execute("CREATE TABLE IF NOT EXISTS daily_ohlcv (code TEXT, date TEXT, open REAL, high REAL, "
                 "low REAL, close REAL, volume REAL)")
    # 예전 수집기(append+중복삭제) 흔적 정리 — 같은 봉은 마지막 저장분만 남기고 고유키로 고정
    conn.execute("DELETE FROM daily_ohlcv WHERE rowid NOT IN (SELECT MAX(rowid) FROM daily_ohlcv GROUP BY code, date)")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_ohlcv_code_date ON daily_ohlcv(code, date)")
    last = dict(conn.execute("SELECT code, MAX(date) FROM daily_ohlcv GROUP BY code"))
    added, fail = 0, []
    for m in krw_markets():
        try:
            rows = candles_since(m, "minutes/240", last.get(m, ""), datetime.timedelta(hours=4))
            conn.executemany("INSERT OR REPLACE INTO daily_ohlcv VALUES (?,?,?,?,?,?,?)",
                             [(m, c["candle_date_time_kst"], c["opening_price"], c["high_price"], c["low_price"],
                               c["trade_price"], c["candle_acc_trade_volume"]) for c in rows])
            conn.commit()
            added += max(0, len(rows) - (1 if m in last else 0))
        except Exception as e:
            fail.append(f"{m}:{e}")
    mx = conn.execute("SELECT MAX(date) FROM daily_ohlcv").fetchone()[0]
    conn.close()
    return added, fail, mx


def update_daily() -> tuple:
    conn = sqlite3.connect(DB_DAY, timeout=30)
    conn.execute("CREATE TABLE IF NOT EXISTS daily (code TEXT, date TEXT, open REAL, high REAL, low REAL, "
                 "close REAL, PRIMARY KEY(code, date))")
    last = dict(conn.execute("SELECT code, MAX(date) FROM daily GROUP BY code"))
    added, fail = 0, []
    for m, d in last.items():
        try:
            rows = candles_since(m, "days", d + "T00:00:00", datetime.timedelta(days=1))
            conn.executemany("INSERT OR REPLACE INTO daily VALUES (?,?,?,?,?,?)",
                             [(m, c["candle_date_time_kst"][:10], c["opening_price"], c["high_price"],
                               c["low_price"], c["trade_price"]) for c in rows])
            conn.commit()
            added += max(0, len(rows) - 1)
        except Exception as e:
            fail.append(f"{m}:{e}")
    mx = conn.execute("SELECT MAX(date) FROM daily").fetchone()[0]
    conn.close()
    return added, fail, mx


if __name__ == "__main__":
    t0 = time.time()
    print(f"⏰ {datetime.datetime.now(KST):%Y-%m-%d %H:%M} 코인 시세 업데이트")
    a, f, mx = update_4h()
    print(f"   4시간봉: 새 봉 {a:,}개 · 마지막 {mx}" + (f" · 실패 {len(f)}: {f[:3]}" if f else ""))
    a2, f2, mx2 = update_daily()
    print(f"   일봉:    새 봉 {a2:,}개 · 마지막 {mx2}" + (f" · 실패 {len(f2)}: {f2[:3]}" if f2 else ""))
    print(f"   소요 {time.time() - t0:.0f}초")
    sys.exit(1 if (f or f2) else 0)
