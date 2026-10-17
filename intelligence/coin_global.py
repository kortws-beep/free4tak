"""
coin_global.py — 해외 거래소(바이낸스) 코인 정보 + 김치 프리미엄 매일 수집 (2026-10-18)
================================================================
[이 파일이 하는 일 — 비개발자용 설명]
대장: "업비트 하나로 판단하기는 걸린다." 가격은 거래소끼리 거의 같이 움직이니, 업비트에서 못 보는
'다른 종류' 정보를 backtestc/coin_global.db에 쌓는다(키 불필요 공개 API, 주문 없음).
  · funding   — 바이낸스 선물 펀딩비(8시간마다). 롱(상승 베팅) 과열이면 +로 커짐. 1년치 이상 과거도 받음
  · oi        — 미결제약정(1시간). 레버리지가 얼마나 쌓였나          ┐ 바이낸스가 최근 30일만 줌 →
  · ls_global — 전체 계정 롱/숏 비율(1시간)                          │ 매일 모아야 나중에 검증 가능
  · ls_top    — 상위 트레이더 포지션 롱/숏 비율(1시간)               │
  · taker     — 시장가 매수/매도 체결량 비율(1시간)                  ┘
  · kimp      — 김치 프리미엄(1시간): 업비트 비트 ÷ (바이낸스 비트 × 환율) - 1, 테더 프리미엄(업비트 USDT ÷ 환율 - 1).
                환율은 FDR USD/KRW 일별 종가(그날 것 없으면 직전 날)
대상: 비트·이더·솔·리플(USDT 무기한 선물). 시각은 모두 한국시간, 끝난 봉만 저장. 마지막 저장 시점부터 이어 받음.
cron 매일 09:10. 실행:  venv/bin/python intelligence/coin_global.py
"""
import os
import sys
import time
import sqlite3
import datetime
from bisect import bisect_right

import requests

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(BASE, "backtestc", "coin_global.db")
SYMS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]
BACKFILL_DAYS = 400
KST = datetime.timezone(datetime.timedelta(hours=9))
S = requests.Session()

SCHEMA = """
CREATE TABLE IF NOT EXISTS funding   (symbol TEXT, ts TEXT, rate REAL, PRIMARY KEY(symbol, ts));
CREATE TABLE IF NOT EXISTS oi        (symbol TEXT, ts TEXT, oi REAL, oi_value REAL, PRIMARY KEY(symbol, ts));
CREATE TABLE IF NOT EXISTS ls_global (symbol TEXT, ts TEXT, long_ratio REAL, ls_ratio REAL, PRIMARY KEY(symbol, ts));
CREATE TABLE IF NOT EXISTS ls_top    (symbol TEXT, ts TEXT, long_ratio REAL, ls_ratio REAL, PRIMARY KEY(symbol, ts));
CREATE TABLE IF NOT EXISTS taker     (symbol TEXT, ts TEXT, buy_vol REAL, sell_vol REAL, ratio REAL, PRIMARY KEY(symbol, ts));
CREATE TABLE IF NOT EXISTS kimp      (ts TEXT PRIMARY KEY, upbit_btc REAL, binance_btc REAL, usdkrw REAL,
                                      premium REAL, upbit_usdt REAL, usdt_premium REAL);
"""


def _get(url: str, params: dict):
    for i in range(4):
        r = S.get(url, params=params, timeout=15)
        if r.status_code in (418, 429):
            time.sleep(2 + 2 * i)
            continue
        r.raise_for_status()
        return r.json()
    r.raise_for_status()


def kst(ms: int) -> str:
    return datetime.datetime.fromtimestamp(ms / 1000, KST).replace(tzinfo=None).isoformat(timespec="seconds")


def ms(ts: str) -> int:
    return int(datetime.datetime.fromisoformat(ts).replace(tzinfo=KST).timestamp() * 1000)


def now_ms() -> int:
    return int(time.time() * 1000)


def last_ts(conn, table: str, symbol: str = None):
    q = f"SELECT MAX(ts) FROM {table}" + (" WHERE symbol=?" if symbol else "")
    return conn.execute(q, (symbol,) if symbol else ()).fetchone()[0]


def update_funding(conn) -> int:
    n = 0
    for s in SYMS:
        lt = last_ts(conn, "funding", s)
        start = ms(lt) + 1 if lt else now_ms() - BACKFILL_DAYS * 86_400_000
        while True:
            rows = _get("https://fapi.binance.com/fapi/v1/fundingRate", {"symbol": s, "startTime": start, "limit": 1000})
            if not rows:
                break
            conn.executemany("INSERT OR REPLACE INTO funding VALUES (?,?,?)",
                             [(s, kst(r["fundingTime"]), float(r["fundingRate"])) for r in rows])
            n += len(rows)
            if len(rows) < 1000:
                break
            start = rows[-1]["fundingTime"] + 1
            time.sleep(0.3)
        conn.commit()
    return n


FUT_DATA = {  # table: (endpoint, row → values)
    "oi": ("openInterestHist", lambda r: (float(r["sumOpenInterest"]), float(r["sumOpenInterestValue"]))),
    "ls_global": ("globalLongShortAccountRatio", lambda r: (float(r["longAccount"]), float(r["longShortRatio"]))),
    "ls_top": ("topLongShortPositionRatio", lambda r: (float(r["longAccount"]), float(r["longShortRatio"]))),
    "taker": ("takerlongshortRatio", lambda r: (float(r["buyVol"]), float(r["sellVol"]), float(r["buySellRatio"]))),
}


