"""
indicator_attribution.py — 지표별 기여도 분해 분석 모듈
================================================================
[역할]
백테스트 거래 결과 + strategy.py 점수 로그를 받아서
"어떤 지표가 수익 거래에 얼마나 기여했는가" 를 정량화

[핵심 아이디어]
  Shapley-Value 방식 (근사) :
    지표 i의 기여도 = 지표 i가 켜진 거래의 평균 수익률
                     - 지표 i가 꺼진 거래의 평균 수익률

  + 단순 상관 분석: 각 지표 점수와 profit_rate의 피어슨 상관계수

[사용법]
  1. 기존 BacktestEngine에 score_log 기록 추가 후 실행
  2. 또는 기존 trades JSON에 score_breakdown 있으면 바로 분석

  from indicator_attribution import AttributionAnalyzer
  analyzer = AttributionAnalyzer(trades, score_logs)
  report = analyzer.run()
  analyzer.print_report(report)

[출력]
  - 지표별: 기여도 점수, 활성 거래 수, 활성 시 평균 수익, 비활성 시 평균 수익
  - 상관계수 (지표 점수 vs profit_rate)
  - "없애야 할 지표" / "가중치 올려야 할 지표" 추천
  - JSON 저장 지원

[기존 코드 연계]
  - backtest_engine.py: Trade 데이터클래스에 score_breakdown 필드 추가
  - strategy.py: get_rule_score()에서 각 지표 점수를 dict로 반환하는
    get_rule_score_breakdown() 메서드 추가
  - run_backtest.py: run_one()에서 attribution 분석 자동 실행

================================================================
"""

import os
import json
import math
import datetime
from collections import defaultdict
from typing import Optional


# ============================================================
# 지표 메타데이터 (strategy.py와 1:1 매핑)
# ============================================================
INDICATOR_META = {
    "change":         {"name": "등락률",         "max_plus": 15, "max_minus": -5,  "cat": "momentum"},
    "value":          {"name": "거래대금",         "max_plus": 10, "max_minus": -5,  "cat": "momentum"},
    "vol_ratio":      {"name": "거래량 증가율",    "max_plus": 8,  "max_minus": -5,  "cat": "momentum"},
    "vol_tnrt":       {"name": "거래량 회전율",    "max_plus": 5,  "max_minus": 0,   "cat": "momentum"},
    "rsi":            {"name": "RSI",             "max_plus": 8,  "max_minus": -10, "cat": "technical"},
    "ma":             {"name": "MA 정배열",        "max_plus": 8,  "max_minus": -3,  "cat": "technical"},
    "macd":           {"name": "MACD 히스토그램", "max_plus": 8,  "max_minus": -6,  "cat": "technical"},
    "bb":             {"name": "볼린저밴드",       "max_plus": 10, "max_minus": -7,  "cat": "technical"},
    "stoch":          {"name": "스토캐스틱",       "max_plus": 6,  "max_minus": -6,  "cat": "technical"},
    "candle":         {"name": "캔들 패턴",        "max_plus": 4,  "max_minus": -4,  "cat": "technical"},
    "foreign5":       {"name": "외국인 5일",       "max_plus": 8,  "max_minus": -6,  "cat": "supply"},
    "orgn5":          {"name": "기관 5일",         "max_plus": 6,  "max_minus": -4,  "cat": "supply"},
    "foreign_today":  {"name": "당일 외국인",      "max_plus": 8,  "max_minus": -8,  "cat": "supply"},
    "orgn_today":     {"name": "당일 기관",        "max_plus": 6,  "max_minus": -5,  "cat": "supply"},
    "foreign_ratio":  {"name": "외국인 비율",      "max_plus": 5,  "max_minus": -5,  "cat": "supply"},
    "prsn":           {"name": "개인 역지표",      "max_plus": 4,  "max_minus": -3,  "cat": "supply"},
    "buy_pressure":   {"name": "매수압력",         "max_plus": 5,  "max_minus": -5,  "cat": "supply"},
    "hoga":           {"name": "호가잔량 비율",    "max_plus": 15, "max_minus": -10, "cat": "hoga"},
}


