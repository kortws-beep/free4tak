"""
premarket_sector_backtest.py — 프리장 섹터 대장~3등주, 본장 시가가 프리장 가격에서 얼마나 벗어나도 되나 (2026-10-26, 대장 질문)
================================================================
[이 파일이 하는 일 — 비개발자용 설명]
대장: "오늘의 섹터 중 대장주~3등주가 본장(시가·고가·저가·종가)에서 프리장 가격 대비 몇 %까지 허용해야 본장에서 상승 유지하나?"
  날마다: 프리장 마지막 가격(NXT 08:50 전, premarket_nxt_collect.py가 모은 것)으로 테마 순위(NXT 종목 3개 이상 테마, 평균 등락)
          → 상위 N_THEMES 테마의 프리장 등락 1~3위(오른 종목만) = '오늘의 섹터 대장~3등주'
  그 종목들의 본장(20:10 수집 일봉): 갭 = 본장 시가 ÷ 프리장 가격 - 1  →  갭 구간별로
     상승 유지(종가 > 시가) 비율 · 시가→종가 평균 · 시가 대비 +2.5% 닿은 비율(daybot 수익 시작선) · 시가 대비 최고/최저 평균 · 종가 > 프리장가 비율
  앞 절반·뒷 절반 기간을 따로 — 둘 다 같은 방향이어야 믿음.
⚠️ 테마 구성은 '지금' 테마 DB · NXT 종목도 '지금' 목록(예전엔 NXT 종목이 적었음). 일봉이라 장중 순서(먼저 +2.5% 닿았나)는 모름.
실행: venv/bin/python backtest/premarket_sector_backtest.py [테마수=5] [프리장최소대금(억)=1]
"""
import os
import sys
import sqlite3

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(BASE, "intelligence")]
PRE_DB = os.path.join(BASE, "backtest", "data", "nxt_premarket.db")
DAILY_DB = os.path.join(BASE, "lina_bot", "kr_theme_finance.db")
MIN_MEMBERS, PICKS = 3, 3
BUCKETS = [(-99, -5), (-5, -3), (-3, -1), (-1, 1), (1, 3), (3, 5), (5, 99)]


def bucket(gap: float) -> tuple:
    return next(b for b in BUCKETS if b[0] <= gap < b[1])


def day_picks(themes: dict, pre_chg: dict, n_themes: int) -> list:
    """{테마: [(코드, 이름)]}, {코드: 프리장 등락%} → [(코드, 이름, 테마순위, 종목순위)] — 종목 중복은 처음 것만."""
    rows = []
    for th, ms in themes.items():
        q = [(c, n, pre_chg[c]) for c, n in ms if c in pre_chg]
        if len(q) >= MIN_MEMBERS:
            rows.append((sum(x[2] for x in q) / len(q), th, q))
    rows.sort(key=lambda r: -r[0])
    out, seen = [], set()
    for tr, (_, th, q) in enumerate(rows[:n_themes], 1):
        for sr, (c, n, ch) in enumerate(sorted((x for x in q if x[2] > 0), key=lambda x: -x[2])[:PICKS], 1):
            if c not in seen:
                seen.add(c)
                out.append((c, n, tr, sr))
    return out


def outcome(pre_price: float, o: float, h: float, lo: float, c: float) -> dict:
    return {"gap": (o / pre_price - 1) * 100, "up": c > o, "oc": (c / o - 1) * 100, "hit": h >= o * 1.025,
            "hi": (h / o - 1) * 100, "lo": (lo / o - 1) * 100, "above_pre": c > pre_price}


def stats(xs: list) -> dict:
    n = len(xs)
    if not n:
        return {"n": 0}
    avg = lambda k: sum(x[k] for x in xs) / n  # noqa: E731
    pct = lambda k: sum(1 for x in xs if x[k]) / n * 100  # noqa: E731
    return {"n": n, "up": pct("up"), "oc": avg("oc"), "hit": pct("hit"), "hi": avg("hi"), "lo": avg("lo"),
            "above_pre": pct("above_pre")}


