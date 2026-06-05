"""
kiki.py — 영암9 AI 비서 디스코드 봇 (통합본)
==========================================
[지원 명령어]
  !상태          : 단타봇 현황 (활성 업종 포함)
  !s상태         : 스윙봇 현황
  !c상태         : 코인봇 현황
  !전체상태      : 모든 봇 현황
  !점수기준 70   : 단타봇 매수 기준 점수 변경
  !매도 005930   : 단타봇 즉시 매도
  !s매도 005930  : 스윙봇 즉시 매도
  !c매도 BTC     : 코인봇 즉시 매도
  !매수 005930 10: 단타봇 수동 매수
  !분석 010820   : AI 분석 결과 조회
  !정지 / !시작  : 단타봇 중단/재개
  !s정지 /!s시작 : 스윙봇 중단/재개
  !c정지 /!c시작 : 코인봇 중단/재개
  !테마          : 당일 강세 업종/테마 현황
  !관심HTS       : 키움 HTS 관심그룹 즉시 동기화
  !관심 005930   : 관심종목 추가/제거
  !관심          : 관심종목 목록
  !브리핑        : 즉시 모닝 브리핑
  !저녁브리핑    : 즉시 저녁 브리핑
  !성과          : 오늘 손익 (단순)
  !c성과         : 코인봇 매매 성과
  !도움말        : 명령어 목록

[자동]
  평일 08:00 — 모닝 브리핑
  평일 09:00 / 11:00 / 14:00 — HTS 관심그룹 동기화
  평일 20:00 — 저녁 브리핑

[변경 이력]
  2026-04-27 kiki.py 최초 정리
  2026-05-01 통합본
    - cbot.py(코인봇) 연동
    - nbot/sbot/cbot 멀티봇 구조
    - !테마 명령어 추가
    - !관심HTS 키움 HTS 관심그룹 동기화
    - 업종_* / 테마 / new 그룹명 규칙
    - 09:00 / 11:00 / 14:00 HTS 자동 동기화
    - 브리핑에 활성 업종 / 코인 정보 추가
    - !상태에 활성 업종 / buy_tag 표시
  2026-05-04 !성과 단순화
    - 오늘 총손익 금액만 표시 (세부내역 제거)
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

try:
    import pytz
    KST = pytz.timezone("Asia/Seoul")
    def now_kst():
        return datetime.datetime.now(KST)
except ImportError:
    def now_kst():
        return (
            datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
            + datetime.timedelta(hours=9)
        )

import requests
import discord
from discord.ext import commands
from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv()


# ============================================================
# 설정
# ============================================================
BOT_TOKEN         = os.getenv("DISCORD_BOT_TOKEN")
CHANNEL_ID        = int(os.getenv("DISCORD_CHANNEL_ID", "0"))
TRADE_HIST_DB     = "trade_history.db"
SBOT_HIST_DB      = "sbot_trade_history.db"
CBOT_HIST_DB      = "cbot_trade_history.db"
AI_CACHE_DB       = "ai_cache.db"
CHAT_HISTORY_FILE = "kiki_history.json"
CHAT_HISTORY_MAX  = 20

BOT_STATE_FILES = {
    "nbot": "bot_state.json",
    "sbot": "sbot_state.json",
    "cbot": "cbot_state.json",
}


# ============================================================
# 상태 파일 헬퍼
# ============================================================

def read_state(bot: str = "nbot") -> dict:
    fname = BOT_STATE_FILES.get(bot, "bot_state.json")
    try:
        if os.path.exists(fname):
            with open(fname, "r") as f:
                return json.load(f)
    except Exception:
        pass
    return {"paused": False, "score_enter": 55,
            "pending_cmd": None, "cmd_result": None, "last_status": None}

def write_state(state: dict, bot: str = "nbot"):
    fname = BOT_STATE_FILES.get(bot, "bot_state.json")
    try:
        with open(fname, "w") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"⚠️ 상태 파일 저장 오류: {e}")

def update_state(bot: str = "nbot", **kwargs):
    state = read_state(bot)
    state.update(kwargs)
    write_state(state, bot)

def get_active_bots() -> list:
    active = []
    for name, fname in BOT_STATE_FILES.items():
        if os.path.exists(fname):
            state = read_state(name)
            last  = state.get("last_update", "")
            active.append((name, last))
    return active


# ============================================================
# DB 조회 헬퍼
# ============================================================

def get_recent_performance(limit: int = 20, db: str = None) -> list:
    db = db or TRADE_HIST_DB
    try:
        conn = sqlite3.connect(db)
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
    db = TRADE_HIST_DB if bot == "nbot" else SBOT_HIST_DB
    try:
        conn = sqlite3.connect(db)
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
    try:
        conn = sqlite3.connect(CBOT_HIST_DB)
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


# ============================================================
# AI 비서 클래스
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
        "미국장":   "US stock market nasdaq dow today",
        "미국 장":  "US stock market nasdaq dow today",
        "나스닥":   "nasdaq composite today",
        "트럼프":   "trump news today",
        "중동":     "middle east news today",
        "환율":     "USD KRW exchange rate today",
        "코스피":   "코스피 오늘 시황",
        "코스닥":   "코스닥 오늘 시황",
        "유가":     "crude oil WTI price today",
        "금값":     "gold price today",
        "반도체":   "semiconductor industry news today",
        "기아":     "기아 타이거즈 오늘 경기 결과 스코어",
        "기아야구":  "기아 타이거즈 오늘 경기 결과 스코어",
        "타이거즈":  "기아 타이거즈 오늘 경기 결과 스코어",
        "야구":     "KBO 오늘 야구 경기 결과 스코어",
        "KBO":      "KBO 오늘 야구 경기 결과 스코어",
        "롯데":     "롯데 자이언츠 오늘 경기 결과",
        "삼성":     "삼성 라이온즈 오늘 경기 결과",
        "한화":     "한화 이글스 오늘 경기 결과",
        "두산":     "두산 베어스 오늘 경기 결과",
        "LG":       "LG 트윈스 오늘 경기 결과",
        "축구":     "K리그 오늘 축구 경기 결과",
        "손흥민":   "손흥민 경기 결과 오늘",
        "삼성":     "삼성전자 뉴스 오늘",
        "선물":     "코스피 선물 오늘",
        "비트코인": "bitcoin BTC price today",
        "비트":     "bitcoin BTC price today",
        "이더리움": "ethereum ETH price today",
        "코인":     "cryptocurrency market bitcoin today",
    }

    def __init__(self):
        self.llm     = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        self.history = self._load_history()
        if self.history:
            print(f"♻️ 대화 히스토리 복원: {len(self.history)}개")

    def _load_history(self) -> list:
        try:
            if os.path.exists(CHAT_HISTORY_FILE):
                with open(CHAT_HISTORY_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    today = datetime.datetime.now().strftime("%Y-%m-%d")
                    if data.get("date") == today:
                        return data.get("history", [])
        except Exception as e:
            print(f"⚠️ 히스토리 로드 오류: {e}")
        return []

    def _save_history(self):
        try:
            today = datetime.datetime.now().strftime("%Y-%m-%d")
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
            url    = "https://apihub.kma.go.kr/api/typ02/openApi/VilageFcstInfoService_2.0/getUltraSrtNcst"
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

    # ── 검색 ─────────────────────────────────────────────────

    def _web_search_global(self, query: str) -> str:
        tavily_key = os.getenv("TAVILY_API_KEY", "")
        if tavily_key:
            try:
                res = requests.post(
                    "https://api.tavily.com/search",
                    json={
                        "api_key": tavily_key, "query": query,
                        "search_depth": "basic", "include_answer": True,
                        "include_raw_content": False, "max_results": 5,
                    },
                    timeout=8,
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
        # ★ Tavily 먼저 시도 (실시간 정보 더 정확)
        tavily_key = os.getenv("TAVILY_API_KEY", "")
        if tavily_key:
            try:
                res = requests.post(
                    "https://api.tavily.com/search",
                    json={
                        "api_key":        tavily_key,
                        "query":          query,
                        "search_depth":   "basic",
                        "include_answer": True,
                        "include_raw_content": False,
                        "max_results":    5,
                    },
                    timeout=8,
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

                # 맛집/장소 키워드면 지역 검색 API 사용
                local_keywords = ["맛집", "음식점", "식당", "카페", "병원", "약국", "마트", "쇼핑", "숙박", "호텔", "펜션"]
                use_local = any(kw in query for kw in local_keywords)

                if use_local:
                    local_url = f"https://openapi.naver.com/v1/search/local.json?query={encoded}&display=5&sort=comment"
                    local_res = requests.get(local_url, headers=headers, timeout=5).json()
                    items = local_res.get("items", [])
                    if items:
                        results = []
                        for item in items:
                            title   = re.sub(r"<[^>]+>", "", item.get("title", "")).strip()
                            address = item.get("roadAddress", item.get("address", "")).strip()
                            cat     = item.get("category", "").strip()
                            tel     = item.get("telephone", "").strip()
                            if title:
                                info = f"- {title}"
                                if cat:     info += f" [{cat}]"
                                if address: info += f" | {address}"
                                if tel:     info += f" | ☎{tel}"
                                results.append(info)
                        if results:
                            return "\n".join(results)

                # 일반 뉴스 검색
                url   = f"https://openapi.naver.com/v1/search/news.json?query={encoded}&display=5&sort=date"
                items = requests.get(url, headers=headers, timeout=5).json().get("items", [])
                if items:
                    results = []
                    for item in items:
                        title = re.sub(r"<[^>]+>", "", item.get("title", "")).strip()
                        desc  = re.sub(r"<[^>]+>", "", item.get("description", "")).strip()[:80]
                        date  = item.get("pubDate", "")[:16]
                        if title:
                            results.append(f"- {title} ({desc}) [{date}]")
                    if results:
                        return "\n".join(results)
            except Exception as e:
                print(f"네이버 검색 오류: {e}")

        try:
            encoded = urllib.parse.quote(query)
            url     = f"https://news.google.com/rss/search?q={encoded}&hl=ko&gl=KR&ceid=KR:ko"
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

    # ── Claude 해석 ───────────────────────────────────────────

    def interpret(self, user_msg: str, current_state: dict) -> str:
        now = now_kst().strftime("%Y-%m-%d %H:%M")

        weather_keywords = ["날씨", "기온", "온도", "비", "눈", "맑", "흐림"]
        if any(kw in user_msg for kw in weather_keywords):
            detected_loc = next((loc for loc in self.LOCATIONS if loc in user_msg), None)
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
        sector_info    = f"\n활성 업종: {', '.join(active_sectors)}" if active_sectors else ""

        cbot_state  = read_state("cbot")
        cbot_status = cbot_state.get("last_status", {})
        cbot_info   = (
            f"\n코인봇: {'일시중단' if cbot_state.get('paused') else '실행중'}"
            f" | KRW:{cbot_status.get('krw', 0):,}원"
        )

        system = f"""너의 이름은 키키야. 키키는 꼬리 두 달린 여우정령이야. 장난스런 성격의 여동생 스타일이야. 자유롭게 뭐든 답해주는 만능 AI 비서야.
