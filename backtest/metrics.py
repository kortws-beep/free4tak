"""
metrics.py — 백테스트 성과 메트릭
================================================================
[제공 지표]
  total_return       총 수익률 (%)
  cagr               연복리 수익률 (%)
  win_rate           승률
  avg_profit         평균 수익률 (승+패 모두)
  avg_win            평균 익절률
  avg_loss           평균 손절률
  profit_factor      총수익 / 총손실 (>1.5 양호, >2 우수)
  mdd                최대낙폭 (%) — 최고점 대비 최저점
  sharpe             샤프지수 (>1 양호, >2 우수)
  sortino            소르티노지수 (하방 변동만 고려)
  calmar             칼마지수 (CAGR / |MDD|)
  max_consec_loss    최대 연속 손절 횟수
  trade_count        총 거래 수
"""
import math
import datetime
from typing import Optional


def calc_metrics(trades: list, equity_curve: list,
                  initial_cash: int) -> dict:
    """
    trades: BacktestEngine.get_trades() 반환값
    equity_curve: [(date, total_value), ...]
    """
    if not trades:
        return {"trade_count": 0, "note": "no trades"}

    # ── 수익률 ──────────────────────────────────────
    final_value = equity_curve[-1][1] if equity_curve else initial_cash
    total_return = (final_value - initial_cash) / initial_cash

    # ── 거래 통계 ───────────────────────────────────
    profits_pct = [t["profit_rate"] for t in trades
                   if t.get("profit_rate") is not None]
    profits_krw = [t["profit_krw"] for t in trades
                   if t.get("profit_krw") is not None]

    wins   = [p for p in profits_pct if p > 0]
    losses = [p for p in profits_pct if p < 0]

    win_rate = len(wins) / len(profits_pct) if profits_pct else 0
    avg_profit = sum(profits_pct) / len(profits_pct) if profits_pct else 0
    avg_win    = sum(wins)   / len(wins)   if wins   else 0
    avg_loss   = sum(losses) / len(losses) if losses else 0

    # ── Profit Factor ───────────────────────────────
    total_win_krw  = sum(p for p in profits_krw if p > 0)
    total_loss_krw = abs(sum(p for p in profits_krw if p < 0))
    profit_factor = (total_win_krw / total_loss_krw
                     if total_loss_krw > 0 else float('inf'))

    # ── MDD (Maximum Drawdown) ──────────────────────
    mdd = 0
    if equity_curve:
        peak = equity_curve[0][1]
        for _, val in equity_curve:
            peak = max(peak, val)
            dd   = (val - peak) / peak if peak > 0 else 0
            mdd  = min(mdd, dd)

    # ── CAGR ────────────────────────────────────────
    cagr = 0
    if equity_curve and len(equity_curve) >= 2:
        try:
            d0 = datetime.datetime.strptime(equity_curve[0][0],  "%Y-%m-%d")
            d1 = datetime.datetime.strptime(equity_curve[-1][0], "%Y-%m-%d")
            years = max((d1 - d0).days / 365.25, 1/365.25)
            cagr = (final_value / initial_cash) ** (1 / years) - 1
        except Exception:
            pass

    # ── 일일 수익률 시계열 (샤프/소르티노용) ────────
    daily_rets = []
    for i in range(1, len(equity_curve)):
        prev = equity_curve[i-1][1]
        curr = equity_curve[i][1]
        if prev > 0:
            daily_rets.append((curr - prev) / prev)

    sharpe = sortino = 0
    if daily_rets:
        mean_r = sum(daily_rets) / len(daily_rets)
        # 표준편차
        var = sum((r - mean_r) ** 2 for r in daily_rets) / len(daily_rets)
        std = math.sqrt(var)
        if std > 0:
            sharpe = (mean_r / std) * math.sqrt(252)  # 연환산
        # 소르티노 (하방 변동만)
        downside = [r for r in daily_rets if r < 0]
        if downside:
            d_var = sum(r ** 2 for r in downside) / len(downside)
            d_std = math.sqrt(d_var)
            if d_std > 0:
                sortino = (mean_r / d_std) * math.sqrt(252)

    # ── Calmar Ratio ────────────────────────────────
    calmar = (cagr / abs(mdd)) if mdd < 0 else 0

    # ── 최대 연속 손절 ──────────────────────────────
    max_consec = 0
    cur_consec = 0
    for p in profits_pct:
        if p < 0:
            cur_consec += 1
            max_consec = max(max_consec, cur_consec)
        else:
            cur_consec = 0

    return {
        "initial_cash":     initial_cash,
        "final_value":      int(final_value),
        "total_return":     round(total_return * 100, 2),       # %
        "cagr":             round(cagr * 100, 2),
        "trade_count":      len(profits_pct),
        "win_count":        len(wins),
        "loss_count":       len(losses),
        "win_rate":         round(win_rate * 100, 2),
        "avg_profit":       round(avg_profit * 100, 2),
        "avg_win":          round(avg_win * 100, 2),
        "avg_loss":         round(avg_loss * 100, 2),
        "profit_factor":    round(profit_factor, 2)
                             if profit_factor != float('inf') else None,
        "mdd":              round(mdd * 100, 2),
        "sharpe":           round(sharpe, 2),
        "sortino":          round(sortino, 2),
        "calmar":           round(calmar, 2),
        "max_consec_loss":  max_consec,
        "total_pnl_krw":    int(sum(profits_krw)),
    }


