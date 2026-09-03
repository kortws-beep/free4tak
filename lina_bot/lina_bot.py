import asyncio
import os
import subprocess
import discord
import aiohttp
import datetime
import sqlite3
import re
import urllib.parse
import json
import quant_analyzer
import yfinance as yf
from bs4 import BeautifulSoup
from dotenv import load_dotenv, find_dotenv
from discord.ext import tasks
from swing_analyzer import get_swing_picks
from trend_analyzer import get_trend_picks
from swing_master import get_master_report
import warnings

# 무적 비동기 크롤러 엔진
from curl_cffi import requests
from curl_cffi.requests import AsyncSession

load_dotenv(find_dotenv())

# .env 로드 세팅
base_dir = os.path.dirname(os.path.abspath(__file__))
env_path = os.path.join(base_dir, '.env')
load_dotenv(dotenv_path=env_path)

# ★ 2026-07-02: intelligence/market_concentration.py를 모듈로 가져오기 위한 경로 추가
import sys as _sys
_INTEL_DIR = os.path.join(os.path.dirname(base_dir), "intelligence")
if _INTEL_DIR not in _sys.path:
    _sys.path.insert(0, _INTEL_DIR)

# ★ 2026-07-09: AI 모멘텀 스캐너에서 core/consensus.py(컨센서스 보강)를
#   가져오기 위한 경로 추가
_CORE_DIR = os.path.join(os.path.dirname(base_dir), "core")
if _CORE_DIR not in _sys.path:
    _sys.path.insert(0, _CORE_DIR)

# 환경 변수 및 모델 세팅
DISCORD_TOKEN = os.getenv("DISCORD_BOT_TOKEN_N")
OLLAMA_API_URL = os.getenv("OLLAMA_API_URL", "http://127.0.0.1:11434/api/chat")
MODEL_NAME = os.getenv("MODEL_NAME", "gemma4:e4b")

# 🚨 대한민국 표준시(KST) 타임존
KST = datetime.timezone(datetime.timedelta(hours=9))

# ★ 2026-07-17 추가: 리나의 스케줄 리포트들이 주말/공휴일 체크가 아예
#   없어서, 공휴일에도 전일 마감 스냅샷을 실시간 데이터로 오인해 정상
#   리포트를 그대로 내보내던 문제 발견(제헌절 사고, 사용자 지적) —
#   sbot/sbo2와 동일하게 하루 1회만 KIS 휴장일 API 조회하는 캐시 헬퍼.
_TRADING_DAY_CACHE = {"date": "", "is_open": True}

def _is_trading_day() -> bool:
    kst_now = datetime.datetime.now(KST)
    if kst_now.weekday() >= 5:   # 토(5)/일(6)
        return False
    today = kst_now.strftime("%Y-%m-%d")
    if _TRADING_DAY_CACHE["date"] != today:
        # ★ 2026-08-17: is_market_open()이 None(API 실패/판단불가)이면 그날
        #   캐시하지 않고 다음 호출 때 재시도 — sbot/sbo2와 동일 사유
        #   (08-17 광복절 대체공휴일에 sbot에서 실제로 발생한 사고, 예방
        #   차원에서 리나도 동일 적용). 실패해도 무조건 True로 캐시하던
        #   기존 동작은 하필 그날 첫 체크가 실패하면 하루 종일 잘못된
        #   판단이 굳어버리는 문제가 있었음.
        try:
            from kis_api import KisAPI
            _open = KisAPI().is_market_open()
        except Exception as e:
            print(f"⚠️ [리나] 휴장일 체크 오류: {e}")
            _open = None
        if _open is None:
            print("⚠️ [리나] 휴장일 판단 실패 — 다음 호출 재시도")
        else:
            _TRADING_DAY_CACHE["is_open"] = _open
            _TRADING_DAY_CACHE["date"]    = today
    return _TRADING_DAY_CACHE["is_open"]

# 💡 리나의 텔레그램 중복 방지용 단기 기억 장치 (마지막 처리한 ID 기억)
LAST_TELEGRAM_ID = 0

# 🚨 리포트 전송할 디스코드 채널 ID 및 DB 경로
REPORT_CHANNEL_ID = 1508487747508240525 
# ★ 수정 (2026-06-23): base_dir(lina_bot/)가 아니라 stock_bot 루트 기준으로 변경.
#   기존 경로(lina_bot/intelligence/telegram_events.db)는 죽은 옛 사본(6/12 이후 갱신 안됨)을
#   가리키고 있어, 30분 텔레그램 브리핑이 항상 "새 속보 없음"으로 나오던 근본 원인.
DB_PATH_TELEGRAM = os.path.join(os.path.dirname(base_dir), "intelligence", "telegram_events.db")
DB_PATH_CONCENTRATION = os.path.join(os.path.dirname(base_dir), "intelligence", "market_concentration.db")
DB_PATH_FINANCE = os.path.join(base_dir, 'finance.db')
DB_PATH_MAPPING = os.path.join(base_dir, 'us_kr_mapping.db')  # 💡 신규 맵핑 DB 경로
DB_PATH_THEME_FINANCE = os.path.join(base_dir, 'kr_theme_finance.db')
SCOPES = ['https://www.googleapis.com/auth/calendar'] 

SYSTEM_PROMPT = (
    "너는 디스코드 서버의 친절하고 활기찬 AI 비서 '리나'야. "
    "너는 꼬리 줄 달린 키키의 동생이야. 그래서 너도 정령이지. "
    "오직 100% 순수한 '한국어'로만 답변해야 해. "
    "사용자들에게 항상 친근하고 귀여운 말투(~했어, ~야 등 반말과 존댓말 사이의 친근함)를 사용해줘. "
    "🚨 답변 룰: "
    "1. 대장의 질문에 대해 **자기소개나 인사를 먼저 하지 마.** "
    "2. 질문에 대한 답변만 간결하고 명확하게 출력해. "
    "3. 데이터 내용이 없다면 '데이터가 없어'라고 솔직하게 말해. "
    "4. 파이썬이 제공한 데이터에 없는 내용은 절대 지어내지 마."
)

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

chat_memory = {}
MAX_MEMORY = 10

# ===================================================
# 🛡️ 안전 전송기
# ===================================================
async def send_safe_message(target, text, reply_to=None):
    # ★ 2026-06-29 수정: 기존엔 "한 줄(line)이 1900자를 넘지 않는다"는
    #   가정 하에서만 안전하게 분할됐음. AI 응답에 줄바꿈 없는 긴 문단이
    #   하나라도 있으면 그 줄이 그대로 청크에 들어가 1900자를 훌쩍
    #   넘긴 채 전송 시도 → 디스코드 길이제한 초과로 400 Bad Request
    #   ("Must be 4000 or fewer in length") 에러가 발생하던 버그.
    #   이제 1900자를 넘는 단일 줄은 강제로 잘라서 여러 청크로 나눔.
    CHUNK_LIMIT = 1900

    def _split_long_line(line: str) -> list:
        """단일 줄이 CHUNK_LIMIT을 넘으면 문자 단위로 강제 분할.
        분할 크기는 CHUNK_LIMIT-1로 잡아 이후 개행문자(\\n)가 붙어도
        CHUNK_LIMIT을 넘지 않도록 함."""
        if len(line) <= CHUNK_LIMIT:
            return [line]
        step = CHUNK_LIMIT - 1
        return [line[i:i + step] for i in range(0, len(line), step)]

    if len(text) <= CHUNK_LIMIT:
        if reply_to: await reply_to.reply(text)
        else: await target.send(text)
        return

    lines = text.split('\n')
    chunks = []
    chunk = ""
    for line in lines:
        # 줄 자체가 너무 길면 먼저 강제 분할
        sub_lines = _split_long_line(line)
        for sub in sub_lines:
            if len(chunk) + len(sub) + 1 > CHUNK_LIMIT:
                if chunk.strip():
                    chunks.append(chunk)
                chunk = sub + '\n'
            else:
                chunk += sub + '\n'
    if chunk.strip():
        chunks.append(chunk)

    for c in chunks:
        if reply_to:
            await reply_to.reply(c)
            reply_to = None
        else:
            await target.send(c)

# ==========================================
# [데이터베이스 / 가계부 / 맵핑 / 캘린더]
# ==========================================
def init_finance_db():
    conn = sqlite3.connect(DB_PATH_FINANCE)
    cursor = conn.cursor()
    cursor.execute("CREATE TABLE IF NOT EXISTS finance_ledger (id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT NOT NULL, type TEXT NOT NULL, item TEXT NOT NULL, amount INTEGER NOT NULL)")
    conn.commit()
    conn.close()

def init_mapping_db():
    """미국장-한국장 수혜주 맵핑 DB 초기화 함수"""
    conn = sqlite3.connect(DB_PATH_MAPPING)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS us_kr_mapping (
            id INTEGER PRIMARY KEY AUTOINCREMENT, 
            us_ticker TEXT NOT NULL, 
            us_name TEXT NOT NULL, 
            kr_name TEXT NOT NULL, 
            reason TEXT, 
            is_static INTEGER DEFAULT 1, 
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("SELECT COUNT(*) FROM us_kr_mapping")
    if cursor.fetchone()[0] == 0:
        samples = [
            ("INTC", "인텔", "인텍플러스", "인텔 패키징 장비 주요 공급사", 1),
            ("INTC", "인텔", "가온칩스", "인텔 파운드리 디자인솔루션 파트너", 1),
            ("INTC", "인텔", "고영", "인텔 어드밴스드 패키징 검사장비 공급", 1),
            ("NVDA", "엔비디아", "SK하이닉스", "HBM 주요 공급사", 1),
            ("NVDA", "엔비디아", "한미반도체", "HBM 필수 장비 TC본더 독점력", 1)
        ]
        cursor.executemany("INSERT INTO us_kr_mapping (us_ticker, us_name, kr_name, reason, is_static) VALUES (?, ?, ?, ?, ?)", samples)
        print("✅ [시스템] 미국장-한국장 초기 맵핑 DB 세팅 완료!")
    conn.commit()
    conn.close()

def get_kr_stocks_by_ticker(us_ticker):
    """티커로 맵핑된 한국 주식 가져오기"""
    conn = sqlite3.connect(DB_PATH_MAPPING)
    cursor = conn.cursor()
    cursor.execute("SELECT kr_name, reason, is_static FROM us_kr_mapping WHERE us_ticker = ?", (us_ticker,))
    rows = cursor.fetchall()
    conn.close()
    return [{"kr_name": r[0], "reason": r[1], "is_static": r[2]} for r in rows]

def add_finance_record(r_type, item, amount):
    conn = sqlite3.connect(DB_PATH_FINANCE)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO finance_ledger (date, type, item, amount) VALUES (?, ?, ?, ?)", 
                   (datetime.datetime.now().strftime("%Y-%m-%d"), r_type, item, amount))
    conn.commit()
    conn.close()
    return f"장부에 [{r_type}] {item} {amount:,}원 기록 완료!"

def get_monthly_report():
    conn = sqlite3.connect(DB_PATH_FINANCE)
    cursor = conn.cursor()
    cursor.execute("SELECT type, amount FROM finance_ledger WHERE date LIKE ?", (f"{datetime.datetime.now().strftime('%Y-%m')}%",))
    rows = cursor.fetchall()
    conn.close()
    if not rows: return "이번 달 장부가 비어있어."
    inc = sum(r[1] for r in rows if r[0] == "입금")
    exp = sum(r[1] for r in rows if r[0] == "출금")
    return f"📝 [이번 달 통계]\n- 총 입금: {inc:,}원\n- 총 출금: {exp:,}원\n- 잔액: {inc - exp:,}원"

def fetch_calendar_events():
    try:
        from googleapiclient.discovery import build
        from google.oauth2.credentials import Credentials
        
        token_path = os.path.join(base_dir, 'token.json')
        if not os.path.exists(token_path): return "구글 인증 토큰이 없어!"
        
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)
        service = build('calendar', 'v3', credentials=creds)
        
        kst_now = datetime.datetime.utcnow() + datetime.timedelta(hours=9)
        start_of_day = kst_now.replace(hour=0, minute=0, second=0, microsecond=0)
        time_min = (start_of_day - datetime.timedelta(hours=9)).isoformat() + 'Z'
        
        events_result = service.events().list(
            calendarId='primary', 
            timeMin=time_min, 
            maxResults=10, 
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        
        events = events_result.get('items', [])
        if not events: return "등록된 일정이 없어!"
            
        return "\n".join([f"- [{e['start'].get('dateTime', e['start'].get('date'))[:10]}] {e['summary']}" for e in events])
    except Exception as e: 
        return f"일정 호출 실패: {str(e)}"

def add_google_calendar_event(summary, target_date):
    try:
        from googleapiclient.discovery import build
        from google.oauth2.credentials import Credentials
        
        token_path = os.path.join(base_dir, 'token.json')
        if not os.path.exists(token_path): return "토큰 파일이 없어서 캘린더에 접근할 수 없어!"
            
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)
        service = build('calendar', 'v3', credentials=creds)
        
        event_body = {
            'summary': summary,
            'start': {'date': target_date, 'timeZone': 'Asia/Seoul'},
            'end': {'date': target_date, 'timeZone': 'Asia/Seoul'},
        }
        
        service.events().insert(calendarId='primary', body=event_body).execute()
        return f"✅ '{target_date}'에 [{summary}] 일정 추가 완료!"
    except Exception as e:
        return f"❌ '{target_date}' 일정 추가 실패: {str(e)}"

# ===================================================
# 🌤️ [기상청 / MBN골드 / 텔레그램 / 수급 타겟팅]
# ===================================================
def get_weather_kma_pure() -> str:
    try:
        auth_key = os.getenv("KMA_API_KEY", "")
        if not auth_key: return "맑음 / 24°C / 습도:50% (기상청 키 미설정 폴백)"
        target = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=9) - datetime.timedelta(minutes=45)
        url = "https://apihub.kma.go.kr/api/typ02/openApi/VilageFcstInfoService_2.0/getUltraSrtNcst"
        params = {"pageNo": "1", "numOfRows": "1000", "dataType": "JSON", "base_date": target.strftime("%Y%m%d"), "base_time": target.strftime("%H00"), "nx": 57, "ny": 74, "authKey": auth_key}
        import requests as sync_req
        res = sync_req.get(url, params=params, timeout=5).json()
        items = res.get("response", {}).get("body", {}).get("items", {}).get("item", [])
        data = {item["category"]: item["obsrValue"] for item in items}
        pty = {"0": "없음", "1": "비", "2": "비/눈", "3": "눈", "4": "소나기"}.get(data.get("PTY", "0"), "없음")
        return f"{'주룩주룩 비소식' if pty != '없음' else '맑고 쾌청함'} / 현재기온: {data.get('T1H', '?')}°C / 습도: {data.get('REH', '?')}%"
    except Exception as e: return f"기상청 수신 지연 중 ({e})"

