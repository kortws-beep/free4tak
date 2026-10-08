import asyncio
import os
import subprocess
import discord
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

# ★ 2026-10-03: !리나등록 자연어버전("등록 종목명")의 이름→코드 변환용.
from candidate_pool import get_stock_code, get_stock_name

# 환경 변수 및 모델 세팅
DISCORD_TOKEN = os.getenv("DISCORD_BOT_TOKEN_N")

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
            # ★ 2026-10-06 — 캐시를 안 건드리면 날짜가 안 바뀌어서 "어제
            #   (혹은 그 전 마지막 성공 시점)" 값이 그대로 남는데, 어제가
            #   휴장일이었으면 오늘도 휴장으로 오판해 리포트/트레일링
            #   감시가 통째로 꺼질 수 있었음(형제 Opus 리뷰로 발견).
            #   판단 자체가 불가능한 상태라 "닫혔다"는 근거도 없으므로
            #   보수적으로 "열려있다"로 간주 — 다음 호출에서 재시도되어
            #   금방 정확한 값으로 갱신됨(캐시는 안 건드리므로 안전).
            return True
        else:
            _TRADING_DAY_CACHE["is_open"] = _open
            _TRADING_DAY_CACHE["date"]    = today
    return _TRADING_DAY_CACHE["is_open"]

# 🚨 리포트 전송할 디스코드 채널 ID 및 DB 경로
REPORT_CHANNEL_ID = 1508487747508240525
# ★ 2026-10-06 대장: "리나가 보내는 게 너무 많다" — 검색식 관찰 알림(3개월수급·
#   주도주3·단타000)은 별도 채널로. .env의 LINA_SCAN_CHANNEL_ID가 없으면 기존 채널.
SCAN_CHANNEL_ID = int(os.getenv("LINA_SCAN_CHANNEL_ID") or REPORT_CHANNEL_ID)
# ★ 2026-10-06 — on_message()가 message.author == client.user(봇 자기
#   자신)만 걸러내고 있어서, 서버 멤버나 봇에게 DM 보낸 누구나 !상태
#   (계좌 잔고/보유종목 노출)/!리나등록/!일정추가 등을 쓸 수 있었음
#   (형제 Opus 리뷰로 발견). 대장 본인 디스코드 user ID만 허용.
OWNER_DISCORD_ID = 1485623383197487237
DB_PATH_CONCENTRATION = os.path.join(os.path.dirname(base_dir), "intelligence", "market_concentration.db")
DB_PATH_FINANCE = os.path.join(base_dir, 'finance.db')
DB_PATH_MAPPING = os.path.join(base_dir, 'us_kr_mapping.db')  # 💡 신규 맵핑 DB 경로
DB_PATH_THEME_FINANCE = os.path.join(base_dir, 'kr_theme_finance.db')
SCOPES = ['https://www.googleapis.com/auth/calendar']

# ============================================================
# 수동매수 트레일링 알림 (!리나등록, 2026-10-03 대장 지정)
# ============================================================
# ★ 2026-10-03 대장 재지정 — 최초엔 daybot과 동일값(+2.5%/-2.0%)으로
#   시작했는데, 같은 날 "3%부터 트레일링 가동, 고점대비 2.5% 밀리면
#   알림"으로 독자값 확정(daybot보다 더 큰 변동폭 허용 — 대장이 직접
#   차트보고 손절 여부까지 판단하는 수동종목이라 daybot보다 여유를 둠).
MANUAL_WATCH_TAKE_PROFIT_PCT  = 3.0
MANUAL_WATCH_TRAILING_STOP_PCT = 2.5
# ★ 2026-10-03 대장 지정(daybot 트레일링 2단 티어링과 동일 아이디어) —
#   고정 2.5% 트레일링만 쓰면 +3.0%에 겨우 턱걸이 후 바로 밀릴 때
#   +0.5%(수수료 공제 후 +0.27%)에서 알림이 나가 사실상 본전치기였음.
#   +3.0~5.0% 구간은 1.5%로 타이트하게 잡아 확정 수익을 더 챙기고,
#   +5.0% 초과 급등은 기존 2.5%로 여유를 준다. 매도는 절대 자동 실행
#   안 하므로(알림만) daybot과 달리 백테스트 검증 없이 바로 적용.
MANUAL_WATCH_TRAILING_STOP_PCT_TIGHT = 1.5
MANUAL_WATCH_TRAILING_STOP_WIDEN_PCT = 5.0
MANUAL_WATCH_MIN_LOCKED_PROFIT_PCT   = 1.0
MANUAL_WATCH_STATE_FILE = os.path.join(base_dir, "manual_watch_state.json")

# ★ 2026-10-03 대장 지정 — 실거래 3건(하이젠알앤엠/스피어/한빛레이저)을
#   대장의 실제 MTS 수익률과 대조해 역산: 가격기준 등락률과 실제 수익률
#   차이가 세 종목 다 0.218~0.229%(평균 0.224%)로 거의 일정 — 매수+매도
#   수수료에 매도시 증권거래세까지 합친 왕복비용으로 추정. 트레일링
#   발동/정지 "판단"은 원래 가격등락률 그대로 쓰고(차트가 보여주는
#   움직임 기준), 사용자에게 "보여주는" 수익률(등록현황/수익실현 리포트)
#   에만 이 상수를 차감해 실제 체감 수익률에 가깝게 보정한다.
MANUAL_WATCH_FEE_DRAG_PCT = 0.224


def _load_manual_watches() -> dict:
    from common_utils import read_state
    return read_state(MANUAL_WATCH_STATE_FILE, default={})


def _save_manual_watches(watches: dict):
    from common_utils import write_state
    write_state(MANUAL_WATCH_STATE_FILE, watches)


def _save_manual_watch_updates(watches: dict, changed_codes: set):
    """★ 2026-10-06 — manual_watch_trailing_loop는 루프 시작 시점의 watches
    스냅샷을 1분 내내 들고 있다가(여러 종목 순회+await) 끝에 통째로
    저장하는데, 그 사이 !리나등록/!리나등록해제가 같은 파일을 건드리면
    옛날 스냅샷으로 덮어써버려 방금 해제한 종목이 되살아나거나 새
    등록이 사라지는 경합이 있었음(대장 지적, 형제 Opus 리뷰로 발견).
    저장 직전에 최신 상태를 다시 읽어(락 보호) 이번 루프에서 실제로
    바뀐 종목(changed_codes)만 병합 — 그 사이 해제된 종목은 latest에
    이미 없으니 되살리지 않고, 그 사이 새로 등록된 종목은 건드리지
    않아 그대로 보존된다."""
    from common_utils import _state_lock, _read_state_raw, _write_state_raw
    with _state_lock(MANUAL_WATCH_STATE_FILE):
        latest = _read_state_raw(MANUAL_WATCH_STATE_FILE, {})
        for code in changed_codes:
            if code in latest:
                latest[code] = watches[code]
        _write_state_raw(MANUAL_WATCH_STATE_FILE, latest)


def _log_manual_watch(event: str, code: str, w: dict, sell_price: float = None,
                      profit_rate: float = None):
    """★ 2026-10-07 — 등록/해제 이력(manual_watch_log). 해제하면 상태파일에서
    지워져 기록이 사라졌음 — 대장 수동매매를 그날 검색식·섹터 신호와 맞춰보려면
    (나중 분석용) 언제 얼마에 들어가고 나왔는지가 남아 있어야 함. 실패해도 무시."""
    try:
        import three_month_leader as tml
        conn = sqlite3.connect(tml.LOG_DB, timeout=10)
        try:
            conn.execute("""CREATE TABLE IF NOT EXISTS manual_watch_log (
                ts TEXT, event TEXT, code TEXT, name TEXT, entry_price REAL,
                sell_price REAL, profit_rate REAL, peak_price REAL, registered_at TEXT)""")
            conn.execute("INSERT INTO manual_watch_log VALUES (?,?,?,?,?,?,?,?,?)", (
                datetime.datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S"), event, code,
                w.get("name"), w.get("entry_price"), sell_price, profit_rate,
                w.get("peak_price"), w.get("registered_at")))
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        print(f"⚠️ [리나등록] 이력 기록 실패: {e}")


async def _register_manual_watch(channel, code: str, buy_price: float = None,
                                  name_override: str = None):
    """!리나등록/자연어("등록 종목명 [평단가]") 공용 등록 로직.
    ★ 2026-10-03 대장 지정 — 분할매수를 하기 때문에 중간 금액으로는
    등록 못 함(최종 매수 완료 후에 등록). 그래서 "매수금액" 대신
    "주당 평균매입가"를 받는다 — 트레일링 추적과 수익률 계산 둘 다
    이 가격 하나로 처리(% 계산은 금액이든 가격이든 결과가 같음).
    평단가 생략하면 현재가로 등록(트레일링 추적만, 해제시 수익률은
    등록시점 대비로 계산됨 — 평단가 정확히 모를 때용)."""
    async with channel.typing():
        try:
            from kis_api import KisAPI
            api = KisAPI()
            if buy_price is None:
                # ★ 2026-10-06 — 동기 KIS 호출을 await 없이 직접 불러
                #   이벤트루프를 블로킹하던 부분(형제 Opus 리뷰로 발견).
                mdata = await asyncio.to_thread(api.get_market_data, code) or {}
                buy_price = float(mdata.get("stck_prpr", 0) or 0)
            if buy_price <= 0:
                await send_safe_message(channel, f"❌ {code} 현재가 조회 실패 — 평단가를 직접 입력해줘.")
                return
            name = name_override or get_stock_name(code)

            watches = _load_manual_watches()
            watches[code] = {
                "name": name, "entry_price": buy_price, "peak_price": None,
                "last_alert_peak": 0,
                "registered_at": datetime.datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S"),
            }
            _save_manual_watches(watches)
            _log_manual_watch("register", code, watches[code])
            await send_safe_message(
                channel,
                f"✅ {name}({code}) 등록 완료 — 평단가 {buy_price:,.0f}원\n"
                f"   +{MANUAL_WATCH_TAKE_PROFIT_PCT}% 찍으면 트레일링 추적 시작, "
                f"고점대비 -{MANUAL_WATCH_TRAILING_STOP_PCT_TIGHT}%(고점+{MANUAL_WATCH_TRAILING_STOP_WIDEN_PCT}% "
                f"초과시 -{MANUAL_WATCH_TRAILING_STOP_PCT}%) 밀리면 알려줄게(계속 감시).\n"
                f"   현재 등록: {len(watches)}종목(`등록현황`으로 확인) — 해제는 `!리나등록해제 {code}` 또는 `해제 {name}`"
            )
        except Exception as e:
            await send_safe_message(channel, f"❌ 등록 오류: {e}")


