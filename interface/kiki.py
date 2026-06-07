"""
kiki.py — 영암9 AI 비서 디스코드 봇 (전면 재구성판 v2)
================================================================
[이 파일이 하는 일 — 비개발자용 설명]

키키는 디스코드에서 영암9 봇들(단타/스윙/종가/코인)을 제어·모니터링하는
AI 비서입니다. 명령어로 봇을 조종하고, 평일 자동 브리핑을 보내고,
사용자의 자연어 질문에 답합니다.

[지원 명령어]
  📈 단타봇:
    !상태          단타봇 현황 (활성 업종 포함)
    !점수기준 70   매수 기준 점수 변경
    !매도 005930   즉시 매도
    !매수 005930 10  수동 매수
    !분석 010820   AI 분석 결과 조회
    !정지 / !시작  중단/재개
    !관심 005930   관심종목 추가/제거
    !관심          관심종목 목록

  📊 스윙봇:
    !s상태 / !s매도 / !s정지 / !s시작 / !s관심

  🌆 종가봇:
    !e상태 / !e성과

  🪙 코인봇:
    !c상태 / !c매도 BTC / !c정지 / !c시작 / !c성과

  🌐 공통:
    !전체상태      모든 봇 현황
    !전체재시작    kiki 제외 전체 재시작
    !테마          당일 강세 업종/테마
    !뉴스          오늘 테마별 뉴스 감성 리포트
    !관심HTS       키움 HTS 관심그룹 즉시 동기화
    !브리핑        즉시 모닝 브리핑
    !저녁브리핑    즉시 저녁 브리핑
    !성과          오늘 손익 (단순)
    !도움말        명령어 목록

[자동 작업]
  🌅 평일 08:00 — 모닝 브리핑
  🔄 평일 09:00 / 11:00 / 14:00 — HTS 관심그룹 자동 동기화
  🌆 평일 20:00 — 저녁 브리핑
  📊 10초마다 — 손익 변동 감지

[적용된 v2 개선사항]
1. ★ common_utils 사용 — atomic 상태파일 쓰기 (JSON 깨짐 방지)
2. ★ ebot 연동 추가 — 종가봇 상태/성과 조회 명령어
3. ★ DB 조회에 WAL 호환 (멀티봇 동시 쓰기 안전)
4. ★ 환경변수 ANTHROPIC_MODEL 지원
5. ★ 디스코드 명령 결과 응답 강화 (cmd_result 폴링 안정화)
6. ★ kiki 자체도 디스코드 알림 재시도 적용 (notify_failed 백업)
7. ★ 코인봇 매도 — 동적 valid 코인 (FIXED_COINS 외 보유 코인도 매도 가능)

[변경 이력]
  2026-04-27 kiki.py 최초 정리
  2026-05-01 통합본 (cbot.py / nbot / sbot 멀티봇 구조)
  2026-05-04 !성과 단순화
  2026-05-08 v2 재구성 (atomic write + ebot 연동 + 동적 코인 매도)
"""
import sys as _sys
import os as _os
_BASE = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
for _d in ["core", "intelligence", "interface", "bots", ""]:
    _p = _os.path.join(_BASE, _d)
    if _p not in _sys.path:
        _sys.path.insert(0, _p)

import os
import time
import json
import sqlite3
import asyncio
import datetime
import urllib.parse
import xml.etree.ElementTree as ET
import re

import requests
import discord
from discord.ext import commands
from anthropic import Anthropic
from dotenv import load_dotenv

# common_utils — atomic 상태파일 쓰기, 시간 헬퍼
from performance import PerformanceAnalyzer, MultiPerformanceAnalyzer
from common_utils import (
    now_kst, now_hms, today_str,
    safe_int, safe_float,
    read_state as _read_state_atomic,
    write_state as _write_state_atomic,
    update_state as _update_state_atomic,
)

# ── 분리 모듈 import ─────────────────────────────────────
from kiki_data import (
    get_recent_performance, get_open_positions_from_db,
    get_coin_performance,
    get_today_realized_all,
)
from kiki_cmd import (
    cmd_status, cmd_score, cmd_sell, cmd_buy, cmd_analyze,
    cmd_pause, cmd_performance, cmd_performance_detail,
    cmd_analyze_today, cmd_analyze_period,
    cmd_watchlist, cmd_watchlist_show,
    cmd_all_status,
    cmd_restart_all,
    cmd_cbot_status, cmd_cbot_sell, cmd_cbot_performance,
    cmd_theme_status, cmd_watchlist_hts, cmd_help,
    cmd_total_performance,
    cmd_news,
    cmd_risk, cmd_risk_pause, cmd_risk_resume,
    cmd_event,
)
# ※ cmd_briefing, cmd_evening_briefing → kiki_briefing에서 import
from kiki_briefing import (
    _get_finnhub_events, _format_finnhub_events, _claude_call,
    _get_global_market, _format_global_market,
    _get_us_events, _get_foreign_flow_summary,
    _build_briefing_msg, _build_evening_briefing_msg,
    cmd_briefing, cmd_evening_briefing,
)
import kiki_briefing as _kb
import kiki_cmd as _kc
from kiki_monitor import (
    init_monitor,
    status_listener,
    proactive_danger_watcher,
    proactive_watch_monitor,
    proactive_insight_provider,
    proactive_daily_review,
)


load_dotenv()


# ============================================================
# 설정
# ============================================================
BOT_TOKEN  = os.getenv("DISCORD_BOT_TOKEN")
CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID", "0"))

# DB 파일
TRADE_HIST_DB = "trade_history.db"
SBOT_HIST_DB  = "sbot_trade_history.db"
CBOT_HIST_DB  = "cbot_trade_history.db"
AI_CACHE_DB   = "ai_cache.db"

# 대화 히스토리
CHAT_HISTORY_FILE = "kiki_history.json"
CHAT_HISTORY_MAX  = 20

# 봇 상태 파일
BOT_STATE_FILES = {
    "nbot": "bot_state.json",
    "sbot": "sbot_state.json",
    "cbot": "cbot_state.json",
}

# AI 모델
DEFAULT_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")


# ============================================================
# 시간 헬퍼 (common_utils 사용)
# ============================================================
def now_kst_dt():
    """이전 코드 호환용 — datetime 객체 반환"""
    return now_kst()


# ============================================================
# 상태 파일 헬퍼 (★ atomic write 적용)
# ============================================================
def read_state(bot: str = "nbot") -> dict:
    """봇 상태 파일 읽기 (없으면 기본값)"""
    fname = BOT_STATE_FILES.get(bot, "bot_state.json")
    return _read_state_atomic(fname, default={
        "paused":      False,
        "score_enter": 55,
        "pending_cmd": None,
        "cmd_result":  None,
        "last_status": None,
    })

