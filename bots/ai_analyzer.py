"""
ai_analyzer.py — Claude AI 종목 분석 (개선판)
================================================================
[이 파일이 하는 일 — 비개발자용 설명]

룰 점수에서 상위 후보들을 Claude AI에게 보내 추가 분석을 받습니다.
숫자만 보는 룰 점수와 달리, AI는 다음을 종합 판단합니다:
- 차트 패턴의 의미 (예: 정배열이 갓 시작된 강한 신호인지)
- 과거 매매 이력 (예: 이 종목에서 과거에 손절했는지)
- 봇의 최근 성과 (승률이 낮으면 더 깐깐하게)
- 업종/테마 모멘텀

[주요 개선 사항]
1. ★ 점수 분포를 프롬프트에 명시 (90+/70+/50+/0+ 구간별 정의)
2. ★ 컨센서스 가점을 캐시에 포함 → 매번 일관된 점수
3. ★ 가격 변동 감지 — DB에서 캐시 무효화 자동 처리
4. ★ 환경변수로 모델 선택 가능 (ANTHROPIC_MODEL)
5. ★ 매개변수 검증 강화 (None/0 들어와도 안전)
================================================================
"""
import os
import re
import json
import datetime
from typing import Optional
from anthropic import Anthropic
from consensus import apply_consensus_bonus
try:
    import openai as _openai
except ImportError:
    _openai = None


# 환경변수로 모델 선택 가능
DEFAULT_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
USE_OLLAMA    = os.getenv("USE_OLLAMA", "false").lower() == "true"
OLLAMA_MODEL  = os.getenv("OLLAMA_MODEL", "qwen2.5:14b")
OLLAMA_URL    = os.getenv("OLLAMA_URL", "http://localhost:11434")