async def fetch_mbngold_async(service_id="10001", limit=5):
    """MBN골드 로그인 후 뉴스 크롤링 (새 URL 구조)"""
    import requests as _req
    from dotenv import load_dotenv as _load
    _load(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

    base_url = "https://www.mbngold.com"
    headers = {"User-Agent": "Mozilla/5.0", "Referer": f"{base_url}/mg/mypage/login.php"}
    sess = _req.Session()

    # 로그인
    try:
        sess.post(f"{base_url}/mg/mypage/login_action.php", headers=headers, data={
            "mode": "login",
            "rURL": f"{base_url}/mg/news/",
            "mID":  os.getenv("MBNGOLD_ID", ""),
            "mPWD": os.getenv("MBNGOLD_PW", ""),
        }, timeout=10)
    except Exception as e:
        print(f"❌ MBN골드 로그인 에러: {e}")
        return "텅 비어 있어. (MBN골드 로그인 실패)"

    # 목록 페이지
    try:
        list_url = f"{base_url}/mg/news/index.php?news_service_id={service_id}"
        res = sess.get(list_url, headers=headers, timeout=10)
        soup = BeautifulSoup(res.content.decode('utf-8', errors='ignore'), 'html.parser')

        links = []
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "view.php" in href and "news_no=MM" in href:
                m = re.search(r"news_no=(MM\d+)", href)
                if m:
                    news_no = m.group(1)
                    if news_no not in [l[0] for l in links]:
                        title = a.get_text(strip=True)
                        if title:
                            links.append((news_no, title))
                            if len(links) >= limit: break

        if not links:
            return "텅 비어 있어. (MBN골드 사이트 지연 또는 오늘자 업데이트 없음)"

        search_results = []
        for news_no, title in links:
            full_url = f"{base_url}/mg/news/view.php?news_no={news_no}&news_service_id={service_id}&page=1"
            try:
                sub_res = sess.get(full_url, headers=headers, timeout=5)
                sub_soup = BeautifulSoup(sub_res.content.decode('utf-8', errors='ignore'), "html.parser")

                if service_id == "10001":
                    content = sub_soup.get_text(separator=" ")
                    clean_content = re.sub(r'\s+', ' ', content).strip()
                    snippet = clean_content[:150] if len(clean_content) > 150 else clean_content
                    search_results.append(f"📰 [기사] {title}\n    └ [내용] {snippet}...")
                else:
                    content = sub_soup.get_text(separator="\n")
                    lines = [line.strip() for line in content.split("\n") if len(line.strip()) > 1]
                    found = False
                    for idx, line in enumerate(lines):
                        if "손절" in line and ("매수" in line or "목표" in line or "원" in line):
                            target_block = []
                            if idx - 1 >= 0: target_block.append(f"📌 {lines[idx-1]}")
                            target_block.append(line)
                            if idx + 1 < len(lines): target_block.append(f"  [사유]: {lines[idx+1]}")
                            search_results.append("\n".join(target_block))
                            found = True
                            break
                    if not found:
                        search_results.append(f"📌 [생쇼 등록됨] {title} (게시글 내 매수가 양식 다름)")
            except Exception as e:
                print(f"상세 페이지 에러: {e}")

        if search_results:
            return "\n\n".join(search_results)
        return "텅 비어 있어. (MBN골드 사이트 지연 또는 오늘자 업데이트 없음)"

    except Exception as e:
        print(f"❌ MBN골드 접속 에러: {e}")
        
    return "텅 비어 있어. (MBN골드 사이트 지연 또는 오늘자 업데이트 없음)"


async def fetch_mbn_strategy(cutoff_hour: int = 8, cutoff_minute: int = 50) -> str:
    """
    MBN골드 투자전략 페이지(/mg/strategy/)에서 당일 올라온 전략/시황 글을 수집.
    (★ 2026-07-01 신규 — 매시간 텔레그램 테마 요약 제거 후 대체)
    수집 기준: 당일 07:30 ~ cutoff(기본 08:50) 사이 글만
    """
    import requests as _req
    from bs4 import BeautifulSoup as _BS
    from dotenv import load_dotenv as _load
    _load(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

    base_url = "https://www.mbngold.com"
    headers  = {"User-Agent": "Mozilla/5.0", "Referer": f"{base_url}/mg/mypage/login.php"}
    sess     = _req.Session()
    try:
        sess.post(f"{base_url}/mg/mypage/login_action.php", headers=headers, data={
            "mode": "login", "rURL": f"{base_url}/mg/news/",
            "mID":  os.getenv("MBNGOLD_ID", ""),
            "mPWD": os.getenv("MBNGOLD_PW", ""),
        }, timeout=10)
    except Exception as e:
        print(f"❌ MBN골드 전략 로그인 에러: {e}"); return ""

    try:
        res  = sess.get(f"{base_url}/mg/strategy/", headers=headers, timeout=10)
        soup = _BS(res.content.decode("utf-8", errors="ignore"), "html.parser")
    except Exception as e:
        print(f"❌ MBN골드 전략 페이지 에러: {e}"); return ""

    today     = datetime.datetime.now(KST).strftime("%Y-%m-%d")
    start_hm  = "07:30"
    cutoff_hm = f"{cutoff_hour:02d}:{cutoff_minute:02d}"

    items = []
    for card in soup.find_all("article", class_="istrat_hero_card"):
        body = card.find("div", class_="istrat_hero_body")
        if not body: continue
        time_tag = body.find("time", class_="istrat_hero_date")
        if not time_tag: continue
        dt_str = time_tag.get_text(strip=True)
        if not dt_str.startswith(today): continue
        hm = dt_str[11:16]
        if not (start_hm <= hm <= cutoff_hm): continue

        manager = ""
        meta = body.find("div", class_="istrat_hero_meta")
        if meta:
            texts = [t.strip() for t in meta.stripped_strings if t.strip()]
            manager = texts[0] if texts else ""

        parts = [p.strip() for p in body.get_text(separator="|", strip=True).split("|") if p.strip()]
        title = parts[-1] if parts else ""

        a_tag = card.find("a", href=True)
        link  = f"{base_url}/mg/strategy/{a_tag['href']}" if a_tag else ""
        items.append({"time": hm, "manager": manager, "title": title, "link": link})

    if not items: return ""
    items.sort(key=lambda x: x["time"])

    # ── 본문 요약 (★ 2026-07-02 추가) ────────────────────────
    #   기존엔 제목+링크만 보내서 로그인 없이는 실제 내용을 알 수 없었음.
    #   각 글의 상세페이지(mhj_pd_view_content)를 열어 본문을 가져오고
    #   LLM으로 A4 1페이지 분량 요약. HTTP+LLM 호출은 블로킹이라
    #   디스코드 이벤트루프를 몇 분씩 막지 않도록 스레드로 분리.
    summaries = await asyncio.to_thread(_fetch_and_summarize_bodies, sess, headers, items)

    lines = []
    for x, summary in zip(items, summaries):
        block = f"📊 [{x['time']}] **{x['manager']}** — {x['title']}\n   🔗 {x['link']}"
        if summary:
            block += f"\n\n{summary}"
        lines.append(block)
    return "\n\n─────────────\n\n".join(lines)


def _fetch_and_summarize_bodies(sess, headers, items: list) -> list:
    """전략 글 상세페이지 본문을 가져와 LLM으로 요약. 동기 함수 — to_thread로 실행."""
    results = []
    for x in items:
        text = ""
        try:
            r = sess.get(x["link"], headers=headers, timeout=10)
            soup = BeautifulSoup(r.content.decode("utf-8", errors="ignore"), "html.parser")
            body = soup.find("div", class_="mhj_pd_view_content")
            text = body.get_text("\n", strip=True) if body else ""
        except Exception as e:
            print(f"⚠️ MBN 본문 조회 오류 ({x.get('title','')}): {e}")
        results.append(_summarize_report_body(text) if text else "")
    return results


def _call_llm(prompt: str, max_tokens: int = 1200, force_claude: bool = False) -> str:
    """로컬 ollama 우선 시도 → 실패 시 Claude API로 폴백 (2026-07-02).
    ★ 2026-07-02: _summarize_report_body 전용이던 이 호출부를 공용 헬퍼로
    추출 — 시장 종합 브리핑(_build_market_context_summary) 등 다른 곳에서도
    같은 폴백 로직을 재사용하기 위함.
    ★ 2026-07-07: force_claude 추가 — 로컬 ollama(qwen2.5:14b)가 프롬프트에
    "보조 참고자료"로만 쓰라고 명시해도, 텔레그램 뉴스 블록이 구조화된
    쏠림 데이터보다 훨씬 크면 그쪽으로 관심이 쏠려 지시를 무시하는 문제가
    있었음(시장 쏠림 브리핑이 그냥 뉴스 나열로 나온 사고). 지시 준수가
    중요한 저빈도 호출은 로컬을 건너뛰고 바로 Claude로 보낸다."""
    ollama_model = os.getenv("OLLAMA_MODEL", "qwen2.5:14b")
    ollama_url   = os.getenv("OLLAMA_URL", "http://localhost:11434")
    if not force_claude:
        try:
            import openai as _openai
            client = _openai.OpenAI(base_url=f"{ollama_url}/v1", api_key="ollama", timeout=120)
            res = client.chat.completions.create(
                model=ollama_model, max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
            result = res.choices[0].message.content.strip()
            # ★ 2026-08-04: 로컬 LLM이 가끔 요약 도중 문단 단위로 중국어로
            #   새는 현상 발견(사용자 지적 — MBN 08:50 리포트 일부 문단이
            #   중국어로 나옴). 한국어 본문엔 한자가 거의 안 나오므로,
            #   한자(CJK 통합 한자) 개수가 일정 수준 넘으면 오염된 출력으로
            #   보고 Claude로 재시도한다.
            han_count = sum(1 for ch in result if '一' <= ch <= '鿿')
            if han_count <= 20:
                return result
            print(f"⚠️ 로컬 LLM 출력에 한자/중국어 혼입 감지({han_count}자) → Claude로 재시도")
        except Exception as e:
            print(f"⚠️ 로컬 LLM 호출 실패({e}) → Claude로 폴백")

    try:
        import anthropic as _ant
        client = _ant.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        # ★ 2026-09-03: 리나는 봇들의 컨트롤타워라 하이쿠→소넷5로 격상
        #   (사용자 요청 — "경직된 느낌", sbot/cbot 고빈도 스코어링은
        #   비용 대비 하이쿠가 적정이라 그대로 둠).
        res = client.messages.create(
            model=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5"),
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        # ★ 2026-09-03: 소넷5 응답의 content[0]이 ThinkingBlock일 수 있어
        #   .content[0].text 직접 접근 대신 공용 헬퍼 사용(자세한 배경은
        #   common_utils.extract_claude_text 참고).
        from common_utils import extract_claude_text
        return extract_claude_text(res)
    except Exception as e:
        print(f"⚠️ Claude 호출도 실패({e})")
        return ""


def _summarize_report_body(text: str) -> str:
    """투자전략 본문을 A4 1페이지 분량으로 요약."""
    # ★ 2026-07-02: 원문엔 날짜가 없거나 모호한 경우가 있어, 로컬 LLM이
    #   요약 제목에 "오늘의 시황 요약(2023년 X월)" 식으로 엉뚱한 연도를
    #   지어내는 환각이 있었음(내용 자체는 맞는데 헤더 날짜만 틀림).
    #   실제 오늘 날짜를 프롬프트에 명시하고 원문의 다른 날짜는 무시하도록 지시.
    today_str = datetime.datetime.now(KST).strftime("%Y년 %m월 %d일")
    prompt = (
        f"오늘은 {today_str}이다. 다음은 오늘자 증권사 전문가 투자전략/시황 "
        "리포트 원문이다. 핵심 내용을 놓치지 않으면서 A4 한 페이지 분량"
        "(1000~1500자)으로 한국어로 요약해라. 숫자·종목명·원인-결과 관계는 "
        "유지하고 불필요한 수식어만 줄여라. 요약에 날짜를 표기해야 한다면 "
        f"반드시 {today_str}만 써라 — 원문 안에 다른 날짜가 있어도 그건 "
        "무시하고 절대 지어내지 마라. 반드시 한국어로만 작성해라 — 원문에 "
        "중국어/영어가 섞여 있어도 요약은 전부 한국어로만 쓰고 절대 중국어를 "
        "섞지 마라.\n\n"
        f"[원문]\n{text[:4000]}"
    )
    result = _call_llm(prompt, max_tokens=1200)
    if result:
        return result
    print("⚠️ 요약 실패 — 본문 일부만 전달")
    return text[:800] + ("..." if len(text) > 800 else "")


# ============================================================
# 시장 쏠림 지수 — 종합 브리핑 (★ 2026-07-02 신규, 관찰 전용 Phase 1)
# ============================================================
def _build_market_context_summary() -> str:
    """
    intelligence/market_concentration.py의 최신 쏠림지수 스냅샷 +
    최근 텔레그램 이벤트 + (있으면) 오늘 MBN 투자전략 요약을 모아
    "오늘 시장 종합 코멘트" 한 문단을 생성한다.

    ★ 이 단계는 관찰 전용이다 — sbot/sbo2 스코어링에는 연결하지 않는다.
    코멘트 품질을 며칠 지켜본 뒤에 점수 보너스로 연결할지 결정한다.
    """
    try:
        from market_concentration import get_latest_snapshot, get_recent_summaries
    except Exception as e:
        print(f"⚠️ market_concentration 모듈 로드 실패: {e}")
        return ""

    snapshot = get_latest_snapshot()
    if not snapshot:
        return ""

    # ★ 2026-07-02: cron(market_concentration.py, 정각/30분 실행)과 이
    #   스케줄러(1분 주기 체크, 봇 재시작 시점 기준이라 정각과 안 맞음)
    #   사이에 타이밍 경합이 있어 — cron이 아직 그 시각 스냅샷을 저장하기
    #   전에 여기서 먼저 조회하면 훨씬 오래된(심하면 장외 시간대) 스냅샷을
    #   "최신"으로 잘못 쓰는 사고가 실제로 발생함(코스피 -5.95%인데 옛날
    #   테스트값 -2.04%를 보낸 사고). 스냅샷이 15분 이내로 신선한지 확인.
    try:
        snap_ts = datetime.datetime.strptime(snapshot.get("ts", ""), "%Y-%m-%d %H:%M")
        age_min = (datetime.datetime.now() - snap_ts).total_seconds() / 60
    except Exception:
        age_min = 9999
    if age_min > 15:
        print(f"⚠️ 쏠림지수 스냅샷이 오래됨({age_min:.0f}분 전, ts={snapshot.get('ts')}) — 브리핑 생략")
        return ""

    # ★ 2026-07-07: minutes_back=150분치를 통째로 넣으면 51건/9천자까지
    #   불어나서(장중 IR공시 몰릴 때), "보조 참고자료"라는 지시에도 불구하고
    #   LLM이 이 뉴스 뭉치를 요약해버리는 사고가 있었음(쏠림 갭/섹터랭킹
    #   숫자는 무시하고 개별 IR 뉴스만 나열). 최근 8건만 남기고 자른다.
    tele_raw   = fetch_recent_telegram_events(minutes_back=150)
    tele_items = [l for l in tele_raw.split("\n\n") if l.strip()]
    tele_context = "\n\n".join(tele_items[-8:])
    recent = get_recent_summaries(days=3)
    trend_text = "\n".join(
        f"- {r['date']}: {r['summary_text'][:150]}..." for r in recent
    ) if recent else "없음"

    # ★ 2026-07-06: 대형주 갭/시장폭이 그날따라 밋밋하면(예: 쏠림갭≈0,
    #   시장폭 90%+) AI가 근거로 쓸 숫자가 없어서 그냥 텔레그램 뉴스
    #   나열로 흘러가는 문제가 있었음(사용자 지적 — 실제로 반도체 쏠림이
    #   심하다고 체감하는데 코멘트엔 그 얘기가 전혀 없었음). 원인은
    #   대형주 워치리스트(S7)+전체 시장폭만으로는 "어떤 섹터가 오르고
    #   어떤 섹터가 못 올랐는지"를 애초에 측정 못 했던 것 — sector_ranking
    #   (오늘 상승/하락 테마 상위 랭킹)을 추가해서 섹터 단위 쏠림을 직접
    #   보여주고, 프롬프트도 숫자/섹터 데이터를 먼저 근거로 쓰도록 순서와
    #   지시를 강화. 텔레그램 뉴스는 보조 참고자료로 명시.
    # ★ 2026-07-08: 평균값(mega_avg_rate)만 주고 종목별 상세는 안 줬더니,
    #   LLM이 "대형주=삼성전자/SK하이닉스"라는 통념으로 실제 안 맞는 종목을
    #   지목해 서술하는 사고 발생(그날 SK하이닉스는 +1.68%로 오히려 상승
    #   했는데 "삼성전자·SK하이닉스가 밀리며"라고 씀 — 실제 하락은 삼성전기/
    #   삼성생명/삼성물산 쪽이었음). mega_detail(종목별 등락률)을 프롬프트에
    #   추가해 실제 데이터로만 종목을 지목하도록 함.
    mega_detail_text = "데이터 없음"
    try:
        _mega_raw = snapshot.get("mega_detail")
        if _mega_raw:
            _mega_map = json.loads(_mega_raw) if isinstance(_mega_raw, str) else _mega_raw
            _mega_names = {
                "005930": "삼성전자", "000660": "SK하이닉스", "402340": "SK스퀘어",
                "005935": "삼성전자우", "009150": "삼성전기", "032830": "삼성생명",
                "028260": "삼성물산",
            }
            mega_detail_text = ", ".join(
                f"{_mega_names.get(c, c)}({r:+.2f}%)" for c, r in _mega_map.items()
            )
    except Exception as e:
        print(f"⚠️ mega_detail 파싱 오류: {e}")

    prompt = (
        f"오늘은 {datetime.datetime.now(KST).strftime('%Y년 %m월 %d일')}이다. "
        "당신은 한국 주식시장 데이터 분석가입니다. 아래 [쏠림 지수 데이터]를 "
        "최우선 근거로 삼아 '오늘 시장이 특정 대형주/섹터에 얼마나 쏠려있는지, "
        "어떤 섹터/테마가 주도하고 소외됐는지'를 3~5문장으로 설명하세요.\n"
        "- 첫 문장은 반드시 쏠림 갭·시장폭·섹터 상승/하락 랭킹 중 가장 특징적인 "
        "숫자로 시작하세요 (예: 특정 테마 쏠림이 뚜렷하면 그 테마명을 명시).\n"
        "- [최근 텔레그램 속보]는 보조 참고자료일 뿐입니다 — 숫자로 뒷받침되지 "
        "않는 개별 종목 뉴스 나열로 답을 채우지 마세요. 숫자 자체가 밋밋하면 "
        "'오늘은 특정 섹터로의 뚜렷한 쏠림은 관찰되지 않음'이라고 솔직히 쓰세요.\n"
        "- 숫자를 지어내지 말고 주어진 값만 근거로 삼으세요. 날짜를 언급할 "
        "일이 있다면 위에 알려준 오늘 날짜만 쓰세요.\n"
        "- 특정 종목을 지목해서 언급할 때는 반드시 [대형주 S7 종목별 등락률]에 "
        "실제로 나온 수치를 확인하고 쓰세요 — '대형주=삼성전자/SK하이닉스'라는 "
        "통념으로 추측하지 말고, 평균을 실제로 끌어내리거나 끌어올린 종목이 "
        "무엇인지 데이터로 확인한 뒤 지목하세요.\n\n"
        f"[쏠림 지수 데이터 — {snapshot.get('ts', '')}]\n"
        f"- 코스피 등락률: {snapshot.get('kospi_rate', 0):+.2f}%\n"
        f"- 대형주 S7 평균 등락률: {snapshot.get('mega_avg_rate', 0):+.2f}%\n"
        f"- 대형주 S7 종목별 등락률: {mega_detail_text}\n"
        f"- 쏠림 갭(대형주-코스피): {snapshot.get('concentration_gap', 0):+.2f}%p "
        f"(클수록 대형주 쏠림)\n"
        f"- 시장 폭(상승종목비율): {snapshot.get('breadth_ratio', 0):.1f}% "
        f"(낮을수록 소수 종목/섹터만 오르는 좁은 장세)\n"
        f"- 오늘 섹터/테마 등락률 랭킹: {snapshot.get('sector_ranking') or '데이터 없음'}\n"
        f"- 주도주/섹터 급변 신호: {snapshot.get('rotation_flag') or '없음'}\n\n"
        f"[최근 텔레그램 속보 — 보조 참고자료]\n{tele_context or '없음'}\n\n"
        f"[최근 3일 종합 코멘트 추세 — 참고용]\n{trend_text}"
    )
    # ★ 2026-07-07: 로컬 ollama가 지시(숫자 우선/뉴스는 보조)를 무시하고
    #   텔레그램 뉴스 나열로 흘러가는 문제가 반복돼, 하루 1회뿐인 이 호출은
    #   비용 부담이 적으니 바로 Claude로 보낸다 (지시 준수 우선).
    return _call_llm(prompt, max_tokens=600, force_claude=True)


# ══════════════════════════════════════════════════════════════
# AI 모멘텀 스캐너 (2026-07-09 신규) — 관찰 전용
# ══════════════════════════════════════════════════════════════
# ★ VCP/추세/촉매는 전부 "이미 벌어진 기술적 패턴"만 본다. 미국-이란
#   재격돌/하이퍼스케일러 CAPEX 우려/중국 반도체 부각 같은 거시 모멘텀
#   내러티브를 종합해 "그래서 오늘 뭐가 뜰까"를 판단하는 축은 없었음.
#   사용자 제안: 하루 2회(아침/오후) 로컬 ollama에게 종목 2개씩 물어보고,
#   생쇼처럼 사후검증(체크인)만 하고 sbot/sbo2 스코어링엔 바로 연결하지
#   않는다. 로컬 AI를 쓰는 이유는 비용뿐 아니라 "로컬 AI가 이런 판단을
#   얼마나 잘하는지" 자체를 관찰하고 싶다는 의도 — force_claude 안 씀.

_THEME_LINE_RE = re.compile(r'테마\s*\d*\s*[:：]\s*(.+)')
MOMENTUM_MIN_PRICE = 5000  # ★ 2026-07-14: 동전주 배제 최소가 (사용자 지적)


def _parse_themes(llm_text: str) -> list:
    """
    '테마1: 전력기기 쇼티지' 라인 포맷 파싱 (AI는 이제 종목이 아니라 테마만 뽑는다).
    ★ 2026-07-14: 실제 운영에서 로컬 모델이 "테마2:" 뒤에 중국어로 된 긴
    추론 과정("...但根据提供的格式要求只能选择两个关键词主题。因此：")을
    그대로 흘려보낸 사고 발견 — 이게 그대로 테마로 쓰이면서 접두어 축소
    매칭(_map_themes_to_candidates)이 사실상 무작위로 종목을 엮어버림
    (유아이엘/인터지스가 이 오염된 테마로 잘못 매칭됨). 정상적인 테마는
    "반도체", "전력기기 쇼티지"처럼 15자 이내 한글 키워드이므로, 그보다
    길거나 한자(CJK 통합 한자)가 섞인 건 오염된 것으로 보고 버린다.
    """
    themes = []
    for line in llm_text.splitlines():
        m = _THEME_LINE_RE.search(line)
        if m:
            theme = m.group(1).strip().strip('*').strip()
            if not theme or len(theme) > 15:
                continue
            if re.search(r'[一-鿿]', theme):  # 한자(중국어) 섞이면 배제
                continue
            themes.append(theme)
    return themes[:3]


# ★ 2026-07-10: Momentum Router 재설계 — 모듈1 (Market Status Analyzer)
#   사용자 지적: AI가 직접 종목까지 고르게 하면 (a) 막연한 섹터명을 대거나
#   (b) 차트 검증을 AI의 텍스트 추론에만 의존하는 문제가 있었음(07-10 아침
#   세션 결과가 "갸우뚱"했다는 피드백). 대안: AI 역할을 "오늘의 명분(테마)
#   추출"로 좁히고, 종목 매핑+차트검증은 결정론적 코드(모듈3)에 맡긴다.
#   모듈1은 그 첫 단계 — 오늘이 애초에 대안주를 찾을 가치가 있는 장인지
#   진단(대형주가 돈을 다 빨아들이는 날엔 대안주 탐색 자체가 의미 없음).
def _check_market_phase() -> tuple:
    """
    market_concentration 스냅샷의 갭/시장폭/코스피등락률로 오늘 장세를
    3단계로 분류. 반환: (phase, reason)
    - 'A' S7 블랙홀   : 대형주 쏠림갭이 크고 시장폭이 좁음 — 대안주 탐색 보류
    - 'B' 순환매 여지 : 그 외 (기본값) — 대안주 탐색 풀가동
    - 'C' 약세장      : 코스피 자체가 뚜렷하게 하락 — 방어적 접근
    ★ 임계치(갭 1.5%p / 시장폭 50% / 코스피 -1.0%)는 이번 주(07-07~09)
      관찰값 기준 1차 추정치. 데이터 쌓이면 조정 필요.
    """
    try:
        from market_concentration import get_latest_snapshot
        snap = get_latest_snapshot() or {}
    except Exception as e:
        print(f"⚠️ [모멘텀] Phase 진단 오류: {e}")
        return 'B', "쏠림지수 조회 실패 — 기본값(B)으로 진행"

    gap     = snap.get('concentration_gap', 0)
    breadth = snap.get('breadth_ratio', 0)
    kospi   = snap.get('kospi_rate', 0)

    if kospi <= -1.0:
        return 'C', f"코스피 {kospi:+.2f}% 약세장"
    if gap >= 1.5 and breadth < 50:
        return 'A', f"대형주 쏠림갭 {gap:+.2f}%p, 시장폭 {breadth:.1f}% — S7 블랙홀"
    return 'B', f"쏠림갭 {gap:+.2f}%p, 시장폭 {breadth:.1f}% — 순환매 여지 있음"


def _enrich_momentum_picks(picks: list) -> list:
    """
    컨센서스 보강만 수행 — 코드/가격은 이미 _map_themes_to_candidates()에서
    VCP/추세 엔진이 계산한 값을 그대로 갖고 들어옴(재계산 불필요).
    ★ 2026-07-10: Momentum Router 재설계로 AI가 더 이상 종목을 직접 안
    고르므로, 여기서 하던 get_stock_code/ATR 재계산은 모듈3으로 이동.
    """
    from consensus import get_consensus

    enriched = []
    for p in picks:
        code = p.get("code", "")
        if code:
            cons = get_consensus(code, current_price=p.get("buy_price", 0))
            p["consensus_bonus"]  = cons.get("bonus", 0)
            p["consensus_reason"] = cons.get("reason", "")
        enriched.append(p)
    return enriched


# ★ 2026-07-10: Momentum Router 모듈3 (Sector & Stock Sniper)
def _map_themes_to_candidates(themes: list, exclude_names: set = None) -> list:
    """
    테마 키워드 → kr_theme_stocks 매칭 → VCP(swing)/추세(trend) 통과 종목만
    필터링해 최종 후보를 만든다. swing_master.py의 sector_monitor 테마-종목
    키워드 매칭 패턴(188-213행)과 동일한 방식, 어제 생쇼(SLOT_SSHOW)가
    했던 "VCP∪추세 교집합 게이팅"과 동일한 원리를 여기도 적용.
    exclude_names: 최근 픽된 종목명 집합 — 반등폭이 커서 계속 1등으로
      뽑히는 종목이 며칠씩 연속 픽되는 문제(2026-08-07, 사용자 지적
      "매일 같네")를 막기 위해, top-2 자르기 전에 아예 후보 풀에서
      제외해서 그다음 순위 종목이 자연스럽게 올라오도록 한다.
    """
    if not themes:
        return []
    exclude_names = exclude_names or set()

    from swing_analyzer import get_swing_data
    from trend_analyzer import get_trend_data
    from sbo2 import get_stock_code

    swing_data  = get_swing_data(top_n=30)
    trend_data  = get_trend_data(top_n=30)
    swing_names = {d["name"] for d in swing_data}
    trend_names = {d["name"] for d in trend_data}
    detail_map = {}
    for d in swing_data + trend_data:
        detail_map.setdefault(d["name"], d)
    pass_names = swing_names | trend_names
    if not pass_names:
        # ★ 2026-07-10: 예전엔 여기서 바로 리턴했으나, 그러면 완화트랙(아래)도
        #   전혀 시도 못 하고 끝나버림 — VCP/추세가 0개인 날에도 완화트랙은
        #   독립적으로 동작해야 하므로 조기 종료하지 않고 계속 진행.
        print("   VCP/추세 통과 종목 0개 — 완화트랙으로만 진행")

    conn = sqlite3.connect(DB_PATH_THEME_FINANCE)
    api = None  # 완화트랙 패턴C(거래량서지)에서만 지연 생성 — 불필요한 토큰/API 부담 방지
    seen = set()
    candidates = []
    light_candidates = []
    for theme in themes:
        words = [k for k in re.split(r'[\s/·,]+', theme) if len(k) >= 2]
        if not words:
            continue

        # 1차: 원단어 그대로 매칭
        rows = conn.execute(
            "SELECT DISTINCT stock_name FROM kr_theme_stocks WHERE " +
            " OR ".join(["theme_name LIKE ?"] * len(words)),
            [f"%{w}%" for w in words],
        ).fetchall()

        # ★ 2026-07-10: 공백 기준 단어("전력기기")가 DB 테마명("전력반도체",
        #   "전력저장장치")과 정확히 안 겹치는 경우가 많음(한글 복합어 특성).
        #   1차가 0건이면 단어 뒷글자를 하나씩 줄여가며 재시도(2글자까지) —
        #   전체 bigram을 한꺼번에 OR하면 "기기"(=device, 너무 흔함) 같은
        #   무의미한 조각이 미용기기 회사까지 끌어오는 오탐이 있었음. 접두어를
        #   점진적으로만 줄이면 "전력기기"→"전력기"→"전력"처럼 의미 있는
        #   단위에서 먼저 매칭을 멈출 수 있어 오탐이 훨씬 적음.
        if not rows:
            for cut in range(1, max(len(w) for w in words) - 1):
                prefixes = list(dict.fromkeys(
                    w[:len(w) - cut] for w in words if len(w) - cut >= 2
                ))
                if not prefixes:
                    break
                rows = conn.execute(
                    "SELECT DISTINCT stock_name FROM kr_theme_stocks WHERE " +
                    " OR ".join(["theme_name LIKE ?"] * len(prefixes)),
                    [f"%{p}%" for p in prefixes],
                ).fetchall()
                if rows:
                    break
        for (sname,) in rows:
            pure = re.sub(r'\s*(KOSPI|KOSDAQ)\s*\d{6}$', '', sname).strip()
            if pure in seen or pure in exclude_names:
                continue
            # ★ 2026-07-14: 동전주(초저가주) 배제 — 사용자 지적으로 실제
            #   운영 픽에서 3,000~5,000원대 저가주가 나온 걸 발견. 유동성/
            #   변동성 리스크가 커서 최소가 기준 미달 종목은 아예 후보에서
            #   제외한다.
            if pure in detail_map:
                _price_check = detail_map[pure].get("curr_price", 0)
            else:
                _row = conn.execute(
                    "SELECT close_price FROM kr_stock_daily_data WHERE stock_name=? "
                    "ORDER BY date DESC LIMIT 1", (pure,)
                ).fetchone()
                _price_check = _row[0] if _row and _row[0] else 0
            if _price_check < MOMENTUM_MIN_PRICE:
                continue
            if pure in pass_names:
                seen.add(pure)
                d = detail_map[pure]
                candidates.append({
                    "stock_name": pure,
                    "code":       get_stock_code(pure),
                    "theme":      theme,
                    "reasoning":  f"'{theme}' 테마 + 기술적 확인({'VCP' if pure in swing_names else '추세'})",
                    "buy_price":  d.get("curr_price", 0),
                    "stop_price": d.get("stop_price", 0),
                    "tgt_price":  d.get("tgt_price", 0),
                    "score":      d.get("score", 0),
                })
            else:
                # ★ 2026-07-10 완화 트랙 — VCP/추세는 "이미 만들어진 차트
                #   패턴"만 잡아서, 오늘 막 터진 속보성 촉매(전쟁/제재 등)에
                #   반응하는 종목은 애초에 그런 패턴이 생길 시간이 없어
                #   놓치는 딜레마가 있음(사용자 지적). 완전 방치하면
                #   텔레스윙(손절률 77.3%)처럼 확인 없이 사는 문제가 재현되니,
                #   "차트가 완전히 망가지진 않았다" 수준의 가벼운 조건만
                #   확인하는 별도 트랙을 둔다 — 하락 전환 or 박스권 상단
                #   돌파 임박 두 패턴만 인정.
                if api is None:
                    try:
                        from kis_api import KisAPI
                        api = KisAPI()
                    except Exception as e:
                        print(f"⚠️ [모멘텀] 완화트랙 거래량서지용 KIS API 초기화 실패: {e}")
                        api = False  # 재시도 방지용 sentinel
                light = _check_light_chart_health(pure, conn, api or None)
                if light:
                    seen.add(pure)
                    light_candidates.append({
                        "stock_name": pure,
                        "code":       get_stock_code(pure),
                        "theme":      theme,
                        "reasoning":  f"'{theme}' 테마 + 완화조건({light['pattern']})",
                        "buy_price":  light["curr_price"],
                        "stop_price": light["stop_price"],
                        "tgt_price":  light["tgt_price"],
                        "score":      0,  # 완화트랙은 항상 후순위
                    })
    conn.close()

    candidates.sort(key=lambda x: x["score"], reverse=True)
    # 기술적 확인(VCP/추세) 통과 종목을 우선하고, 부족하면 완화트랙으로 채움
    return (candidates + light_candidates)[:2]


def _check_light_chart_health(stock_name: str, conn: sqlite3.Connection, api=None) -> dict:
    """
    촉매 전용 완화 트랙 — VCP/추세의 다단계 조건 대신 "차트가 완전히
    망가지지 않았다" 수준만 가볍게 확인. 세 패턴 중 하나만 만족하면 통과:
    (A) 하락 전환: 최근 저점이 2~7일 전(너무 오래된 저점 제외)에 찍혔고
        현재가가 그 저점보다 3% 이상 위(★ 2026-08-07 강화 — 기존엔
        상한선 없이 "2일 이상 전"+"저점보다 아주 조금 위"만 봐서, 장기
        눌림 구간에서 같은 종목이 며칠씩 연속으로 픽되는 문제 발견
        (사용자 지적 — "매일 같네"). 저점 유효기간에 상한을 두고 반등폭
        최소 기준을 추가해 "진짜 갓 전환된" 종목만 통과하도록 함)
    (B) 박스권 상단 돌파 임박: 최근 15일 변동폭이 좁고(≤15%) 현재가가
        그 구간 상단 근처(3% 이내)이거나 이미 돌파
    (C) 거래량 서지 (2026-07-10 추가 — "마이크로 모멘텀" 대안): 200일선 위 +
        52주 고점 대비 -20% 이내인 종목 중, 당일 거래량이 최근 20일
        평균 대비 300%+ 이고 양봉(현재가>시가)이며 윗꼬리가 길지 않은
        경우(고가 대비 3% 이내). 신선한 속보성 촉매로 "오늘 갑자기" 돈이
        몰리는 종목은 A/B 같은 지난 15일 패턴이 없을 수 있어 이 축을 추가.
        VWAP은 분봉 데이터가 없어서 제외 — 거래량 서지만으로 근사.
        ★ 2026-07-10 검토 중 발견/수정: 처음엔 거래량 급증만 보고 방향을
        확인 안 해서, 나쁜 뉴스로 대량 매도가 터져 폭락하는 날에도
        "거래량서지"로 오판될 수 있는 버그가 있었음 — 양봉+윗꼬리 조건 추가.
        살아있는 KIS API 조회가 필요해 A/B가 이미 실패했을 때만, 그리고
        200일선/52주고점의 저렴한 DB 조건을 먼저 통과했을 때만 시도한다
        (불필요한 API 호출 방지 — 지난주 API 호출빈도 초과 사고 교훈).
    + 최소 안전장치: 60일선 대비 15% 이상 못 빠져있어야 함(완전 붕괴 배제).
    통과 시 {"pattern": ..., "curr_price", "stop_price", "tgt_price"} 반환, 아니면 {}.
    """
    rows = conn.execute("""
        SELECT close_price, volume FROM kr_stock_daily_data
        WHERE stock_name = ? ORDER BY date DESC LIMIT 260
    """, (stock_name,)).fetchall()
    closes  = [r[0] for r in rows if r[0] and r[0] > 0]
    volumes = [r[1] for r in rows if r[1] and r[1] > 0]
    if len(closes) < 30:
        return {}

    curr = closes[0]
    ma60 = sum(closes[:60]) / len(closes[:60]) if len(closes) >= 30 else 0
    if ma60 > 0 and curr < ma60 * 0.85:
        return {}  # 60일선 대비 15%+ 이탈 — 완전 붕괴, 완화트랙도 배제

    window = closes[0:15]
    lo, hi = min(window), max(window)

    pattern = None
    # (A) 하락 전환 — 저점이 2~7일 전(상한 있음)이고, 저점 대비 3%+ 반등
    idx_lo = window.index(lo)
    if 2 <= idx_lo <= 7 and lo > 0 and curr >= lo * 1.03:
        pattern = "하락전환"
    # (B) 박스권 상단 돌파 임박 — 최근 변동폭 좁고 상단 근접/돌파
    elif lo > 0 and (hi - lo) / lo <= 0.15 and curr >= hi * 0.97:
        pattern = "박스돌파임박"

    # (C) 거래량 서지 — A/B 둘 다 실패했을 때만 시도
    if not pattern and api and len(closes) >= 200 and len(volumes) >= 20:
        ma200 = sum(closes[:200]) / 200
        week52_high = max(closes[:252]) if len(closes) >= 252 else max(closes)
        if curr > ma200 and curr >= week52_high * 0.8:
            try:
                from sbo2 import get_stock_code
                code = get_stock_code(stock_name)
                mdata = api.get_market_data(code) if code else None
                if mdata:
                    acml_vol = float(mdata.get("acml_vol", 0) or 0)
                    avg_vol20 = sum(volumes[:20]) / 20
                    day_open = float(mdata.get("stck_oprc", 0) or 0)
                    day_high = float(mdata.get("stck_hgpr", 0) or 0)
                    # ★ 2026-07-10: 검토 중 발견한 버그 — 거래량 급증만 보고
                    #   방향(양봉/음봉)을 전혀 확인 안 해서, 나쁜 뉴스로 대량
                    #   매도가 터져 폭락하는 날에도 "거래량서지"로 오판될 수
                    #   있었음(사용자 지적). 양봉 확인(현재가>시가) + 윗꼬리
                    #   배제(고가 대비 3% 이상 밀리면 가짜돌파로 간주) 추가.
                    is_bullish   = day_open > 0 and curr > day_open
                    no_long_wick = day_high <= 0 or (day_high - curr) / curr <= 0.03
                    if (avg_vol20 > 0 and acml_vol >= avg_vol20 * 3.0
                            and is_bullish and no_long_wick):
                        pattern = "거래량서지"
            except Exception as e:
                print(f"⚠️ [모멘텀] {stock_name} 거래량서지 조회 오류: {e}")

    if not pattern:
        return {}

    return {
        "pattern": pattern,
        "curr_price": curr,
        "stop_price": round(curr * 0.93, 0),
        "tgt_price":  round(curr * 1.12, 0),
    }


_STOPWORDS_KO = {
    "있다", "없다", "한다", "하는", "했다", "되는", "된다", "위해", "대한", "대해",
    "관련", "이번", "지난", "오늘", "지금", "부터", "까지", "에서", "으로", "그리고",
    "하지만", "이라고", "라고", "이며", "또한", "통해", "같은", "이는", "것으로",
    "채널", "내용", "키워드", "가산점", "없음",
}


def _extract_top_keywords(text: str, top_n: int = 10) -> str:
    """
    ★ 2026-07-10 Momentum Router 모듈2 — 텔레그램/뉴스 원문이 방대해서
    (07-07 쏠림브리핑 사고, 07-09 젬마4:26b 타임아웃 등) 로컬 AI에게 그대로
    던지면 관심이 뉴스 나열 쪽으로 쏠리거나 추론이 안 끝나는 문제가 반복됨.
    파이썬에서 먼저 빈도수 상위 키워드로 1차 압축해 신호 대 잡음비를 높인다.
    """
    tokens = re.findall(r'[가-힣]{2,}', text or "")
    freq = {}
    for t in tokens:
        if t in _STOPWORDS_KO:
            continue
        freq[t] = freq.get(t, 0) + 1
    top = sorted(freq.items(), key=lambda x: x[1], reverse=True)[:top_n]
    return ", ".join(f"{w}({c})" for w, c in top) if top else "없음"


def _build_momentum_context_am() -> str:
    """아침 세션 컨텍스트 — 전일/미장/국제정세/전문가 시황"""
    from swing_master import _get_us_market_movers
    movers = _get_us_market_movers()
    us_lines = []
    for ticker, chg, kr_names in (movers[:6] + movers[-4:]):
        if kr_names:
            us_lines.append(f"{ticker}({chg:+.1f}%) → {', '.join(kr_names[:3])}")
    us_text = "\n".join(us_lines) if us_lines else "데이터 없음"

    tele_raw   = fetch_recent_telegram_events(minutes_back=720)
    keywords   = _extract_top_keywords(tele_raw)
    tele_items = [l for l in tele_raw.split("\n\n") if l.strip()]
    tele_text  = "\n\n".join(tele_items[-8:]) if tele_items else "없음"

    from market_concentration import get_recent_summaries
    recent = get_recent_summaries(days=5)
    trend_text = "\n".join(
        f"- {r['date']}: {r['summary_text'][:150]}..." for r in recent
    ) if recent else "없음"

    return (
        f"[간밤 미국 증시 — 한국 수혜/피해 종목 매핑]\n{us_text}\n\n"
        f"[핵심 키워드 빈도 — 간밤~아침 텔레그램]\n{keywords}\n\n"
        f"[간밤~아침 텔레그램 속보 (국제정세/전일상황 포함)]\n{tele_text}\n\n"
        f"[최근 며칠 쏠림 흐름 — 참고용]\n{trend_text}"
    )


def _build_momentum_context_pm() -> str:
    """오후 세션 컨텍스트 — 장중상황/섹터/텔레그램"""
    from market_concentration import get_latest_snapshot, _calc_sector_ranking, _calc_rotation_flag
    snapshot = get_latest_snapshot() or {}
    sector_ranking = _calc_sector_ranking()
    rotation_flag  = _calc_rotation_flag()

    tele_raw   = fetch_recent_telegram_events(minutes_back=300)
    keywords   = _extract_top_keywords(tele_raw)
    tele_items = [l for l in tele_raw.split("\n\n") if l.strip()]
    tele_text  = "\n\n".join(tele_items[-8:]) if tele_items else "없음"

    return (
        f"[오늘 장중 쏠림 지수 — {snapshot.get('ts', '')}]\n"
        f"- 코스피: {snapshot.get('kospi_rate', 0):+.2f}% / "
        f"대형주평균: {snapshot.get('mega_avg_rate', 0):+.2f}% / "
        f"시장폭: {snapshot.get('breadth_ratio', 0):.1f}%\n"
        f"- 섹터 등락률 랭킹: {sector_ranking or '데이터 없음'}\n"
        f"- 주도주/섹터 급변 신호: {rotation_flag or '없음'}\n\n"
        f"[핵심 키워드 빈도 — 장중 텔레그램]\n{keywords}\n\n"
        f"[장중 텔레그램 속보]\n{tele_text}"
    )


def _build_momentum_picks_sync(session: str, mbn_text: str = "") -> str:
    """
    동기 파트 (Momentum Router 4모듈 파이프라인, 2026-07-10 재설계):
    모듈1(Phase진단, pm만) → 모듈2(컨텍스트+키워드압축→LLM 테마추출)
    → 모듈3(테마→종목 매핑+VCP/추세 검증) → 컨센서스 보강 → DB저장 → 리포트.
    asyncio.to_thread로 실행.
    """
    today_str_kr = datetime.datetime.now(KST).strftime("%Y년 %m월 %d일")
    today_iso    = datetime.datetime.now(KST).strftime("%Y-%m-%d")

    if session == "am":
        context = _build_momentum_context_am()
        if mbn_text:
            context += f"\n\n[오늘 아침 전문가 시황(MBN 투자전략)]\n{mbn_text[:2000]}"
        session_label = "아침(시초가 판단용)"
        phase, phase_reason = "B", "아침 세션은 Phase 진단 생략(전일 마감 스냅샷)"
    else:
        context = _build_momentum_context_pm()
        session_label = "오후(종가 임박 판단용)"
        phase, phase_reason = _check_market_phase()

    if phase == "A":
        print(f"🧭 [모멘텀-{session}] PHASE_A — 대안주 탐색 보류: {phase_reason}")
        return (f"🧭 **[AI 모멘텀 스캐너 — {session_label}]** 🧭\n\n"
                f"💤 오늘은 대형주 쏠림이 심해({phase_reason}) 대안주 탐색을 보류했어.")

    prompt = (
        f"오늘은 {today_str_kr}이다. 당신은 한국 주식시장 모멘텀 분석가입니다. "
        f"아래 자료를 종합해서 오늘({session_label}) 시장을 관통하는 "
        "핵심 테마(명분) 2~3개를 뽑아주세요. 종목명이 아니라 테마/키워드만 뽑으면 됩니다.\n"
        "- 지정학적 이슈(예: 중동 갈등), 산업 이슈(예: 하이퍼스케일러 CAPEX, "
        "중국 반도체 정책), 수급 신호 등 여러 모멘텀 축을 실제로 비교해서 "
        "가장 설득력 있는 테마만 고르세요.\n"
        "- 숫자나 사실을 지어내지 말고 주어진 자료(특히 [핵심 키워드 빈도])에 "
        "실제로 있는 내용만 근거로 쓰세요.\n"
        "- 반드시 아래 형식 그대로, 다른 말 없이 이 줄들만 출력하세요:\n"
        "테마1: <테마 키워드 2~5자>\n"
        "테마2: <테마 키워드 2~5자>\n\n"
        f"{context}"
    )

    llm_text = _call_llm(prompt, max_tokens=400)
    if not llm_text:
        print(f"⚠️ [모멘텀-{session}] LLM 응답 없음")
        return ""

    themes = _parse_themes(llm_text)
    if not themes:
        print(f"⚠️ [모멘텀-{session}] 테마 파싱 실패 — 원문:\n{llm_text}")
        return ""

    import ai_momentum_db
    recent_names = ai_momentum_db.get_recent_pick_names(trading_days=5)
    candidates = _map_themes_to_candidates(themes, exclude_names=recent_names)
    if not candidates:
        print(f"💤 [모멘텀-{session}] 테마({', '.join(themes)})에 맞는 "
              "VCP/추세 통과 종목 없음(최근 5거래일 픽 제외 반영) — 오늘은 후보 없음")
        return (f"🧭 **[AI 모멘텀 스캐너 — {session_label}]** 🧭\n\n"
                f"오늘의 테마({', '.join(themes)})는 뽑혔지만, 최근 5거래일 내 "
                "이미 나온 종목을 빼면 VCP/추세 기술적 확인을 통과한 새 종목이 "
                "없어서 최종 후보는 없어.")

    for c in candidates:
        c["phase"] = phase
    enriched = _enrich_momentum_picks(candidates)
    if not enriched:
        return ""

    import ai_momentum_db
    ai_momentum_db.save_picks(today_iso, session, enriched)

    lines = [f"🧭 **[AI 모멘텀 스캐너 — {session_label}]** 🧭",
              f"   오늘의 테마: {', '.join(themes)}\n"]
    for p in enriched:
        cons_line = f" | 컨센서스: {p['consensus_reason']}" if p.get("consensus_reason") else ""
        lines.append(
            f"📌 **{p['stock_name']}**({p.get('code', '')})\n"
            f"   근거: {p['reasoning']}\n"
            f"   매수:{p['buy_price']:,.0f} 손절:{p['stop_price']:,.0f} "
            f"목표:{p['tgt_price']:,.0f}{cons_line}"
        )
    return "\n\n".join(lines)


async def _build_momentum_picks(session: str) -> str:
    """session: 'am' | 'pm'"""
    mbn_text = ""
    if session == "am":
        try:
            mbn_text = await fetch_mbn_strategy()
        except Exception as e:
            print(f"⚠️ [모멘텀-am] MBN 조회 오류: {e}")
    return await asyncio.to_thread(_build_momentum_picks_sync, session, mbn_text)


@tasks.loop(minutes=1)
async def daily_momentum_am_report():
    kst_now = datetime.datetime.now(KST)
    if kst_now.hour != 8 or kst_now.minute != 55:
        return
    if not _is_trading_day():
        print(f"🎌 [모멘텀-am] 주말/휴장일 — 스킵")
        return
    print(f"\n🧭 [{kst_now.strftime('%H:%M')}] AI 모멘텀 스캐너(아침) 가동!")
    try:
        channel = await client.fetch_channel(REPORT_CHANNEL_ID)
        report = await _build_momentum_picks("am")
        if report:
            await send_safe_message(channel, report)
            print("✅ AI 모멘텀(아침) 전송 완료!")
        else:
            print("💤 AI 모멘텀(아침) — 픽 생성 실패, 생략")
    except Exception as e:
        print(f"❌ AI 모멘텀(아침) 에러: {e}")


@tasks.loop(minutes=1)
async def daily_momentum_pm_report():
    kst_now = datetime.datetime.now(KST)
    if kst_now.hour != 14 or kst_now.minute != 35:
        return
    if not _is_trading_day():
        print(f"🎌 [모멘텀-pm] 주말/휴장일 — 스킵")
        return
    print(f"\n🧭 [{kst_now.strftime('%H:%M')}] AI 모멘텀 스캐너(오후) 가동!")
    try:
        channel = await client.fetch_channel(REPORT_CHANNEL_ID)
        report = await _build_momentum_picks("pm")
        if report:
            await send_safe_message(channel, report)
            print("✅ AI 모멘텀(오후) 전송 완료!")
        else:
            print("💤 AI 모멘텀(오후) — 픽 생성 실패, 생략")
    except Exception as e:
        print(f"❌ AI 모멘텀(오후) 에러: {e}")


@tasks.loop(minutes=1)
async def daily_momentum_checkin():
    """장 마감 후 체크인 — 7/14일 역일 도달 픽 판정 (sshow와 동일 방식)"""
    kst_now = datetime.datetime.now(KST)
    if kst_now.hour != 16 or kst_now.minute != 0:
        return
    if not _is_trading_day():
        print(f"🎌 [모멘텀-체크인] 주말/휴장일 — 스킵")
        return
    try:
        import ai_momentum_db
        notifications = await asyncio.to_thread(ai_momentum_db.check_and_update_results)
        if notifications:
            channel = await client.fetch_channel(REPORT_CHANNEL_ID)
            text = "🧭 **[AI 모멘텀 체크인]** 🧭\n\n" + "\n".join(n["text"] for n in notifications)
            await send_safe_message(channel, text)
    except Exception as e:
        print(f"❌ AI 모멘텀 체크인 에러: {e}")


_KIWOOM_POOL_SCAN_TIMES = {(9, 30), (12, 30), (15, 0)}
_kiwoom_pool_scan_state = {"date": "", "done": set(), "retry_at": None, "retry_label": None}


@tasks.loop(minutes=1)
async def kiwoom_pool_scan_loop():
    """키움 조건검색식 전체 스캔 → 소스별 누적 저장(kiwoom_pool_tracker.py).
    09:30/12:30/15:00 하루 3회, 실패 시 5분 뒤 1회 재시도 (2026-07-25 사용자 결정).
    당일 중복은 kiwoom_pool_tracker.py의 UNIQUE(scan_date,stock_name,source)로 제거."""
    kst_now = datetime.datetime.now(KST)
    if not _is_trading_day():
        return

    today = kst_now.strftime("%Y-%m-%d")
    if _kiwoom_pool_scan_state["date"] != today:
        _kiwoom_pool_scan_state.update(
            {"date": today, "done": set(), "retry_at": None, "retry_label": None})

    hm = (kst_now.hour, kst_now.minute)
    label = f"{kst_now.hour:02d}:{kst_now.minute:02d}"

    if hm in _KIWOOM_POOL_SCAN_TIMES and label not in _kiwoom_pool_scan_state["done"]:
        _kiwoom_pool_scan_state["done"].add(label)
        print(f"\n🔍 [키움풀] {label} 스캔 시작")
        try:
            from kiwoom_pool_tracker import scan_and_log
            ok = await scan_and_log()
        except Exception as e:
            print(f"❌ [키움풀] {label} 스캔 에러: {e}")
            ok = False
        if not ok:
            _kiwoom_pool_scan_state["retry_at"] = kst_now + datetime.timedelta(minutes=5)
            _kiwoom_pool_scan_state["retry_label"] = label
            print(f"⚠️ [키움풀] {label} 스캔 실패 — 5분 뒤 재시도 예정")
        return

    if (_kiwoom_pool_scan_state["retry_at"] is not None and
            kst_now >= _kiwoom_pool_scan_state["retry_at"]):
        retry_label = _kiwoom_pool_scan_state["retry_label"]
        _kiwoom_pool_scan_state["retry_at"] = None
        _kiwoom_pool_scan_state["retry_label"] = None
        print(f"🔁 [키움풀] {retry_label} 스캔 재시도")
        try:
            from kiwoom_pool_tracker import scan_and_log
            await scan_and_log()
        except Exception as e:
            print(f"❌ [키움풀] {retry_label} 재시도 에러: {e}")


@kiwoom_pool_scan_loop.before_loop
async def before_kiwoom_pool_scan_loop():
    await client.wait_until_ready()


@tasks.loop(minutes=1)
async def daily_market_context_report():
    kst_now = datetime.datetime.now(KST)
    # ★ 09:30이 아니라 09:35 — cron(market_concentration.py)이 09:30 정각에
    #   실행되므로, API 호출 몇 개(1~2분 소요 가능) 끝날 시간을 벌어주기 위함.
    #   (그래도 늦어질 수 있어 위 신선도 체크가 최종 안전장치)
    if kst_now.hour != 9 or kst_now.minute != 35:
        return
    if not _is_trading_day():
        print(f"🎌 [쏠림브리핑] 주말/휴장일 — 스킵")
        return
    print(f"\n📐 [{kst_now.strftime('%H:%M')}] 시장 쏠림 종합 브리핑 가동!")
    try:
        channel = await client.fetch_channel(REPORT_CHANNEL_ID)
    except Exception as e:
        print(f"❌ 쏠림 브리핑 채널 접속 실패: {e}"); return
    try:
        summary = await asyncio.to_thread(_build_market_context_summary)
        if summary:
            today = kst_now.strftime("%Y-%m-%d")
            await send_safe_message(
                channel,
                f"📐 **[대장! 오늘 시장 쏠림 코멘트야 (관찰 전용)]** 📐\n\n{summary}"
            )
            try:
                from market_concentration import save_market_summary, get_latest_snapshot
                save_market_summary(today, summary, get_latest_snapshot())
            except Exception as e:
                print(f"⚠️ 쏠림 브리핑 저장 오류: {e}")
            print("✅ 시장 쏠림 종합 브리핑 전송 완료!")
        else:
            print("💤 쏠림 지수 스냅샷 없음 — 브리핑 생략 (장 시작 직후이거나 cron 미실행)")
    except Exception as e:
        print(f"❌ 시장 쏠림 종합 브리핑 에러: {e}")

@daily_market_context_report.before_loop
async def before_daily_market_context_report():
    await client.wait_until_ready()


def fetch_recent_telegram_events(limit_count=4, minutes_back=65):
    """
    ★ 시간 기준으로 변경 (2026-06-23) — 기존 id 기반(LAST_TELEGRAM_ID 전역변수) 방식은
      재시작/재로드/예외 상황에서 값이 꼬이면 영구적으로 "새 메시지 없음"이 되는
      버그가 있어 시간 윈도우 방식으로 교체. 메모리 상태에 의존하지 않아 안전.
    최근 minutes_back분 이내 메시지만 반환.
    """
    try:
        conn = sqlite3.connect(DB_PATH_TELEGRAM, timeout=10)
        cursor = conn.cursor()
        cutoff = (datetime.datetime.now() -
                  datetime.timedelta(minutes=minutes_back)).strftime("%Y-%m-%d %H:%M:%S")
        query = """
            SELECT id, channel, message, keywords, themes, score
            FROM telegram_events
            WHERE created_at >= ?
            ORDER BY id ASC
        """
        cursor.execute(query, (cutoff,))
        rows = cursor.fetchall()
        conn.close()

        if not rows: return ""

        raw_context = ""
        seen = set()
        for r in rows:
            row_id, channel, msg, keywords, themes, score = r
            msg = str(msg or "").strip().replace("\xed\x8c\xb9리스", "팹리스")
            if not msg or msg in seen:
                continue
            seen.add(msg)
            kw = ", ".join(json.loads(keywords)) if keywords else "없음"
            raw_context += f"채널: [{channel}] | 내용: {msg} | 키워드: {kw} | 가산점: +{score or 10}점\n\n"

        return raw_context
    except Exception as e:
        return f"디비 접근 오류: {str(e)}"

# 💡 [신규 엔진 기능] 아침 브리핑에 주입할 최고 우량 수급 종목 발굴 엔진
def fetch_top_institutional_and_foreign_picks():
    # 💡 복잡한 로직은 모듈로 다 보냈으니, 여기선 깔끔하게 Call만 때린다!
    return quant_analyzer.get_hybrid_top_picks()

# ===================================================
# 💡 [테마 역추적 기능이 추가된 하이브리드 검색 라우터]
# ===================================================
async def web_search_hybrid(query):
    # 1. 특정 종목에 대해 테마를 물어보는 경우 (예: "필옵틱스 테마 뭐야?")
    if "테마" in query or "뭐야" in query:
        conn = sqlite3.connect(DB_PATH_THEME_FINANCE)
        cursor = conn.cursor()
        
        cursor.execute("SELECT theme_name FROM kr_theme_stocks WHERE stock_name LIKE ?", ('%' + query.replace("테마", "").replace("뭐야", "").strip() + '%',))
        results = cursor.fetchall()
        conn.close()
        
        if results:
            themes = [r[0] for r in set(results)]
            return f"🔍 **[테마 탐색기]** 대장! 찾았어! \n{', '.join(themes)} 테마에 묶여있는 종목이야!"

    # 2. 기존 기능들 그대로 유지
    if any(kw in query for kw in ["일정", "스케줄", "계획"]) and "추가" not in query: return f"[구글 캘린더 일정 목록]:\n{fetch_calendar_events()}"
    if any(kw in query for kw in ["입출금", "출금", "내역", "수입", "지출", "가계부", "장부"]): return get_monthly_report()
    if any(kw in query for kw in ["날씨", "기온", "온도", "비와", "눈와", "기상"]): return f"[국내 대한민국 기상청]:\n{get_weather_kma_pure()}"
    if any(kw in query for kw in ["뉴스", "속보", "mbn", "모닝", "브리핑"]): return "[MBN골드 뉴스]:\n" + await fetch_mbngold_async("10001", 6)
    if any(kw in query for kw in ["텔레그램", "텔레", "실시간속보"]): return "[텔레그램 속보]:\n" + fetch_recent_telegram_events()
    return ""

# ===================================================
# ⏰ [정품 디스코드 tasks.loop 스케줄러]
# ===================================================
US_WATCHLIST = ["NVDA", "INTC", "TSLA", "AAPL", "MSFT", "GOOGL"]

# 1. 07시 30분 장전 통합 융합 마스터 브리핑 루프 (수급 데이터 전격 연동 완비!)
@tasks.loop(minutes=1)
async def daily_morning_report():
    kst_now = datetime.datetime.now(KST)
    
    if kst_now.hour != 7 or kst_now.minute != 30:
        return
    if not _is_trading_day():
        print(f"🎌 [융합브리핑] 주말/휴장일 — 스킵")
        return

    print(f"\n☀️ [{kst_now.strftime('%H:%M')}] 텔레그램+미국장+뉴스+수급 통합 융합 마스터 브리핑 가동!")
    
    try:
        channel = await client.fetch_channel(REPORT_CHANNEL_ID)
    except Exception as e:
        print(f"❌ 장전 브리핑 채널 접속 실패: {e}")
        return

    # STEP 1: 간밤의 미국 증시 급등주 스캔 & 고정 DB 맵핑
    us_movers_summary = ""
    for ticker in US_WATCHLIST:
        try:
            stock = yf.Ticker(ticker)
            hist = stock.history(period="2d")
            if len(hist) >= 2:
                prev_close = hist['Close'].iloc[0]
                last_close = hist['Close'].iloc[1]
                change_pct = ((last_close - prev_close) / prev_close) * 100
                
                if change_pct >= 3.0:
                    mapped_stocks = get_kr_stocks_by_ticker(ticker)
                    stock_names = [s['kr_name'] for s in mapped_stocks]
                    us_movers_summary += f"- 🇺🇸 {ticker} ({change_pct:+.2f}%) ➡️ 🇰🇷 고정 수혜주: {', '.join(stock_names) if stock_names else '등록 필요'}\n"
        except Exception as e:
            print(f"⚠️ {ticker} 스캔 실패: {e}")

    # STEP 2: 텔레그램 속보 대량 수집 (최근 15개)
    telegram_context = fetch_recent_telegram_events(limit_count=15)
    if not telegram_context.strip() or "비어있네" in telegram_context:
        telegram_context = "- 밤사이 특이 텔레그램 동향 없음"

    # STEP 3: 크롤러 수급
    crawler_finance_context = fetch_top_institutional_and_foreign_picks()

    # STEP 4: AI 융합 브리핑 (미장 + 텔레그램 + 수급)
    prompt = (
        f"너는 대한민국 최고의 모멘텀 단타 트레이더를 보좌하는 수석 참모 리나야.\n"
        f"제공된 3가지 핵심 데이터를 상호 교차 검증하여 오늘 장초반 시나리오를 짜줘.\n\n"
        f"[데이터 1: 미국장 급등 현황 & 고정 관련주]\n{us_movers_summary if us_movers_summary else '- 특이 급등 종목 없음'}\n\n"
        f"[데이터 2: 최근 국내 텔레그램 주요 속보 맥락]\n{telegram_context}\n\n"
        f"[데이터 3: 크롤러 엔진 수집 종목별 메이저 쌍끌이 수급 현황]\n{crawler_finance_context}\n\n"
        f"🚨 [브리핑 핵심 지침]:\n"
        f"1. **교차 검증**: 미국장 급등 섹터와 텔레그램 속보 테마가 일치하는지 집중 매칭해줘.\n"
        f"2. **수급 주도주**: 데이터 3의 쌍끌이 수급 유입 주도주를 강조해줘.\n"
        f"3. **원픽 테마**: 오늘 수급이 가장 강하게 붙을 원픽 테마와 핵심 종목을 단도직입적으로 요약해줘."
    )

    payload = {"model": MODEL_NAME, "messages": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}], "stream": False, "options": {"temperature": 0.2}}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(OLLAMA_API_URL, json=payload) as response:
                if response.status == 200:
                    res_json = await response.json()
                    reply_text = res_json.get("message", {}).get("content", "").strip()
                    await send_safe_message(channel, f"☀️ **[대장! 07시 30분 융합 마스터 전략 브리핑이야]** ☀️\n\n{reply_text}")
                    print(f"✅ [디버그] 07시 30분 4합 통합 융합 마스터 브리핑 전송 완료!")
    except Exception as e: print(f"❌ 통합 브리핑 전송 에러: {e}")

@daily_morning_report.before_loop
async def before_daily_morning_report():
    await client.wait_until_ready()

# ★ 2026-07-25: 오후 2시 30분 생쇼 관심종목 루프 제거 — MBN이 생쇼
#   뉴스 코너(news_service_id=10020) 자체를 폐지해서(게시글 0건, 사이트
#   뉴스탭에서도 카테고리 소실 확인됨) 소스가 영구 중단됨.

# 3. 매 시간 30분 텔레그램 속보 루프
@tasks.loop(minutes=1)
async def hourly_telegram_event_report():
    global LAST_TELEGRAM_ID
    kst_now = datetime.datetime.now(KST)
    
    if kst_now.minute != 30:
        return

    print(f"\n🚀 [디버그] {kst_now.strftime('%H:%M')} 텔레그램 루프 출발! 채널 접속 중...")

    try:
        channel = await client.fetch_channel(REPORT_CHANNEL_ID)
    except Exception as e:
        print(f"❌ [디버그 에러] 텔레그램 채널 접속 실패: {e}")
        return

    raw_context = fetch_recent_telegram_events()
    if not raw_context.strip():
        print(f"💤 [디버그] 새로운 텔레그램 속보가 없어서 브리핑을 건너뜁니다! (중복 방지)")
        return





    prompt = (
        f"너는 1시간 동안 발생한 텔레그램 주식/시황 속보를 정밀 요약하는 참모 리나야.\n"
        f"🚨 [초특급 핵심 규칙]: 수집된 개별 뉴스 '하나당' 반드시 딱 아래의 '3줄 포맷'을 적용해!\n\n"
        f"📌 테마/이슈명 (가산점: +00점)\n"
        f"  - 첫 번째 핵심 속보 내용 요약\n"
        f"  - 두 번째 관련 핵심 종목/섹터 압축\n\n"
        f"[최신 속보 데이터]:\n{raw_context}"
    )
    payload = {"model": MODEL_NAME, "messages": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}], "stream": False, "options": {"temperature": 0.0}}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(OLLAMA_API_URL, json=payload) as response:
                if response.status == 200:
                    res_json = await response.json()
                    reply_text = res_json.get("message", {}).get("content", "").strip()
                    await send_safe_message(channel, f"🚨 **[대장! 지난 텔레그램 주도 테마 요약이야]** 🚨\n\n{reply_text}")
                    print(f"🎉 [디버그] 텔레그램 리포트 전송 완벽 성공!")
    except Exception as e: print(f"❌ 텔레그램 리포트 전송 에러: {e}")