def write_state(bot: str = "nbot", state: dict = None):
    """봇 상태 파일 쓰기 (★ atomic — 중간에 죽어도 안 깨짐)"""
    if state is None: state = {}
    fname = BOT_STATE_FILES.get(bot, "bot_state.json")
    _write_state_atomic(fname, state)

def update_state(bot: str = "nbot", **kwargs):
    """봇 상태 부분 업데이트"""
    fname = BOT_STATE_FILES.get(bot, "bot_state.json")
    _update_state_atomic(fname, **kwargs)

def get_active_bots() -> list:
    """현재 실행 중인(상태파일이 있는) 봇 목록"""
    active = []
    for name, fname in BOT_STATE_FILES.items():
        if os.path.exists(fname):
            state = read_state(name)
            last  = state.get("last_update", "")
            active.append((name, last))
    return active


# ============================================================
# DB 조회 헬퍼 (★ WAL 호환 — read-only 모드)
# ============================================================
def _ro_connect(db_file: str) -> sqlite3.Connection:
    """읽기 전용 SQLite 연결 (WAL 모드 봇이 쓰는 동안 안전하게 읽기)"""
    conn = sqlite3.connect(db_file, timeout=10)
    conn.execute("PRAGMA query_only = ON")
    return conn

def get_recent_performance(limit: int = 20, db: str = None) -> list:
    """최근 매매 성과 (단타/스윙)"""
    db = db or TRADE_HIST_DB
    try:
        conn = _ro_connect(db)
        rows = conn.execute("""
            SELECT profit_rate, sell_reason, ai_score, code,
                   buy_price, sell_price, buy_time, sell_time
            FROM trades WHERE sell_price IS NOT NULL
            ORDER BY id DESC LIMIT ?
        """, (limit,)).fetchall()
        conn.close()
        return rows
    except Exception:
        return []

def get_open_positions_from_db(bot: str = "nbot") -> list:
    """DB의 미청산 매수 건"""
    db = TRADE_HIST_DB if bot == "nbot" else SBOT_HIST_DB
    try:
        conn = _ro_connect(db)
        rows = conn.execute("""
            SELECT code, buy_price, qty, ai_score, buy_time
            FROM trades WHERE sell_price IS NULL
            ORDER BY buy_time DESC
        """).fetchall()
        conn.close()
        return rows
    except Exception:
        return []

def get_coin_performance(limit: int = 20) -> list:
    """코인봇 매매 성과"""
    try:
        conn = _ro_connect(CBOT_HIST_DB)
        rows = conn.execute("""
            SELECT profit_rate, sell_reason, ai_score, market,
                   buy_price, sell_price, buy_time, sell_time
            FROM trades WHERE sell_price IS NOT NULL
            ORDER BY id DESC LIMIT ?
        """, (limit,)).fetchall()
        conn.close()
        return rows
    except Exception:
        return []

def get_ebot_performance(limit: int = 20) -> list:
    """★ 신규: 종가봇 매매 성과"""
    try:
        conn = _ro_connect(EBOT_HIST_DB)
        rows = conn.execute("""
            SELECT profit_rate, sell_reason, code, stock_name,
                   buy_price, sell_price, buy_time, sell_time
            FROM trades WHERE sell_price IS NOT NULL
            ORDER BY id DESC LIMIT ?
        """, (limit,)).fetchall()
        conn.close()
        return rows
    except Exception:
        return []

def get_today_realized_all() -> dict:
    """★ 신규: 모든 봇의 오늘 실현손익 합계"""
    today  = today_str()
    result = {"nbot": 0, "sbot": 0, "cbot": 0}
    db_map = {
        "nbot": TRADE_HIST_DB,
        "sbot": SBOT_HIST_DB,
        "cbot": CBOT_HIST_DB,
    }
    for bot_name, db in db_map.items():
        if not os.path.exists(db):
            continue
        try:
            conn = _ro_connect(db)
            if bot_name == "cbot":
                # cbot은 profit_krw 컬럼 사용
                rows = conn.execute("""
                    SELECT profit_krw FROM trades
                    WHERE sell_price IS NOT NULL AND sell_time >= ?
                """, (today,)).fetchall()
                result[bot_name] = sum(int(r[0] or 0) for r in rows)
            else:
                rows = conn.execute("""
                    SELECT buy_price, sell_price, qty FROM trades
                    WHERE sell_price IS NOT NULL AND sell_time >= ?
                """, (today,)).fetchall()
                result[bot_name] = sum(
                    int((sp - bp) * qty) for bp, sp, qty in rows
                    if sp is not None and bp is not None
                )
            conn.close()
        except Exception:
            pass
    # ★ 미실현 손익 추가 (보유 중인 포지션)
    for bot_name in ["nbot", "sbot"]:
        try:
            st = read_state(bot_name)
            unrealized = st.get("last_status", {}).get("total_profit", 0)
            if unrealized:
                result[bot_name] = result.get(bot_name, 0) + int(unrealized)
        except Exception:
            pass
    return result


