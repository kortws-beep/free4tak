"""
sbot_candidate_review.py — sbot 후보 전체의 '그 뒤' 성적 + 주도 테마 안/밖 비교 (2026-11-28 주말 숙제 1번)
================================================================
[이 파일이 하는 일 — 비개발자용 설명]
하락장 1슬롯이라 sbot이 실제로 산 건 2주에 3건뿐 → 산 것만으론 판단 불가. 대신 sbot이 '사려고 했던 후보'
(candidate_log, 11-16~)를 전부 모아서 "그 가격에 샀다면 1·3·5일 뒤 얼마였나"를 본다.
그리고 대장 질문(11-24 동국제약 — 로봇·반도체 급등일에 유튜브 소스로 제약주): 후보가 나온 그 시각에
그 종목이 '그 시각 상위 10개 테마'(theme_open_obs, 15분마다) 안에 있었나 → 주도 테마 안/밖 성적 비교.
산 뒤 정보는 안 씀(테마 순위는 후보 시각 이전 마지막 기록, 수익은 후보 가격 기준).
  · 진입가 = 후보 기록 가격 · d0 = 그날 종가, d1/d3/d5/d10 = 1·3·5·10거래일 뒤 종가 · hit5 = 5거래일 안에 고가 +5%,
    hit10 = 10거래일 안에 고가 +10%(★ 11-28 대장 "횡보·상승장에선 유튜브 소스가 바로 또는 1~2주 뒤 오름" — 늦게 오르는 것도 잡으려고)
  · 같은 날 같은 종목은 1건(매수 판단 buy > 관찰 observe > 점수 scored 순으로 대표)
실행: venv/bin/python backtest/sbot_candidate_review.py [--top 10]
"""
import argparse
import os
import re
import sqlite3
import statistics as st
from collections import defaultdict

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SBOT_DB = os.path.join(BASE, "sbot_trade_history.db")
KR_DB = os.path.join(BASE, "lina_bot", "kr_theme_finance.db")
LOG_DB = os.path.join(BASE, "lina_bot", "three_month_leader_log.db")
MODE_PRI = {"buy": 0, "observe": 1, "scored": 2}


def ro(path):
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def candidates():
    best = {}
    for ts, code, name, grade, score, price, mode in ro(SBOT_DB).execute(
            "SELECT ts, code, stock_name, grade, score, price, mode FROM candidate_log WHERE price > 0 ORDER BY ts"):
        key = (ts[:10], code)
        cur = best.get(key)
        if cur is None or MODE_PRI.get(mode, 9) < MODE_PRI.get(cur["mode"], 9):
            best[key] = dict(ts=ts, date=ts[:10], hhmm=ts[11:13] + ts[14:16], code=code, name=(name or "").strip(),
                             grade=grade, score=score or 0, price=float(price), mode=mode)
    return list(best.values())


def daily(names):
    c = ro(KR_DB)
    out = {}
    q = ",".join("?" * len(names))
    for n, d, cl, hi in c.execute(f"SELECT stock_name, date, close_price, high_price FROM kr_stock_daily_data "
                                  f"WHERE stock_name IN ({q}) AND date >= '2026-11-01' ORDER BY date", list(names)):
        if cl:
            out.setdefault(n, []).append((d, float(cl), float(hi or cl)))
    return out


def theme_members():
    c = ro(KR_DB)
    m = defaultdict(set)
    for th, sn in c.execute("SELECT theme_name, stock_name FROM kr_theme_stocks"):
        m[re.sub(r"(KOSPI|KOSDAQ|KONEX).*$", "", sn).strip()].add(th)
    return m


def top_themes(top):
    """{date: [(hhmm, set(top themes))]} — 정규장 기록만(프리장 08:xx 제외)."""
    out = defaultdict(list)
    for d, t, th in ro(LOG_DB).execute("SELECT date, time, theme FROM theme_open_obs WHERE rank <= ? AND time >= '0900' "
                                       "ORDER BY date, time", (top,)):
        if out[d] and out[d][-1][0] == t:
            out[d][-1][1].add(th)
        else:
            out[d].append((t, {th}))
    return out


def lead_at(tt, date, hhmm):
    """후보 시각 이전 마지막 기록의 상위 테마(09:15 전 후보는 09:15 기록 — 첫 기록, 약간의 선견 있음)."""
    snaps = tt.get(date) or []
    prev = [s for t, s in snaps if t <= hhmm]
    return prev[-1] if prev else (snaps[0][1] if snaps else None)