def load(n_themes: int, min_value: float):
    import theme_open_obs as T
    themes = T.load_themes()
    name = {c: n for ms in themes.values() for c, n in ms}
    pc = sqlite3.connect(PRE_DB)
    pre = {}
    for d, c, p, v in pc.execute("SELECT date, code, price, value FROM pre WHERE price > 0"):
        if (v or 0) >= min_value:
            pre.setdefault(d, {})[c] = p
    pc.close()
    dc = sqlite3.connect(DAILY_DB)
    daily = {}
    for nm, d, o, h, lo, c in dc.execute("SELECT stock_name, date, open_price, high_price, low_price, close_price "
                                         "FROM kr_stock_daily_data WHERE date >= '2025-10-15' ORDER BY date"):
        daily.setdefault(nm, []).append((d, o, h, lo, c))
    dc.close()
    res = []
    for d in sorted(pre):
        chg, bars = {}, {}
        for c, p in pre[d].items():
            rows = daily.get(name.get(c), [])
            i = next((k for k, r in enumerate(rows) if r[0] == d), None)
            if i is None or i == 0 or not rows[i - 1][4] or not all(rows[i][1:]):
                continue
            chg[c] = (p / rows[i - 1][4] - 1) * 100
            bars[c] = rows[i]
        for c, nm, tr, sr in day_picks(themes, chg, n_themes):
            _, o, h, lo, cl = bars[c]
            res.append({"date": d, "name": nm, "theme_rank": tr, "stock_rank": sr, "pre_chg": chg[c],
                        **outcome(pre[d][c], o, h, lo, cl)})
    return res


def _line(label: str, s: dict) -> str:
    if not s["n"]:
        return f"   {label:<14}   0건"
    return (f"   {label:<14}{s['n']:>5}건 · 상승유지 {s['up']:3.0f}% · 시가→종가 {s['oc']:+5.1f}% · +2.5% 닿음 {s['hit']:3.0f}%"
            f" · 최고 {s['hi']:+5.1f}% · 최저 {s['lo']:+5.1f}% · 종가>프리장 {s['above_pre']:3.0f}%")


def main():
    nums = [float(a) for a in sys.argv[1:] if a.replace(".", "").isdigit()]
    n_themes = int(nums[0]) if nums else 5
    min_value = (nums[1] if len(nums) > 1 else 1) * 1e8
    res = load(n_themes, min_value)
    if not res:
        print("데이터 없음 — premarket_nxt_collect.py 먼저")
        return
    dates = sorted({r["date"] for r in res})
    mid = dates[len(dates) // 2]
    print(f"🌅 프리장 상위 {n_themes}테마 대장~3등주 {len(res)}건 · {len(dates)}일({dates[0]}~{dates[-1]}) · "
          f"프리장 대금 {min_value / 1e8:g}억↑ · 앞/뒤 경계 {mid}")
    print("\n① 본장 시가가 프리장 가격 대비 (갭 구간별)")
    for lo, hi in BUCKETS:
        xs = [r for r in res if lo <= r["gap"] < hi]
        lab = f"{'~' if lo == -99 else f'{lo:+d}%'}~{'' if hi == 99 else f'{hi:+d}%'}"
        print(_line(lab, stats(xs)))
        for half, f in (("  앞", lambda r: r["date"] < mid), ("  뒤", lambda r: r["date"] >= mid)):
            print(_line(half, stats([r for r in xs if f(r)])))
    print("\n② 허용 폭 ±X% 안 vs 밖")
    for x in (1, 2, 3, 4, 5):
        print(_line(f"±{x}% 안", stats([r for r in res if abs(r["gap"]) <= x])) + "\n"
              + _line(f"±{x}% 밖(아래)", stats([r for r in res if r["gap"] < -x])))
    print("\n③ 테마 안 순위별(갭 ±3% 안만)")
    for sr in (1, 2, 3):
        print(_line(f"{sr}등주", stats([r for r in res if r["stock_rank"] == sr and abs(r["gap"]) <= 3])))
    print("\n④ 기준: 전체")
    print(_line("전체", stats(res)))


if __name__ == "__main__":
    main()