# ============================================================
# AI 비서 클래스 (키키 캐릭터 — 검증된 구조 그대로)
# ============================================================
class AIAssistant:
    """키키 — 꼬리 두 달린 여우정령. 장난스런 여동생 스타일 만능 AI 비서."""

    LOCATIONS = {
        "도포면": (57, 74), "영암": (58, 74), "목포": (50, 67),
        "나주":   (56, 77), "해남": (54, 65), "광주": (60, 74),
        "강진":   (56, 68), "무안": (52, 71), "운남면": (52, 70),
    }
    PTY_CODE = {"0": "없음", "1": "비", "2": "비/눈", "3": "눈", "4": "소나기"}

    SEARCH_SHORTCUTS = {
        "미국장":   "US stock market nasdaq dow jones today closing",
        "미국 장":  "US stock market nasdaq dow jones today closing",
        "나스닥":   "nasdaq composite index today",
        "트럼프":   "trump news today",
        "중동":     "middle east news today",
        "환율":     "USD KRW exchange rate today",
        "코스피":   "코스피 오늘 시황",
        "코스닥":   "코스닥 오늘 시황",
        "유가":     "crude oil WTI price today",
        "금값":     "gold price today",
        "반도체":   "semiconductor industry news today",
        "기아":     "기아 타이거즈 오늘 경기 결과 스코어",
        "기아야구": "기아 타이거즈 오늘 경기 결과 스코어",
        "타이거즈": "기아 타이거즈 오늘 경기 결과 스코어",
        "야구":     "KBO 오늘 야구 경기 결과 스코어",
        "KBO":      "KBO 오늘 야구 경기 결과 스코어",
        "롯데":     "롯데 자이언츠 오늘 경기 결과",
        "삼성":     "삼성 라이온즈 오늘 경기 결과",
        "한화":     "한화 이글스 오늘 경기 결과",
        "두산":     "두산 베어스 오늘 경기 결과",
        "LG":       "LG 트윈스 오늘 경기 결과",
        "축구":     "K리그 오늘 축구 경기 결과",
        "손흥민":   "손흥민 경기 결과 오늘",
        "선물":     "코스피 선물 오늘",
        "비트코인": "bitcoin BTC price today",
        "비트":     "bitcoin BTC price today",
        "이더리움": "ethereum ETH price today",
        "코인":     "cryptocurrency bitcoin ethereum price today",
    }

    def __init__(self):
        self.llm     = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        self.model   = DEFAULT_MODEL
        self.history = self._load_history()
        if self.history:
            print(f"♻️ 대화 히스토리 복원: {len(self.history)}개")

    def _load_history(self) -> list:
        try:
            if os.path.exists(CHAT_HISTORY_FILE):
                with open(CHAT_HISTORY_FILE, "r", encoding="utf-8") as f:
                    data  = json.load(f)
                    today = today_str()
                    if data.get("date") == today:
                        return data.get("history", [])
        except Exception as e:
            print(f"⚠️ 히스토리 로드 오류: {e}")
        return []

    def _save_history(self):
        try:
            today = today_str()
            with open(CHAT_HISTORY_FILE, "w", encoding="utf-8") as f:
                json.dump({
                    "date": today,
                    "history": self.history[-CHAT_HISTORY_MAX:],
                }, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"⚠️ 히스토리 저장 오류: {e}")

    # ── 날씨 ─────────────────────────────────────────────────
    def _get_weather_kma(self, nx: int, ny: int) -> str:
        try:
            auth_key = os.getenv("KMA_API_KEY", "")
            if not auth_key:
                return "기상청 API 키 없음"
            target = datetime.datetime.now() - datetime.timedelta(minutes=45)
            url    = ("https://apihub.kma.go.kr/api/typ02/openApi/"
                      "VilageFcstInfoService_2.0/getUltraSrtNcst")
            params = {
                "pageNo": "1", "numOfRows": "1000", "dataType": "JSON",
                "base_date": target.strftime("%Y%m%d"),
                "base_time": target.strftime("%H00"),
                "nx": nx, "ny": ny, "authKey": auth_key,
            }
            items = (
                requests.get(url, params=params, timeout=5)
                .json().get("response", {}).get("body", {})
                .get("items", {}).get("item", [])
            )
            data    = {item["category"]: item["obsrValue"] for item in items}
            pty     = self.PTY_CODE.get(data.get("PTY", "0"), "없음")
            weather = "비" if pty != "없음" else "맑음"
            return (
                f"{weather} / {data.get('T1H', '?')}°C"
                f" / 습도:{data.get('REH', '?')}%"
                f" / 풍속:{data.get('WSD', '?')}m/s"
            )
        except Exception as e:
            return f"날씨 오류: {e}"

    def _get_weather(self, location: str = "도포면") -> str:
        coords = self.LOCATIONS.get(location)
        if not coords:
            return f"{location} 좌표 없음"
        return self._get_weather_kma(*coords)

    def _get_weather_region(self) -> str:
        # ★ 도포면만 표시
        return f"  도포면: {self._get_weather('도포면')}"

    # ── 검색 (Tavily 우선 → 네이버 → 구글 뉴스 RSS 폴백) ────
    def _web_search_global(self, query: str) -> str:
        # 1순위: Tavily (유료, 실시간)
        tavily_key = os.getenv("TAVILY_API_KEY", "")
        if tavily_key:
            try:
                # ★ 금융/시세 쿼리는 advanced (실시간), 일반은 basic (캐시)
                fin_keywords = ["stock", "nasdaq", "dow", "exchange rate",
                                "bitcoin", "ethereum", "crypto", "price", "USD"]
                depth = "advanced" if any(kw in query.lower() for kw in fin_keywords) else "basic"

                res = requests.post(
                    "https://api.tavily.com/search",
                    json={
                        "api_key": tavily_key, "query": query,
                        "search_depth": depth, "include_answer": True,
                        "include_raw_content": False, "max_results": 5,
                    }, timeout=10,
                ).json()
                lines   = []
                answer  = res.get("answer", "").strip()
                results = res.get("results", [])
                if answer:
                    lines.append(f"[요약] {answer}")
                for r in results[:4]:
                    title   = r.get("title", "").strip()
                    content = r.get("content", "").strip()[:120]
                    date    = r.get("published_date", "")[:10]
                    if title:
                        lines.append(f"- {title} ({content}) [{date}]")
                if lines:
                    return "\n".join(lines)
            except Exception as e:
                print(f"Tavily 오류: {e}")

        # 2순위: ★ 네이버 뉴스 (Tavily 실패 시 — 미국 증시도 잘 잡힘)
        client_id     = os.getenv("NAVER_CLIENT_ID", "")
        client_secret = os.getenv("NAVER_CLIENT_SECRET", "")
        if client_id and client_secret:
            try:
                encoded  = urllib.parse.quote(query)
                headers  = {
                    "X-Naver-Client-Id":     client_id,
                    "X-Naver-Client-Secret": client_secret,
                }
                news_url = (f"https://openapi.naver.com/v1/search/news.json"
                           f"?query={encoded}&display=5&sort=date")
                news_res = requests.get(news_url, headers=headers, timeout=5).json()
                items    = news_res.get("items", [])
                if items:
                    results = []
                    for item in items:
                        title = re.sub(r"<[^>]+>", "", item.get("title", "")).strip()
                        desc  = re.sub(r"<[^>]+>", "", item.get("description", "")).strip()[:100]
                        if title:
                            results.append(f"- {title} ({desc})")
                    if results:
                        print(f"🇰🇷 네이버 폴백 검색 성공: {query}")
                        return "\n".join(results)
            except Exception as e:
                print(f"네이버 글로벌 폴백 오류: {e}")

        # 3순위: 구글 뉴스 RSS
        try:
            encoded = urllib.parse.quote(query)
            url     = f"https://news.google.com/rss/search?q={encoded}&hl=en&gl=US&ceid=US:en"
            res     = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=5)
            root    = ET.fromstring(res.content)
            results = []
            for item in root.findall(".//item")[:5]:
                title = item.findtext("title", "").strip()
                desc  = re.sub(r"<[^>]+>", "", item.findtext("description", "")).strip()[:80]
                if title:
                    results.append(f"- {title} ({desc})")
            return "\n".join(results) if results else "검색 결과 없음"
        except Exception as e:
            return f"검색 실패: {e}"

    def _web_search_korea(self, query: str) -> str:
        # Tavily 먼저 시도
        tavily_key = os.getenv("TAVILY_API_KEY", "")
        if tavily_key:
            try:
                res = requests.post(
                    "https://api.tavily.com/search",
                    json={
                        "api_key":             tavily_key,
                        "query":               query,
                        "search_depth":        "basic",
                        "include_answer":      True,
                        "include_raw_content": False,
                        "max_results":         5,
                    }, timeout=8,
                ).json()
                lines   = []
                answer  = res.get("answer", "").strip()
                results = res.get("results", [])
                if answer:
                    lines.append(f"[요약] {answer}")
                for r in results[:4]:
                    title   = r.get("title", "").strip()
                    content = r.get("content", "").strip()[:120]
                    date    = r.get("published_date", "")[:10]
                    if title:
                        lines.append(f"- {title} ({content}) [{date}]")
                if lines:
                    return "\n".join(lines)
            except Exception as e:
                print(f"Tavily 국내 검색 오류: {e}")

        # 폴백: 네이버 API
        client_id     = os.getenv("NAVER_CLIENT_ID", "")
        client_secret = os.getenv("NAVER_CLIENT_SECRET", "")
        if client_id and client_secret:
            try:
                encoded = urllib.parse.quote(query)
                headers = {
                    "X-Naver-Client-Id":     client_id,
                    "X-Naver-Client-Secret": client_secret,
                }

                # 맛집/장소 키워드면 지역 검색
                local_keywords = [
                    "맛집", "음식점", "식당", "카페", "병원", "약국",
                    "마트", "쇼핑", "숙박", "호텔", "펜션",
                ]
                use_local = any(kw in query for kw in local_keywords)

                if use_local:
                    local_url = (f"https://openapi.naver.com/v1/search/local.json"
                                f"?query={encoded}&display=5&sort=comment")
                    local_res = requests.get(
                        local_url, headers=headers, timeout=5,
                    ).json()
                    items = local_res.get("items", [])
                    if items:
                        results = []
                        for item in items:
                            title = re.sub(r"<[^>]+>", "", item.get("title", "")).strip()
                            addr  = item.get("address", "").strip()
                            cat   = item.get("category", "").strip()
                            if title:
                                results.append(f"- {title} | {cat} | {addr}")
                        return "\n".join(results) if results else "결과 없음"

                # 일반 뉴스 검색
                news_url = (f"https://openapi.naver.com/v1/search/news.json"
                           f"?query={encoded}&display=5&sort=date")
                news_res = requests.get(news_url, headers=headers, timeout=5).json()
                items    = news_res.get("items", [])
                if items:
                    results = []
                    for item in items:
                        title = re.sub(r"<[^>]+>", "", item.get("title", "")).strip()
                        desc  = re.sub(r"<[^>]+>", "", item.get("description", "")).strip()[:100]
                        if title:
                            results.append(f"- {title} ({desc})")
                    return "\n".join(results)
            except Exception as e:
                print(f"네이버 API 오류: {e}")

        return "검색 결과 없음"

    # ── ★ v3 자연어 해석 — 풍부한 컨텍스트 + NLU ──────────────
    def interpret(self, user_msg: str, current_state: dict) -> str:
        now = now_kst().strftime("%Y-%m-%d %H:%M")

        # ── 날씨 키워드 감지 ─────────────────────────────────
        weather_keywords = ["날씨", "기온", "온도", "비", "눈", "맑", "흐림"]
        if any(kw in user_msg for kw in weather_keywords):
            detected_loc = next(
                (loc for loc in self.LOCATIONS if loc in user_msg), None,
            )
            weather_hint = "\n[날씨 정보]\n" + (
                f"  {detected_loc}: {self._get_weather(detected_loc)}"
                if detected_loc else self._get_weather_region()
            )
        else:
            weather_hint = ""

        search_hint = weather_hint
        for k, v in self.SEARCH_SHORTCUTS.items():
            if k in user_msg:
                has_korean = any('\uac00' <= c <= '\ud7a3' for c in v)
                tool_name  = "search_korea" if has_korean else "search_global"
                search_hint += f"\n[검색 힌트] '{k}' → {tool_name}('{v}') 로 검색하세요"
                break

        # ── ★ 풍부한 봇 컨텍스트 수집 ───────────────────────
        active_sectors = current_state.get("active_sectors", [])
        sector_info    = f", '.join(active_sectors)" if active_sectors else "없음"

        # 단타봇 보유종목
        nbot_positions = current_state.get("positions", {})
        nbot_pos_str   = ""
        if nbot_positions:
            pos_lines = []
            for code, pos in list(nbot_positions.items())[:5]:
                rate = pos.get("rate", 0)
                e    = "📈" if rate >= 0 else "📉"
                pos_lines.append(f"  {e} {code}: {rate:+.1f}%")
            nbot_pos_str = "\n보유종목:\n" + "\n".join(pos_lines)

        # 코인봇
        cbot_state  = read_state("cbot")
        cbot_status = cbot_state.get("last_status", {})
        cbot_positions = cbot_state.get("positions", {})
        cbot_pos_str = ""
        if cbot_positions:
            clines = []
            for mkt, pos in list(cbot_positions.items())[:3]:
                rate = pos.get("rate", 0)
                e    = "📈" if rate >= 0 else "📉"
                clines.append(f"  {e} {mkt.replace('KRW-','')}:{rate:+.1f}%")
            cbot_pos_str = " | " + " ".join(clines)

        # 스윙봇
        sbot_state  = read_state("sbot")
        sbot_status = sbot_state.get("last_status", {})

        # 종가봇
        ebot_state  = {}

        # 오늘 실현손익
        today_pnl = get_today_realized_all()
        total_pnl = sum(today_pnl.values())
        pnl_str   = f"{total_pnl:+,}원" if total_pnl != 0 else "0원"

        # ★ f-string 밖에서 미리 계산 (unhashable 오류 방지)
        nbot_status   = current_state.get("last_status", {})
        nbot_paused   = "🔴일시중단" if current_state.get("paused") else "🟢실행중"
        nbot_score    = current_state.get("score_enter", 55)
        nbot_pos_cnt  = nbot_status.get("positions", 0)
        sbot_paused   = "🔴일시중단" if sbot_state.get("paused") else "🟢실행중"
        sbot_pos_cnt  = sbot_status.get("positions", 0)
        cbot_paused   = "🔴일시중단" if cbot_state.get("paused") else "🟢실행중"
        cbot_krw      = cbot_status.get("krw", 0)

        system = f"""너의 이름은 키키야. 꼬리 두 달린 여우정령, 장난스런 여동생 스타일의 만능 AI 비서야.
지금: {now}

━━━ 📊 봇 현황 ━━━
📈 단타봇: {nbot_paused} | 점수기준:{nbot_score}점 | 포지션:{nbot_pos_cnt}개{nbot_pos_str}
📊 스윙봇: {sbot_paused} | 포지션:{sbot_pos_cnt}개
🪙 코인봇: {cbot_paused} | KRW:{cbot_krw:,}원{cbot_pos_str}
💰 오늘 실현손익: {pnl_str}
🏭 활성업종: {sector_info}
{search_hint}

━━━ 🎮 봇 제어 CMD 형식 ━━━
사용자가 아래 의도를 말하면 반드시 CMD: 형식으로 답해:

[상태 확인]
"어때/상태/현황/지금" → CMD:!전체상태
"단타 어때" → CMD:!상태
"스윙 어때" → CMD:!s상태
"코인 어때/코봇" → CMD:!c상태

[성과/수익]
"얼마 벌었/손익/성과/수익/오늘" → CMD:!성과
"성과 자세히/상세/분석" → CMD:!성과상세
"최근 N일 성과/수익" → CMD:!성과상세 N  (숫자 그대로)
예: "최근 5일 성과" → CMD:!성과상세 5
예: "이번달 성과" → CMD:!성과상세 30
예: "일주일 성과/이번주" → CMD:!성과상세 7
"오늘 왜 손해/분석" → CMD:!분석오늘
"이번주 패턴/분석" → CMD:!분석이번주

[매도]
"XXX 팔아/매도/청산" (단타종목) → CMD:!매도 종목코드
"XXX 팔아/매도/청산" (스윙종목) → CMD:!s매도 종목코드
"BTC/비트/이더/코인 팔아" → CMD:!c매도 코인명
예: "MINA 팔아줘" → CMD:!c매도 KRW-MINA
예: "삼성전자 팔아" → CMD:!매도 005930

[시작/정지]
"단타 멈춰/정지/세워" → CMD:!정지
"단타 다시/시작/켜줘" → CMD:!시작
"코인봇 멈춰" → CMD:!c정지
"코인봇 시작" → CMD:!c시작
"스윙 멈춰" → CMD:!s정지

[설정]
"점수 X로/기준 X점" → CMD:!점수기준 X

[분석]
"왜 손해/오늘 분석/패턴" → CMD:!성과상세
"업종/테마 뭐가 강해" → CMD:!테마
"모닝브리핑/시황/브리핑" → CMD:!브리핑
"저녁브리핑/저녁시황/저녁 브리핑" → CMD:!저녁브리핑

━━━ 🔍 검색 규칙 ━━━
- 최신 정보는 무조건 검색 툴 사용
- search_global: 미국증시/나스닥/환율/코인/해외
- search_korea: 코스피/코스닥/국내종목/날씨/스포츠/맛집/생활
- 금융 수치는 검색 결과에서만 인용 (날조 절대 금지)
- 검색 실패 시 "조회 실패" 솔직히 말하기

━━━ 💬 응답 규칙 ━━━
- CMD: 로 시작하면 봇이 자동 실행함
- CMD: 반환 시 첫 줄에 CMD만, 설명 텍스트 절대 추가 금지
- 예: CMD:!성과상세 5  (이게 전부, 뒤에 아무것도 붙이지 마)
- 일반 대화는 3줄 이내, 친근하게
- 보유종목/포지션 관련은 위 현황 데이터 활용
- 뭐든 물어봐도 OK, 일상 대화도 환영"""

        tools = [
            {
                "name": "search_global",
                "description": "국제 뉴스 및 코인 시세 검색. 미국증시, 나스닥, 환율, 비트코인 등.",
                "input_schema": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            },
            {
                "name": "search_korea",
                "description": "국내 모든 정보 검색. 코스피/코스닥/종목, 날씨, 스포츠, 맛집, 음식점, 여행, 생활정보, 교통, 병원 등 일상 모든 것.",
                "input_schema": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            },
        ]

        self.history.append({"role": "user", "content": user_msg})
        messages = self.history[-10:]
        try:
            res = _claude_call(self.llm, 
                model=self.model, max_tokens=1024,
                system=system, tools=tools, messages=messages,
            )
        except Exception as e:
            return f"AI 응답 오류: {e}"

        # 도구 호출 루프
        for i in range(3):
            if res.stop_reason != "tool_use":
                break
            tool_results = []
            for block in res.content:
                if block.type == "tool_use" and block.name in ("search_global", "search_korea"):
                    query = block.input.get("query", user_msg)
                    if block.name == "search_global":
                        print(f"🌍 글로벌 검색: {query}")
                        result = self._web_search_global(query)
                    else:
                        print(f"🇰🇷 국내 검색: {query}")
                        result = self._web_search_korea(query)
                    tool_results.append({
                        "type":        "tool_result",
                        "tool_use_id": block.id,
                        "content":     result,
                    })
            messages = messages + [
                {"role": "assistant", "content": res.content},
                {"role": "user",      "content": tool_results},
            ]
            try:
                res = _claude_call(self.llm, 
                    model=self.model, max_tokens=1024,
                    system=system, tools=tools, messages=messages,
                )
            except Exception as e:
                return f"AI 응답 오류(도구 후): {e}"

        reply = "".join(b.text for b in res.content if hasattr(b, "text")).strip()
        self.history.append({"role": "assistant", "content": reply})
        self._save_history()
        return reply or "응답을 생성하지 못했어요."