@hourly_telegram_event_report.before_loop
async def before_hourly_telegram_event_report():
    await client.wait_until_ready()

# 4. 07시 00분 아침 뉴스 루프
@tasks.loop(minutes=1)
async def daily_news_report():
    kst_now = datetime.datetime.now(KST)
    if kst_now.hour != 7 or kst_now.minute != 0:
        return
    if not _is_trading_day():
        print(f"🎌 [아침뉴스] 주말/휴장일 — 스킵")
        return

    print(f"\n📰 [{kst_now.strftime('%H:%M')}] 아침 뉴스 브리핑 가동!")
    try:
        channel = await client.fetch_channel(REPORT_CHANNEL_ID)
    except Exception as e:
        print(f"❌ 뉴스 채널 접속 실패: {e}")
        return

    raw_news = await fetch_mbngold_async(service_id="10001", limit=6)
    if not raw_news or "텅 비어" in raw_news:
        try:
            async with AsyncSession() as naver_session:
                naver_res = await naver_session.get(
                    "https://finance.naver.com/news/news_list.naver?mode=LSS2D&section_id=101&section_id2=258",
                    headers={"User-Agent": "Mozilla/5.0"},
                    impersonate="chrome", timeout=10
                )
                naver_soup = BeautifulSoup(
                    naver_res.content.decode('euc-kr', errors='ignore'), 'html.parser')
                headlines = [a.get_text(strip=True)
                             for a in naver_soup.select('.articleSubject a')][:6]
                raw_news = "\n".join(f"- {h}" for h in headlines) if headlines \
                           else "- 국내 장전 뉴스 데이터 없음"
        except Exception:
            raw_news = "- 국내 장전 뉴스 데이터 없음"

    prompt = (
        f"너는 아침 뉴스를 브리핑하는 참모 리나야.\n"
        f"수집된 실제 데이터만 바탕으로 핵심만 요약해줘. 절대 지어내지 마.\n\n"
        f"[오늘 아침 뉴스]\n{raw_news}"
    )
    payload = {"model": MODEL_NAME,
               "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                             {"role": "user", "content": prompt}],
               "stream": False, "options": {"temperature": 0.0}}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(OLLAMA_API_URL, json=payload) as response:
                if response.status == 200:
                    res_json = await response.json()
                    reply_text = res_json.get("message", {}).get("content", "").strip()
                    await send_safe_message(channel,
                        f"📰 **[대장! 07시 아침 뉴스야]** 📰\n\n{reply_text}")
                    print(f"✅ 07시 뉴스 브리핑 전송 완료!")
    except Exception as e:
        print(f"❌ 뉴스 브리핑 오류: {e}")

