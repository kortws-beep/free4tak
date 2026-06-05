"""
performance.py — 영암9 성과 분석 모듈
================================================================
[이 파일이 하는 일]

매매 이력 DB를 분석해서 전략 개선에 필요한 메트릭을 계산합니다.
kiki의 !성과상세 명령어 + 자동 일일 리뷰에서 사용합니다.

[제공 메트릭]
  ① 기본 성과:   승률, 평균수익, 누적손익
  ② 리스크:     MDD, 샤프지수, Profit Factor, Sortino Ratio
  ③ 종목별:     종목당 승률/평균수익 → 잘하는 종목/못하는 종목
  ④ 시간대별:   9시/10시/11시/... 매수 성과
  ⑤ 시장상태별: normal/weak/stop 구간 성과
  ⑥ 매도사유별: 익절/손절/트레일링 비율
  ⑦ 기간 비교:  이번 주 vs 저번 주, 이번 달 vs 저번 달

[사용법]
  from performance import PerformanceAnalyzer

  pa = PerformanceAnalyzer("trade_history.db")
  summary = pa.full_report()
  print(pa.format_discord(summary))
"""

import sqlite3
import datetime
import math
from typing import Optional


# ============================================================
# DB 연결
# ============================================================
def _ro_connect(db_path: str) -> sqlite3.Connection:
    """읽기 전용 연결 (WAL 모드 봇과 안전하게 공존)"""
    conn = sqlite3.connect(db_path, timeout=10)
    conn.execute("PRAGMA query_only = ON")
    return conn


