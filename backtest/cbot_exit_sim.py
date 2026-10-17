"""
cbot_exit_sim.py — cbot 매도 규칙 후보를 30초 시세 흐름 위에서 다시 돌려보기 (2026-10-17)
================================================================
[이 파일이 하는 일 — 비개발자용 설명]

cbot_audit.py 결과(120일 96건): 진 매매 47건 중 31건(66%)이 한 번은 +2%↑ 올랐다가 손실로 끝남
(최고 +2~5% 갔던 24건 -73만), 하루 넘게 들면 PF<1. → 진입은 살리고 청산만 손보면 되는지 확인한다.
매매마다 매수~첫 실제 매도 사이의 30초 시세를 순서대로 따라가며, 규칙이 실제 매도보다 먼저 걸리면
그 가격에 전량 매도한 것으로 바꿔 손익을 다시 계산(수수료 왕복 0.1%). 안 걸리면 실제 결과 그대로.
  P. 보호선 — 한 번 +X% 찍은 뒤 +Y%까지 내려오면 매도      (X 2·3·4 / Y +0.3·+1)
  R. 짧은 추적 — +X% 찍은 뒤 고점에서 Z% 내려오면 매도       (X 3 / Z 2·3, X 5 / Z 3)
  T. 시간 정리 — 산 지 H시간 지났는데 +1% 미만이면 매도      (H 24·48)
각 규칙: 바뀐 건수·나아진/나빠진 건수·손익 변화 + 앞·뒤 절반 기간 모두 나아졌는지(우연 걸러내기).
★ 크게 오른 매매를 중간에 잘라버리는 손해까지 같이 계산되므로, 합계가 +면 그 손해를 넘는 효과.
주문·저장 없음. 실행:  python backtest/cbot_exit_sim.py [최근 N일, 기본 120]
"""
import os
import sys
import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cbot_audit as A   # noqa: E402
from cbot_crash_review import _find, _dt   # noqa: E402


def path(ticks: A.Ticks, x: dict) -> list:
    """매수 ~ 첫 실제 매도(분할 익절 포함) 사이 [(시각, 가격)] — 그 뒤는 실제 매매가 이미 일부 팔려 비교 불가."""
    if not ticks.conn:
        return []
    end = x["sells"][0][0]
    rows = ticks.conn.execute("SELECT ts, price FROM price_ticks WHERE market=? AND ts>? AND ts<? ORDER BY ts",
                              (x["market"], x["buy"].isoformat(timespec="seconds"),
                               end.isoformat(timespec="seconds"))).fetchall()
    return [(_dt(t), p) for t, p in rows]


def protect(X: float, Y: float):
    def rule(x, pts):
        armed = False
        for t, p in pts:
            r = (p / x["bp"] - 1) * 100
            armed = armed or r >= X
            if armed and r <= Y:
                return p
        return None
    return rule


def trail(X: float, Z: float):
    def rule(x, pts):
        peak = None
        for t, p in pts:
            if peak is None and (p / x["bp"] - 1) * 100 >= X:
                peak = p
            if peak is not None:
                peak = max(peak, p)
                if p <= peak * (1 - Z / 100):
                    return p
        return None
    return rule


def time_exit(H: float, min_pct: float = 1.0):
    def rule(x, pts):
        lim = x["buy"] + datetime.timedelta(hours=H)
        for t, p in pts:
            if t >= lim:
                return p if (p / x["bp"] - 1) * 100 < min_pct else None
        return None
    return rule


RULES = [(f"P. +{X:g}% 찍은 뒤 +{Y:g}%로 내려오면", protect(X, Y)) for X in (2, 3, 4) for Y in (0.3, 1.0)] + \
        [(f"R. +{X:g}% 찍은 뒤 고점 -{Z:g}%", trail(X, Z)) for X, Z in ((3, 2), (3, 3), (5, 3))] + \
        [(f"T. {H}시간 지나 +1% 미만이면", time_exit(H)) for H in (24, 48)]


def simulate(trades: list, paths: dict, rule) -> dict:
    half = trades[len(trades) // 2]["buy"] if trades else None
    out = {"changed": 0, "better": 0, "worse": 0, "delta": 0.0, "a": 0.0, "b": 0.0, "cut_big": 0}
    for x in trades:
        p = rule(x, paths.get(id(x), []))
        if p is None:
            continue
        new = (p / x["bp"] - 1) * x["cost"] - x["cost"] * A.FEE
        d = new - x["net"]
        out["changed"] += 1
        out["better" if d > 0 else "worse"] += 1
        out["delta"] += d
        out["a" if x["buy"] < half else "b"] += d
        if d < 0 and x["rate"] >= 5:
            out["cut_big"] += 1
    return out


def report(trades: list, paths: dict, days: int) -> str:
    base = sum(x["net"] for x in trades)
    cov = sum(1 for x in trades if paths.get(id(x)))
    half = trades[len(trades) // 2]["buy"] if trades else None
    L = [f"🧪 cbot 매도 규칙 시뮬 — 최근 {days}일 {len(trades)}건(30초 시세 있는 {cov}건) · 지금 순손익 {base / 1e4:+.1f}만"
         + (f" · 앞·뒤 절반 기준 {half:%m-%d}" if half else "")]
    res = []
    for name, rule in RULES:
        s = simulate(trades, paths, rule)
        both = s["a"] > 0 and s["b"] > 0
        res.append((s["delta"], name, s, both))
        L.append(f"   {name:<24} 바뀜 {s['changed']:>3}(나아짐 {s['better']} · 나빠짐 {s['worse']}, 그중 +5%↑ 이긴 걸 자름 "
                 f"{s['cut_big']}) · {s['delta'] / 1e4:+6.1f}만 → {(base + s['delta']) / 1e4:+.1f}만 "
                 f"(앞 {s['a'] / 1e4:+.1f} · 뒤 {s['b'] / 1e4:+.1f}) {'✅' if both else '❌'}")
    good = sorted([r for r in res if r[3] and r[0] > 0], key=lambda r: -r[0])
    L.append("■ 후보(앞뒤 둘 다 나아짐, 효과 큰 순): " + (" / ".join(f"{n} {d / 1e4:+.1f}만" for d, n, _, _ in good[:3])
                                                   if good else "없음"))
    L.append("   ※ 실제 매도 전까지만 따라감(분할 익절 뒤 잔량은 실제대로). 30초 시세라 급락 땐 실제 체결가가 더 나쁠 수 있음")
    return "\n".join(L)


if __name__ == "__main__":
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 120
    trades = A.load(_find("cbot_trade_history.db"), days)
    ticks = A.Ticks(_find("coin_price_ticks.db"))
    paths = {id(x): path(ticks, x) for x in trades}
    print(report(trades, paths, days))