async def _deregister_manual_watch(channel, code: str, sell_price: float = None):
    """!리나등록해제/자연어("해제 종목명 [매도가]") 공용 해제 로직.
    매도가가 주어지면 등록시 평단가 대비 수익률을 바로 계산해서
    "종목명 ±N% 수익실현"으로 보고(대장 지정 — 분할매도도 고려해 가격
    기준, 금액 기준 아님)."""
    watches = _load_manual_watches()
    if code not in watches:
        await send_safe_message(channel, f"⚠️ {code}는 등록돼 있지 않아.")
        return
    w = watches.pop(code)
    name = w.get("name", code)
    _save_manual_watches(watches)

    entry_price = w.get("entry_price")
    profit_rate = None
    if sell_price is not None and entry_price:
        profit_rate = (sell_price - entry_price) / entry_price * 100 - MANUAL_WATCH_FEE_DRAG_PCT
    _log_manual_watch("deregister", code, w, sell_price, profit_rate)
    if profit_rate is not None:
        emoji = "💰" if profit_rate >= 0 else "💔"
        await send_safe_message(
            channel,
            f"{emoji} **{name}({code}) {profit_rate:+.2f}% 수익실현**\n"
            f"   평단가 {entry_price:,.0f}원 → 매도가 {sell_price:,.0f}원"
        )
    else:
        await send_safe_message(channel, f"✅ {name}({code}) 등록 해제했어.")

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
    """★ 2026-10-06 — 이 함수 자체는 디스코드 API 예외(429 레이트리밋/
    5xx 등)를 절대 밖으로 흘려보내지 않는다(형제 Opus 리뷰로 발견).
    이전엔 여기서 터진 예외가 호출부(특히 @tasks.loop로 도는
    manual_watch_trailing_loop 등)까지 그대로 올라가, discord.py의
    tasks.loop가 처리 안 된 예외 시 루프를 영구 정지시켜버리는 문제가
    있었음 — 전송 실패 한 번으로 매도 알림 전체가 재시작 전까지
    조용히 꺼지는 사고로 이어질 수 있었음."""
    try:
        await _send_safe_message_impl(target, text, reply_to)
    except Exception as e:
        print(f"⚠️ [send_safe_message] 전송 실패(무시하고 계속 진행): {e}")


async def _send_safe_message_impl(target, text, reply_to=None):
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
    # ★ 2026-10-06 — naive datetime.now()는 서버 시스템 타임존에 암묵적으로
    #   의존하는데, 이 파일 다른 곳은 전부 KST를 명시적으로 쓰고 있어서
    #   서버 TZ가 바뀌면 조용히 날짜가 틀어질 수 있었음(형제 Opus 리뷰로
    #   발견) — 다른 곳과 통일해 KST 명시.
    conn = sqlite3.connect(DB_PATH_FINANCE)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO finance_ledger (date, type, item, amount) VALUES (?, ?, ?, ?)",
                   (datetime.datetime.now(KST).strftime("%Y-%m-%d"), r_type, item, amount))
    conn.commit()
    conn.close()
    return f"장부에 [{r_type}] {item} {amount:,}원 기록 완료!"

def get_monthly_report():
    conn = sqlite3.connect(DB_PATH_FINANCE)
    cursor = conn.cursor()
    cursor.execute("SELECT type, amount FROM finance_ledger WHERE date LIKE ?", (f"{datetime.datetime.now(KST).strftime('%Y-%m')}%",))
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

        # ★ 2026-10-06 — 구글 캘린더 종일(all-day) 일정은 end.date가
        #   "배타적"(해당 날짜는 포함 안 됨)이어야 해서, 하루짜리 일정도
        #   end는 시작일+1일로 줘야 함. start==end로 주면 빈 시간범위라
        #   API가 거부하거나 깨진 일정이 생길 수 있었음(형제 Opus 리뷰로
        #   발견 — !일정추가가 계속 실패하던 원인으로 추정).
        start_dt = datetime.datetime.strptime(target_date, "%Y-%m-%d")
        end_date = (start_dt + datetime.timedelta(days=1)).strftime("%Y-%m-%d")

        event_body = {
            'summary': summary,
            'start': {'date': target_date, 'timeZone': 'Asia/Seoul'},
            'end': {'date': end_date, 'timeZone': 'Asia/Seoul'},
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
    except Exception as e:
        # ★ 2026-10-06 — requests 예외 메시지엔 요청 URL 전체(authKey
        #   쿼리파라미터 포함)가 그대로 들어가는 경우가 있어, 이걸 그대로
        #   반환하면 LLM 프롬프트에 먹혀 디스코드 응답으로까지 API 키가
        #   노출될 위험이 있었음(형제 Opus 리뷰로 발견). 원인 종류만
        #   남기고 상세 메시지는 서버 로그에만 출력.
        print(f"⚠️ [날씨] 기상청 조회 오류: {e}")
        return f"기상청 수신 지연 중 ({type(e).__name__})"

async def fetch_mbngold_async(service_id="10001", limit=5):
    """★ 2026-10-06 — 내부 로그인/목록조회/본문조회가 전부 동기 requests
    호출이라(여러 개 순차 GET, 타임아웃 합치면 수십 초) async def인데
    실제로는 이벤트루프를 그 시간만큼 블로킹하고 있었음(형제 Opus
    리뷰로 발견) — 블로킹되는 동안 heartbeat/다른 스케줄러의 정각체크/
    디스코드 게이트웨이 핑이 전부 밀림. asyncio.to_thread로 별도
    스레드에 위임."""
    return await asyncio.to_thread(_fetch_mbngold_sync, service_id, limit)


def _fetch_mbngold_sync(service_id="10001", limit=5):
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


def _fetch_mbn_strategy_page_sync(sess, headers, base_url):
    """로그인+전략 목록페이지 조회 (동기 — to_thread로 실행). 실패시 None."""
    try:
        sess.post(f"{base_url}/mg/mypage/login_action.php", headers=headers, data={
            "mode": "login", "rURL": f"{base_url}/mg/news/",
            "mID":  os.getenv("MBNGOLD_ID", ""),
            "mPWD": os.getenv("MBNGOLD_PW", ""),
        }, timeout=10)
    except Exception as e:
        print(f"❌ MBN골드 전략 로그인 에러: {e}"); return None
    try:
        res = sess.get(f"{base_url}/mg/strategy/", headers=headers, timeout=10)
        return res.content.decode("utf-8", errors="ignore")
    except Exception as e:
        print(f"❌ MBN골드 전략 페이지 에러: {e}"); return None


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

    # ★ 2026-10-06 — 로그인+목록페이지 조회가 동기 requests 호출이라
    #   이벤트루프를 블로킹하고 있었음(형제 Opus 리뷰로 발견 — 본문
    #   요약 부분은 이미 to_thread로 분리돼있었는데 이 앞부분만 빠짐).
    page_html = await asyncio.to_thread(_fetch_mbn_strategy_page_sync, sess, headers, base_url)
    if page_html is None:
        return ""
    soup = _BS(page_html, "html.parser")

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
        # ★ 2026-10-06 — href가 "/"로 시작하는 절대경로나 전체 URL이면
        #   단순 f-string 접합은 깨진 링크(이중 슬래시 등)를 만듦(형제
        #   Opus 리뷰로 발견) — urljoin으로 교체(상단에 이미 import돼
        #   있었는데 실제로 안 쓰이고 있었음).
        link = urllib.parse.urljoin(f"{base_url}/mg/strategy/", a_tag['href']) if a_tag else ""
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


def _call_llm(prompt: str, max_tokens: int = 1200, system: str = None) -> str:
    """Claude API 직접 호출.
    ★ 2026-09-07: 로컬 ollama 경로를 완전히 제거함 — 이 서버는 실계좌
    봇(sbot/sbo2/cbot)과 원격데스크톱이 같은 GPU(RTX 3070Ti, 8GB)를
    나눠 쓰는데, 로컬 LLM(gemma4:e4b 등 8GB 이상 모델)이 VRAM을 넘어서면
    일부 레이어가 CPU로 넘어가면서 CPU/GPU를 동시에 크게 잡아먹어 원격
    데스크톱이 느려지는 문제가 반복 발생함(대장 신고). 이 헬퍼가 하루
    몇 번밖에 안 불리는 저빈도 호출이라 로컬로 비용을 아낄 실익도 작아,
    아예 Claude로 통일. 모델은 09-03에 이미 "리나는 컨트롤타워라 소넷5"로
    격상 결정된 것을 그대로 유지(대장 확인)."""
    try:
        import anthropic as _ant
        client = _ant.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        kwargs = {}
        if system:
            kwargs["system"] = system
        res = client.messages.create(
            model=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5"),
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
            **kwargs,
        )
        # ★ 2026-09-03: 소넷5 응답의 content[0]이 ThinkingBlock일 수 있어
        #   .content[0].text 직접 접근 대신 공용 헬퍼 사용(자세한 배경은
        #   common_utils.extract_claude_text 참고).
        from common_utils import extract_claude_text
        return extract_claude_text(res)
    except Exception as e:
        print(f"⚠️ Claude 호출 실패({e})")
        return ""


def _summarize_report_body(text: str) -> str:
    """투자전략 본문을 짧은 요약으로 압축.
    ★ 2026-09-15: 대장 지적 — "시황 그대로 보여주지 말고 요약으로
    보여주는 걸로 하자", 실제로는 응답이 중간에 짤리는 증상이었음.
    원인 둘 다 수정: (1) "A4 1페이지(1000~1500자)"는 진짜 요약이라기엔
    길어서 500자 내외로 더 압축, (2) max_tokens=1200은 소넷5가 thinking에
    토큰을 먼저 소모해 본문이 중간에 잘리는 이 프로젝트의 재발 버그
    패턴과 정확히 일치(day_trade_scout/09-03 lina 브리핑과 동일 클래스)
    — 2000으로 상향."""
    # ★ 2026-07-02: 원문엔 날짜가 없거나 모호한 경우가 있어, 로컬 LLM이
    #   요약 제목에 "오늘의 시황 요약(2023년 X월)" 식으로 엉뚱한 연도를
    #   지어내는 환각이 있었음(내용 자체는 맞는데 헤더 날짜만 틀림).
    #   실제 오늘 날짜를 프롬프트에 명시하고 원문의 다른 날짜는 무시하도록 지시.
    today_str = datetime.datetime.now(KST).strftime("%Y년 %m월 %d일")
    prompt = (
        f"오늘은 {today_str}이다. 다음은 오늘자 증권사 전문가 투자전략/시황 "
        "리포트 원문이다. 핵심만 골라 500자 내외로 짧게 한국어로 요약해라 "
        "(원문 그대로 옮기지 말고 진짜 요약본으로). 숫자·종목명·원인-결과 "
        "관계는 유지하고 나머지 수식어/반복은 과감히 줄여라. 요약에 날짜를 "
        f"표기해야 한다면 반드시 {today_str}만 써라 — 원문 안에 다른 날짜가 "
        "있어도 그건 무시하고 절대 지어내지 마라. 반드시 한국어로만 "
        "작성해라 — 원문에 중국어/영어가 섞여 있어도 요약은 전부 한국어로만 "
        "쓰고 절대 중국어를 섞지 마라.\n\n"
        f"[원문]\n{text[:4000]}"
    )
    result = _call_llm(prompt, max_tokens=2000)
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
    (있으면) 오늘 MBN 투자전략 요약을 모아
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
        "- 숫자 자체가 밋밋하면 '오늘은 특정 섹터로의 뚜렷한 쏠림은 관찰되지 "
        "않음'이라고 솔직히 쓰세요.\n"
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
        f"[최근 3일 종합 코멘트 추세 — 참고용]\n{trend_text}"
    )
    # ★ 2026-09-04: 소넷5가 이 프롬프트류에서 내부적으로 thinking 블록을
    #   쓰면서 max_tokens 예산을 상당량(실측 231/600) 먼저 소모해버려,
    #   정작 답변 텍스트가 문장 중간에 잘려 디스코드로 그대로 전송되는
    #   사고 발생(09-04 09:35 브리핑이 "…대형"에서 끊김, 사용자가 발견).
    #   thinking+본문(3~5문장)이 모두 들어갈 여유를 두기 위해 상향.
    return _call_llm(prompt, max_tokens=1500)


