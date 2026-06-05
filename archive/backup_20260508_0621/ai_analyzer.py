"""
ai_analyzer.py — Claude AI 종목 분석
"""
import os
import re
import json
import datetime
from anthropic import Anthropic
from consensus import apply_consensus_bonus


class AIAnalyzer:

    def __init__(self, db_manager):
        self.llm = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        self.db  = db_manager

    def analyze(self, code: str, data: dict,
                active_sectors: list = None) -> dict:
        now_t = datetime.datetime.now().strftime("%H%M")
        valid = ("0900" <= now_t <= "1530") or ("1800" <= now_t <= "2000")

        if valid:
            cached = self.db.get_ai_cache(code)
            if cached:
                print(f"   💾 DB캐시 {code} | {cached['score']}점 | {cached['analyzed_at'][:10]}")
                return {"score": cached["score"], "reason": cached["reason"]}

        try:
            ma5  = data.get("ma5",  0)
            ma20 = data.get("ma20", 0)
            ma60 = data.get("ma60", 0)

            hist      = self.db.get_trade_history(code, limit=5)
            perf      = self.db.get_recent_performance(limit=20)
            hist_text = "\n[이 종목 과거 매매 이력 (최근 5건)]\n"
            if hist:
                for h in hist:
                    buy_t, sell_t, bp, sp, pr, sr, ais, air = h
                    hist_text += (
                        f"- {buy_t[:10]} 매수{bp:,}→매도{sp:,}원 "
                        f"수익률:{pr:+.1f}% 사유:{sr} AI점수:{ais}\n"
                    )
            else:
                hist_text = "\n[이 종목 과거 매매 이력] 없음\n"

            perf_text = ""
            if perf:
                perf_text = (
                    f"\n[봇 최근 {perf['total']}건 전체 성과]\n"
                    f"- 승률: {perf['win_rate']}% | 평균수익: {perf['avg_profit']:+.2f}%\n"
                    f"- 최고: {perf['best']:+.2f}% | 최저: {perf['worst']:+.2f}%\n"
                )

            sector_hint = ""
            if data.get("buy_tag") == "theme_buy":
                sectors = ', '.join(active_sectors or []) or '없음'
                sector_hint = f"\n[업종/테마 정보]\n- 오늘 강세 업종/테마 종목\n- 활성업종: {sectors}\n"

            prompt = (
                "당신은 한국 주식 단타~스윙 트레이더 전문가입니다.\n"
                "아래 종목 지표와 과거 매매 이력을 함께 분석해\n"
                "매수 점수(0~100)와 간단한 이유를 JSON으로 반환하세요.\n\n"
                f"[종목코드] {code}\n\n"
                "[기본 지표]\n"
                f"- 현재가: {data.get('current_price', 0):,}원\n"
                f"- 등락률: {data.get('change_rate', 0):+.2f}%\n"
                f"- 거래대금: {data.get('trading_value', 0):,}억원\n"
                f"- 거래량: {data.get('volume', 0):,}주\n"
                f"- 거래량증가율: {data.get('volume_ratio', 0):.1f}%\n"
                f"- 거래량회전율: {data.get('vol_tnrt', 0):.2f}%\n\n"
                "[기술적 지표]\n"
                f"- MA5: {ma5:,.0f}원 | MA20: {ma20:,.0f}원 | MA60: {ma60:,.0f}원\n"
                f"- RSI14: {data.get('rsi', 50):.1f}\n"
                f"- MA 정배열: {ma5 > ma20 > ma60}\n\n"
                "[수급]\n"
                f"- 외국인 5일 순매수: {data.get('foreign_5d', 0):,}백만원\n"
                f"- 기관 5일 순매수: {data.get('institution_5d', 0):,}백만원\n"
                + sector_hint
                + "[판단 기준]\n"
                "- 단타~스윙 전략 (보유기간 1일~1주)\n"
                "- 급등 초입 or 눌림목 반등 선호\n"
                "- 거래량 급증 + 등락률 양봉 + 수급 우호 → 높은 점수\n"
                "- 과매수(RSI>75) or 하락추세 → 낮은 점수\n"
                "- 상한가 근접(등락률>15%) → 0점\n"
                "- 오늘 강세 업종/테마 종목이면 모멘텀 가산\n"
                "- 과거 손절 이력 있으면 신중하게 판단\n\n"
                + hist_text + perf_text
                + "\n반드시 아래 JSON 형식으로만 답하세요:\n"
                + '{"score": 75, "reason": "이유 한 줄"}'
            )

            res   = self.llm.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=256,
                messages=[{"role": "user", "content": prompt}]
            )
            text  = res.content[0].text.strip()
            text  = re.sub(r"```(?:json)?", "", text).strip()
            match = re.search(r'\{.*\}', text, re.DOTALL)
            if not match:
                print(f"⚠️ Claude 응답 파싱 불가 {code}: [{text[:80]}]")
                return {"score": 0, "reason": "파싱실패-룰점수사용"}

            result = json.loads(match.group())
            score  = max(0, min(100, int(result.get("score", 0))))
            reason = result.get("reason", "")

            if valid:
                self.db.save_ai_cache(code, score, reason)

            # ★ 컨센서스 가점 적용
            current_price = data.get("current_price", 0)
            cons_score, cons_reason = apply_consensus_bonus(code, score, current_price)
            if cons_reason:
                score  = cons_score
                reason = f"{reason} | {cons_reason}"

            return {"score": score, "reason": reason}
        except Exception as e:
            print(f"⚠️ Claude 분석 오류 {code}: {e}")
            return {"score": 0, "reason": "분석실패"}