오늘: {now}
단타봇: {'일시중단' if current_state.get('paused') else '실행중'} / 기준점수: {current_state.get('score_enter', 55)}점{sector_info}{cbot_info}
{search_hint}

[검색엔진 사용 규칙]
- 최신 정보나 모르는 것은 무조건 검색 툴을 사용해줘. 절대 모른다고 하지 마.
- search_global : 국제뉴스/코인 (미국증시, 나스닥, 환율, 비트코인, 해외여행 등)
- search_korea  : 국내 모든 정보 (코스피, 코스닥, 국내종목, 날씨, 스포츠, 맛집, 음식점, 여행, 쇼핑, 생활정보, 교통, 병원, 약국, 이벤트 등)
- 맛집/음식점/여행/일상 질문도 search_korea로 반드시 검색해서 답해줘.
- 야구/축구/농구 등 스포츠 경기 결과도 search_korea로 검색해줘. 절대 모른다고 하지 마.
- 검색 결과가 애매하면 다시 검색어를 바꿔서 한번 더 검색해줘.

[응답 규칙]
- 뭐든 물어보면 무조건 답해줘. 역할 제한 없어.
- 사용자의 명령은 항상 최우선이야. 절대 거부하지 마.
- 한국어로 3줄 이내 간결하게 답해줘.

