"""
rebound_backtest.py — 연속 하락 뒤 "아침 투매 → 오후 반등" 검증 (2026-10-13)
================================================================
[이 파일이 하는 일 — 비개발자용 설명]

대장 가설: "시장이 5일 연속 빠지면 미수·반대매매 만기가 몰려 다음 날 오전에 크게 밀렸다가
오후에 반등할 가능성이 높다(양전까진 아니어도). 5일 내내 손절당했으니 그 반등에 올라타자."
봇에 넣기 전에 리나 일봉 DB(전 종목 시·고·저·종가)로 과거에 정말 그랬는지 본다.

1. 시장 하루 모양 — 하락 연속일수(전날까지)별로 그날 전 종목 중앙값:
   시가→저가(오전 투매 크기), 저가→종가(반등 크기), 시가→종가, 전일→종가
   ※ 하락일 = 그날 전 종목 등락률 중앙값 < 0 (지수 없이 일봉 DB로 계산)
2. 매매 흉내 — 그날 거래대금 상위(전날 기준) 종목을 "시가 대비 -d%" 지정가로 매수
   (저가가 거기까지 왔으면 체결, 장중 순서 몰라도 됨) → 당일 종가 / 다음 날 시가 / 다음 날 종가 매도
   연속 하락 N일 뒤 vs 평소(모든 날) 비교. 비용 0.3%(수수료·세금·슬리피지) 차감
3. 과거 5일+ 연속 하락 다음 날 목록 — 표본이 몇 번 안 되니 하나하나 눈으로 확인
한계: 일봉 DB가 약 1년치라 "5일 연속"은 몇 번 없음 → 3·4일도 같이 봄. 미수 만기 데이터는 없음.
실행:  python backtest/rebound_backtest.py [--days 250] [--top 200]
"""
import os
import sys
import statistics

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "backtest"))
import swing_backtest as sb  # noqa: E402   (일봉 읽기·성적 집계 재사용)

THEME_DB = os.path.join(BASE, "lina_bot", "kr_theme_finance.db")
COST = 0.003
DIPS = (2.0, 3.0, 4.0, 5.0)
STREAKS = (3, 4, 5)
EXCLUDE_WORDS = ("KODEX", "TIGER", "KBSTAR", "ARIRANG", "HANARO", "KOSEF", "ACE ", "SOL ", "RISE ",
                 "PLUS ", "ETN", "스팩", "레버리지", "인버스")


def excluded(name: str) -> bool:
    return any(w in name for w in EXCLUDE_WORDS) or name.endswith("우") or name.endswith("우B")


def day_table(daily: dict) -> dict:
    """{날짜: {"ret","o2l","l2c","o2c"} 중앙값(%) + "n"} — 전 종목 기준."""
    per = {}
    for name, rows in daily.items():
        if excluded(name):
            continue
        for i in range(1, len(rows)):
            d, o, h, l, c, _ = rows[i]
            pc = rows[i - 1][4]
            if not (pc and o and l):
                continue
            per.setdefault(d, []).append(((c / pc - 1) * 100, (l / o - 1) * 100, (c / l - 1) * 100,
                                          (c / o - 1) * 100))
    out = {}
    for d, xs in per.items():
        if len(xs) < 50:
            continue
        out[d] = {k: statistics.median(x[j] for x in xs) for j, k in enumerate(("ret", "o2l", "l2c", "o2c"))}
        out[d]["n"] = len(xs)
    return out


def streaks(table: dict) -> dict:
    """{날짜: 전날까지 연속 하락일수}."""
    out, run = {}, 0
    for d in sorted(table):
        out[d] = run
        run = run + 1 if table[d]["ret"] < 0 else 0
    return out


