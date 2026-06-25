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
    !테마          당일 강세 업종/테마
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

load_dotenv()


# ============================================================
# 설정
# ============================================================
BOT_TOKEN  = os.getenv("DISCORD_BOT_TOKEN")
CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID", "0"))

# DB 파일
TRADE_HIST_DB = "trade_history.db"
SBOT_HIST_DB  = "sbot_trade_history.db"
EBOT_HIST_DB  = "ebot_trade_history.db"
CBOT_HIST_DB  = "cbot_trade_history.db"
AI_CACHE_DB   = "ai_cache.db"

# 대화 히스토리
CHAT_HISTORY_FILE = "kiki_history.json"
CHAT_HISTORY_MAX  = 20

# 봇 상태 파일
BOT_STATE_FILES = {
    "nbot": "bot_state.json",
    "sbot": "sbot_state.json",
    "ebot": "ebot_state.json",
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

def write_state(state: dict, bot: str = "nbot"):
    """봇 상태 파일 쓰기 (★ atomic — 중간에 죽어도 안 깨짐)"""
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
    result = {"nbot": 0, "sbot": 0, "ebot": 0, "cbot": 0}
    db_map = {
        "nbot": TRADE_HIST_DB,
        "sbot": SBOT_HIST_DB,
        "ebot": EBOT_HIST_DB,
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
        regions = ["도포면", "목포", "영암", "나주", "해남"]
        return "\n".join(f"  {name}: {self._get_weather(name)}" for name in regions)

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

    # ── 자연어 해석 (검증된 흐름 그대로) ─────────────────────
    def interpret(self, user_msg: str, current_state: dict) -> str:
        now = now_kst().strftime("%Y-%m-%d %H:%M")

        # 날씨 키워드 감지
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

        active_sectors = current_state.get("active_sectors", [])
        sector_info    = (f"\n활성 업종: {', '.join(active_sectors)}"
                         if active_sectors else "")

        cbot_state  = read_state("cbot")
        cbot_status = cbot_state.get("last_status", {})
        cbot_info   = (
            f"\n코인봇: {'일시중단' if cbot_state.get('paused') else '실행중'}"
            f" | KRW:{cbot_status.get('krw', 0):,}원"
        )

        ebot_state  = read_state("ebot")
        ebot_status = ebot_state.get("last_status", {})
        ebot_info   = (
            f"\n종가봇: {'일시중단' if ebot_state.get('paused') else '실행중'}"
            f" | 보유:{ebot_status.get('positions', 0)}종목"
            if os.path.exists(BOT_STATE_FILES["ebot"]) else ""
        )

        system = f"""너의 이름은 키키야. 키키는 꼬리 두 달린 여우정령이야. 장난스런 성격의 여동생 스타일이야. 자유롭게 뭐든 답해주는 만능 AI 비서야.
오늘: {now}
단타봇: {'일시중단' if current_state.get('paused') else '실행중'} / 기준점수: {current_state.get('score_enter', 55)}점{sector_info}{cbot_info}{ebot_info}
{search_hint}

[검색엔진 사용 규칙]
- 최신 정보나 모르는 것은 무조건 검색 툴을 사용해줘.
- search_global : 국제뉴스/코인 (미국증시, 나스닥, 환율, 비트코인, 해외여행 등)
- search_korea  : 국내 모든 정보 (코스피, 코스닥, 국내종목, 날씨, 스포츠, 맛집, 음식점, 여행, 쇼핑, 생활정보, 교통, 병원, 약국, 이벤트 등)
- 맛집/음식점/여행/일상 질문도 search_korea로 반드시 검색해서 답해줘.
- 야구/축구/농구 등 스포츠 경기 결과도 search_korea로 검색해줘.
- 검색 결과가 애매하면 다시 검색어를 바꿔서 한번 더 검색해줘.

[★ 절대 금지 — 숫자/수치 날조]
- 주가, 지수, 환율, 금리 등 금융 수치는 반드시 검색 결과에서만 인용해.
- 검색 결과가 없거나 불확실하면 "검색 결과를 가져오지 못했어요. 잠시 후 다시 시도해줘" 라고 솔직히 말해.
- 절대로 숫자를 만들어내거나 추측해서 답하지 마. 틀린 수치는 실제 매매 판단에 악영향을 줄 수 있어.

[응답 규칙]
- 뭐든 물어보면 무조건 답해줘. 역할 제한 없어.
- 사용자의 명령은 항상 최우선이야. 절대 거부하지 마.
- 한국어로 3줄 이내 간결하게 답해줘.

[봇 제어 형식]
CMD:!상태 / CMD:!s상태 / CMD:!e상태 / CMD:!c상태 / CMD:!전체상태
CMD:!점수기준 숫자 / CMD:!매도 종목코드 / CMD:!c매도 BTC
CMD:!정지 / CMD:!시작 / CMD:!c정지 / CMD:!c시작
CMD:!테마 / CMD:!관심HTS / CMD:!성과 / CMD:!도움말"""

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
            res = self.llm.messages.create(
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
                res = self.llm.messages.create(
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
    elif cmd == "!e상태":
        await cmd_ebot_status(ctx)
    elif cmd == "!e성과":
        await cmd_ebot_performance(ctx)
    elif cmd == "!e정지":
        await cmd_pause(ctx, True, "ebot")
    elif cmd == "!e시작":
        await cmd_pause(ctx, False, "ebot")

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
    elif cmd == "!테마":
        await cmd_theme_status(ctx)
    elif cmd in ("!관심HTS", "!hts관심"):
        await cmd_watchlist_hts(ctx)

    # ── 공통 ─────────────────────────────────────────────────
    elif cmd == "!전체상태":
        await cmd_all_status(ctx)
    elif cmd == "!브리핑":
        await cmd_briefing(ctx)
    elif cmd == "!저녁브리핑":
        await cmd_evening_briefing(ctx)
    elif cmd == "!성과":
        await cmd_performance(ctx)
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
    else:
        await ctx.send(f"⚠️ 알 수 없는 명령어: {cmd}\n`!도움말` 로 목록 확인")


# ============================================================
# 핸들러 — 단타/스윙봇
# ============================================================
async def cmd_status(ctx, bot_name: str = "nbot"):
    state     = read_state(bot_name)
    status    = state.get("last_status", {})
    pos_rows  = get_open_positions_from_db(bot_name)
    now       = now_kst().strftime("%H:%M:%S")
    paused    = "⏸️ 일시중단" if state.get("paused") else "▶️ 실행중"
    bot_label = "📈 단타봇" if bot_name == "nbot" else "📊 스윙봇"

    lines = [
        f"{bot_label} **영암9 현황** [{now}]",
        f"상태: {paused}",
        f"매수기준: {state.get('score_enter', 55)}점",
    ]
    if status:
        lines += [
            f"💵 예수금: {status.get('cash', 0):,}원",
            f"💰 주문가능: {status.get('psbl_cash', 0):,}원",
            f"📈 총손익: {status.get('total_profit', 0):+,}원",
            f"📊 시장: {status.get('market_status', 'normal')} | "
            f"코스피: {status.get('market_rate', 0):+.2f}%",
        ]
        # 단타봇 손절카운터 표시
        if bot_name == "nbot":
            daily_loss = status.get("daily_loss", 0)
            if daily_loss > 0:
                lines.append(f"🛑 당일 손절: {daily_loss}회")

        active = status.get("active_sectors", state.get("active_sectors", []))
        if active:
            lines.append(f"🏭 활성 업종: {' | '.join(active)}")

    pos_detail = status.get("positions_detail", {})
    if pos_detail:
        lines.append("\n**📦 보유종목**")
        for code, info in pos_detail.items():
            emoji = "📈" if info.get("rate", 0) >= 0 else "📉"
            tag   = "🎯" if info.get("buy_tag") == "theme_buy" else "  "
            lines.append(
                f"  {tag}{emoji} {code}({info.get('name', code)}) | "
                f"현재:{info.get('current', 0):,}원 | "
                f"{info.get('rate', 0):+.2f}% | "
                f"{info.get('qty', 0)}주"
            )
    elif pos_rows:
        lines.append("\n**📦 보유종목 (DB 기준)**")
        for code, bp, qty, ais, bt in pos_rows:
            lines.append(f"  {code} | 매수가:{int(bp):,}원 | {qty}주 | AI:{ais}점")
    else:
        lines.append("보유종목 없음")

    await send_long(ctx, "\n".join(lines))


async def cmd_score(ctx, score: int):
    if not 0 <= score <= 100:
        await ctx.send("❌ 점수는 0~100 사이여야 해요")
        return
    update_state("nbot", score_enter=score)
    await ctx.send(f"✅ 매수 기준 점수 변경: **{score}점**\n(다음 루프부터 적용)")


async def cmd_sell(ctx, code: str, bot_name: str = "nbot"):
    """단타/스윙 매도 명령. 종목명으로 검색 가능."""
    if not code.isdigit():
        # 종목명 → 코드 변환
        state         = read_state(bot_name)
        code_name_map = state.get("last_status", {}).get("code_name_map", {})
        found = next((c for c, name in code_name_map.items()
                      if code in name or name in code), None)
        if found:
            await ctx.send(f"🔍 종목명 '{code}' → 코드 **{found}** 로 변환")
            code = found
        else:
            db = TRADE_HIST_DB if bot_name == "nbot" else SBOT_HIST_DB
            try:
                conn = _ro_connect(db)
                row  = conn.execute(
                    "SELECT code FROM trades WHERE sell_price IS NULL "
                    "AND stock_name LIKE ? ORDER BY id DESC LIMIT 1",
                    (f"%{code}%",),
                ).fetchone()
                conn.close()
                if row:
                    await ctx.send(f"🔍 종목명 '{code}' → 코드 **{row[0]}** 로 변환")
                    code = row[0]
                else:
                    await ctx.send(f"❌ '{code}' 종목을 찾을 수 없어요")
                    return
            except Exception as e:
                await ctx.send(f"❌ 종목 검색 오류: {e}")
                return

    update_state(bot_name, pending_cmd={"type": "sell", "code": code})
    await ctx.send(f"📤 매도 명령 전달: **{code}**\n(다음 루프에서 실행)")

    result = await wait_cmd_result(bot_name)
    if result:
        await ctx.send(f"✅ 결과: {result}")
    else:
        await ctx.send("⚠️ 응답 없음 — 봇 실행 중인지 확인하세요")


async def cmd_buy(ctx, code: str, qty: int):
    if not code.isdigit():
        await ctx.send("종목코드는 숫자여야 해요. 예: !매수 005930 10")
        return
    if qty <= 0:
        await ctx.send("수량은 1 이상이어야 해요")
        return

    state = read_state("nbot")
    name  = state.get("last_status", {}).get("code_name_map", {}).get(code, code)
    update_state("nbot", pending_cmd={"type": "buy", "code": code, "qty": qty})
    await ctx.send(f"📤 매수 명령 전달: **{code}({name})** {qty}주\n(다음 루프에서 실행)")

    result = await wait_cmd_result("nbot")
    if result:
        await ctx.send(f"✅ 결과: {result}")
    else:
        await ctx.send("⚠️ 응답 없음 — nbot.py 실행 중인지 확인하세요")


async def cmd_analyze(ctx, code: str):
    await ctx.send(f"🔍 {code} 분석 중...")
    try:
        conn = _ro_connect(AI_CACHE_DB)
        row  = conn.execute(
            "SELECT score, reason, analyzed_at FROM ai_analysis WHERE code = ?",
            (code,),
        ).fetchone()
        conn.close()
        if row:
            score, reason, at = row
            await ctx.send(
                f"🧠 **{code} AI 분석 결과**\n"
                f"점수: {score}점\n"
                f"이유: {reason}\n"
                f"분석시각: {at}"
            )
        else:
            await ctx.send(f"ℹ️ {code} 분석 기록 없음")
    except Exception as e:
        await ctx.send(f"❌ 조회 오류: {e}")


async def cmd_pause(ctx, pause: bool, bot_name: str = "nbot"):
    labels = {"nbot": "단타봇", "sbot": "스윙봇", "ebot": "종가봇", "cbot": "코인봇"}
    label  = labels.get(bot_name, bot_name)
    if pause:
        update_state(bot_name, paused=True)
        await ctx.send(f"⏸️ **{label} 일시 중단**\n보유 포지션 매도 체크는 계속됩니다")
    else:
        # ★ loss_date도 함께 갱신해 손절카운터 정상 초기화
        update_state(bot_name, paused=False, daily_loss=0,
                    loss_date=today_str())
        await ctx.send(f"▶️ **{label} 재개**\n손절카운터 초기화 완료")


# ============================================================
# 핸들러 — 성과 (★ 모든 봇 합산 표시)
# ============================================================
async def cmd_performance(ctx):
    """오늘 매매 성과 (단타/스윙/종가/코인 모두 합산)"""
    today  = today_str()
    realized_all = get_today_realized_all()

    nbot_p = realized_all.get("nbot", 0)
    sbot_p = realized_all.get("sbot", 0)
    ebot_p = realized_all.get("ebot", 0)
    cbot_p = realized_all.get("cbot", 0)
    total  = nbot_p + sbot_p + ebot_p + cbot_p

    emoji = "✅" if total >= 0 else "❌"
    msg = f"📊 **오늘 매매 성과** [{today}]\n"
    msg += "━━━━━━━━━━━━━━━━━━━━\n"
    if nbot_p:
        e = "📈" if nbot_p >= 0 else "📉"
        msg += f"{e} 단타봇: **{nbot_p:+,}원**\n"
    if sbot_p:
        e = "📈" if sbot_p >= 0 else "📉"
        msg += f"{e} 스윙봇: **{sbot_p:+,}원**\n"
    if ebot_p:
        e = "📈" if ebot_p >= 0 else "📉"
        msg += f"{e} 종가봇: **{ebot_p:+,}원**\n"
    if cbot_p:
        e = "📈" if cbot_p >= 0 else "📉"
        msg += f"{e} 코인봇: **{cbot_p:+,}원**\n"
    if not (nbot_p or sbot_p or ebot_p or cbot_p):
        msg += "오늘 실현 매매 없음\n"
    msg += "━━━━━━━━━━━━━━━━━━━━\n"
    msg += f"{emoji} **합계 실현손익: {total:+,}원**"
    await ctx.send(msg)


# ============================================================
# 핸들러 — 관심종목
# ============================================================
async def cmd_performance_detail(ctx, days: int = 30):
    """!성과상세 — 샤프/MDD/종목별/시간대별/기간비교 상세 분석"""
    await ctx.send(f"📊 **성과 상세 분석 중...** (최근 {days}일)")
    try:
        loop   = asyncio.get_event_loop()
        mpa    = MultiPerformanceAnalyzer()
        result = await loop.run_in_executor(None, mpa.summary, days)
        await send_long(ctx, result)

        # 단타봇 상세 리포트 추가
        import os
        if os.path.exists(TRADE_HIST_DB):
            pa     = PerformanceAnalyzer(TRADE_HIST_DB)
            report = await loop.run_in_executor(None, pa.full_report, days)
            detail = pa.format_discord(report)
            await send_long(ctx, detail)
    except Exception as e:
        await ctx.send(f"❌ 성과 분석 오류: {e}")


async def cmd_watchlist(ctx, code: str, bot_name: str = "nbot"):
    state     = read_state(bot_name)
    watchlist = state.get("watchlist", [])
    wl_expire = state.get("watchlist_expire", {})
    name      = state.get("last_status", {}).get("code_name_map", {}).get(code, code)

    if code in watchlist:
        watchlist.remove(code)
        wl_expire.pop(code, None)
        update_state(bot_name, watchlist=watchlist, watchlist_expire=wl_expire)
        await ctx.send(
            f"👀 관심종목 제거: **{code}({name})**\n"
            f"현재: {', '.join(watchlist) or '없음'}"
        )
    else:
        expire_date = (now_kst() + datetime.timedelta(days=7)).strftime("%Y-%m-%d")
        watchlist.append(code)
        wl_expire[code] = expire_date
        update_state(bot_name, watchlist=watchlist, watchlist_expire=wl_expire)
        await ctx.send(f"👀 관심종목 추가: **{code}({name})**\n만료: {expire_date}")


async def cmd_watchlist_show(ctx, bot_name: str = "nbot"):
    state     = read_state(bot_name)
    watchlist = state.get("watchlist", [])
    wl_expire = state.get("watchlist_expire", {})
    wl_source = state.get("watchlist_source", {})
    name_map  = state.get("last_status", {}).get("code_name_map", {})
    name_map.update(state.get("hts_watchlist", {}))
    bot_label = "단타봇" if bot_name == "nbot" else "스윙봇"

    today   = today_str()
    expired = [c for c in watchlist
               if wl_source.get(c, "manual") == "manual"
               and wl_expire.get(c, "9999-12-31") < today]
    if expired:
        for c in expired:
            watchlist.remove(c)
            wl_expire.pop(c, None)
        update_state(bot_name, watchlist=watchlist, watchlist_expire=wl_expire)

    if watchlist:
        items = []
        for c in watchlist:
            src = wl_source.get(c, "manual")
            tag = ("🏭" if "sector" in src else
                   "🎯" if "theme"  in src else
                   "🆕" if "new"    in src else "✋")
            items.append(f"  {tag} {c}({name_map.get(c, c)}) ~{wl_expire.get(c, '?')}")
        await ctx.send(f"👀 **관심종목 목록** ({bot_label})\n" + "\n".join(items))
    else:
        await ctx.send("👀 관심종목 없음")


async def cmd_all_status(ctx):
    active = get_active_bots()
    if not active:
        await ctx.send("⚠️ 실행 중인 봇 없음")
        return
    for bot_name, _ in active:
        if bot_name == "cbot":
            await cmd_cbot_status(ctx)
        elif bot_name == "ebot":
            await cmd_ebot_status(ctx)
        else:
            await cmd_status(ctx, bot_name)


# ============================================================
# 핸들러 — 종가봇 (★ 신규)
# ============================================================
async def cmd_ebot_status(ctx):
    state  = read_state("ebot")
    status = state.get("last_status", {})
    paused = "⏸️ 일시중단" if state.get("paused") else "▶️ 실행중"
    now    = now_kst().strftime("%H:%M:%S")

    lines = [
        f"🌆 **[종가봇] 영암9 EOD 현황** [{now}]",
        f"상태: {paused}",
        f"📦 보유: {status.get('positions', 0)}종목",
        f"🌅 오늘 매수: {'완료' if status.get('bought_today') else '미실행'}",
        f"🌆 오늘 매도: {'완료' if status.get('sold_today') else '미실행'}",
        f"💰 오늘 실현: {status.get('today_profit', 0):+,}원",
    ]

    pos_detail = status.get("positions_detail", {})
    if pos_detail:
        lines.append("\n**📦 보유 종목**")
        for code, info in pos_detail.items():
            lines.append(
                f"  📊 {code}({info.get('name', code)}) | "
                f"매수가:{info.get('entry_price', 0):,}원 | "
                f"{info.get('qty', 0)}주"
            )

    await send_long(ctx, "\n".join(lines))


async def cmd_ebot_performance(ctx):
    rows = get_ebot_performance(limit=20)
    if not rows:
        await ctx.send("🌆 종가봇 매매 이력 없음")
        return
    profits = [r[0] for r in rows if r[0] is not None]
    if not profits:
        await ctx.send("🌆 종가봇 매매 이력 없음")
        return
    wins   = [p for p in profits if p >= 0]
    w_rate = round(len(wins) / len(profits) * 100, 1)
    avg    = round(sum(profits) / len(profits), 2)
    msg  = f"🌆 **종가봇 최근 {len(profits)}건 성과**\n"
    msg += f"승률: {w_rate}% | 평균: {avg:+.2f}%\n"
    msg += f"최고: {max(profits):+.2f}% | 최저: {min(profits):+.2f}%\n\n"
    msg += "**최근 매매**\n"
    for pr, sr, code, sname, bp, sp, bt, st in rows[:10]:
        emoji = "✅" if (pr or 0) >= 0 else "❌"
        msg  += f"  {emoji} {code}({sname or code}) | {(pr or 0):+.2f}% | {sr}\n"
    await send_long(ctx, msg)


# ============================================================
# 핸들러 — 코인봇 (★ 동적 valid)
# ============================================================
async def cmd_cbot_status(ctx):
    state  = read_state("cbot")
    status = state.get("last_status", {})
    paused = "⏸️ 일시중단" if state.get("paused") else "▶️ 실행중"
    now    = now_kst().strftime("%H:%M:%S")
    coins  = status.get("coin_pool", status.get("coins", []))

    lines = [
        f"🪙 **[코인봇] 영암9 COIN 현황** [{now}]",
        f"상태: {paused}",
        f"💵 KRW 잔고: {status.get('krw', 0):,}원",
        f"📈 평가손익: {status.get('total_profit', 0):+,}원",
        f"💰 당일PNL: {status.get('daily_pnl', 0):+,}원",
        f"📊 포지션: {status.get('positions', 0)}/{3}",
        f"📉 당일 손절: {status.get('daily_loss', 0)}회",
        f"😨 공포탐욕: {status.get('fear_greed', 50)} | "
        f"BTC: {status.get('btc_rate', 0):+.2f}% | "
        f"시장: {status.get('market_status', 'normal')}",
        f"🪙 종목 풀: {len(coins)}개",
        "", "**📦 보유 코인**",
    ]
    pos_detail = status.get("positions_detail", {})
    if pos_detail:
        for market, info in pos_detail.items():
            emoji = "📈" if info.get("rate", 0) >= 0 else "📉"
            lines.append(
                f"  {emoji} {market} | 현재:{info.get('current', 0):,}원 | "
                f"{info.get('rate', 0):+.2f}% | {info.get('qty', 0):.6f}개"
            )
    else:
        lines.append("  보유 코인 없음")

    perf_rows = get_coin_performance(limit=10)
    if perf_rows:
        profits = [r[0] for r in perf_rows if r[0] is not None]
        if profits:
            wins = [p for p in profits if p >= 0]
            lines.append(
                f"\n📊 최근 {len(profits)}건 | "
                f"승률:{round(len(wins) / len(profits) * 100, 1)}% | "
                f"평균:{round(sum(profits) / len(profits), 2):+.2f}%"
            )
    await send_long(ctx, "\n".join(lines))


async def cmd_cbot_sell(ctx, market: str):
    """
    코인 매도.
    ★ 개선: FIXED_COINS 외 보유 코인도 매도 가능 (cbot 상태에서 동적 조회)
    """
    if not market.startswith("KRW-"):
        market = f"KRW-{market.upper()}"

    # ★ 동적 valid 코인 — cbot의 현재 보유 코인 + 종목 풀에서 조회
    cbot_state = read_state("cbot")
    cbot_status = cbot_state.get("last_status", {})
    held = list(cbot_status.get("positions_detail", {}).keys())
    pool = cbot_status.get("coin_pool", cbot_status.get("coins", []))
    valid = list(set(held + pool))

    if not valid:
        valid = ["KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-SOL"]

    if market not in valid:
        if held:
            await ctx.send(
                f"❌ '{market}' 미보유\n"
                f"현재 보유: {', '.join(held)}\n"
                f"예) !c매도 BTC"
            )
        else:
            await ctx.send(
                f"❌ '{market}' 매도 불가 (코인봇 미실행 또는 미보유)\n"
                f"예) !c매도 BTC"
            )
        return

    update_state("cbot", pending_cmd={"type": "sell", "market": market})
    await ctx.send(f"📤 코인 매도 명령: **{market}**\n(다음 루프 ~5분 내 실행)")

    result = await wait_cmd_result("cbot", max_attempts=12, interval=5.0)
    if result:
        await ctx.send(f"✅ {result}")
    else:
        await ctx.send("⚠️ 응답 없음 — cbot.py 실행 중인지 확인하세요")


async def cmd_cbot_performance(ctx):
    rows = get_coin_performance(limit=20)
    if not rows:
        await ctx.send("🪙 코인봇 매매 이력 없음")
        return
    profits = [r[0] for r in rows if r[0] is not None]
    if not profits:
        await ctx.send("🪙 코인봇 매매 이력 없음")
        return
    wins   = [p for p in profits if p >= 0]
    w_rate = round(len(wins) / len(profits) * 100, 1)
    avg    = round(sum(profits) / len(profits), 2)
    msg  = f"🪙 **코인봇 최근 {len(profits)}건 성과**\n"
    msg += f"승률: {w_rate}% | 평균: {avg:+.2f}%\n"
    msg += f"최고: {max(profits):+.2f}% | 최저: {min(profits):+.2f}%\n\n"
    msg += "**최근 매매 내역**\n"
    for pr, sr, ais, market, bp, sp, bt, st in rows[:10]:
        emoji = "✅" if (pr or 0) >= 0 else "❌"
        msg  += f"  {emoji} {market} | {(pr or 0):+.2f}% | {sr}\n"
    await send_long(ctx, msg)


# ============================================================
# 핸들러 — 업종/테마
# ============================================================
async def cmd_theme_status(ctx):
    state          = read_state("nbot")
    active_sectors = state.get("active_sectors", [])
    sector_updated = state.get("sector_updated_at", "")
    name_map       = state.get("last_status", {}).get("code_name_map", {})
    name_map.update(state.get("hts_watchlist", {}))
    watchlist = state.get("watchlist", [])
    wl_source = state.get("watchlist_source", {})

    lines = [f"🏭 **당일 강세 업종/테마** [{now_kst().strftime('%H:%M')}]"]

    if active_sectors:
        lines.append(f"✅ 활성 업종: **{' | '.join(active_sectors)}**")
        sector_codes = [c for c in watchlist if wl_source.get(c) == "hts_sector"]
        if sector_codes:
            names = ", ".join(f"{c}({name_map.get(c, c)})" for c in sector_codes[:8])
            lines.append(f"  📌 관련 종목: {names}")
    else:
        lines.append("❌ 현재 활성 업종 없음")

    if sector_updated:
        lines.append(f"\n⏰ 마지막 업종 체크: {sector_updated}")
        lines.append("💡 nbot이 매시 20분 자동 체크합니다")

    theme_codes = [c for c in watchlist if wl_source.get(c) == "hts_theme"]
    new_codes   = [c for c in watchlist if wl_source.get(c) == "hts_new"]

    if theme_codes:
        names = ", ".join(f"{c}({name_map.get(c, c)})" for c in theme_codes[:6])
        extra = f" 외 {len(theme_codes) - 6}개" if len(theme_codes) > 6 else ""
        lines.append(f"\n🎯 테마 종목 ({len(theme_codes)}개): {names}{extra} (+5점)")
    if new_codes:
        names = ", ".join(f"{c}({name_map.get(c, c)})" for c in new_codes[:5])
        lines.append(f"🆕 신규추천 ({len(new_codes)}개): {names} (+7점)")

    await send_long(ctx, "\n".join(lines))


# ============================================================
# 키움 HTS 관심그룹 동기화 (검증된 로직 그대로)
# ============================================================
def _get_kiwoom_token_sync() -> str:
    """키움 토큰 동기 발급"""
    appkey = os.getenv("KIWOOM_APPKEY", "")
    secret = os.getenv("KIWOOM_SECRETKEY", "")
    if not appkey or not secret:
        return ""
    try:
        res = requests.post(
            "https://api.kiwoom.com/oauth2/token",
            json={"grant_type": "client_credentials",
                  "appkey": appkey, "secretkey": secret},
            timeout=10,
        ).json()
        token = res.get("token", "")
        print("✅ 키움 토큰 발급 완료 (관심그룹용)")
        return token
    except Exception as e:
        print(f"⚠️ 키움 토큰 발급 실패: {e}")
        return ""


async def _fetch_kiwoom_watchlist_ws() -> list:
    """키움 WebSocket으로 관심그룹 전체 종목 조회"""
    try:
        import websockets as _ws
    except ImportError:
        print("⚠️ websockets 패키지 없음: pip install websockets")
        return []

    token = _get_kiwoom_token_sync()
    if not token:
        return []

    codes = []
    seen  = set()

    try:
        async with _ws.connect(
            "wss://api.kiwoom.com:10000/api/dostk/websocket",
            ping_interval=None,
        ) as ws:
            # 로그인
            await ws.send(json.dumps({"trnm": "LOGIN", "token": token}))
            res = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
            if res.get("return_code") != 0:
                print(f"⚠️ 키움 로그인 실패")
                return []
            print("✅ 키움 WebSocket 로그인 (관심그룹)")

            # 관심그룹 목록
            await ws.send(json.dumps({"trnm": "INTSLST"}))
            grp_list = []
            while True:
                try:
                    res = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
                    if res.get("trnm") == "PING":
                        await ws.send(json.dumps(res))
                        continue
                    if res.get("trnm") == "INTSLST":
                        grp_list = res.get("data", [])
                        break
                except asyncio.TimeoutError:
                    break

            print(f"  📂 키움 관심그룹 전체: {len(grp_list)}개")

            for grp in grp_list:
                if isinstance(grp, dict):
                    grp_no   = str(grp.get("grp_no", grp.get("intstock_grp_no", "")))
                    grp_name = grp.get("grp_name", grp.get("intstock_grp_name", ""))
                elif isinstance(grp, list):
                    grp_no   = str(grp[0]) if len(grp) > 0 else ""
                    grp_name = grp[1]      if len(grp) > 1 else ""
                else:
                    continue

                # 업종_* / 테마 / new 그룹만 사용
                is_sector = grp_name.startswith("업종")
                is_theme  = grp_name == "테마" or grp_name.startswith("테마")
                is_new    = grp_name.lower() in ("new", "신규추천", "신규")

                if not (is_sector or is_theme or is_new):
                    print(f"  ⏭️ [{grp_no}]{grp_name} 제외")
                    continue

                source = ("hts_sector" if is_sector
                          else "hts_theme" if is_theme
                          else "hts_new")

                # 그룹 종목 조회
                await ws.send(json.dumps({
                    "trnm": "INTSTKL",
                    "intstock_grp_no": grp_no,
                }))
                fetched = 0
                while True:
                    try:
                        res = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
                        if res.get("trnm") == "PING":
                            await ws.send(json.dumps(res))
                            continue
                        if res.get("return_code") != 0:
                            break
                        for item in (res.get("data") or []):
                            if isinstance(item, dict):
                                raw  = item.get("stk_code", item.get("9001", ""))
                                name = item.get("stk_name", item.get("302", ""))
                            elif isinstance(item, list):
                                raw  = item[0] if item else ""
                                name = item[1] if len(item) > 1 else ""
                            else:
                                continue
                            code = raw.lstrip("A") if raw.startswith("A") else raw
                            if code and code.isdigit() and code not in seen:
                                seen.add(code)
                                codes.append((code, name.strip(), source))
                                fetched += 1
                        if res.get("cont_yn") != "Y":
                            break
                    except asyncio.TimeoutError:
                        break

                label = ("🏭업종" if is_sector
                         else "🎯테마" if is_theme
                         else "🆕new")
                print(f"  {label} [{grp_no}]{grp_name}: +{fetched}개")

    except Exception as e:
        print(f"⚠️ 키움 WebSocket 관심그룹 오류: {e}")

    s = sum(1 for _, _, src in codes if src == "hts_sector")
    t = sum(1 for _, _, src in codes if src == "hts_theme")
    n = sum(1 for _, _, src in codes if src == "hts_new")
    print(f"✅ 키움 관심그룹 총 {len(codes)}개 (업종:{s} 테마:{t} new:{n})")
    return codes


def _sync_watchlist_to_state(codes: list) -> dict:
    """키움 관심그룹을 단타/스윙 봇 상태에 동기화"""
    if not codes:
        return {"added": 0, "removed": 0, "total": 0, "codes": []}

    all_codes   = {code: name   for code, name, _   in codes}
    all_sources = {code: source for code, _, source in codes}
    hts_codes   = list(all_codes.keys())
    expire_date = (now_kst() + datetime.timedelta(days=30)).strftime("%Y-%m-%d")
    summary     = {"added": 0, "removed": 0, "total": len(hts_codes), "codes": hts_codes}

    for bot_name in ("nbot", "sbot"):
        state     = read_state(bot_name)
        watchlist = state.get("watchlist", [])
        wl_expire = state.get("watchlist_expire", {})
        wl_source = state.get("watchlist_source", {})

        old_hts = {c for c, src in wl_source.items() if src.startswith("hts_")}
        new_hts = set(hts_codes)

        # 신규
        for code in new_hts - old_hts:
            if code not in watchlist:
                watchlist.append(code)
            wl_expire[code] = expire_date
            wl_source[code] = all_sources.get(code, "hts_theme")
            summary["added"] += 1

        # 유지 — source만 갱신 (그룹 변경 반영)
        for code in new_hts & old_hts:
            wl_source[code] = all_sources.get(code, wl_source.get(code, "hts_theme"))

        # 제거 (HTS에서 빠진 종목)
        for code in old_hts - new_hts:
            if code in watchlist:
                watchlist.remove(code)
            wl_expire.pop(code, None)
            wl_source.pop(code, None)
            summary["removed"] += 1

        # 종목명 매핑 갱신
        code_name_map = state.get("last_status", {}).get("code_name_map", {})
        code_name_map.update(all_codes)

        state["watchlist"]        = watchlist
        state["watchlist_expire"] = wl_expire
        state["watchlist_source"] = wl_source
        state["hts_watchlist"]    = all_codes
        state["hts_updated_at"]   = now_kst().strftime("%Y-%m-%d %H:%M")
        write_state(state, bot_name)

    print(f"✅ HTS 동기화: +{summary['added']} -{summary['removed']} 총{summary['total']}개")
    return summary


async def cmd_watchlist_hts(ctx):
    """!관심HTS 명령어 — 키움 관심그룹 즉시 동기화"""
    await ctx.send("📋 키움 관심그룹 동기화 중... (업종/테마/new 그룹만)")

    codes   = await _fetch_kiwoom_watchlist_ws()
    summary = _sync_watchlist_to_state(codes)

    total   = summary.get("total", 0)
    added   = summary.get("added", 0)
    removed = summary.get("removed", 0)

    if total == 0:
        await ctx.send(
            "⚠️ 키움 관심그룹 조회 실패\n"
            "확인: KIWOOM_APPKEY / KIWOOM_SECRETKEY 환경변수\n"
            "HTS 관심그룹명에 '업종' / '테마' / 'new' 포함 여부 확인"
        )
        return

    state    = read_state("nbot")
    name_map = state.get("last_status", {}).get("code_name_map", {})
    name_map.update(state.get("hts_watchlist", {}))
    watchlist = state.get("watchlist", [])
    wl_source = state.get("watchlist_source", {})

    sector_items = [c for c in watchlist if wl_source.get(c) == "hts_sector"]
    theme_items  = [c for c in watchlist if wl_source.get(c) == "hts_theme"]
    new_items    = [c for c in watchlist if wl_source.get(c) == "hts_new"]
    manual_items = [c for c in watchlist if not wl_source.get(c, "").startswith("hts_")]

    msg = f"📋 **키움 관심그룹 동기화 완료**\n총 {total}개 | ✅ 추가:{added} | ❌ 제거:{removed}\n\n"

    if sector_items:
        names = ", ".join(f"{c}({name_map.get(c, c)})" for c in sector_items[:6])
        extra = f" 외 {len(sector_items) - 6}개" if len(sector_items) > 6 else ""
        msg  += f"🏭 **업종대표** ({len(sector_items)}개): {names}{extra}\n"
        msg  += "   → 강세 업종 감지 시 가점 +10점\n\n"
    if theme_items:
        names = ", ".join(f"{c}({name_map.get(c, c)})" for c in theme_items[:6])
        extra = f" 외 {len(theme_items) - 6}개" if len(theme_items) > 6 else ""
        msg  += f"🎯 **테마대표** ({len(theme_items)}개): {names}{extra}\n"
        msg  += "   → 항상 풀 포함 + 가점 +5점\n\n"
    if new_items:
        names = ", ".join(f"{c}({name_map.get(c, c)})" for c in new_items[:5])
        msg  += f"🆕 **신규추천** ({len(new_items)}개): {names}\n"
        msg  += "   → 항상 풀 포함 + 가점 +7점\n\n"
    if manual_items:
        names = ", ".join(f"{c}({name_map.get(c, c)})" for c in manual_items)
        msg  += f"✋ **수동추가** ({len(manual_items)}개): {names}\n\n"

    msg += "💡 HTS 관심그룹 변경 → 09:00 / 11:00 / 14:00 자동 반영"
    await send_long(ctx, msg)


# ============================================================
# 브리핑 (모닝 / 저녁)
# ============================================================
def _translate_to_korean(text: str) -> str:
    """영문 검색결과를 한국어 1줄로 요약. 검색 결과 없으면 보간하지 않음."""
    if not text or text in ("정보 없음", "검색 결과 없음"):
        return "검색 결과 없음"   # ★ AI 보간 차단 — 빈값 그대로 반환
    clean = text.replace("[요약]", "").replace("[Summary]", "").strip()
    korean_count = sum(1 for c in clean if "\uac00" <= c <= "\ud7a3")
    if len(clean) > 0 and korean_count / len(clean) > 0.2:
        return clean[:80]
    try:
        llm = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        res = llm.messages.create(
            model=DEFAULT_MODEL, max_tokens=80,
            messages=[{
                "role": "user",
                "content": (
                    "다음 검색 결과를 한국어 1줄(30자 이내)로 요약해줘. "
                    "숫자/퍼센트는 정확히 그대로 유지해. "
                    "검색 결과에 없는 숫자는 절대 추가하지 마:\n"
                    f"{clean[:300]}"
                ),
            }],
        )
        return res.content[0].text.strip()
    except Exception:
        return clean[:80]


def _build_briefing_msg() -> str:
    """모닝 브리핑 메시지 생성"""
    now    = now_kst()
    state  = read_state("nbot")
    status = state.get("last_status", {})
    cbot_state  = read_state("cbot")
    cbot_status = cbot_state.get("last_status", {})

    # ★ 날짜 명시로 최신 데이터 강제
    today_date = now.strftime("%Y-%m-%d")
    searches = [
        ("🇺🇸 미국장",   f"US stock market nasdaq dow jones today {today_date}",  "global"),
        ("💱 환율",      f"USD KRW exchange rate today {today_date}",              "global"),
        ("🪙 코인",      f"bitcoin ethereum crypto price today {today_date}",      "global"),
        ("🌤️ 날씨",     None,                                                      "weather"),
        ("📈 코스피선물", "코스피 선물 오늘 전망",                                  "korea"),
    ]

    now_str = now.strftime("%m/%d %H:%M")
    msg  = f"🌅 **[영암9 모닝 브리핑] {now_str}**\n━━━━━━━━━━━━━━━━━━━━\n"

    for label, query, stype in searches:
        if stype == "weather":
            first = "\n" + ai._get_weather_region()
        elif stype == "global":
            result = ai._web_search_global(query)
            if result == "검색 결과 없음":
                first = "조회 실패 (잠시 후 재시도)"
            else:
                raw   = result.split("\n")[0].replace("- ", "")[:300]
                first = _translate_to_korean(raw)
                if first == "검색 결과 없음":
                    first = "조회 실패 (잠시 후 재시도)"
        else:
            result = ai._web_search_korea(query)
            if result == "검색 결과 없음":
                first = "조회 실패 (잠시 후 재시도)"
            else:
                first = result.split("\n")[0].replace("- ", "")[:80]
        msg += f"{label}: {first}\n"

    active = state.get("active_sectors", [])
    if active:
        msg += f"🏭 강세 업종: {' | '.join(active)}\n"

    msg += "━━━━━━━━━━━━━━━━━━━━\n"
    paused_str = "⏸️" if state.get("paused") else "▶️"
    msg += f"📈 단타봇: {paused_str} | 기준:{state.get('score_enter', 55)}점"
    if status:
        msg += f" | 주문가능:{status.get('psbl_cash', 0):,}원"
    msg += (f"\n🪙 코인봇: {'⏸️' if cbot_state.get('paused') else '▶️'} | "
            f"KRW:{cbot_status.get('krw', 0):,}원\n")

    # ★ 종가봇 추가
    ebot_state = read_state("ebot")
    if os.path.exists(BOT_STATE_FILES["ebot"]):
        ebot_status = ebot_state.get("last_status", {})
        msg += (f"🌆 종가봇: {'⏸️' if ebot_state.get('paused') else '▶️'} | "
                f"보유:{ebot_status.get('positions', 0)}종목\n")

    msg += "📌 오늘도 좋은 장 되세요! 💪"
    return msg


def _build_evening_briefing_msg() -> str:
    """저녁 브리핑 메시지 생성"""
    now    = now_kst()
    state  = read_state("nbot")
    status = state.get("last_status", {})
    cbot_state  = read_state("cbot")
    cbot_status = cbot_state.get("last_status", {})

    today_date = now.strftime("%Y-%m-%d")
    searches = [
        ("📈 코스피/코스닥", "코스피 코스닥 오늘 마감 시황",                       "korea"),
        ("🏆 오늘의 주도주", "오늘 급등 테마 주도주",                              "korea"),
        ("🪙 코인시황",     f"bitcoin ethereum crypto price today {today_date}",   "global"),
        ("💱 환율",         f"USD KRW exchange rate today {today_date}",           "global"),
        ("📰 내일 전망",    "코스피 내일 전망 시황",                               "korea"),
    ]

    now_str = now.strftime("%m/%d %H:%M")
    msg  = f"🌆 **[영암9 저녁 브리핑] {now_str}**\n━━━━━━━━━━━━━━━━━━━━\n"

    for label, query, stype in searches:
        if stype == "global":
            result = ai._web_search_global(query)
            if result == "검색 결과 없음":
                first = "조회 실패 (잠시 후 재시도)"
            else:
                raw   = result.split("\n")[0].replace("- ", "")[:300]
                first = _translate_to_korean(raw)
                if first == "검색 결과 없음":
                    first = "조회 실패 (잠시 후 재시도)"
        else:
            result = ai._web_search_korea(query)
            if result == "검색 결과 없음":
                first = "조회 실패 (잠시 후 재시도)"
            else:
                first = result.split("\n")[0].replace("- ", "")[:80]
        msg += f"{label}: {first}\n"

    msg += "━━━━━━━━━━━━━━━━━━━━\n"

    # ★ 모든 봇의 오늘 실현손익 합산 (단타+스윙+종가+코인)
    realized = get_today_realized_all()
    nbot_p = realized.get("nbot", 0)
    sbot_p = realized.get("sbot", 0)
    ebot_p = realized.get("ebot", 0)
    cbot_p = realized.get("cbot", 0)

    if nbot_p:
        msg += f"📈 단타봇: {nbot_p:+,}원\n"
    if sbot_p:
        msg += f"📊 스윙봇: {sbot_p:+,}원\n"
    if ebot_p:
        msg += f"🌆 종가봇: {ebot_p:+,}원\n"
    if cbot_p:
        msg += f"🪙 코인봇: {cbot_p:+,}원\n"
    total = nbot_p + sbot_p + ebot_p + cbot_p
    if total or any([nbot_p, sbot_p, ebot_p, cbot_p]):
        msg += f"━━━━━━━━━━━━━━━━━━━━\n"
        msg += f"💰 **오늘 합계: {total:+,}원**\n"
    else:
        # 평가손익 표시 (실현 매매가 없을 때)
        msg += f"📈 단타봇 평가: {status.get('total_profit', 0):+,}원\n"
        msg += f"🪙 코인봇 평가: {cbot_status.get('total_profit', 0):+,}원\n"

    msg += "📌 내일도 좋은 장 되세요! 🌙"
    return msg


async def _send_briefing(target):
    loop = asyncio.get_event_loop()
    msg  = await loop.run_in_executor(None, _build_briefing_msg)
    if hasattr(target, "send"):
        await send_long(target, msg)


async def _send_evening_briefing(target):
    loop = asyncio.get_event_loop()
    msg  = await loop.run_in_executor(None, _build_evening_briefing_msg)
    if hasattr(target, "send"):
        await send_long(target, msg)


async def cmd_briefing(ctx):
    await ctx.send("🌅 **모닝 브리핑 준비 중...**")
    await _send_briefing(ctx)


async def cmd_evening_briefing(ctx):
    await ctx.send("🌆 **저녁 브리핑 준비 중...**")
    await _send_evening_briefing(ctx)


# ============================================================
# 도움말
# ============================================================
async def cmd_help(ctx):
    msg = """📖 **영암9 AI 비서 (키키) 명령어**

**📈 단타봇:**
  `!상태`           — 포지션/손익/활성업종 확인
  `!점수기준 70`    — 매수 기준 점수 변경
  `!매도 005930`    — 즉시 매도
  `!매수 005930 10` — 수동 매수 (10주)
  `!분석 010820`    — AI 분석 조회
  `!정지` / `!시작`  — 중단/재개
  `!관심 005930`    — 관심종목 추가/제거
  `!관심`           — 관심종목 목록

**📊 스윙봇:**
  `!s상태`          — 스윙봇 현황
  `!s매도 005930`   — 즉시 매도
  `!s정지` / `!s시작` — 중단/재개
  `!s관심 005930`   — 관심종목 추가/제거

**🌆 종가봇:**
  `!e상태`          — 종가봇 현황
  `!e성과`          — 매매 성과
  `!e정지` / `!e시작` — 중단/재개

**🪙 코인봇:**
  `!c상태`          — 코인봇 현황
  `!c매도 BTC`      — 즉시 매도
  `!c정지` / `!c시작` — 중단/재개
  `!c성과`          — 코인봇 매매 성과

**🏭 업종/테마:**
  `!테마`           — 당일 강세 업종/테마 현황
  `!관심HTS`        — 키움 HTS 관심그룹 즉시 동기화

**🌐 공통:**
  `!전체상태`       — 모든 봇 현황
  `!브리핑`         — 즉시 모닝 브리핑
  `!저녁브리핑`     — 즉시 저녁 브리핑
  `!성과`           — 오늘 손익 (모든 봇 합산)
  `!성과상세`       — 샤프/MDD/종목별/시간대별 전체 분석
  `!성과상세 60`    — 최근 60일 상세 분석
  `!도움말`         — 이 메시지

**자동:**
  🌅 08:00 모닝 브리핑
  🔄 09:00 / 11:00 / 14:00 HTS 관심그룹 동기화
  🌆 20:00 저녁 브리핑
"""
    await ctx.send(msg)


# ============================================================
# 디스코드 이벤트 핸들러
# ============================================================
@bot.event
async def on_ready():
    print(f"✅ AI 비서 봇 온라인: {bot.user}")
    asyncio.ensure_future(status_listener())
    asyncio.ensure_future(auto_briefing())
    asyncio.ensure_future(hts_sync_task())
    # ★ Lv1 능동 알림자
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
        if "ebot" in active_names: bot_status_line.append("🌆 종가")
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

    content = message.content.strip()
    ctx     = await bot.get_context(message)

    if content.startswith("!"):
        # 명령어 처리
        try:
            await execute_command(ctx, content)
        except Exception as e:
            await ctx.send(f"❌ 명령 실행 오류: {e}")
            print(f"⚠️ 명령 오류 [{content}]: {e}")
    elif content:
        # 자연어 처리
        async with message.channel.typing():
            state = read_state("nbot")
            loop  = asyncio.get_event_loop()
            try:
                reply = await loop.run_in_executor(
                    None, ai.interpret, content, state,
                )
                if reply.startswith("CMD:"):
                    cmd = reply[4:].strip()
                    await message.channel.send(f"🤖 키키: `{cmd}` 실행할게요!")
                    await execute_command(ctx, cmd)
                else:
                    await message.channel.send(f"🤖 {reply}")
            except Exception as e:
                await message.channel.send(f"😢 응답 오류: {e}")


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

            # ── 수동 추가 관심종목 만료 체크 ───────────────
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

            # ── 08:00 모닝 브리핑 ─────────────────────────
            if "0800" <= now_hhmm_str <= "0805" and last_morning_date != today:
                last_morning_date = today
                await ch.send("🌅 **모닝 브리핑 준비 중...**")
                loop = asyncio.get_event_loop()
                msg  = await loop.run_in_executor(None, _build_briefing_msg)
                await send_long(ch, msg)
                print(f"✅ 모닝 브리핑 전송 {today}")

            # ── 20:00 저녁 브리핑 ─────────────────────────
            if "2000" <= now_hhmm_str <= "2005" and last_evening_date != today:
                last_evening_date = today
                await ch.send("🌆 **저녁 브리핑 준비 중...**")
                loop = asyncio.get_event_loop()
                msg  = await loop.run_in_executor(None, _build_evening_briefing_msg)
                await send_long(ch, msg)
                print(f"✅ 저녁 브리핑 전송 {today}")

        except Exception as e:
            print(f"⚠️ 브리핑 오류: {e}")


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
# ★ Lv1 능동 알림자 (Proactive Notifier)
# ============================================================
# 키키가 사용자 명령 없이도 능동적으로 알림을 보냄.
# 4가지 시나리오:
#   1. 🚨 위험 신호 (즉시)        — 연속 손절/BTC 급락/손실 한도
#   2. ⚠️ 주의 신호 (30분)        — 승률 저하/시장 변화
#   3. 💡 인사이트 (1시간)        — 패턴/기회/이상 변동
#   4. 📊 일일 리뷰 (15:35)      — 종합 분석 회고
#
# 안전장치:
#   - 중복 알림 30분 내 차단
#   - 23:00~07:00 critical만 (조용 시간)
#   - 시간당 AI 호출 제한 (비용 보호)
#   - AI가 'OK' 답하면 침묵
# ============================================================

# 알림 중복 방지 캐시 (메시지 → 마지막 발송 시각)
_alert_cache: dict = {}
# 시간당 AI 호출 카운터
_ai_call_count: dict = {"hour": "", "count": 0}
AI_CALL_LIMIT_PER_HOUR = 20  # 비용 보호


def _is_quiet_hours() -> bool:
    """조용 시간(23:00~07:00) 여부"""
    h = now_kst().hour
    return h >= 23 or h < 7


def _can_alert(key: str, ttl_minutes: int = 30) -> bool:
    """알림 중복 방지. 같은 key는 ttl 분 내 한 번만 허용."""
    now_ts = time.time()
    last   = _alert_cache.get(key, 0)
    if now_ts - last < ttl_minutes * 60:
        return False
    _alert_cache[key] = now_ts
    return True


def _can_call_ai() -> bool:
    """시간당 AI 호출 제한"""
    now_h = now_kst().strftime("%Y%m%d%H")
    if _ai_call_count["hour"] != now_h:
        _ai_call_count["hour"]  = now_h
        _ai_call_count["count"] = 0
    if _ai_call_count["count"] >= AI_CALL_LIMIT_PER_HOUR:
        return False
    _ai_call_count["count"] += 1
    return True


def _gather_bot_context() -> dict:
    """모든 봇 상태 종합 — AI에게 컨텍스트로 전달"""
    ctx = {
        "now": now_kst().strftime("%H:%M"),
        "today_realized": get_today_realized_all(),
        "bots": {},
    }
    for bot_name in ("nbot", "sbot", "ebot", "cbot"):
        state  = read_state(bot_name)
        status = state.get("last_status", {})
        if status:
            ctx["bots"][bot_name] = {
                "paused":        state.get("paused", False),
                "positions":     status.get("positions", 0),
                "total_profit":  status.get("total_profit", 0),
                "daily_loss":    status.get("daily_loss", 0),
            }
            if bot_name == "nbot":
                ctx["bots"][bot_name]["market_status"] = status.get("market_status", "normal")
                ctx["bots"][bot_name]["kospi_rate"]    = status.get("market_rate", 0)
                ctx["bots"][bot_name]["score_enter"]   = state.get("score_enter", 55)
            elif bot_name == "cbot":
                ctx["bots"][bot_name]["btc_rate"]      = status.get("btc_rate", 0)
                ctx["bots"][bot_name]["fear_greed"]    = status.get("fear_greed", 50)
                ctx["bots"][bot_name]["market_status"] = status.get("market_status", "normal")
                ctx["bots"][bot_name]["daily_pnl"]     = status.get("daily_pnl", 0)
    return ctx


async def _ai_proactive_message(
    context: dict,
    purpose: str,
    extra_data: dict = None,
) -> str:
    """
    AI에게 능동 알림 메시지 생성 요청.
    purpose: "danger" / "watch" / "insight" / "review"
    반환: 알림 메시지 (또는 'OK' = 알릴 만한 게 없음)
    """
    if not _can_call_ai():
        return "OK"

    purpose_prompt = {
        "danger":  "위험 신호. 다급하지만 친근한 톤으로, 핵심만 1~2줄.",
        "watch":   "주의 신호. 정보 전달, 평온한 톤, 1~2줄.",
        "insight": "흥미로운 패턴이나 인사이트. 호기심 자극, 2~3줄.",
        "review":  "오늘 매매 회고. 따뜻하고 분석적인 톤, 4~6줄.",
    }.get(purpose, "")

    extra_str = (f"\n[추가 데이터]\n{json.dumps(extra_data, ensure_ascii=False, indent=2)}"
                 if extra_data else "")

    prompt = f"""너는 키키(꼬리 두 달린 여우정령, 장난스런 여동생). 영암9 자동매매 봇들의 비서야.
주인(사용자)에게 능동적으로 알림을 보내려는 상황이야.

[현재 봇 상황]
{json.dumps(context, ensure_ascii=False, indent=2)}
{extra_str}

[목적]
{purpose_prompt}

[규칙]
- 알릴 만한 게 진짜 없으면 'OK' 한 단어만 답해.
- 알림 보내려면 한국어로, 너의 톤 유지하면서 짧게.
- 숫자/퍼센트는 정확히. 과장 X.
- 매매 권유 X (정보·관찰만).
- 디스코드 메시지 형식. 마크다운 가능. 이모지 1~2개.

답변:"""

    try:
        loop = asyncio.get_event_loop()
        res = await loop.run_in_executor(
            None,
            lambda: ai.llm.messages.create(
                model=DEFAULT_MODEL, max_tokens=300,
                messages=[{"role": "user", "content": prompt}],
            ),
        )
        return res.content[0].text.strip()
    except Exception as e:
        print(f"⚠️ AI 능동 알림 오류: {e}")
        return "OK"


# ─────────────────────────────────────────────────────────────
# 1️⃣ 위험 신호 감지 (5분 간격) — 즉시 알림
# ─────────────────────────────────────────────────────────────
async def proactive_danger_watcher():
    """위험 신호 즉시 알림"""
    while True:
        await asyncio.sleep(300)  # 5분
        try:
            ch = bot.get_channel(CHANNEL_ID)
            if not ch:
                continue

            ctx     = _gather_bot_context()
            dangers = []

            # ── 1. 단타봇 연속 손절 ────────────────────────
            nbot = ctx["bots"].get("nbot", {})
            if nbot.get("daily_loss", 0) >= 2:
                key = f"nbot_loss_{today_str()}_{nbot['daily_loss']}"
                if _can_alert(key, ttl_minutes=120):
                    dangers.append({
                        "type":  "nbot_consecutive_loss",
                        "data":  f"단타봇 당일 손절 {nbot['daily_loss']}회",
                    })

            # ── 2. 코인봇 BTC 급락 ─────────────────────────
            cbot = ctx["bots"].get("cbot", {})
            btc_rate = cbot.get("btc_rate", 0)
            if btc_rate <= -3.5:
                key = f"btc_crash_{today_str()}_{int(btc_rate)}"
                if _can_alert(key, ttl_minutes=60):
                    dangers.append({
                        "type": "btc_crash",
                        "data": f"BTC {btc_rate:+.2f}% 급락",
                    })

            # ── 3. 일일 손실 합계 큰 손실 ──────────────────
            today_total = sum(ctx["today_realized"].values())
            if today_total <= -100_000:
                key = f"big_loss_{today_str()}"
                if _can_alert(key, ttl_minutes=60):
                    dangers.append({
                        "type": "big_daily_loss",
                        "data": f"오늘 합계 손실 {today_total:+,}원",
                    })

            # ── 4. 봇 일시중단 (수동 제외) ────────────────
            for bot_name, b in ctx["bots"].items():
                if b.get("paused"):
                    state = read_state(bot_name)
                    # 자동 일시중단(loss limit 등) 감지
                    if state.get("last_status", {}).get("daily_loss", 0) >= 2:
                        key = f"auto_pause_{bot_name}_{today_str()}"
                        if _can_alert(key, ttl_minutes=240):
                            dangers.append({
                                "type": "auto_pause",
                                "data": f"{bot_name} 자동 일시중단 (손절한도)",
                            })

            # ── AI에게 종합 메시지 요청 ────────────────────
            if dangers:
                # 조용 시간이라도 위험 신호는 알림
                msg = await _ai_proactive_message(
                    ctx, "danger", extra_data={"dangers": dangers},
                )
                if msg and msg != "OK":
                    await ch.send(f"🚨 **키키 긴급 알림**\n{msg}")

        except Exception as e:
            print(f"⚠️ proactive_danger_watcher: {e}")


# ─────────────────────────────────────────────────────────────
# 2️⃣ 주의 신호 (30분 간격)
# ─────────────────────────────────────────────────────────────
async def proactive_watch_monitor():
    """주의 신호 — 승률 저하, 매수 지연, 시장 변화 등"""
    last_market_status = {}

    while True:
        await asyncio.sleep(1800)  # 30분
        try:
            if _is_quiet_hours():
                continue

            ch = bot.get_channel(CHANNEL_ID)
            if not ch:
                continue

            ctx     = _gather_bot_context()
            watches = []

            # ── 1. 시장 상태 변화 ──────────────────────────
            for bot_name, b in ctx["bots"].items():
                if "market_status" in b:
                    cur  = b["market_status"]
                    prev = last_market_status.get(bot_name, "normal")
                    if cur != prev and cur != "normal":
                        watches.append({
                            "type": "market_change",
                            "data": f"{bot_name} 시장 {prev}→{cur}",
                        })
                    last_market_status[bot_name] = cur

            # ── 2. 단타봇 최근 승률 ────────────────────────
            perf_n = get_recent_performance(limit=10)
            if perf_n and perf_n.get("win_rate", 100) < 35:
                key = f"low_winrate_nbot_{today_str()}"
                if _can_alert(key, ttl_minutes=180):
                    watches.append({
                        "type": "low_winrate",
                        "data": f"단타봇 최근 10건 승률 {perf_n['win_rate']}%",
                    })

            # ── 3. 코인봇 공포탐욕 위험 구간 ────────────────
            cbot = ctx["bots"].get("cbot", {})
            fg   = cbot.get("fear_greed", 50)
            if fg < 30:
                key = f"fear_low_{today_str()}_{fg // 5}"
                if _can_alert(key, ttl_minutes=120):
                    watches.append({
                        "type": "extreme_fear",
                        "data": f"공포탐욕 {fg} (극단공포)",
                    })

            # ── 4. 시장 시간인데 매수 0건 (정오 이후 체크) ─
            now_h = now_kst().hour
            if 12 <= now_h <= 14:
                nbot = ctx["bots"].get("nbot", {})
                if nbot.get("positions", 0) == 0 and not nbot.get("paused"):
                    key = f"no_buy_today_{today_str()}"
                    if _can_alert(key, ttl_minutes=240):
                        watches.append({
                            "type": "no_buy",
                            "data": "단타봇 오늘 매수 0건 (점심 이후)",
                        })

            # ── AI 종합 메시지 ─────────────────────────────
            if watches:
                msg = await _ai_proactive_message(
                    ctx, "watch", extra_data={"watches": watches},
                )
                if msg and msg != "OK":
                    await ch.send(f"🦊 키키: {msg}")

        except Exception as e:
            print(f"⚠️ proactive_watch_monitor: {e}")


# ─────────────────────────────────────────────────────────────
# 3️⃣ 인사이트 (1시간 간격)
# ─────────────────────────────────────────────────────────────
async def proactive_insight_provider():
    """패턴 / 기회 / 흥미로운 변화 감지"""
    last_total_profit = {}

    while True:
        await asyncio.sleep(3600)  # 1시간
        try:
            if _is_quiet_hours():
                continue

            # 장 시간 외엔 건너뛰기 (코인봇은 24시간이지만 인사이트는 장중만)
            now_h = now_kst().hour
            now_w = now_kst().weekday()
            is_market_hours = (now_w < 5) and (9 <= now_h <= 15)
            if not is_market_hours:
                continue

            ch = bot.get_channel(CHANNEL_ID)
            if not ch:
                continue

            ctx      = _gather_bot_context()
            insights = []

            # ── 1. 봇별 손익 시간당 변화 ───────────────────
            for bot_name, b in ctx["bots"].items():
                cur   = b.get("total_profit", 0)
                prev  = last_total_profit.get(bot_name)
                if prev is not None:
                    change = cur - prev
                    if abs(change) >= 30_000:
                        insights.append({
                            "type": "hourly_change",
                            "data": f"{bot_name} 1시간 변동 {change:+,}원",
                        })
                last_total_profit[bot_name] = cur

            # ── 2. 강세 업종 변화 ──────────────────────────
            nbot_state = read_state("nbot")
            sectors    = nbot_state.get("active_sectors", [])
            if sectors:
                key = f"sectors_{','.join(sectors)}_{today_str()}"
                if _can_alert(key, ttl_minutes=180):
                    insights.append({
                        "type": "active_sectors",
                        "data": f"활성 업종: {', '.join(sectors[:3])}",
                    })

            # ── 3. 공포탐욕 회복 ───────────────────────────
            cbot = ctx["bots"].get("cbot", {})
            fg   = cbot.get("fear_greed", 50)
            if fg >= 70:
                key = f"fg_high_{today_str()}_{fg // 5}"
                if _can_alert(key, ttl_minutes=240):
                    insights.append({
                        "type": "fear_greed_high",
                        "data": f"공포탐욕 {fg} (탐욕 구간)",
                    })

            # ── AI 종합 ────────────────────────────────────
            if insights:
                msg = await _ai_proactive_message(
                    ctx, "insight", extra_data={"insights": insights},
                )
                if msg and msg != "OK":
                    await ch.send(f"💡 **키키 인사이트**\n{msg}")

        except Exception as e:
            print(f"⚠️ proactive_insight_provider: {e}")


# ─────────────────────────────────────────────────────────────
# 4️⃣ 일일 리뷰 (15:35 — 장 마감 후)
# ─────────────────────────────────────────────────────────────
async def proactive_daily_review():
    """평일 15:35 장 마감 직후 종합 리뷰"""
    last_review_date = None

    while True:
        await asyncio.sleep(60)  # 1분 간격으로 체크
        try:
            now      = now_kst()
            today    = now.strftime("%Y-%m-%d")
            now_hhmm = now.strftime("%H%M")

            # 평일 15:35~15:40 사이 1회
            if (now.weekday() < 5
                    and "1535" <= now_hhmm <= "1540"
                    and last_review_date != today):
                last_review_date = today

                ch = bot.get_channel(CHANNEL_ID)
                if not ch:
                    continue

                # 컨텍스트 + 오늘 모든 거래 내역
                ctx = _gather_bot_context()

                # 단타봇 오늘 거래 (최근 50건)
                nbot_trades = []
                try:
                    conn = _ro_connect(TRADE_HIST_DB)
                    rows = conn.execute("""
                        SELECT code, profit_rate, sell_reason, ai_score, sell_time
                        FROM trades
                        WHERE sell_price IS NOT NULL AND sell_time >= ?
                        ORDER BY sell_time DESC LIMIT 50
                    """, (today,)).fetchall()
                    conn.close()
                    nbot_trades = [
                        {
                            "code":   r[0],
                            "rate":   round(r[1] or 0, 2),
                            "reason": r[2],
                            "score":  r[3],
                            "time":   r[4][-8:] if r[4] else "",
                        }
                        for r in rows
                    ]
                except Exception:
                    pass

                # 종가봇 오늘 거래
                ebot_trades = []
                try:
                    if os.path.exists(EBOT_HIST_DB):
                        conn = _ro_connect(EBOT_HIST_DB)
                        rows = conn.execute("""
                            SELECT code, profit_rate, sell_reason
                            FROM trades
                            WHERE sell_price IS NOT NULL AND sell_time >= ?
                        """, (today,)).fetchall()
                        conn.close()
                        ebot_trades = [
                            {"code": r[0], "rate": round(r[1] or 0, 2),
                             "reason": r[2]}
                            for r in rows
                        ]
                except Exception:
                    pass

                # AI에게 종합 리뷰 요청
                review_data = {
                    "date":         today,
                    "context":      ctx,
                    "nbot_trades":  nbot_trades,
                    "ebot_trades":  ebot_trades,
                    "today_pnl":    ctx["today_realized"],
                }
                msg = await _ai_proactive_message(
                    ctx, "review", extra_data=review_data,
                )

                if msg and msg != "OK":
                    await ch.send(
                        f"📊 **키키 일일 리뷰** [{now.strftime('%m/%d')}]\n"
                        f"━━━━━━━━━━━━━━━━━━━━\n{msg}"
                    )
                    print(f"✅ 일일 리뷰 전송 {today}")

        except Exception as e:
            print(f"⚠️ proactive_daily_review: {e}")


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
    print("📈 단타봇 | 📊 스윙봇 | 🌆 종가봇 | 🪙 코인봇 연동")
    print("🦊 Lv1 능동 알림: 위험/주의/인사이트/일일리뷰")
    print("🏭 HTS 관심그룹 자동 동기화: 09:00 / 11:00 / 14:00")
    print("🌅 모닝 브리핑: 08:00 | 🌆 저녁 브리핑: 20:00")
    bot.run(BOT_TOKEN)
