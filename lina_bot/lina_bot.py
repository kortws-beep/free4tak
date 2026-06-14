import asyncio
import os
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

# 환경 변수 및 모델 세팅
DISCORD_TOKEN = os.getenv("DISCORD_BOT_TOKEN_N")
OLLAMA_API_URL = os.getenv("OLLAMA_API_URL", "http://127.0.0.1:11434/api/chat")
MODEL_NAME = os.getenv("MODEL_NAME", "gemma4:e4b")

# 🚨 대한민국 표준시(KST) 타임존
KST = datetime.timezone(datetime.timedelta(hours=9))

# 💡 리나의 텔레그램 중복 방지용 단기 기억 장치
LAST_TELEGRAM_CONTEXT = ""

# 🚨 리포트 전송할 디스코드 채널 ID 및 DB 경로
REPORT_CHANNEL_ID = 1508487747508240525 
DB_PATH_TELEGRAM = os.path.join(base_dir, "intelligence", "telegram_events.db")
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
    if len(text) <= 1900:
        if reply_to: await reply_to.reply(text)
        else: await target.send(text)
        return
    lines = text.split('\n')
    chunk = ""
    for line in lines:
        if len(chunk) + len(line) + 1 > 1900:
            if reply_to:
                await reply_to.reply(chunk)
                reply_to = None
            else:
                await target.send(chunk)
            chunk = line + '\n'
        else:
            chunk += line + '\n'
    if chunk.strip():
        if reply_to: await reply_to.reply(chunk)
        else: await target.send(chunk)

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
    search_results = []
    base_url = "https://www.mbngold.com"
    list_url = f"{base_url}/st/news/news.ls?news_service_id={service_id}"
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.mbngold.com/"}
    
    try:
        async with AsyncSession() as session:
            res = await session.get(list_url, headers=headers, impersonate="chrome", timeout=10)
        
        if res.status_code == 200:
            html_text = res.content.decode('euc-kr', errors='ignore')
            soup = BeautifulSoup(html_text, 'html.parser')
            
            links = []
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if "newsview.ls" in href:
                    m = re.search(r"news_no=(MM\d+)", href)
                    if m:
                        news_no = m.group(1)
                        if news_no not in [l[0] for l in links]:
                            title = a.get_text(strip=True)
                            links.append((news_no, title))
                            if len(links) >= limit: break
            
            async with AsyncSession() as session:
                for news_no, title in links:
                    full_url = f"{base_url}/st/news/newsview.ls?news_no={news_no}&news_service_id={service_id}"
                    try:
                        sub_res = await session.get(full_url, headers=headers, impersonate="chrome", timeout=5)
                        sub_soup = BeautifulSoup(sub_res.content.decode('euc-kr', errors='ignore'), "html.parser")
                        
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
                
    except Exception as e: 
        print(f"❌ MBN골드 접속 에러: {e}")
        
    return "텅 비어 있어. (MBN골드 사이트 지연 또는 오늘자 업데이트 없음)"

def fetch_recent_telegram_events(limit_count=4):
    try:
        conn = sqlite3.connect(DB_PATH_TELEGRAM, timeout=10)
        cursor = conn.cursor()
        query = """
            SELECT channel, message, keywords, themes, score 
            FROM telegram_events 
            ORDER BY id DESC 
            LIMIT ?
        """
        cursor.execute(query, (limit_count,))
        rows = cursor.fetchall()
        conn.close()
        
        if not rows: return "대장, 데이터베이스에 수집된 속보가 아예 비어있네!"
            
        raw_context = ""
        for r in rows:
            msg = str(r[1] or "").strip().replace("<0xED><0x8C><0xB9>리스", "팹리스")
            kw = ", ".join(json.loads(r[2])) if r[2] else "없음"
            raw_context += f"채널: [{r[0]}] | 내용: {msg} | 키워드: {kw} | 가산점: +{r[4] or 10}점\n\n"
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
    if any(kw in query for kw in ["생쇼", "추천종목"]): return "[생쇼 공략주]:\n" + await fetch_mbngold_async("10020", 4)
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

# 2. 오후 2시 30분 생쇼 관심종목 루프
@tasks.loop(minutes=1)
async def daily_afternoon_report():
    kst_now = datetime.datetime.now(KST)
    if kst_now.hour != 14 or kst_now.minute != 30:
        return
        
    print(f"\n🔔 [디버그] {kst_now.strftime('%H:%M')} 생쇼 브리핑 출발! 채널 접속 중...")
    
    try:
        channel = await client.fetch_channel(REPORT_CHANNEL_ID)
    except Exception as e:
        print(f"❌ [디버그 에러] 14시 30분 채널 접속 실패: {e}")
        return

    extracted_picks = await fetch_mbngold_async(service_id="10020", limit=4)
    if not extracted_picks or "텅 비어" in extracted_picks: return

    # 생쇼 DB 저장
    try:
        from sshow_db import save_sshow_picks
        saved = save_sshow_picks(extracted_picks)
        print(f"💾 생쇼 DB 저장: {saved}건")
    except Exception as e:
        print(f"⚠️ 생쇼 DB 저장 오류: {e}")

    prompt = (
        f"너는 오후 생쇼 공략주를 보고하는 리나야.\n"
        f"🚨 **'종목명(코드번호 생략가능)'**와 **'핵심 공략 사유'**만 칼같이 리스트로 만들어서 대령해줘.\n\n"
        f"[추출된 공략주 데이터]:\n{extracted_picks}"
    )
    payload = {"model": MODEL_NAME, "messages": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}], "stream": False, "options": {"temperature": 0.0}}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(OLLAMA_API_URL, json=payload) as response:
                if response.status == 200:
                    res_json = await response.json()
                    reply_text = res_json.get("message", {}).get("content", "").strip()
                    await send_safe_message(channel, f"🔔 **[대장! 오후 2시 30분 생쇼 관심종목 배달이야]** 🔔\n\n{reply_text}")
                    print(f"✅ [디버그] 14시 30분 생쇼 브리핑 전송 완료!")
    except Exception as e: print(f"❌ 생쇼 리포트 에러: {e}")