def update_fut_data(conn) -> int:
    n, floor = 0, now_ms() - 29 * 86_400_000          # 바이낸스가 30일보다 오래된 시작점은 거부
    for table, (ep, conv) in FUT_DATA.items():
        for s in SYMS:
            lt = last_ts(conn, table, s)
            start = max(floor, ms(lt) + 1 if lt else 0)
            while start < now_ms() - 3_600_000:
                # 시작 시각만 주면 '가장 최근 500개'를 돌려줌 → 끝 시각을 같이 줘야 앞에서부터 이어짐
                p = {"symbol": s, "period": "1h", "limit": 500, "startTime": start,
                     "endTime": min(start + 499 * 3_600_000, now_ms())}
                rows = _get(f"https://fapi.binance.com/futures/data/{ep}", p)
                rows = [r for r in rows or [] if start <= r["timestamp"] and r["timestamp"] + 3_600_000 <= now_ms()]
                if not rows:
                    break
                ph = ",".join("?" * (2 + len(conv(rows[0]))))
                conn.executemany(f"INSERT OR REPLACE INTO {table} VALUES ({ph})",
                                 [(s, kst(r["timestamp"]), *conv(r)) for r in rows])
                n += len(rows)
                start = rows[-1]["timestamp"] + 1
                time.sleep(0.3)
            conn.commit()
    return n


def upbit_1h(market: str, since: str) -> dict:
    out, to, now = {}, None, datetime.datetime.now()
    while True:
        p = {"market": market, "count": 200}
        if to:
            p["to"] = to
        rows = _get("https://api.upbit.com/v1/candles/minutes/60", p)
        time.sleep(0.12)
        if not rows:
            break
        for c in rows:
            t = datetime.datetime.fromisoformat(c["candle_date_time_kst"])
            if t + datetime.timedelta(hours=1) <= now:
                out[c["candle_date_time_kst"]] = c["trade_price"]
        if rows[-1]["candle_date_time_kst"] <= since:
            break
        to = rows[-1]["candle_date_time_utc"] + "Z"
    return {k: v for k, v in out.items() if k >= since}


def binance_1h(since: str) -> dict:
    out, start = {}, ms(since)
    while True:
        rows = _get("https://api.binance.com/api/v3/klines",
                    {"symbol": "BTCUSDT", "interval": "1h", "startTime": start, "limit": 1000})
        rows = [r for r in rows or [] if r[6] < now_ms()]
        if not rows:
            break
        out.update({kst(r[0]): float(r[4]) for r in rows})
        if len(rows) < 1000:
            break
        start = rows[-1][0] + 1
        time.sleep(0.3)
    return out


def fx_series(since: str) -> tuple:
    import FinanceDataReader as fdr
    d0 = (datetime.date.fromisoformat(since[:10]) - datetime.timedelta(days=10)).isoformat()
    s = fdr.DataReader("USD/KRW", d0)["Close"].dropna()
    return [d.strftime("%Y-%m-%d") for d in s.index], [float(v) for v in s.values]


def update_kimp(conn) -> int:
    lt = last_ts(conn, "kimp")
    since = lt or (datetime.datetime.now() - datetime.timedelta(days=BACKFILL_DAYS)).replace(
        minute=0, second=0, microsecond=0).isoformat(timespec="seconds")
    up, usdt, bn = upbit_1h("KRW-BTC", since), upbit_1h("KRW-USDT", since), binance_1h(since)
    fd, fv = fx_series(since)
    rows = []
    for t in sorted(set(up) & set(bn)):
        i = bisect_right(fd, t[:10]) - 1          # 그날 환율, 없으면 직전 날(주말·휴일)
        if i < 0:
            continue
        fx = fv[i]
        u = usdt.get(t)
        rows.append((t, up[t], bn[t], fx, (up[t] / (bn[t] * fx) - 1) * 100,
                     u, None if u is None else (u / fx - 1) * 100))
    conn.executemany("INSERT OR REPLACE INTO kimp VALUES (?,?,?,?,?,?,?)", rows)
    conn.commit()
    return len(rows)


if __name__ == "__main__":
    t0 = time.time()
    conn = sqlite3.connect(DB, timeout=30)
    conn.executescript(SCHEMA)
    print(f"⏰ {datetime.datetime.now():%Y-%m-%d %H:%M} 해외 코인 정보 수집")
    fail = 0
    for name, fn in (("펀딩비", update_funding), ("선물 지표(OI·롱숏·체결)", update_fut_data), ("김치 프리미엄", update_kimp)):
        try:
            print(f"   {name}: {fn(conn):,}행")
        except Exception as e:
            fail += 1
            print(f"   ⚠️ {name} 실패: {e}")
    for t in ("funding", "oi", "ls_global", "ls_top", "taker", "kimp"):
        mn, mx, c = conn.execute(f"SELECT MIN(ts), MAX(ts), COUNT(*) FROM {t}").fetchone()
        print(f"   {t:<10} {mn} ~ {mx} ({c:,}행)")
    k = conn.execute("SELECT ts, premium, usdt_premium FROM kimp ORDER BY ts DESC LIMIT 1").fetchone()
    if k:
        print(f"   지금 김프 {k[1]:+.2f}% · 테더 프리미엄 {k[2] if k[2] is None else round(k[2], 2)}% ({k[0]})")
    conn.close()
    print(f"   소요 {time.time() - t0:.0f}초")
    sys.exit(1 if fail else 0)
