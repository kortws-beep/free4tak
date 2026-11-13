"""
buy_feature_analysis.py — 봇이 산 종목의 '매수 시점 특성' 분석 (2026-11-14, 대장 "숫자로 나와야만 해")
================================================================
[이 파일이 하는 일 — 비개발자용 설명]
봇(sbot·daybot·옛 sbo2)이 실제로 사고판 기록을 모아서, 각 매수 건마다 '산 날 전날까지' 보였던 모습을 숫자로 만든다
(산 뒤의 정보는 절대 안 씀 — 미래를 보고 고르는 착시 방지):
  · 추격률     : 매수가가 전날 종가보다 몇 % 위였나(당일 이미 얼마나 올라 있었나)
  · 5·20·60일 수익률 : 사기 전 1주·1달·3달 동안 얼마나 올랐/빠졌나
  · 20일선 거리 : 전날 종가가 20일 평균선에서 얼마나 떨어져 있었나
  · 60일 고점 대비 : 3달 고점에서 몇 % 내려와 있었나
  · 거래량 배수 : 전날 거래량이 20일 평균의 몇 배였나
  · 거래대금   : 20일 평균 거래대금(억) — 종목 덩치·유동성
  · 시장 국면   : 그날 20일선 위 종목 비율(%)
그리고 특성마다 구간을 나눠 '구간별 건수·승률·평균 수익률'을 표로 보여준다.
→ 여기서 차이가 크게 나는 특성만 '필터 후보'로 골라 백테스트로 다시 검증(통과한 것만 실전 적용).
손절 건은 '팔고 나서 5·10거래일 뒤 매수가를 회복했나'도 같이 봄(손절 직후 반등 패턴 — 11-09 디아이 사례).

일봉은 backtest_data.db에 있으면 그걸, 없으면 네이버에서 받아 backtest/data/feature_ohlcv.db에 캐시.
실행: venv/bin/python backtest/buy_feature_analysis.py [--bot sbot|daybot|sbo2] [--since 2026-06-01] [--csv out.csv]
"""
import argparse
import csv
import datetime
import os
import sqlite3
import sys
import time

import requests

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BT_DB = os.path.join(BASE, "backtest", "data", "backtest_data.db")
CACHE_DB = os.path.join(BASE, "backtest", "data", "feature_ohlcv.db")
REGIME_DB = os.path.join(BASE, "lina_bot", "three_month_leader_log.db")
SOURCES = {
    "sbot": (os.path.join(BASE, "sbot_trade_history.db"), "trades", "buy_tag"),
    "daybot": (os.path.join(BASE, "daybot_trade_history.db"), "trades", "buy_tag"),
    "sbo2": (os.path.join(BASE, "lina_bot", "sbo2_trades.db"), "sbo2_trades", "grade"),
}
SKIP_TAGS = {"수동"}          # 대장이 직접 산 걸 봇이 입양한 건 — 봇의 선택이 아니라 제외


def load_trades(bot: str, since: str) -> list:
    path, table, tagcol = SOURCES[bot]
    c = sqlite3.connect(path)
    rows = c.execute(f"""SELECT code, stock_name, buy_price, buy_time, sell_price, sell_time, profit_rate, sell_reason,
                                COALESCE({tagcol}, '') FROM {table}
                         WHERE sell_price IS NOT NULL AND sell_price > 0 AND buy_price > 0
                           AND buy_time IS NOT NULL AND buy_time != '' AND substr(buy_time,1,10) >= ?
                         ORDER BY buy_time""", (since,)).fetchall()
    out = []
    for code, name, bp, bt, sp, st, rate, reason, tag in rows:
        if str(tag) in SKIP_TAGS:
            continue
        rate = rate if rate is not None else (sp / bp - 1) * 100
        out.append(dict(bot=bot, code=code, name=name or code, buy_price=float(bp), buy_date=str(bt)[:10].replace("T", " ")[:10],
                        sell_date=str(st)[:10], rate=float(rate), reason=reason or "", tag=str(tag)))
    return out