# ============================================================
# 메인 클래스
# ============================================================
class PerformanceAnalyzer:

    def __init__(self, db_path: str = "trade_history.db"):
        self.db_path = db_path

    def _fetch_trades(self, days: int = None,
                      exclude_bug: bool = True) -> list:
        """
        매매 이력 조회.
        exclude_bug=True: sell_price=0 (kiki 즉시매도 버그) 제외
        """
        try:
            conn  = _ro_connect(self.db_path)
            where = ["sell_price IS NOT NULL", "sell_price > 0"]
            if exclude_bug:
                where.append("profit_rate > -99")  # -100% 버그 제외
            if days:
                cutoff = (datetime.datetime.now()
                          - datetime.timedelta(days=days)).strftime("%Y-%m-%d")
                where.append(f"sell_time >= '{cutoff}'")
            cur   = conn.execute("PRAGMA table_info(master_trades)").fetchall()
            cols  = [c[1] for c in cur]
            c_code  = "market as code" if "market" in cols and "code" not in cols else "code"
            c_score = "ai_score" if "ai_score" in cols else "0 as ai_score"
            sql  = f"""
                SELECT {c_code}, buy_price, sell_price, qty,
                       profit_rate, sell_reason,
                       buy_time, sell_time,
                       {c_score},
                       '' as market_status
                FROM master_trades
                WHERE {' AND '.join(where)}
                ORDER BY sell_time ASC
            """
            rows = conn.execute(sql).fetchall()
            conn.close()
            return rows
        except Exception as e:
            print(f"⚠️ 이력 조회 오류: {e}")
            return []

    # ============================================================
    # 1. 기본 성과
    # ============================================================
    def basic_stats(self, trades: list) -> dict:
        if not trades:
            return {}
        profits  = [r[4] for r in trades if r[4] is not None]
        if not profits:
            return {}
        wins     = [p for p in profits if p >= 0]
        losses   = [p for p in profits if p < 0]
        krw_list = [(r[1] - r[2]) * r[3] * -1 for r in trades
                    if r[1] and r[2] and r[3]]  # (sell-buy)*qty

        return {
            "total":         len(profits),
            "wins":          len(wins),
            "losses":        len(losses),
            "win_rate":      round(len(wins) / len(profits) * 100, 1),
            "avg_profit":    round(sum(profits) / len(profits), 2),
            "avg_win":       round(sum(wins) / len(wins), 2) if wins else 0,
            "avg_loss":      round(sum(losses) / len(losses), 2) if losses else 0,
            "best":          round(max(profits), 2),
            "worst":         round(min(profits), 2),
            "total_krw":     round(sum(krw_list)),
            "profit_factor": self._profit_factor(profits),
        }

    def _profit_factor(self, profits: list) -> float:
        """Profit Factor = 총익절합 / |총손절합|"""
        total_win  = sum(p for p in profits if p > 0)
        total_loss = abs(sum(p for p in profits if p < 0))
        if total_loss == 0:
            return 999.0
        return round(total_win / total_loss, 2)

    # ============================================================
    # 2. 리스크 메트릭
    # ============================================================
    def risk_metrics(self, trades: list,
                     initial_capital: int = 5_000_000) -> dict:
        if not trades:
            return {}

        # 일별 수익률 계산 (간이 — 매도일 기준)
        daily_pnl: dict = {}
        for r in trades:
            sell_date = r[7][:10] if r[7] else ""
            if not sell_date:
                continue
            buy_p, sell_p, qty = r[1], r[2], r[3]
            if buy_p and sell_p and qty:
                pnl = (sell_p - buy_p) * qty
                daily_pnl[sell_date] = daily_pnl.get(sell_date, 0) + pnl

        if not daily_pnl:
            return {}

        # 누적 자산 곡선
        dates   = sorted(daily_pnl.keys())
        equity  = initial_capital
        equities = []
        for d in dates:
            equity += daily_pnl[d]
            equities.append(equity)

        # MDD (최대낙폭)
        mdd        = 0.0
        peak_eq    = initial_capital
        for eq in equities:
            if eq > peak_eq:
                peak_eq = eq
            dd = (peak_eq - eq) / peak_eq
            if dd > mdd:
                mdd = dd

        # 샤프지수 (일별 수익률 기준, 무위험 수익률 3.5% 가정)
        daily_returns = [daily_pnl[d] / initial_capital for d in dates]
        rf_daily      = 0.035 / 252
        excess        = [r - rf_daily for r in daily_returns]
        if len(excess) >= 2:
            avg_ex = sum(excess) / len(excess)
            std_ex = math.sqrt(sum((r - avg_ex)**2 for r in excess)
                               / (len(excess) - 1))
            sharpe = (avg_ex / std_ex * math.sqrt(252)) if std_ex > 0 else 0
        else:
            sharpe = 0

        # Sortino (하방 변동성만 — 손실 구간)
        neg_returns = [r for r in excess if r < 0]
        if neg_returns:
            downside_std = math.sqrt(sum(r**2 for r in neg_returns)
                                     / len(neg_returns))
            avg_ex_full  = sum(excess) / len(excess)
            sortino      = (avg_ex_full / downside_std * math.sqrt(252)
                           if downside_std > 0 else 0)
        else:
            sortino = 0

        # 최대 연속 손절
        profits       = [r[4] for r in trades if r[4] is not None]
        max_consec    = cur_consec = 0
        for p in profits:
            if p < 0:
                cur_consec += 1
                max_consec  = max(max_consec, cur_consec)
            else:
                cur_consec  = 0

        # 연환산 수익률
        if len(dates) >= 2:
            days_elapsed = (datetime.datetime.strptime(dates[-1], "%Y-%m-%d")
                           - datetime.datetime.strptime(dates[0], "%Y-%m-%d")).days
            total_return  = (equities[-1] - initial_capital) / initial_capital
            annual_return = ((1 + total_return) ** (365 / max(days_elapsed, 1)) - 1) * 100
        else:
            annual_return = 0

        return {
            "mdd":            round(mdd * 100, 2),
            "sharpe":         round(sharpe, 2),
            "sortino":        round(sortino, 2),
            "max_consec_loss": max_consec,
            "annual_return":  round(annual_return, 2),
            "final_equity":   round(equities[-1]) if equities else initial_capital,
        }

    # ============================================================
    # 3. 종목별 성과
    # ============================================================
    def by_stock(self, trades: list, top_n: int = 10) -> list:
        """종목별 승률/평균수익 → 상위/하위 종목"""
        stock_data: dict = {}
        for r in trades:
            code, profit = r[0], r[4]
            if not code or profit is None:
                continue
            if code not in stock_data:
                stock_data[code] = []
            stock_data[code].append(profit)

        results = []
        for code, profits in stock_data.items():
            if len(profits) < 2:
                continue
            wins = [p for p in profits if p >= 0]
            results.append({
                "code":       code,
                "trades":     len(profits),
                "win_rate":   round(len(wins) / len(profits) * 100, 1),
                "avg_profit": round(sum(profits) / len(profits), 2),
                "total":      round(sum(profits), 2),
            })

        results.sort(key=lambda x: x["avg_profit"], reverse=True)
        return results

    # ============================================================
    # 4. 시간대별 성과
    # ============================================================
    def by_hour(self, trades: list) -> dict:
        """매수 시간대별 성과 (9시~15시)"""
        hour_data: dict = {}
        for r in trades:
            buy_time = r[6]  # "2026-04-28T09:38:41"
            profit   = r[4]
            if not buy_time or profit is None:
                continue
            try:
                hour = int(buy_time[11:13])
                if hour not in hour_data:
                    hour_data[hour] = []
                hour_data[hour].append(profit)
            except Exception:
                continue

        results = {}
        for hour, profits in sorted(hour_data.items()):
            wins = [p for p in profits if p >= 0]
            results[f"{hour:02d}시"] = {
                "trades":     len(profits),
                "win_rate":   round(len(wins) / len(profits) * 100, 1),
                "avg_profit": round(sum(profits) / len(profits), 2),
            }
        return results

    # ============================================================
    # 5. 시장상태별 성과
    # ============================================================
    def by_market_status(self, trades: list) -> dict:
        """normal/weak/stop 시장 상태별 성과"""
        status_data: dict = {}
        for r in trades:
            status = r[9] if len(r) > 9 else "normal"
            status = status or "normal"
            profit = r[4]
            if profit is None:
                continue
            if status not in status_data:
                status_data[status] = []
            status_data[status].append(profit)

        results = {}
        for status, profits in status_data.items():
            wins = [p for p in profits if p >= 0]
            results[status] = {
                "trades":     len(profits),
                "win_rate":   round(len(wins) / len(profits) * 100, 1),
                "avg_profit": round(sum(profits) / len(profits), 2),
            }
        return results

    # ============================================================
    # 6. 매도사유별 성과
    # ============================================================
    def by_sell_reason(self, trades: list) -> dict:
        """익절/손절/트레일링 비율 + 각 평균 수익"""
        reason_data: dict = {}
        for r in trades:
            reason = r[5] or "기타"
            profit = r[4]
            if profit is None:
                continue

            # 사유 정규화
            if   "1차익절" in reason: key = "1차익절"
            elif "2차익절" in reason: key = "2차익절"
            elif "3차익절" in reason: key = "3차익절"
            elif "트레일링" in reason: key = "트레일링"
            elif "본절"    in reason: key = "본절보호"
            elif "손절"    in reason: key = "손절"
            elif "즉시매도" in reason: key = "즉시매도(수동)"
            else:                     key = "기타"

            if key not in reason_data:
                reason_data[key] = []
            reason_data[key].append(profit)

        results = {}
        for reason, profits in reason_data.items():
            results[reason] = {
                "count":      len(profits),
                "avg_profit": round(sum(profits) / len(profits), 2),
                "pct":        0,  # 아래서 계산
            }

        total = sum(v["count"] for v in results.values())
        for reason in results:
            results[reason]["pct"] = round(
                results[reason]["count"] / total * 100, 1
            ) if total > 0 else 0

        # 건수 내림차순 정렬
        return dict(sorted(results.items(),
                           key=lambda x: x[1]["count"], reverse=True))

    # ============================================================
    # 7. 기간 비교
    # ============================================================
    def period_compare(self) -> dict:
        """이번 주/저번 주, 이번 달/저번 달 비교"""
        now   = datetime.datetime.now()
        today = now.date()

        # 이번 주: 월~오늘
        this_week_start = today - datetime.timedelta(days=today.weekday())
        last_week_start = this_week_start - datetime.timedelta(days=7)
        last_week_end   = this_week_start - datetime.timedelta(days=1)

        # 이번 달
        this_month_start = today.replace(day=1)
        last_month_end   = this_month_start - datetime.timedelta(days=1)
        last_month_start = last_month_end.replace(day=1)

        def get_period_stats(start, end) -> dict:
            trades = self._fetch_trades_between(start.isoformat(), end.isoformat())
            if not trades:
                return {"total": 0, "win_rate": 0, "avg_profit": 0, "total_krw": 0}
            profits = [r[4] for r in trades if r[4] is not None]
            if not profits:
                return {"total": 0, "win_rate": 0, "avg_profit": 0, "total_krw": 0}
            wins    = [p for p in profits if p >= 0]
            krw     = [(r[2]-r[1])*r[3] for r in trades if r[1] and r[2] and r[3]]
            return {
                "total":      len(profits),
                "win_rate":   round(len(wins)/len(profits)*100, 1),
                "avg_profit": round(sum(profits)/len(profits), 2),
                "total_krw":  round(sum(krw)),
            }

        return {
            "this_week": get_period_stats(this_week_start, today),
            "last_week": get_period_stats(last_week_start, last_week_end),
            "this_month": get_period_stats(this_month_start, today),
            "last_month": get_period_stats(last_month_start, last_month_end),
        }

    def _fetch_trades_between(self, start: str, end: str) -> list:
        try:
            conn = _ro_connect(self.db_path)
            rows = conn.execute("""
                SELECT code, buy_price, sell_price, qty,
                       profit_rate, sell_reason, buy_time, sell_time,
                       ai_score,
                       '' as market_status
                FROM master_trades
                WHERE sell_price IS NOT NULL AND sell_price > 0
                  AND profit_rate > -99
                  AND sell_time >= ? AND sell_time <= ?
                ORDER BY sell_time
            """, (start, end + "T23:59:59")).fetchall()
            conn.close()
            return rows
        except Exception:
            return []

    # ============================================================
    # 8. 종합 리포트
    # ============================================================
    def full_report(self, days: int = None) -> dict:
        """종합 성과 리포트"""
        trades = self._fetch_trades(days=days)
        if not trades:
            return {"error": "매매 이력 없음"}

        return {
            "trade_count": len(trades),
            "period_days": days,
            "basic":       self.basic_stats(trades),
            "risk":        self.risk_metrics(trades),
            "by_stock":    self.by_stock(trades, top_n=10),
            "by_hour":     self.by_hour(trades),
            "by_market":   self.by_market_status(trades),
            "by_reason":   self.by_sell_reason(trades),
            "period":      self.period_compare(),
        }

    # ============================================================
    # 9. 디스코드 포맷
    # ============================================================
    def format_discord(self, report: dict) -> str:
        """kiki !성과상세 응답 포맷"""
        if "error" in report:
            return f"❌ {report['error']}"

        b  = report.get("basic", {})
        r  = report.get("risk",  {})
        pd = report.get("period", {})

        days_str = f"최근 {report['period_days']}일" if report["period_days"] else "전체"
        lines = [
            f"📊 **영암9 단타봇 성과 상세** [{days_str} / {b.get('total',0)}건]",
            "━━━━━━━━━━━━━━━━━━━━",
        ]

        # 기본 성과
        if b:
            wr_e = "✅" if b.get("win_rate",0) >= 55 else "⚠️"
            pf_e = "✅" if b.get("profit_factor",0) >= 1.5 else "⚠️"
            lines += [
                f"\n**[거래 성과]**",
                f"  {wr_e} 승률:          {b.get('win_rate',0)}%  "
                f"(익절:{b.get('wins',0)} / 손절:{b.get('losses',0)})",
                f"  평균 수익:       {b.get('avg_profit',0):+.2f}%",
                f"  평균 익절:       {b.get('avg_win',0):+.2f}%",
                f"  평균 손절:       {b.get('avg_loss',0):+.2f}%",
                f"  {pf_e} Profit Factor: {b.get('profit_factor',0):.2f}",
                f"  누적 손익:       {b.get('total_krw',0):+,.0f}원",
            ]

        # 리스크
        if r:
            mdd_e = "✅" if r.get("mdd",99) < 10 else "⚠️"
            sh_e  = "✅" if r.get("sharpe",0) >= 1.0 else "⚠️"
            lines += [
                f"\n**[리스크]**",
                f"  {mdd_e} MDD:           -{r.get('mdd',0):.2f}%",
                f"  {sh_e} 샤프지수:      {r.get('sharpe',0):.2f}",
                f"  Sortino:         {r.get('sortino',0):.2f}",
                f"  연환산 수익률:   {r.get('annual_return',0):+.1f}%",
                f"  최대 연속 손절:  {r.get('max_consec_loss',0)}회",
            ]

        # 매도사유
        by_reason = report.get("by_reason", {})
        if by_reason:
            lines.append(f"\n**[매도사유]**")
            for reason, info in by_reason.items():
                e = "✅" if info["avg_profit"] >= 0 else "❌"
                lines.append(
                    f"  {e} {reason:<12} "
                    f"{info['count']:>3}건({info['pct']:>4.1f}%) | "
                    f"평균{info['avg_profit']:+.1f}%"
                )

        # 시간대별 (상위 3개)
        by_hour = report.get("by_hour", {})
        if by_hour:
            sorted_hours = sorted(by_hour.items(),
                                  key=lambda x: x[1]["avg_profit"],
                                  reverse=True)
            lines.append(f"\n**[시간대별 상위 3개]**")
            for hour, info in sorted_hours[:3]:
                e = "✅" if info["avg_profit"] >= 0 else "❌"
                lines.append(
                    f"  {e} {hour}: {info['trades']}건 | "
                    f"승률{info['win_rate']}% | "
                    f"평균{info['avg_profit']:+.2f}%"
                )

        # 기간 비교
        if pd:
            tw = pd.get("this_week", {})
            lw = pd.get("last_week", {})
            tm = pd.get("this_month", {})
            lines.append(f"\n**[기간 비교]**")
            if tw.get("total", 0):
                lines.append(
                    f"  이번 주: {tw['total']}건 | "
                    f"승률{tw['win_rate']}% | "
                    f"{tw['total_krw']:+,.0f}원"
                )
            if lw.get("total", 0):
                lines.append(
                    f"  저번 주: {lw['total']}건 | "
                    f"승률{lw['win_rate']}% | "
                    f"{lw['total_krw']:+,.0f}원"
                )
            if tm.get("total", 0):
                lines.append(
                    f"  이번 달: {tm['total']}건 | "
                    f"승률{tm['win_rate']}% | "
                    f"{tm['total_krw']:+,.0f}원"
                )

        # 종목별 (상위 3개 + 하위 3개)
        by_stock = report.get("by_stock", [])
        if by_stock:
            lines.append(f"\n**[종목별 — 상위 3개]**")
            for s in by_stock[:3]:
                lines.append(
                    f"  ✅ {s['code']} | {s['trades']}건 | "
                    f"승률{s['win_rate']}% | 평균{s['avg_profit']:+.1f}%"
                )
            if len(by_stock) > 3:
                lines.append(f"**[종목별 — 하위 3개]**")
                for s in by_stock[-3:]:
                    lines.append(
                        f"  ❌ {s['code']} | {s['trades']}건 | "
                        f"승률{s['win_rate']}% | 평균{s['avg_profit']:+.1f}%"
                    )

        lines.append("━━━━━━━━━━━━━━━━━━━━")
        return "\n".join(lines)

    def format_brief(self, report: dict) -> str:
        """kiki 일일 리뷰용 간략 버전"""
        if "error" in report:
            return report["error"]

        b = report.get("basic", {})
        r = report.get("risk",  {})
        if not b:
            return "매매 없음"

        wr = b.get("win_rate", 0)
        pf = b.get("profit_factor", 0)
        mdd = r.get("mdd", 0)

        verdict = "✅ 양호" if wr >= 55 and pf >= 1.3 else "⚠️ 점검 필요"

        return (
            f"{verdict} | 승률:{wr}% | PF:{pf:.2f} | MDD:-{mdd:.1f}%\n"
            f"누적:{b.get('total_krw',0):+,.0f}원 | "
            f"연환산:{r.get('annual_return',0):+.1f}%"
        )


