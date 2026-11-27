"""
event_sector_validate.py — 연례 이벤트 전후 섹터 초과수익 검증 (2026-11-28, 주말 숙제 3번 '방패 정밀 설계')
================================================================
[이 파일이 하는 일 — 비개발자용 설명]
"블프 전엔 화장품·의류가, CES 전엔 로봇·IT가, 암학회 전엔 바이오가 미리 오르고, 당일·직후엔 셀온" 같은
대장 경험칙이 숫자로도 맞는지 본다. 우리 일봉 DB는 2년뿐(이벤트당 2번)이라, 섹터 ETF 10년치(네이버)를 쓴다.
  · 기준 = KODEX 200 대비 초과수익(%p) — 시장 전체 흐름을 뺀 '그 섹터만의 움직임'
  · 창 3개: 전(이벤트 10거래일 전→전날) · 당일(전날→이벤트 뒤 첫 거래일) · 후(그날→5거래일 뒤)
  · 이벤트 날짜 = intelligence/event_calendar.py. 한국 시간 기준 첫 거래일로 맞춤(미국 행사는 다음 한국장 반응).
  · 통과 기준(표시 ★): 표본 6번↑, 같은 방향 70%↑, 평균 1%p↑ — 그래도 해마다 1번이라 '참고 패턴'이지 규칙 아님.
일봉 캐시: backtest/data/etf_daily.db (없으면 네이버 siseJson에서 받음).
실행: venv/bin/python backtest/event_sector_validate.py [--refresh] [--event 블랙프라이데이]
"""
import argparse
import ast
import bisect
import datetime
import os
import sqlite3
import statistics as st
import sys
import time

import requests

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "intelligence"))
import event_calendar  # noqa: E402

CACHE = os.path.join(BASE, "backtest", "data", "etf_daily.db")
BENCH = "069500"
SECTORS = {   # 코드: 이름 (ETF는 2015~, 로봇 ETF는 2022-11~ · 방패 개별주 포함)
    "228790": "화장품", "244580": "바이오", "143860": "헬스케어", "228800": "여행레저", "228810": "미디어컨텐츠",
    "266360": "K콘텐츠", "300610": "게임", "091170": "은행", "102970": "증권", "140700": "보험", "091160": "반도체",
    "305720": "2차전지", "266410": "필수소비재", "266390": "경기소비재", "117680": "철강", "140710": "운송",
    "157490": "소프트웨어", "139260": "IT", "117460": "에너지화학", "445290": "로봇(22~)",
    "033780": "KT&G", "032640": "LG유플러스", "030200": "KT", "111770": "영원무역", "161890": "한국콜마",
    "090430": "아모레퍼시픽", "036570": "엔씨소프트", "035900": "JYP",
}
PRE, POST = 10, 5


def fetch(code: str) -> list:
    url = (f"https://api.finance.naver.com/siseJson.naver?symbol={code}&requestType=1&startTime=20150101"
           f"&endTime={datetime.date.today():%Y%m%d}&timeframe=day")
    txt = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"}).text.strip()
    rows = ast.literal_eval(txt.replace("\n", "").replace("\t", ""))[1:]
    return [(r[0][:4] + "-" + r[0][4:6] + "-" + r[0][6:], float(r[4])) for r in rows if r[4]]


def load(refresh=False) -> dict:
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    c = sqlite3.connect(CACHE)
    c.execute("CREATE TABLE IF NOT EXISTS daily (code TEXT, date TEXT, close REAL, PRIMARY KEY(code, date))")
    out = {}
    for code in [BENCH] + list(SECTORS):
        rows = c.execute("SELECT date, close FROM daily WHERE code=? ORDER BY date", (code,)).fetchall()
        if refresh or not rows or rows[-1][0] < (datetime.date.today() - datetime.timedelta(days=5)).isoformat():
            try:
                new = fetch(code)
                c.executemany("INSERT OR REPLACE INTO daily VALUES (?,?,?)", [(code, d, v) for d, v in new])
                c.commit()
                rows = new
                time.sleep(0.2)
            except Exception as e:
                print(f"⚠️ {code} 받기 실패: {e}")
        out[code] = rows
    c.close()
    return out


def windows(series: list, bench: dict, date: datetime.date, ev: str):
    """(전, 당일, 후) 초과수익 %p — 데이터 모자라면 None."""
    ds = [d for d, _ in series]
    i = bisect.bisect_left(ds, date.isoformat())
    if ev == "연말배당":                       # 12월 마지막 거래일을 '이벤트 날'로(배당 기준일 무렵)
        i -= 1
    if i - PRE - 1 < 0 or i + POST >= len(ds) or i < 1:
        return None
    px = dict(series)

    def ex(a, b):
        da, db = ds[a], ds[b]
        if da not in bench or db not in bench:
            return None
        return (px[db] / px[da] - 1) * 100 - (bench[db] / bench[da] - 1) * 100
    return ex(i - PRE - 1, i - 1), ex(i - 1, i), ex(i, i + POST)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--event")
    a = ap.parse_args()
    data = load(a.refresh)
    bench = dict(data[BENCH])
    evs = event_calendar.events(2015, datetime.date.today().year)
    today = datetime.date.today()
    hits = []
    for ev, dates in evs.items():
        if a.event and ev != a.event:
            continue
        dates = [d for d in dates if d < today - datetime.timedelta(days=10)]
        print(f"\n=== {ev} ({len(dates)}회: {dates[0].year}~{dates[-1].year}) — KODEX200 대비 %p, (같은 방향 비율) ===")
        print(f"{'섹터':12s} {'n':>3s} | {'전 10일':>16s} | {'당일':>16s} | {'후 5일':>16s}")
        for code, name in SECTORS.items():
            w = [x for x in (windows(data.get(code) or [], bench, d, ev) for d in dates) if x]
            if len(w) < 4:
                continue
            cols = []
            for k in range(3):
                v = [x[k] for x in w if x[k] is not None]
                m = st.mean(v)
                agree = sum((x > 0) == (m > 0) for x in v) / len(v) * 100
                star = "★" if len(v) >= 6 and agree >= 70 and abs(m) >= 1.0 else " "
                cols.append(f"{m:+6.2f} ({agree:3.0f}%){star}")
                if star == "★":
                    hits.append((ev, ("전", "당일", "후")[k], name, m, agree, len(v)))
            print(f"{name:12s} {len(w):3d} | " + " | ".join(cols))
    print("\n=== ★ 통과(표본 6↑ · 같은 방향 70%↑ · 평균 1%p↑) ===")
    for ev, win, name, m, agree, n in sorted(hits, key=lambda h: (h[0], h[1], -abs(h[3]))):
        print(f"{ev:8s} {win:3s} {name:12s} {m:+6.2f}%p  같은 방향 {agree:3.0f}%  n={n}")


if __name__ == "__main__":
    main()