# ── 일봉 ────────────────────────────────────────────────────
def _cache():
    c = sqlite3.connect(CACHE_DB)
    c.execute("CREATE TABLE IF NOT EXISTS ohlcv (code TEXT, date TEXT, open REAL, high REAL, low REAL, close REAL, "
              "volume REAL, PRIMARY KEY(code, date))")
    c.execute("CREATE TABLE IF NOT EXISTS fetched (code TEXT PRIMARY KEY, upto TEXT)")
    return c


def _naver(code: str, start: str, end: str) -> list:
    url = (f"https://api.stock.naver.com/chart/domestic/item/{code}/day?"
           f"startDateTime={start.replace('-', '')}0000&endDateTime={end.replace('-', '')}0000")
    d = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10).json()
    out = []
    for x in d or []:
        ld = x.get("localDate", "")
        out.append((f"{ld[:4]}-{ld[4:6]}-{ld[6:]}", x.get("openPrice"), x.get("highPrice"), x.get("lowPrice"),
                    x.get("closePrice"), x.get("accumulatedTradingVolume")))
    return out


def bars(code: str, need_until: str, cache) -> list:
    """[(date, o, h, l, c, v)] 오름차순. 캐시 → 없으면 네이버(2025-12-01 ~ 오늘)."""
    row = cache.execute("SELECT upto FROM fetched WHERE code=?", (code,)).fetchone()
    if not row or row[0] < need_until:
        try:
            got = _naver(code, "2025-12-01", (datetime.date.today() + datetime.timedelta(days=1)).isoformat())
            cache.executemany("INSERT OR REPLACE INTO ohlcv VALUES (?,?,?,?,?,?,?)", [(code, *g) for g in got])
            cache.execute("INSERT OR REPLACE INTO fetched VALUES (?,?)", (code, datetime.date.today().isoformat()))
            cache.commit()
            time.sleep(0.15)
        except Exception as e:
            print(f"⚠️ {code} 일봉 받기 실패: {e}", file=sys.stderr)
    return cache.execute("SELECT date, open, high, low, close, volume FROM ohlcv WHERE code=? ORDER BY date",
                         (code,)).fetchall()


def regime_map() -> dict:
    try:
        c = sqlite3.connect(REGIME_DB)
        t = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        for name in t:
            cols = [r[1] for r in c.execute(f"PRAGMA table_info({name})")]
            if "breadth" in cols and "date" in cols:
                return {d: b for d, b in c.execute(f"SELECT date, breadth FROM {name}")}
    except Exception:
        pass
    return {}


# ── 특성 ────────────────────────────────────────────────────
def features(tr: dict, b: list, regime: dict) -> dict:
    dates = [x[0] for x in b]
    prev = [x for x in b if x[0] < tr["buy_date"]]          # 매수 전날까지만
    if len(prev) < 21:
        return {}
    closes = [x[4] for x in prev]
    vols = [x[5] or 0 for x in prev]
    pc = closes[-1]

    def ret(n):
        return (pc / closes[-1 - n] - 1) * 100 if len(closes) > n else None

    ma20 = sum(closes[-20:]) / 20
    hi60 = max(x[2] for x in prev[-60:])
    v20 = sum(vols[-21:-1]) / 20 if len(vols) >= 21 else None
    val20 = sum(x[4] * (x[5] or 0) for x in prev[-20:]) / 20 / 1e8
    f = dict(chase=(tr["buy_price"] / pc - 1) * 100, r5=ret(5), r20=ret(20), r60=ret(60),
             ma20_gap=(pc / ma20 - 1) * 100, off_hi60=(pc / hi60 - 1) * 100,
             vol_x=(vols[-1] / v20) if v20 else None, value20=val20,
             regime=regime.get(prev[-1][0]))
    # 손절 뒤 회복: 매도일 이후 5·10거래일 안에 종가가 매수가 이상이었나
    after = [x for x in b if x[0] > tr["sell_date"]]
    for n in (5, 10):
        seg = after[:n]
        f[f"recover{n}"] = (max(x[4] for x in seg) >= tr["buy_price"]) if len(seg) >= n else None
    return f