# ============================================================
# 디스코드 봇 인스턴스
# ============================================================
intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)
ai  = AIAssistant()

# ★ 확인 대기 중인 명령 {user_id: (cmd, ts)}
_pending_confirm: dict = {}


# ============================================================
# 유틸리티
# ============================================================
async def send_long(ctx, text: str, max_len: int = 1900):
    """긴 메시지를 여러 개로 쪼개 전송"""
    for i in range(0, len(text), max_len):
        await ctx.send(text[i:i + max_len])


async def wait_cmd_result(bot_name: str, max_attempts: int = 12,
                          interval: float = 5.0) -> str:
    """
    pending_cmd 처리 결과 폴링.
    ★ 개선: ctx.send를 직접 안 하고 결과만 반환 (호출자가 처리)
    """
    for _ in range(max_attempts):
        await asyncio.sleep(interval)
        state  = read_state(bot_name)
        result = state.get("cmd_result")
        if result:
            update_state(bot_name, cmd_result=None)
            return result
    return ""


# ============================================================
# 명령어 라우터
# ============================================================
async def execute_command(ctx, cmd: str):
    cmd = cmd.strip()

    # ── 단타봇 ───────────────────────────────────────────────
    if cmd == "!상태":
        await cmd_status(ctx, "nbot")
    elif cmd.startswith("!점수기준"):
        parts = cmd.split()
        if len(parts) == 2 and parts[1].isdigit():
            await cmd_score(ctx, int(parts[1]))
        else:
            await ctx.send("❌ 사용법: !점수기준 70")
    elif cmd.startswith("!매도"):
        parts = cmd.split()
        if len(parts) == 2:
            await cmd_sell(ctx, parts[1], "nbot")
        else:
            await ctx.send("❌ 사용법: !매도 005930")
    elif cmd.startswith("!매수"):
        parts = cmd.split()
        if len(parts) == 3 and parts[2].isdigit():
            await cmd_buy(ctx, parts[1], int(parts[2]))
        else:
            await ctx.send("❌ 사용법: !매수 005930 10")
    elif cmd.startswith("!분석"):
        parts = cmd.split()
        if len(parts) == 2:
            await cmd_analyze(ctx, parts[1])
        else:
            await ctx.send("❌ 사용법: !분석 005930")
    elif cmd == "!정지":
        await cmd_pause(ctx, True, "nbot")
    elif cmd == "!시작":
        await cmd_pause(ctx, False, "nbot")

    # ── 스윙봇 ───────────────────────────────────────────────
    elif cmd in ("!s상태", "!상태 sbot"):
        await cmd_status(ctx, "sbot")
    elif cmd.startswith("!s매도"):
        parts = cmd.split()
        if len(parts) == 2:
            await cmd_sell(ctx, parts[1], "sbot")
        else:
            await ctx.send("❌ 사용법: !s매도 005930")
    elif cmd == "!s정지":
        await cmd_pause(ctx, True, "sbot")
    elif cmd == "!s시작":
        await cmd_pause(ctx, False, "sbot")
    elif cmd.startswith("!s관심"):
        parts = cmd.split()
        if len(parts) == 2:
            await cmd_watchlist(ctx, parts[1], "sbot")
        elif len(parts) == 1:
            await cmd_watchlist_show(ctx, "sbot")
        else:
            await ctx.send("❌ 사용법: !s관심 005930")

    # ── 종가봇 (★ 신규) ─────────────────────────────────────

    # ── 코인봇 ───────────────────────────────────────────────
    elif cmd == "!c상태":
        await cmd_cbot_status(ctx)
    elif cmd.startswith("!c매도"):
        parts = cmd.split()
        if len(parts) == 2:
            await cmd_cbot_sell(ctx, parts[1])
        else:
            await ctx.send("❌ 사용법: !c매도 BTC")
    elif cmd == "!c정지":
        await cmd_pause(ctx, True, "cbot")
    elif cmd == "!c시작":
        await cmd_pause(ctx, False, "cbot")
    elif cmd == "!c성과":
        await cmd_cbot_performance(ctx)

    # ── 업종/테마 ───────────────────────────────────────────
    elif cmd == "!이벤트":
        await cmd_event(ctx)
    elif cmd == "!리스크":
        await cmd_risk(ctx)
    elif cmd == "!리스크중단":
        await cmd_risk_pause(ctx)
    elif cmd == "!리스크재개":
        await cmd_risk_resume(ctx)
    elif cmd == "!뉴스":
        await cmd_news(ctx)
    elif cmd == "!테마":
        await cmd_theme_status(ctx)
    elif cmd in ("!관심HTS", "!hts관심"):
        await cmd_watchlist_hts(ctx)

    # ── 공통 ─────────────────────────────────────────────────
    elif cmd == "!전체상태":
        await cmd_all_status(ctx)
    elif cmd in ("!전체재시작", "!재시작전체", "!all재시작"):
        await cmd_restart_all(ctx)
    elif cmd == "!브리핑":
        await cmd_briefing(ctx)
    elif cmd == "!저녁브리핑":
        await cmd_evening_briefing(ctx)
    elif cmd == "!성과":
        await cmd_performance(ctx)
    elif cmd.startswith("!전체성과"):
        parts = cmd.split()
        days = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 30
        await cmd_total_performance(ctx, days)
    elif cmd == "!성과상세":
        await cmd_performance_detail(ctx)
    elif cmd.startswith("!성과상세"):
        parts = cmd.split()
        days  = int(parts[1]) if len(parts) == 2 and parts[1].isdigit() else 30
        await cmd_performance_detail(ctx, days=days)
    elif cmd.startswith("!관심"):
        parts = cmd.split()
        if len(parts) == 2:
            await cmd_watchlist(ctx, parts[1], "nbot")
        elif len(parts) == 1:
            await cmd_watchlist_show(ctx, "nbot")
        else:
            await ctx.send("❌ 사용법: !관심 005930")
    elif cmd == "!도움말":
        await cmd_help(ctx)
    elif cmd == "!성과상세" or cmd.startswith("!성과상세 "):
        parts = cmd.split()
        days  = int(parts[1]) if len(parts) == 2 and parts[1].isdigit() else 30
        await cmd_performance_detail(ctx, days=days)
    elif cmd == "!분석오늘":
        await cmd_analyze_today(ctx)
    elif cmd == "!분석이번주":
        await cmd_analyze_period(ctx, days=7)
    elif cmd.startswith("!분석 ") and len(cmd.split()) == 2:
        # !분석 005930 (종목 분석)
        code = cmd.split()[1]
        if code.isdigit():
            await cmd_analyze(ctx, code)
        else:
            await cmd_analyze_period(ctx, days=7)
    else:
        # ★ 알 수 없는 명령어도 AI에게 자연어로 처리
        await ctx.send(f"🦊 키키: `{cmd}` 명령어를 모르겠어요. 자연어로 말해주세요!")


