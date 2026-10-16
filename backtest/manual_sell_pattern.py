"""
manual_sell_pattern.py — 대장 수동매도 vs sbot 봇 매도 비교 (2026-10-17, 로드맵 6번)
================================================================
[이 파일이 하는 일 — 비개발자용 설명]

같은 sbot 계좌에서 봇이 판 97건은 -362.8만, 대장이 직접 판 17건은 +48.1만. 대장은 뭘 다르게 했나?
sbot_trade_history.db(매매) + 리나 일봉 DB(kr_theme_finance.db)로 매도 한 건마다:
  · 판 시각대(장전 08시대 / 시가 09:00~09:10 / 오전 / 오후)
  · 보유 중 최고가(일봉 고가) 대비 판 가격 — 고점에서 얼마나 내주고 팔았나
  · 판 날 시가 갭(전날 종가 대비) — 갭 상승에 판 건가
  · 판 뒤 5·10거래일 종가(판 가격 대비) — 너무 일찍 판 건가, 잘 판 건가
  · 보유 일수, 판 수익률
수동 vs 봇(매도 사유별) 평균 비교 → 마지막에 "대장식 규칙"을 봇 매매 전체에 덮어씌운 시뮬레이션:
  A. 보유 중 시가 갭 +g% 이상이고 시가가 매수가 위면 그 시가에 매도  (g = 2·3·5)
  B. 보유 중 시가 기준 수익 +p% 이상이면 그 시가에 매도               (p = 3·5·8)
  (시가는 그날 첫 가격이라 아직 보유 중인 날이면 항상 팔 수 있음. 판 날도 09:10 뒤에 팔았으면 그날 시가 포함. 주문·저장 없음)
실행:  python backtest/manual_sell_pattern.py [최근 N일, 기본 120]
"""
import os
import sys
import statistics
from bisect import bisect_left

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "backtest"))
sys.path.insert(0, os.path.join(BASE, "core"))
import sbot_review as srv     # noqa: E402
import swing_backtest as sb   # noqa: E402

COST = srv.COST


def is_manual(t: dict) -> bool:
    return "수동" in t["reason"]


def slot(t: dict) -> str:
    hm = t["sell"].strftime("%H%M")
    return ("장전" if hm < "0900" else "시가(~09:10)" if hm <= "0910" else "오전" if hm < "1200" else "오후")


def enrich(trades: list, daily: dict) -> list:
    """일봉으로 보유 중 고가·판 날 갭·판 뒤 5/10일 — 일봉 없는 종목은 None."""
    for t in trades:
        rows = daily.get(t["name"]) or []
        dates = [r[0] for r in rows]
        bd, sd = t["buy"].date().isoformat(), t["sell"].date().isoformat()
        i0, i1 = bisect_left(dates, bd), bisect_left(dates, sd)
        t["peak"] = t["gap"] = t["a5"] = t["a10"] = None
        t["hold_rows"] = []
        if not rows or i1 >= len(rows) or dates[i1] != sd:
            continue
        span = rows[i0:i1 + 1]
        t["peak"] = (max(r[2] for r in span) / t["bp"] - 1) * 100
        if i1 > 0:
            t["gap"] = (rows[i1][1] / rows[i1 - 1][4] - 1) * 100
        for k in (5, 10):
            if i1 + k < len(rows):
                t[f"a{k}"] = (rows[i1 + k][4] / t["sp"] - 1) * 100
        # 시뮬레이션용: 산 다음 날 ~ 판 날(09:10 뒤에 팔았으면 그날 시가까지)의 (시가, 전날 종가)
        end = i1 + 1 if t["sell"].strftime("%H%M") > "0910" else i1
        t["hold_rows"] = [(rows[j][1], rows[j - 1][4]) for j in range(max(i0 + 1, 1), end)]
    return trades


def _avg(xs):
    xs = [x for x in xs if x is not None]
    return (statistics.mean(xs), len(xs)) if xs else (None, 0)


def _f(v, s="%"):
    return f"{v:+.1f}{s}" if v is not None else "-"


