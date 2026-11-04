"""
dip_engine2_minute.py — '음봉 엔진 2' 분봉 검증 (2026-11-04, 대장 아이디어)
================================================================
[이 파일이 하는 일 — 비개발자용 설명]
대장: "음봉 엔진 가동 조건이 갖춰지면 일반 엔진은 멈추고, 20분 정도 종목을 추리고, 이후 강하게 반등하는 녀석을 잡는다."
지금 음봉 엔진(14:30 고정 진입)과 같은 날·같은 대상·같은 매도 규칙으로 비교한다.
  날짜: 지수(KODEX 200 / 코스닥150 ETF)가 장중 -2%↓ 찍은 날 중 현행 상황 조건 통과
        (전날까지 연속하락 ≥1일 · 전날 종가가 20일 고점 -15% 이내) — dip_situations_prep 기준과 같음
  대상: dip_leader.universe(60일 +30%↑·20일 대금 100억↑) — 오늘 -5%↓ & 5일 고점 -10%↓, 전체 / NEW·SUB 두 줄
  진입:
    현행 14:30        — 14:30에 저가 대비 반등 1등
    T+20 즉시         — 지수가 처음 -2% 찍은 시각(T)에서 20분 뒤 반등 1등
    추림→반등 R%      — T+20에 조건 통과 종목을 '후보'로 고정, 그 뒤 15:20까지 매분 후보 중
                        당일 저가 대비 R%(1/2/3) 넘게 반등한 첫 종목(여럿이면 반등 큰 놈)
    추림→새반등 R%    — 같은데 저가를 T+20부터 새로 잼(추린 뒤 한 번 더 빠졌다 튀는 놈)
  매도(현행 음봉 규칙): -10% 손절 · +5%에 절반 → 나머지 고점 -5% 트레일링(본전 아래로는 안 내려감) · 5거래일째 종가
  수수료·세금 0.2%, 매수 미끄러짐 0.1%. 분봉은 backtest/data/minute_cache.db 재사용.
실행: venv/bin/python backtest/dip_engine2_minute.py
"""
import os
import sys
import datetime

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(BASE, "core"), os.path.join(BASE, "backtest")]
import dip_leader                     # noqa: E402
import dip_leader_minute as M         # noqa: E402
import dip_situations_prep as P       # noqa: E402

WAIT_MIN = 20
REBOUNDS = (1.0, 2.0, 3.0)
LAST = "152000"


def plus_min(t: str, m: int) -> str:
    d = datetime.datetime.strptime(t, "%H%M%S") + datetime.timedelta(minutes=m)
    return d.strftime("%H%M%S")


def first_hit(path: list) -> str:
    """지수가 처음 장중 -2%↓ 된 시각(09:05 이후)."""
    for t, st in path:
        if t >= "090500" and any(c <= -dip_leader.INDEX_DROP for c, r in st.values()):
            return t
    return ""


def state_at(store, date, t, uni, daily, allow):
    """그 시각 조건 통과 종목 {코드: (이름, 가격, 반등%)}."""
    out = {}
    for code, u in uni.items():
        if allow is not None and code not in allow:
            continue
        d = daily.get(code)
        if not d:
            continue
        prev, dh, dl = d
        if dl > prev * (1 - dip_leader.DAY_DROP / 100) or dl > max(u["hi5"], dh) * (1 - dip_leader.FROM_HIGH / 100):
            continue
        bars = [b for b in store.day(code, date) if b[0] <= t]
        if not bars:
            continue
        price, high, low = bars[-1][1], max(b[2] for b in bars), min(b[3] for b in bars)
        if (price / prev - 1) * 100 <= -dip_leader.DAY_DROP and price <= max(u["hi5"], high) * (1 - dip_leader.FROM_HIGH / 100):
            out[code] = (u["name"], price, (price / low - 1) * 100)
    return out


def bounce_path(store, date, code, start, end, fresh=False):
    """[(시각, 가격, 저가 대비 반등%)] start~end. fresh=True면 저가를 start부터 새로 잼(추린 뒤 새 반등)."""
    bars, low, out = store.day(code, date), None, []
    for tm, p, h, l in bars:
        if fresh and tm < start:
            continue
        low = l if low is None else min(low, l)
        if start <= tm <= end:
            out.append((tm, p, (p / low - 1) * 100))
    return out


def sim(store, code, date, t, price):
    """현행 음봉 매도 규칙 → 수익률%(비용 포함)."""
    entry = price * (1 + M.SLIP)
    ds = store.days_from(code, date, dip_leader.HOLD_DAYS)
    half, peak, booked = False, entry, 0.0
    for i, (d, bars) in enumerate(ds):
        for tm, p, h, l in bars:
            if i == 0 and tm <= t:
                continue
            if not half:
                if l <= entry * (1 - dip_leader.STOP_PCT / 100):
                    px = min(h, entry * (1 - dip_leader.STOP_PCT / 100))
                    return (px / entry - 1) * 100 - M.COST * 100
                if h >= entry * (1 + dip_leader.TP_PCT / 100):
                    half, booked, peak = True, dip_leader.TP_PCT * dip_leader.TP_RATIO, h
                continue
            peak = max(peak, h)
            line = max(entry, peak * (1 - dip_leader.TRAIL_PCT / 100))
            if l <= line:
                px = min(h, line)
                return booked + (px / entry - 1) * 100 * (1 - dip_leader.TP_RATIO) - M.COST * 100
    if not ds:
        return None
    last = ds[-1][1][-1][1]
    rest = (1 - dip_leader.TP_RATIO) if half else 1.0
    return booked + (last / entry - 1) * 100 * rest - M.COST * 100


