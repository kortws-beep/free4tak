"""
backtest_engine_attribution.py
================================================================
[역할]
기존 backtest_engine.py를 상속해서
_replay_day() 매수 후보 평가 시 score_logs를 자동 수집하고
백테스트 종료 후 거래 결과(profit_rate)를 score_logs에 역추적 반영.

→ 실제 backtest_data.db 데이터로 지표별 기여도 분석 실행

[사용법 — run_backtest.py에서 기존 BacktestEngine 대체]

  # 기존:
  # from backtest_engine import BacktestEngine, BacktestConfig
  # engine = BacktestEngine(config, db_path)

  # 변경:
  from backtest_engine_attribution import AttributionBacktestEngine, BacktestConfig
  engine = AttributionBacktestEngine(config, db_path)
  engine.run()

  # 기여도 분석 자동 실행
  report = engine.run_attribution()
  engine.print_attribution(report)
  engine.save_attribution(report, "results/attribution_latest.json")

[기존 코드 변경 없음]
- backtest_engine.py: 수정 불필요
- strategy.py: 수정 불필요
- run_backtest.py: import 줄 1개만 변경

================================================================
"""

import os
import sys
import datetime

# 프로젝트 루트 경로 설정 (실제 서버 환경)
_candidates = [
    os.environ.get("K_BOT_ROOT"),
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..")),
    "/home/free4tak/k-bot/stock_bot",
    "/mnt/project",
]
for _root in _candidates:
    if _root and os.path.exists(os.path.join(_root, "strategy.py")):
        if _root not in sys.path:
            sys.path.insert(0, _root)
        PROJECT_ROOT = _root
        break
else:
    raise ImportError("strategy.py 위치를 찾을 수 없음")

from backtest_engine import BacktestEngine, BacktestConfig
from indicator_attribution import (
    AttributionAnalyzer,
    get_rule_score_breakdown,
    INDICATOR_META,
)