class AIAnalyzer:
    """단타봇용 AI 분석기."""

    def __init__(self, db_manager):
        self.db  = db_manager
        if USE_OLLAMA and _openai:
            self.llm   = _openai.OpenAI(
                base_url=f"{OLLAMA_URL}/v1",
                api_key="ollama",
            )
            self.model = OLLAMA_MODEL
            print(f"🤖 로컬 AI 모드: {OLLAMA_MODEL}")
        else:
            self.llm   = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
            self.model = DEFAULT_MODEL
            print(f"☁️ Claude API 모드: {DEFAULT_MODEL}")

    def analyze(self, code: str, data: dict,
                active_sectors: list = None,
                market_status: str = "normal", market_rate: float = 0.0) -> dict:
        """
        종목 분석.
        반환: {"score": 0~100, "reason": "이유"}
        """
        active_sectors = active_sectors or []
        current_price = data.get("current_price", 0)
        if current_price <= 0:
            return {"score": 0, "reason": "가격 데이터 없음"}

        # ----------------------------------------------------------
        # 1. 캐시 조회 (24시간 이내 + 가격 변동 5% 이내일 때)
        # ----------------------------------------------------------
        now_t = datetime.datetime.now().strftime("%H%M")
        # 정규장 시간 또는 분석 시간(18:00~20:00)일 때만 캐시 활용
        in_valid_window = ("0900" <= now_t <= "1530") or ("1800" <= now_t <= "2000")

        if in_valid_window:
            cached = self.db.get_ai_cache(code, current_price=current_price)
            # 약세장/stop 시 캐시 무효화 — 시장 상황 즉시 반영
            if cached and market_status in ("weak", "stop"):
                cached = None
            if cached:
                print(f"   💾 캐시 {code} | {cached['score']}점 | "
                      f"{cached['analyzed_at'][:16]}")
                return {
                    "score":  cached["score"],
                    "reason": cached["reason"],
                }

        # ----------------------------------------------------------
        # 2. AI 분석 수행
        # ----------------------------------------------------------
        try:
            prompt = self._build_prompt(code, data, active_sectors, market_status, market_rate)

            if USE_OLLAMA and _openai:
                res = self.llm.chat.completions.create(
                    model=self.model,
                    max_tokens=300,
                    messages=[{"role": "user", "content": prompt}],
                )
                text = res.choices[0].message.content.strip()
            else:
                res = self.llm.messages.create(
                    model=self.model,
                    max_tokens=300,
                    messages=[{"role": "user", "content": prompt}],
                )
                text = res.content[0].text.strip()
            text  = re.sub(r"```(?:json)?", "", text).strip()
            match = re.search(r'\{.*\}', text, re.DOTALL)

            if not match:
                print(f"⚠️ AI 응답 파싱 불가 {code}: [{text[:80]}]")
                return {"score": 0, "reason": "파싱실패-룰점수사용"}

            result = json.loads(match.group())
            score  = max(0, min(100, int(result.get("score", 0))))
            reason = str(result.get("reason", ""))[:200]  # 너무 긴 응답 자르기

            # ----------------------------------------------------------
            # 3. ★ 컨센서스 가점 적용 (캐시 저장 전!)
            # ----------------------------------------------------------
            cons_score, cons_reason = apply_consensus_bonus(code, score, current_price)
            if cons_reason:
                score  = cons_score
                reason = f"{reason} | {cons_reason}"

            # 4. 캐시 저장 (가점 포함된 최종 점수)
            if in_valid_window:
                self.db.save_ai_cache(code, score, reason, current_price=current_price)

            return {"score": score, "reason": reason}

        except Exception as e:
            print(f"⚠️ AI 분석 오류 {code}: {e}")
            return {"score": 0, "reason": "분석실패"}

    # ============================================================
    # 프롬프트 빌더 (★ 점수 분포 명확화)
    # ============================================================
    def _build_prompt(self, code: str, data: dict,
                      active_sectors: list,
                      market_status: str = "normal", market_rate: float = 0.0) -> str:
        """Claude에게 보낼 프롬프트 생성."""

        ma5  = data.get("ma5",  0)
        ma20 = data.get("ma20", 0)
        ma60 = data.get("ma60", 0)

        # 과거 매매 이력
        hist = self.db.get_trade_history(code, limit=5)
        hist_text = "\n[이 종목 과거 매매 이력]\n"
        if hist:
            for h in hist:
                buy_t, sell_t, bp, sp, pr, sr, ais, _ = h
                hist_text += (
                    f"- {buy_t[:10]} {bp:,}→{sp:,}원 "
                    f"({pr:+.1f}%) {sr} AI:{ais}점\n"
                )
        else:
            hist_text += "없음 (첫 거래)\n"

        # 봇의 최근 성과
        perf = self.db.get_recent_performance(limit=20)
        perf_text = ""
        if perf:
            perf_text = (
                f"\n[봇 최근 {perf['total']}건 성과]\n"
                f"- 승률: {perf['win_rate']}% | 평균: {perf['avg_profit']:+.2f}%\n"
                f"- 최고: {perf['best']:+.2f}% / 최저: {perf['worst']:+.2f}%\n"
            )

        # 업종/테마 힌트
        sector_hint = ""
        if data.get("buy_tag") == "theme_buy":
            sectors = ", ".join(active_sectors) if active_sectors else "없음"
            sector_hint = (
                f"\n[업종/테마 정보]\n"
                f"- 오늘 강세 업종: {sectors}\n"
                f"- 이 종목은 강세 업종/테마에 속함\n"
            )

        # 본 프롬프트
        return (
            "당신은 15년 경력의 한국 주식 단타 전문 트레이더입니다.\n"
            "한국 주식 시장의 특성(수급 패턴, 테마 순환, 개인/외국인/기관 행동패턴)에 "
            "대한 당신의 전문 지식을 최대한 활용하여 판단하세요.\n"
            "아래 종목 지표와 과거 이력을 종합 분석해 매수 점수(0~100)를 매겨주세요.\n\n"

            f"[종목코드] {code}\n\n"

            f"[시장 상황]\n"
            f"- 코스피 등락률: {market_rate:+.2f}%\n"
            f"- 시장 상태: {market_status} "
            f"({'⚠️ 약세장 — 보수적 판단 필요' if market_status in ('weak','stop') else '✅ 정상'})\n\n"
            "[기본 지표]\n"
            f"- 현재가: {data.get('current_price', 0):,}원\n"
            f"- 등락률: {data.get('change_rate', 0):+.2f}%\n"
            f"- 거래대금: {data.get('trading_value', 0):,}억원\n"
            f"- 거래량: {data.get('volume', 0):,}주\n"
            f"- 거래량 증가율: {data.get('volume_ratio', 0):.1f}%\n"
            f"- 거래량 회전율: {data.get('vol_tnrt', 0):.2f}%\n\n"

            "[기술적 지표]\n"
            f"- MA5: {ma5:,.0f}원 / MA20: {ma20:,.0f}원 / MA60: {ma60:,.0f}원\n"
            f"- MA 정배열(5>20>60): {ma5 > ma20 > ma60}\n"
            f"- RSI14: {data.get('rsi', 50):.1f} (30이하 과매도/70이상 과매수)\n"
            f"- MACD Hist: {data.get('macd_hist', 0):.2f} (양수=상승모멘텀)\n"
            f"- 볼린저밴드 위치: {data.get('bb_pct', 0.5):.2f} (0=하단/1=상단)\n"
            f"- 스토캐스틱K: {data.get('stoch_k', 50):.1f}\n"
            f"- 당일 고가: {data.get('stck_hgpr', 0):,}원 / 저가: {data.get('stck_sdpr', 0):,}원\n\n"

            "[수급]\n"
            f"- 외국인 당일: {data.get('foreign_today', 0):,}백만원 / 5일: {data.get('foreign_5d', 0):,}백만원\n"
            f"- 기관 당일: {data.get('orgn_today', 0):,}백만원 / 5일: {data.get('institution_5d', 0):,}백만원\n"
            f"- 호가 매수/매도 비율: {data.get('ask_bid_ratio', 1.0):.2f} (1미만=매수우세)\n"

            + sector_hint
            + hist_text
            + perf_text
            + self._get_event_hint(active_sectors)
            + self._get_review_hint()
            + self._get_news_hint()

            + "\n[★ 점수 기준 — 단타봇 특성 반영 (당일 모멘텀 중심)]\n"
            "기본 출발점은 60점. 아래 조건에 따라 가감:\n"
            "- 85~100: 강력 매수. MA정배열+강한수급+모멘텀 모두 우수\n"
            "- 70~84:  매수 추천. 2가지 이상 강한 신호. 단타 진입 적합\n"
            "- 55~69:  조건부 매수. 1가지 강한 신호\n"
            "- 40~54:  관망. 신호 혼조\n"
            "- 0~39:   회피. 부정적 신호 우세\n\n"

            "[가점 요소]\n"
            "- MA 정배열(5>20>60) + 거래량 급증: +10~15점\n"
            "- 외국인+기관 동반 순매수: +10점\n"
            "- 강세 테마/업종 소속: +5~10점\n"
            "- RSI 50~65 적정 구간: +5점\n"
            "- 당일 등락률 +2~+8%: +5점\n\n"

            "[감점 요소]\n"
            "- 등락률 +20% 초과: -15점 (과열)\n"
            "- RSI 75 초과: -10점 (과매수)\n"
            "- 외국인+기관 모두 대량 순매도: -15점\n"
            "- 과거 손절 이력 3회 이상: -5점\n"
            "- MA 역배열: -10점\n\n"

            '반드시 아래 JSON으로만 답변:\n'
            '{"score": 75, "reason": "이유 30자 이내"}'
        )


    def _get_event_hint(self, active_sectors: list = None) -> str:
        """텔레그램 빅 이벤트 가산점 프롬프트 주입"""
        try:
            import sqlite3 as _sl
            import os as _os
            _db = _os.path.join(
                _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
                "intelligence", "telegram_events.db"
            )
            if not _os.path.exists(_db):
                return ""
            conn = _sl.connect(_db, timeout=3)
            conn.execute("PRAGMA query_only = ON")
            rows = conn.execute("""
                SELECT theme, bonus_score, reason
                FROM event_bonus
                WHERE expires_at > datetime('now','localtime')
                ORDER BY bonus_score DESC
                LIMIT 5
            """).fetchall()
            conn.close()
            if not rows:
                return ""
            lines = ["\n[★ 실시간 빅 이벤트 — 테마 가산점]"]
            for theme, score, reason in rows:
                lines.append(f"- {theme}: +{score}점 ({reason})")
            lines.append("※ 위 테마 관련 종목은 가산점 적극 반영할 것")
            return "\n".join(lines) + "\n"
        except Exception:
            return ""

    def _get_review_hint(self) -> str:
        """어제 자동 복기 결과를 프롬프트에 주입"""
        try:
            from daily_review import load_yesterday_review
            review = load_yesterday_review()
            if not review:
                return ""
            lines = ["\n[어제 복기 — 내일 전략에 반영]"]
            if review.get("내일전략"):
                lines.append(f"- 오늘 주의사항: {review['내일전략']}")
            if review.get("주의종목유형"):
                lines.append(f"- 피해야 할 종목: {review['주의종목유형']}")
            if review.get("주목시간대"):
                lines.append(f"- 집중 시간대: {review['주목시간대']}")
            if review.get("반복패턴"):
                lines.append(f"- 반복 패턴 주의: {review['반복패턴']}")
            return "\n".join(lines) + "\n"
        except Exception:
            return ""
    def _get_news_hint(self) -> str:
        """오늘 뉴스 감성 분석 결과를 프롬프트에 주입"""
        try:
            import sqlite3 as _sl
            import os as _os
            _db = _os.path.join(
                _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
                "intelligence", "news_sentiment.db"
            )
            if not _os.path.exists(_db):
                return ""
            from datetime import datetime as _dt
            today = _dt.now().strftime("%Y%m%d")
            conn = _sl.connect(_db, timeout=3)
            conn.execute("PRAGMA query_only = ON")
            rows = conn.execute("""
                SELECT keyword,
                       AVG(CASE sentiment
                           WHEN '긍정' THEN 1
                           WHEN '부정' THEN -1
                           ELSE 0 END) as score,
                       COUNT(*) as cnt
                FROM news_sentiment
                WHERE date = ?
                GROUP BY keyword
                ORDER BY score DESC
            """, (today,)).fetchall()
            conn.close()
            if not rows:
                return ""
            lines = ["\n[오늘 뉴스 감성 — 테마별 투자 심리]"]
            for kw, score, cnt in rows:
                emoji = "▲" if score > 0.2 else ("▼" if score < -0.2 else "●")
                lines.append(f"- {emoji} {kw}: {score:+.2f} ({cnt}건)")
            return "\n".join(lines) + "\n"
        except Exception:
            return ""


