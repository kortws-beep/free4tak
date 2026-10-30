"""
dip_leader_minute.py — 데이봇 '음봉 엔진' 장중 진입 분봉 검증 (2026-10-30, 대장 "분봉분석 해볼까")
================================================================
[이 파일이 하는 일 — 비개발자용 설명]
음봉 엔진(core/dip_leader.py)은 일봉 종가 매수로만 검증됐고, 실전은 장중 진입
("빠진 지수가 당일 저가 대비 +0.5% 반등하면, 대상 중 저가 대비 가장 많이 오른 종목")이라 분봉으로 다시 돌려 본다.
  지수 대용: KODEX 200(069500)=코스피, KODEX 코스닥150(229200)=코스닥 — 1분봉 등락·당일 저가 대비 반등.
  하락일: 둘 중 하나가 장중 전일 대비 -2% 이하로 내려간 날(ETF 일봉 저가 기준으로 먼저 추림).
  대상: dip_leader.universe(그날 전까지 일봉: 60일 +30%↑·20일 거래대금 100억↑) — 그 시각 1분봉으로
        오늘 -5%↓ & 5일 고점(오늘 고가 포함) -10%↓ → 당일 저가 대비 반등 큰 순 1종목.
        NEW·SUB 관심그룹 필터는 '지금' 목록이라(과거엔 달랐을 수 있음) 전체/NEW·SUB 두 줄로 같이 봄.
  진입 방식 비교: 반등 +0.3/+0.5(현행)/+1.0% 첫 시각, 14:30(처음 설계), 15:20(종가 근사).
  매도: +3% 전량 익절 / -3.5% 손절(같은 봉이면 손절로 — 불리하게) / 산 날 포함 4거래일째 종가 정리.
  수수료·세금 왕복 0.2%, 매수 미끄러짐 0.1%. 분봉은 backtest/data/minute_cache.db 재사용.
실행: venv/bin/python backtest/dip_leader_minute.py
"""
import os
import sys
import sqlite3
import datetime

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(BASE, "core"), os.path.join(BASE, "backtest")]
import dip_leader                     # noqa: E402
from daybot_replay import MinuteStore  # noqa: E402

ETF = {"kospi": "069500", "kosdaq": "229200"}
COST, SLIP = 0.002, 0.001
TP, SL, HOLD_DAYS = 3.0, 3.5, 4
START, END = "100000", "152000"
SINCE = "2025-11-06"                  # 분봉 캐시 시작
AMT = 500_000


def etf_daily(api, code: str) -> dict:
    """{date: (전일종가, 저가)} — 100봉씩 이어 받기."""
    out, end = {}, None
    for _ in range(4):
        rows = api.get_daily_ohlc(code, days=100, end_date=end)
        if not rows:
            break
        for r in rows:
            out[r["date"]] = r
        oldest = min(r["date"] for r in rows)
        if oldest <= SINCE:
            break
        end = (datetime.date.fromisoformat(oldest) - datetime.timedelta(days=1)).strftime("%Y%m%d")
    dates = sorted(out)
    return {d: (out[p]["close"], out[d]["low"]) for p, d in zip(dates, dates[1:])}


def index_path(store, date: str, prev: dict) -> list:
    """[(time, {지수: (등락%, 저가 대비 반등%)})] 정규장 1분마다."""
    bars = {k: store.day(c, date) for k, c in ETF.items()}
    times = sorted(set().union(*({b[0] for b in v} for v in bars.values())))
    last, low, out = {}, {}, []
    idx = {k: {b[0]: b for b in v} for k, v in bars.items()}
    for t in times:
        st = {}
        for k in ETF:
            b = idx[k].get(t)
            if b:
                last[k] = b[1]
                low[k] = min(low.get(k, b[3]), b[3])
            if k in last and prev.get(k):
                st[k] = ((last[k] / prev[k] - 1) * 100, (last[k] / low[k] - 1) * 100)
        out.append((t, st))
    return out


def trigger_times(path: list) -> dict:
    """진입 방식 → 시각(없으면 빠짐)."""
    out = {}
    for name, reb in (("반등+0.3%", 0.3), ("반등+0.5%(현행)", 0.5), ("반등+1.0%", 1.0)):
        for t, st in path:
            if START <= t <= END and any(c <= -dip_leader.INDEX_DROP and r >= reb for c, r in st.values()):
                out[name] = t
                break
    for name, at in (("14:30 고정", "143000"), ("15:20 고정", "152000")):
        hit = [t for t, st in path if t <= at and any(c <= -dip_leader.INDEX_DROP for c, r in st.values())]
        cand = [t for t, _ in path if t >= at]
        if hit and cand:
            out[name] = cand[0]
    return out


def daily_rows(date: str) -> dict:
    """{코드: (전일종가, 오늘 고가, 오늘 저가)} — 일봉 DB(이름→코드)."""
    conn = sqlite3.connect(dip_leader.DAILY_DB, timeout=10)
    try:
        codes = {n: c for c, n in conn.execute("SELECT code, name FROM kr_stock_master")}
        prev_d = conn.execute("SELECT MAX(date) FROM kr_stock_daily_data WHERE date < ?", (date,)).fetchone()[0]
        prev = {n: c for n, c in conn.execute("SELECT stock_name, close_price FROM kr_stock_daily_data WHERE date=?", (prev_d,))}
        out = {}
        for n, h, l in conn.execute("SELECT stock_name, high_price, low_price FROM kr_stock_daily_data WHERE date=?", (date,)):
            if n in codes and prev.get(n):
                out[codes[n]] = (prev[n], h or 0, l or 0)
        return out
    finally:
        conn.close()


