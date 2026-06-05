"""
sbot_analyzer.py — 스윙봇 AI 종목 분석
================================================================
[이 파일이 하는 일 — 비개발자용 설명]

스윙봇(며칠~1주일 보유) 전용 AI 분석기.
단타봇(ai_analyzer.py)과 차이점:
  - 일봉 기반 추세 판단 (단타는 분봉 모멘텀)
  - MA20/MA60 이탈 여부 중시
  - 외국인/기관 5일~20일 누적 수급 중시
  - 뉴스 감성 3일치 누적 반영 (단타는 당일만)
  - 점수 기준 스윙 특화 (85+ 강력 / 70+ 매수)

[★ 개선사항]
1. 뉴스 감성 분석 연동 (_get_news_hint)
2. 스윙봇 특화 프롬프트 (일봉 추세 중심)
3. 로컬 AI(Qwen) / Claude API 선택 가능
4. 24시간 캐시 (스윙은 당일 여러 번 재분석 불필요)
================================================================
"""
import os
import re
import json
import sqlite3
import datetime
from typing import Optional
from anthropic import Anthropic
try:
    import openai as _openai
except ImportError:
    _openai = None

try:
    from consensus import apply_consensus_bonus
except ImportError:
    def apply_consensus_bonus(code, score, price):
        return score, ""


# ── 모델 설정 ──────────────────────────────────────────────────
DEFAULT_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
USE_OLLAMA    = os.getenv("USE_OLLAMA", "false").lower() == "true"
OLLAMA_MODEL  = os.getenv("OLLAMA_MODEL", "qwen2.5:14b")
OLLAMA_URL    = os.getenv("OLLAMA_URL", "http://localhost:11434")

# ── DB 경로 ────────────────────────────────────────────────────
_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AI_CACHE_DB     = os.path.join(_BASE, "bots",          "sbot_ai_cache.db")
SBOT_TRADE_DB   = os.path.join(_BASE, "data",          "sbot_trade_history.db")
NEWS_DB         = os.path.join(_BASE, "intelligence",  "news_sentiment.db")