# ============================================================
# 핸들러 — 단타/스윙봇
# ============================================================

# ── 명령 처리 함수 → kiki_cmd.py로 분리 ──────────────────
# cmd_status, cmd_score, cmd_sell, cmd_buy, cmd_analyze 등
# kiki_cmd.py 참조


@bot.event
async def on_ready():
    print(f"✅ AI 비서 봇 온라인: {bot.user}")
    # ★ kiki_briefing에 전역 변수 주입
    import kiki_briefing as _kb
    _kb.ai           = ai
    _kb.bot          = bot
    _kb.send_long    = send_long
    _kb.BOT_STATE_FILES = BOT_STATE_FILES
    _kb.CHANNEL_ID   = CHANNEL_ID
    _kb.read_state   = read_state
    _kb.write_state  = write_state
    _kb.update_state = update_state
    # ★ kiki_cmd에 전역 변수 주입
    import kiki_cmd as _kc
    _kc.ai             = ai
    _kc.bot            = bot
    _kc.BOT_STATE_FILES = BOT_STATE_FILES
    _kc.CHANNEL_ID     = CHANNEL_ID
    _kc.send_long      = send_long
    _kc.wait_cmd_result = wait_cmd_result
    _kc.execute_command = execute_command
    asyncio.ensure_future(status_listener())
    asyncio.ensure_future(auto_briefing())
    asyncio.ensure_future(hts_sync_task())
    # ★ Lv1 능동 알림자 — kiki_monitor
    init_monitor(
        bot=bot, ai=ai, channel_id=CHANNEL_ID,
        read_state_fn=read_state,
        bot_state_files=BOT_STATE_FILES,
        get_today_realized_fn=get_today_realized_all,
        get_recent_perf_fn=get_recent_performance,
        ro_connect_fn=_ro_connect,
        send_long_fn=send_long,
        default_model=DEFAULT_MODEL,
    )
    asyncio.ensure_future(proactive_danger_watcher())
    asyncio.ensure_future(proactive_watch_monitor())
    asyncio.ensure_future(proactive_insight_provider())
    asyncio.ensure_future(proactive_daily_review())

    ch = bot.get_channel(CHANNEL_ID)
    if ch:
        active = get_active_bots()
        active_names = [a[0] for a in active]
        bot_status_line = []
        if "nbot" in active_names: bot_status_line.append("📈 단타")
        if "sbot" in active_names: bot_status_line.append("📊 스윙")
        if "cbot" in active_names: bot_status_line.append("🪙 코인")
        bots_str = " | ".join(bot_status_line) if bot_status_line else "감지된 봇 없음"

        await ch.send(
            f"🤖 **영암9 AI 비서 (키키) 온라인**\n"
            f"⏰ {now_kst().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"{bots_str}\n"
            f"🦊 능동 알림: 🚨위험/⚠️주의/💡인사이트/📊일일리뷰\n"
            f"🏭 HTS 관심그룹 자동 동기화 (09:00/11:00/14:00)\n"
            "`!도움말` 로 명령어 확인"
        )


