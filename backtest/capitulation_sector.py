"""
capitulation_sector.py — 투매일 '이길 섹터' 고르기 검증 (2026-10-14)
================================================================
[이 파일이 하는 일 — 비개발자용 설명]

10-13 투매(신저가 51%) → 10-14 반등에서 대장이 고른 하락 전 주목주 10개를 보니, 다음 날 고가
+7%를 넘은 건 거의 1섹터(2차전지) 전부였고 2섹터(부품·광통신)는 하나뿐. 이긴 섹터는
  · 하락 구간에 덜 빠졌고(평균 -9% vs -13.6%)
  · 바닥날 오후에 저가 대비 두 배로 세게 튀었다(저가→종가 평균 +9% vs +4.6%)
둘 다 바닥날 14:30쯤 장중에 보이는 숫자 → 그날 오후에 살 섹터를 고르는 규칙이 될 수 있나?
과거 투매일(그날 20일 신저가 종목 비율 40%+)마다 관심그룹을 줄 세워 확인한다.

  그룹 점수(그룹 안 거래대금 상위 3종목 = 1·2·3등주 기준, 대장 "123등주"):
    오후반등 = 바닥날 저가→종가 평균 · 낙폭 = 하락 직전 종가→바닥날 종가 평균
    결합 = 오후반등 순위 + 낙폭 순위(덜 빠진 게 위) 합이 작은 순
  결과 = 다음 날 고가가 바닥날 종가 대비 +3·5·7% 닿았나(= 바닥날 종가 매수, 다음 날 목표가 지정가
         매도 체결) — 대장: "종가 말고 장중 최고가로 봐야 의미가 있다". 다음 날 종가도 참고로.
  비교 = 1등 그룹(결합/오후반등만/낙폭만) vs 꼴찌 그룹(결합) vs 전 그룹 대표주 vs 시장(거래대금 상위 300)
한계: 관심그룹은 '지금' 구성(최근 뜬 종목 위주로 만들어져 과거 성적이 부풀 수 있음), 고점 시각은 일봉으론
      모름(09:45 전에 나왔는지는 분봉으로 따로), 투매일 표본 수가 적음.
실행:  python backtest/capitulation_sector.py [--nl 40] [--days 300] [--top-groups 1]
"""
import os
import sys
import statistics

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "backtest"))
import swing_backtest as sb      # noqa: E402
import rebound_backtest as rb    # noqa: E402
import bottom_signal as bs       # noqa: E402
import rebound_case as rc        # noqa: E402

TARGETS = (3, 5, 7)
LEADERS = 3            # 그룹 안 거래대금 상위 3종목(1·2·3등주)
MIN_MEMBERS = 3


def cap_days(table: dict, lows: dict, nl: float) -> list:
    """투매일 — 20일 신저가 비율 nl% 이상, 다음 거래일이 있는 날."""
    dates = sorted(table)
    return [d for i, d in enumerate(dates[:-1]) if lows.get(d, 0) >= nl]


def stock_row(rows: dict, P: str, Bd: str, R: str):
    """rows {date: (o, h, l, c, val)} → (낙폭%, 오후반등%, 다음날 고가%, 다음날 종가%, 거래대금,
    다음날 시가%, 다음날 저가%, 바닥날 저가%) 또는 None. 다음날·바닥날 저가 %는 바닥날 종가(매수가) 대비."""
    if not all(d in rows for d in (P, Bd, R)) or not rows[P][3] or not rows[Bd][2]:
        return None
    o, h, l, c, val = rows[Bd]
    base = c
    pct = lambda x: (x / base - 1) * 100
    return ((c / rows[P][3] - 1) * 100, (c / l - 1) * 100, pct(rows[R][1]), pct(rows[R][3]), val,
            pct(rows[R][0]), pct(rows[R][2]), pct(l))


