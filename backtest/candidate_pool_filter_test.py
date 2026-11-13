"""
candidate_pool_filter_test.py — 매수 특성 필터를 '후보 풀 전체'로 재검증 (2026-11-14, 대장 "숫자로 나와야만 해")
================================================================
[이 파일이 하는 일 — 비개발자용 설명]
buy_feature_analysis.py는 '봇이 실제로 산 것'만 봤다. 그런데 필터로 걸러 매매가 줄면 회전율이 떨어지므로
(대장 철학: 매매 줄이지 말고 종목을 바꿔라), '봇이 후보로 봤지만 안 산 종목'까지 넣어 다시 본다:
  · daybot  : candidate_log(10-06~) — 종목·날짜별 첫 등장 가격에 샀다고 가정, 단타 규칙 비슷하게
              (★ 11-14 대장 "그날 승부" — 당일 종가 청산, 당일 저가가 -3.5% 닿았으면 손절로 보수적)
  · 스윙    : sbo2_candidates(06-12~09-28) — 그날 가격에 샀다고 가정, 10거래일(-7% 손절 / +10% 익절, 아니면 10일째 종가).
              sbot 후보 기록은 없어서, 같은 스윙인 sbo2 후보로 sbot 필터를 검증(처음 찾은 데이터와 겹치지 않는 별도 데이터).
특성은 buy_feature_analysis.features와 같은 것(매수 전날까지만). 필터마다 '통과 / 걸림' 두 묶음의 승률·평균을 비교하고,
기간을 앞/뒤 반으로 나눠 같은 방향인지 본다. 통과 묶음이 충분히 많은지(=대신 살 후보가 있는지)도 같이 봄.
실행: venv/bin/python backtest/candidate_pool_filter_test.py
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import buy_feature_analysis as bfa  # noqa: E402

BASE = bfa.BASE
DAY_DB = os.path.join(BASE, "daybot_trade_history.db")
SBO2_DB = os.path.join(BASE, "lina_bot", "sbo2_trades.db")
MASTER = os.path.join(BASE, "lina_bot", "kr_theme_finance.db")
# daybot이 이미 거르는 이유(이 종목들은 봇 규칙상 어차피 안 삼) — 필터 효과는 '봇이 살 수 있었던' 후보 안에서 봐야 함
DAY_QUALITY_SKIPS = ("등락률범위밖", "호가비율미달", "시총미달", "섹터밖", "자사주:")


def simulate(b: list, entry_date: str, entry: float, days: int, stop: float, take: float):
    after = [x for x in b if x[0] > entry_date][:days]
    if len(after) < days:
        return None
    for _, o, h, l, c, v in after:
        if l is not None and l <= entry * (1 - stop / 100):
            return -stop
        if h is not None and h >= entry * (1 + take / 100):
            return take
    return (after[-1][4] / entry - 1) * 100


def sameday(b: list, d: str, entry: float, stop: float = 3.5):
    """★ 11-14 대장 "dbot은 그날 승부" — 당일 종가 청산, 당일 저가가 -stop% 닿았으면 손절(진입 전 저가일 수도 있어 보수적)."""
    day = [x for x in b if x[0] == d]
    if not day:
        return None
    _, o, h, l, c, v = day[0]
    if l is not None and l <= entry * (1 - stop / 100):
        return -stop
    return (c / entry - 1) * 100


def daybot_pool(cache, regime, quality_only: bool = True) -> list:
    c = sqlite3.connect(DAY_DB)
    rows = c.execute("SELECT code, stock_name, substr(ts,1,10), price, COALESCE(skip_reason,''), bought "
                     "FROM candidate_log WHERE price > 0 ORDER BY ts").fetchall()
    seen, out = set(), []
    for code, name, d, price, skip, bought in rows:
        if (code, d) in seen:
            continue
        seen.add((code, d))
        if quality_only and not bought and skip.startswith(DAY_QUALITY_SKIPS):
            continue
        b = bfa.bars(code, d, cache)
        tr = dict(code=code, name=name, buy_price=float(price), buy_date=d, sell_date=d, rate=0.0)
        f = bfa.features(tr, b, regime)
        res = sameday(b, d, float(price))
        if f and res is not None:
            out.append({**tr, **f, "rate": res, "bought": bool(bought)})
    return out


def swing_pool(cache, regime) -> list:
    name2code = {n.replace(" ", ""): k for k, n in sqlite3.connect(MASTER).execute("SELECT code, name FROM kr_stock_master") if n}
    c = sqlite3.connect(SBO2_DB)
    rows = c.execute("SELECT stock_name, scan_date, curr_price, bought FROM sbo2_candidates "
                     "WHERE curr_price > 0 ORDER BY scan_date, scan_time").fetchall()
    seen, out = set(), []
    for name, d, price, bought in rows:
        code = name2code.get((name or "").replace(" ", ""))
        if not code or (code, d) in seen:
            continue
        seen.add((code, d))
        b = bfa.bars(code, d, cache)
        tr = dict(code=code, name=name, buy_price=float(price), buy_date=d, sell_date=d, rate=0.0)
        f = bfa.features(tr, b, regime)
        res = simulate(b, d, float(price), 10, 7.0, 10.0)
        if f and res is not None:
            out.append({**tr, **f, "rate": res, "bought": bool(bought)})
    return out


def g(r, k, default=0.0):
    v = r.get(k)
    return default if v is None else v


FILTERS = {
    "daybot": {
        "③ 과열 제외(60일 +80%↑ 또는 20일 +20%↑)": lambda r: g(r, "r60") >= 80 or g(r, "r20") >= 20,
        "  ㄴ 60일 +80%↑만": lambda r: g(r, "r60") >= 80,
        "  ㄴ 20일 +20%↑만": lambda r: g(r, "r20") >= 20,
        "(참고) 추격 7%↑": lambda r: g(r, "chase") >= 7,
    },
    "swing": {
        "① sbot 5일 수익률 +3~10% 밖": lambda r: not (3 <= g(r, "r5") < 10),
        "② sbo2 20일 -5%↓": lambda r: g(r, "r20") < -5,
        "①+② 같이": lambda r: (not (3 <= g(r, "r5") < 10)) or g(r, "r20") < -5,
        "(참고) 낙하칼 20일선 -10%↓·60일고점 -40%↓": lambda r: g(r, "ma20_gap") < -10 or g(r, "off_hi60") < -40,
    },
}


def stat(rows):
    if not rows:
        return "  0건"
    w = sum(r["rate"] > 0 for r in rows)
    return f"{len(rows):>5}건 승률 {w / len(rows) * 100:>3.0f}% 평균 {sum(r['rate'] for r in rows) / len(rows):+6.2f}%"


def report(label: str, pool: list, filters: dict):
    pool.sort(key=lambda r: r["buy_date"])
    days = sorted({r["buy_date"] for r in pool})
    mid = days[len(days) // 2] if days else ""
    print(f"\n━━━ {label}: 후보 {len(pool)}건 ({days[0] if days else ''}~{days[-1] if days else ''}, {len(days)}일) · "
          f"전체 {stat(pool)} ━━━")
    for name, bad in filters.items():
        print(f"■ {name}")
        for lab, part in (("전체", pool), (f"앞반(~{mid})", [r for r in pool if r["buy_date"] < mid]),
                          ("뒷반", [r for r in pool if r["buy_date"] >= mid])):
            ok = [r for r in part if not bad(r)]
            ng = [r for r in part if bad(r)]
            per_day = len(ok) / max(1, len({r['buy_date'] for r in part}))
            print(f"   {lab:<14} 통과 {stat(ok)} | 걸림 {stat(ng)} | 통과 후보 하루 {per_day:.1f}개")


def main():
    cache, regime = bfa._cache(), bfa.regime_map()
    report("daybot 후보 풀 — 봇이 살 수 있었던 것 (당일 종가, 장중 -3.5% 손절)", daybot_pool(cache, regime), FILTERS["daybot"])
    report("daybot 후보 전체 — 기존 필터로 빠진 것 포함 (당일 종가, 장중 -3.5% 손절)",
           daybot_pool(cache, regime, quality_only=False), FILTERS["daybot"])
    if "--day-only" in sys.argv:
        return
    report("스윙 후보 풀 = sbo2 후보 (10거래일, -7%/+10%)", swing_pool(cache, regime), FILTERS["swing"])


if __name__ == "__main__":
    main()