@bot.event
async def on_message(message):
    if message.author == bot.user:
        return
    if CHANNEL_ID and message.channel.id != CHANNEL_ID:
        return

    msg_content = message.content.strip()
    ctx         = await bot.get_context(message)
    user_id     = message.author.id

    # ── 확인 대기 응답 처리 (네/아니오) ────────────────────────
    if user_id in _pending_confirm:
        cmd, ts = _pending_confirm[user_id]
        if time.time() - ts < 30:  # 30초 내 응답
            if msg_content in ("네", "응", "ㅇ", "yes", "y", "ㅇㅇ", "고고", "해줘"):
                del _pending_confirm[user_id]
                await message.channel.send(f"✅ 실행할게요!")
                await execute_command(ctx, cmd)
                return
            elif msg_content in ("아니", "ㄴ", "취소", "no", "n", "ㄴㄴ", "됐어"):
                del _pending_confirm[user_id]
                await message.channel.send("✅ 취소했어요!")
                return
        else:
            del _pending_confirm[user_id]

    if msg_content.startswith("!"):
        # ── 명령어 직접 처리 ────────────────────────────────────
        try:
            await execute_command(ctx, msg_content)
        except Exception as e:
            await ctx.send(f"❌ 명령 실행 오류: {e}")
            print(f"⚠️ 명령 오류 [{msg_content}]: {e}")

    elif msg_content:
        # ── ★ 자연어 처리 — AI 직통
        async with message.channel.typing():
            state = read_state("nbot")
            loop  = asyncio.get_event_loop()
            try:
                reply = await asyncio.wait_for(
                    loop.run_in_executor(None, ai.interpret, msg_content, state),
                    timeout=30.0
                )
                if reply.startswith("CMD:"):
                    cmd = reply.split("\n")[0][4:].strip()
                    danger_cmds = ["!매도","!s매도","!c매도","!정지","!s정지","!c정지","!e정지"]
                    is_danger   = any(cmd.startswith(d) for d in danger_cmds)
                    if is_danger:
                        _pending_confirm[user_id] = (cmd, time.time())
                        await message.channel.send(f"🦊 키키: `{cmd}` 실행할까요? (네/아니오)")
                    else:
                        await execute_command(ctx, cmd)
                else:
                    await message.channel.send(f"🤖 {reply}")
            except asyncio.TimeoutError:
                await message.channel.send("🦊 키키: 잠깐 생각 중이에요. 다시 말해줘요!")