def profile(label: str, ts: list) -> str:
    if not ts:
        return f"   {label}: 0건"
    krw = sum(t["krw"] for t in ts)
    win = sum(1 for t in ts if t["krw"] > 0) / len(ts) * 100
    pk, _ = _avg(t["peak"] for t in ts)
    gb, _ = _avg(t["peak"] - t["rate"] if t["peak"] is not None else None for t in ts)
    gp, _ = _avg(t["gap"] for t in ts)
    a5, n5 = _avg(t["a5"] for t in ts)
    a10, _ = _avg(t["a10"] for t in ts)
    hd = statistics.mean(t["hold"] for t in ts)
    return (f"   {label}: {len(ts)}건 · 승률 {win:.0f}% · 합계 {krw / 1e4:+,.1f}만 · 판 수익률 "
            f"{_f(statistics.mean(t['rate'] for t in ts))} · 보유 {hd:.1f}일\n"
            f"      보유 중 최고 {_f(pk)} → 고점서 내준 폭 {_f(gb)} · 판 날 시가 갭 {_f(gp)} · "
            f"판 뒤 5일 {_f(a5)} / 10일 {_f(a10)} ({n5}건)")


def simulate(trades: list, rule, label: str) -> str:
    """rule(open, prev_close, bp) → True면 그 시가에 매도. 실제 매도보다 먼저 걸린 것만 바꿈."""
    base = sum(t["krw"] for t in trades)
    new, changed, better = 0.0, 0, 0
    for t in trades:
        hit = next((o for o, pc in t["hold_rows"] if rule(o, pc, t["bp"])), None)
        if hit is None:
            new += t["krw"]
            continue
        k = (hit - t["bp"]) * t["qty"] - t["bp"] * t["qty"] * COST
        changed += 1
        better += k > t["krw"]
        new += k
    return (f"   {label}: 바뀐 매도 {changed}건(그중 나아짐 {better}) · 합계 {base / 1e4:+,.1f}만 → "
            f"{new / 1e4:+,.1f}만 ({(new - base) / 1e4:+,.1f}만)")


def report(trades: list, days: int) -> str:
    man = [t for t in trades if is_manual(t)]
    bot = [t for t in trades if not is_manual(t)]
    cov = sum(1 for t in trades if t["peak"] is not None)
    L = [f"🖐️ sbot 수동매도 vs 봇 매도 — 최근 {days}일, 매도 {len(trades)}건(수동 {len(man)} · 봇 {len(bot)}) · "
         f"일봉 맞춘 건 {cov}"]
    L.append("■ 한눈에")
    L.append(profile("대장 수동", man))
    L.append(profile("봇 전체", bot))
    L.append("■ 봇 매도 사유별")
    groups = {}
    for t in bot:
        groups.setdefault(srv.reason_group(t["reason"]), []).append(t)
    for g, ts in sorted(groups.items(), key=lambda x: -len(x[1])):
        L.append(profile(g, ts))
    L.append("■ 판 시각대 — 수동 / 봇")
    for s_ in ("장전", "시가(~09:10)", "오전", "오후"):
        m = [t for t in man if slot(t) == s_]
        b = [t for t in bot if slot(t) == s_]
        L.append(f"   {s_:<9} 수동 {len(m)}건 {sum(t['krw'] for t in m) / 1e4:+,.1f}만 · "
                 f"봇 {len(b)}건 {sum(t['krw'] for t in b) / 1e4:+,.1f}만")
    L.append("■ 수동매도 하나씩")
    for t in man:
        L.append(f"   {t['sell']:%m-%d %H:%M} {t['name']} {_f(t['rate'])} · 보유 {t['hold']}일 · 최고 {_f(t['peak'])}"
                 f" · 갭 {_f(t['gap'])} · 판 뒤 5일 {_f(t['a5'])}")
    L.append("■ 대장식 규칙을 봇 매매에 덮어씌우면 (보유 중 시가에 매도)")
    for g in (2, 3, 5):
        L.append(simulate(bot, lambda o, pc, bp, g=g: o >= pc * (1 + g / 100) and o > bp, f"A. 시가 갭 +{g}%↑·수익 중"))
    for p in (3, 5, 8):
        L.append(simulate(bot, lambda o, pc, bp, p=p: o >= bp * (1 + p / 100), f"B. 시가 수익 +{p}%↑"))
    L.append("   ※ 수동 17건은 표본이 작음 — 규칙 후보는 봇 매매 시뮬에서 나아지는 것만. 수동매도 가격은 봇이 감지한"
             " 시세라 실제 체결가와 조금 다를 수 있음")
    return "\n".join(L)


if __name__ == "__main__":
    n = next((int(a) for a in sys.argv[1:] if a.isdigit()), 120)
    trades = srv.load_trades(srv.dr._find("sbot_trade_history.db"), n)
    print(report(enrich(trades, sb.load_daily(srv.THEME_DB, n + 40)), n))