@daily_news_report.before_loop
async def before_daily_news_report():
    await client.wait_until_ready()


# 4-1. 08시 50분 MBN골드 투자전략 요약 루프 (★ 2026-07-01 신규)
@tasks.loop(minutes=1)
async def daily_strategy_report():
    kst_now = datetime.datetime.now(KST)
    if kst_now.hour != 8 or kst_now.minute != 50:
        return
    if not _is_trading_day():
        print(f"🎌 [MBN전략] 주말/휴장일 — 스킵")
        return
    print(f"\n📊 [{kst_now.strftime('%H:%M')}] MBN 투자전략 요약 가동!")
    try:
        channel = await client.fetch_channel(REPORT_CHANNEL_ID)
    except Exception as e:
        print(f"❌ 전략 채널 접속 실패: {e}"); return
    try:
        result = await fetch_mbn_strategy(cutoff_hour=8, cutoff_minute=50)
        if result:
            await send_safe_message(
                channel,
                f"📊 **[대장! 오늘 전문가 투자전략/시황 요약이야 (07:30~08:50)]** 📊\n\n{result}"
            )
            print("✅ MBN 투자전략 요약 전송 완료!")
        else:
            print("💤 MBN 투자전략 07:30~08:50 사이 새 글 없음")
    except Exception as e:
        print(f"❌ MBN 투자전략 요약 에러: {e}")