[봇 제어 형식]
CMD:!상태 / CMD:!s상태 / CMD:!c상태 / CMD:!전체상태
CMD:!점수기준 숫자 / CMD:!매도 종목코드 / CMD:!c매도 BTC
CMD:!정지 / CMD:!시작 / CMD:!c정지 / CMD:!c시작
CMD:!테마 / CMD:!관심HTS / CMD:!성과 / CMD:!도움말"""

        tools = [
            {
                "name": "search_global",
                "description": "국제 뉴스 및 코인 시세 검색. 미국증시, 나스닥, 환율, 비트코인 등. Tavily 엔진.",
                "input_schema": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            },
            {
                "name": "search_korea",
                "description": "국내 모든 정보 검색. 코스피/코스닥/종목, 날씨, 스포츠, 맛집, 음식점, 여행, 생활정보, 교통, 병원 등 일상 모든 것. 네이버 엔진.",
                "input_schema": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            },
        ]

        self.history.append({"role": "user", "content": user_msg})
        messages = self.history[-10:]
        res      = self.llm.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            system=system,
            tools=tools,
            messages=messages,
        )

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
            res = self.llm.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=1024,
                system=system,
                tools=tools,
                messages=messages,
            )

        reply = "".join(b.text for b in res.content if hasattr(b, "text")).strip()
        self.history.append({"role": "assistant", "content": reply})
        self._save_history()
        return reply or "응답을 생성하지 못했어요."


# ============================================================
# 디스코드 봇
# ============================================================

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)
ai  = AIAssistant()


# ============================================================
# 유틸리티
# ============================================================

async def send_long(ctx, text: str, max_len: int = 1900):
    for i in range(0, len(text), max_len):
        await ctx.send(text[i:i + max_len])


# ============================================================
# 명령어 라우터
# ============================================================

async def execute_command(ctx, cmd: str):
    cmd = cmd.strip()

    # 단타봇
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

    # 스윙봇
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

    # 코인봇
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

    # 업종/테마
    elif cmd == "!테마":
        await cmd_theme_status(ctx)
    elif cmd in ("!관심HTS", "!hts관심"):
        await cmd_watchlist_hts(ctx)

    # 공통
    elif cmd == "!전체상태":
        await cmd_all_status(ctx)
    elif cmd == "!브리핑":
        await cmd_briefing(ctx)
    elif cmd == "!저녁브리핑":
        await cmd_evening_briefing(ctx)
    elif cmd == "!성과":
        await cmd_performance(ctx)
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
        await ctx.send("❌ 점수는 0~100 사이여야 해요"); return
    update_state("nbot", score_enter=score)
    await ctx.send(f"✅ 매수 기준 점수 변경: **{score}점**\n(다음 루프부터 적용)")


async def cmd_sell(ctx, code: str, bot_name: str = "nbot"):
    if not code.isdigit():
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
                conn = sqlite3.connect(db)
                row  = conn.execute(
                    "SELECT code FROM trades WHERE sell_price IS NULL AND stock_name LIKE ? ORDER BY id DESC LIMIT 1",
                    (f"%{code}%",)
                ).fetchone()
                conn.close()
                if row:
                    await ctx.send(f"🔍 종목명 '{code}' → 코드 **{row[0]}** 로 변환")
                    code = row[0]
                else:
                    await ctx.send(f"❌ '{code}' 종목을 찾을 수 없어요"); return
            except Exception as e:
                await ctx.send(f"❌ 종목 검색 오류: {e}"); return

    update_state(bot_name, pending_cmd={"type": "sell", "code": code})
    await ctx.send(f"📤 매도 명령 전달: **{code}**\n(다음 루프에서 실행)")

    for _ in range(12):
        await asyncio.sleep(5)
        state  = read_state(bot_name)
        result = state.get("cmd_result")
        if result:
            update_state(bot_name, cmd_result=None)
            await ctx.send(f"✅ 결과: {result}"); return
    await ctx.send("⚠️ 응답 없음 — 봇 실행 중인지 확인하세요")


async def cmd_buy(ctx, code: str, qty: int):
    if not code.isdigit():
        await ctx.send("종목코드는 숫자여야 해요. 예: !매수 005930 10"); return
    if qty <= 0:
        await ctx.send("수량은 1 이상이어야 해요"); return

    state = read_state("nbot")
    name  = state.get("last_status", {}).get("code_name_map", {}).get(code, code)
    update_state("nbot", pending_cmd={"type": "buy", "code": code, "qty": qty})
    await ctx.send(f"📤 매수 명령 전달: **{code}({name})** {qty}주\n(다음 루프에서 실행)")

    for _ in range(12):
        await asyncio.sleep(5)
        state  = read_state("nbot")
        result = state.get("cmd_result")
        if result:
            update_state("nbot", cmd_result=None)
            await ctx.send(f"✅ 결과: {result}"); return
    await ctx.send("⚠️ 응답 없음 — nbot.py 실행 중인지 확인하세요")


async def cmd_analyze(ctx, code: str):
    await ctx.send(f"🔍 {code} 분석 중...")
    try:
        conn = sqlite3.connect(AI_CACHE_DB)
        row  = conn.execute(
            "SELECT score, reason, analyzed_at FROM ai_analysis WHERE code = ?", (code,)
        ).fetchone()
        conn.close()
        if row:
            score, reason, at = row
            await ctx.send(
                f"🧠 **{code} AI 분석 결과**\n점수: {score}점\n이유: {reason}\n분석시각: {at}"
            )
        else:
            await ctx.send(f"ℹ️ {code} 분석 기록 없음")
    except Exception as e:
        await ctx.send(f"❌ 조회 오류: {e}")


async def cmd_pause(ctx, pause: bool, bot_name: str = "nbot"):
    labels = {"nbot": "단타봇", "sbot": "스윙봇", "cbot": "코인봇"}
    label  = labels.get(bot_name, bot_name)
    if pause:
        update_state(bot_name, paused=True)
        await ctx.send(f"⏸️ **{label} 일시 중단**\n보유 포지션 매도 체크는 계속됩니다")
    else:
        update_state(bot_name, paused=False, daily_loss=0)
        await ctx.send(f"▶️ **{label} 재개**\n손절카운터 초기화 완료")


# ============================================================
# ★ 성과 명령어 — 단순화 (오늘 손익만 표시)
# ============================================================

async def cmd_performance(ctx):
    today = now_kst().strftime("%Y-%m-%d")

    # 오늘 매도 완료 건만 금액으로 합산 (보유 중인 건 제외)
    try:
        conn = sqlite3.connect(TRADE_HIST_DB)
        rows = conn.execute("""
            SELECT buy_price, sell_price, qty FROM trades
            WHERE sell_price IS NOT NULL
              AND sell_time >= ?
        """, (today,)).fetchall()
        conn.close()
        realized = sum(int((sp - bp) * qty) for bp, sp, qty in rows)
    except Exception:
        realized = 0

    emoji = "✅" if realized >= 0 else "❌"
    msg   = (
        f"📊 **오늘 매매 성과** [{today}]\n"
        f"{emoji} 실현손익: **{realized:+,}원**"
    )
    await ctx.send(msg)


async def cmd_watchlist(ctx, code: str, bot_name: str = "nbot"):
    state     = read_state(bot_name)
    watchlist = state.get("watchlist", [])
    wl_expire = state.get("watchlist_expire", {})
    name      = state.get("last_status", {}).get("code_name_map", {}).get(code, code)

    if code in watchlist:
        watchlist.remove(code)
        wl_expire.pop(code, None)
        update_state(bot_name, watchlist=watchlist, watchlist_expire=wl_expire)
        await ctx.send(f"👀 관심종목 제거: **{code}({name})**\n현재: {', '.join(watchlist) or '없음'}")
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

    today   = now_kst().strftime("%Y-%m-%d")
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
            tag = "🏭" if "sector" in src else ("🎯" if "theme" in src else ("🆕" if "new" in src else "✋"))
            items.append(f"  {tag} {c}({name_map.get(c, c)}) ~{wl_expire.get(c, '?')}")
        await ctx.send(f"👀 **관심종목 목록** ({bot_label})\n" + "\n".join(items))
    else:
        await ctx.send("👀 관심종목 없음")


async def cmd_all_status(ctx):
    active = get_active_bots()
    if not active:
        await ctx.send("⚠️ 실행 중인 봇 없음"); return
    for bot_name, _ in active:
        if bot_name == "cbot":
            await cmd_cbot_status(ctx)
        else:
            await cmd_status(ctx, bot_name)


# ============================================================
# 핸들러 — 코인봇
# ============================================================

async def cmd_cbot_status(ctx):
    state  = read_state("cbot")
    status = state.get("last_status", {})
    paused = "⏸️ 일시중단" if state.get("paused") else "▶️ 실행중"
    now    = now_kst().strftime("%H:%M:%S")
    coins  = status.get("coins", ["KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-SOL"])

    lines = [
        f"🪙 **[코인봇] 영암9 COIN 현황** [{now}]",
        f"상태: {paused}",
        f"💵 KRW 잔고: {status.get('krw', 0):,}원",
        f"📈 평가손익: {status.get('total_profit', 0):+,}원",
        f"📊 포지션: {status.get('positions', 0)}/{len(coins)}",
        f"📉 당일 손절: {status.get('daily_loss', 0)}회",
        f"😨 공포탐욕: {status.get('fear_greed', 50)} | BTC: {status.get('btc_rate', 0):+.2f}%",
        "", "**📦 보유 코인**",
    ]
    pos_detail = status.get("positions_detail", {})
    if pos_detail:
        for market, info in pos_detail.items():
            emoji = "📈" if info.get("rate", 0) >= 0 else "📉"
            lines.append(
                f"  {emoji} {market} | 현재:{info.get('current',0):,}원 | "
                f"{info.get('rate',0):+.2f}% | {info.get('qty',0):.6f}개"
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
                f"승률:{round(len(wins)/len(profits)*100,1)}% | "
                f"평균:{round(sum(profits)/len(profits),2):+.2f}%"
            )
    await send_long(ctx, "\n".join(lines))


async def cmd_cbot_sell(ctx, market: str):
    if not market.startswith("KRW-"):
        market = f"KRW-{market.upper()}"
    valid = ["KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-SOL"]
    if market not in valid:
        await ctx.send(f"❌ 지원 코인: {', '.join(valid)}\n예) !c매도 BTC"); return

    update_state("cbot", pending_cmd={"type": "sell", "market": market})
    await ctx.send(f"📤 코인 매도 명령: **{market}**\n(다음 루프 ~5분 내 실행)")

    for _ in range(12):
        await asyncio.sleep(5)
        state  = read_state("cbot")
        result = state.get("cmd_result")
        if result:
            update_state("cbot", cmd_result=None)
            await ctx.send(f"✅ {result}"); return
    await ctx.send("⚠️ 응답 없음 — cbot.py 실행 중인지 확인하세요")


async def cmd_cbot_performance(ctx):
    rows = get_coin_performance(limit=20)
    if not rows:
        await ctx.send("🪙 코인봇 매매 이력 없음"); return
    profits = [r[0] for r in rows if r[0] is not None]
    wins    = [p for p in profits if p >= 0]
    w_rate  = round(len(wins)/len(profits)*100, 1) if profits else 0
    avg     = round(sum(profits)/len(profits), 2) if profits else 0
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
            names = ", ".join(f"{c}({name_map.get(c,c)})" for c in sector_codes[:8])
            lines.append(f"  📌 관련 종목: {names}")
    else:
        lines.append("❌ 현재 활성 업종 없음")

    if sector_updated:
        lines.append(f"\n⏰ 마지막 업종 체크: {sector_updated}")
        lines.append("💡 nbot이 매시 20분 자동 체크합니다")

    theme_codes = [c for c in watchlist if wl_source.get(c) == "hts_theme"]
    new_codes   = [c for c in watchlist if wl_source.get(c) == "hts_new"]

    if theme_codes:
        names = ", ".join(f"{c}({name_map.get(c,c)})" for c in theme_codes[:6])
        extra = f" 외 {len(theme_codes)-6}개" if len(theme_codes) > 6 else ""
        lines.append(f"\n🎯 테마 종목 ({len(theme_codes)}개): {names}{extra} (+5점)")
    if new_codes:
        names = ", ".join(f"{c}({name_map.get(c,c)})" for c in new_codes[:5])
        lines.append(f"🆕 신규추천 ({len(new_codes)}개): {names} (+7점)")

    await send_long(ctx, "\n".join(lines))


# ============================================================
# 키움 HTS 관심그룹 동기화
# ============================================================

def _get_kiwoom_token_sync() -> str:
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
        print(f"⚠️ 키움 토큰 발급 실패: {e}"); return ""


async def _fetch_kiwoom_watchlist_ws() -> list:
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
            ping_interval=None
        ) as ws:
            await ws.send(json.dumps({"trnm": "LOGIN", "token": token}))
            res = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
            if res.get("return_code") != 0:
                print(f"⚠️ 키움 로그인 실패"); return []
            print("✅ 키움 WebSocket 로그인 (관심그룹)")

            await ws.send(json.dumps({"trnm": "INTSLST"}))
            grp_list = []
            while True:
                try:
                    res = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
                    if res.get("trnm") == "PING":
                        await ws.send(json.dumps(res)); continue
                    if res.get("trnm") == "INTSLST":
                        grp_list = res.get("data", []); break
                except asyncio.TimeoutError: break

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

                is_sector = grp_name.startswith("업종")
                is_theme  = grp_name == "테마" or grp_name.startswith("테마")
                is_new    = grp_name.lower() in ("new", "신규추천", "신규")

                if not (is_sector or is_theme or is_new):
                    print(f"  ⏭️ [{grp_no}]{grp_name} 제외")
                    continue

                source = ("hts_sector" if is_sector
                          else "hts_theme" if is_theme
                          else "hts_new")

                await ws.send(json.dumps({
                    "trnm": "INTSTKL", "intstock_grp_no": grp_no,
                }))
                fetched = 0
                while True:
                    try:
                        res = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
                        if res.get("trnm") == "PING":
                            await ws.send(json.dumps(res)); continue
                        if res.get("return_code") != 0: break
                        for item in (res.get("data") or []):
                            if isinstance(item, dict):
                                raw  = item.get("stk_code", item.get("9001", ""))
                                name = item.get("stk_name", item.get("302", ""))
                            elif isinstance(item, list):
                                raw  = item[0] if item else ""
                                name = item[1] if len(item) > 1 else ""
                            else: continue
                            code = raw.lstrip("A") if raw.startswith("A") else raw
                            if code and code.isdigit() and code not in seen:
                                seen.add(code)
                                codes.append((code, name.strip(), source))
                                fetched += 1
                        if res.get("cont_yn") != "Y": break
                    except asyncio.TimeoutError: break

                label = "🏭업종" if is_sector else ("🎯테마" if is_theme else "🆕new")
                print(f"  {label} [{grp_no}]{grp_name}: +{fetched}개")

    except Exception as e:
        print(f"⚠️ 키움 WebSocket 관심그룹 오류: {e}")

    s = sum(1 for _, _, src in codes if src == "hts_sector")
    t = sum(1 for _, _, src in codes if src == "hts_theme")
    n = sum(1 for _, _, src in codes if src == "hts_new")
    print(f"✅ 키움 관심그룹 총 {len(codes)}개 (업종:{s} 테마:{t} new:{n})")
    return codes


def _sync_watchlist_to_state(codes: list) -> dict:
    if not codes:
        return {"added": 0, "removed": 0, "total": 0, "codes": []}

    all_codes   = {code: name   for code, name, _   in codes}
    all_sources = {code: source for code, _, source in codes}
    hts_codes   = list(all_codes.keys())
    expire_date = (datetime.datetime.now() + datetime.timedelta(days=30)).strftime("%Y-%m-%d")
    summary     = {"added": 0, "removed": 0, "total": len(hts_codes), "codes": hts_codes}

    for bot_name in ("nbot", "sbot"):
        state     = read_state(bot_name)
        watchlist = state.get("watchlist", [])
        wl_expire = state.get("watchlist_expire", {})
        wl_source = state.get("watchlist_source", {})

        old_hts = {c for c, src in wl_source.items() if src.startswith("hts_")}
        new_hts = set(hts_codes)

        for code in new_hts - old_hts:
            if code not in watchlist:
                watchlist.append(code)
            wl_expire[code] = expire_date
            wl_source[code] = all_sources.get(code, "hts_theme")
            summary["added"] += 1

        for code in new_hts & old_hts:
            wl_source[code] = all_sources.get(code, wl_source.get(code, "hts_theme"))

        for code in old_hts - new_hts:
            if code in watchlist:
                watchlist.remove(code)
            wl_expire.pop(code, None)
            wl_source.pop(code, None)
            summary["removed"] += 1

        code_name_map = state.get("last_status", {}).get("code_name_map", {})
        code_name_map.update(all_codes)

        state["watchlist"]        = watchlist
        state["watchlist_expire"] = wl_expire
        state["watchlist_source"] = wl_source
        state["hts_watchlist"]    = all_codes
        state["hts_updated_at"]   = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        write_state(state, bot_name)

    print(f"✅ HTS 동기화: +{summary['added']} -{summary['removed']} 총{summary['total']}개")
    return summary


async def cmd_watchlist_hts(ctx):
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

    msg  = f"📋 **키움 관심그룹 동기화 완료**\n총 {total}개 | ✅ 추가:{added} | ❌ 제거:{removed}\n\n"

    if sector_items:
        names = ", ".join(f"{c}({name_map.get(c,c)})" for c in sector_items[:6])
        extra = f" 외 {len(sector_items)-6}개" if len(sector_items) > 6 else ""
        msg  += f"🏭 **업종대표** ({len(sector_items)}개): {names}{extra}\n"
        msg  += "   → 강세 업종 감지 시 가점 +10점\n\n"
    if theme_items:
        names = ", ".join(f"{c}({name_map.get(c,c)})" for c in theme_items[:6])
        extra = f" 외 {len(theme_items)-6}개" if len(theme_items) > 6 else ""
        msg  += f"🎯 **테마대표** ({len(theme_items)}개): {names}{extra}\n"
        msg  += "   → 항상 풀 포함 + 가점 +5점\n\n"
    if new_items:
        names = ", ".join(f"{c}({name_map.get(c,c)})" for c in new_items[:5])
        msg  += f"🆕 **신규추천** ({len(new_items)}개): {names}\n"
        msg  += "   → 항상 풀 포함 + 가점 +7점\n\n"
    if manual_items:
        names = ", ".join(f"{c}({name_map.get(c,c)})" for c in manual_items)
        msg  += f"✋ **수동추가** ({len(manual_items)}개): {names}\n\n"

    msg += "💡 HTS 관심그룹 변경 → 09:00 / 11:00 / 14:00 자동 반영"
    await send_long(ctx, msg)


# ============================================================
# 브리핑
# ============================================================

def _translate_to_korean(text: str) -> str:
    if not text or text in ("정보 없음", "검색 결과 없음"):
        return "정보 없음"
    clean = text.replace("[요약]", "").replace("[Summary]", "").strip()
    korean_count = sum(1 for c in clean if "\uac00" <= c <= "\ud7a3")
    if len(clean) > 0 and korean_count / len(clean) > 0.2:
        return clean[:80]
    try:
        llm = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        res = llm.messages.create(
            model="claude-haiku-4-5-20251001", max_tokens=80,
            messages=[{"role": "user", "content":
                f"다음을 한국어 1줄(30자 이내)로 요약해줘. 숫자/퍼센트는 그대로:\n{clean[:300]}"}]
        )
        return res.content[0].text.strip()
    except Exception:
        return clean[:80]


def _build_briefing_msg() -> str:
    now    = now_kst()
    state  = read_state("nbot")
    status = state.get("last_status", {})
    cbot_state  = read_state("cbot")
    cbot_status = cbot_state.get("last_status", {})

    searches = [
        ("🇺🇸 미국장",   "US stock market nasdaq dow latest",  "global"),
        ("💱 환율",      "USD KRW exchange rate today",         "global"),
        ("🪙 코인",      "bitcoin ethereum price today",        "global"),
        ("🌤️ 날씨",     None,                                  "weather"),
        ("📈 코스피선물", "코스피 선물 오늘 전망",               "korea"),
    ]

    now_str = now.strftime("%m/%d %H:%M")
    msg  = f"🌅 **[영암9 모닝 브리핑] {now_str}**\n━━━━━━━━━━━━━━━━━━━━\n"

    for label, query, stype in searches:
        if stype == "weather":
            first = "\n" + ai._get_weather_region()
        elif stype == "global":
            result = ai._web_search_global(query)
            raw    = result.split("\n")[0].replace("- ", "")[:300] if result != "검색 결과 없음" else "정보 없음"
            first  = _translate_to_korean(raw)
        else:
            result = ai._web_search_korea(query)
            first  = result.split("\n")[0].replace("- ", "")[:80] if result != "검색 결과 없음" else "정보 없음"
        msg += f"{label}: {first}\n"

    active = state.get("active_sectors", [])
    if active:
        msg += f"🏭 강세 업종: {' | '.join(active)}\n"

    msg += "━━━━━━━━━━━━━━━━━━━━\n"
    paused_str = "⏸️" if state.get("paused") else "▶️"
    msg += f"📈 단타봇: {paused_str} | 기준:{state.get('score_enter', 55)}점"
    if status:
        msg += f" | 주문가능:{status.get('psbl_cash', 0):,}원"
    msg += f"\n🪙 코인봇: {'⏸️' if cbot_state.get('paused') else '▶️'} | KRW:{cbot_status.get('krw', 0):,}원\n"
    msg += "📌 오늘도 좋은 장 되세요! 💪"
    return msg


def _build_evening_briefing_msg() -> str:
    now    = now_kst()
    state  = read_state("nbot")
    status = state.get("last_status", {})
    cbot_state  = read_state("cbot")
    cbot_status = cbot_state.get("last_status", {})

    searches = [
        ("📈 코스피/코스닥", "코스피 코스닥 오늘 마감 시황", "korea"),
        ("🏆 오늘의 주도주", "오늘 급등 테마 주도주",        "korea"),
        ("🪙 코인시황",     "bitcoin ethereum price today",   "global"),
        ("💱 환율",         "USD KRW exchange rate today",    "global"),
        ("📰 내일 전망",    "코스피 내일 전망 시황",          "korea"),
    ]

    now_str = now.strftime("%m/%d %H:%M")
    msg  = f"🌆 **[영암9 저녁 브리핑] {now_str}**\n━━━━━━━━━━━━━━━━━━━━\n"

    for label, query, stype in searches:
        if stype == "global":
            result = ai._web_search_global(query)
            raw    = result.split("\n")[0].replace("- ", "")[:300] if result != "검색 결과 없음" else "정보 없음"
            first  = _translate_to_korean(raw)
        else:
            result = ai._web_search_korea(query)
            first  = result.split("\n")[0].replace("- ", "")[:80] if result != "검색 결과 없음" else "정보 없음"
        msg += f"{label}: {first}\n"

    msg += "━━━━━━━━━━━━━━━━━━━━\n"
    msg += f"📈 단타봇 손익: {status.get('total_profit', 0):+,}원\n"
    msg += f"🪙 코인봇 손익: {cbot_status.get('total_profit', 0):+,}원\n"
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

**🪙 코인봇:**
  `!c상태`          — 코인봇 현황
  `!c매도 BTC`      — 즉시 매도
  `!c정지` / `!c시작` — 중단/재개
  `!c성과`          — 코인봇 매매 성과

**🏭 업종/테마:**
  `!테마`           — 당일 강세 업종/테마 현황
  `!관심HTS`        — 키움 HTS 관심그룹 즉시 동기화

**🌐 공통:**
  `!전체상태`        — 모든 봇 현황
  `!브리핑`         — 즉시 모닝 브리핑
  `!저녁브리핑`     — 즉시 저녁 브리핑
  `!성과`           — 오늘 손익 확인
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
    ch = bot.get_channel(CHANNEL_ID)
    if ch:
        await ch.send(
            f"🤖 **영암9 AI 비서 (키키) 온라인**\n"
            f"⏰ {now_kst().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"📈 단타봇 | 📊 스윙봇 | 🪙 코인봇 연동\n"
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
        await execute_command(ctx, content)
    elif content:
        async with message.channel.typing():
            state = read_state("nbot")
            loop  = asyncio.get_event_loop()
            reply = await loop.run_in_executor(None, ai.interpret, content, state)
            if reply.startswith("CMD:"):
                cmd = reply[4:].strip()
                await message.channel.send(f"🤖 키키: `{cmd}` 실행할게요!")
                await execute_command(ctx, cmd)
            else:
                await message.channel.send(f"🤖 {reply}")


# ============================================================
# 백그라운드 태스크
# ============================================================

async def hts_sync_task():
    """키움 HTS 관심그룹 자동 동기화 (09:00 / 11:00 / 14:00)."""
    last_sync = {}
    SYNC_TIMES = ["0900", "1100", "1400"]

    while True:
        await asyncio.sleep(30)
        try:
            now      = now_kst()
            today    = now.strftime("%Y-%m-%d")
            now_hhmm = now.strftime("%H%M")

            if now.weekday() >= 5:
                continue

            ch = bot.get_channel(CHANNEL_ID)
            if not ch:
                continue

            for sync_t in SYNC_TIMES:
                key   = f"{today}_{sync_t}"
                end_t = str(int(sync_t) + 5).zfill(4)
                if key in last_sync or not (sync_t <= now_hhmm <= end_t):
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
                    msg  = f"📋 **HTS 관심그룹 동기화** [{now_str}]\n"
                    msg += f"총 {total}개 | ✅ 추가:{added} | ❌ 제거:{removed}"
                    await ch.send(msg)
                else:
                    print(f"📋 HTS 관심그룹 변화 없음 ({total}개 유지)")

            for k in [k for k in last_sync if not k.startswith(today)]:
                del last_sync[k]

        except Exception as e:
            print(f"⚠️ HTS 동기화 오류: {e}")


async def auto_briefing():
    """평일 08:00 모닝 / 20:00 저녁 브리핑."""
    last_morning_date = None
    last_evening_date = None

    while True:
        await asyncio.sleep(30)
        try:
            now      = now_kst()
            today    = now.strftime("%Y-%m-%d")
            now_hhmm = now.strftime("%H%M")

            if now.weekday() >= 5:
                continue

            ch = bot.get_channel(CHANNEL_ID)
            if not ch:
                continue

            # 수동 추가 관심종목 만료 체크
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
                    update_state(bot_name, watchlist=watchlist, watchlist_expire=wl_expire)
                    await ch.send(f"🗑️ 관심종목 만료 제거: {', '.join(expired)}")

            # 08:00 모닝 브리핑
            if "0800" <= now_hhmm <= "0805" and last_morning_date != today:
                last_morning_date = today
                await ch.send("🌅 **모닝 브리핑 준비 중...**")
                loop = asyncio.get_event_loop()
                msg  = await loop.run_in_executor(None, _build_briefing_msg)
                await send_long(ch, msg)
                print(f"✅ 모닝 브리핑 전송 {today}")

            # 20:00 저녁 브리핑
            if "2000" <= now_hhmm <= "2005" and last_evening_date != today:
                last_evening_date = today
                await ch.send("🌆 **저녁 브리핑 준비 중...**")
                loop = asyncio.get_event_loop()
                msg  = await loop.run_in_executor(None, _build_evening_briefing_msg)
                await send_long(ch, msg)
                print(f"✅ 저녁 브리핑 전송 {today}")

        except Exception as e:
            print(f"⚠️ 브리핑 오류: {e}")


async def status_listener():
    """10초마다 손익 변동 감지 — 단타봇 + 코인봇."""
    last_stock_profit = None
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
                if last_stock_profit is not None and abs(profit - last_stock_profit) > 5000:
                    diff = profit - last_stock_profit
                    await ch.send(
                        f"💹 [단타] 손익 변동: {last_stock_profit:+,}원 → {profit:+,}원 ({diff:+,}원)"
                    )
                last_stock_profit = profit

            # 코인봇
            cstate  = read_state("cbot")
            cstatus = cstate.get("last_status")
            if cstatus:
                cprofit = cstatus.get("total_profit", 0)
                if last_coin_profit is not None and abs(cprofit - last_coin_profit) > 3000:
                    diff = cprofit - last_coin_profit
                    await ch.send(
                        f"🪙 [코인] 손익 변동: {last_coin_profit:+,}원 → {cprofit:+,}원 ({diff:+,}원)"
                    )
                last_coin_profit = cprofit

        except Exception:
            pass


# ============================================================
# 진입점
# ============================================================

if __name__ == "__main__":
    if not BOT_TOKEN:
        print("❌ DISCORD_BOT_TOKEN 환경변수가 없어요!"); exit(1)
    if not CHANNEL_ID:
        print("❌ DISCORD_CHANNEL_ID 환경변수가 없어요!"); exit(1)

    print("🤖 영암9 AI 비서 (키키) 시작...")
    print("📈 단타봇 | 📊 스윙봇 | 🪙 코인봇 연동")
    print("🏭 HTS 관심그룹 자동 동기화: 09:00 / 11:00 / 14:00")
    print("🌅 모닝 브리핑: 08:00 | 🌆 저녁 브리핑: 20:00")
    bot.run(BOT_TOKEN)
