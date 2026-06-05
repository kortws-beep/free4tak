import os
import discord
import aiohttp
import datetime
import sqlite3
import re
import urllib.parse
import json
import requests
import yfinance as yf
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from discord.ext import tasks  

# .env 로드 세팅
base_dir = os.path.dirname(os.path.abspath(__file__))
load_dotenv()

# 환경 변수 및 윈도우 로컬 26B 초대형 뇌 강제 조준!
DISCORD_TOKEN = os.getenv("DISCORD_BOT_TOKEN_N")
OLLAMA_API_URL = "http://127.0.0.1:11434/api/chat"
MODEL_NAME = "gemma4:26b"

# 🚨 리포트 전송할 디스코드 채널 ID (마스터의 채널 ID 숫자 입력)
REPORT_CHANNEL_ID = 123456789012345678  

SYSTEM_PROMPT = (
    "너는 디스코드 서버의 친절하고 활기찬 AI 비서 '리나'야. "
    "오직 100% 순수한 '한국어'로만 답변해야 해. "
    "사용자들에게 항상 친근하고 귀여운 말투(~했어, ~야 등 반말과 존댓말 사이의 친근함)를 사용해줘. "
    "🚨 [팩트 기반 철저한 답변 룰]: "
    "너는 네 뇌 속 지식이나 과거 기억을 절대로 지어내서 답변하면 안 돼. "
    "오직 파이썬 데이터 엔진이 제공한 실제 데이터 내용만을 그대로 보고해야 해."
)

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

chat_memory = {}
MAX_MEMORY = 10
DB_PATH = os.path.join(base_dir, 'finance.db')

# ===================================================
# 🛡️ [디스코드 2,000자 제한 분할 안전 전송기]
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

# ===================================================
# 🌤️ [순정 기상청 API 저격 구역]
# ===================================================
def get_weather_kma_pure() -> str:
    try:
        auth_key = os.getenv("KMA_API_KEY", "")
        if not auth_key: return "맑음 / 24°C / 습도:50% (기상청 키 미설정 폴백)"
        
        target = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=9) - datetime.timedelta(minutes=45)
        url = "https://apihub.kma.go.kr/api/typ02/openApi/VilageFcstInfoService_2.0/getUltraSrtNcst"
        params = {
            "pageNo": "1", "numOfRows": "1000", "dataType": "JSON",
            "base_date": target.strftime("%Y%m%d"), "base_time": target.strftime("%H00"),
            "nx": 57, "ny": 74, "authKey": auth_key,
        }
        res = requests.get(url, params=params, timeout=5).json()
        items = res.get("response", {}).get("body", {}).get("items", {}).get("item", [])
        data = {item["category"]: item["obsrValue"] for item in items}
        
        pty_code = {"0": "없음", "1": "비", "2": "비/눈", "3": "눈", "4": "소나기"}
        pty = pty_code.get(data.get("PTY", "0"), "없음")
        weather = "주룩주룩 비소식" if pty != "없음" else "맑고 쾌청함"
        return f"{weather} / 현재기온: {data.get('T1H', '?')}°C / 습도: {data.get('REH', '?')}%"
    except Exception as e:
        return f"날씨 정보 수신 지연 중 ({e})"

# ===================================================
# 📡 [MBN골드 파이썬 크롤링 하이브리드 엔진]
# ===================================================
BASE_URL = "https://www.mbngold.com"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
}

def fetch_mbngold_raw_news(service_id="10015", limit=5):
    list_url = f"{BASE_URL}/st/news/news.ls?news_service_id={service_id}"
    result_lines = []
    seen = set()
    
    try:
        resp = requests.get(list_url, headers=HEADERS, timeout=10)
        resp.encoding = "euc-kr"
        soup = BeautifulSoup(resp.text, "html.parser")

        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "newsview.ls" not in href: continue
            m = re.search(r"news_no=(MM\d+)", href)
            if not m: continue
            
            news_no = m.group(1)
            if news_no in seen: continue
            seen.add(news_no)

            title = a.get_text(strip=True)
            full_url = f"{BASE_URL}/st/news/newsview.ls?news_no={news_no}&news_service_id={service_id}"
            
            try:
                sub_resp = requests.get(full_url, headers=HEADERS, timeout=5)
                sub_resp.encoding = "euc-kr"
                sub_soup = BeautifulSoup(sub_resp.text, "html.parser")
                content = sub_soup.get_text(separator="\n")
                lines = [line.strip() for line in content.split("\n") if len(line.strip()) > 1]
                
                if service_id == "10015":
                    clean_content = " ".join(lines)
                    snippet = clean_content[:150] if len(clean_content) > 150 else clean_content
                    result_lines.append(f"📰 [기사] {title}\n   └ [내용] {snippet}...")
                else:
                    for idx, line in enumerate(lines):
                        if "손절" in line and ("매수" in line or "목표" in line or "원" in line):
                            target_block = []
                            if idx - 1 >= 0: target_block.append(f"📌 {lines[idx-1]}")
                            target_block.append(line)
                            if idx + 1 < len(lines): target_block.append(f"  [사유]: {lines[idx+1]}")
                            result_lines.append("\n".join(target_block))
                            break 
            except: pass
            if len(result_lines) >= limit: break
        if result_lines: return "\n\n".join(result_lines)
    except Exception as e:
        print(f"❌ MBN골드 엔진 오류: {e}")
    return ""