@daily_strategy_report.before_loop
async def before_daily_strategy_report():
    await client.wait_until_ready()


# 5. 07시 20분 스윙 마스터 리포트 루프
@tasks.loop(minutes=1)
async def daily_master_report():
    kst_now = datetime.datetime.now(KST)
    if kst_now.hour != 7 or kst_now.minute != 20:
        return
    if not _is_trading_day():
        print(f"🎌 [마스터리포트] 주말/휴장일 — 스킵")
        return

    print(f"\n🎯 [{kst_now.strftime('%H:%M')}] 스윙 마스터 리포트 가동!")
    try:
        channel = await client.fetch_channel(REPORT_CHANNEL_ID)
    except Exception as e:
        print(f"❌ 마스터 채널 접속 실패: {e}")
        return

    try:
        master_report = await asyncio.to_thread(get_master_report, 3)

        # ★ 2026-09-03: 키움풀 체크인(5영업일 경과분 검증) — 07/25 신설 이후
        #   스케줄러에 안 물려 있어 한 달 넘게 데이터만 쌓이고 검증이 한 번도
        #   안 되고 있었음(사용자 지적). 매일 07:20 마스터 리포트에 같이
        #   포함해서 매일 자동 검증되도록 연결. sbo2 실거래 자동연결은 며칠
        #   더 관찰 + 주말 백테스터 재검증 후 별도 결정(사용자 지시).
        try:
            from kiwoom_pool_tracker import checkin_pool_log
            checked_cnt, promoted_list = await asyncio.to_thread(checkin_pool_log)
            if checked_cnt > 0:
                pool_lines = [f"\n\n🔍 **키움풀 체크인 (5영업일 경과분)**",
                              f"평가: {checked_cnt}건 | 재검토 후보: {len(promoted_list)}건"]
                for name, scan_date, chg in promoted_list[:10]:
                    pool_lines.append(f"  · {name} ({scan_date} 스캔, {chg:+.1f}%)")
                master_report += "\n".join(pool_lines)
        except Exception as e:
            print(f"⚠️ 키움풀 체크인 오류: {e}")

        await send_safe_message(channel,
            f"🎯 **[대장! 07:20 스윙 마스터 리포트야]** 🎯\n\n{master_report}")
        print(f"✅ 07:20 마스터 리포트 전송 완료!")
    except Exception as e:
        print(f"❌ 마스터 리포트 오류: {e}")