def fwd(rows, date, price):
    ds = [r[0] for r in rows]
    if date not in ds:
        return None
    i = ds.index(date)
    g = lambda k: (rows[i + k][1] / price - 1) * 100 if i + k < len(rows) else None
    hi = max(r[2] for r in rows[i:i + 6])
    hi10 = max(r[2] for r in rows[i:i + 11])
    return dict(d0=g(0), d1=g(1), d3=g(3), d5=g(5), d10=g(10), hit5=hi >= price * 1.05,
                hit10=hi10 >= price * 1.10, days=len(rows) - i - 1)


def summ(xs, k):
    v = [x[k] for x in xs if x.get(k) is not None]
    if not v:
        return "     -          "
    return f"{st.mean(v):+6.2f}% ({sum(a > 0 for a in v) / len(v) * 100:3.0f}%↑)"


def table(title, groups):
    print(f"\n=== {title} ===")
    print(f"{'구분':22s} {'건수':>4s} | {'당일종가':>16s} | {'1일뒤':>16s} | {'3일뒤':>16s} | {'5일뒤':>16s} | {'10일뒤':>16s} | +5%찍음 | 10일내+10%")
    for name, xs in groups:
        if not xs:
            continue
        h = sum(x["hit5"] for x in xs) / len(xs) * 100
        h10 = sum(x["hit10"] for x in xs) / len(xs) * 100
        print(f"{name:22s} {len(xs):4d} | {summ(xs, 'd0')} | {summ(xs, 'd1')} | {summ(xs, 'd3')} | {summ(xs, 'd5')} | {summ(xs, 'd10')} | {h:6.0f}% | {h10:6.0f}%")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=10)
    a = ap.parse_args()
    cs = candidates()
    dd = daily({c["name"] for c in cs if c["name"]})
    mem = theme_members()
    tt = top_themes(a.top)
    rows = []
    for c in cs:
        f = fwd(dd.get(c["name"], []), c["date"], c["price"])
        if not f:
            continue
        top = lead_at(tt, c["date"], c["hhmm"])
        mine = mem.get(c["name"], set())
        c.update(f, lead=None if top is None else bool(mine & top), themes=sorted(mine & top) if top else [],
                 has_theme=bool(mine))
        rows.append(c)
    print(f"후보 {len(cs)}건 중 일봉 맞춘 {len(rows)}건 · 날짜 {min(r['date'] for r in rows)}~{max(r['date'] for r in rows)} · 주도 = 그 시각 상위 {a.top} 테마")
    real = [r for r in rows if r["mode"] != "scored"]
    table("① 소스별 (매수 판단·관찰 후보)", [(g, [r for r in real if r["grade"] == g]) for g in sorted({r["grade"] for r in real})]
          + [("(풀 점수만 매긴 후보)", [r for r in rows if r["mode"] == "scored"])])
    table("② 주도 테마 안/밖 (매수 판단·관찰 후보)", [
        ("주도 테마 안", [r for r in real if r["lead"]]),
        ("주도 테마 밖(테마 있음)", [r for r in real if r["lead"] is False and r["has_theme"]]),
        ("테마 DB에 없음", [r for r in real if not r["has_theme"]])])
    table("③ 주도 테마 안/밖 (풀 전체 — 점수만 매긴 후보 포함)", [
        ("주도 테마 안", [r for r in rows if r["lead"]]),
        ("주도 테마 밖", [r for r in rows if r["lead"] is False])])
    table("④ 소스 × 주도 (매수 판단·관찰)", [(f"{g} {'안' if l else '밖'}", [r for r in real if r["grade"] == g and bool(r["lead"]) == l])
                                       for g in sorted({r["grade"] for r in real}) for l in (True, False)])
    table("⑤ 점수대 (매수 판단·관찰)", [(n, [r for r in real if lo <= r["score"] < hi]) for n, lo, hi in
                                    (("~59", 0, 60), ("60~79", 60, 80), ("80~94", 80, 95), ("95~", 95, 999))])
    print("\n=== ⑥ 실제 매수·눈여겨볼 사례 ===")
    for r in rows:
        if r["name"].startswith(("동국제약", "HK이노엔", "DB")) and r["mode"] == "buy":
            print(f"{r['ts'][:16]} {r['name']} {r['grade']} {r['score']:.0f}점 @{r['price']:,.0f} · 주도 {'안 ' + ','.join(r['themes'][:2]) if r['lead'] else '밖'}"
                  f" · 당일 {r['d0']:+.1f}% 1일 {r['d1'] if r['d1'] is None else round(r['d1'], 1)} 3일 {r['d3'] if r['d3'] is None else round(r['d3'], 1)}")


if __name__ == "__main__":
    main()