# ============================================================
# strategy.py 확장 — get_rule_score_breakdown()
# ============================================================
def get_rule_score_breakdown(data: dict) -> dict:
    """
    strategy.py의 get_rule_score()와 동일한 로직을
    지표별로 분해해서 반환.

    반환: {
        "indicator_id": {
            "score": int,       # 이 지표가 기여한 점수 (양/음)
            "value": float,     # 실제 지표 값
            "active_pos": bool, # 양의 기여 여부
            "active_neg": bool, # 음의 기여 여부
        },
        "__base__": 30,
        "__total__": int,
    }
    """
    result = {}
    base = 30  # strategy.py 기본 점수

    # ── 원시 데이터 추출 ──────────────────────────────────────
    change       = data.get("change_rate",    0)
    value        = data.get("trading_value",  0)
    vol_ratio    = data.get("volume_ratio",   0)
    vol_tnrt     = data.get("vol_tnrt",       0)
    rsi          = data.get("rsi",            50)
    ma5          = data.get("ma5",             0)
    ma20         = data.get("ma20",            0)
    ma60         = data.get("ma60",            0)
    foreign      = data.get("foreign_5d",      0)
    institution  = data.get("institution_5d",  0)
    foreign_today  = data.get("foreign_today",  0)
    orgn_today     = data.get("orgn_today",     0)
    prsn_today     = data.get("prsn_today",     0)
    foreign_ratio  = data.get("foreign_ratio",  50)
    buy_pressure   = data.get("buy_pressure",   0)
    macd_hist      = data.get("macd_hist",      0)
    bb_pct         = data.get("bb_pct",         0.5)
    bb_width       = data.get("bb_width",       0)
    stoch_k        = data.get("stoch_k",        50)
    candle_pat     = data.get("candle_pattern", 0)
    ask_rsqn       = data.get("total_ask_rsqn", 0)
    bid_rsqn       = data.get("total_bid_rsqn", 0)

    # ── 각 지표별 점수 계산 ───────────────────────────────────
    # 등락률
    if   change > 5:  sc = 15
    elif change > 3:  sc = 10
    elif change > 1:  sc = 5
    else:             sc = -5
    result["change"] = {"score": sc, "value": change,
                        "active_pos": sc > 0, "active_neg": sc < 0}

    # 거래대금
    if   value > 300: sc = 10
    elif value > 100: sc = 5
    elif value < 30:  sc = -5
    else:             sc = 0
    result["value"] = {"score": sc, "value": value,
                       "active_pos": sc > 0, "active_neg": sc < 0}

    # 거래량 증가율
    if   vol_ratio > 300: sc = 8
    elif vol_ratio > 200: sc = 5
    elif vol_ratio > 120: sc = 2
    elif vol_ratio < 50:  sc = -5
    else:                 sc = 0
    result["vol_ratio"] = {"score": sc, "value": vol_ratio,
                           "active_pos": sc > 0, "active_neg": sc < 0}

    # 거래량 회전율
    if   vol_tnrt > 50: sc = 5
    elif vol_tnrt > 20: sc = 2
    else:               sc = 0
    result["vol_tnrt"] = {"score": sc, "value": vol_tnrt,
                          "active_pos": sc > 0, "active_neg": sc < 0}

    # RSI
    if   45 < rsi < 65:  sc = 8
    elif rsi > 75:       sc = -10
    elif rsi < 30:       sc = -3
    else:                sc = 0
    result["rsi"] = {"score": sc, "value": rsi,
                     "active_pos": sc > 0, "active_neg": sc < 0}

    # MA 정배열
    if   ma5 > ma20 > ma60 > 0: sc = 8
    elif ma5 > ma20 > 0:        sc = 4
    else:                       sc = -3
    result["ma"] = {"score": sc, "value": ma5,
                    "active_pos": sc > 0, "active_neg": sc < 0}

    # 외국인 5일
    if   foreign > 10000:  sc = 8
    elif foreign > 5000:   sc = 5
    elif foreign > 1000:   sc = 3
    elif foreign < -10000: sc = -6
    elif foreign < -5000:  sc = -3
    else:                  sc = 0
    result["foreign5"] = {"score": sc, "value": foreign,
                          "active_pos": sc > 0, "active_neg": sc < 0}

    # 기관 5일
    if   institution > 10000:  sc = 6
    elif institution > 5000:   sc = 4
    elif institution > 1000:   sc = 2
    elif institution < -10000: sc = -4
    elif institution < -5000:  sc = -2
    else:                      sc = 0
    result["orgn5"] = {"score": sc, "value": institution,
                       "active_pos": sc > 0, "active_neg": sc < 0}

    # 당일 외국인
    if   foreign_today > 5000:   sc = 8
    elif foreign_today > 2000:   sc = 5
    elif foreign_today > 500:    sc = 3
    elif foreign_today < -5000:  sc = -8
    elif foreign_today < -2000:  sc = -5
    else:                        sc = 0
    result["foreign_today"] = {"score": sc, "value": foreign_today,
                               "active_pos": sc > 0, "active_neg": sc < 0}

    # 당일 기관
    if   orgn_today > 3000:   sc = 6
    elif orgn_today > 1000:   sc = 4
    elif orgn_today > 300:    sc = 2
    elif orgn_today < -3000:  sc = -5
    elif orgn_today < -1000:  sc = -3
    else:                     sc = 0
    result["orgn_today"] = {"score": sc, "value": orgn_today,
                            "active_pos": sc > 0, "active_neg": sc < 0}

    # 외국인 매수비율
    if   foreign_ratio > 60:  sc = 5
    elif foreign_ratio > 55:  sc = 3
    elif foreign_ratio < 40:  sc = -5
    elif foreign_ratio < 45:  sc = -3
    else:                     sc = 0
    result["foreign_ratio"] = {"score": sc, "value": foreign_ratio,
                               "active_pos": sc > 0, "active_neg": sc < 0}

    # 개인 역지표
    if   prsn_today < -2000:  sc = 4
    elif prsn_today < -500:   sc = 2
    elif prsn_today > 5000:   sc = -3
    else:                     sc = 0
    result["prsn"] = {"score": sc, "value": prsn_today,
                      "active_pos": sc > 0, "active_neg": sc < 0}

    # 매수압력
    if   buy_pressure > 5:    sc = 5
    elif buy_pressure > 2:    sc = 3
    elif buy_pressure < -5:   sc = -5
    elif buy_pressure < -2:   sc = -3
    else:                     sc = 0
    result["buy_pressure"] = {"score": sc, "value": buy_pressure,
                              "active_pos": sc > 0, "active_neg": sc < 0}

    # MACD
    if   macd_hist > 0:  sc = 8
    elif macd_hist < 0:  sc = -6
    else:                sc = 0
    result["macd"] = {"score": sc, "value": macd_hist,
                      "active_pos": sc > 0, "active_neg": sc < 0}

    # 볼린저밴드
    sc = 0
    if   bb_pct < 0.2:        sc += 7
    elif bb_pct > 0.85:       sc -= 7
    elif 0.3 < bb_pct < 0.7:  sc += 2
    if 0 < bb_width < 0.05:   sc += 3
    result["bb"] = {"score": sc, "value": bb_pct,
                    "active_pos": sc > 0, "active_neg": sc < 0}

    # 스토캐스틱
    if   stoch_k < 20:        sc = 6
    elif stoch_k > 80:        sc = -6
    elif 40 < stoch_k < 60:   sc = 2
    else:                     sc = 0
    result["stoch"] = {"score": sc, "value": stoch_k,
                       "active_pos": sc > 0, "active_neg": sc < 0}

    # 캔들 패턴
    if   candle_pat == 1:  sc = 4
    elif candle_pat == -1: sc = -4
    else:                  sc = 0
    result["candle"] = {"score": sc, "value": candle_pat,
                        "active_pos": sc > 0, "active_neg": sc < 0}

    # 호가잔량
    sc = 0
    if bid_rsqn > 0:
        hoga_ratio = ask_rsqn / bid_rsqn
        if   hoga_ratio >= 5.0: sc = 15
        elif hoga_ratio >= 3.0: sc = 10
        elif hoga_ratio >= 2.0: sc = 5
        elif hoga_ratio <= 0.3: sc = -10
        elif hoga_ratio <= 0.5: sc = -5
        result["hoga"] = {"score": sc, "value": hoga_ratio,
                          "active_pos": sc > 0, "active_neg": sc < 0}
    else:
        result["hoga"] = {"score": 0, "value": 0,
                          "active_pos": False, "active_neg": False}

    total = base + sum(v["score"] for v in result.values())
    result["__base__"] = base
    result["__total__"] = max(0, min(100, total))
    return result