@daily_master_report.before_loop
async def before_daily_master_report():
    await client.wait_until_ready()

@tasks.loop(minutes=1)
async def daily_tele_swing_report():
    kst_now = datetime.datetime.now(KST)
    if kst_now.hour != 7 or kst_now.minute != 50:
        return
    if not _is_trading_day():
        print(f"🎌 [텔레스윙] 주말/휴장일 — 스킵")
        return
    print(f"\n📡 [{kst_now.strftime('%H:%M')}] 텔레스윙 리포트 가동!")
    try:
        channel = await client.fetch_channel(REPORT_CHANNEL_ID)
        from tele_swing_analyzer import get_tele_swing_report
        report = await asyncio.to_thread(get_tele_swing_report, 3)
        await send_safe_message(channel, f"📡 **[대장! 07:50 텔레스윙 리포트야]** 📡\n\n{report}")
        print("✅ 07:50 텔레스윙 전송 완료!")
    except Exception as e:
        print(f"❌ 텔레스윙 오류: {e}")

@daily_tele_swing_report.before_loop
async def before_daily_tele_swing_report():
    await client.wait_until_ready()

@tasks.loop(minutes=1)
async def daily_tele_swing_afternoon():
    kst_now = datetime.datetime.now(KST)
    if kst_now.hour != 14 or kst_now.minute != 40:
        return
    if not _is_trading_day():
        print(f"🎌 [텔레스윙PM] 주말/휴장일 — 스킵")
        return
    print(f"\n📡 [{kst_now.strftime('%H:%M')}] 텔레스윙 오후 재기동!")
    try:
        channel = await client.fetch_channel(REPORT_CHANNEL_ID)
        from tele_swing_analyzer import get_tele_swing_report
        report = await asyncio.to_thread(get_tele_swing_report, 3, True)
        await send_safe_message(channel, f"📡 **[대장! 14:40 텔레스윙 업데이트]** 📡\n\n{report}")
        print("✅ 14:40 텔레스윙 전송 완료!")
    except Exception as e:
        print(f"❌ 텔레스윙 오후 오류: {e}")