def score_groups(groups: dict, rows: dict, P: str, Bd: str, R: str) -> list:
    """[{group, drop, aft, members:[(name, row)]}] — 대표주(거래대금 상위 LEADERS) 기준."""
    out = []
    for g, members in groups.items():
        if g.strip().lower() in sb.NEW_GROUP_NAMES:
            continue                           # NEW는 섹터가 아니라 신규추천 모음
        rs = [(n, stock_row(rows[n], P, Bd, R)) for _, n in members if n in rows]
        rs = [(n, r) for n, r in rs if r]
        if len(rs) < MIN_MEMBERS:
            continue
        lead = sorted(rs, key=lambda x: -x[1][4])[:LEADERS]
        out.append({"group": g, "drop": statistics.mean(r[0] for _, r in lead),
                    "aft": statistics.mean(r[1] for _, r in lead), "members": lead})
    if not out:
        return out
    by_aft = sorted(out, key=lambda x: -x["aft"])
    by_drop = sorted(out, key=lambda x: -x["drop"])
    for i, x in enumerate(by_aft):
        x["r_aft"] = i
    for i, x in enumerate(by_drop):
        x["r_drop"] = i
    return sorted(out, key=lambda x: (x["r_aft"] + x["r_drop"], x["r_aft"]))


COST = 0.3     # % 왕복 수수료·세금·슬리피지


def hits(rs: list) -> dict:
    """rs [(name, row)] → 목표 도달률·다음날 종가 평균 + 실제 손익(★ 10-14: 목표가 지정가 매도,
    못 닿으면 다음 날 종가 매도 — 비용 차감 평균 %)."""
    if not rs:
        return {"n": 0}
    out = {"n": len(rs), "close": statistics.mean(r[3] for _, r in rs), "high": statistics.median(r[2] for _, r in rs)}
    for t in TARGETS:
        out[t] = sum(r[2] >= t for _, r in rs) / len(rs) * 100
        out[f"pnl{t}"] = statistics.mean((t if r[2] >= t else r[3]) for _, r in rs) - COST
        # ★ 10-14: 못 닿은 종목이 종가에 평균 -8%로 끝나 이익을 다 까먹음 → 빠지는 규칙 두 가지
        out[f"gap{t}"] = statistics.mean(exit_gap(r, t) for _, r in rs) - COST
        out[f"stop{t}"] = statistics.mean(exit_stop(r, t) for _, r in rs) - COST
    return out


def exit_gap(r, t: float) -> float:
    """다음 날 시가가 매수가 아래로 시작하면 시가에 정리, 아니면 +t 지정가 익절·못 닿으면 종가."""
    hi, cl, op = r[2], r[3], r[5]
    if op < 0:
        return op
    return t if hi >= t else cl


def exit_stop(r, t: float) -> float:
    """바닥날 저가를 손절선 — 다음 날 그 아래로 가면 정리(시가부터 아래면 시가, 같은 날 익절선도 닿으면
    손절 먼저로 불리하게). 아니면 +t 익절·못 닿으면 종가."""
    hi, cl, op, lo, stop = r[2], r[3], r[5], r[6], r[7]
    if op <= stop:
        return op
    if lo <= stop:
        return stop
    return t if hi >= t else cl


def fmt(h: dict) -> str:
    if not h["n"]:
        return "     0종목"
    return (f"{h['n']:>5}종목 · " + " · ".join(f"+{t}% {h[t]:3.0f}%" for t in TARGETS)
            + f" · 고가 중앙 {h['high']:+.1f}% · 다음날 종가 {h['close']:+.2f}%"
            + f"\n{'':<16}손익(+5%/+7% 익절) 못 닿으면 종가 {h['pnl5']:+.2f}/{h['pnl7']:+.2f}% · "
            f"시가가 매수가 밑이면 시가 정리 {h['gap5']:+.2f}/{h['gap7']:+.2f}% · "
            f"바닥날 저가 손절 {h['stop5']:+.2f}/{h['stop7']:+.2f}%")