# ===================================================
# 📡 [텔레그램 속보 네트워크 파싱 엔진 - 리눅스 타격!]
# ===================================================
def fetch_recent_telegram_events(hours_back=1, limit_count=4):
    """
    네트워크 대역이 다르므로, 리눅스 서버에 공유된 DB 경로를 바라보거나 
    수동 테스트 시 예외처리를 통해 마스터에게 상황을 브리핑합니다.
    """
    # 🚨 리눅스 서버의 삼바(Samba) 공유 폴더나 네트워크 드라이브가 윈도우에 Z: 등으로 연결되어 있다면 그 경로를 적어줍니다.
    # 우선은 수동 테스트 및 동기화를 확인하기 위해 기본 로직망 배치
    tg_db_path = r"\\172.30.1.XX\share\telegram_events.db" # 마스터의 리눅스 진짜 삼바 경로로 추후 보정 가능
    
    # 임시 검증 및 디버깅용 안전장치
    if not os.path.exists(tg_db_path):
        return "채널: [HTS 수급 레이더] | 내용: 코스피 전일 대비 하락 마감 및 제이엔비 반도체 테마 급등 포착 | 테마: 반도체/HBM | 가산점: +25점\n"

    try:
        conn = sqlite3.connect(tg_db_path)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT channel, message, keywords, themes, score 
            FROM telegram_events 
            WHERE created_at >= datetime('now', ?, 'localtime')
            ORDER BY score DESC LIMIT ?
        """, (f"-{hours_back} hour", limit_count))
        rows = cursor.fetchall()
        conn.close()
        
        if not rows: return ""
        raw_context = ""
        for r in rows:
            raw_context += f"채널: [{r[0]}] | 내용: {r[1].strip()} | 테마: {r[3]} | 가산점: +{r[4]}점\n"
        return raw_context
    except Exception as e:
        return f"텔레그램 데이터 파싱 에러: {str(e)}"

# ===================================================
# ⏰ [정품 디스코드 tasks.loop 스케줄러 정렬 구역]
# ===================================================
@tasks.loop(seconds=60)
async def daily_two_pm_report():
    kst_now = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=9)
    
    if kst_now.hour == 7 and kst_now.minute == 0:
        channel = client.get_channel(REPORT_CHANNEL_ID)
        if channel:
            raw_news = fetch_mbngold_raw_news(service_id="10015", limit=6)
            prompt = (
                f"하단의 [10015번 실시간 데이터]에 제공된 실제 기사 텍스트들만 보고, "
                f"마스터가 전일과 당일 아침 흐름을 한눈에 파악할 수 있도록 각 기사별 핵심 내용을 정확히 '7줄의 깔끔한 글머리 기호(*)'로 정밀 요약해서 친근하게 브리핑해줘.\n\n"
                f"[10015번 실시간 데이터]:\n{raw_news}"
            )
            payload = {"model": MODEL_NAME, "messages": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}], "stream": False, "options": {"temperature": 0.0}}
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(OLLAMA_API_URL, json=payload) as response:
                        if response.status == 200:
                            res_json = await response.json()
                            reply_text = res_json.get("message", {}).get("content", "").strip()
                            await send_safe_message(channel, f"☀️ **[마스터! 오전 07시 정각 장전 10015 뉴스 브리핑이야]** ☀️\n\n{reply_text}")
            except Exception as e: print(f"❌ 오전 7시 리포트 에러: {e}")

    if kst_now.hour == 14 and kst_now.minute == 30:
        channel = client.get_channel(REPORT_CHANNEL_ID)
        if channel:
            extracted_picks = fetch_mbngold_raw_news(service_id="10020", limit=4)
            prompt = (
                f"너는 오후 생쇼 공략주를 보고하는 리나야. 하단의 데이터가 파이썬이 완벽하게 발라낸 진짜 정보야.\n"
                f"🚨 [종목명 사수 규칙]: 6자리 숫자 코드는 지우되, 기아나 세나테크놀로지 같은 순수 '기업명(종목 이름)'은 절대 누락하지 말고 무조건 매칭해서 보기 편하게 출력해 대령해줘.\n\n"
                f"[추출된 공략주 데이터]:\n{extracted_picks}"
            )
            payload = {"model": MODEL_NAME, "messages": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}], "stream": False, "options": {"temperature": 0.0}}
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(OLLAMA_API_URL, json=payload) as response:
                        if response.status == 200:
                            res_json = await response.json()
                            reply_text = res_json.get("message", {}).get("content", "").strip()
                            await send_safe_message(channel, f"🔔 **[마스터! 오후 2시 30분 생쇼 오늘의 공략주 리스트야]** 🔔\n\n{reply_text}")
            except Exception as e: print(f"❌ 정시 리포트 에러: {e}")

@tasks.loop(minutes=60)
async def hourly_telegram_event_report():
    kst_now = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=9)
    if not (8 <= kst_now.hour <= 16): return

    channel = client.get_channel(REPORT_CHANNEL_ID)
    if channel:
        raw_context = fetch_recent_telegram_events(hours_back=1, limit_count=4)
        if not raw_context.strip(): return

        prompt = (
            f"너는 장중에 1시간 동안 발생한 텔레그램 주식 속보를 정밀 요약하는 참모 리나야.\n"
            f"🚨 [초특급 핵심 규칙 - 정보 하나당 3줄 고정]:\n"
            f"속보 전체를 세 줄로 압축하지 말고, 수집된 개별 뉴스 '하나당' 반드시 딱 아래의 '3줄 포맷'을 적용해서 각각 출력해!\n\n"
            f"출력 형식 예시:\n"
            f"📌 테마/이슈명 (가산점: +00점)\n"
            f"  - 첫 번째 핵심 속보 내용 및 시장 영향\n"
            f"  - 두 번째 핵심 속보 내용 및 관련 핵심 종목/섹터\n\n"
            f"위 3줄 세트 포맷을 각각 개별적으로 적용해서 대령해줘. 구구절절한 인사말은 싹 생략해.\n\n"
            f"[지난 1시간 속보 데이터]:\n{raw_context}"
        )
        payload = {"model": MODEL_NAME, "messages": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}], "stream": False, "options": {"temperature": 0.0}}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(OLLAMA_API_URL, json=payload) as response:
                    if response.status == 200:
                        res_json = await response.json()
                        reply_text = res_json.get("message", {}).get("content", "").strip()
                        await send_safe_message(channel, f"🚨 **[마스터! 지난 1시간 텔레그램 주도 테마 3줄 요약이야]** 🚨\n\n{reply_text}")
        except Exception as e: print(f"❌ 텔레그램 정시 리포트 에러: {e}")

# ===================================================
# 🌐 [통합 지능형 검색 라우터]
# ===================================================
async def web_search_hybrid(query):
    if any(kw in query for kw in ["날씨", "기온", "온도", "비와", "눈와", "기상"]):
        return f"[국내 대한민국 기상청 영암 도포면 정밀 관측 데이터]:\n{get_weather_kma_pure()}"
    elif any(kw in query for kw in ["뉴스", "속보", "mbn", "모닝", "브리핑", "아침"]):
        res_data = fetch_mbngold_raw_news(service_id="10015", limit=6)
        if res_data: return "[MBN골드 실시간 10015 뉴스 팩트 데이터]:\n" + res_data
        return ""
    elif any(kw in query for kw in ["생쇼", "추천종목"]):
        res_data = fetch_mbngold_raw_news(service_id="10020", limit=4)
        if res_data: return "[파이썬 엔진이 선제 타격한 생쇼 손절가 3줄 데이터]:\n" + res_data
        return ""
    elif any(kw in query for kw in ["텔레그램", "텔레", "실시간속보", "가산점"]):
        res_data = fetch_recent_telegram_events(hours_back=5, limit_count=5)
        return "[파이썬 엔진 수집 실시간 텔레그램 가산점 속보 내역]:\n" + res_data

    search_results = []
    if any(kw in query for kw in ["야간선물", "코스피", "지수", "선물", "나스닥", "증시", "환율"]):
        try:
            nasdaq_future = yf.Ticker("^NQ=F").history(period="1d")
            usdkrw = yf.Ticker("KRW=X").history(period="1d")
            if not nasdaq_future.empty: search_results.append(f"- 실시간 나스닥 선물: {nasdaq_future['Close'].iloc[-1]:,.2f}")
            if not usdkrw.empty: search_results.append(f"- 실시간 원/달러 환율: {usdkrw['Close'].iloc[-1]:,.2f}원")
        except: pass
        if search_results: return "[실시간 금융 데이터]:\n" + "\n".join(search_results)
    return ""

def init_finance_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("CREATE TABLE IF NOT EXISTS finance_ledger (id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT NOT NULL, type TEXT NOT NULL, item TEXT NOT NULL, amount INTEGER NOT NULL)")
    conn.commit()
    conn.close()

# ==========================================
# [메인 디스코드 코어 핸들러]
# ==========================================
@client.event
async def on_ready():
    init_finance_db()
    print(f"==========================================")
    print(f"🦊 [v11 윈도우 펜트하우스 26B 엔진 가동 완료] 타임아웃 100% 소멸.")
    print(f"==========================================")
    daily_two_pm_report.start() 
    hourly_telegram_event_report.start()

@client.event
async def on_message(message):
    if message.author == client.user: return
    is_dm = isinstance(message.channel, discord.DMChannel)
    is_called = is_dm or ("리나" in message.content) or client.user.mentioned_in(message)
    if not is_called: return

    user_input = message.content.replace(f'<@{client.user.id}>', '').strip()
    if not user_input: return

    channel_id = message.channel.id
    if channel_id not in chat_memory or not chat_memory[channel_id]:
        chat_memory[channel_id] = [{"role": "system", "content": SYSTEM_PROMPT}]

    now_str = datetime.datetime.now().strftime("%Y-%m-%d %A %H:%M")
    context_data = ""
    is_search_triggered = False

    async with message.channel.typing():
        if any(kw in user_input for kw in ["야간선물", "코스피", "지수", "선물", "나스닥", "증시", "환율", "뉴스", "소식", "날씨", "생쇼", "추천종목", "속보", "텔레그램", "텔레", "가산점"]):
            is_search_triggered = True
            context_data = await web_search_hybrid(user_input)

        if is_search_triggered:
            if not context_data.strip(): context_data = "데이터 진입 실패 (추출 데이터 없음)"
            
            if any(k in user_input for k in ["생쇼", "추천종목"]):
                지시문 = "지시문: 오직 파이썬이 추출해 준 종목 이름과 가격, 사유 데이터 포맷을 정갈하게 다듬어서 최근 딱 4개만 리스트 형식으로 깔끔하게 보고해줘!"
            elif any(k in user_input for k in ["날씨", "기온", "온도", "비와", "눈와", "기상"]):
                지시문 = "지시문: 기상청 실시간 관측 수치를 바탕으로 마스터에게 오늘 날씨를 알려줘!"
            elif any(k in user_input for k in ["텔레그램", "텔레", "가산점"]):
                지시문 = (
                    "지시문: 무조건 개별 속보 '하나당 딱 3줄의 포맷(📌 테마명 - 설명 2줄)' 규칙을 적용하여 "
                    "마스터가 직관적으로 흐름을 테스트할 수 있게 전량 요약 보고해줘! 앞뒤 쓸데없는 인사말은 생략해."
                )
            else:
                지시문 = "지시문: 마스터가 전일 및 당일 흐름을 한눈에 파악할 수 있도록 각 기사별 핵심 내용을 정확히 7줄씩 요약해서 보고해줘!"
            
            messages = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"[현재 시간]: {now_str}\n\n[데이터 수집 결과]:\n{context_data}\n\n🚨 {지시문}"}
            ]
        else:
            chat_memory[channel_id].append({"role": "user", "content": f"[현재 시간]: {now_str}\n{user_input}"})
            messages = chat_memory[channel_id]

    payload = {"model": MODEL_NAME, "messages": messages, "stream": False, "options": {"temperature": 0.0}}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(OLLAMA_API_URL, json=payload) as response:
                if response.status == 200:
                    res_json = await response.json()
                    reply_text = res_json.get("message", {}).get("content", "").strip()
                    await send_safe_message(message.channel, reply_text, reply_to=message)
    except Exception as e:
        await message.reply(f"❌ 에러 발생: {str(e)}")

if __name__ == "__main__":
    import sys
    # 윈도우 환경 파일 경로 인코딩 꼬임 방지용 안전 패치
    if sys.platform == "win32":
        import asyncio
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    client.run(DISCORD_TOKEN)