@daily_tele_swing_afternoon.before_loop
async def before_daily_tele_swing_afternoon():
    await client.wait_until_ready()

# ===================================================
# 🛡️ API 에러 감시 + 자동 재시작 (1분 주기)
# ===================================================
# 감시 대상: sbot, sbo2, sector — 한투/키움 API 문제가
# 반복되면 해당 systemd 서비스만 재시작한다.
#
# 두 종류의 에러를 독립적으로 추적한다 (원인이 다르므로 카운터 분리):
#   1) 토큰/인증 실패 — 토큰 자체가 무효화된 상태
#   2) API 호출 빈도 초과(rate limit) — 캐시로 넘어가며 조용히 누적,
#      실계좌와 캐시가 어긋날 위험. 30초 루프 기준 2분(4회) 내
#      해소 안 되면 재시작.
WATCHDOG_BOTS = ["sbot", "sbo2", "sector"]

TOKEN_ERROR_PATTERNS = [
    "인증에 실패했습니다",
    "Token이 유효하지 않습니다",
    "토큰 발급 오류",
    "토큰 발급 실패",
]

RATE_LIMIT_ERROR_PATTERNS = [
    # ★ 2026-07-06: 기존엔 "초당 거래건수를 초과"/"잔고 빈값 — 재시도"를
    #   감지 패턴으로 썼는데, 이 둘은 core/kis_api.py의 get_current_positions()가
    #   1초 대기 후 최대 3회 재시도하는 과정에서 나오는 "시도 중" 메시지라
    #   sbot/sbo2가 곧바로 스스로 복구해도 그대로 찍힘. 즉 실제로는 멀쩡히
    #   자가복구된 순간적 지연을 watchdog이 "문제 지속"으로 오인해 불필요하게
    #   재시작시키는 원인이었음. 3회 재시도가 전부 실패했을 때만 찍히는
    #   "이전 캐시 유지"로 교체 — 이게 진짜 "재시도로도 안 풀린" 신호다.
    "이전 캐시 유지",
]

# 봇별 연속 감지 횟수 (에러 종류별로 독립)
_token_error_streak = {bot: 0 for bot in WATCHDOG_BOTS}
_rate_limit_error_streak = {bot: 0 for bot in WATCHDOG_BOTS}
# 마지막 재시작 시각 (쿨다운 체크용)
_last_restart_at = {bot: None for bot in WATCHDOG_BOTS}

TOKEN_ERROR_STREAK_THRESHOLD = 2       # 연속 2분 감지되면 재시작
RATE_LIMIT_ERROR_STREAK_THRESHOLD = 2  # 연속 2분(루프 30초 기준 약 4회) 감지되면 재시작
RESTART_COOLDOWN_SECONDS = 300         # 재시작 후 5분간 재감지 무시


# ★ 각 systemd 유닛의 StandardOutput 설정이 봇마다 다르다
#   (실제로 /etc/systemd/system/yeongam9-*.service 에서 확인됨):
#     - sbo2   : StandardOutput=journal           → journalctl로 조회 가능
#     - sbot   : StandardOutput=append:logs/sbot.log          → journal에 안 쌓임
#     - sector : StandardOutput=append:logs/sector_monitor.log → journal에 안 쌓임
#   sbot/sector를 journalctl로만 조회하면 항상 빈 로그를 받아
#   watchdog이 절대 감지를 못 하므로(실제로 이 버그로 sbot 미감지 발생),
#   파일 직접 출처인 봇은 로그 파일을 직접 읽는다.
_BOT_LOG_FILE = {
    "sbot":   "/home/free4tak/k-bot/stock_bot/logs/sbot.log",
    "sector": "/home/free4tak/k-bot/stock_bot/logs/sector_monitor.log",
}
# ★ 2026-07-02: 고정 바이트 tail(_LOG_TAIL_BYTES) 방식은 로그가 적게 쌓이는
#   구간(장외 대기 등)에서 8000바이트가 10~20분치까지 덮어버려, 이미 지나간
#   1회성 rate-limit 에러가 여러 번의 1분 체크에서 계속 "감지"되며 스트릭이
#   허위로 쌓여 sbot이 불필요하게 자주 재시작되는 버그가 있었음. 각 봇의
#   마지막 읽은 오프셋을 기억해 그 이후 새로 추가된 부분만 읽도록 수정 —
#   이래야 진짜 "최근 1분" 신규 로그만 보게 된다.
_log_read_offset: dict[str, int] = {}


def _fetch_recent_log(bot_name: str) -> str:
    """최근 1분 로그를 봇의 실제 출처(journal 또는 파일)에 맞게 가져온다"""
    log_file = _BOT_LOG_FILE.get(bot_name)
    if log_file:
        # ★ 파일로 직접 출력하는 봇 — 마지막 체크 이후 새로 추가된 부분만 읽는다
        try:
            with open(log_file, "rb") as f:
                f.seek(0, os.SEEK_END)
                size = f.tell()
                last_offset = _log_read_offset.get(bot_name, size)
                if last_offset > size:
                    # 로그 로테이션/재생성 등으로 파일이 줄어든 경우 — 처음부터 다시 추적
                    last_offset = 0
                f.seek(last_offset, os.SEEK_SET)
                data = f.read()
                _log_read_offset[bot_name] = size
                return data.decode("utf-8", errors="ignore")
        except Exception as e:
            print(f"⚠️ [watchdog] {bot_name} 로그 파일 읽기 실패: {e}")
            return ""
    try:
        result = subprocess.run(
            ["journalctl", "-u", f"yeongam9-{bot_name}",
             "--since", "1 minute ago", "--no-pager"],
            capture_output=True, text=True, timeout=15,
        )
        return result.stdout
    except Exception as e:
        print(f"⚠️ [watchdog] {bot_name} 로그 조회 실패: {e}")
        return ""


@tasks.loop(minutes=1)
async def api_error_watchdog():
    now = datetime.datetime.now(KST)

    try:
        channel = await client.fetch_channel(REPORT_CHANNEL_ID)
    except Exception as e:
        print(f"❌ [watchdog] 채널 접속 실패: {e}")
        return

    for bot_name in WATCHDOG_BOTS:
        # 재시작 쿨다운 중이면 스킵 (재기동 직후 토큰 재발급/API 안정화 시간 확보)
        last_restart = _last_restart_at[bot_name]
        if last_restart and (now - last_restart).total_seconds() < RESTART_COOLDOWN_SECONDS:
            continue

        log_text = await asyncio.to_thread(_fetch_recent_log, bot_name)

        has_token_error = any(p in log_text for p in TOKEN_ERROR_PATTERNS)
        has_rate_limit_error = any(p in log_text for p in RATE_LIMIT_ERROR_PATTERNS)

        # ── 토큰/인증 에러 추적 ──────────────────────────
        if has_token_error:
            _token_error_streak[bot_name] += 1
            print(f"⚠️ [watchdog] {bot_name} 토큰 에러 감지 "
                  f"({_token_error_streak[bot_name]}/{TOKEN_ERROR_STREAK_THRESHOLD})")
        else:
            _token_error_streak[bot_name] = 0

        # ── rate limit 에러 추적 ─────────────────────────
        if has_rate_limit_error:
            _rate_limit_error_streak[bot_name] += 1
            print(f"⚠️ [watchdog] {bot_name} API 호출빈도 초과 감지 "
                  f"({_rate_limit_error_streak[bot_name]}/{RATE_LIMIT_ERROR_STREAK_THRESHOLD})")
        else:
            _rate_limit_error_streak[bot_name] = 0

        reason = None
        if _token_error_streak[bot_name] >= TOKEN_ERROR_STREAK_THRESHOLD:
            reason = "토큰/인증 오류 지속"
        elif _rate_limit_error_streak[bot_name] >= RATE_LIMIT_ERROR_STREAK_THRESHOLD:
            reason = "API 호출빈도 초과(잔고조회 실패→캐시) 지속"

        if reason:
            await send_safe_message(
                channel,
                f"🚨 **[watchdog] {bot_name} {reason}**\n"
                f"yeongam9-{bot_name} 재시작을 시도할게!"
            )
            try:
                ret = subprocess.run(
                    ["sudo", "systemctl", "restart", f"yeongam9-{bot_name}"],
                    capture_output=True, text=True, timeout=30,
                )
                if ret.returncode == 0:
                    await send_safe_message(channel, f"✅ {bot_name} 재시작 완료!")
                else:
                    err = (ret.stderr or "").strip()[:200]
                    await send_safe_message(channel, f"❌ {bot_name} 재시작 실패: {err}")
            except Exception as e:
                await send_safe_message(channel, f"❌ {bot_name} 재시작 오류: {e}")

            _token_error_streak[bot_name] = 0
            _rate_limit_error_streak[bot_name] = 0
            _last_restart_at[bot_name] = now


@api_error_watchdog.before_loop
async def before_api_error_watchdog():
    await client.wait_until_ready()

# ==========================================
# [메인 디스코드 코어 핸들러]
# ==========================================
@client.event
async def on_ready():
    init_finance_db()
    init_mapping_db()  # 💡 맵핑 DB 초기화 호출 추가 완료!
    
    print(f"==========================================")
    print(f"🦊 [v13 맵핑 DB & 수급 완전융합 3합 브리핑 가동]")
    print(f"==========================================")
    
    # ★ 2026-09-04: discord.py는 게이트웨이 세션이 무효화(invalidated)돼
    #   재-IDENTIFY하면 on_ready가 프로세스 중에 다시 호출될 수 있음.
    #   기존엔 매번 무조건 .start()를 걸어서, 이미 돌고 있던(정상 동작
    #   중인) 스케줄러마다 "Task is already launched" 에러가 우르르
    #   찍혀 실제 장애처럼 보였음(사용자가 로그 보고 놀라서 발견) —
    #   실제로는 최초 기동 때의 루프가 끊김 없이 계속 돌고 있어 기능
    #   장애는 아니었지만, 매번 이 노이즈가 재발하는 걸 막기 위해
    #   is_running() 가드 추가.
    try:
        if not daily_morning_report.is_running():
            daily_morning_report.start()
        print("✅ [시스템] 7시 30분 융합 브리핑 스케줄러 가동 성공!")
    except Exception as e: print(f"⚠️ [에러] 7시 30분 스케줄러: {e}")

    # ★ 2026-07-01: hourly_telegram_event_report 비활성화
    #   텔레그램 메시지는 이미 1분마다 DB에 수집/저장 중이므로
    #   매 시간 30분마다 자동으로 디스코드에 요약을 보낼 필요 없음.
    #   같은 내용이 12:30/13:30/14:30에 반복 전송되고, 14:30엔
    #   생쇼 브리핑과 겹쳐서 지저분해지는 문제가 있었음.
    #   필요할 때 !텔레요약 명령어로 조회하는 방식으로 대체.
    # try:
    #     hourly_telegram_event_report.start()
    #     print("✅ [시스템] 텔레그램 1분 감시 스케줄러 가동 성공!")
    # except Exception as e: print(f"⚠️ [에러] 텔레그램 스케줄러: {e}")

    try:
        if not daily_news_report.is_running():
            daily_news_report.start()
        print("✅ [시스템] 07시 뉴스 스케줄러 가동 성공!")
    except Exception as e: print(f"⚠️ [에러] 뉴스 스케줄러: {e}")

    try:
        if not daily_strategy_report.is_running():
            daily_strategy_report.start()
        print("✅ [시스템] 08:50 MBN 투자전략 요약 스케줄러 가동 성공!")
    except Exception as e: print(f"⚠️ [에러] 투자전략 스케줄러: {e}")

    try:
        if not daily_market_context_report.is_running():
            daily_market_context_report.start()
        print("✅ [시스템] 09:35 시장 쏠림 종합 브리핑 스케줄러 가동 성공! (관찰 전용)")
    except Exception as e: print(f"⚠️ [에러] 쏠림 브리핑 스케줄러: {e}")

    try:
        if not daily_momentum_am_report.is_running():
            daily_momentum_am_report.start()
        if not daily_momentum_pm_report.is_running():
            daily_momentum_pm_report.start()
        if not daily_momentum_checkin.is_running():
            daily_momentum_checkin.start()
        print("✅ [시스템] AI 모멘텀 스캐너(08:55/14:35) + 체크인(16:00) 스케줄러 가동 성공! (관찰 전용)")
    except Exception as e: print(f"⚠️ [에러] AI 모멘텀 스케줄러: {e}")

    try:
        if not kiwoom_pool_scan_loop.is_running():
            kiwoom_pool_scan_loop.start()
        print("✅ [시스템] 키움풀 스캔 스케줄러(09:30/12:30/15:00) 가동 성공! (관찰 전용)")
    except Exception as e: print(f"⚠️ [에러] 키움풀 스캔 스케줄러: {e}")

    try:
        if not daily_master_report.is_running():
            daily_master_report.start()
        print("✅ [시스템] 07:20 마스터 리포트 스케줄러 가동 성공!")
    except Exception as e: print(f"⚠️ [에러] 마스터 스케줄러: {e}")

    try:
        if not daily_tele_swing_report.is_running():
            daily_tele_swing_report.start()
        print("✅ [시스템] 07:50 텔레스윙 스케줄러 가동 성공!")
    except Exception as e: print(f"⚠️ [에러] 텔레스윙 스케줄러: {e}")

    try:
        if not daily_tele_swing_afternoon.is_running():
            daily_tele_swing_afternoon.start()
        print("✅ [시스템] 14:40 텔레스윙 오후 스케줄러 가동 성공!")
    except Exception as e: print(f"⚠️ [에러] 텔레스윙 오후 스케줄러: {e}")

    try:
        if not api_error_watchdog.is_running():
            api_error_watchdog.start()
        print("✅ [시스템] API 에러 watchdog (1분 주기, sbot/sbo2/sector) 가동 성공!")
    except Exception as e: print(f"⚠️ [에러] API watchdog 스케줄러: {e}")