def pick_at(store, date: str, t: str, uni: dict, daily: dict, allow=None):
    """그 시각 조건 통과 종목 중 저가 대비 반등 1등 → (코드, 이름, 가격, 등락, 반등) / 없으면 None."""
    best = None
    for code, u in uni.items():
        if allow is not None and code not in allow:
            continue
        d = daily.get(code)
        if not d:
            continue
        prev, dh, dl = d
        hi_all = max(u["hi5"], dh)
        if dl > prev * (1 - dip_leader.DAY_DROP / 100) or dl > hi_all * (1 - dip_leader.FROM_HIGH / 100):
            continue                               # 하루 종일 조건에 못 닿은 종목은 분봉 안 받음
        bars = [b for b in store.day(code, date) if b[0] <= t]
        if not bars:
            continue
        price, high, low = bars[-1][1], max(b[2] for b in bars), min(b[3] for b in bars)
        chg = (price / prev - 1) * 100
        if chg <= -dip_leader.DAY_DROP and price <= max(u["hi5"], high) * (1 - dip_leader.FROM_HIGH / 100):
            bounce = (price / low - 1) * 100
            if best is None or bounce > best[4]:
                best = (code, u["name"], price, chg, bounce)
    return best


def exit_after(store, code: str, date: str, t: str, price: float) -> tuple:
    """→ (수익률%, 사유, 보유일)."""
    entry = price * (1 + SLIP)
    days = store.days_from(code, date, HOLD_DAYS)
    for i, (d, bars) in enumerate(days):
        for tm, p, h, l in bars:
            if i == 0 and tm <= t:
                continue
            if l <= entry * (1 - SL / 100):
                if h < entry * (1 - SL / 100):     # 봉 전체가 손절선 아래(갭) → 그 봉 고가에 체결(불리하게)
                    return (h / entry - 1) * 100 - COST * 100, "손절", i
                return -SL - COST * 100, "손절", i
            if h >= entry * (1 + TP / 100):
                return TP - COST * 100, "익절", i
    if not days:
        return 0.0, "자료없음", 0
    last = days[-1][1][-1][1]
    return (last / entry - 1) * 100 - COST * 100, "기한", len(days) - 1


def main():
    from dotenv import load_dotenv
    load_dotenv(os.path.join(BASE, ".env"))
    from kis_api import KisAPI
    api = KisAPI()
    store = MinuteStore(api)
    daily = {k: etf_daily(api, c) for k, c in ETF.items()}
    days = sorted(d for d in set(daily["kospi"]) | set(daily["kosdaq"]) if d >= SINCE and any(
        d in daily[k] and daily[k][d][1] <= daily[k][d][0] * (1 - dip_leader.INDEX_DROP / 100) for k in ETF))
    watch = dip_leader.watch_codes(api, os.getenv("KIS_HTS_ID2", os.getenv("KIS_HTS_ID", "")))
    print(f"📉 지수 -{dip_leader.INDEX_DROP:g}% 닿은 날 {len(days)}일 ({days[0] if days else '-'} ~ {days[-1] if days else '-'}) · "
          f"NEW·SUB(지금 목록) {len(watch)}종목")
    res = {}       # (방식, 필터) → [(날짜, 이름, 시각, 수익률, 사유, 보유일)]
    for date in days:
        prev = {k: daily[k][date][0] for k in ETF if date in daily[k]}
        path = index_path(store, date, prev)
        trig = trigger_times(path)
        if not trig:
            continue
        uni, dd = dip_leader.universe(date), daily_rows(date)
        for name, t in trig.items():
            for flt, allow in (("전체", None), ("NEW·SUB", watch)):
                p = pick_at(store, date, t, uni, dd, allow)
                if not p:
                    continue
                ret, why, held = exit_after(store, p[0], date, t, p[2])
                res.setdefault((name, flt), []).append((date, p[1], t[:4], ret, why, held))
        print(f"  {date} 진입시각 {', '.join(f'{k} {v[:2]}:{v[2:4]}' for k, v in trig.items())} · 대상 {len(uni)} · 한투 호출 누적 {store.calls}")
    print(f"\n{'진입 방식':<16}{'대상':<9}{'거래':>4}{'승률':>7}{'평균':>8}{'50만씩':>11}{'익절/손절/기한':>14}")
    for (name, flt), xs in sorted(res.items(), key=lambda kv: (kv[0][1], -sum(x[3] for x in kv[1]))):
        n = len(xs)
        win = sum(1 for x in xs if x[3] > 0) / n * 100
        avg = sum(x[3] for x in xs) / n
        cnt = {w: sum(1 for x in xs if x[4] == w) for w in ("익절", "손절", "기한")}
        half = n // 2
        a1 = sum(x[3] for x in xs[:half]) / half if half else 0
        a2 = sum(x[3] for x in xs[half:]) / (n - half) if n - half else 0
        print(f"{name:<16}{flt:<9}{n:>4}{win:>6.0f}%{avg:>+7.2f}%{avg / 100 * AMT * n:>+11,.0f}"
              f"{cnt['익절']:>6}/{cnt['손절']}/{cnt['기한']}   앞 {a1:+.2f} / 뒤 {a2:+.2f}")
    print("\n[현행 방식 · NEW·SUB 거래 목록]")
    for x in res.get(("반등+0.5%(현행)", "NEW·SUB"), []):
        print(f"  {x[0]} {x[2][:2]}:{x[2][2:]} {x[1]:<12} {x[3]:+.2f}% {x[4]} {x[5]}일")
    print("\n※ NEW·SUB는 지금 목록 기준(과거엔 없던 종목이 들어갔을 수 있음 — 후한 쪽). 지수는 ETF 대용.")


if __name__ == "__main__":
    sys.exit(main())