# ============================================================
# 멀티봇 종합 성과
# ============================================================
class MultiPerformanceAnalyzer:
    """단타/스윙/종가/코인 4봇 종합 성과"""

    DB_MAP = {
        "nbot": "trade_history.db",
        "sbot": "sbot_trade_history.db",
        "ebot": "ebot_trade_history.db",
        "cbot": "cbot_trade_history.db",
    }
    LABELS = {
        "nbot": "📈 단타봇",
        "sbot": "📊 스윙봇",
        "ebot": "🌆 종가봇",
        "cbot": "🪙 코인봇",
    }

    def summary(self, days: int = 30) -> str:
        """모든 봇 요약 (kiki !성과상세 에서 사용)"""
        import os
        lines = [
            f"📊 **영암9 전체 봇 성과** [최근 {days}일]",
            "━━━━━━━━━━━━━━━━━━━━",
        ]

        total_krw = 0
        for bot_name, db_path in self.DB_MAP.items():
            if not os.path.exists(db_path):
                continue
            try:
                pa     = PerformanceAnalyzer(db_path)
                report = pa.full_report(days=days)
                b      = report.get("basic", {})
                r      = report.get("risk", {})
                label  = self.LABELS[bot_name]

                if not b or b.get("total", 0) == 0:
                    lines.append(f"{label}: 매매 없음")
                    continue

                wr  = b.get("win_rate", 0)
                pf  = b.get("profit_factor", 0)
                krw = b.get("total_krw", 0)
                total_krw += krw
                e   = "✅" if wr >= 55 else "⚠️"

                lines.append(
                    f"{e} **{label}**: {b['total']}건 | "
                    f"승률{wr}% | PF{pf:.2f} | "
                    f"MDD-{r.get('mdd',0):.1f}% | "
                    f"샤프{r.get('sharpe',0):.2f} | "
                    f"{krw:+,.0f}원"
                )
            except Exception as e_:
                lines.append(f"{self.LABELS[bot_name]}: 조회 오류 {e_}")

        lines += [
            "━━━━━━━━━━━━━━━━━━━━",
            f"💰 **합계 실현손익: {total_krw:+,.0f}원**",
        ]
        return "\n".join(lines)


# ============================================================
# 단독 실행 (테스트)
# ============================================================
if __name__ == "__main__":
    import sys

    db   = sys.argv[1] if len(sys.argv) > 1 else "trade_history.db"
    days = int(sys.argv[2]) if len(sys.argv) > 2 else None

    print(f"📊 성과 분석: {db} (최근 {days or '전체'}일)")
    pa     = PerformanceAnalyzer(db)
    report = pa.full_report(days=days)
    print(pa.format_discord(report))

    print("\n\n" + "="*50)
    print("📊 멀티봇 종합:")
    mpa = MultiPerformanceAnalyzer()
    print(mpa.summary(days=30))