# ============================================================
# 기여도 분석기
# ============================================================
class AttributionAnalyzer:
    """
    trades: BacktestEngine.get_trades() 반환값 (list of dict)
    score_logs: [{
        "code": str, "date": str,
        "features": dict,          # build_features_at() 결과
        "profit_rate": float,      # 해당 거래 수익률
    }]
    score_logs가 없으면 trades의 score 필드만으로 제한적 분석
    """

    def __init__(self, trades: list, score_logs: list = None):
        self.trades = [t for t in trades if t.get("sell_price")]
        self.score_logs = score_logs or []
        self._breakdowns = []  # [{indicator_id: {score, value, ...}, profit_rate}]

    def _build_breakdowns(self):
        """score_logs에서 지표별 breakdown × profit_rate 페어 구성"""
        self._breakdowns = []
        for log in self.score_logs:
            features = log.get("features", {})
            profit   = log.get("profit_rate", 0)
            if not features:
                continue
            bd = get_rule_score_breakdown(features)
            bd["__profit_rate__"] = profit
            self._breakdowns.append(bd)

    def _attribution_by_activation(self) -> dict:
        """
        지표 활성(양/음) 여부별 평균 수익률 차이 → 기여도 근사
        result[indicator_id] = {
            "pos_mean": 양의 기여 시 평균 수익,
            "pos_n":    건수,
            "neg_mean": 음의 기여 시 평균 수익,
            "neg_n":    건수,
            "neutral_mean": 중립 시 평균 수익,
            "effect": pos_mean - neg_mean (클수록 중요한 지표)
        }
        """
        agg = defaultdict(lambda: {
            "pos": [], "neg": [], "neutral": []
        })
        for bd in self._breakdowns:
            pr = bd["__profit_rate__"]
            for iid in INDICATOR_META:
                info = bd.get(iid, {})
                if not info:
                    continue
                if info["active_pos"]:
                    agg[iid]["pos"].append(pr)
                elif info["active_neg"]:
                    agg[iid]["neg"].append(pr)
                else:
                    agg[iid]["neutral"].append(pr)

        result = {}
        for iid, data in agg.items():
            pos_m = _mean(data["pos"])
            neg_m = _mean(data["neg"])
            neu_m = _mean(data["neutral"])
            result[iid] = {
                "pos_mean":     round(pos_m * 100, 3),   # %
                "pos_n":        len(data["pos"]),
                "neg_mean":     round(neg_m * 100, 3),
                "neg_n":        len(data["neg"]),
                "neutral_mean": round(neu_m * 100, 3),
                "neutral_n":    len(data["neutral"]),
                "effect":       round((pos_m - neg_m) * 100, 3),  # 핵심 지표
            }
        return result

    def _correlation_analysis(self) -> dict:
        """각 지표 점수와 profit_rate의 피어슨 상관계수"""
        scores_by_iid = defaultdict(list)
        profits = []
        for bd in self._breakdowns:
            pr = bd["__profit_rate__"]
            profits.append(pr)
            for iid in INDICATOR_META:
                info = bd.get(iid, {})
                scores_by_iid[iid].append(info.get("score", 0) if info else 0)

        result = {}
        for iid, scores in scores_by_iid.items():
            corr = _pearson(scores, profits)
            result[iid] = round(corr, 4) if corr is not None else 0.0
        return result

    def _win_rate_by_indicator(self) -> dict:
        """지표 활성 시 승률(profit_rate > 0) vs 비활성 시 승률"""
        agg = defaultdict(lambda: {"pos_wins": 0, "pos_n": 0, "off_wins": 0, "off_n": 0})
        for bd in self._breakdowns:
            pr = bd["__profit_rate__"]
            win = pr > 0
            for iid in INDICATOR_META:
                info = bd.get(iid, {})
                if not info:
                    continue
                if info["active_pos"]:
                    agg[iid]["pos_n"] += 1
                    if win: agg[iid]["pos_wins"] += 1
                else:
                    agg[iid]["off_n"] += 1
                    if win: agg[iid]["off_wins"] += 1

        result = {}
        for iid, d in agg.items():
            result[iid] = {
                "winrate_on":  round(d["pos_wins"] / d["pos_n"] * 100, 1) if d["pos_n"] else None,
                "winrate_off": round(d["off_wins"] / d["off_n"] * 100, 1) if d["off_n"] else None,
                "winrate_lift": round(
                    (d["pos_wins"] / d["pos_n"] - d["off_wins"] / d["off_n"]) * 100, 1
                ) if (d["pos_n"] and d["off_n"]) else None,
            }
        return result

    def _score_distribution(self) -> dict:
        """지표별 점수 분포 (평균, 표준편차, 활성 비율)"""
        scores_by_iid = defaultdict(list)
        for bd in self._breakdowns:
            for iid in INDICATOR_META:
                info = bd.get(iid, {})
                scores_by_iid[iid].append(info.get("score", 0) if info else 0)

        result = {}
        for iid, scores in scores_by_iid.items():
            n = len(scores)
            if n == 0:
                continue
            mu = sum(scores) / n
            sigma = math.sqrt(sum((s - mu) ** 2 for s in scores) / n) if n > 1 else 0
            pos_ratio = sum(1 for s in scores if s > 0) / n
            neg_ratio = sum(1 for s in scores if s < 0) / n
            result[iid] = {
                "mean":      round(mu, 2),
                "std":       round(sigma, 2),
                "pos_ratio": round(pos_ratio * 100, 1),
                "neg_ratio": round(neg_ratio * 100, 1),
                "n":         n,
            }
        return result

    def _generate_recommendations(self, attribution, correlation, winrate) -> list:
        """분석 결과 기반 가중치 조정 추천"""
        recs = []
        for iid, meta in INDICATOR_META.items():
            att  = attribution.get(iid, {})
            corr = correlation.get(iid, 0)
            wr   = winrate.get(iid, {})

            effect   = att.get("effect", 0)
            lift     = wr.get("winrate_lift", 0) or 0
            pos_n    = att.get("pos_n", 0)

            # 데이터 부족 스킵
            if pos_n < 5:
                continue

            # 없애야 할 지표: effect 음수 + 상관관계 음수
            if effect < -0.3 and corr < -0.05:
                recs.append({
                    "indicator": iid,
                    "name": meta["name"],
                    "action": "REMOVE",
                    "reason": f"활성 시 평균수익 {effect:+.2f}%p 낮아짐, 상관 {corr:+.4f}",
                    "priority": abs(effect),
                })
            # 가중치 올려야 할 지표: effect 양수 + 상관관계 양수 + 승률 리프트 양수
            elif effect > 0.5 and corr > 0.05 and lift > 2:
                recs.append({
                    "indicator": iid,
                    "name": meta["name"],
                    "action": "BOOST",
                    "reason": f"활성 시 {effect:+.2f}%p 높음, 상관 {corr:+.4f}, 승률 리프트 {lift:+.1f}%p",
                    "priority": effect,
                })
            # 노이즈 지표: effect 작고 pos_n 많은데 기여 없음
            elif abs(effect) < 0.1 and pos_n > 20 and abs(corr) < 0.02:
                recs.append({
                    "indicator": iid,
                    "name": meta["name"],
                    "action": "NOISE",
                    "reason": f"다수 거래({pos_n}건)에서 효과 거의 없음 (effect {effect:+.2f}%p)",
                    "priority": 0,
                })

        recs.sort(key=lambda r: -r["priority"])
        return recs

    def run(self) -> dict:
        """전체 분석 실행 → 리포트 dict 반환"""
        print("🔍 지표별 기여도 분해 분석 시작...")

        if not self._breakdowns:
            self._build_breakdowns()

        if not self._breakdowns:
            print("⚠️ score_logs 없음 — trades의 score 필드로 제한 분석")
            return self._limited_analysis()

        print(f"   분석 대상: {len(self._breakdowns)}건")

        attribution  = self._attribution_by_activation()
        correlation  = self._correlation_analysis()
        winrate      = self._win_rate_by_indicator()
        distribution = self._score_distribution()
        recs         = self._generate_recommendations(attribution, correlation, winrate)

        # 전체 거래 요약
        all_profits = [bd["__profit_rate__"] for bd in self._breakdowns]
        summary = {
            "total_trades": len(all_profits),
            "win_rate":     round(sum(1 for p in all_profits if p > 0) / len(all_profits) * 100, 1),
            "avg_profit":   round(_mean(all_profits) * 100, 3),
            "analyzed_at":  datetime.datetime.now().isoformat(timespec="seconds"),
        }

        return {
            "summary":      summary,
            "attribution":  attribution,
            "correlation":  correlation,
            "winrate":      winrate,
            "distribution": distribution,
            "recommendations": recs,
        }

    def _limited_analysis(self) -> dict:
        """score_logs 없을 때 trades의 score 필드만으로 분석 (제한적)"""
        score_profit = [(t.get("score", 0), t.get("profit_rate", 0))
                        for t in self.trades if t.get("sell_price")]
        if not score_profit:
            return {"error": "분석할 거래 데이터 없음"}

        # 점수 구간별 수익률
        buckets = defaultdict(list)
        for sc, pr in score_profit:
            bucket = (sc // 5) * 5
            buckets[bucket].append(pr)

        score_analysis = {
            str(b): {
                "n": len(prs),
                "avg_profit": round(_mean(prs) * 100, 3),
                "win_rate": round(sum(1 for p in prs if p > 0) / len(prs) * 100, 1),
            }
            for b, prs in sorted(buckets.items())
        }
        return {
            "mode": "limited (no score_logs)",
            "score_bucket_analysis": score_analysis,
            "total_trades": len(score_profit),
        }

    def print_report(self, report: dict):
        """콘솔 출력"""
        print("\n" + "=" * 70)
        print("📊 지표별 기여도 분해 분석 리포트")
        print("=" * 70)

        if "error" in report:
            print(f"❌ {report['error']}")
            return

        s = report.get("summary", {})
        print(f"총 거래: {s.get('total_trades')}건 | "
              f"승률: {s.get('win_rate')}% | "
              f"평균 수익: {s.get('avg_profit'):+.3f}%")

        print("\n【 지표별 효과 (활성 vs 비활성 평균수익 차이) 】")
        print(f"{'지표':<16} {'효과':>8} {'활성수익':>10} {'비활성수익':>10} "
              f"{'상관':>8} {'승률리프트':>10}")
        print("-" * 70)

        att  = report.get("attribution", {})
        corr = report.get("correlation", {})
        wr   = report.get("winrate", {})

        rows = []
        for iid, meta in INDICATOR_META.items():
            a = att.get(iid, {})
            c = corr.get(iid, 0)
            w = wr.get(iid, {})
            rows.append({
                "iid": iid, "name": meta["name"],
                "effect": a.get("effect", 0),
                "pos_mean": a.get("pos_mean", 0),
                "neg_mean": a.get("neg_mean", 0),
                "corr": c,
                "lift": w.get("winrate_lift", 0) or 0,
            })

        rows.sort(key=lambda r: -r["effect"])
        for r in rows:
            mark = "🟢" if r["effect"] > 0.3 else ("🔴" if r["effect"] < -0.3 else "⚪")
            print(f"{mark} {r['name']:<14} "
                  f"{r['effect']:>+7.2f}%p "
                  f"{r['pos_mean']:>+9.2f}% "
                  f"{r['neg_mean']:>+9.2f}% "
                  f"{r['corr']:>+7.4f} "
                  f"{r['lift']:>+9.1f}%p")

        recs = report.get("recommendations", [])
        if recs:
            print("\n【 권고 사항 】")
            for rec in recs:
                icon = {"REMOVE": "❌", "BOOST": "⬆️", "NOISE": "🔇"}.get(rec["action"], "•")
                print(f"  {icon} [{rec['action']}] {rec['name']}: {rec['reason']}")

        print("=" * 70)

    def save_json(self, report: dict, path: str):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2, default=str)
        print(f"💾 분석 결과 저장: {path}")