# ══════════════════════════════════════════════════════════════
# AI 모멘텀 스캐너 (2026-07-09 신규) — 관찰 전용
# ══════════════════════════════════════════════════════════════
# ★ VCP/추세/촉매는 전부 "이미 벌어진 기술적 패턴"만 본다. 미국-이란
#   재격돌/하이퍼스케일러 CAPEX 우려/중국 반도체 부각 같은 거시 모멘텀
#   내러티브를 종합해 "그래서 오늘 뭐가 뜰까"를 판단하는 축은 없었음.
#   사용자 제안: 하루 2회(아침/오후) 종목 2개씩 물어보고, 생쇼처럼
#   사후검증(체크인)만 하고 sbot/sbo2 스코어링엔 바로 연결하지 않는다.
#   ★ 2026-09-07: 원래는 "로컬 AI가 이런 판단을 얼마나 잘하는지"를 관찰할
#   목적으로 로컬 ollama를 일부러 썼는데, 관찰 결과 "20B 미만 로컬모델은
#   품질이 대화용 수준이지 이런 판단엔 부적합"으로 결론 남(대장 확인) —
#   관찰 종료, 이 호출도 다른 곳과 동일하게 Claude로 통일.

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
    # ★ 2026-10-06 — 파일 상단에서 이미 candidate_pool.get_stock_code를
    #   쓰는데 여기만 sbo2(실거래 봇 모듈 전체)에서 다시 import하고
    #   있었음(형제 Opus 리뷰로 발견) — 불필요한 모듈 재사용, 통일.

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
    live_price = None   # ★ 2026-10-06 — C 패턴에서 실제 조회된 라이브 현재가(있으면)
    if not pattern and api and len(closes) >= 200 and len(volumes) >= 20:
        ma200 = sum(closes[:200]) / 200
        week52_high = max(closes[:252]) if len(closes) >= 252 else max(closes)
        if curr > ma200 and curr >= week52_high * 0.8:
            try:
                # ★ 2026-10-06 — 상단에서 이미 candidate_pool.get_stock_code를
                #   쓰는데 여기만 sbo2에서 다시 import하고 있었음(형제
                #   Opus 리뷰로 발견) — 통일.
                code = get_stock_code(stock_name)
                mdata = api.get_market_data(code) if code else None
                if mdata:
                    # ★ 2026-10-06 — curr(DB 종가, 대개 전일)를 오늘의
                    #   실시간 시가/고가와 비교하고 있던 버그(형제 Opus
                    #   리뷰로 발견) — 이미 mdata를 받아왔으니 오늘
                    #   현재가(stck_prpr)로 비교해야 양봉/윗꼬리 판정이
                    #   실제 오늘 움직임을 반영함.
                    live_price = float(mdata.get("stck_prpr", 0) or 0)
                    ref = live_price if live_price > 0 else curr
                    acml_vol = float(mdata.get("acml_vol", 0) or 0)
                    avg_vol20 = sum(volumes[:20]) / 20
                    day_open = float(mdata.get("stck_oprc", 0) or 0)
                    day_high = float(mdata.get("stck_hgpr", 0) or 0)
                    # ★ 2026-07-10: 검토 중 발견한 버그 — 거래량 급증만 보고
                    #   방향(양봉/음봉)을 전혀 확인 안 해서, 나쁜 뉴스로 대량
                    #   매도가 터져 폭락하는 날에도 "거래량서지"로 오판될 수
                    #   있었음(사용자 지적). 양봉 확인(현재가>시가) + 윗꼬리
                    #   배제(고가 대비 3% 이상 밀리면 가짜돌파로 간주) 추가.
                    is_bullish   = day_open > 0 and ref > day_open
                    no_long_wick = day_high <= 0 or (day_high - ref) / ref <= 0.03
                    if (avg_vol20 > 0 and acml_vol >= avg_vol20 * 3.0
                            and is_bullish and no_long_wick):
                        pattern = "거래량서지"
            except Exception as e:
                print(f"⚠️ [모멘텀] {stock_name} 거래량서지 조회 오류: {e}")

    if not pattern:
        return {}

    # ★ 2026-10-06 — A/B는 라이브 시세를 조회하지 않으니 DB 종가(curr)가
    #   최선의 근사치지만, C(거래량서지)는 이미 조회해둔 오늘 현재가를
    #   기준가로 써야 함 — 안 그러면 손절/목표가가 전일 종가 기준으로
    #   계산되는 버그가 그대로 남음(형제 Opus 리뷰로 발견).
    ref_price = live_price if (pattern == "거래량서지" and live_price) else curr

    return {
        "pattern": pattern,
        "curr_price": ref_price,
        "stop_price": round(ref_price * 0.93, 0),
        "tgt_price":  round(ref_price * 1.12, 0),
    }


def _build_momentum_context_am() -> str:
    """아침 세션 컨텍스트 — 전일/미장/국제정세/전문가 시황"""
    from swing_master import _get_us_market_movers
    movers = _get_us_market_movers()
    us_lines = []
    for ticker, chg, kr_names in (movers[:6] + movers[-4:]):
        if kr_names:
            us_lines.append(f"{ticker}({chg:+.1f}%) → {', '.join(kr_names[:3])}")
    us_text = "\n".join(us_lines) if us_lines else "데이터 없음"

    from market_concentration import get_recent_summaries
    recent = get_recent_summaries(days=5)
    trend_text = "\n".join(
        f"- {r['date']}: {r['summary_text'][:150]}..." for r in recent
    ) if recent else "없음"

    return (
        f"[간밤 미국 증시 — 한국 수혜/피해 종목 매핑]\n{us_text}\n\n"
        f"[최근 며칠 쏠림 흐름 — 참고용]\n{trend_text}"
    )