@daily_afternoon_report.before_loop
async def before_daily_afternoon_report():
    await client.wait_until_ready()

# 3. 매 시간 30분 텔레그램 속보 루프 
@tasks.loop(minutes=1)
async def hourly_telegram_event_report():
    global LAST_TELEGRAM_CONTEXT
    kst_now = datetime.datetime.now(KST)
    
    if kst_now.minute != 30:
        return

    print(f"\n🚀 [디버그] {kst_now.strftime('%H:%M')} 텔레그램 루프 출발! 채널 접속 중...")

    try:
        channel = await client.fetch_channel(REPORT_CHANNEL_ID)
    except Exception as e:
        print(f"❌ [디버그 에러] 텔레그램 채널 접속 실패: {e}")
        return

    raw_context = fetch_recent_telegram_events(limit_count=4)
    if not raw_context.strip() or "비어있네" in raw_context: return

    if raw_context == LAST_TELEGRAM_CONTEXT:
        print(f"💤 [디버그] 새로운 텔레그램 속보가 없어서 브리핑을 건너뜁니다! (중복 방지)")
        return
        
    LAST_TELEGRAM_CONTEXT = raw_context

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

# 5. 07시 20분 스윙 마스터 리포트 루프
@tasks.loop(minutes=1)
async def daily_master_report():
    kst_now = datetime.datetime.now(KST)
    if kst_now.hour != 7 or kst_now.minute != 20:
        return

    print(f"\n🎯 [{kst_now.strftime('%H:%M')}] 스윙 마스터 리포트 가동!")
    try:
        channel = await client.fetch_channel(REPORT_CHANNEL_ID)
    except Exception as e:
        print(f"❌ 마스터 채널 접속 실패: {e}")
        return

    try:
        master_report = await asyncio.to_thread(get_master_report, 3)
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
    print(f"\n📡 [{kst_now.strftime('%H:%M')}] 텔레스윙 오후 재기동!")
    try:
        channel = await client.fetch_channel(REPORT_CHANNEL_ID)
        from tele_swing_analyzer import get_tele_swing_report
        report = await asyncio.to_thread(get_tele_swing_report, 3)
        await send_safe_message(channel, f"📡 **[대장! 14:40 텔레스윙 업데이트]** 📡\n\n{report}")
        print("✅ 14:40 텔레스윙 전송 완료!")
    except Exception as e:
        print(f"❌ 텔레스윙 오후 오류: {e}")

@daily_tele_swing_afternoon.before_loop
async def before_daily_tele_swing_afternoon():
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
    
    try:
        daily_morning_report.start() 
        print("✅ [시스템] 7시 30분 융합 브리핑 스케줄러 가동 성공!")
    except Exception as e: print(f"⚠️ [에러] 7시 30분 스케줄러: {e}")

    try:
        daily_afternoon_report.start()
        print("✅ [시스템] 14시 30분 생쇼 스케줄러 가동 성공!")
    except Exception as e: print(f"⚠️ [에러] 생쇼 스케줄러: {e}")

    try:
        hourly_telegram_event_report.start()
        print("✅ [시스템] 텔레그램 1분 감시 스케줄러 가동 성공!")
    except Exception as e: print(f"⚠️ [에러] 텔레그램 스케줄러: {e}")

    try:
        daily_news_report.start()
        print("✅ [시스템] 07시 뉴스 스케줄러 가동 성공!")
    except Exception as e: print(f"⚠️ [에러] 뉴스 스케줄러: {e}")

    try:
        daily_master_report.start()
        print("✅ [시스템] 07:20 마스터 리포트 스케줄러 가동 성공!")
    except Exception as e: print(f"⚠️ [에러] 마스터 스케줄러: {e}")

    try:
        daily_tele_swing_report.start()
        print("✅ [시스템] 07:50 텔레스윙 스케줄러 가동 성공!")
    except Exception as e: print(f"⚠️ [에러] 텔레스윙 스케줄러: {e}")

    try:
        daily_tele_swing_afternoon.start()
        print("✅ [시스템] 14:40 텔레스윙 오후 스케줄러 가동 성공!")
    except Exception as e: print(f"⚠️ [에러] 텔레스윙 오후 스케줄러: {e}")

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
                import json
                state_file = os.path.join(base_dir, 'sbo2_state.json')
                if not os.path.exists(state_file):
                    await send_safe_message(message.channel, "⚠️ sbo2 상태파일 없어.")
                    return
                with open(state_file, 'r', encoding='utf-8') as f:
                    state = json.load(f)
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
                elif any(k in user_input for k in ["생쇼"]):
                    지시문 = "추출된 공략주 데이터를 깔끔한 리스트로 정리해."
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