# ============================================================
# 헬퍼
# ============================================================
def _mean(lst: list) -> float:
    return sum(lst) / len(lst) if lst else 0.0

def _pearson(x: list, y: list) -> Optional[float]:
    """피어슨 상관계수"""
    n = len(x)
    if n < 3:
        return None
    mx, my = _mean(x), _mean(y)
    num = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y))
    dx  = math.sqrt(sum((xi - mx) ** 2 for xi in x))
    dy  = math.sqrt(sum((yi - my) ** 2 for yi in y))
    if dx == 0 or dy == 0:
        return None
    return num / (dx * dy)


# ============================================================
# backtest_engine.py 연계 패치
# ============================================================
def patch_backtest_engine_for_attribution():
    """
    기존 BacktestEngine._replay_day()에서 매수 시 features를
    score_log에 기록하도록 monkey-patch하는 함수.

    사용:
        from indicator_attribution import patch_backtest_engine_for_attribution
        score_logs = []
        patch_backtest_engine_for_attribution()
        # ... engine.run() ...
        analyzer = AttributionAnalyzer(engine.get_trades(), score_logs)
    """
    # 이 함수는 실제 서버에서 BacktestEngine import 후 패치로 사용
    # 아래는 패치 코드 템플릿 (실제 적용 시 붙여넣기)
    PATCH_CODE = '''
# indicator_attribution.py 연계 패치
# backtest_engine.py의 _replay_day() 내 매수 후보 평가 블록에 추가:

score_logs = []  # 엔진 외부에서 선언

# 기존 코드 (line ~411):
# if final_score >= self.config.buy_score_min:
#     candidates.append((final_score, code, features, buy_tag))

# ★ 아래 코드를 위 라인 직후에 추가:
# if final_score >= self.config.buy_score_min:
#     candidates.append((final_score, code, features, buy_tag))
#     # ★ 기여도 분석용 로그
#     score_logs.append({
#         "code": code, "date": date_str,
#         "features": features,
#         "profit_rate": 0,  # 나중에 거래 완료 후 업데이트
#     })
'''
    return PATCH_CODE