def _build_momentum_context_pm() -> str:
    """오후 세션 컨텍스트 — 장중상황/섹터/텔레그램"""
    from market_concentration import get_latest_snapshot, _calc_sector_ranking, _calc_rotation_flag
    snapshot = get_latest_snapshot() or {}
    sector_ranking = _calc_sector_ranking()
    rotation_flag  = _calc_rotation_flag()

    return (
        f"[오늘 장중 쏠림 지수 — {snapshot.get('ts', '')}]\n"
        f"- 코스피: {snapshot.get('kospi_rate', 0):+.2f}% / "
        f"대형주평균: {snapshot.get('mega_avg_rate', 0):+.2f}% / "
        f"시장폭: {snapshot.get('breadth_ratio', 0):.1f}%\n"
        f"- 섹터 등락률 랭킹: {sector_ranking or '데이터 없음'}\n"
        f"- 주도주/섹터 급변 신호: {rotation_flag or '없음'}"
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
async def manual_watch_trailing_loop():
    """!리나등록으로 등록된 종목의 트레일링스탑을 매분 체크.
    ★ 매도 실행은 절대 안 함(키움 주문 API 자체가 없음) — 조건
    충족시 알림만 보냄.
    ★ 2026-10-03 대장 지정 — "내가 차트보고 매도 안하면 계속 감시체계로
    가자": 알림 보냈다고 등록을 자동 해제하지 않고 계속 추적한다.
    대신 같은 고점에서 매분 똑같은 알림이 스팸처럼 반복되지 않도록,
    직전에 알림을 보냈던 고점(last_alert_peak)보다 peak_price가 더
    올라간 경우에만 재알림 — "신고점 찍고 또 밀리면 다시 알려줌"
    패턴이 되어 결과적으로 반복 모니터링 취지에 맞음."""
    kst_now = datetime.datetime.now(KST)
    hhmm = kst_now.strftime("%H%M")
    if not ("0800" <= hhmm <= "1950"):
        return
    if not _is_trading_day():
        return

    watches = _load_manual_watches()
    if not watches:
        return

    try:
        from kis_api import KisAPI
        api = KisAPI()
        channel = await client.fetch_channel(REPORT_CHANNEL_ID)
    except Exception as e:
        print(f"⚠️ [리나등록 추적] 초기화 오류: {e}")
        return

    changed_codes = set()
    for code, w in list(watches.items()):
        # ★ 2026-10-06 — 종목 하나 처리 중 생기는 예외(디스코드 전송
        #   오류/필드 누락 등)가 전체를 덮지 않도록 격리. discord.py의
        #   tasks.loop는 처리 안 된 예외가 새면 루프 자체를 영구 정지
        #   시키는데(형제 Opus 리뷰로 발견), 그러면 재시작 전까지 매도
        #   알림이 조용히 전부 꺼져버림 — 종목 단위로 try를 걸어 한
        #   종목의 문제가 다른 종목 감시/다음 루프를 막지 않게 한다.
        try:
            try:
                mdata = await asyncio.to_thread(api.get_market_data, code)
                price = float((mdata or {}).get("stck_prpr", 0) or 0)
            except Exception as e:
                print(f"⚠️ [리나등록 추적] {code} 시세조회 오류: {e}")
                continue
            if price <= 0:
                continue

            entry = w["entry_price"]
            # ★ rate(가격기준)는 트레일링 발동/정지 "판단"에만 사용 — 차트가
            #   보여주는 실제 가격움직임 기준이어야 함. 알림 문구에 보여줄
            #   때만 net_rate(수수료+세금 차감한 체감 수익률)로 바꿔치기.
            rate = (price - entry) / entry * 100
            net_rate = rate - MANUAL_WATCH_FEE_DRAG_PCT

            if w.get("peak_price") is not None:
                if price > w["peak_price"]:
                    w["peak_price"] = price
                    changed_codes.add(code)
                peak_rate = (w["peak_price"] - entry) / entry * 100
                trail_pct = (MANUAL_WATCH_TRAILING_STOP_PCT
                             if peak_rate > MANUAL_WATCH_TRAILING_STOP_WIDEN_PCT
                             else MANUAL_WATCH_TRAILING_STOP_PCT_TIGHT)
                trail_stop = w["peak_price"] * (1 - trail_pct / 100)
                floor_price = entry * (1 + MANUAL_WATCH_MIN_LOCKED_PROFIT_PCT / 100)
                trail_stop = max(trail_stop, floor_price)
                # ★ 2026-10-06 — floor_price가 trail_pct 계산값보다 높아서
                #   실제 매도선이 된 경우에도 메시지엔 그냥 trail_pct(1.5/
                #   2.0%)를 그대로 찍어서, 실제 발동 지점(예: 평단+1%
                #   바닥선)과 안 맞는 숫자가 표시되던 문제(형제 Opus
                #   리뷰로 발견) — 고점 대비 실제 하락률을 역산해서 표시.
                actual_drop_pct = ((w["peak_price"] - trail_stop) / w["peak_price"] * 100
                                   if w["peak_price"] > 0 else trail_pct)
                if price <= trail_stop and w["peak_price"] > w.get("last_alert_peak", 0):
                    await send_safe_message(
                        channel,
                        f"🔔 **[리나등록] {w.get('name', code)}({code}) 매도 신호**\n"
                        f"   고점 {w['peak_price']:,.0f}원 대비 -{actual_drop_pct:.1f}% "
                        f"({price:,.0f}원, 총 {net_rate:+.2f}%) — 키움에서 매도 판단해줘.\n"
                        f"   (계속 감시할게 — 신고점 찍고 또 밀리면 다시 알려줄게. "
                        f"그만 지켜봐도 되면 `!리나등록해제 {code}`)"
                    )
                    w["last_alert_peak"] = w["peak_price"]
                    changed_codes.add(code)
                continue

            if rate >= MANUAL_WATCH_TAKE_PROFIT_PCT:
                w["peak_price"] = price
                changed_codes.add(code)
                await send_safe_message(
                    channel,
                    f"📈 [리나등록] {w.get('name', code)}({code}) +{net_rate:.2f}% 도달 — "
                    f"트레일링 추적 시작(고점 {price:,.0f}원, -{MANUAL_WATCH_TRAILING_STOP_PCT_TIGHT}% 밀리면 알려줄게)"
                )
        except Exception as e:
            print(f"⚠️ [리나등록 추적] {code} 처리 중 예외(건너뜀): {e}")
            continue

    if changed_codes:
        # ★ 2026-10-06 — write_state(전체덮어쓰기) 대신 변경된 종목만
        #   락 보호 병합 저장(위 _save_manual_watch_updates 참고) — 루프가
        #   1분간 들고 있던 옛날 스냅샷으로 그 사이의 등록/해제를
        #   덮어쓰는 경합을 막는다.
        _save_manual_watch_updates(watches, changed_codes)


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


# ══════════════════════════════════════════════════════════════
# 3개월수급 당일주도주 — 키움 조건식의 파이썬 구현 (2026-10-06, 관찰 전용)
# ══════════════════════════════════════════════════════════════
# ★ 키움 API 없이 일봉 DB(B·E) + 한투 현재가(F·G·H·I) + 체결강도(A)로
#   같은 조건을 계산(intelligence/three_month_leader.py 참고). 09:00~12:00에
#   3분마다 확인하고, 새로 걸린 종목만 알린다. 매매는 하지 않음 — 며칠간 키움
#   결과와 나란히 비교하는 용도.
# ★ 2026-10-06 대장: 장 초반엔 체결강도가 100% 아래라 안 잡히는 경우가 많아
#   키움 쪽 감시를 12시까지로 늘림 → 여기도 맞춤.
TML_WATCH_END = "1200"
_TML_STATE = {"date": "", "alerted": set()}


def _scan_footer(extra: str = "") -> str:
    """★ 2026-10-08: 데이봇이 파이썬판을 후보로 쓰기 시작(DAYBOT_SCAN_SOURCE=python) —
    '관찰 전용/키움과 비교' 문구를 실제 용도에 맞게."""
    src = os.getenv("DAYBOT_SCAN_SOURCE", "kiwoom").strip().lower()
    use = "→ 데이봇 매수 후보로 전달" if src in ("python", "union", "fallback") else "관찰 전용(데이봇 미사용)"
    return f"\n   ({use}{' · ' + extra if extra else ''})"


def _alerted_today(table: str, today: str) -> set:
    """오늘 이미 통과 기록이 있는 종목 = 이미 알린 종목.
    ★ 2026-10-06: 알림 중복방지가 메모리에만 있어서 bot restart 할 때마다 같은
      종목(알멕)을 또 알렸음 — 재시작 후 첫 검사 전에 기록 DB에서 복원."""
    try:
        import three_month_leader as tml
        conn = sqlite3.connect(tml.LOG_DB, timeout=10)
        try:
            return {c for (c,) in conn.execute(
                f"SELECT DISTINCT code FROM {table} WHERE date=? AND passed=1", (today,))}
        finally:
            conn.close()
    except Exception:
        return set()
_tml_api = None


def _tml_scan_sync(with_strength: bool = True):
    global _tml_api
    import three_month_leader as tml
    if _tml_api is None:
        from kis_api import KisAPI
        _tml_api = KisAPI()
    universe = tml.build_universe()
    return universe, tml.check_candidates(_tml_api, universe, with_strength=with_strength)


@tasks.loop(minutes=3)
async def three_month_leader_watch():
    kst_now = datetime.datetime.now(KST)
    if not ("0900" <= kst_now.strftime("%H%M") < TML_WATCH_END):
        return
    if not _is_trading_day():
        return
    try:
        today = kst_now.strftime("%Y-%m-%d")
        if _TML_STATE["date"] != today:
            _TML_STATE.update(date=today, alerted=_alerted_today("tml_obs", today))
        universe, results = await asyncio.to_thread(_tml_scan_sync)
        try:   # 체결강도 기준 결정용 기록 (python three_month_leader.py report)
            import three_month_leader as tml
            await asyncio.to_thread(tml.log_observations, results)
        except Exception as e:
            print(f"⚠️ [3개월수급] 기록 오류: {e}")
        hits = [r for r in results if r["passed"] and r["code"] not in _TML_STATE["alerted"]]
        print(f"🧪 [3개월수급] 후보 {len(universe['items'])}개 → 통과 {sum(r['passed'] for r in results)}개 (신규 {len(hits)})")
        if not hits:
            return
        import three_month_leader as tml
        channel = await client.fetch_channel(SCAN_CHANNEL_ID)
        await send_safe_message(
            channel,
            f"🧪 **[3개월수급 당일주도주]** {kst_now.strftime('%H:%M')}\n"
            + "\n".join(tml.format_hit(r) for r in hits)
            + _scan_footer()
        )
        for r in hits:
            _TML_STATE["alerted"].add(r["code"])
    except Exception as e:
        print(f"⚠️ [3개월수급] 스캔 오류: {e}")


@three_month_leader_watch.before_loop
async def before_three_month_leader_watch():
    await client.wait_until_ready()


# ══════════════════════════════════════════════════════════════
# 주도주검색식3 — 키움 조건식의 파이썬 구현 (2026-10-06, 관찰 전용)
# ══════════════════════════════════════════════════════════════
# intelligence/leader_scan.py 참고. 09:00~15:20 3분마다, 새로 걸린 종목만
# 알리고 매 스캔 결과는 기록(three_month_leader_log.db의 leader_obs).
LEADER_WATCH_START, LEADER_WATCH_END = "0900", "1520"
_LEADER_STATE = {"date": "", "alerted": set()}


def _leader_scan_sync():
    global _tml_api
    import leader_scan
    if _tml_api is None:
        from kis_api import KisAPI
        _tml_api = KisAPI()
    return leader_scan.scan(_tml_api, leader_scan.build_pool(_tml_api))


@tasks.loop(minutes=3)
async def leader_scan_watch():
    kst_now = datetime.datetime.now(KST)
    if not (LEADER_WATCH_START <= kst_now.strftime("%H%M") < LEADER_WATCH_END):
        return
    if not _is_trading_day():
        return
    try:
        import leader_scan
        today = kst_now.strftime("%Y-%m-%d")
        if _LEADER_STATE["date"] != today:
            _LEADER_STATE.update(date=today, alerted=_alerted_today("leader_obs", today))
        out = await asyncio.to_thread(_leader_scan_sync)
        try:
            await asyncio.to_thread(leader_scan.log_scan, out)
        except Exception as e:
            print(f"⚠️ [주도주3] 기록 오류: {e}")
        hits = [r for r in out["results"] if r["passed"] and r["code"] not in _LEADER_STATE["alerted"]]
        print(f"🧪 [주도주3] 풀 {out['pool']} → 상위 {out['ranked']} → 통과 "
              f"{sum(r['passed'] for r in out['results'])}개 (신규 {len(hits)})")
        if not hits:
            return
        channel = await client.fetch_channel(SCAN_CHANNEL_ID)
        await send_safe_message(
            channel,
            f"🧪 **[주도주검색식3]** {kst_now.strftime('%H:%M')}\n"
            + "\n".join(leader_scan.format_hit(r) for r in hits)
            + _scan_footer()
        )
        _LEADER_STATE["alerted"].update(r["code"] for r in hits)
    except Exception as e:
        print(f"⚠️ [주도주3] 스캔 오류: {e}")


@leader_scan_watch.before_loop
async def before_leader_scan_watch():
    await client.wait_until_ready()


# ══════════════════════════════════════════════════════════════
# 단타000 — 키움 조건식의 파이썬 구현 (2026-10-06, 관찰 전용)
# ══════════════════════════════════════════════════════════════
# intelligence/danta_scan.py 참고. 잠깐 떴다 사라지는 유형이라 1분마다 보고,
# 같은 날 주도주3/3개월수급 기록과 겹치면 🔗로 표시(대장: "단타000에 걸린
# 넘이 다른 두 곳에서 걸릴 가능성이 높다" — 겹침 확인이 목적).
DANTA_WATCH_START, DANTA_WATCH_END = "0900", "1520"
_DANTA_STATE = {"date": "", "alerted": set(), "scanner": None}


def _danta_scan_sync():
    global _tml_api
    import danta_scan
    if _tml_api is None:
        from kis_api import KisAPI
        _tml_api = KisAPI()
    if _DANTA_STATE["scanner"] is None:
        _DANTA_STATE["scanner"] = danta_scan.DantaScanner(_tml_api)
    out = danta_scan.scan_and_tag(_DANTA_STATE["scanner"])
    return out


@tasks.loop(minutes=1)
async def danta_scan_watch():
    kst_now = datetime.datetime.now(KST)
    if not (DANTA_WATCH_START <= kst_now.strftime("%H%M") < DANTA_WATCH_END):
        return
    if not _is_trading_day():
        return
    try:
        import danta_scan
        today = kst_now.strftime("%Y-%m-%d")
        if _DANTA_STATE["date"] != today:
            _DANTA_STATE.update(date=today, alerted=_alerted_today("danta_obs", today))
        out = await asyncio.to_thread(_danta_scan_sync)
        try:
            await asyncio.to_thread(danta_scan.log_scan, out)
        except Exception as e:
            print(f"⚠️ [단타000] 기록 오류: {e}")
        hits = [r for r in out["results"] if r["passed"] and r["code"] not in _DANTA_STATE["alerted"]]
        print(f"🧪 [단타000] 풀 {out['pool']} → 1단계 {out['stage1']} → 통과 "
              f"{sum(r['passed'] for r in out['results'])}개 (신규 {len(hits)})")
        if not hits:
            return
        channel = await client.fetch_channel(SCAN_CHANNEL_ID)
        await send_safe_message(
            channel,
            f"🧪 **[단타000]** {kst_now.strftime('%H:%M')}\n"
            + "\n".join(danta_scan.format_hit(r, r.get("overlap", "")) for r in hits)
            + _scan_footer("🔗 = 오늘 주도주/3개월수급과 겹침")
        )
        _DANTA_STATE["alerted"].update(r["code"] for r in hits)
    except Exception as e:
        print(f"⚠️ [단타000] 스캔 오류: {e}")


@danta_scan_watch.before_loop
async def before_danta_scan_watch():
    await client.wait_until_ready()


# ══════════════════════════════════════════════════════════════
# 한투 관심그룹 섹터 감시 (2026-10-06, 관찰 전용)
# ══════════════════════════════════════════════════════════════
# intelligence/sector_watch.py 참고. 대장이 한투에 분야별로 정리한 관심그룹의
# 강도 순위를 정해진 시각에 한 번씩 올리고(검색식 채널), 강한 분야의 대장주·
# 2등주가 +3%를 넘으면 알림(NEW ⭐, 검색식 겹침 🔗).
SECTOR_WATCH_START, SECTOR_WATCH_END = "0900", "1520"
SECTOR_RANK_TIMES = ("0910", "1000", "1100", "1300", "1430")
_SECTOR_STATE = {"date": "", "alerted": set(), "posted": set(), "watcher": None, "last": None}


def _sector_scan_sync():
    global _tml_api
    import sector_watch
    if _tml_api is None:
        from kis_api import KisAPI
        _tml_api = KisAPI()
    if _SECTOR_STATE["watcher"] is None:
        _SECTOR_STATE["watcher"] = sector_watch.SectorWatcher(_tml_api)
    out = _SECTOR_STATE["watcher"].scan()
    _SECTOR_STATE["last"] = out
    return out


@tasks.loop(minutes=3)
async def sector_watch_loop():
    kst_now = datetime.datetime.now(KST)
    hhmm = kst_now.strftime("%H%M")
    if not (SECTOR_WATCH_START <= hhmm < SECTOR_WATCH_END):
        return
    if not _is_trading_day():
        return
    try:
        import sector_watch
        import danta_scan
        today = kst_now.strftime("%Y-%m-%d")
        if _SECTOR_STATE["date"] != today:
            _SECTOR_STATE.update(date=today, alerted=sector_watch.alerted_today(today), posted=set())
        out = await asyncio.to_thread(_sector_scan_sync)
        if not out["sectors"]:
            print("⚠️ [섹터] 관심그룹을 못 읽었거나 비어 있음 (KIS_HTS_ID 확인)")
            return
        await asyncio.to_thread(sector_watch.log_scan, out)
        channel = None
        due = [t for t in SECTOR_RANK_TIMES if t <= hhmm and t not in _SECTOR_STATE["posted"]]
        if due:
            _SECTOR_STATE["posted"].update(due)
            channel = await client.fetch_channel(SCAN_CHANNEL_ID)
            await send_safe_message(channel, sector_watch.format_ranking(out))
        hits = sector_watch.leader_moves(out["sectors"], out["new_codes"], _SECTOR_STATE["alerted"])
        print(f"🗺️ [섹터] 그룹 {out['groups']} · 1위 {out['sectors'][0]['group']} "
              f"{out['sectors'][0]['avg_chg']:+.2f}% · 대장/2등 신규 {len(hits)}")
        if hits:
            channel = channel or await client.fetch_channel(SCAN_CHANNEL_ID)
            await send_safe_message(channel, "\n".join(
                sector_watch.format_move(h, danta_scan.overlap_today(h["code"], today)) for h in hits))
            _SECTOR_STATE["alerted"].update(h["code"] for h in hits)
            await asyncio.to_thread(sector_watch.save_alerts, hits)
    except Exception as e:
        print(f"⚠️ [섹터] 감시 오류: {e}")


@sector_watch_loop.before_loop
async def before_sector_watch_loop():
    await client.wait_until_ready()


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


# 💡 [신규 엔진 기능] 아침 브리핑에 주입할 최고 우량 수급 종목 발굴 엔진
def fetch_top_institutional_and_foreign_picks():
    # 💡 복잡한 로직은 모듈로 다 보냈으니, 여기선 깔끔하게 Call만 때린다!
    return quant_analyzer.get_hybrid_top_picks()


def _build_morning_market_context_sync():
    """07:30 브리핑 STEP1(미장 yfinance 스캔)+STEP2(수급 크롤러, DB 대량
    스캔) — 둘 다 블로킹 I/O·연산이라 동기로 묶어 to_thread로 실행
    (형제 Opus 리뷰로 발견 — 이전엔 async def 안에서 await 없이 직접
    호출돼 몇 초~몇십 초씩 이벤트루프를 막고 있었음)."""
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

    crawler_finance_context = fetch_top_institutional_and_foreign_picks()
    return us_movers_summary, crawler_finance_context

# ===================================================
# 💡 [테마 역추적 기능이 추가된 하이브리드 검색 라우터]
# ===================================================
async def web_search_hybrid(query):
    # 1. 특정 종목에 대해 테마를 물어보는 경우 (예: "필옵틱스 테마 뭐야?")
    # ★ 2026-10-06 — "뭐야"만으로도 이 분기가 걸려서 "오늘 날씨 뭐야?"
    #   같은 무관한 질문도 종목테마 DB를 조회했고, 검색어가 "테마"/"뭐야"
    #   제거 후 빈 문자열이 되면(예: 질문이 "뭐야" 하나뿐일 때) LIKE '%%'
    #   가 되어 테이블 전체 테마가 쏟아지는 버그가 있었음(형제 Opus
    #   리뷰로 발견). "테마"가 실제로 포함된 경우만 + 검색어가 비지
    #   않을 때만 조회하도록 교체.
    if "테마" in query:
        search_term = query.replace("테마", "").replace("뭐야", "").strip()
        if search_term:
            conn = sqlite3.connect(DB_PATH_THEME_FINANCE)
            cursor = conn.cursor()

            cursor.execute("SELECT theme_name FROM kr_theme_stocks WHERE stock_name LIKE ?", ('%' + search_term + '%',))
            results = cursor.fetchall()
            conn.close()

            if results:
                themes = [r[0] for r in set(results)]
                return f"🔍 **[테마 탐색기]** 대장! 찾았어! \n{', '.join(themes)} 테마에 묶여있는 종목이야!"

    # 2. 기존 기능들 그대로 유지
    # ★ 2026-10-06 — fetch_calendar_events()/get_weather_kma_pure()는
    #   동기 네트워크 호출인데 await 없이 직접 불려서 이벤트루프를
    #   블로킹하고 있었음(형제 Opus 리뷰로 발견) — to_thread로 위임.
    if any(kw in query for kw in ["일정", "스케줄", "계획"]) and "추가" not in query: return f"[구글 캘린더 일정 목록]:\n{await asyncio.to_thread(fetch_calendar_events)}"
    if any(kw in query for kw in ["입출금", "출금", "내역", "수입", "지출", "가계부", "장부"]): return get_monthly_report()
    if any(kw in query for kw in ["날씨", "기온", "온도", "비와", "눈와", "기상"]): return f"[국내 대한민국 기상청]:\n{await asyncio.to_thread(get_weather_kma_pure)}"
    if any(kw in query for kw in ["뉴스", "속보", "mbn", "모닝", "브리핑"]): return "[MBN골드 뉴스]:\n" + await fetch_mbngold_async("10001", 6)
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

    print(f"\n☀️ [{kst_now.strftime('%H:%M')}] 미국장+뉴스+수급 통합 융합 마스터 브리핑 가동!")
    
    try:
        channel = await client.fetch_channel(REPORT_CHANNEL_ID)
    except Exception as e:
        print(f"❌ 장전 브리핑 채널 접속 실패: {e}")
        return

    # STEP 1+2: 미장 스캔 + 수급 크롤러(둘 다 블로킹이라 스레드로 위임)
    # ★ 2026-10-06 — try 밖이라 수급 크롤러가 한 번 예외를 내면 tasks.loop가
    #   영구 정지(재시작 전까지 07:30 브리핑이 다시 안 옴) — 실패해도 빈 값으로 진행.
    try:
        us_movers_summary, crawler_finance_context = await asyncio.to_thread(_build_morning_market_context_sync)
    except Exception as e:
        print(f"⚠️ 장전 브리핑 데이터 수집 오류: {e}")
        us_movers_summary, crawler_finance_context = "", "수급 데이터 수집 실패"

    # STEP 3: AI 융합 브리핑 (미장 + 수급)
    prompt = (
        f"너는 대한민국 최고의 모멘텀 단타 트레이더를 보좌하는 수석 참모 리나야.\n"
        f"제공된 2가지 핵심 데이터를 상호 교차 검증하여 오늘 장초반 시나리오를 짜줘.\n\n"
        f"[데이터 1: 미국장 급등 현황 & 고정 관련주]\n{us_movers_summary if us_movers_summary else '- 특이 급등 종목 없음'}\n\n"
        f"[데이터 2: 크롤러 엔진 수집 종목별 메이저 쌍끌이 수급 현황]\n{crawler_finance_context}\n\n"
        f"🚨 [브리핑 핵심 지침]:\n"
        f"1. **수급 주도주**: 데이터 2의 쌍끌이 수급 유입 주도주를 강조해줘.\n"
        f"2. **원픽 테마**: 오늘 수급이 가장 강하게 붙을 원픽 테마와 핵심 종목을 단도직입적으로 요약해줘."
    )

    try:
        reply_text = await asyncio.to_thread(_call_llm, prompt, max_tokens=1500, system=SYSTEM_PROMPT)
        if reply_text:
            await send_safe_message(channel, f"☀️ **[대장! 07시 30분 융합 마스터 전략 브리핑이야]** ☀️\n\n{reply_text}")
            print(f"✅ [디버그] 07시 30분 4합 통합 융합 마스터 브리핑 전송 완료!")
    except Exception as e: print(f"❌ 통합 브리핑 전송 에러: {e}")

@daily_morning_report.before_loop
async def before_daily_morning_report():
    await client.wait_until_ready()

# ★ 2026-07-25: 오후 2시 30분 생쇼 관심종목 루프 제거 — MBN이 생쇼
#   뉴스 코너(news_service_id=10020) 자체를 폐지해서(게시글 0건, 사이트
#   뉴스탭에서도 카테고리 소실 확인됨) 소스가 영구 중단됨.

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
    try:
        reply_text = await asyncio.to_thread(_call_llm, prompt, max_tokens=1500, system=SYSTEM_PROMPT)
        if reply_text:
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
RATE_LIMIT_ERROR_STREAK_THRESHOLD = 2  # 연속 2회(루프 1분 기준 약 2분) 감지되면 재시작 — ★ 2026-10-06 주석이 루프 주기(30초)와 실제(1분) 불일치하던 것 수정(형제 Opus 리뷰로 발견)
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
    "sbot":     "/home/free4tak/k-bot/stock_bot/logs/sbot.log",
    "sector":   "/home/free4tak/k-bot/stock_bot/logs/sector_monitor.log",
}
# ★ 2026-07-02: 고정 바이트 tail(_LOG_TAIL_BYTES) 방식은 로그가 적게 쌓이는
#   구간(장외 대기 등)에서 8000바이트가 10~20분치까지 덮어버려, 이미 지나간
#   1회성 rate-limit 에러가 여러 번의 1분 체크에서 계속 "감지"되며 스트릭이
#   허위로 쌓여 sbot이 불필요하게 자주 재시작되는 버그가 있었음. 각 봇의
#   마지막 읽은 오프셋을 기억해 그 이후 새로 추가된 부분만 읽도록 수정 —
#   이래야 진짜 "최근 1분" 신규 로그만 보게 된다.
_log_read_offset: dict[str, int] = {}
# ★ 2026-10-06 — journalctl 조회용 커서(파일형 봇의 _log_read_offset과
#   동일 원리). "--since 1 minute ago"는 호출마다 "지금 기준 1분 전"으로
#   새로 계산되는 롤링 윈도우라, 호출 간격이 정확히 60초가 아니면(지연/
#   지터) 경계 부근 로그 한 줄이 연속 두 번 잡혀 에러 스트릭이 허위로
#   쌓일 수 있었음(형제 Opus 리뷰로 발견) — 마지막 조회 시각을 기억해
#   그 이후분만 가져오도록 교체.
_journal_read_since: dict[str, datetime.datetime] = {}


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
        now   = datetime.datetime.now()
        since = _journal_read_since.get(bot_name, now - datetime.timedelta(minutes=1))
        result = subprocess.run(
            ["journalctl", "-u", f"yeongam9-{bot_name}",
             "--since", since.strftime("%Y-%m-%d %H:%M:%S"), "--no-pager"],
            capture_output=True, text=True, timeout=15,
        )
        _journal_read_since[bot_name] = now
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
                # ★ 2026-10-06 — subprocess.run(timeout=30)을 await 없이
                #   직접 호출해서 최악의 경우 30초간 전체 이벤트루프(디스코드
                #   게이트웨이 핑/다른 스케줄러 포함)를 블로킹하고 있었음
                #   (형제 Opus 리뷰로 발견).
                ret = await asyncio.to_thread(
                    subprocess.run,
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
    # ★ 2026-09-21: 기동 로그가 스케줄 시각과 무관한 뒤죽박죽 순서로 찍혀
    #   눈에 계속 걸린다는 대장 지적("순서좀 맞춰주라") — 실제 동작(각
    #   스케줄러의 .start() 호출 순서)은 서로 독립적이라 바뀌어도 무해,
    #   출력 순서만 스케줄 시각순으로 재배열.
    try:
        if not daily_news_report.is_running():
            daily_news_report.start()
        print("✅ [시스템] 07시 뉴스 스케줄러 가동 성공!")
    except Exception as e: print(f"⚠️ [에러] 뉴스 스케줄러: {e}")

    try:
        if not daily_master_report.is_running():
            daily_master_report.start()
        print("✅ [시스템] 07:20 마스터 리포트 스케줄러 가동 성공!")
    except Exception as e: print(f"⚠️ [에러] 마스터 스케줄러: {e}")

    try:
        if not daily_morning_report.is_running():
            daily_morning_report.start()
        print("✅ [시스템] 7시 30분 융합 브리핑 스케줄러 가동 성공!")
    except Exception as e: print(f"⚠️ [에러] 7시 30분 스케줄러: {e}")

    try:
        if not daily_strategy_report.is_running():
            daily_strategy_report.start()
        print("✅ [시스템] 08:50 MBN 투자전략 요약 스케줄러 가동 성공!")
    except Exception as e: print(f"⚠️ [에러] 투자전략 스케줄러: {e}")

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
        if not daily_market_context_report.is_running():
            daily_market_context_report.start()
        print("✅ [시스템] 09:35 시장 쏠림 종합 브리핑 스케줄러 가동 성공! (관찰 전용)")
    except Exception as e: print(f"⚠️ [에러] 쏠림 브리핑 스케줄러: {e}")

    try:
        if not api_error_watchdog.is_running():
            api_error_watchdog.start()
        print("✅ [시스템] API 에러 watchdog (1분 주기, sbot/sbo2/sector) 가동 성공!")
    except Exception as e: print(f"⚠️ [에러] API watchdog 스케줄러: {e}")

    try:
        if not manual_watch_trailing_loop.is_running():
            manual_watch_trailing_loop.start()
        print("✅ [시스템] 리나등록 수동매수 트레일링 추적 (1분 주기) 가동 성공!")
    except Exception as e: print(f"⚠️ [에러] 리나등록 추적 스케줄러: {e}")

    try:
        if not three_month_leader_watch.is_running():
            three_month_leader_watch.start()
        print("✅ [시스템] 3개월수급 당일주도주 파이썬판 (09:00~12:00, 3분 주기) 가동 성공!")
    except Exception as e: print(f"⚠️ [에러] 3개월수급 스케줄러: {e}")

    try:
        if not leader_scan_watch.is_running():
            leader_scan_watch.start()
        print("✅ [시스템] 주도주검색식3 파이썬판 (09:00~15:20, 3분 주기) 가동 성공!")
    except Exception as e: print(f"⚠️ [에러] 주도주3 스케줄러: {e}")

    try:
        if not danta_scan_watch.is_running():
            danta_scan_watch.start()
        print("✅ [시스템] 단타000 파이썬판 (09:00~15:20, 1분 주기) 가동 성공!")
    except Exception as e: print(f"⚠️ [에러] 단타000 스케줄러: {e}")

    try:
        if not sector_watch_loop.is_running():
            sector_watch_loop.start()
        print("✅ [시스템] 한투 관심그룹 섹터 감시 (09:00~15:20, 3분 주기) 가동 성공! (관찰 전용)")
    except Exception as e: print(f"⚠️ [에러] 섹터 감시 스케줄러: {e}")

def _fetch_sbo2_status_sync(api, positions: dict):
    """!상태 — 보유종목 기준 주문가능금액+시세 조회 (동기, to_thread로 실행)."""
    psbl = 0
    for _code in list(positions.keys()):
        psbl = api.get_psbl_order_cash(_code)
        if psbl > 0:
            break
    if psbl == 0:
        psbl = api.get_buyable_cash() if hasattr(api, 'get_buyable_cash') else 0

    rows = []
    total_pnl = 0
    for code, pos in positions.items():
        mdata = api.get_market_data(code)
        # ★ 2026-10-06 — stck_prpr가 빈 문자열("")로 오는 경우 float("")가
        #   ValueError를 던져서 !상태 전체가 실패하던 버그(형제 Opus
        #   리뷰로 발견) — "or 0"으로 falsy 값을 먼저 걸러냄(이 파일
        #   다른 곳에서 이미 쓰는 패턴과 통일).
        curr  = float(mdata.get("stck_prpr", 0) or 0) if mdata else pos.get("entry_price", 0)
        entry = pos.get("entry_price", 0)
        qty   = pos.get("qty", 0)
        rate  = (curr - entry) / entry * 100 if entry > 0 else 0
        pnl   = (curr - entry) * qty
        total_pnl += pnl
        rows.append((code, pos, curr, entry, qty, rate, pnl))
    return psbl, rows, total_pnl


@client.event
async def on_message(message):
    if message.author == client.user: return
    # ★ 2026-10-06 — 대장 전용 봇. 계좌 조회/가계부/캘린더/종목등록 등
    #   전부 민감한 명령이라 대장 본인이 아니면 아예 반응하지 않음
    #   (형제 Opus 리뷰로 발견된 권한체크 누락 수정).
    if message.author.id != OWNER_DISCORD_ID: return

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
                # 41만 건 분석 모듈 호출 (Call) — ★ 2026-10-06 — await 없이
                #   직접 호출하면 이 분석(41만 건) 도는 동안 이벤트루프가
                #   그대로 막힘(형제 Opus 리뷰로 발견) — to_thread로 위임.
                picks_report = await asyncio.to_thread(quant_analyzer.get_hybrid_top_picks)

                # 결과 출력
                await send_safe_message(message.channel, picks_report)
                print("🎯 [명령어] 대장의 요청으로 41만 건 하이브리드 추천종목 송출 완료!")
            except Exception as e:
                await send_safe_message(message.channel, f"❌ 앗, 대장! 수급 데이터 분석하다가 꼬였어: {e}")
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
    # 💡 [신규] 대장의 수동 2개 교집합 엔진 호출 명령어 (!마스터)
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

    # ── !3개월수급 (파이썬판 조건검색 수동 확인, 2026-10-06) ──────────
    #   시간 제한 없이 즉시 실행 — 장외엔 마지막 시세 기준이라 참고용.
    # ── !섹터 / !섹터 우주 — 한투 관심그룹 분야별 강도 (2026-10-06) ──
    # (!섹터재시작은 키키 명령 — 리나는 무시)
    if message.content.startswith("!섹터") and not message.content.startswith("!섹터재시작"):
        async with message.channel.typing():
            try:
                import sector_watch
                keyword = message.content[len("!섹터"):].strip()
                out = await asyncio.to_thread(_sector_scan_sync)
                if not out["sectors"]:
                    await send_safe_message(message.channel, "⚠️ 관심그룹을 못 읽었어 (.env의 KIS_HTS_ID 확인)")
                elif keyword:
                    await send_safe_message(message.channel, sector_watch.format_group(out, keyword))
                else:
                    await send_safe_message(message.channel, sector_watch.format_ranking(out, top=10))
            except Exception as e:
                await send_safe_message(message.channel, f"❌ 섹터 조회 오류: {e}")
        return

    # ── !단타 — 단타000 파이썬판 즉시 조회 (2026-10-06) ──
    #   1분 순매수(E)는 직전 검사와의 차이라, 1분 감시가 돌고 있어야 값이 나온다.
    if message.content.startswith("!단타"):
        async with message.channel.typing():
            try:
                import danta_scan
                # !단타 종목명 — 그 종목이 왜 안 잡히는지(마지막 1분 감시 결과 기준)
                keyword = message.content[len("!단타"):].strip()
                if keyword and _DANTA_STATE["scanner"] is not None:
                    await send_safe_message(message.channel, "🔎 **단타000 판정**\n" + "\n".join(
                        "   " + x for x in danta_scan.explain(_DANTA_STATE["scanner"], keyword)))
                    return
                out = await asyncio.to_thread(_danta_scan_sync)
                passed = [r for r in out["results"] if r["passed"]]
                lines = [f"🧪 **단타000 (파이썬판)** {out['time']} — 풀 {out['pool']} → "
                         f"1단계(시총·회전율·잔량비) {out['stage1']} → 검사 {len(out['results'])}"]
                lines += [danta_scan.format_hit(r, r.get("overlap", "")) for r in passed] \
                    or ["   지금 전 조건 통과 종목 없음"]
                near = [r for r in out["results"] if not r["passed"]][:8]
                if near:
                    lines.append("\n**근접 후보 (탈락 조건)**")
                    lines += [f"   {r['name']}({r['code']}) {r['chg']:+.1f}% — {', '.join(r['fails'])}"
                              + (f"  🔗 {r['overlap']}" if r.get("overlap") else "") for r in near]
                await send_safe_message(message.channel, "\n".join(lines))
            except Exception as e:
                await send_safe_message(message.channel, f"❌ 단타 조회 오류: {e}")
        return

    # ── !주도주 — 주도주검색식3 파이썬판 즉시 조회 (2026-10-06) ──
    if message.content.startswith("!주도주"):
        async with message.channel.typing():
            try:
                import leader_scan
                out = await asyncio.to_thread(_leader_scan_sync)
                passed = [r for r in out["results"] if r["passed"]]
                lines = [f"🧪 **주도주검색식3 (파이썬판)** {out['time']} — 풀 {out['pool']}종목 "
                         f"(시세 {out['priced']}) → 거래대금 상위 {out['ranked']}"]
                lines += [leader_scan.format_hit(r) for r in passed] or ["   지금 전 조건 통과 종목 없음"]
                near = [r for r in out["results"] if not r["passed"]][:8]
                if near:
                    lines.append("\n**근접 후보 (탈락 조건)**")
                    lines += [f"   {r['name']}({r['code']}) {r['chg']:+.1f}% [{r['path']}] — "
                              f"{', '.join(r['fails'])}" for r in near]
                await send_safe_message(message.channel, "\n".join(lines))
            except Exception as e:
                await send_safe_message(message.channel, f"❌ 주도주 조회 오류: {e}")
        return

    if message.content.startswith("!3개월수급"):
        async with message.channel.typing():
            try:
                import three_month_leader as tml
                universe, results = await asyncio.to_thread(_tml_scan_sync)
                lines = [f"🧪 **3개월수급 당일주도주 (파이썬판)** — 일봉기준 {universe['latest_db_date']}",
                         f"   B·E 통과 후보 {len(universe['items'])}개 / 검사 {universe['scanned']}종목"]
                passed = [r for r in results if r["passed"]]
                lines += [tml.format_hit(r) for r in passed] or ["   지금 전 조건 통과 종목 없음"]
                near = [r for r in results if not r["passed"]][:8]
                if near:
                    lines.append("\n**근접 후보 (탈락 조건)**")
                    lines += [f"   {r['name']}({r['code']}) {r['chg']:+.1f}% — {', '.join(r['fails'])}" for r in near]
                fakes = universe.get("fakes", [])
                if fakes:
                    lines.append("\n**가짜 거르기로 제외 (B·E는 통과)**")
                    lines += [f"   ✂️ {n} — {why}" for n, why in fakes[:8]]
                await send_safe_message(message.channel, "\n".join(lines))
            except Exception as e:
                await send_safe_message(message.channel, f"❌ 3개월수급 조회 오류: {e}")
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
                # ★ 2026-10-06 — 보유종목 수만큼 KIS 동기호출을 순차로
                #   돌려서 이벤트루프를 오래 블로킹하던 부분(형제 Opus
                #   리뷰로 발견) — 루프 전체를 스레드로 위임.
                psbl, rows, total_pnl = await asyncio.to_thread(_fetch_sbo2_status_sync, api, positions)

                lines = [f"📊 **[sbo2 현재 상태]** [{datetime.datetime.now(KST).strftime('%H:%M:%S')}]"]
                lines.append(f"   💰 주문가능: {psbl:,}원")
                lines.append(f"   📦 보유종목: {len(positions)}개")

                for code, pos, curr, entry, qty, rate, pnl in rows:
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

    # ── !리나등록 / !리나등록해제 (대장 키움 수동매수 트레일링 알림) ──
    # ★ 2026-10-03 대장 지정 — 키움으로 직접 산 종목을 등록하면 익절
    #   (트레일링)만 기계가 체크해서 알림을 주고, 손절 판단은 대장이
    #   직접. 키움은 주문실행 API가 없어 매도는 절대 대신 못 해주니
    #   "타이밍은 놓치지 않게, 실행은 사람이" 구조.
    # ★ "등록 종목명"/"해제 종목명" 자연어 버전도 같이 지원(대장 지정) —
    #   candidate_pool.get_stock_code()로 이름→코드 변환, 가격은 항상
    #   현재가로 등록(자연어 경로는 진입가 직접지정 미지원, 필요하면
    #   !리나등록으로).
    # ── !리나등록현황 (대장 — "하루에 4~5종목도 등록하니까") ──────
    if message.content.startswith("!리나등록현황") or message.content.startswith("등록현황"):
        async with message.channel.typing():
            watches = _load_manual_watches()
            if not watches:
                await send_safe_message(message.channel, "📭 현재 등록된 종목 없어.")
                return
            try:
                from kis_api import KisAPI
                api = KisAPI()
                lines = [f"📋 **리나등록 현황** ({len(watches)}종목)"]
                for code, w in watches.items():
                    name  = w.get("name", code)
                    entry = w.get("entry_price", 0)
                    # ★ 2026-10-06 — 동기 KIS 호출을 await 없이 직접 호출해
                    #   종목 수만큼 이벤트루프를 블로킹하던 부분(형제
                    #   Opus 리뷰로 발견).
                    mdata = await asyncio.to_thread(api.get_market_data, code) or {}
                    price = float(mdata.get("stck_prpr", 0) or 0)
                    raw_rate = (price - entry) / entry * 100 if entry else 0
                    net_rate = raw_rate - MANUAL_WATCH_FEE_DRAG_PCT
                    emoji = "📈" if net_rate >= 0 else "📉"
                    trail = f" (트레일링 고점 {w['peak_price']:,.0f})" if w.get("peak_price") else ""
                    lines.append(f"   {emoji} {name}({code}) {net_rate:+.2f}% "
                                 f"| 평단가 {entry:,.0f} → 현재가 {price:,.0f}{trail}")
                await send_safe_message(message.channel, "\n".join(lines))
            except Exception as e:
                await send_safe_message(message.channel, f"❌ 등록현황 조회 오류: {e}")
        return

    if message.content.startswith("!리나등록해제") or message.content.startswith("해제 "):
        parts = message.content.split()
        if len(parts) < 2:
            await send_safe_message(message.channel, "사용법: `!리나등록해제 종목코드 [매도가]` 또는 `해제 종목명 [매도가]`")
            return
        arg = parts[1].strip()
        code = arg if arg.isdigit() else get_stock_code(arg)
        if not code:
            await send_safe_message(message.channel, f"❌ '{arg}' 종목코드를 못 찾았어.")
            return
        sell_price = None
        if len(parts) >= 3:
            # ★ 2026-10-06 — "71,500"처럼 쉼표 들어간 가격을 float()에
            #   그대로 넣으면 ValueError가 나서 매도가 자체가 조용히
            #   무시되고(수익률 계산 없이 그냥 해제됨) 사용자는 자기가
            #   입력한 가격이 반영이 안 됐는지도 모르는 상태였음(형제
            #   Opus 리뷰로 발견). 쉼표 제거 후 재시도, 그래도 실패하면
            #   안내 메시지로 알림.
            try:
                sell_price = float(parts[2].replace(",", ""))
            except ValueError:
                await send_safe_message(message.channel, f"⚠️ 매도가 '{parts[2]}' 인식 실패 — 숫자만 입력해줘(평단가 없이 해제 처리할게).")
        await _deregister_manual_watch(message.channel, code, sell_price)
        return

    if message.content.startswith("!리나등록") or message.content.startswith("등록 "):
        parts = message.content.split()
        if len(parts) < 2:
            await send_safe_message(message.channel, "사용법: `!리나등록 종목코드 [평단가] [종목명]` 또는 `등록 종목명 [평단가]`\n"
                                     "평단가 생략하면 현재가로 등록해(분할매수 완료 후 평단가로 등록 권장).")
            return
        arg = parts[1].strip()
        code = arg if arg.isdigit() else get_stock_code(arg)
        if not code:
            await send_safe_message(message.channel, f"❌ '{arg}' 종목코드를 못 찾았어.")
            return
        buy_price = None
        name_override = None
        if len(parts) >= 3:
            # ★ 2026-10-06 — 매도가와 동일한 쉼표 파싱 버그(위 해제 참고).
            try:
                buy_price = float(parts[2].replace(",", ""))
            except ValueError:
                await send_safe_message(message.channel, f"⚠️ 평단가 '{parts[2]}' 인식 실패 — 숫자만 입력해줘(현재가로 등록할게).")
        if len(parts) >= 4:
            name_override = parts[3].strip()
        await _register_manual_watch(message.channel, code, buy_price, name_override)
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
                    # ★ 2026-10-06 — profit_rate/profit_krw가 전부 NULL인
                    #   bot_type이 있으면 SUM()이 NULL을 반환해서 total이
                    #   None이 되고, 밑의 "total > 0" 비교에서 TypeError가
                    #   나 명령 전체가 실패하던 버그(형제 Opus 리뷰로 발견).
                    avg   = avg or 0
                    total = total or 0
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
                # ★ 2026-10-06 — 구글 캘린더 API 호출(동기)을 await 없이
                #   직접 호출하던 부분(형제 Opus 리뷰로 발견) — 여러 줄
                #   일정을 한 번에 추가하면 줄 수만큼 누적 블로킹됨.
                res = await asyncio.to_thread(add_google_calendar_event, parts[1], parts[0])
                result_messages.append(res)
            else:
                result_messages.append(f"⚠️ 형식 오류: '{line}' (YYYY-MM-DD 내용)")
        if result_messages: await message.channel.send("\n".join(result_messages))
        return

    user_input = message.content.replace(f'<@{client.user.id}>', '').strip()
    if not user_input: return

    is_dm = isinstance(message.channel, discord.DMChannel)
    is_called = is_dm or ("리나" in message.content) or client.user.mentioned_in(message)
    if not is_called: return

    async with message.channel.typing():
        # ★ 2026-10-06 — "원" 포함 여부로만 판단하면 "병원"/"원래" 같은
        #   무관한 단어에도 걸렸고, 금액은 "첫 번째 숫자 덩어리"를 그대로
        #   써서 "10월 5일 커피 4500원"이 10원으로, "4,500원"이 쉼표 때문에
        #   4원으로 기록되는 등 가계부가 엉뚱하게 꼬이는 버그가 있었음
        #   (형제 Opus 리뷰로 발견). "숫자(쉼표 허용)+원"이 실제로 붙어있는
        #   패턴만 금액으로 인정하도록 교체.
        _amount_match = re.search(r'(\d[\d,]*)\s*원', user_input)
        if _amount_match and any(kw in user_input for kw in ["원", "지출", "샀어", "보냈어"]):
            num  = int(_amount_match.group(1).replace(",", ""))
            item = re.sub(r'\d[\d,]*\s*원', '',
                           user_input.replace("리나야", "").replace("샀어", "")).strip() or "기타"
            r_type = "입금" if "입금" in user_input else "출금"
            context_data = f"[시스템 가계부]: {add_finance_record(r_type, item, num)}"
            prompt = f"{context_data}\n\n질문: {user_input}\n친절하게 답해줘."
        else:
            context_data = await web_search_hybrid(user_input)
            
            if context_data and "실패" not in context_data and "텅 비어" not in context_data:
                if any(k in user_input for k in ["뉴스", "속보", "mbn", "아침"]):
                    지시문 = "수집된 실제 데이터(기사 내용)만을 바탕으로 다정하게 요약 보고해줘. 절대 지어내지 마."
                else:
                    지시문 = "수집된 실제 데이터를 바탕으로 대장에게 친절하게 요약해서 알려줘."

                prompt = f"[파이썬 실시간 수집 데이터]:\n{context_data}\n\n[사용자 질문]: {user_input}\n\n[지시문]: {지시문}"
            else:
                # ★ 2026-10-06 — append만 하고 자르는 코드가 없어서
                #   채널마다 대화기록이 무한히 쌓이던 메모리 누수(형제
                #   Opus 리뷰로 발견) — MAX_MEMORY를 실제로 적용해 상한선
                #   을 둠(system 메시지는 유지, 나머지는 최근 N개만).
                history = chat_memory.setdefault(message.channel.id, [{"role": "system", "content": SYSTEM_PROMPT}])
                history.append({"role": "user", "content": user_input})
                if len(history) > MAX_MEMORY + 1:
                    chat_memory[message.channel.id] = [history[0]] + history[-MAX_MEMORY:]
                prompt = user_input

        try:
            reply_text = await asyncio.to_thread(_call_llm, prompt, max_tokens=1500, system=SYSTEM_PROMPT)
            await send_safe_message(message.channel, reply_text or "에러 발생!", reply_to=message)
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