# ============================================================
# 출력 포맷터
# ============================================================
def format_report(metrics: dict, name: str = "") -> str:
    """단일 백테스트 결과 보고서"""
    if metrics.get("trade_count", 0) == 0:
        return f"📊 {name}\n  거래 없음 — 매수 임계치 너무 높거나 데이터 부족"

    lines = [
        f"📊 백테스트 결과 — {name}",
        f"{'─' * 50}",
        f"  초기자본    : {metrics['initial_cash']:>15,}원",
        f"  최종자산    : {metrics['final_value']:>15,}원",
        f"  순손익      : {metrics['total_pnl_krw']:>+15,}원",
        f"  총수익률    : {metrics['total_return']:>+10.2f}%",
        f"  CAGR        : {metrics['cagr']:>+10.2f}%",
        f"",
        f"  거래수      : {metrics['trade_count']:>10d}건"
        f"  (승 {metrics['win_count']} / 패 {metrics['loss_count']})",
        f"  승률        : {metrics['win_rate']:>10.2f}%",
        f"  평균수익    : {metrics['avg_profit']:>+10.2f}%",
        f"  평균익절    : {metrics['avg_win']:>+10.2f}%",
        f"  평균손절    : {metrics['avg_loss']:>+10.2f}%",
        f"  Profit Factor: {(metrics.get('profit_factor') or 0):>9.2f}",
        f"",
        f"  MDD         : {metrics['mdd']:>+10.2f}%",
        f"  샤프지수    : {metrics['sharpe']:>10.2f}",
        f"  소르티노    : {metrics['sortino']:>10.2f}",
        f"  칼마        : {metrics['calmar']:>10.2f}",
        f"  최대연속손절: {metrics['max_consec_loss']:>10d}건",
    ]
    return "\n".join(lines)


def format_comparison(results: list) -> str:
    """여러 시나리오 비교 표"""
    if not results:
        return "비교할 결과 없음"

    header = f"{'시나리오':<20} {'수익률':>10} {'CAGR':>10} {'승률':>8} " \
             f"{'MDD':>8} {'샤프':>6} {'PF':>6} {'거래':>6}"
    rows = [header, "─" * len(header)]
    for r in results:
        m = r.get("metrics", {})
        if m.get("trade_count", 0) == 0:
            rows.append(f"{r['name']:<20} (거래 없음)")
            continue
        pf = m.get("profit_factor") or 0
        rows.append(
            f"{r['name']:<20} "
            f"{m.get('total_return', 0):>+9.2f}% "
            f"{m.get('cagr', 0):>+9.2f}% "
            f"{m.get('win_rate', 0):>7.1f}% "
            f"{m.get('mdd', 0):>+7.2f}% "
            f"{m.get('sharpe', 0):>6.2f} "
            f"{pf:>6.2f} "
            f"{m.get('trade_count', 0):>6d}"
        )
    return "\n".join(rows)