def report(daily: dict, groups: dict, nl: float = 40.0, top_groups: int = 1, market_top: int = 300) -> str:
    table = rb.day_table(daily)
    dates = sorted(table)
    lows = bs.new_low_share(daily)
    rows = {n: {r[0]: r[1:6] for r in rs} for n, rs in daily.items() if not rb.excluded(n)}
    days = cap_days(table, lows, nl)
    L = [f"🧭 투매일 섹터 고르기 검증 — 20일 신저가 {nl:.0f}%+ 투매일 {len(days)}일 · 바닥날 종가 매수 → 다음 날 "
         f"고가 목표 도달 · 그룹 대표주 = 거래대금 상위 {LEADERS}"]
    if not groups:
        return "\n".join(L + ["   관심그룹(backtest/data/watch_groups.json)이 없어 — swing_backtest 한 번 돌려 저장해줘"])
    pools = {k: [] for k in ("결합 1등", "오후반등 1등", "낙폭 1등", "결합 꼴찌", "전 그룹 대표주", "시장")}
    lines = []
    for Bd in days:
        R = dates[dates.index(Bd) + 1]
        S = rc.decline_start(table, dates, Bd)
        si = dates.index(S)
        if si == 0:
            continue
        P = dates[si - 1]
        sc = score_groups(groups, rows, P, Bd, R)
        if not sc:
            continue
        pools["결합 1등"] += [m for x in sc[:top_groups] for m in x["members"]]
        pools["오후반등 1등"] += [m for x in sorted(sc, key=lambda x: x["r_aft"])[:top_groups] for m in x["members"]]
        pools["낙폭 1등"] += [m for x in sorted(sc, key=lambda x: x["r_drop"])[:top_groups] for m in x["members"]]
        pools["결합 꼴찌"] += [m for x in sc[-top_groups:] for m in x["members"]]
        pools["전 그룹 대표주"] += [m for x in sc for m in x["members"]]
        mk = sorted((n for n in rows if Bd in rows[n]), key=lambda n: -(rows[n][Bd][4] or 0))[:market_top]
        pools["시장"] += [(n, r) for n in mk for r in [stock_row(rows[n], P, Bd, R)] if r]
        best = sc[0]
        mem = ", ".join(f"{n} {r[2]:+.0f}%" for n, r in best["members"])
        lines.append(f"   {Bd}→{R} 신저가 {lows[Bd]:4.1f}% · 1등 [{best['group']}] 오후반등 {best['aft']:+.1f}% "
                     f"낙폭 {best['drop']:+.1f}% → 다음날 고가: {mem} · 꼴찌 [{sc[-1]['group']}]")
    # ★ 10-14: 투매일이 원래 변동이 커서 도달률이 높은 건지 — 평소(투매일 아닌 날) 시장과 비교
    capset, normal = set(days), []
    for i, d in enumerate(dates[1:-1], 1):
        if d in capset:
            continue
        R, P = dates[i + 1], dates[i - 1]
        mk = sorted((n for n in rows if d in rows[n]), key=lambda n: -(rows[n][d][4] or 0))[:market_top]
        normal += [(n, r) for n in mk for r in [stock_row(rows[n], P, d, R)] if r]
    pools["평소 시장(투매일 아님)"] = normal
    L.append("■ 1. 합계 (다음 날 고가가 바닥날 종가 대비 목표에 닿은 비율 · 손익은 비용 0.3% 차감)")
    for k, rs in pools.items():
        L.append(f"   {k:<10} {fmt(hits(rs))}")
    L.append("■ 2. 투매일별 1등 그룹(결합)과 그 대표주 다음 날 고가")
    L += lines or ["   (투매일 없음 — --nl 낮춰서)"]
    L.append("   ※ 관심그룹은 지금 구성 — 과거 날짜일수록 '나중에 뜬 종목'이 섞여 부풀 수 있음")
    return "\n".join(L)


if __name__ == "__main__":
    a = sys.argv[1:]
    nl = float(a[a.index("--nl") + 1]) if "--nl" in a else 40.0
    days = int(a[a.index("--days") + 1]) if "--days" in a else 300
    tg = int(a[a.index("--top-groups") + 1]) if "--top-groups" in a else 1
    print(report(sb.load_daily(rb.THEME_DB, days), sb.load_groups(False, False), nl, tg))