def entries(store, date, path, uni, daily, allow):
    """방식 → (시각, 코드, 이름, 가격)."""
    out = {}
    if any(t <= "143000" and any(c <= -dip_leader.INDEX_DROP for c, r in st.values()) for t, st in path):
        st = state_at(store, date, "143000", uni, daily, allow)
        if st:
            c = max(st, key=lambda k: st[k][2])
            out["현행 14:30"] = ("143000", c, st[c][0], st[c][1])
    t0 = first_hit(path)
    if not t0:
        return out
    tw = plus_min(t0, WAIT_MIN)
    if tw > LAST:
        return out
    st = state_at(store, date, tw, uni, daily, allow)
    if not st:
        return out
    c = max(st, key=lambda k: st[k][2])
    out[f"T+{WAIT_MIN} 즉시"] = (tw, c, st[c][0], st[c][1])
    for fresh, tag in ((False, "추림→반등"), (True, "추림→새반등")):
        paths = {k: bounce_path(store, date, k, tw, LAST, fresh) for k in st}
        times = sorted({tm for v in paths.values() for tm, _, _ in v})
        for r in REBOUNDS:
            for tm in times:
                hits = [(b, k, p) for k, v in paths.items() for t2, p, b in v if t2 == tm and b >= r]
                if hits:
                    b, k, p = max(hits)
                    out[f"{tag} {r:g}%"] = (tm, k, st[k][0], p)
                    break
    return out


def summary(label, xs):
    if not xs:
        return f"  {label:16s} 0건"
    n, half = len(xs), len(xs) // 2
    a = lambda v: sum(v) / len(v) if v else 0  # noqa: E731
    return (f"  {label:16s} {n:3d}건 승률 {sum(x > 0 for x in xs) / n * 100:4.0f}% · 평균 {a(xs):+.2f}% · "
            f"앞 {a(xs[:half]):+.2f}% / 뒤 {a(xs[half:]):+.2f}% · 합 {sum(xs):+.1f}%")


def main():
    from dotenv import load_dotenv
    load_dotenv(os.path.join(BASE, ".env"))
    from kis_api import KisAPI
    api = KisAPI()
    store = M.MinuteStore(api)
    daily = {k: M.etf_daily(api, c) for k, c in M.ETF.items()}
    tags = {k: P.index_tags(P.etf_rows(api, c)) for k, c in M.ETF.items()}
    days = sorted(d for d in set(daily["kospi"]) | set(daily["kosdaq"]) if d >= M.SINCE and any(
        d in daily[k] and daily[k][d][1] <= daily[k][d][0] * (1 - dip_leader.INDEX_DROP / 100) for k in M.ETF))
    watch = dip_leader.watch_codes(api, os.getenv("KIS_HTS_ID2", os.getenv("KIS_HTS_ID", "")))
    res = {}
    used = 0
    for date in days:
        drop = {k: (daily[k][date][1] / daily[k][date][0] - 1) * 100 for k in M.ETF if date in daily[k]}
        key = min(drop, key=drop.get)
        tg = tags[key].get(date, {})
        if tg.get("streak", 0) < dip_leader.MIN_STREAK or tg.get("dd20", 0) <= -dip_leader.MAX_DD20:
            continue
        used += 1
        prev = {k: daily[k][date][0] for k in M.ETF if date in daily[k]}
        path = M.index_path(store, date, prev)
        uni, dd = dip_leader.universe(date), M.daily_rows(date)
        line = []
        for flt, allow in (("전체", None), ("NEW·SUB", watch)):
            for name, (t, code, nm, price) in entries(store, date, path, uni, dd, allow).items():
                r = sim(store, code, date, t, price)
                if r is None:
                    continue
                res.setdefault((flt, name), []).append(r)
                if flt == "NEW·SUB":
                    line.append(f"{name}:{nm}@{t[:4]} {r:+.1f}%")
        print(f"{date} {key} 연속{tg.get('streak')}일 · " + " | ".join(line))
    print(f"\n상황 통과 하락일 {used}일 / 장중 -2% 하락일 {len(days)}일")
    for flt in ("전체", "NEW·SUB"):
        print(f"\n[{flt}]")
        for name in (["현행 14:30", f"T+{WAIT_MIN} 즉시"] + [f"추림→반등 {r:g}%" for r in REBOUNDS]
                     + [f"추림→새반등 {r:g}%" for r in REBOUNDS]):
            print(summary(name, res.get((flt, name), [])))


if __name__ == "__main__":
    sys.exit(main())
