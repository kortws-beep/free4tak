"""
sector_dip_replay.py — "강한 섹터 대장주가 눌려 출발하면 장 시작에 산다" 1분봉 재현 (2026-10-19)
================================================================
[이 파일이 하는 일 — 비개발자용 설명]
daybot 신호(이미 오르는 종목 추격)는 타점·매도 규칙을 바꿔도 2주 내내 마이너스였다(SCENARIOS 6-1).
10-19 대장 수동 +12.5%는 반대 방식 — 강한 섹터(2차전지·반도체 테스트)의 대장주가 갭하락했을 때 바닥 쪽에서
사서 10:30 전에 정리. 그 방식을 규칙으로 옮겨 과거에 돌려 본다(조건은 돌리기 전에 고정).
  1. 강한 섹터 = 리나 관심그룹(backtest/data/watch_groups.json, 'new' 제외) 중 전날까지 5거래일 상승률 상위 3개
  2. 후보 = 그 섹터에서 전날 거래대금 1·2등(대장·2등주)
  3. 매수 = 그날 장 시작 첫 1분봉 가격. 시가가 전날 종가 대비 얼마나 낮게/높게 출발했는지(갭) 구간별로 성적 비교
     → '갭하락일수록 낫다'가 맞는지 확인
  4. 매도 = ⓐ daybot 현행(손절 -3.5%, +5%에 30% 익절 후 고점 -2%) ⓑ 대장식(손절 -3.5%, 아니면 10:30 정리)
일봉은 리나 kr_stock_daily_data, 1분봉은 daybot_replay의 저장소(backtest/data/minute_cache.db, 없으면 한투 조회).
앞·뒤 절반 기간 둘 다 +인 구간만 의미 있게 본다. 주문 없음.
실행:  python backtest/sector_dip_replay.py [시작일, 기본 2026-04-01]
"""
import os
import sys
import json
import sqlite3
from dataclasses import replace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import daybot_replay as R   # noqa: E402

BASE = R.BASE
THEME_DB = os.path.join(BASE, "lina_bot", "kr_theme_finance.db")
GROUPS = os.path.join(BASE, "backtest", "data", "watch_groups.json")
TOP_SECTORS, PER_SECTOR, LOOKBACK = 3, 2, 5
EXITS = {"ⓐ daybot 현행": R.CURRENT, "ⓑ 대장식(손절-3.5·10:30정리)": replace(R.CURRENT, eod="103000")}
GAPS = [(-99, -4, "갭 -4% 이하"), (-4, -2, "갭 -4~-2%"), (-2, 0, "갭 -2~0%"), (0, 2, "갭 0~+2%"), (2, 99, "갭 +2% 이상")]


def load_daily(start: str) -> tuple:
    """({날짜: {종목명: (시가, 종가, 거래대금)}}, 정렬된 날짜) — start 10거래일 전부터."""
    conn = sqlite3.connect(f"file:{THEME_DB}?mode=ro", uri=True)
    dates = [d for (d,) in conn.execute("SELECT DISTINCT date FROM kr_stock_daily_data WHERE open_price>0 ORDER BY date")]
    i0 = max(0, next((i for i, d in enumerate(dates) if d >= start), len(dates)) - LOOKBACK - 5)
    rows = conn.execute("SELECT date, stock_name, open_price, close_price, trade_value FROM kr_stock_daily_data "
                        "WHERE date>=? AND open_price>0 AND close_price>0", (dates[i0],)).fetchall()
    conn.close()
    day = {}
    for d, n, o, c, v in rows:
        day.setdefault(d, {})[n] = (o, c, v or 0)
    return day, sorted(day)


def pick_events(day: dict, dates: list, groups: dict, start: str) -> list:
    """[{date, code, name, sector, gap, prev_close}] — 그날 아침 알 수 있는 정보(전날까지)로만 고름."""
    ev = []
    for i, d in enumerate(dates):
        if d < start or i < LOOKBACK + 1:
            continue
        prev, base = dates[i - 1], dates[i - 1 - LOOKBACK]
        strength = []
        for g, members in groups.items():
            rets = [day[prev][n][1] / day[base][n][1] - 1 for _, n in members
                    if n in day.get(prev, {}) and n in day.get(base, {})]
            if len(rets) >= 2:
                strength.append((sum(rets) / len(rets), g))
        for _, g in sorted(strength, reverse=True)[:TOP_SECTORS]:
            ranked = sorted([(day[prev][n][2], c, n) for c, n in groups[g] if n in day[prev]], reverse=True)
            for _, c, n in ranked[:PER_SECTOR]:
                if n not in day.get(d, {}):
                    continue
                pc = day[prev][n][1]
                ev.append({"date": d, "code": c, "name": n, "sector": g, "prev_close": pc,
                           "gap": (day[d][n][0] / pc - 1) * 100})
    return ev


def run(store, events: list) -> dict:
    out = {k: [] for k in EXITS}
    for e in events:
        days = store.days_from(e["code"], e["date"], R.CURRENT.hold_days + 2)
        if not days or days[0][0] != e["date"] or not days[0][1]:
            continue
        t0, p0 = days[0][1][0][0], days[0][1][0][1]          # 첫 1분봉에 매수
        for k, rule in EXITS.items():
            r = R.simulate(days, t0, p0, rule)
            if r:
                out[k].append(dict(e, **r))
    return out


def report(res: dict, half: str) -> str:
    L = []
    for k, rs in res.items():
        L.append(f"■ {k} — 전체 {R.fmt(R.stats([r['ret'] for r in rs]))}")
        for lo, hi, name in GAPS:
            g = [r for r in rs if lo <= r["gap"] < hi]
            if not g:
                continue
            a = sum(r["ret"] for r in g if r["date"] < half) * R.AMT
            b = sum(r["ret"] for r in g if r["date"] >= half) * R.AMT
            tag = "✅" if a > 0 and b > 0 else "❌"
            L.append(f"   {name:<12} {R.fmt(R.stats([r['ret'] for r in g]))} | 앞 {a:+,.0f} · 뒤 {b:+,.0f} {tag}")
    return "\n".join(L)


if __name__ == "__main__":
    start = next((a for a in sys.argv[1:] if a[:2] == "20"), "2026-04-01")
    sys.path.insert(0, os.path.join(BASE, "core"))
    from dotenv import load_dotenv
    load_dotenv(os.path.join(BASE, ".env"))
    from kis_api import KisAPI
    with open(GROUPS, encoding="utf-8") as f:
        groups = {g: [tuple(x) for x in v] for g, v in json.load(f).items() if g != "new"}
    day, dates = load_daily(start)
    events = pick_events(day, dates, groups, start)
    ds = sorted({e["date"] for e in events})
    half = ds[len(ds) // 2] if ds else start
    print(f"📈 강한 섹터 대장·2등주 — {start}~ {len(ds)}거래일 {len(events)}건 (섹터 {len(groups)}개, 앞뒤 기준 {half}) — 분봉 받는 중…")
    store = R.MinuteStore(KisAPI())
    res = run(store, events)
    print(f"   분봉 재현 {len(next(iter(res.values())))}건 · 한투 호출 {store.calls}회\n")
    print(report(res, half))
    print("※ 첫 1분봉 가격 매수·분봉 고가/저가 근사·수수료세금 0.2%. 그룹 구성이 지금 기준이라 과거 강도는 부풀 수 있음")
