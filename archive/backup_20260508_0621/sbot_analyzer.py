"""
sbot_analyzer.py — 스윙봇 Claude AI 분석
"""
import os
import re
import json
import sqlite3
import datetime
from anthropic import Anthropic

AI_CACHE_DB   = "sbot_ai_cache.db"
AI_CACHE_DAYS = 3


class SwingAnalyzer:

    def __init__(self):
        self.llm = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    def init_db(self):
        try:
            conn = sqlite3.connect(AI_CACHE_DB, timeout=15)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS ai_analysis (
                    code TEXT PRIMARY KEY, score INTEGER NOT NULL,
                    reason TEXT, analyzed_at TEXT NOT NULL
                )
            """)
            conn.commit(); conn.close()
            print(f"✅ 스윙 AI DB ({AI_CACHE_DB})")
        except Exception as e:
            print(f"❌ 스윙 AI DB 오류: {e}")

    def get_cache(self, code: str) -> dict:
        try:
            conn   = sqlite3.connect(AI_CACHE_DB, timeout=15)
            cursor = conn.execute(
                "SELECT score, reason, analyzed_at FROM ai_analysis WHERE code=?", (code,))
            row = cursor.fetchone(); conn.close()
            if not row: return None
            score, reason, analyzed_at = row
            age = (datetime.datetime.now() - datetime.datetime.fromisoformat(analyzed_at)).days
            if age >= AI_CACHE_DAYS: return None
            return {"score": score, "reason": reason}
        except: return None

    def save_cache(self, code: str, score: int, reason: str):
        try:
            now  = datetime.datetime.now().isoformat(timespec="seconds")
            conn = sqlite3.connect(AI_CACHE_DB, timeout=15)
            conn.execute("""
                INSERT INTO ai_analysis (code, score, reason, analyzed_at) VALUES (?,?,?,?)
                ON CONFLICT(code) DO UPDATE SET
                    score=excluded.score, reason=excluded.reason, analyzed_at=excluded.analyzed_at
            """, (code, score, reason, now))
            conn.commit(); conn.close()
        except: pass

    def analyze(self, code: str, data: dict,
                new_codes_list: list = None) -> dict:
        cached = self.get_cache(code)
        if cached:
            print(f"   💾 캐시 {code} | {cached['score']}점")
            return cached

        try:
            ma5  = data.get("ma5",  0)
            ma20 = data.get("ma20", 0)
            ma60 = data.get("ma60", 0)

            new_hint = ""
            if new_codes_list and code in new_codes_list:
                new_hint = "\n[참고] 최근 추천 신규 종목 — 모멘텀 주의깊게 판단\n"

            prompt = (
                "당신은 한국 주식 스윙 트레이더 전문가입니다.\n"
                "아래 종목 지표를 분석해 스윙 매수 점수(0~100)와 이유를 JSON으로 반환하세요.\n\n"
                f"[종목코드] {code}\n\n"
                "[기본 지표]\n"
                f"- 현재가: {data.get('current_price', 0):,}원\n"
                f"- 등락률: {data.get('change_rate', 0):+.2f}%\n"
                f"- 거래대금: {data.get('trading_value', 0):,}억원\n"
                f"- 시가총액: {data.get('mkt_cap', 0):,}억원\n\n"
                "[기술적 지표]\n"
                f"- MA5: {ma5:,.0f} | MA20: {ma20:,.0f} | MA60: {ma60:,.0f}\n"
                f"- RSI14: {data.get('rsi', 50):.1f}\n"
                f"- MA 정배열: {ma5 > ma20 > ma60}\n\n"
                "[수급]\n"
                f"- 외국인 5일: {data.get('foreign_5d', 0):,}백만원\n"
                f"- 기관 5일: {data.get('institution_5d', 0):,}백만원\n"
                + new_hint
                + "[스윙 판단 기준]\n"
                "- 보유기간 3~5일 스윙 전략\n"
                "- MA 정배열 + 수급 우호 + 거래대금 충분 → 높은 점수\n"
                "- 시총 1조~20조 중대형주 선호\n"
                "- RSI 80 이상 과매수 → 낮은 점수\n"
                "- 외국인+기관 동반 순매수 → 가산점\n\n"
                '{"score": 75, "reason": "이유 한 줄"}'
            )

            res   = self.llm.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=256,
                messages=[{"role": "user", "content": prompt}]
            )
            text  = res.content[0].text.strip()
            text  = re.sub(r"```(?:json)?", "", text).strip()
            match = re.search(r'\{.*\}', text, re.DOTALL)
            if not match: return {"score": 0, "reason": "파싱실패"}

            result = json.loads(match.group())
            score  = max(0, min(100, int(result.get("score", 0))))
            reason = result.get("reason", "")
            self.save_cache(code, score, reason)
            return {"score": score, "reason": reason}
        except Exception as e:
            print(f"⚠️ Claude 오류 {code}: {e}")
            return {"score": 0, "reason": "분석실패"}
