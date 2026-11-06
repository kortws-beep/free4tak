"""
disclosure_stock.py — 횡보장 엔진 후보 ② '개별 특징주'를 실제 공시(재료)로 검증 (2026-11-07, 대장·제미나이)
================================================================
[이 파일이 하는 일 — 비개발자용 설명]
11-05 feature_stock.py는 '가격으로 본 특징주'(테마 조용·혼자 급등)라 진짜 재료와 펌핑을 못 갈랐음 →
11-06 모은 거래소 KIND 공시(7월~, 시각 포함)로 '그 종목만의 재료'가 터진 순간을 기준으로 본다.
  재료: 수주(단일판매ㆍ공급계약체결) · 자사주(취득 결정·취득 신탁·소각 결정) — 정정 공시는 뺌.
        실적(잠정실적)은 좋은지 나쁜지 제목만으론 몰라서 참고용으로만.
  시점: 장중(09:00~15:20) 공시 → 공시 2분 뒤 분봉 가격에 매수(반응 지연 감안)
        장전(~09:00)·장후(15:20~) 공시 → 다음 거래일(장전이면 그날) 09:05 매수
  구분: 공시 시간대(장전·오전·오후·장후) · 국면(전날 20일선 위 비율) · 매수 시점에 이미 +5%↑ 오른 상태였나
  매도: 당일종가 / 당일+3/-2.5(제미나이 짧은 방망이) / 당일정리(+3%·-3.5%·15:15) / 다음날시가 / 다음날+3(+3%·-3.5%·다음 날 15:15) / 3일종가
  시간대(제미나이): A오전 09:00~11:30 · 점심 11:30~13:00 · B오후 13:00~15:20
  수수료·세금 0.2%, 매수 미끄러짐 0.1%. 분봉은 backtest/data/minute_vol.db(VolStore) 재사용·추가.
실행: venv/bin/python backtest/disclosure_stock.py
"""
import os
import sys
import sqlite3
import datetime
import collections

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(BASE, "core"), os.path.join(BASE, "backtest"), os.path.join(BASE, "intelligence")]
import market_regime                    # noqa: E402
import dip_leader                       # noqa: E402
import sideways_pullback as SP          # noqa: E402

KIND_DB = os.path.join(BASE, "intelligence", "kind_disclosure.db")
SINCE, UNTIL = "2026-07-01", "2026-10-31"
COST, SLIP = 0.002, 0.001
BUYBACK = ("자기주식취득결정", "자기주식취득신탁계약체결결정", "주식소각결정")


def material(kind: str, title: str):
    t = title.replace(" ", "")
    if t.startswith("[정정]"):
        return None
    if kind == "수주":
        return "수주"
    if kind == "자사주" and any(w in t for w in BUYBACK):
        return "자사주"
    if kind == "실적":
        return "실적"
    return None


def slot(tm: str) -> str:
    """★ 제미나이 관전 포인트 — A 오전(09:00~11:30) · 점심(11:30~13:00) · B 오후(13:00~15:20)."""
    if tm < "09:00":
        return "장전"
    if tm < "11:30":
        return "A오전"
    if tm < "13:00":
        return "점심"
    return "B오후" if tm < "15:20" else "장후"


def events():
    c = sqlite3.connect(KIND_DB)
    seen, out = set(), []
    for date, tm, code, title, kind in c.execute(
            "SELECT date, time, code, title, kind FROM disc WHERE code IS NOT NULL AND date BETWEEN ? AND ? "
            "ORDER BY date, time", (SINCE, UNTIL)):
        m = material(kind, title)
        if not m or (date, code, m) in seen:
            continue
        seen.add((date, code, m))
        out.append((date, tm, code, m))
    c.close()
    return out


def prev_close(code: str, date: str):
    c = sqlite3.connect(dip_leader.DAILY_DB)
    try:
        name = c.execute("SELECT name FROM kr_stock_master WHERE code=?", (code,)).fetchone()
        if not name:
            return None
        r = c.execute("SELECT close_price FROM kr_stock_daily_data WHERE stock_name=? AND date<? ORDER BY date DESC LIMIT 1",
                      (name[0], date)).fetchone()
        return r[0] if r else None
    finally:
        c.close()