# ============================================================
# 백그라운드 태스크
# ============================================================
async def hts_sync_task():
    """평일 09:00 / 11:00 / 14:00 HTS 관심그룹 자동 동기화"""
    last_sync = {}
    SYNC_TIMES = ["0900", "1100", "1400"]

    while True:
        await asyncio.sleep(30)
        try:
            now      = now_kst()
            today    = now.strftime("%Y-%m-%d")
            now_hhmm_str = now.strftime("%H%M")

            if now.weekday() >= 5:
                continue

            ch = bot.get_channel(CHANNEL_ID)
            if not ch:
                continue

            for sync_t in SYNC_TIMES:
                key   = f"{today}_{sync_t}"
                end_t = str(int(sync_t) + 5).zfill(4)
                if key in last_sync or not (sync_t <= now_hhmm_str <= end_t):
                    continue

                last_sync[key] = True
                print(f"🔄 HTS 관심그룹 자동 동기화 ({sync_t})")

                codes   = await _fetch_kiwoom_watchlist_ws()
                summary = _sync_watchlist_to_state(codes)

                total   = summary.get("total", 0)
                added   = summary.get("added", 0)
                removed = summary.get("removed", 0)

                if total == 0:
                    await ch.send("⚠️ HTS 관심그룹 조회 실패 — API 확인 필요")
                elif added > 0 or removed > 0:
                    now_str = now.strftime("%H:%M")
                    msg = (
                        f"📋 **HTS 관심그룹 동기화** [{now_str}]\n"
                        f"총 {total}개 | ✅ 추가:{added} | ❌ 제거:{removed}"
                    )
                    await ch.send(msg)
                else:
                    print(f"📋 HTS 관심그룹 변화 없음 ({total}개 유지)")

            # 오래된 동기화 키 정리
            for k in [k for k in last_sync if not k.startswith(today)]:
                del last_sync[k]

        except Exception as e:
            print(f"⚠️ HTS 동기화 오류: {e}")


