"""
intraday_decline_detector.py — 장중 '하락폭을 키워가는 날' 감지 규칙 검증 (2026-10-19)
================================================================
[이 파일이 하는 일 — 비개발자용 설명]
대장: "가장 경계할 날은 아침에 강하게 빠지는 장보다 갈수록 하락폭을 키우는 장 — 전일 데이터론 구분 어려움.
cbot 급락 감지기처럼 주식에도 있어야." 코스피200(KODEX200 069500)·코스닥150(KODEX 코스닥150 229200) 1분봉으로
날마다 아래 규칙을 10:00~14:30 사이 1분씩 확인해, 처음 울린 시각·가격을 기록한다(규칙은 돌리기 전에 고정).
  R1  시가 대비 -1% 아래 + 당일 신저가 갱신
  R2  시가 대비 -1.5% 아래
  R3  10:30 이후, 시가 대비 -1% 아래 + 09:30까지의 저가도 깨짐
'하락 확대 날' = 그 ETF가 시가→종가 -1.5%↓ & 저가 근처(하루 범위 하위 15%) 마감.
보는 것: ① 하락 확대 날을 몇 번 잡았나·울린 뒤에도 하락이 얼마나 남았나(=일찍 잡은 가치)
         ② 평범한 날 헛경보 몇 번·울린 뒤 오히려 올랐나(=멈춰서 놓친 비용)
주문 없음. 1분봉은 daybot_replay 저장소(backtest/data/minute_cache.db) 사용, 없으면 한투 조회.
실행:  python backtest/intraday_decline_detector.py [시작일, 기본 2026-04-01]
"""
import os
import sys
import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import daybot_replay as R   # noqa: E402

ETFS = {"069500": "코스피200", "229200": "코스닥150"}


def _r1(bars, i, o, lo_sofar, lo930):
    t, p, h, l = bars[i]
    return l <= o * 0.99 and l < lo_sofar


def _r2(bars, i, o, lo_sofar, lo930):
    return bars[i][3] <= o * 0.985


def _r3(bars, i, o, lo930_unused=None, lo930=None):
    t, p, h, l = bars[i]
    return t >= "103000" and l <= o * 0.99 and lo930 is not None and l < lo930


RULES = {"R1 시가-1%+신저가": _r1, "R2 시가-1.5%": _r2, "R3 10:30후 시가-1%+09:30저가깨짐": _r3}


def day_stats(bars: list) -> dict:
    o = bars[0][1]
    c = bars[-1][1]
    hi = max(b[2] for b in bars)
    lo = min(b[3] for b in bars)
    pos = (c - lo) / (hi - lo) if hi > lo else 0.5
    return {"open": o, "close": c, "oc": (c / o - 1) * 100, "trend_down": (c / o - 1) * 100 <= -1.5 and pos <= 0.15}


def scan(bars: list, rule) -> tuple:
    """(울린 시각, 그 분 가격) 또는 None — 10:00~14:30만 확인."""
    o = bars[0][1]
    lo_sofar = min(b[3] for b in bars[:1])
    lo930 = None
    for i, (t, p, h, l) in enumerate(bars):
        if t <= "093000":
            lo930 = l if lo930 is None else min(lo930, l)
        if "100000" <= t <= "143000" and rule(bars, i, o, lo_sofar, lo930):
            return t, p
        lo_sofar = min(lo_sofar, l)
    return None


def trading_days(start: str) -> list:
    d, end, out = datetime.date.fromisoformat(start), datetime.date.today(), []
    while d <= end:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += datetime.timedelta(days=1)
    return out


def report(store, start: str) -> str:
    L = []
    for code, nm in ETFS.items():
        days = [(d, store.day(code, d)) for d in trading_days(start)]
        days = [(d, b) for d, b in days if len(b) > 200]
        tds = [d for d, b in days if day_stats(b)["trend_down"]]
        L.append(f"■ {nm}({code}) {len(days)}거래일 · 하락 확대 날 {len(tds)}일: {', '.join(x[5:] for x in tds)}")
        for rn, rule in RULES.items():
            hit, miss_cost, false, false_ret = [], [], 0, []
            for d, b in days:
                st, f = day_stats(b), scan(b, rule)
                if st["trend_down"]:
                    if f:
                        hit.append((st["close"] / f[1] - 1) * 100)
                elif f:
                    false += 1
                    false_ret.append((st["close"] / f[1] - 1) * 100)
            nd = len(days) - len(tds)
            L.append(f"   {rn:<28} 하락 확대 날 {len(hit)}/{len(tds)}일 잡음 · 울린 뒤 남은 하락 평균 "
                     f"{(sum(hit) / len(hit) if hit else 0):+.2f}% | 평범한 날 헛경보 {false}/{nd}일 · 울린 뒤 종가까지 평균 "
                     f"{(sum(false_ret) / len(false_ret) if false_ret else 0):+.2f}%")
    return "\n".join(L)


if __name__ == "__main__":
    start = next((a for a in sys.argv[1:] if a[:2] == "20"), "2026-04-01")
    sys.path.insert(0, os.path.join(R.BASE, "core"))
    from dotenv import load_dotenv
    load_dotenv(os.path.join(R.BASE, ".env"))
    from kis_api import KisAPI
    store = R.MinuteStore(KisAPI())
    print(f"📉 장중 하락 확대 감지 규칙 검증 — {start}~ (10:00~14:30 확인)")
    print(report(store, start))
    print(f"   한투 호출 {store.calls}회")
    print("※ 울린 뒤 남은 하락이 클수록(더 음수) 일찍 잡은 것 · 헛경보 뒤 종가가 +면 멈춰서 놓친 비용")