class SwingAnalyzer:
    """스윙봇 전용 AI 분석기."""

    def __init__(self):
        if USE_OLLAMA and _openai:
            self.llm   = _openai.OpenAI(
                base_url=f"{OLLAMA_URL}/v1",
                api_key="ollama",
            )
            self.model = OLLAMA_MODEL
            print(f"🤖 [SWING AI] 로컬 AI 모드: {OLLAMA_MODEL}")
        else:
            self.llm   = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
            self.model = DEFAULT_MODEL
            print(f"☁️  [SWING AI] Claude API 모드: {DEFAULT_MODEL}")

        self._cache_db = self._find_cache_db()

    def _find_cache_db(self) -> str:
        """캐시 DB 경로 탐색 (환경에 따라 경로 다름)"""
        candidates = [
            AI_CACHE_DB,
            os.path.join(_BASE, "sbot_ai_cache.db"),
            os.path.join(os.getcwd(), "sbot_ai_cache.db"),
        ]
        for p in candidates:
            if os.path.exists(p):
                return p
        # 없으면 기본 경로에 생성
        return candidates[0]

    def init_db(self):
        """캐시 DB 초기화"""
        try:
            os.makedirs(os.path.dirname(self._cache_db), exist_ok=True)
        except Exception:
            pass
        conn = sqlite3.connect(self._cache_db, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ai_cache (
                code         TEXT PRIMARY KEY,
                score        INTEGER,
                reason       TEXT,
                analyzed_at  TEXT,
                price_at     REAL DEFAULT 0
            )
        """)
        conn.commit()
        conn.close()

    # ============================================================
    # 메인 분석
    # ============================================================
    def analyze(self, code: str, data: dict,
                new_codes: list = None,
                market_status: str = "normal",
                market_rate: float = 0.0) -> dict:
        """
        스윙봇 종목 분석.
        반환: {"score": 0~100, "reason": "이유"}
        """
        new_codes     = new_codes or []
        current_price = data.get("current_price", 0)
        if current_price <= 0:
            return {"score": 0, "reason": "가격 데이터 없음"}

        # ── 1. 캐시 조회 ────────────────────────────────────────
        now_t = datetime.datetime.now().strftime("%H%M")
        in_valid_window = ("0900" <= now_t <= "1530") or ("1800" <= now_t <= "2000")

        if in_valid_window:
            cached = self._get_cache(code, current_price)
            if cached and market_status not in ("weak", "stop"):
                print(f"   💾 [SWING] 캐시 {code} | {cached['score']}점 | "
                      f"{cached['analyzed_at'][:16]}")
                return {"score": cached["score"], "reason": cached["reason"]}

        # ── 2. AI 분석 ──────────────────────────────────────────
        try:
            prompt = self._build_prompt(
                code, data, new_codes, market_status, market_rate
            )

            if USE_OLLAMA and _openai:
                res  = self.llm.chat.completions.create(
                    model=self.model,
                    max_tokens=300,
                    messages=[{"role": "user", "content": prompt}],
                )
                text = res.choices[0].message.content.strip()
            else:
                res  = self.llm.messages.create(
                    model=self.model,
                    max_tokens=300,
                    messages=[{"role": "user", "content": prompt}],
                )
                text = res.content[0].text.strip()

            text  = re.sub(r"```(?:json)?", "", text).strip()
            match = re.search(r'\{.*\}', text, re.DOTALL)

            if not match:
                print(f"⚠️ [SWING AI] 응답 파싱 불가 {code}: [{text[:80]}]")
                return {"score": 0, "reason": "파싱실패-룰점수사용"}

            result = json.loads(match.group())
            score  = max(0, min(100, int(result.get("score", 0))))
            reason = str(result.get("reason", ""))[:200]

            # ── 3. 컨센서스 가점 ────────────────────────────────
            cons_score, cons_reason = apply_consensus_bonus(code, score, current_price)
            if cons_reason:
                score  = cons_score
                reason = f"{reason} | {cons_reason}"

            # ── 4. 캐시 저장 ────────────────────────────────────
            if in_valid_window:
                self._save_cache(code, score, reason, current_price)

            return {"score": score, "reason": reason}

        except Exception as e:
            print(f"⚠️ [SWING AI] 분석 오류 {code}: {e}")
            return {"score": 0, "reason": "분석실패"}

    # ============================================================
    # 프롬프트 빌더 — 스윙봇 특화
    # ============================================================
    def _build_prompt(self, code: str, data: dict,
                      new_codes: list,
                      market_status: str = "normal",
                      market_rate: float = 0.0) -> str:
        """스윙봇 특화 프롬프트 (일봉 추세 + 수급 중심)"""

        ma5  = data.get("ma5",  0)
        ma20 = data.get("ma20", 0)
        ma60 = data.get("ma60", 0)

        # 과거 매매 이력
        hist      = self._get_trade_history(code)
        hist_text = "\n[이 종목 과거 스윙 매매 이력]\n"
        if hist:
            for row in hist:
                hist_text += f"- {row}\n"
        else:
            hist_text += "없음 (첫 거래)\n"

        # new 그룹 여부
        is_new     = code in new_codes
        new_hint   = "\n⭐ [신규 추천 종목] — AI 분석가 추천 종목입니다.\n" if is_new else ""

        return (
            "당신은 15년 경력의 한국 주식 스윙 트레이더입니다.\n"
            "며칠~1주일 보유 관점으로 아래 종목을 분석해 매수 점수(0~100)를 매겨주세요.\n"
            "단타가 아닌 스윙 관점: 일봉 추세, 5일~20일 수급 누적, 섹터 모멘텀 지속성을 중시합니다.\n\n"

            f"[종목코드] {code}\n"
            + new_hint +
            f"\n[시장 상황]\n"
            f"- 코스피 등락률: {market_rate:+.2f}%\n"
            f"- 시장 상태: {market_status} "
            f"({'⚠️ 약세장 — 보수적 판단' if market_status in ('weak','stop') else '✅ 정상'})\n\n"

            "[기본 지표]\n"
            f"- 현재가: {data.get('current_price', 0):,}원\n"
            f"- 등락률: {data.get('change_rate', 0):+.2f}%\n"
            f"- 거래대금: {data.get('trading_value', 0):,}억원\n"
            f"- 거래량 증가율: {data.get('volume_ratio', 0):.1f}%\n\n"

            "[기술적 지표 — 일봉 기준]\n"
            f"- MA5:  {ma5:,.0f}원\n"
            f"- MA20: {ma20:,.0f}원\n"
            f"- MA60: {ma60:,.0f}원\n"
            f"- MA 정배열(5>20>60): {ma5 > ma20 > ma60 > 0}\n"
            f"- MA20 대비 현재가: {((data.get('current_price',0)-ma20)/ma20*100 if ma20 else 0):+.1f}%\n"
            f"- RSI14: {data.get('rsi', 50):.1f}\n\n"

            "[수급 — 스윙은 5일 누적 중시]\n"
            f"- 외국인 5일 순매수: {data.get('foreign_5d', 0):,}주\n"
            f"- 기관 5일 순매수: {data.get('institution_5d', 0):,}주\n"
            f"- 외국인 당일: {data.get('foreign_today', 0):,}주\n"
            f"- 기관 당일: {data.get('orgn_today', 0):,}주\n\n"

            + hist_text
            + self._get_review_hint()
            + self._get_news_hint()   # ★ 뉴스 감성 (3일치)

            + "\n[★ 점수 기준 — 스윙봇 특성 (일봉 추세 + 수급 지속성)]\n"
            "기본 출발점은 60점. 아래 조건에 따라 가감:\n"
            "- 85~100: 강력 매수. MA정배열+5일수급+모멘텀 모두 우수. 스윙 진입 최적\n"
            "- 70~84:  매수 추천. 2가지 이상 강한 신호. 수급 유입 확인\n"
            "- 55~69:  조건부 매수. 추세는 있으나 수급 확인 필요\n"
            "- 40~54:  관망. 신호 혼조 또는 수급 약함\n"
            "- 0~39:   회피. 역배열 / 수급 이탈 / 테마 소멸\n\n"

            "[가점 요소 — 스윙 특화]\n"
            "- MA 정배열(5>20>60) 완성: +15점\n"
            "- 외국인+기관 5일 동반 순매수: +12점\n"
            "- 거래량 급증 + MA20 돌파: +10점\n"
            "- 뉴스 감성 긍정 테마 소속: +5~8점\n"
            "- 신규 추천(new) 종목: +7점\n\n"

            "[감점 요소 — 스윙 특화]\n"
            "- MA20 하향 이탈 (지지선 붕괴): -15점\n"
            "- RSI 75 초과 (단기 과매수): -10점\n"
            "- 외국인+기관 5일 동반 순매도: -15점\n"
            "- 뉴스 감성 부정 테마: -5~8점\n"
            "- 과거 손절 이력 3회 이상: -5점\n\n"

            '반드시 아래 JSON으로만 답변:\n'
            '{"score": 75, "reason": "이유 30자 이내"}'
        )

    # ============================================================
    # 뉴스 감성 힌트 (★ 핵심 추가 기능)
    # ============================================================
    def _get_news_hint(self) -> str:
        """
        오늘 + 최근 3일치 뉴스 감성 분석 결과를 프롬프트에 주입.
        스윙봇은 당일뿐 아니라 3일치 트렌드가 중요.
        """
        try:
            if not os.path.exists(NEWS_DB):
                # 절대경로 탐색 재시도
                alt = os.path.join(_BASE, "intelligence", "news_sentiment.db")
                if not os.path.exists(alt):
                    return ""
                db_path = alt
            else:
                db_path = NEWS_DB

            conn  = sqlite3.connect(db_path, timeout=3)
            conn.execute("PRAGMA query_only = ON")
            today = datetime.datetime.now().strftime("%Y%m%d")

            # ① 오늘 감성
            rows_today = conn.execute("""
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
                LIMIT 10
            """, (today,)).fetchall()

            # ② 최근 3일 누적 감성 (스윙 특화)
            rows_3d = conn.execute("""
                SELECT keyword,
                       AVG(CASE sentiment
                           WHEN '긍정' THEN 1
                           WHEN '부정' THEN -1
                           ELSE 0 END) as score,
                       COUNT(*) as cnt
                FROM news_sentiment
                WHERE date >= date('now', 'localtime', '-3 days')
                GROUP BY keyword
                HAVING cnt >= 5
                ORDER BY score DESC
                LIMIT 5
            """).fetchall()

            conn.close()

            if not rows_today and not rows_3d:
                return ""

            lines = ["\n[뉴스 감성 — 스윙 투자심리]"]

            if rows_today:
                lines.append("▶ 오늘:")
                for kw, score, cnt in rows_today:
                    emoji = "▲" if score > 0.2 else ("▼" if score < -0.2 else "●")
                    lines.append(f"  {emoji} {kw}: {score:+.2f} ({cnt}건)")

            if rows_3d:
                lines.append("▶ 3일 누적 트렌드 (5건↑):")
                for kw, score, cnt in rows_3d:
                    emoji = "▲" if score > 0.2 else ("▼" if score < -0.2 else "●")
                    lines.append(f"  {emoji} {kw}: {score:+.2f} ({cnt}건)")

            return "\n".join(lines) + "\n"

        except Exception as e:
            print(f"⚠️ [SWING AI] 뉴스 힌트 오류: {e}")
            return ""

    # ============================================================
    # 어제 복기 힌트
    # ============================================================
    def _get_review_hint(self) -> str:
        """어제 자동 복기 결과를 프롬프트에 주입"""
        try:
            from daily_review import load_yesterday_review
            review = load_yesterday_review()
            if not review:
                return ""
            lines = ["\n[어제 복기 — 오늘 전략 반영]"]
            if review.get("내일전략"):
                lines.append(f"- 주의사항: {review['내일전략']}")
            if review.get("주의종목유형"):
                lines.append(f"- 피해야 할 종목: {review['주의종목유형']}")
            if review.get("주목시간대"):
                lines.append(f"- 집중 시간대: {review['주목시간대']}")
            return "\n".join(lines) + "\n"
        except Exception:
            return ""

    # ============================================================
    # 과거 매매 이력 조회
    # ============================================================
    def _get_trade_history(self, code: str, limit: int = 5) -> list:
        """sbot 매매 이력 조회"""
        candidates = [
            SBOT_TRADE_DB,
            os.path.join(_BASE, "sbot_trade_history.db"),
            os.path.join(os.getcwd(), "sbot_trade_history.db"),
        ]
        for db_path in candidates:
            if not os.path.exists(db_path):
                continue
            try:
                conn = sqlite3.connect(db_path, timeout=5)
                conn.execute("PRAGMA query_only = ON")
                rows = conn.execute("""
                    SELECT buy_time, sell_time, buy_price, sell_price,
                           profit_rate, sell_reason, ai_score
                    FROM trades
                    WHERE code = ? AND sell_price IS NOT NULL
                    ORDER BY id DESC LIMIT ?
                """, (code, limit)).fetchall()
                conn.close()
                result = []
                for row in rows:
                    bt, st, bp, sp, pr, sr, ais = row
                    result.append(
                        f"{str(bt)[:10]} {bp:,}→{sp:,}원 "
                        f"({pr:+.1f}%) {sr} AI:{ais}점"
                    )
                return result
            except Exception:
                continue
        return []

    # ============================================================
    # 캐시 조회/저장
    # ============================================================
    def _get_cache(self, code: str, current_price: float) -> Optional[dict]:
        """캐시 조회 (24시간 이내 + 가격 변동 5% 이내)"""
        try:
            conn = sqlite3.connect(self._cache_db, timeout=5)
            conn.execute("PRAGMA query_only = ON")
            row  = conn.execute("""
                SELECT score, reason, analyzed_at, price_at
                FROM ai_cache
                WHERE code = ?
                  AND analyzed_at >= datetime('now', 'localtime', '-24 hours')
            """, (code,)).fetchone()
            conn.close()
            if not row:
                return None
            score, reason, analyzed_at, price_at = row
            # 가격 변동 5% 초과 시 캐시 무효화
            if price_at > 0 and abs(current_price - price_at) / price_at > 0.05:
                return None
            return {"score": score, "reason": reason, "analyzed_at": analyzed_at}
        except Exception:
            return None

    def _save_cache(self, code: str, score: int, reason: str,
                    current_price: float):
        """캐시 저장"""
        try:
            conn = sqlite3.connect(self._cache_db, timeout=5)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("""
                INSERT OR REPLACE INTO ai_cache
                    (code, score, reason, analyzed_at, price_at)
                VALUES (?, ?, ?, datetime('now', 'localtime'), ?)
            """, (code, score, reason, current_price))
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"⚠️ [SWING AI] 캐시 저장 오류: {e}")