async def auto_briefing():
    """평일 08:00 모닝 / 20:00 저녁 브리핑"""
    last_morning_date = None
    last_evening_date = None

    # ★ 정확한 시간 대기 방식 — tasks.loop 스타일 (시간 윈도우 놓침 방지)
    def _next_target(hour: int, minute: int = 0) -> float:
        """다음 HH:MM KST까지 남은 초 계산"""
        now_dt  = now_kst()
        target  = now_dt.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if now_dt >= target:
            target += datetime.timedelta(days=1)
        return (target - now_dt).total_seconds()

    # ★ 시작 시 오늘 08:00~09:00 사이면 모닝 브리핑 즉시 전송 (재시작으로 놓친 경우 대비)
    _now = now_kst()
    if _now.weekday() < 5 and "0800" <= _now.strftime("%H%M") <= "0900":
        try:
            _ch = bot.get_channel(CHANNEL_ID)
            if _ch:
                _msg = await asyncio.get_event_loop().run_in_executor(None, _build_briefing_msg)
                await send_long(_ch, _msg)
                last_morning_date = _now.strftime("%Y-%m-%d")
                print("✅ 재시작 후 모닝 브리핑 즉시 전송")
        except Exception as _e:
            print(f"⚠️ 재시작 브리핑 오류: {_e}")

    # ★ 관심종목 만료 체크 전용 루프 (30초마다, 브리핑과 분리)
    async def _watchlist_expire_loop():
        while True:
            await asyncio.sleep(30)
            try:
                now   = now_kst()
                today = now.strftime("%Y-%m-%d")
                if now.weekday() >= 5:
                    continue
                ch = bot.get_channel(CHANNEL_ID)
                if not ch:
                    continue
                for bot_name in ("nbot", "sbot"):
                    state     = read_state(bot_name)
                    watchlist = state.get("watchlist", [])
                    wl_expire = state.get("watchlist_expire", {})
                    wl_source = state.get("watchlist_source", {})
                    expired   = [
                        c for c in watchlist
                        if wl_source.get(c, "manual") == "manual"
                        and wl_expire.get(c, "9999-12-31") < today
                    ]
                    if expired:
                        for c in expired:
                            watchlist.remove(c)
                            wl_expire.pop(c, None)
                        update_state(bot_name,
                                    watchlist=watchlist,
                                    watchlist_expire=wl_expire)
                        await ch.send(f"🗑️ 관심종목 만료 제거: {', '.join(expired)}")
            except Exception as e:
                print(f"⚠️ 관심종목 만료 체크 오류: {e}")

    asyncio.ensure_future(_watchlist_expire_loop())

    # ── ★ 정확한 시간 트리거 루프 ────────────────────────
    while True:
        try:
            now   = now_kst()
            today = now.strftime("%Y-%m-%d")

            # 평일만
            if now.weekday() >= 5:
                # 다음 월요일 08:00까지 대기
                days_until_mon = (7 - now.weekday()) % 7 or 7
                target = (now + datetime.timedelta(days=days_until_mon)).replace(
                    hour=8, minute=0, second=0, microsecond=0)
                wait = (target - now).total_seconds()
                print(f"🗓️ 주말 — 다음 월요일 08:00까지 {wait/3600:.1f}h 대기")
                await asyncio.sleep(max(wait, 60))
                continue

            ch = bot.get_channel(CHANNEL_ID)

            # ── 08:00 모닝 브리핑 ─────────────────────────
            if last_morning_date != today:
                wait = _next_target(8, 0)
                if wait > 3600:   # 1시간 이상 남음 → 대기
                    await asyncio.sleep(min(wait - 60, 1800))
                    continue
                # 08:00 도달 — 정확히 대기
                await asyncio.sleep(max(wait, 0))
                now = now_kst()
                if now.weekday() < 5 and ch:
                    last_morning_date = today
                    await ch.send("🌅 **모닝 브리핑 준비 중...**")
                    msg = await asyncio.get_event_loop().run_in_executor(
                        None, _build_briefing_msg)
                    await send_long(ch, msg)
                    print(f"✅ 모닝 브리핑 전송 {today}")

            # ── 20:00 저녁 브리핑 ─────────────────────────
            if last_evening_date != today:
                wait = _next_target(20, 0)
                if wait > 3600:
                    await asyncio.sleep(min(wait - 60, 1800))
                    continue
                await asyncio.sleep(max(wait, 0))
                now = now_kst()
                if now.weekday() < 5 and ch:
                    last_evening_date = today
                    await ch.send("🌆 **저녁 브리핑 준비 중...**")
                    msg = await asyncio.get_event_loop().run_in_executor(
                        None, _build_evening_briefing_msg)
                    await send_long(ch, msg)
                    print(f"✅ 저녁 브리핑 전송 {today}")

            # 두 브리핑 모두 전송 완료 → 내일 08:00까지 대기
            if last_morning_date == today and last_evening_date == today:
                wait = _next_target(8, 0)
                print(f"📌 오늘 브리핑 완료 — 내일 08:00까지 {wait/3600:.1f}h 대기")
                await asyncio.sleep(max(wait, 60))

        except Exception as e:
            print(f"⚠️ 브리핑 오류: {e}")
            await asyncio.sleep(60)


async def status_listener():
    """10초마다 손익 변동 감지 — 단타/스윙/코인"""
    last_stock_profit = None
    last_swing_profit = None
    last_coin_profit  = None

    while True:
        await asyncio.sleep(10)
        try:
            ch = bot.get_channel(CHANNEL_ID)
            if not ch:
                continue

            # 단타봇
            state  = read_state("nbot")
            status = state.get("last_status")
            if status:
                profit = status.get("total_profit", 0)
                if (last_stock_profit is not None
                        and abs(profit - last_stock_profit) > 5000):
                    diff = profit - last_stock_profit
                    await ch.send(
                        f"💹 [단타] 손익 변동: {last_stock_profit:+,}원 → "
                        f"{profit:+,}원 ({diff:+,}원)"
                    )
                last_stock_profit = profit

            # 스윙봇
            sstate  = read_state("sbot")
            sstatus = sstate.get("last_status")
            if sstatus:
                sprofit = sstatus.get("total_profit", 0)
                if (last_swing_profit is not None
                        and abs(sprofit - last_swing_profit) > 10000):
                    diff = sprofit - last_swing_profit
                    await ch.send(
                        f"💹 [스윙] 손익 변동: {last_swing_profit:+,}원 → "
                        f"{sprofit:+,}원 ({diff:+,}원)"
                    )
                last_swing_profit = sprofit

            # 코인봇
            cstate  = read_state("cbot")
            cstatus = cstate.get("last_status")
            if cstatus:
                cprofit = cstatus.get("total_profit", 0)
                if (last_coin_profit is not None
                        and abs(cprofit - last_coin_profit) > 3000):
                    diff = cprofit - last_coin_profit
                    await ch.send(
                        f"🪙 [코인] 손익 변동: {last_coin_profit:+,}원 → "
                        f"{cprofit:+,}원 ({diff:+,}원)"
                    )
                last_coin_profit = cprofit

        except Exception:
            pass


# ============================================================
# ★ Lv1 능동 알림자 — kiki_monitor.py 로 분리
# ============================================================
# status_listener, proactive_danger_watcher, proactive_watch_monitor,
# proactive_insight_provider, proactive_daily_review
# → kiki_monitor.py 참조


# ============================================================
# 진입점
# ============================================================
if __name__ == "__main__":
    if not BOT_TOKEN:
        print("❌ DISCORD_BOT_TOKEN 환경변수가 없어요!")
        exit(1)
    if not CHANNEL_ID:
        print("❌ DISCORD_CHANNEL_ID 환경변수가 없어요!")
        exit(1)

    print("🤖 영암9 AI 비서 (키키) 시작...")
    print("📈 단타봇 | 📊 스윙봇 | 🪙 코인봇 연동")
    print("🦊 Lv1 능동 알림: 위험/주의/인사이트/일일리뷰")
    print("🏭 HTS 관심그룹 자동 동기화: 09:00 / 11:00 / 14:00")
    print("🌅 모닝 브리핑: 08:00 | 🌆 저녁 브리핑: 20:00")
    bot.run(BOT_TOKEN)
