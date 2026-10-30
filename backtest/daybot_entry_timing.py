"""
daybot_entry_timing.py — 데이봇 매수 타점 비교 (2026-10-30, 대장 "데이봇 매수타점 테스트")
================================================================
[이 파일이 하는 일 — 비개발자용 설명]
10-27~30 하락장 슬롯 6번 매수 중 5번 손절 — 전부 '직전 5분 +2~4% 급등한 꼭대기'에서 삼(검색식에 잡히는 순간이 급등 직후).
유일한 익절(한미반도체)은 직전 5분이 옆으로 기던 자리. → 대장 "한 템포 오르고 조금 눌릴 때".
10-06부터 데이봇 후보 기록 중 '매수했거나 하락장이라 쉰'(= 필터 다 통과한) 09:40 전 후보마다, 분봉으로 4가지 타점을 비교:
  R0 지금 — 잡힌 가격에 바로
  R1 직전 5분 상승이 +2% 넘으면 건너뜀(아니면 R0)
  R2 잡힌 뒤 고점에서 -1.5% 눌리면 그 값에 지정가(20분 안에 안 눌리면 패스)
  R3 -1.5% 눌린 뒤 1분봉 종가가 직전 봉 고가를 넘을 때(고개 듦) 그 종가에(30분 안)
결과: 산 뒤 +2.5%(데이봇 수익 시작선) / -3.5%(손절) 어디 먼저 — 승률과 '기대값'(+2.5/-3.5/둘다 아니면 15:20 종가).
앞 절반·뒤 절반 기간 따로.
실행: venv/bin/python backtest/daybot_entry_timing.py
"""
import os
import sys
import sqlite3

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(BASE, "core"), os.path.join(BASE, "backtest")]
DB = os.path.join(BASE, "daybot_trade_history.db")
UP, DN = 2.5, 3.5
RUN5_MAX, PULL, PULL_WAIT, TURN_WAIT = 2.0, 1.5, 20, 30


def first_touch(entry: float, bars: list) -> str:
    for b in bars:
        if b["low"] <= entry * (1 - DN / 100):
            return "loss"
        if b["high"] >= entry * (1 + UP / 100):
            return "win"
    return "none"


def result_pct(entry: float, bars: list) -> float:
    r = first_touch(entry, bars)
    if r == "win":
        return UP
    if r == "loss":
        return -DN
    return (bars[-1]["price"] / entry - 1) * 100 if bars else 0.0


def entries(t0: str, p0: float, bars: list) -> dict:
    """bars = 그날 분봉(시간순). → {규칙: (진입 인덱스 다음부터 평가할 봉 목록, 진입가) 또는 None}."""
    before = [b for b in bars if b["time"] <= t0]
    after = [b for b in bars if b["time"] > t0]
    base = before[-6]["price"] if len(before) >= 6 else (before[0]["price"] if before else p0)
    run5 = (p0 / base - 1) * 100 if base else 0.0
    out = {"R0": (after, p0), "R1": None if run5 > RUN5_MAX else (after, p0), "R2": None, "R3": None}
    hi, pulled = p0, None
    for i, b in enumerate(after[:TURN_WAIT]):
        if pulled is None:
            if b["low"] <= hi * (1 - PULL / 100):
                pulled = i
                if i < PULL_WAIT:
                    out["R2"] = (after[i + 1:], hi * (1 - PULL / 100))
                continue
            hi = max(hi, b["high"])
        elif i > pulled and b["price"] > after[i - 1]["high"]:
            out["R3"] = (after[i + 1:], b["price"])
            break
    out["run5"] = run5
    return out


def candidates(db: str = DB) -> list:
    conn = sqlite3.connect(db)
    rows = conn.execute("""SELECT substr(ts,1,10), code, MIN(stock_name), MIN(substr(ts,12,8)), price FROM candidate_log
                           WHERE substr(ts,12,5) < '09:40' AND (bought=1 OR skip_reason='하락장쉬기')
                           GROUP BY substr(ts,1,10), code ORDER BY 1, 4""").fetchall()
    conn.close()
    return rows


def main():
    from dotenv import load_dotenv
    load_dotenv(os.path.join(BASE, ".env"))
    from kis_api import KisAPI
    import premarket_sector_review as P
    api = KisAPI()
    rows = candidates()
    dates = sorted({r[0] for r in rows})
    mid = dates[len(dates) // 2]
    res = {k: [] for k in ("R0", "R1", "R2", "R3")}
    for d, code, name, t, price in rows:
        bars = P.day_bars(api, code, d.replace("-", ""))
        if not bars or not price:
            continue
        e = entries(t.replace(":", "")[:6], price, bars)
        for k in res:
            if e[k] and e[k][0]:
                res[k].append((d, result_pct(e[k][1], e[k][0]), first_touch(e[k][1], e[k][0])))
    print(f"🎯 데이봇 매수 타점 비교 — 후보 {len(rows)}건 · {len(dates)}일({dates[0]}~{dates[-1]}) · 앞/뒤 경계 {mid}")
    names = {"R0": "지금(바로)", "R1": "직전5분 +2% 넘으면 패스", "R2": "고점 -1.5% 눌림 지정가", "R3": "눌린 뒤 고개 들 때"}
    for k, xs in res.items():
        for lab, ys in (("전체", xs), ("앞", [x for x in xs if x[0] < mid]), ("뒤", [x for x in xs if x[0] >= mid])):
            n = len(ys)
            if not n:
                print(f"   {names[k]:<18} {lab}: 0건"); continue
            w = sum(1 for y in ys if y[2] == "win"); l_ = sum(1 for y in ys if y[2] == "loss")
            print(f"   {names[k]:<18} {lab}: {n:>3}건 · +2.5% 먼저 {w}({w / n * 100:.0f}%) · -3.5% 먼저 {l_}({l_ / n * 100:.0f}%) · "
                  f"기대값 {sum(y[1] for y in ys) / n:+.2f}%/건 · 합 {sum(y[1] for y in ys):+.1f}%")


if __name__ == "__main__":
    main()