BUCKETS = {
    "chase": [-99, 0, 3, 7, 15, 99], "r5": [-99, -10, -3, 3, 10, 99], "r20": [-99, -15, -5, 5, 20, 999],
    "r60": [-99, -20, 0, 30, 80, 9999], "ma20_gap": [-99, -10, -3, 3, 10, 99], "off_hi60": [-99, -40, -25, -10, -3, 1],
    "vol_x": [0, 0.7, 1.5, 3, 999], "value20": [0, 50, 200, 1000, 99999], "regime": [0, 25, 35, 50, 101],
}
NAMES = {"chase": "추격률(매수가/전날종가)", "r5": "5일 수익률", "r20": "20일 수익률", "r60": "60일 수익률",
         "ma20_gap": "20일선 거리", "off_hi60": "60일 고점 대비", "vol_x": "거래량 배수(전날/20일평균)",
         "value20": "20일 평균 거래대금(억)", "regime": "시장 국면(%)"}


def table(rows: list, key: str) -> list:
    edges = BUCKETS[key]
    out = []
    for lo, hi in zip(edges, edges[1:]):
        g = [r for r in rows if r.get(key) is not None and lo <= r[key] < hi]
        if not g:
            continue
        w = sum(r["rate"] > 0 for r in g)
        out.append((f"{lo:g}~{hi:g}", len(g), w / len(g) * 100, sum(r["rate"] for r in g) / len(g)))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bot", choices=list(SOURCES), action="append")
    ap.add_argument("--since", default="2026-01-01")
    ap.add_argument("--csv")
    a = ap.parse_args()
    bots = a.bot or ["sbot", "daybot", "sbo2"]
    cache, regime = _cache(), regime_map()
    rows = []
    for bot in bots:
        trs = load_trades(bot, a.since)
        for tr in trs:
            f = features(tr, bars(tr["code"], tr["sell_date"], cache), regime)
            if f:
                rows.append({**tr, **f})
        print(f"[{bot}] 매매 {len(trs)}건 중 특성 계산 {sum(r['bot'] == bot for r in rows)}건")
    if a.csv:
        with open(a.csv, "w", newline="", encoding="utf-8") as fo:
            w = csv.DictWriter(fo, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        print(f"📄 {a.csv}")
    for bot in bots:
        g = [r for r in rows if r["bot"] == bot]
        if not g:
            continue
        w = sum(r["rate"] > 0 for r in g)
        print(f"\n━━━ {bot}: {len(g)}건 · 승률 {w / len(g) * 100:.0f}% · 평균 {sum(r['rate'] for r in g) / len(g):+.2f}% ━━━")
        for key in BUCKETS:
            t = table(g, key)
            if len(t) < 2:
                continue
            print(f"  [{NAMES[key]}]")
            for rng, n, wr, avg in t:
                print(f"     {rng:>12}  {n:>3}건  승률 {wr:>4.0f}%  평균 {avg:+6.2f}%")
        stops = [r for r in g if r["rate"] < 0 and r.get("recover5") is not None]
        if stops:
            r5 = sum(bool(r["recover5"]) for r in stops)
            r10 = [r for r in stops if r.get("recover10") is not None]
            print(f"  [손실 매도 뒤 회복] {len(stops)}건 중 5거래일 안에 매수가 회복 {r5}건({r5 / len(stops) * 100:.0f}%)"
                  + (f" · 10거래일 {sum(bool(r['recover10']) for r in r10)}/{len(r10)}건" if r10 else ""))


if __name__ == "__main__":
    main()