def after(bars, t):
    return [b for b in bars if b[0] > t]


def sim_day(bars, entry, end="151500", stop=3.5):
    """그 날 분봉으로 +3%/-stop%/end 정리."""
    for tm, p, h, l, v in bars:
        if tm >= end:
            return (p / entry - 1) * 100
        if l <= entry * (1 - stop / 100):
            return (min(h, entry * (1 - stop / 100)) / entry - 1) * 100
        if h >= entry * 1.03:
            return 3.0
    return (bars[-1][1] / entry - 1) * 100 if bars else None


def main():
    from dotenv import load_dotenv
    load_dotenv(os.path.join(BASE, ".env"))
    from kis_api import KisAPI
    store = SP.VolStore(KisAPI())
    c = sqlite3.connect(dip_leader.DAILY_DB)
    ds = [d for (d,) in c.execute("SELECT DISTINCT date FROM kr_stock_daily_data WHERE date >= '2026-05-01' ORDER BY date")]
    c.close()
    br = {d: v[0] for d, v in market_regime.compute(dates=ds).items()}
    bd = sorted(br)
    res = collections.defaultdict(list)
    evs = events()
    print(f"재료 공시 {len(evs)}건", flush=True)
    for i, (date, tm, code, mat) in enumerate(evs):
        sl = slot(tm)
        days = store.days_from(code, date, 4)
        if not days:
            continue
        if sl in ("장전", "A오전", "점심", "B오후") and days[0][0] == date:
            d0, bars0 = days[0]
            rest = days[1:]
            t_in = "090500" if sl == "장전" else (datetime.datetime.strptime(tm, "%H:%M") + datetime.timedelta(minutes=2)).strftime("%H%M00")
        else:                                            # 장후(또는 그날 분봉 없음) → 다음 거래일 09:05
            nxt = [x for x in days if x[0] > date]
            if not nxt:
                continue
            d0, bars0 = nxt[0]
            rest = nxt[1:]
            t_in = "090500"
        aft = after(bars0, t_in)
        if not aft:
            continue
        price = aft[0][1]
        pc = prev_close(code, d0)
        if not pc:
            continue
        pre = (price / pc - 1) * 100                     # 매수 시점에 이미 오른 정도
        prior = [d for d in bd if d < d0]
        g = SP.bucket(br[prior[-1]]) if prior else "?"
        entry = price * (1 + SLIP)
        out = {"당일종가": (aft[-1][1] / entry - 1) * 100,
               "당일+3/-2.5": sim_day(aft[1:], entry, stop=2.5),
               "당일정리": sim_day(aft[1:], entry)}
        if rest:
            out["다음날시가"] = (rest[0][1][0][1] / entry - 1) * 100
            out["다음날+3"] = sim_day(rest[0][1], entry)
            out["3일종가"] = (rest[min(1, len(rest) - 1)][1][-1][1] / entry - 1) * 100
        tag = "이미+5%↑" if pre >= 5 else "덜 오름"
        for rule, r in out.items():
            if r is None:
                continue
            r -= COST * 100
            for key in ((mat, sl, rule), (mat, sl + "·" + tag, rule), (mat, g, rule)):
                res[key].append((d0, r))
        if i % 100 == 0:
            print(f"  {i}/{len(evs)} · API {store.calls}", flush=True)
    for mat in ("수주", "자사주", "실적"):
        print(f"\n===== {mat} =====")
        keys = sorted({k[1] for k in res if k[0] == mat})
        for k in keys:
            for rule in ("당일종가", "당일+3/-2.5", "당일정리", "다음날시가", "다음날+3", "3일종가"):
                xs = [r for _, r in sorted(res.get((mat, k, rule), []))]
                if len(xs) >= 8:
                    print(SP.line(f"{k} {rule}", xs))


if __name__ == "__main__":
    sys.exit(main())