# ============================================================
# 독립 실행 (시뮬레이션 데이터로 테스트)
# ============================================================
if __name__ == "__main__":
    import random
    random.seed(42)

    print("🧪 시뮬레이션 데이터로 기여도 분석 테스트")

    def _make_fake_features(profit_bias: float) -> dict:
        """profit_bias > 0이면 좋은 지표값, < 0이면 나쁜 지표값"""
        b = profit_bias
        return {
            "change_rate":    random.gauss(3 * b, 2),
            "trading_value":  random.gauss(200 + 100 * b, 80),
            "volume_ratio":   random.gauss(200 + 80 * b, 60),
            "vol_tnrt":       random.gauss(30 + 20 * b, 15),
            "rsi":            random.gauss(55 - 5 * b, 10),
            "ma5":            100 + 5 * b,
            "ma20":           98 + 2 * b,
            "ma60":           95,
            "foreign_5d":     random.gauss(3000 * b, 2000),
            "institution_5d": random.gauss(2000 * b, 1500),
            "foreign_today":  random.gauss(1000 * b, 800),
            "orgn_today":     random.gauss(500 * b, 400),
            "prsn_today":     random.gauss(-800 * b, 600),
            "foreign_ratio":  random.gauss(52 + 3 * b, 5),
            "buy_pressure":   random.gauss(2 * b, 2),
            "macd_hist":      random.gauss(0.5 * b, 0.5),
            "bb_pct":         random.gauss(0.4 - 0.1 * b, 0.2),
            "bb_width":       random.gauss(0.04, 0.02),
            "stoch_k":        random.gauss(45 - 10 * b, 15),
            "candle_pattern": random.choice([0, 0, 1 if b > 0 else -1]),
            "total_ask_rsqn": random.gauss(3000 * max(b, 0.1), 1000),
            "total_bid_rsqn": random.gauss(1000, 400),
        }

    # 500건 시뮬레이션
    logs = []
    fake_trades = []
    for i in range(500):
        # 수익 거래: 좋은 지표 / 손실 거래: 나쁜 지표 (상관관계 시뮬레이션)
        is_win = random.random() < 0.55
        bias   = random.gauss(0.8 if is_win else -0.5, 0.3)
        feat   = _make_fake_features(bias)
        pr     = random.gauss(0.04 if is_win else -0.03, 0.02)
        logs.append({"code": f"TEST{i:03d}", "date": "2025-01-01",
                     "features": feat, "profit_rate": pr})
        fake_trades.append({
            "code": f"TEST{i:03d}", "buy_date": "2025-01-01",
            "sell_date": "2025-01-02", "buy_price": 10000,
            "sell_price": 10000 * (1 + pr),
            "profit_rate": pr, "score": 70,
        })

    analyzer = AttributionAnalyzer(fake_trades, logs)
    report   = analyzer.run()
    analyzer.print_report(report)

    # JSON 저장
    out = os.path.join(os.path.dirname(__file__), "attribution_test.json")
    analyzer.save_json(report, out)