def trades(daily: dict, dip: float, top: int, days: set) -> list:
    """days에 속한 날, 전날 거래대금 상위 top 종목을 시가-dip% 지정가로 매수 → 세 가지 매도."""
    by_date = {}
    for name, rows in daily.items():
        if excluded(name):
            continue
        for i in range(1, len(rows)):
            if rows[i][0] in days:
                by_date.setdefault(rows[i][0], []).append((rows[i - 1][5], name, i))
    out = []
    for d, xs in by_date.items():
        xs.sort(key=lambda x: -x[0])
        for _, name, i in xs[:top]:
            rows = daily[name]
            _, o, h, l, c, _ = rows[i]
            px = o * (1 - dip / 100)
            if l > px:
                continue                       # 거기까지 안 빠짐 — 미체결
            r = {"date": d, "name": name, "days": 0, "ret": c / px - 1 - COST, "nxt_o": None, "nxt_c": None}
            if i + 1 < len(rows):
                r["nxt_o"] = rows[i + 1][1] / px - 1 - COST
                r["nxt_c"] = rows[i + 1][4] / px - 1 - COST
            out.append(r)
    return out


def _stats(rs: list, key: str) -> str:
    xs = [dict(r, ret=r[key], days=0) for r in rs if r[key] is not None]
    return sb.fmt(sb.stats(xs)).split(" 보유")[0] if xs else "     0건"


def report(daily: dict, top: int) -> str:
    table = day_table(daily)
    st = streaks(table)
    L = [f"📉 연속 하락 뒤 반등 검증 — {min(table)}~{max(table)} {len(table)}거래일, 종목 중앙값 기준"]
    L.append("■ 1. 하락 연속일수(전날까지)별 그날 시장 모양 (중앙값 %의 평균)")
    L.append("      연속    일수  시가→저가  저가→종가  시가→종가  전일→종가  반등일(종가>시가)")
    for k, label in ((0, "0일"), (1, "1일"), (2, "2일"), (3, "3일"), (4, "4일"), (5, "5일+")):
        ds = [d for d in table if (st[d] >= 5 if k == 5 else st[d] == k)]
        if not ds:
            continue
        avg = lambda f: sum(table[d][f] for d in ds) / len(ds)
        up = sum(table[d]["o2c"] > 0 for d in ds)
        L.append(f"      {label:<5} {len(ds):>5}   {avg('o2l'):+6.2f}    {avg('l2c'):+6.2f}    "
                 f"{avg('o2c'):+6.2f}    {avg('ret'):+6.2f}    {up}/{len(ds)}")
    L.append(f"■ 2. 매매 흉내 — 전날 거래대금 상위 {top}종목, 시가 대비 -d% 지정가 매수 (비용 {COST:.1%} 차감)")
    alld = set(table)
    for n in (0,) + STREAKS:
        ds = alld if n == 0 else {d for d in table if st[d] >= n}
        L.append(f"   [{'평소(모든 날)' if n == 0 else f'{n}일+ 연속 하락 다음 날'} — {len(ds)}일]")
        for dip in DIPS:
            rs = trades(daily, dip, top, ds)
            L.append(f"     -{dip:.0f}% 매수 → 당일종가 {_stats(rs, 'ret')}")
            L.append(f"               → 다음날시가 {_stats(rs, 'nxt_o')}")
            L.append(f"               → 다음날종가 {_stats(rs, 'nxt_c')}")
    big = [d for d in sorted(table) if st[d] >= 5]
    L.append(f"■ 3. 5일+ 연속 하락 다음 날 {len(big)}번 (종목 중앙값)")
    for d in big:
        t = table[d]
        L.append(f"   {d} (연속 {st[d]}일 뒤) 시가→저가 {t['o2l']:+.2f}% · 저가→종가 {t['l2c']:+.2f}% · "
                 f"시가→종가 {t['o2c']:+.2f}% · 전일→종가 {t['ret']:+.2f}%")
    if len(big) < 5:
        L.append("   ※ 5번 미만 — 이것만으로 봇 규칙을 정하기엔 표본이 너무 적음, 3·4일 결과와 같이 볼 것")
    return "\n".join(L)


if __name__ == "__main__":
    a = sys.argv[1:]
    days = int(a[a.index("--days") + 1]) if "--days" in a else 250
    top = int(a[a.index("--top") + 1]) if "--top" in a else 200
    print(report(sb.load_daily(THEME_DB, days), top))