@client.event
async def on_message(message):
    if message.author == client.user: return

    # 💡 [신규] 대장의 수동 맵핑 추가 명령어 (!맵핑)
    if message.content.startswith("!맵핑 "):
        try:
            parts = message.content.split(" ", 3)
            if len(parts) < 4:
                await send_safe_message(message.channel, "⚠️ 대장, 형식이 틀렸어! \n사용법: `!맵핑 [미국티커] [한국종목] [사유]`")
                return

            us_ticker = parts[1].upper()
            kr_name = parts[2]
            reason = parts[3]

            conn = sqlite3.connect(DB_PATH_MAPPING)
            cursor = conn.cursor()
            cursor.execute("INSERT INTO us_kr_mapping (us_ticker, us_name, kr_name, reason, is_static) VALUES (?, ?, ?, ?, 1)", 
                           (us_ticker, us_ticker, kr_name, reason))
            conn.commit()
            conn.close()

            await send_safe_message(message.channel, f"✅ **[맵핑 완벽 등록]** 대장! 🇺🇸`{us_ticker}` 관련주로 🇰🇷`{kr_name}` 녀석을 정식 DB에 꽂아뒀어!\n(사유: {reason})")
            print(f"💾 [DB 추가] {us_ticker} -> {kr_name}")
        except Exception as e:
            await send_safe_message(message.channel, f"❌ 앗, DB 저장 에러: {e}")
        return

    # ---------------------------------------------------------
    # 💡 [신규] 대장의 종목 테마 검색 명령어 (!테마)
    # ---------------------------------------------------------
    if message.content.startswith("!테마 "):
        try:
            search_term = message.content.replace("!테마 ", "").strip()
            
            conn = sqlite3.connect(DB_PATH_THEME_FINANCE)
            cursor = conn.cursor()
            
            cursor.execute("SELECT theme_name, stock_name FROM kr_theme_stocks WHERE stock_name LIKE ?", ('%' + search_term + '%',))
            results = cursor.fetchall()
            conn.close()
            
            if results:
                themes = list(set([r[0] for r in results]))
                found_stock = results[0][1] 
                
                report = f"🔍 **[테마 탐색기]** 대장! '{found_stock}'은(는) 이런 테마에 묶여있어!\n\n"
                report += "\n".join([f"- {t}" for t in themes])
                await send_safe_message(message.channel, report)
            else:
                await send_safe_message(message.channel, f"대장, '{search_term}'은(내) DB에 안 보이네! 오타 한번 확인해봐.")
        
        except Exception as e:
            await send_safe_message(message.channel, f"❌ 앗, 테마 찾다가 꼬였어: {e}")
        return
    
    # ---------------------------------------------------------
    # 💡 [신규] 대장의 수동 퀀트 엔진 호출 명령어 (!추천종목)
    # ---------------------------------------------------------
    if message.content.startswith("!추천종목"):
        async with message.channel.typing():
            try:
                # 41만 건 분석 모듈 호출 (Call)
                picks_report = quant_analyzer.get_hybrid_top_picks()
                
                # 결과 출력
                await send_safe_message(message.channel, picks_report)
                print("🎯 [명령어] 대장의 요청으로 41만 건 하이브리드 추천종목 송출 완료!")
            except Exception as e:
                await send_safe_message(message.channel, f"❌ 앗, 대장! 수급 데이터 분석하다가 꼬였어: {e}")
        return

    # ---------------------------------------------------------
    # 💡 [신규] 대장의 수동 스윙 엔진 호출 명령어 (!스윙)
    # --------------------------------------------------------
    if message.content.startswith("!스윙"):
        async with message.channel.typing():
            report = await asyncio.to_thread(get_swing_picks, 5)
            await send_safe_message(message.channel, report)
        return    

    # ---------------------------------------------------------
    # 💡 [신규] 대장의 수동 상승추세 엔진 호출 명령어 (!추세)
    # --------------------------------------------------------
    if message.content.startswith("!추세"):
        async with message.channel.typing():
            report = await asyncio.to_thread(get_trend_picks, 5)
            await send_safe_message(message.channel, report)
        return    

    # --------------------------------------------------------
    # 💡 [신규] 대장의 수동 3개 교집합 엔진 호출 명령어 (!마스터)
    # --------------------------------------------------------
    if message.content.startswith("!마스터"):
        async with message.channel.typing():
            report = await asyncio.to_thread(get_master_report, 5)
            await send_safe_message(message.channel, report)
        return

    # ── !쏠림 (수동 시장 쏠림 브리핑 확인용, 2026-07-07) ────────
    #   09:35 스케줄러와 동일한 _build_market_context_summary()를 즉시
    #   호출 — 시간 체크만 건너뛰고, 스냅샷 신선도(15분) 체크는 그대로
    #   적용됨. 수동 확인용이라 이력 DB엔 저장하지 않음.
    if message.content.startswith("!쏠림"):
        async with message.channel.typing():
            summary = await asyncio.to_thread(_build_market_context_summary)
            if summary:
                await send_safe_message(
                    message.channel,
                    f"📐 **[쏠림 브리핑 — 수동 확인]** 📐\n\n{summary}"
                )
            else:
                await send_safe_message(
                    message.channel,
                    "💤 쏠림 지수 스냅샷이 없거나 15분 이상 오래됐어 (장 시작 "
                    "직후이거나 cron 미실행일 수 있음)."
                )
        return

    # ── !모멘텀 (AI 모멘텀 스캐너 픽 이력 + 적중률 확인용, 2026-07-09) ──
    if message.content.startswith("!모멘텀"):
        async with message.channel.typing():
            import ai_momentum_db
            picks   = await asyncio.to_thread(ai_momentum_db.get_recent_picks, 10)
            pending = await asyncio.to_thread(ai_momentum_db.get_pending_with_current_price)
            stats   = await asyncio.to_thread(ai_momentum_db.get_momentum_stats)

            # 미결 픽은 (date, session, name)으로 실시간 현재가/수익률 매핑
            pending_map = {(p["date"], p["session"], p["name"]): p for p in pending}

            lines = ["🧭 **[AI 모멘텀 스캐너 — 최근 픽 + 적중률]** 🧭\n"]
            if picks:
                for p in picks:
                    key = (p["date"], p["session"], p["name"])
                    if p["result"] == "pending" and key in pending_map:
                        live = pending_map[key]
                        pct_str = (f"{live['current_pct']:+.1f}%"
                                   if live["current_pct"] is not None else "가격조회실패")
                        lines.append(
                            f"[{p['date']} {p['session']}] {p['name']} "
                            f"(⏳{live['checkin_label']}, 현재 {pct_str}) "
                            f"— {p['reasoning'][:60]}"
                        )
                    else:
                        result_tag = {"hit": "🎯적중", "stop": "🛑손절",
                                      "hold": "⏱️보합"}.get(p["result"], p["result"])
                        lines.append(
                            f"[{p['date']} {p['session']}] {p['name']} ({result_tag}) "
                            f"— {p['reasoning'][:60]}"
                        )
            else:
                lines.append("아직 픽 이력 없음.")
            lines.append(
                f"\n📊 최근 30일: 총 {stats['total']}건 "
                f"(적중 {stats['hit']} / 손절 {stats['stop']} / 보합 {stats['hold']}) "
                f"— 적중률 {stats['hit_rate']*100:.1f}%"
                + ("" if stats["sample_size_ok"] else " (표본 20건 미만, 참고만)")
            )
            await send_safe_message(message.channel, "\n".join(lines))
        return

    # ── !텔레스윙 ──────────────────────────────────────────────
    if message.content.startswith("!텔레스윙"):
        async with message.channel.typing():
            from tele_swing_analyzer import get_tele_swing_report
            report = await asyncio.to_thread(get_tele_swing_report, 3)
            await send_safe_message(message.channel, report)
        return

    # ── !상태 (sbo2 현재 보유종목) ─────────────────────────────
    if message.content.startswith("!상태"):
        async with message.channel.typing():
            try:
                # ★ 2026-09-03: 일반 open()+json.load()는 sbo2가 마침 그
                #   순간에 파일을 쓰고 있으면 JSONDecodeError로 튕길 수
                #   있어(재점검 리포트로 발견) — common_utils.py의 원자적
                #   read_state()로 교체(다른 곳도 오늘 다 이걸로 통일함).
                from common_utils import read_state as _cu_read_state
                state_file = os.path.join(base_dir, 'sbo2_state.json')
                if not os.path.exists(state_file):
                    await send_safe_message(message.channel, "⚠️ sbo2 상태파일 없어.")
                    return
                state = _cu_read_state(state_file, default={})
                positions = state.get("positions", {})

                from kis_api import KisAPI
                api = KisAPI()
                # 보유종목 기준 주문가능금액 조회
                psbl = 0
                for _code in list(positions.keys()):
                    psbl = api.get_psbl_order_cash(_code)
                    if psbl > 0:
                        break
                if psbl == 0:
                    psbl = api.get_buyable_cash() if hasattr(api, 'get_buyable_cash') else 0

                lines = [f"📊 **[sbo2 현재 상태]** [{datetime.datetime.now(KST).strftime('%H:%M:%S')}]"]
                lines.append(f"   💰 주문가능: {psbl:,}원")
                lines.append(f"   📦 보유종목: {len(positions)}개")

                total_pnl = 0
                for code, pos in positions.items():
                    mdata = api.get_market_data(code)
                    curr  = float(mdata.get("stck_prpr", 0)) if mdata else pos.get("entry_price", 0)
                    entry = pos.get("entry_price", 0)
                    qty   = pos.get("qty", 0)
                    rate  = (curr - entry) / entry * 100 if entry > 0 else 0
                    pnl   = (curr - entry) * qty
                    total_pnl += pnl
                    emoji = "📈" if rate > 0 else "📉"
                    lines.append(
                        f"   {emoji} {pos.get('name', code)}({code}) [{pos.get('grade','?')}] "
                        f"{rate:+.1f}% | {entry:,}→{curr:,}원 | {qty}주 | 손익:{int(pnl):,}원 "
                        f"🛑{pos.get('stop_price',0):,.0f} 🎯{pos.get('tgt_price',0):,.0f}"
                    )
                lines.append(f"   💵 총 평가손익: {int(total_pnl):,}원")
                await send_safe_message(message.channel, "\n".join(lines))
            except Exception as e:
                await send_safe_message(message.channel, f"❌ 상태 조회 오류: {e}")
        return

    # ── !성과 (sbo2 매매 이력) ─────────────────────────────────
    if message.content.startswith("!성과"):
        async with message.channel.typing():
            try:
                from sbo2 import get_trade_review
                days = 30
                parts = message.content.split()
                if len(parts) > 1 and parts[1].isdigit():
                    days = int(parts[1])
                report = get_trade_review(days)
                await send_safe_message(message.channel, f"📊 **[sbo2 성과]**\n\n{report}")
            except Exception as e:
                await send_safe_message(message.channel, f"❌ 성과 조회 오류: {e}")
        return

    # ── !전체성과 ─────────────────────────────────────────────
    if message.content.startswith("!전체성과"):
        async with message.channel.typing():
            try:
                import sqlite3
                master_db = os.path.join(base_dir, 'master_trades.db')
                if not os.path.exists(master_db):
                    await send_safe_message(message.channel, "⚠️ master_trades.db 없어.")
                    return

                conn   = sqlite3.connect(master_db)
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT bot_type, COUNT(*) as cnt,
                           SUM(CASE WHEN profit_rate > 0 THEN 1 ELSE 0 END) as wins,
                           ROUND(AVG(profit_rate), 2) as avg_rate,
                           ROUND(SUM(profit_krw), 0) as total_krw
                    FROM master_trades
                    GROUP BY bot_type
                    ORDER BY total_krw DESC
                """)
                rows = cursor.fetchall()
                conn.close()

                lines = ["📊 **[전체 봇 성과]**"]
                lines.append(f"{'봇':<8} {'거래':>5} {'승률':>7} {'평균':>7} {'총손익':>12}")
                lines.append("-" * 45)
                for bot, cnt, wins, avg, total in rows:
                    win_rate = wins / cnt * 100 if cnt > 0 else 0
                    emoji = "✅" if total > 0 else "❌"
                    lines.append(
                        f"{emoji} {bot:<6} {cnt:>5} {win_rate:>6.1f}% "
                        f"{avg:>+6.1f}% {int(total):>11,}원"
                    )
                await send_safe_message(message.channel, "\n".join(lines))
            except Exception as e:
                await send_safe_message(message.channel, f"❌ 전체성과 조회 오류: {e}")
        return

    # 🚨 다중 일정 추가 로직
    if message.content.startswith("!일정추가"):
        lines = message.content.split('\n')
        result_messages = []
        for line in lines:
            line = line.strip()
            if not line or line == "!일정추가": continue
            parts = line.replace("!일정추가", "").strip().split(" ", 1)
            if len(parts) == 2:
                res = add_google_calendar_event(parts[1], parts[0])
                result_messages.append(res)
            else:
                result_messages.append(f"⚠️ 형식 오류: '{line}' (YYYY-MM-DD 내용)")
        if result_messages: await message.channel.send("\n".join(result_messages))
        return

    user_input = message.content.replace(f'<@{client.user.id}>', '').strip()
    if not user_input: return

    is_dm = isinstance(message.channel, discord.DMChannel)
    is_called = is_dm or ("리na" in message.content or "리나" in message.content) or client.user.mentioned_in(message)
    if not is_called: return

    async with message.channel.typing():
        if any(kw in user_input for kw in ["원", "지출", "샀어", "보냈어"]) and any(c.isdigit() for c in user_input):
            num = re.findall(r'\d+', user_input)[0]
            item = re.sub(r'\d+', '', user_input.replace("리나야", "").replace("원", "").replace("샀어", "")).strip() or "기타"
            r_type = "입금" if "입금" in user_input else "출금"
            context_data = f"[시스템 가계부]: {add_finance_record(r_type, item, int(num))}"
            prompt = f"{context_data}\n\n질문: {user_input}\n친절하게 답해줘."
        else:
            context_data = await web_search_hybrid(user_input)
            
            if context_data and "실패" not in context_data and "텅 비어" not in context_data:
                if any(k in user_input for k in ["텔레", "속보"]):
                    지시문 = "제공된 텔레그램 속보를 각 뉴스당 '5줄 코드블록 포맷'으로 엄격하게 요약해."
                elif any(k in user_input for k in ["뉴스", "mbn", "아침"]):
                    지시문 = "수집된 실제 데이터(기사 내용)만을 바탕으로 다정하게 요약 보고해줘. 절대 지어내지 마."
                else:
                    지시문 = "수집된 실제 데이터를 바탕으로 대장에게 친절하게 요약해서 알려줘."

                prompt = f"[파이썬 실시간 수집 데이터]:\n{context_data}\n\n[사용자 질문]: {user_input}\n\n[지시문]: {지시문}"
            else:
                chat_memory.setdefault(message.channel.id, [{"role": "system", "content": SYSTEM_PROMPT}])
                chat_memory[message.channel.id].append({"role": "user", "content": user_input})
                prompt = user_input 

        payload = {"model": MODEL_NAME, "messages": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}], "stream": False, "options": {"temperature": 0.0}}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(OLLAMA_API_URL, json=payload) as response:
                    res_json = await response.json()
                    await send_safe_message(message.channel, res_json.get("message", {}).get("content", "에러 발생!").strip(), reply_to=message)
        except Exception as e:
            await message.reply(f"❌ 엔진 에러: {str(e)}")

if __name__ == "__main__":
    import sys
    if sys.platform == "win32":
        import asyncio
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    client.run(DISCORD_TOKEN)