class AttributionBacktestEngine(BacktestEngine):
    """
    BacktestEngine 상속 — score_logs 자동 수집 추가.
    기존 BacktestEngine 동작 100% 유지.
    """

    def __init__(self, config: BacktestConfig, db_path: str):
        super().__init__(config, db_path)
        # 매수 후보 평가 시 수집: {trade_key → log_entry}
        self._score_logs: list = []
        # 매수 체결 종목 → score_log 인덱스 매핑 (profit_rate 역추적용)
        self._buy_log_index: dict = {}  # (code, buy_date) → index in _score_logs

    # ============================================================
    # _replay_day() 오버라이드 — 매수 후보 블록에 로그 수집 추가
    # ============================================================
    def _replay_day(self, date):
        """
        부모의 _replay_day()를 호출하기 전에
        해당 날짜의 매수 후보들을 미리 평가해서 score_logs 수집.
        부모 호출 후 실제 체결 종목에만 인덱스를 매핑.
        """
        import pandas as pd
        from feature_builder import build_features_at

        date_str = date.strftime("%Y-%m-%d")

        # ── 사전 로그 수집 (매수 필터 통과 + 임계치 통과 종목) ──
        pre_positions = set(self.positions.keys())

        # 부모 실행 (실제 체결)
        super()._replay_day(date)

        # 부모 실행 후 새로 진입한 종목들 파악
        new_positions = set(self.positions.keys()) - pre_positions

        # 새 포지션 종목에 대해 features 재계산 → score_log 추가
        for code in new_positions:
            try:
                features = build_features_at(self.loader, code, date)
                if not features:
                    continue

                # breakdown 계산
                bd = get_rule_score_breakdown(features)
                rule_score = bd.get("__total__", 0)

                log_entry = {
                    "code":        code,
                    "date":        date_str,
                    "features":    features,
                    "rule_score":  rule_score,
                    "profit_rate": 0.0,   # 나중에 역추적
                    "_resolved":   False,
                }
                idx = len(self._score_logs)
                self._score_logs.append(log_entry)
                self._buy_log_index[(code, date_str)] = idx

            except Exception as e:
                if self.config.verbose:
                    print(f"   ⚠️ attribution 로그 수집 오류 {code}: {e}")

    # ============================================================
    # run() 오버라이드 — 종료 후 profit_rate 역추적
    # ============================================================
    def run(self):
        """백테스트 실행 + 종료 후 score_logs에 profit_rate 반영"""
        super().run()
        self._resolve_profit_rates()
        print(f"📊 Attribution 로그: {len(self._score_logs)}건 수집 완료")

    def _resolve_profit_rates(self):
        """
        완료된 거래(self.trades)에서 profit_rate를 꺼내
        해당 score_log에 역추적 반영.

        [핵심 수정]
        score_log.date  = T일 (신호 발생일 — _replay_day의 date)
        Trade.buy_date  = T+1일 (실제 시가 매수일 — look-ahead bias 방지)
        → 날짜 키가 불일치하므로 code 기준으로 매핑.
        같은 종목이 여러 번 거래된 경우 신호일(T) 직후
        첫 번째 거래를 매핑 (날짜 diff 최소화).
        """
        from datetime import datetime

        # 거래 완료 테이블: code → [(buy_date_str, profit_rate), ...]
        from collections import defaultdict
        trade_by_code = defaultdict(list)
        for t in self.trades:
            trade_by_code[t.code].append((t.buy_date, t.profit_rate))
        # 날짜 오름차순 정렬 (신호일 직후 거래를 먼저 매핑)
        for code in trade_by_code:
            trade_by_code[code].sort(key=lambda x: x[0])

        # 매핑: 신호일(T) 이후 가장 가까운 buy_date 거래에 연결
        used = set()  # (code, buy_date) 중복 방지
        resolved = 0
        for log in self._score_logs:
            code      = log["code"]
            signal_dt = log["date"]  # T일 문자열
            trades    = trade_by_code.get(code, [])

            best = None
            for buy_date_str, profit_rate in trades:
                if (code, buy_date_str) in used:
                    continue
                # buy_date >= signal_date (T+1 이상)
                if buy_date_str >= signal_dt:
                    best = (buy_date_str, profit_rate)
                    break

            if best:
                log["profit_rate"] = best[1]
                log["_resolved"]   = True
                used.add((code, best[0]))
                resolved += 1

        unresolved = len(self._score_logs) - resolved
        print(f"   profit_rate 매핑: {resolved}건 완료, {unresolved}건 미완료(기간말 보유중)")

    # ============================================================
    # Attribution 분석 실행
    # ============================================================
    def run_attribution(self) -> dict:
        """
        수집된 score_logs로 AttributionAnalyzer 실행.
        profit_rate가 확정된 거래만 분석.
        """
        resolved_logs = [
            log for log in self._score_logs if log["_resolved"]
        ]
        if not resolved_logs:
            print("⚠️ 분석할 확정 거래 없음 (score_logs 비어있거나 미매핑)")
            return {"error": "no resolved trades"}

        print(f"\n🔍 지표별 기여도 분석 — 실제 거래 {len(resolved_logs)}건")

        analyzer = AttributionAnalyzer(
            trades    = self.get_trades(),
            score_logs = resolved_logs,
        )
        report = analyzer.run()
        self._last_analyzer = analyzer
        self._last_report   = report
        return report

    def print_attribution(self, report: dict = None):
        """콘솔 리포트 출력"""
        rpt = report or getattr(self, "_last_report", None)
        if not rpt:
            print("⚠️ 분석 결과 없음 — run_attribution() 먼저 실행")
            return
        if hasattr(self, "_last_analyzer"):
            self._last_analyzer.print_report(rpt)

    def save_attribution(self, report: dict = None, path: str = None):
        """JSON 저장"""
        rpt = report or getattr(self, "_last_report", None)
        if not rpt:
            print("⚠️ 분석 결과 없음")
            return
        if path is None:
            ts   = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            path = os.path.join(PROJECT_ROOT, "backtest", "results",
                                f"attribution_{ts}.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if hasattr(self, "_last_analyzer"):
            self._last_analyzer.save_json(rpt, path)

    def get_score_logs(self) -> list:
        """수집된 score_logs 반환 (외부 분석용)"""
        return self._score_logs


# ============================================================
# run_backtest.py 패치 헬퍼
# ============================================================
def patch_run_backtest():
    """
    run_backtest.py의 run_one() 함수에 추가할 코드 스니펫 출력.
    직접 붙여넣기 가능.
    """
    snippet = '''
# ============================================================
# run_backtest.py 수정 — 지표 기여도 분석 자동 추가
# ============================================================

# 1. import 변경 (파일 상단)
# 기존: from backtest_engine import BacktestEngine, BacktestConfig
# 변경:
from backtest_engine_attribution import AttributionBacktestEngine, BacktestConfig

# 2. run_one() 내부 수정
def run_one(name: str, config: BacktestConfig, db_path: str) -> dict:
    print(f"\\n{'='*60}\\n▶ 실행: {name}\\n{'='*60}")

    # ★ BacktestEngine → AttributionBacktestEngine 으로만 변경
    engine = AttributionBacktestEngine(config, db_path)
    engine.run()

    # ★ 기여도 분석 추가 (3줄)
    attr_report = engine.run_attribution()
    engine.print_attribution(attr_report)
    engine.save_attribution(attr_report,
        f"results/attribution_{name.replace(' ','_')}.json")

    # 기존 메트릭 (변경 없음)
    metrics = calc_metrics(
        engine.get_trades(),
        engine.get_equity_curve(),
        config.initial_cash,
    )
    return {
        "name":        name,
        "config":      {...},
        "metrics":     metrics,
        "trades":      engine.get_trades(),
        "equity":      engine.get_equity_curve(),
        "attribution": attr_report,   # ★ 추가
    }
'''
    print(snippet)
    return snippet


if __name__ == "__main__":
    print("=" * 60)
    print("backtest_engine_attribution.py")
    print("실제 DB 연계 지표 기여도 분석 패치")
    print("=" * 60)
    patch_run_backtest()

    print("\n📋 파일 배치 방법:")
    print(f"  1. indicator_attribution.py    → {PROJECT_ROOT}/")
    print(f"  2. backtest_engine_attribution.py → {PROJECT_ROOT}/")
    print(f"  3. run_backtest.py: import 1줄 변경")
    print(f"\n실행:")
    print(f"  cd {PROJECT_ROOT}")
    print(f"  python3 run_backtest.py --compare")
    print(f"\n결과:")
    print(f"  backtest/results/attribution_*.json")
