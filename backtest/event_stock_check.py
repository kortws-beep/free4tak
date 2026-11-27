"""
event_stock_check.py — 이벤트 미리 사기를 '종목' 단위로 (2026-11-28, sbot 이벤트 슬롯 사전 검증)
================================================================
ETF로 통과한 짝(CES 반도체 등)이 sbot이 실제로 사는 '관심그룹 종목'에서도 나오나.
  · 종목 = 지금 한투 관심그룹(예: 반도체_ 7개 그룹) 멤버 — ⚠️ 지금 기준 목록이라 살아남은 종목 쏠림(사후 선택) 있음
  · 해마다: 그룹 종목 평균(동일 비중) 초과수익(KODEX200 대비), 종목별 이긴 비율, 그해 상위·하위
  · 가짜 날짜 대조(event_sector_validate와 같은 방식)
일봉 캐시: backtest/data/etf_daily.db(같은 daily 테이블). 실행: venv/bin/python backtest/event_stock_check.py [--event CES] [--prefix 반도체_]
"""
import argparse
import bisect
import datetime
import json
import os
import sqlite3
import statistics as st
import sys
import time

_H = os.path.dirname(os.path.abspath(__file__))
_B = os.path.dirname(_H)
sys.path[:0] = [_H, os.path.join(_B, "intelligence"), os.path.join(_B, "core")]
import event_calendar as E          # noqa: E402
import event_sector_validate as V   # noqa: E402


def group_codes(prefix):
    import kr_heatmap
    with open(kr_heatmap.GROUP_CACHE, encoding="utf-8") as f:
        g = json.load(f)["groups"]
    return {c: nm for n, v in g.items() if n.startswith(prefix) for c, nm in v}


def load_codes(codes):
    c = sqlite3.connect(V.CACHE)
    c.execute("CREATE TABLE IF NOT EXISTS daily (code TEXT, date TEXT, close REAL, PRIMARY KEY(code, date))")
    out = {}
    for code in codes:
        rows = c.execute("SELECT date, close FROM daily WHERE code=? ORDER BY date", (code,)).fetchall()
        if not rows or rows[-1][0] < (datetime.date.today() - datetime.timedelta(days=5)).isoformat():
            try:
                rows = V.fetch(code)
                c.executemany("INSERT OR REPLACE INTO daily VALUES (?,?,?)", [(code, d, v) for d, v in rows])
                c.commit()
                time.sleep(0.15)
            except Exception as e:
                print(f"⚠️ {code} 실패: {e}")
        out[code] = rows
    c.close()
    return out


def basket(data, bench, codes, dates, b, s):
    """[(year, 평균 초과, 종목수, 이긴 종목 비율)]"""
    out = []
    for d in dates:
        ex = []
        for code in codes:
            ser = data.get(code) or []
            ds = [x for x, _ in ser]
            i = bisect.bisect_left(ds, d.isoformat())
            if i - b < 0 or i >= len(ds) or (len(ds) > i and ds[i][:7] > d.isoformat()[:7] and False):
                continue
            a, z = ds[i - b], ds[i - s]
            if a in bench and z in bench and abs((datetime.date.fromisoformat(ds[i]) - d).days) <= 7:
                px = dict(ser)
                ex.append((px[z] / px[a] - 1) * 100 - (bench[z] / bench[a] - 1) * 100)
        if len(ex) >= 5:
            out.append((d.year, st.mean(ex), len(ex), sum(x > 0 for x in ex) / len(ex) * 100))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--event", default="CES")
    ap.add_argument("--prefix", default="반도체_")
    a = ap.parse_args()
    codes = group_codes(a.prefix)
    data = load_codes(list(codes))
    bench = dict(V.load()[V.BENCH])
    today = datetime.date.today()
    dates = [d for d in E.events(2015, today.year)[a.event] if d < today - datetime.timedelta(days=10)]
    print(f"{a.event} × '{a.prefix}' 관심그룹 {len(codes)}종목 (지금 목록 기준 — 사후 선택 쏠림 있음)")
    for b, s in V.HOLDS:
        rs = basket(data, bench, codes, dates, b, s)
        m = st.mean(r[1] for r in rs)
        fake = [st.mean(r[1] for r in f) for f in (basket(data, bench, codes, [d + datetime.timedelta(days=k) for d in dates], b, s)
                                                     for k in V.SHIFTS[::2]) if len(f) >= 4]
        pct = sum(m > f for f in fake) / len(fake) * 100
        print(f"\n[{b}일 전 매수 → {s}일 전 매도] 그룹 평균 {m:+.2f}%p · 이긴 해 {sum(r[1] > 0 for r in rs)}/{len(rs)} · 가짜 날짜보다 좋은 비율 {pct:.0f}%")
        for y, mm, n, w in rs:
            print(f"   {y}: 평균 {mm:+6.2f}%p · {n}종목 중 {w:3.0f}% 이김")


if __name__ == "__main__":
    main()
