"""
KIS10_RealTrade — 한국투자증권 OpenAPI 기반 자동매매 봇
========================================================
[매도 전략]
  1차 익절 +5%  → 보유량의 30% 매도
  2차 익절 +10% → 보유량의 40% 매도
  나머지 30%    → 트레일링 스탑 (고점 대비 -5%) 또는 종가 전량 매도

[분할매수 전략]
  1차 매수: BUY_1ST_AMT (20만원) 지정가 매수
  2차 매수: 1차 매수가 ±2% 도달 시 BUY_2ND_AMT (10만원) 추가 매수
           약세장(weak/stop)에서는 -2% 물타기 금지, +2% 추격만 허용

[종가 매도 전략 - 15:15]
  1차 익절 후(stage >= 1)        → 전량 종가매도
  1차 익절 전 + 수익률 -1%~+1%  → 횡보 정리 종가매도 (시드 회수)
  1차 익절 전 + 수익률 > +1%    → 1차 익절(+5%) 계속 대기
  1차 익절 전 + 수익률 < -1%    → 손절선(-5%) 계속 대기

[약세장 방어]
  코스피 -1.5% 이하 (weak)  → 신규 매수 중단 / 손절선 -3% 강화
  코스피 -2.5% 이하 (stop)  → 전면 관망 / 긴급 손절
  당일 손절 2회 도달         → 매수 자동 정지 (!시작 으로 재개)

[paused 상태 동작]
  신규 매수만 중단 / 보유종목 매도 체크는 계속 실행
  긴급 !매도 명령도 paused 상태에서 즉시 실행

[미체결 재매수 방지]
  매수 시도 즉시 sold_today 등록 → 미체결이어도 당일 재매수 금지
  sold_today → bot_state.json 저장 → 재시작해도 유지

[관심종목]
  키키에서 !관심 종목코드 → 조건검색식 외 종목도 분석 풀에 추가

[변경 이력]
  2026-04-24
    - 조건검색식 seq 0~3 으로 수정 (0부터 시작)
    - 대형주 제외 기준 5조(50,000억)로 상향
    - 거래량 필터 5만→3만주 완화
    - 회전율 필터 3%→2% 완화
    - MAX_POSITIONS 4→5
    - BUY_1ST_AMT 20만원 / BUY_2ND_AMT 10만원 분할매수 추가
    - 매수 방식 시장가→지정가(현재가+1호가) 변경
    - 호가단위 자동 계산 (buy()에 인라인 처리)
    - sold_today bot_state.json 영구 저장 (재시작 복원)
    - 미체결 재매수 방지 (매수 시도 즉시 sold_today 등록)
    - 약세장 방어 시스템 추가 (weak/stop 모드)
    - 당일 손절 MAX_DAILY_LOSS(2회) 초과 시 자동 정지
    - 15:15 횡보 종목(-1%~+1%) 종가 정리 매도 추가
    - 관심종목 watchlist 기능 추가 (키키 !관심 명령어 연동)
  2026-04-27
    - paused 상태에서도 보유종목 매도 체크 계속 실행 (핵심 버그 수정)
    - paused 상태에서 긴급 !매도 명령 즉시 실행
    - !시작 명령 시 daily_loss_count 초기화 (반복정지 버그 수정)
    - 키키 시스템 프롬프트에 사용자 명령 최우선 규칙 추가
"""

import os
import time
import json
import sqlite3
import asyncio
import requests
import datetime
from dotenv import load_dotenv
from anthropic import Anthropic
from hiaku import HaikuAI

load_dotenv()


# ============================================================
# 상수
# ============================================================
MAX_POSITIONS    = 5        # 최대 보유 종목 수
LIMIT_PER_STOCK  = 300000   # 종목당 최대 투자 한도 (원)
BUY_1ST_AMT      = 200000   # 1차 매수 금액 (20만원)
BUY_2ND_AMT      = 100000   # 2차 매수 금액 (10만원)
BUY_2ND_THRESHOLD = -0.02   # 2차 매수 진입 조건 (1차 매수가 대비 ±2%)
BUY_SCORE_MIN    = 45       # 후보 선정 최소 점수
BUY_SCORE_ENTER  = 55       # 실제 매수 진입 최소 점수
LOOP_SLEEP       = 30       # 메인 루프 슬립 (초)
TOKEN_TTL        = 86400    # KIS 토큰 유효시간 (초, 24시간)
POOL_SIZE        = 150      # 분석 대상 종목 풀 크기

# ── 매도 전략 상수 ──────────────────────────────────────────
SELL_1ST_RATE   = 0.05   # 1차 익절 기준 수익률 (+5%)
SELL_1ST_QTY    = 0.30   # 1차 익절 매도 비율 (30%)
SELL_2ND_RATE   = 0.10   # 2차 익절 기준 수익률 (+10%)
SELL_2ND_QTY    = 0.40   # 2차 익절 매도 비율 (40%)
TRAIL_STOP      = 0.05   # 고점 대비 트레일링 스탑 하락폭 (-5%)
STOP_LOSS_BASIC = -0.05  # 기본 손절 (-5%, 1차 익절 전)
STOP_LOSS_AFTER = -0.05  # 익절 후 손절 (-5%, 1차 익절 후)
EOD_SELL_TIME   = "1515" # 종가 매도 시각 (15:15)

# ── 약세장 방어 ─────────────────────────────────────────────
MARKET_WEAK_THRESH = -1.5   # 관망 전환 기준: 코스피 -1.5%
MARKET_STOP_THRESH = -2.5   # 매매 중단 기준: 코스피 -2.5%
STOP_LOSS_WEAK     = -0.03  # 약세장 손절선 (-3%)
MAX_DAILY_LOSS     = 2      # 당일 최대 손절 횟수
BUY_2ND_WEAK_ONLY  = True   # 약세장에서 2차 매수는 +2% 추격만 허용

# ── 거래 시간대 ─────────────────────────────────────────────
PRE_MARKET_START  = "0800"
PRE_MARKET_END    = "0900"
REG_MARKET_START  = "0900"
REG_MARKET_END    = "1530"
SLEEP_INTERVAL    = 60

# ── 파일 경로 ───────────────────────────────────────────────
AI_CACHE_DB    = "ai_cache.db"
BOT_STATE_FILE = "bot_state.json"
AI_CACHE_DAYS  = 7
TRADE_HIST_DB  = "trade_history.db"


# ============================================================
# AI 비서 연동 헬퍼
# ============================================================
def _read_bot_state():
    try:
        if os.path.exists(BOT_STATE_FILE):
            with open(BOT_STATE_FILE, "r") as f:
                return json.load(f)
    except Exception:
        pass
    return {"paused": False, "score_enter": BUY_SCORE_ENTER,
            "pending_cmd": None, "cmd_result": None}

def _write_bot_status(status: dict):
    try:
        state = _read_bot_state()
        state["last_status"] = status
        state["last_update"] = datetime.datetime.now().strftime("%H:%M:%S")
        with open(BOT_STATE_FILE, "w") as f:
            json.dump(state, f, ensure_ascii=False)
    except Exception as e:
        print(f"⚠️ 상태 파일 저장 오류: {e}")

def _write_cmd_result(result: str):
    try:
        state = _read_bot_state()
        state["cmd_result"]  = result
        state["pending_cmd"] = None
        with open(BOT_STATE_FILE, "w") as f:
            json.dump(state, f, ensure_ascii=False)
    except Exception as e:
        print(f"⚠️ 명령 결과 저장 오류: {e}")


# ============================================================
# 메인 클래스
# ============================================================
class KIS10_RealTrade:

    def __init__(self):
        print("🚀 [영암9 LIVE] 안정화 엔진 가동")

        self.haiku = HaikuAI()
        self.appkey  = os.getenv("KIS_APPKEY")
        self.secret  = os.getenv("KIS_SECRET")
        self.cano    = os.getenv("KIS_CANO")
        self.acnt    = os.getenv("KIS_ACNT_PRDT_CD")
        self.discord = os.getenv("DISCORD_WEBHOOK_URL")
        self.base_url = "https://openapi.koreainvestment.com:9443"
       
        # 키움 WebSocket 조건검색 설정
        self.kiwoom_appkey    = os.getenv("KIWOOM_APPKEY", "")
        self.kiwoom_secretkey = os.getenv("KIWOOM_SECRETKEY", "")
        self.kiwoom_token     = ""
        self.kiwoom_token_at  = 0
        self._kiwoom_enabled  = bool(self.kiwoom_appkey and self.kiwoom_secretkey)
        if self._kiwoom_enabled:
            print("✅ 키움 조건검색 연동 활성화")
        else:
            print("⚠️ 키움 API 없음 → 한투 조건검색 사용")

        self.llm = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

        self.token           = self.get_token()
        self.token_issued_at = time.time()

        self.positions   = {}
        self.score_cache = {}
        self.buy_context = {}

        # 단계별 익절 + 분할매수 추적
        # { 종목코드: {
        #     peak_rate  : 최고 수익률
        #     stage      : 0=초기 / 1=1차익절후 / 2=2차익절후
        #     remain_qty : 1차 익절 후 잔여 수량
        #     buy2_done  : 2차 매수 완료 여부
        #     buy1_price : 1차 매수가 (2차 매수 진입 기준)
        # } }
        self.peak_tracker = {}

        # 기술지표/수급 캐시 {코드: (데이터, 캐시시간)}
        self._tech_cache = {}   # MA/RSI 5분 캐시
        self._flow_cache = {}   # 수급 10분 캐시

        # 당일 재매수 금지
        self.sold_today = {}
        self._sold_today_date = datetime.datetime.now().strftime("%Y-%m-%d")

        # 약세장 방어
        self.daily_loss_count = 0
        self.market_status    = "normal"
        self.market_rate      = 0.0

        self.limit_per_stock = LIMIT_PER_STOCK

        self._init_ai_db()
        self._init_trade_db()

    # ============================================================
    # AI 분석 캐시 DB
    # ============================================================
    def _init_ai_db(self):
        try:
            conn = sqlite3.connect(AI_CACHE_DB)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS ai_analysis (
                    code        TEXT PRIMARY KEY,
                    score       INTEGER NOT NULL,
                    reason      TEXT,
                    analyzed_at TEXT NOT NULL
                )
            """)
            conn.commit()
            conn.close()
            print(f"✅ AI DB 초기화 완료 ({AI_CACHE_DB})")
        except Exception as e:
            print(f"❌ AI DB 초기화 오류: {e}")

    def _get_ai_cache(self, code):
        try:
            conn   = sqlite3.connect(AI_CACHE_DB)
            cursor = conn.execute(
                "SELECT score, reason, analyzed_at FROM ai_analysis WHERE code = ?", (code,)
            )
            row = cursor.fetchone()
            conn.close()
            if not row:
                return None
            score, reason, analyzed_at = row
            age_days = (datetime.datetime.now() - datetime.datetime.fromisoformat(analyzed_at)).days
            if age_days >= AI_CACHE_DAYS:
                return None
            return {"score": score, "reason": reason, "analyzed_at": analyzed_at}
        except Exception as e:
            print(f"⚠️ AI DB 조회 오류 {code}: {e}")
            return None

    def _save_ai_cache(self, code, score, reason):
        try:
            now  = datetime.datetime.now().isoformat(timespec="seconds")
            conn = sqlite3.connect(AI_CACHE_DB)
            conn.execute("""
                INSERT INTO ai_analysis (code, score, reason, analyzed_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(code) DO UPDATE SET
                    score=excluded.score, reason=excluded.reason, analyzed_at=excluded.analyzed_at
            """, (code, score, reason, now))
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"⚠️ AI DB 저장 오류 {code}: {e}")

    def _clean_ai_db(self):
        try:
            today = datetime.datetime.now().strftime("%Y-%m-%d")
            conn  = sqlite3.connect(AI_CACHE_DB)
            cur   = conn.execute("DELETE FROM ai_analysis WHERE analyzed_at < ?", (today,))
            deleted = cur.rowcount
            conn.commit()
            conn.close()
            if deleted:
                print(f"🗑️ AI DB 전일 캐시 {deleted}개 삭제")
        except Exception as e:
            print(f"⚠️ AI DB 정리 오류: {e}")

    # ============================================================
    # 토큰 관리
    # ============================================================
    def get_token(self):
        url  = f"{self.base_url}/oauth2/tokenP"
        body = {"grant_type": "client_credentials", "appkey": self.appkey, "appsecret": self.secret}
        try:
            token = requests.post(url, json=body).json().get("access_token", "")
            print("✅ 토큰 발급 완료")
            return token
        except Exception as e:
            print(f"❌ 토큰 발급 실패: {e}")
            return ""

    def _refresh_token_if_needed(self):
        if time.time() - self.token_issued_at > TOKEN_TTL - 3600:
            print("🔄 토큰 갱신 중...")
            self.token           = self.get_token()
            self.token_issued_at = time.time()

    def _get_kiwoom_token(self) -> str:
        """키움 접근토큰 발급 (24시간 캐시)."""
        if self.kiwoom_token and time.time() - self.kiwoom_token_at < 82800:
            return self.kiwoom_token
        try:
            res = requests.post(
                "https://api.kiwoom.com/oauth2/token",
                json={
                    "grant_type": "client_credentials",
                    "appkey":     self.kiwoom_appkey,
                    "secretkey":  self.kiwoom_secretkey,
                },
                timeout=10,
            ).json()
            self.kiwoom_token    = res.get("token", "")
            self.kiwoom_token_at = time.time()
            print(f"✅ 키움 토큰 발급 완료")
            return self.kiwoom_token
        except Exception as e:
            print(f"⚠️ 키움 토큰 발급 실패: {e}")
            return ""

    async def _get_kiwoom_condition_codes(self) -> list:
        """키움 WebSocket으로 모든 조건검색식 종목 조회."""
        import websockets as _ws
        token = self._get_kiwoom_token()
        if not token:
            return []

        codes = []
        seen  = set()
        try:
            async with _ws.connect("wss://api.kiwoom.com:10000/api/dostk/websocket") as ws:
                # LOGIN
                await ws.send(json.dumps({"trnm": "LOGIN", "token": token}))
                res = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
                if res.get("return_code") != 0:
                    print(f"⚠️ 키움 로그인 실패: {res.get('return_msg')}")
                    return []

                # 조건검색식 목록
                await ws.send(json.dumps({"trnm": "CNSRLST"}))
                cond_list = []
                while True:
                    try:
                        res = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
                        if res.get("trnm") == "PING":
                            await ws.send(json.dumps(res))
                            continue
                        if res.get("trnm") == "CNSRLST":
                            cond_list = res.get("data", [])
                            break
                    except asyncio.TimeoutError:
                        break

                print(f"  🔍 키움 조건검색식: {len(cond_list)}개")

                # 각 조건식 실행
                for cond in cond_list:
                    seq  = cond[0] if isinstance(cond, list) else cond.get("seq", "")
                    name = cond[1] if isinstance(cond, list) else cond.get("name", "")
                    await ws.send(json.dumps({
                        "trnm": "CNSRREQ", "seq": seq,
                        "search_type": "0", "stex_tp": "K",
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
                                raw  = item.get("9001", "") if isinstance(item, dict) else (item[0] if item else "")
                                code = raw.lstrip("A") if raw.startswith("A") else raw
                                iname = item.get("302", "") if isinstance(item, dict) else (item[1] if len(item) > 1 else "")
                                if code and code not in seen:
                                    seen.add(code)
                                    codes.append(code)
                                    if not hasattr(self, "code_name_map"):
                                        self.code_name_map = {}
                                    self.code_name_map[code] = iname
                                    fetched += 1
                            if res.get("cont_yn") != "Y":
                                break
                        except asyncio.TimeoutError:
                            break
                    print(f"  📊 키움 조건검색 [{seq}]{name}: +{fetched}개 (누적 {len(codes)}개)")

        except Exception as e:
            print(f"⚠️ 키움 WebSocket 오류: {e}")

        return codes

    # ============================================================
    # 디스코드 알림
    # ============================================================
    def _code_to_name(self, code: str) -> str:
        return getattr(self, "code_name_map", {}).get(code, code)

    def notify(self, msg):
        print(msg)
        if self.discord:
            try:
                requests.post(self.discord, json={"content": msg}, timeout=3)
            except Exception as e:
                print(f"⚠️ 웹훅 전송 실패: {e}")
        bot_token  = os.getenv("DISCORD_BOT_TOKEN")
        channel_id = os.getenv("DISCORD_CHANNEL_ID")
        if bot_token and channel_id:
            try:
                requests.post(
                    f"https://discord.com/api/v10/channels/{channel_id}/messages",
                    headers={"Authorization": f"Bot {bot_token}"},
                    json={"content": msg}, timeout=3
                )
            except Exception as e:
                print(f"⚠️ 봇 채널 전송 실패: {e}")

    # ============================================================
    # KIS API 공통
    # ============================================================
    def get_hashkey(self, data):
        url = f"{self.base_url}/uapi/hashkey"
        headers = {"Content-Type": "application/json", "appkey": self.appkey, "appsecret": self.secret}
        try:
            return requests.post(url, headers=headers, data=json.dumps(data)).json().get("HASH", "")
        except Exception as e:
            print(f"⚠️ 해시키 발급 실패: {e}")
            return ""

    # ============================================================
    # 계좌 조회
    # ============================================================
    def get_buyable_cash(self):
        url = f"{self.base_url}/uapi/domestic-stock/v1/trading/inquire-balance"
        headers = {
            "authorization": f"Bearer {self.token}",
            "appkey": self.appkey, "appsecret": self.secret, "tr_id": "TTTC8434R",
        }
        params = {"CANO": self.cano, "ACNT_PRDT_CD": self.acnt, "INQR_DVSN": "01"}
        try:
            res     = requests.get(url, headers=headers, params=params).json()
            output2 = res.get("output2", [{}])[0] if res.get("output2") else {}
            cash = (
                output2.get("dnca_tot_amt")
                or output2.get("prvs_rcdl_excc_amt")
                or output2.get("tot_evlu_amt") or 0
            )
            return int(float(cash))
        except Exception as e:
            print(f"❌ 예수금 조회 오류: {e}")
            return 0

    def get_psbl_order_cash(self, code, price=0):
        url = f"{self.base_url}/uapi/domestic-stock/v1/trading/inquire-psbl-order"
        headers = {
            "authorization": f"Bearer {self.token}",
            "appkey": self.appkey, "appsecret": self.secret, "tr_id": "TTTC8908R",
        }
        params = {
            "CANO": self.cano, "ACNT_PRDT_CD": self.acnt,
            "PDNO": code, "ORD_UNPR": str(int(price)),
            "ORD_DVSN": "01", "CMA_EVLU_AMT_ICLD_YN": "N", "OVRS_ICLD_YN": "N",
        }
        try:
            res    = requests.get(url, headers=headers, params=params).json()
            output = res.get("output", {})
            cash   = (
                output.get("nrcvb_buy_amt")
                or output.get("max_buy_amt")
                or output.get("ord_psbl_cash") or 0
            )
            result = int(float(cash))
            print(f"   💰 주문가능 {code}(price=0): {result:,}원")
            return result
        except Exception as e:
            print(f"❌ 주문가능금액 조회 오류: {e}")
            return 0

    def get_current_positions(self):
        url = f"{self.base_url}/uapi/domestic-stock/v1/trading/inquire-balance"
        headers = {
            "authorization": f"Bearer {self.token}",
            "appkey": self.appkey, "appsecret": self.secret, "tr_id": "TTTC8434R",
        }
        params = {"CANO": self.cano, "ACNT_PRDT_CD": self.acnt, "INQR_DVSN": "01"}
        try:
            res = requests.get(url, headers=headers, params=params).json()
            pos = {}
            for item in res.get("output1", []):
                qty = int(item.get("hldg_qty", 0))
                if qty <= 0:
                    continue
                code = item.get("pdno")
                avg  = float(item.get("pchs_avg_pric", 0))
                pos[code] = {"entry_price": avg, "qty": qty}
            return pos
        except Exception as e:
            print(f"❌ 보유종목 조회 오류: {e}")
            return {}

    # ============================================================
    # 시세 조회
    # ============================================================
    def get_market_data(self, code):
        url = f"{self.base_url}/uapi/domestic-stock/v1/quotations/inquire-price"
        headers = {
            "authorization": f"Bearer {self.token}",
            "appkey": self.appkey, "appsecret": self.secret, "tr_id": "FHKST01010100",
        }
        params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code}
        try:
            return requests.get(url, headers=headers, params=params).json().get("output")
        except Exception as e:
            print(f"⚠️ 시세 조회 오류 {code}: {e}")
            return None

    # ============================================================
    # 시장 지수 조회 (약세장 방어용)
    # ============================================================
    def get_market_index(self):
        """코스피·코스닥 당일 등락률 조회."""
        result = {"kospi": 0.0, "kosdaq": 0.0}
        for market_code, key in [("0001", "kospi"), ("1001", "kosdaq")]:
            url = f"{self.base_url}/uapi/domestic-stock/v1/quotations/inquire-index-price"
            headers = {
                "authorization": f"Bearer {self.token}",
                "appkey": self.appkey, "appsecret": self.secret, "tr_id": "FHPUP03500100",
            }
            params = {"FID_COND_MRKT_DIV_CODE": "U", "FID_INPUT_ISCD": market_code}
            try:
                res  = requests.get(url, headers=headers, params=params).json()
                rate = float(res.get("output", {}).get("bstp_nmix_prdy_ctrt", 0) or 0)
                result[key] = rate
            except Exception as e:
                print(f"⚠️ 지수 조회 오류 {key}: {e}")
        return result

    def _update_market_status(self):
        """코스피 등락률로 시장 상태 갱신.
        API 오류로 0.0% 반환 시 기존 상태 유지 (오판 방지).
        """
        idx    = self.get_market_index()
        kospi  = idx["kospi"]
        kosdaq = idx["kosdaq"]

        # API 오류로 0.0% 반환 시 기존 상태 유지
        if kospi == 0.0 and kosdaq == 0.0:
            print(f"⚠️ 시장지수 조회 실패 — 기존 상태 유지: {self.market_status}")
            return self.market_status

        self.market_rate = kospi

        if kospi <= MARKET_STOP_THRESH:
            status = "stop"
            emoji  = "🚨"
        elif kospi <= MARKET_WEAK_THRESH:
            status = "weak"
            emoji  = "⚠️"
        else:
            status = "normal"
            emoji  = "✅"

        if status != self.market_status:
            self.notify(
                f"{emoji} 시장상태 변경: {self.market_status} → {status}\n"
                f"코스피: {kospi:+.2f}% | 코스닥: {kosdaq:+.2f}%"
            )
        self.market_status = status
        print(f"📊 시장상태: {status} | 코스피:{kospi:+.2f}% | 코스닥:{kosdaq:+.2f}%")
        return status

    # ============================================================
    # 종목 풀 조회
    # ============================================================
    PSEARCH_SEQS = ["0", "1", "2", "3"]

    def get_top50_codes(self):
        # ── 키움 조건검색 우선 사용 ──────────────────────────
        if self._kiwoom_enabled:
            try:
                loop  = asyncio.new_event_loop()
                codes = loop.run_until_complete(self._get_kiwoom_condition_codes())
                loop.close()
                if codes:
                    # watchlist 추가
                    _st = _read_bot_state()
                    for wcode in _st.get("watchlist", []):
                        if wcode not in codes and wcode.isdigit():
                            codes.append(wcode)
                            print(f"  👀 관심종목 추가: {wcode}")
                    result = codes[:POOL_SIZE]
                    print(f"🎯 종목 풀 (키움): {len(result)}개 확정")
                    return result
                else:
                    print("⚠️ 키움 조건검색 결과 없음 → 한투 폴백")
            except Exception as e:
                print(f"⚠️ 키움 조건검색 오류: {e} → 한투 폴백")

        # ── 한투 조건검색 (폴백) ─────────────────────────────
        url    = f"{self.base_url}/uapi/domestic-stock/v1/quotations/psearch-result"
        hts_id = os.getenv("KIS_HTS_ID", "")
        seen   = set()
        codes  = []

        if not hasattr(self, "code_name_map"):
            self.code_name_map = {}

        skip_keywords = [
            "KODEX", "TIGER", "KBSTAR", "ARIRANG", "HANARO",
            "KOSEF", "TREX", "SOL", "ACE", "PLUS",
            "인버스", "레버리지", "ETN", "선물", "RISE", "TIME",
        ]

        for seq in self.PSEARCH_SEQS:
            headers = {
                "Content-Type":  "application/json; charset=utf-8",
                "authorization": f"Bearer {self.token}",
                "appKey":        self.appkey,
                "appSecret":     self.secret,
                "tr_id":         "HHKST03900400",
                "custtype":      "P",
            }
            params = {"user_id": hts_id, "seq": seq}
            try:
                res = requests.get(url, headers=headers, params=params).json()
                if res.get("rt_cd") != "0":
                    print(f"⚠️ 조건검색 seq={seq} 오류: {res.get('msg1')}")
                    continue
                fetched = 0
                for item in res.get("output2", []):
                    code = item.get("code", "").strip()
                    name = item.get("name", "").strip()
                    if not code or code in seen or not code.isdigit():
                        continue
                    if any(kw in name for kw in skip_keywords):
                        continue
                    seen.add(code)
                    codes.append(code)
                    self.code_name_map[code] = name
                    fetched += 1
                print(f"  📊 조건검색 seq={seq}: +{fetched}개 (누적 {len(codes)}개)")
            except Exception as e:
                print(f"❌ 조건검색 seq={seq} 예외: {e}")

        if len(codes) < 10:
            print(f"⚠️ 조건검색 결과 부족({len(codes)}개) → 순위 API 보완")
            codes += self._get_rank_codes(seen)

        # ── 관심종목(watchlist) 추가 ──────────────────────────
        _st = _read_bot_state()
        watchlist = _st.get("watchlist", [])
        for wcode in watchlist:
            if wcode not in seen and wcode.isdigit():
                seen.add(wcode)
                codes.append(wcode)
                print(f"  👀 관심종목 추가: {wcode}")

        result = codes[:POOL_SIZE]
        print(f"🎯 종목 풀: {len(result)}개 확정")
        return result

    def _get_rank_codes(self, seen: set) -> list:
        url  = f"{self.base_url}/uapi/domestic-stock/v1/quotations/volume-rank"
        skip_keywords = [
            "KODEX", "TIGER", "KBSTAR", "ARIRANG", "HANARO",
            "KOSEF", "TREX", "SOL", "ACE", "PLUS", "인버스", "레버리지", "ETN", "선물",
        ]
        headers = {
            "Content-Type":  "application/json",
            "authorization": f"Bearer {self.token}",
            "appKey":        self.appkey,
            "appSecret":     self.secret,
            "tr_id":         "FHPST01710000",
        }
        params = {
            "FID_COND_MRKT_DIV_CODE": "J", "FID_COND_SCR_DIV_CODE": "20171",
            "FID_INPUT_ISCD": "0000", "FID_DIV_CLS_CODE": "0",
            "FID_BLNG_CLS_CODE": "0", "FID_TRGT_CLS_CODE": "111111111",
            "FID_TRGT_EXLS_CLS_CODE": "000000", "FID_INPUT_PRICE_1": "0",
            "FID_INPUT_PRICE_2": "0", "FID_VOL_CNT": "0", "FID_INPUT_DATE_1": "0",
        }
        codes = []
        try:
            res = requests.get(url, headers=headers, params=params).json()
            if res.get("rt_cd") == "0":
                for item in res.get("output", []):
                    code = item.get("mksc_shrn_iscd", "").strip()
                    name = item.get("hts_kor_isnm",  "").strip()
                    if not code or code in seen or not code.isdigit():
                        continue
                    if any(kw in name for kw in skip_keywords):
                        continue
                    seen.add(code)
                    codes.append(code)
                    self.code_name_map[code] = name
            print(f"  📊 거래량 순위 보완: +{len(codes)}개")
        except Exception as e:
            print(f"❌ 거래량 순위 API 예외: {e}")
        return codes

    # ============================================================
    # 기술적 지표
    # ============================================================
    def get_technical_indicators(self, stock_code):
        # 5분 캐시 확인
        if stock_code in self._tech_cache:
            cached_data, cached_time = self._tech_cache[stock_code]
            if time.time() - cached_time < 300:
                return cached_data

        url = f"{self.base_url}/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"
        headers = {
            "Content-Type":  "application/json",
            "authorization": f"Bearer {self.token}",
            "appKey":        self.appkey,
            "appSecret":     self.secret,
            "tr_id":         "FHKST03010100",
        }
        end_date   = datetime.datetime.now().strftime("%Y%m%d")
        start_date = (datetime.datetime.now() - datetime.timedelta(days=120)).strftime("%Y%m%d")
        params = {
            "fid_cond_mrkt_div_code": "J",
            "fid_input_iscd":         stock_code,
            "fid_input_date_1":       start_date,
            "fid_input_date_2":       end_date,
            "fid_period_div_code":    "D",
            "fid_org_adj_prc":        "0",
        }
        try:
            res     = requests.get(url, headers=headers, params=params).json()
            candles = res.get("output2", [])
            closes  = [int(x["stck_clpr"]) for x in candles if x.get("stck_clpr")]
            if len(closes) < 20:
                return {}

            def ma(n):
                return sum(closes[:n]) / n if len(closes) >= n else 0

            def rsi(period=14):
                if len(closes) < period + 1:
                    return 50
                gains  = [closes[i] - closes[i+1] for i in range(period) if closes[i] > closes[i+1]]
                losses = [abs(closes[i] - closes[i+1]) for i in range(period) if closes[i] <= closes[i+1]]
                avg_gain = sum(gains)  / period if gains  else 0
                avg_loss = sum(losses) / period if losses else 1
                rs = avg_gain / avg_loss if avg_loss != 0 else 0
                return 100 - (100 / (1 + rs))

            result = {"ma5": ma(5), "ma20": ma(20), "ma60": ma(60), "rsi": rsi()}
            self._tech_cache[stock_code] = (result, time.time())
            return result
        except Exception as e:
            print(f"⚠️ 기술지표 오류 {stock_code}: {e}")
            return {}

    # ============================================================
    # 수급 조회
    # ============================================================
    def get_investor_trend(self, stock_code):
        # 10분 캐시 확인
        if stock_code in self._flow_cache:
            cached_data, cached_time = self._flow_cache[stock_code]
            if time.time() - cached_time < 600:
                return cached_data

        def safe_int(v):
            try:   return int(v)
            except: return 0

        url = f"{self.base_url}/uapi/domestic-stock/v1/quotations/inquire-investor"
        headers = {
            "Content-Type":  "application/json",
            "authorization": f"Bearer {self.token}",
            "appKey":        self.appkey,
            "appSecret":     self.secret,
            "tr_id":         "FHKST01010900",
        }
        params = {"fid_cond_mrkt_div_code": "J", "fid_input_iscd": stock_code}
        try:
            res   = requests.get(url, headers=headers, params=params).json()
            items = res.get("output", [])
            if not items:
                return {}
            frgn = sum(safe_int(x.get("frgn_ntby_tr_pbmn")) for x in items[:5])
            orgn = sum(safe_int(x.get("orgn_ntby_tr_pbmn")) for x in items[:5])
            result = {"foreign_5d": frgn, "institution_5d": orgn}
            self._flow_cache[stock_code] = (result, time.time())
            return result
        except Exception as e:
            print(f"⚠️ 수급 조회 오류 {stock_code}: {e}")
            return {}

    # ============================================================
    # 매매 이력 DB
    # ============================================================
    def _init_trade_db(self):
        try:
            conn = sqlite3.connect(TRADE_HIST_DB)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    id             INTEGER PRIMARY KEY AUTOINCREMENT,
                    code           TEXT    NOT NULL,
                    stock_name     TEXT,
                    buy_price      REAL    NOT NULL,
                    buy_time       TEXT    NOT NULL,
                    sell_price     REAL,
                    sell_time      TEXT,
                    qty            INTEGER NOT NULL,
                    profit_rate    REAL,
                    sell_reason    TEXT,
                    ai_score       INTEGER,
                    ai_reason      TEXT,
                    change_rate    REAL,
                    volume_ratio   REAL,
                    rsi            REAL,
                    ma_aligned     INTEGER,
                    foreign_5d     REAL,
                    institution_5d REAL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS market_daily (
                    date          TEXT PRIMARY KEY,
                    kospi_change  REAL,
                    kosdaq_change REAL,
                    created_at    TEXT NOT NULL
                )
            """)
            conn.commit()
            conn.close()
            print(f"✅ 매매이력 DB 초기화 완료 ({TRADE_HIST_DB})")
        except Exception as e:
            print(f"❌ 매매이력 DB 초기화 오류: {e}")

    def _save_buy_history(self, code, buy_price, qty, ai_score, ai_reason, indicators, stock_name=""):
        try:
            now  = datetime.datetime.now().isoformat(timespec="seconds")
            conn = sqlite3.connect(TRADE_HIST_DB)
            conn.execute("""
                INSERT INTO trades
                    (code, stock_name, buy_price, buy_time, qty, ai_score, ai_reason,
                     change_rate, volume_ratio, rsi, ma_aligned, foreign_5d, institution_5d)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                code, stock_name, buy_price, now, qty, ai_score, ai_reason,
                indicators.get("change_rate",    0),
                indicators.get("volume_ratio",   0),
                indicators.get("rsi",            50),
                1 if indicators.get("ma5", 0) > indicators.get("ma20", 0) > indicators.get("ma60", 0) else 0,
                indicators.get("foreign_5d",     0),
                indicators.get("institution_5d", 0),
            ))
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"⚠️ 매수이력 저장 오류 {code}: {e}")

    def _save_sell_history(self, code, sell_price, sell_reason):
        try:
            now  = datetime.datetime.now().isoformat(timespec="seconds")
            conn = sqlite3.connect(TRADE_HIST_DB)
            row  = conn.execute("""
                SELECT id, buy_price FROM trades
                WHERE code = ? AND sell_price IS NULL
                ORDER BY id DESC LIMIT 1
            """, (code,)).fetchone()
            if not row:
                conn.close()
                return
            trade_id, buy_price = row
            profit_rate = (sell_price - buy_price) / buy_price * 100 if buy_price else 0
            conn.execute("""
                UPDATE trades SET sell_price=?, sell_time=?, profit_rate=?, sell_reason=?
                WHERE id=?
            """, (sell_price, now, round(profit_rate, 2), sell_reason, trade_id))
            conn.commit()
            conn.close()
            emoji = "✅" if profit_rate >= 0 else "❌"
            print(f"   {emoji} 이력저장 {code} | {profit_rate:+.2f}% | {sell_reason}")
        except Exception as e:
            print(f"⚠️ 매도이력 저장 오류 {code}: {e}")

    def _get_trade_history(self, code, limit=10):
        try:
            conn = sqlite3.connect(TRADE_HIST_DB)
            rows = conn.execute("""
                SELECT buy_time, sell_time, buy_price, sell_price,
                       profit_rate, sell_reason, ai_score, ai_reason
                FROM trades WHERE code=? AND sell_price IS NOT NULL
                ORDER BY id DESC LIMIT ?
            """, (code, limit)).fetchall()
            conn.close()
            return rows
        except Exception as e:
            print(f"⚠️ 이력 조회 오류 {code}: {e}")
            return []

    def _get_recent_performance(self, limit=20):
        try:
            conn = sqlite3.connect(TRADE_HIST_DB)
            rows = conn.execute("""
                SELECT profit_rate, sell_reason, ai_score FROM trades
                WHERE sell_price IS NOT NULL ORDER BY id DESC LIMIT ?
            """, (limit,)).fetchall()
            conn.close()
            if not rows:
                return None
            profits = [r[0] for r in rows]
            wins    = [p for p in profits if p >= 0]
            return {
                "total":      len(profits),
                "win_rate":   round(len(wins) / len(profits) * 100, 1),
                "avg_profit": round(sum(profits) / len(profits), 2),
                "best":       round(max(profits), 2),
                "worst":      round(min(profits), 2),
            }
        except Exception as e:
            print(f"⚠️ 성과 조회 오류: {e}")
            return None

    # ============================================================
    # 룰 기반 점수
    # ============================================================
    def get_rule_score(self, data):
        try:
            score       = 50
            change      = data.get("change_rate",    0)
            value       = data.get("trading_value",  0)
            vol_ratio   = data.get("volume_ratio",   0)
            vol_tnrt    = data.get("vol_tnrt",       0)
            rsi         = data.get("rsi",            50)
            ma5         = data.get("ma5",             0)
            ma20        = data.get("ma20",            0)
            ma60        = data.get("ma60",            0)
            foreign     = data.get("foreign_5d",      0)
            institution = data.get("institution_5d",  0)

            if   change > 5:  score += 25
            elif change > 3:  score += 15
            elif change > 1:  score += 5
            else:             score -= 10

            if   value > 300: score += 20
            elif value > 100: score += 10
            elif value < 30:  score -= 10

            if   vol_ratio > 300: score += 15
            elif vol_ratio > 200: score += 10
            elif vol_ratio > 120: score += 5
            elif vol_ratio < 50:  score -= 10

            if   vol_tnrt > 50: score += 10
            elif vol_tnrt > 20: score += 5

            if   45 < rsi < 65:  score += 10
            elif rsi > 75:       score -= 15
            elif rsi < 30:       score -= 5

            if   ma5 > ma20 > ma60 > 0: score += 15
            elif ma5 > ma20 > 0:        score += 7
            else:                       score -= 5

            if   foreign > 5000:  score += 10
            elif foreign > 1000:  score += 5
            elif foreign < -5000: score -= 5

            if   institution > 5000:  score += 10
            elif institution > 1000:  score += 5
            elif institution < -5000: score -= 5

            return max(0, min(100, score))
        except Exception as e:
            print(f"⚠️ 룰 점수 오류: {e}")
            return 0

    # ============================================================
    # Claude AI 분석
    # ============================================================
    def get_claude_score(self, code, data):
        _now_t  = datetime.datetime.now().strftime("%H%M")
        _valid  = ("0900" <= _now_t <= "1530") or ("1800" <= _now_t <= "2000")

        if _valid:
            cached = self._get_ai_cache(code)
            if cached:
                print(f"   💾 DB캐시 {code} | {cached['score']}점 | {cached['analyzed_at'][:10]}")
                return {"score": cached["score"], "reason": cached["reason"]}

        try:
            import json as _json, re as _re

            ma5  = data.get("ma5",  0)
            ma20 = data.get("ma20", 0)
            ma60 = data.get("ma60", 0)

            hist      = self._get_trade_history(code, limit=5)
            perf      = self._get_recent_performance(limit=20)
            hist_text = "\n[이 종목 과거 매매 이력 (최근 5건)]\n"
            if hist:
                for h in hist:
                    buy_t, sell_t, bp, sp, pr, sr, ais, air = h
                    hist_text += (
                        f"- {buy_t[:10]} 매수{bp:,}→매도{sp:,}원 "
                        f"수익률:{pr:+.1f}% 사유:{sr} AI점수:{ais}\n"
                    )
            else:
                hist_text = "\n[이 종목 과거 매매 이력] 없음\n"

            perf_text = ""
            if perf:
                perf_text = (
                    f"\n[봇 최근 {perf['total']}건 전체 성과]\n"
                    f"- 승률: {perf['win_rate']}% | 평균수익: {perf['avg_profit']:+.2f}%\n"
                    f"- 최고: {perf['best']:+.2f}% | 최저: {perf['worst']:+.2f}%\n"
                )

            prompt = (
                "당신은 한국 주식 단타~스윙 트레이더 전문가입니다.\n"
                "아래 종목 지표와 과거 매매 이력을 함께 분석해\n"
                "매수 점수(0~100)와 간단한 이유를 JSON으로 반환하세요.\n\n"
                f"[종목코드] {code}\n\n"
                "[기본 지표]\n"
                f"- 현재가: {data.get('current_price', 0):,}원\n"
                f"- 등락률: {data.get('change_rate', 0):+.2f}%\n"
                f"- 거래대금: {data.get('trading_value', 0):,}억원\n"
                f"- 거래량: {data.get('volume', 0):,}주\n"
                f"- 거래량증가율: {data.get('volume_ratio', 0):.1f}%\n"
                f"- 거래량회전율: {data.get('vol_tnrt', 0):.2f}%\n\n"
                "[기술적 지표]\n"
                f"- MA5:  {ma5:,.0f}원\n"
                f"- MA20: {ma20:,.0f}원\n"
                f"- MA60: {ma60:,.0f}원\n"
                f"- RSI14: {data.get('rsi', 50):.1f}\n"
                f"- MA 정배열(MA5>MA20>MA60): {ma5 > ma20 > ma60}\n\n"
                "[수급]\n"
                f"- 외국인 5일 순매수: {data.get('foreign_5d', 0):,}백만원\n"
                f"- 기관 5일 순매수: {data.get('institution_5d', 0):,}백만원\n\n"
                "[판단 기준]\n"
                "- 단타~스윙 전략 (보유기간 1일~1주)\n"
                "- 급등 초입 or 눌림목 반등 선호\n"
                "- 거래량 급증 + 등락률 양봉 + 수급 우호 → 높은 점수\n"
                "- 과매수(RSI>75) or 하락추세 → 낮은 점수\n"
                "- 상한가 근접(등락률>15%) → 0점\n"
                "- MA 데이터 없으면 MA 조건 무시, 등락률/거래량/수급으로만 판단\n"
                "- MA 없어도 등락률 양봉 + 거래량 급증 + 수급 우호면 55~70점 가능\n"
                "- 과거 손절 이력 있으면 신중하게 판단\n\n"
                + hist_text + perf_text
                + "\n반드시 아래 JSON 형식으로만 답하세요 (다른 텍스트 없이):\n"
                + '{"score": 75, "reason": "이유 한 줄"}'
            )

            res  = self.llm.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=256,
                messages=[{"role": "user", "content": prompt}]
            )
            text  = res.content[0].text.strip()
            text  = _re.sub(r"```(?:json)?", "", text).strip()
            match = _re.search(r'\{.*\}', text, _re.DOTALL)
            if not match:
                print(f"⚠️ Claude 응답 파싱 불가 {code}: [{text[:80]}]")
                return {"score": 0, "reason": "파싱실패-룰점수사용"}

            result = _json.loads(match.group())
            score  = max(0, min(100, int(result.get("score", 0))))
            reason = result.get("reason", "")

            if _valid:
                self._save_ai_cache(code, score, reason)

            return {"score": score, "reason": reason}

        except Exception as e:
            print(f"⚠️ Claude 분석 오류 {code}: {e}")
            return {"score": 0, "reason": "분석실패"}

    # ============================================================
    # 주문
    # ============================================================
    def buy(self, code: str, price: float, amount: int, is_second: bool = False):
        """지정가 매수. amount: 실제 투자할 금액(원)
        is_second=True 이면 2차 분할매수 → sold_today 등록 안 함.
        is_second=False(기본) 이면 1차 매수 → sold_today 즉시 등록.
        """
        psbl = self.get_psbl_order_cash(code, price=0)
        if psbl <= 0:
            print(f"⚠️ 주문가능금액 없음: {code}")
            return

        order_cash = min(psbl, amount)

        # 주가별 호가단위 계산 (KIS 규정)
        if   price < 1000:   hoga = 1
        elif price < 5000:   hoga = 5
        elif price < 10000:  hoga = 10
        elif price < 50000:  hoga = 50
        elif price < 100000: hoga = 100
        elif price < 500000: hoga = 500
        else:                hoga = 1000
        limit_price = int(price / hoga) * hoga + hoga  # 현재가 + 1호가

        qty = int(order_cash / (limit_price * 1.00015))
        if qty <= 0:
            print(f"⚠️ 수량 부족: {code} | {order_cash:,}원 | {price:,}원")
            return

        # ★ 1차 매수만 sold_today 등록 (2차 분할매수는 등록 안 함)
        if not is_second:
            self.sold_today[code] = datetime.datetime.now().strftime("%H:%M:%S")
        print(f"💡 매수계산 {code} | 한도:{order_cash:,} | {price:,}원×{qty}주 | 지정가:{limit_price:,} (호가:{hoga}원)")

        url  = f"{self.base_url}/uapi/domestic-stock/v1/trading/order-cash"
        data = {
            "CANO":         self.cano,
            "ACNT_PRDT_CD": self.acnt,
            "PDNO":         code,
            "ORD_QTY":      str(qty),
            "ORD_UNPR":     str(limit_price),
            "ORD_DVSN":     "00",  # 지정가
        }
        headers = {
            "authorization": f"Bearer {self.token}",
            "appkey":        self.appkey,
            "appsecret":     self.secret,
            "tr_id":         "TTTC0802U",
            "hashkey":       self.get_hashkey(data),
        }
        try:
            res = requests.post(url, headers=headers, data=json.dumps(data)).json()
            if res.get("rt_cd") == "0":
                self.notify(
                    f"🚀 매수 체결 {code}({self._code_to_name(code)}) | "
                    f"{qty}주 | {order_cash:,}원 | 지정가:{limit_price:,}원"
                )
                ctx = self.buy_context.get(code, {})
                self._save_buy_history(
                    code=code, buy_price=price, qty=qty,
                    ai_score=ctx.get("ai_score", 0), ai_reason=ctx.get("ai_reason", ""),
                    indicators=ctx.get("indicators", {}), stock_name=ctx.get("stock_name", ""),
                )
            else:
                print(f"❌ 매수 실패 {code}: {res.get('msg1', '알 수 없는 오류')}")
        except Exception as e:
            print(f"❌ 매수 요청 예외 {code}: {e}")

    def sell(self, code: str, qty: int, reason: str, sell_price: float = 0):
        """시장가 매도"""
        url  = f"{self.base_url}/uapi/domestic-stock/v1/trading/order-cash"
        data = {
            "CANO":         self.cano,
            "ACNT_PRDT_CD": self.acnt,
            "PDNO":         code,
            "ORD_QTY":      str(qty),
            "ORD_UNPR":     "0",
            "ORD_DVSN":     "01",  # 시장가
        }
        headers = {
            "authorization": f"Bearer {self.token}",
            "appkey":        self.appkey,
            "appsecret":     self.secret,
            "tr_id":         "TTTC0801U",
            "hashkey":       self.get_hashkey(data),
        }
        try:
            res = requests.post(url, headers=headers, data=json.dumps(data)).json()
            if res.get("rt_cd") == "0":
                self.notify(f"💰 매도 체결 {code}({self._code_to_name(code)}) | {reason}")
                self._save_sell_history(code, sell_price, reason)
                self.sold_today[code] = datetime.datetime.now().strftime("%H:%M:%S")
                print(f"🚫 당일 재매수 금지 등록: {code}")
                # bot_state.json에도 저장 (재시작 대비)
                _st = _read_bot_state()
                _st["sold_today"] = self.sold_today
                _st["sold_today_date"] = datetime.datetime.now().strftime("%Y-%m-%d")
                with open(BOT_STATE_FILE, "w") as _f:
                    json.dump(_st, _f, ensure_ascii=False)
                self.buy_context.pop(code, None)
            else:
                print(f"❌ 매도 실패 {code}: {res.get('msg1', '알 수 없는 오류')}")
        except Exception as e:
            print(f"❌ 매도 요청 예외 {code}: {e}")

    # ============================================================
    # 매도 체크 (보유종목 익절/손절/종가 체크) — paused 상태에서도 실행
    # ============================================================
    def _check_sell(self, code, pos, now_t, position_market_cache):
        """보유종목 매도 체크. paused 상태에서도 호출된다."""
        data = position_market_cache.get(code) or self.get_market_data(code)
        if not data:
            return

        current = float(data.get("stck_prpr", 0))
        entry   = pos["entry_price"]
        qty     = pos["qty"]

        if entry == 0 or current == 0 or qty <= 0:
            return

        rate = (current - entry) / entry

        # peak_tracker 없으면 초기화 (재시작 후 기존 보유종목)
        if code not in self.peak_tracker:
            self.peak_tracker[code] = {
                "peak_rate":  rate,
                "stage":      0,
                "remain_qty": qty,
                "buy2_done":  True,
                "buy1_price": entry,
            }

        tracker    = self.peak_tracker[code]
        stage      = tracker["stage"]
        peak_rate  = tracker["peak_rate"]
        buy2_done  = tracker.get("buy2_done", True)
        buy1_price = tracker.get("buy1_price", entry)

        # 고점 갱신
        if rate > peak_rate:
            tracker["peak_rate"] = rate

        # ── 2차 분할매수 체크 (paused 상태에서는 건너뜀) ──────
        if not self.peak_tracker[code].get("_paused_skip"):
            buy2_rate   = (current - buy1_price) / buy1_price if buy1_price else 0
            _is_weak    = self.market_status in ("weak", "stop")
            _buy2_allow = (
                buy2_rate > 0
                or (buy2_rate < 0 and not (_is_weak and BUY_2ND_WEAK_ONLY))
            )
            if (not buy2_done
                    and stage == 0
                    and abs(buy2_rate) >= abs(BUY_2ND_THRESHOLD)
                    and _buy2_allow
                    and not self._is_paused):
                tag = "눌림목" if buy2_rate < 0 else "추격"
                print(f"➕ 2차 매수 시도 {code} | 1차가:{buy1_price:,}→현재:{current:,} ({buy2_rate:+.2%}) {tag}")
                self.buy(code, current, amount=BUY_2ND_AMT)
                tracker["buy2_done"] = True
                self.notify(f"➕ 2차 분할매수 {code}({self._code_to_name(code)}) | {buy2_rate:+.2%} {tag}")
            elif not buy2_done and buy2_rate < 0 and _is_weak and BUY_2ND_WEAK_ONLY:
                print(f"⚠️ 약세장 물타기 금지 {code} ({buy2_rate:+.2%})")

        # ── 종가 매도 ──────────────────────────────────────────
        if now_t >= EOD_SELL_TIME:
            if stage >= 1:
                print(f"🔔 종가매도 {code} ({rate:+.2%}) | {qty}주")
                self.notify(f"🔔 종가매도 {code}({self._code_to_name(code)}) | {rate:+.2%} | {qty}주")
                self.sell(code, qty, f"종가매도({rate:+.2%})", sell_price=current)
                self.peak_tracker.pop(code, None)
                return
            elif stage == 0 and -0.01 <= rate <= 0.01:
                print(f"🔔 종가매도(횡보정리) {code} ({rate:+.2%}) | {qty}주")
                self.notify(f"🔔 종가매도(횡보정리) {code}({self._code_to_name(code)}) | {rate:+.2%} | {qty}주")
                self.sell(code, qty, f"종가매도횡보({rate:+.2%})", sell_price=current)
                self.peak_tracker.pop(code, None)
                return

        # ── 트레일링 스탑 (2차 익절 이후) ────────────────────
        if stage >= 2 and rate <= peak_rate - TRAIL_STOP:
            print(f"📉 트레일링스탑 {code} | 고점:{peak_rate:+.2%}→현재:{rate:+.2%}")
            self.notify(f"📉 트레일링스탑 {code}({self._code_to_name(code)}) | 고점:{peak_rate:+.2%}→현재:{rate:+.2%}")
            self.sell(code, qty, f"트레일링스탑({rate:+.2%})", sell_price=current)
            self.peak_tracker.pop(code, None)
            return

        # ── 2차 익절 (+10% → 40%) ─────────────────────────────
        if stage < 2 and rate >= SELL_2ND_RATE:
            sell_qty = max(int(tracker["remain_qty"] * SELL_2ND_QTY / (1 - SELL_1ST_QTY)), 1)
            sell_qty = min(sell_qty, qty)
            print(f"🎯 2차익절 {code} ({rate:+.2%}) | {sell_qty}주")
            self.notify(f"🎯 2차익절 {code}({self._code_to_name(code)}) | {rate:+.2%} | {sell_qty}주")
            self.sell(code, sell_qty, f"2차익절({rate:+.2%})", sell_price=current)
            tracker["stage"] = 2
            return

        # ── 1차 익절 (+5% → 30%) ──────────────────────────────
        if stage < 1 and rate >= SELL_1ST_RATE:
            sell_qty = max(int(qty * SELL_1ST_QTY), 1)
            print(f"✂️ 1차익절 {code} ({rate:+.2%}) | {sell_qty}주")
            self.notify(f"✂️ 1차익절 {code}({self._code_to_name(code)}) | {rate:+.2%} | {sell_qty}주")
            self.sell(code, sell_qty, f"1차익절({rate:+.2%})", sell_price=current)
            tracker["stage"]      = 1
            tracker["remain_qty"] = qty - sell_qty
            return

        # ── 손절 ──────────────────────────────────────────────
        if self.market_status in ("weak", "stop"):
            stop_line = STOP_LOSS_WEAK
        else:
            stop_line = STOP_LOSS_AFTER if stage >= 1 else STOP_LOSS_BASIC

        if rate <= stop_line:
            label = "손절(익절후)" if stage >= 1 else "손절"
            if self.market_status in ("weak", "stop"):
                label += "(약세장)"
            print(f"🛑 {label} {code} ({rate:+.2%}) | 기준:{stop_line:.0%}")
            self.notify(f"🛑 {label} {code}({self._code_to_name(code)}) | {rate:+.2%}")
            self.sell(code, qty, f"{label}({rate:+.2%})", sell_price=current)
            self.daily_loss_count += 1
            self.peak_tracker.pop(code, None)
            print(f"📉 당일 손절 누적: {self.daily_loss_count}회 / 최대:{MAX_DAILY_LOSS}회")
    
    def haiku_decision(self, code, data):
        return {
            "action": "BUY",   # BUY / HOLD / SKIP
            "confidence": 80,
            "reason": "테스트"
        }
        
    # ============================================================
    # 메인 루프
    # ============================================================
    def run(self):
        self.notify(
            f"🚀 [영암9 LIVE] AI TOP50 캐시 엔진 가동\n"
            f"⏰ {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"💰 1차매수:{BUY_1ST_AMT:,}원 / 2차매수:{BUY_2ND_AMT:,}원 / 최대{MAX_POSITIONS}종목\n"
            "📌 매수/매도/종료 시에만 알림 전송"
        )
        self._is_paused = False  # 매도 체크 시 분할매수 스킵 여부
        
        # 🔥 TEST MODE (장외에서도 AI 확인)
        if os.getenv("TEST_MODE") == "1":
            print("🧪 테스트 모드 실행")

            sample_data = {
                "current_price": 10000,
                "change_rate": 5.2,
                "volume": 120000,
                "volume_ratio": 180,
                "ma5": 10200,
                "ma20": 9800,
                "rsi": 62,
                "stock_name": "테스트종목"
            }

            decision = self.haiku.decide_buy(sample_data)
            print("🧠 AI 결과:", decision)

            return

        while True:
            try:
                now_t   = datetime.datetime.now().strftime("%H%M")
                now     = datetime.datetime.now().strftime("%H:%M:%S")
                weekday = datetime.datetime.now().weekday()

                if weekday >= 5:
                    day_name = "토요일" if weekday == 5 else "일요일"
                    print(f"😴 [{now}] {day_name} — 장 없음")
                    time.sleep(SLEEP_INTERVAL)
                    continue

                is_reg      = REG_MARKET_START <= now_t <= REG_MARKET_END
                is_reg      = REG_MARKET_START <= now_t <= REG_MARKET_END
                can_trade   = is_reg
                can_analyze = is_reg

                if not can_analyze:
                    print(f"😴 [{now}] 장외 대기 중...")
                    time.sleep(SLEEP_INTERVAL)
                    continue

                print(f"\n📈 정규장 [{now}]")

                # ── AI 비서 명령 처리 ─────────────────────────
                bot_state = _read_bot_state()
                self._is_paused = bot_state.get("paused", False)

                # ── 자정 초기화 ───────────────────────────────
                today = datetime.datetime.now().strftime("%Y-%m-%d")
                if today != self._sold_today_date:
                    self.sold_today       = {}
                    self._sold_today_date = today
                    self.daily_loss_count = 0
                    self.market_status    = "normal"
                    _st = _read_bot_state()
                    _st["sold_today"] = {}
                    _st["sold_today_date"] = today
                    with open(BOT_STATE_FILE, "w") as _f:
                        json.dump(_st, _f, ensure_ascii=False)
                    self._tech_cache = {}
                    self._flow_cache = {}
                    print("🔄 당일 재매수 금지 목록 / 손절카운터 / 기술지표 캐시 초기화")
                else:
                    # 재시작 시 sold_today 복원
                    if not self.sold_today:
                        _st = _read_bot_state()
                        _saved = _st.get("sold_today", {})
                        _saved_date = _st.get("sold_today_date", "")
                        if _saved and _saved_date == today:
                            self.sold_today = _saved
                            if self.sold_today:
                                print(f"♻️ sold_today 복원: {list(self.sold_today.keys())}")

                score_enter_now = bot_state.get("score_enter", BUY_SCORE_ENTER)

                # !시작 으로 재개 시 daily_loss 초기화 반영
                _saved_loss = bot_state.get("daily_loss", None)
                if _saved_loss is not None and _saved_loss == 0 and self.daily_loss_count > 0:
                    self.daily_loss_count = 0
                    print("♻️ 손절카운터 초기화 (AI비서 !시작 명령)")

                # 긴급 명령 처리 (paused 상태에서도 실행)
                pending = bot_state.get("pending_cmd")
                if pending and pending.get("type") == "sell":
                    sell_code = pending.get("code", "")
                    if sell_code in self.positions:
                        pos     = self.positions[sell_code]
                        # 현재가 조회해서 sell_price 전달 (-100% 방지)
                        mdata   = position_market_cache.get(sell_code) or self.get_market_data(sell_code)
                        s_price = float(mdata.get("stck_prpr", 0)) if mdata else 0
                        self.sell(sell_code, pos["qty"], "즉시매도(AI비서명령)", sell_price=s_price)
                        self.sold_today[sell_code] = datetime.datetime.now().strftime("%H:%M:%S")
                        _write_cmd_result(f"✅ {sell_code} 즉시매도 완료 ({s_price:,}원)")
                        print(f"📤 [AI비서] {sell_code} 즉시매도 실행 ({s_price:,}원)")
                    else:
                        _write_cmd_result(f"⚠️ {sell_code} 보유 중이 아님")

                elif pending and pending.get("type") == "buy":
                    buy_code = pending.get("code", "")
                    buy_qty  = int(pending.get("qty", 0))
                    if buy_qty <= 0:
                        _write_cmd_result(f"⚠️ 수량 오류: {buy_qty}")
                    else:
                        # 현재가 조회
                        mdata = self.get_market_data(buy_code)
                        if not mdata:
                            _write_cmd_result(f"⚠️ {buy_code} 시세 조회 실패")
                        else:
                            current = float(mdata.get("stck_prpr", 0))
                            if current <= 0:
                                _write_cmd_result(f"⚠️ {buy_code} 현재가 없음")
                            else:
                                amount = int(current * buy_qty * 1.01)
                                self.buy_context[buy_code] = {
                                    "ai_score":   0,
                                    "ai_reason":  "AI비서 수동매수",
                                    "indicators": {},
                                    "stock_name": self._code_to_name(buy_code),
                                }
                                self.buy(buy_code, current, amount=amount, is_second=True)
                                if buy_code not in self.peak_tracker:
                                    self.peak_tracker[buy_code] = {
                                        "peak_rate":  0.0,
                                        "stage":      0,
                                        "remain_qty": 0,
                                        "buy2_done":  True,
                                        "buy1_price": current,
                                    }
                                _write_cmd_result(f"✅ {buy_code} {buy_qty}주 매수 완료 ({current:,}원)")
                                print(f"📤 [AI비서] {buy_code} {buy_qty}주 수동매수 실행")

                # ── 토큰 갱신 / DB 정리 ───────────────────────
                self._refresh_token_if_needed()
                self._clean_ai_db()

                # ── 계좌 상태 ─────────────────────────────────
                cash           = self.get_buyable_cash()
                self.positions = self.get_current_positions()
                psbl_cash      = self.get_psbl_order_cash("005930", price=0)
                if psbl_cash <= 0:
                    psbl_cash = cash
                print(f"\n⏰ {now} | 💵 예수금(참고): {cash:,} | 💰 주문가능금액: {psbl_cash:,}")

                # ── 보유종목 현황 ─────────────────────────────
                position_market_cache = {}
                total_profit = 0
                print("📦 보유종목")
                for code, pos in self.positions.items():
                    data = self.get_market_data(code)
                    if not data:
                        continue
                    position_market_cache[code] = data
                    current = float(data.get("stck_prpr", 0))
                    entry   = pos["entry_price"]
                    qty     = pos["qty"]
                    profit  = (current - entry) * qty
                    rate    = (current - entry) / entry * 100 if entry > 0 else 0
                    total_profit += profit
                    print(f"  💰 {code} | {rate:+.2f}% | {qty}주")
                print(f"📈 총손익: {int(total_profit):,}원")

                # ── 시장 상태 체크 (5분마다) ───────────────────
                if not hasattr(self, "_last_market_check"):
                    self._last_market_check = 0
                if time.time() - self._last_market_check > 300:
                    self._update_market_status()
                    self._last_market_check = time.time()

                # ── stop 상태: 긴급 손절만 ────────────────────
                if self.market_status == "stop":
                    print(f"🚨 시장 중단 모드 | 코스피:{self.market_rate:+.2f}%")
                    for code, pos in list(self.positions.items()):
                        mdata   = position_market_cache.get(code) or self.get_market_data(code)
                        if not mdata:
                            continue
                        current = float(mdata.get("stck_prpr", 0))
                        entry   = pos["entry_price"]
                        if entry > 0 and current > 0:
                            rate = (current - entry) / entry
                            if rate <= STOP_LOSS_WEAK:
                                self.notify(f"🚨 긴급손절(약세장) {code} | {rate:+.2%}")
                                self.sell(code, pos["qty"], f"긴급손절(약세장)", sell_price=current)
                                self.daily_loss_count += 1
                                self.peak_tracker.pop(code, None)
                    time.sleep(LOOP_SLEEP)
                    continue

                # ── paused 상태: 매도 체크만 실행, 분석/매수 스킵 ──
                if self._is_paused:
                    print("⏸️ [AI비서] 일시중단 — 매도 체크만 실행")
                    if can_trade:
                        for code, pos in list(self.positions.items()):
                            self._check_sell(code, pos, now_t, position_market_cache)
                    # 상태 파일 업데이트
                    _write_bot_status({
                        "cash":          cash,
                        "psbl_cash":     psbl_cash,
                        "total_profit":  int(total_profit),
                        "positions":     len(self.positions),
                        "score_enter":   score_enter_now,
                        "last_update":   now,
                        "code_name_map": getattr(self, "code_name_map", {}),
                        "market_status": self.market_status,
                        "market_rate":   self.market_rate,
                        "daily_loss":    self.daily_loss_count,
                    })
                    time.sleep(LOOP_SLEEP)
                    continue

                # ── 종목 풀 조회 ──────────────────────────────
                codes = self.get_top50_codes()
                if not codes:
                    print("⚠️ 종목 풀 조회 실패, 재시도...")
                    time.sleep(5)
                    continue

                # ── 종목 분석 ─────────────────────────────────
                new_codes    = [c for c in codes if c not in self.score_cache]
                cached_codes = [c for c in codes if c in self.score_cache]
                print(f"\n🔄 분석: 신규 {len(new_codes)}개 | 캐시 재사용 {len(cached_codes)}개")

                rule_candidates = []

                for idx, code in enumerate(new_codes):
                    print(f"🔎 룰분석 {idx+1}/{len(new_codes)}: {code}", end="")
                    basic = self.get_market_data(code)
                    if not basic:
                        print()
                        continue
                    try:
                        data = {
                            "current_price": float(basic.get("stck_prpr",   0) or 0),
                            "change_rate":   float(basic.get("prdy_ctrt",   0) or 0),
                            "trading_value": int(basic.get("acml_tr_pbmn",  0) or 0) // 100_000_000,
                            "volume":        int(basic.get("acml_vol",      0) or 0),
                            "volume_ratio":  float(basic.get("vol_inrt",    0) or 0),
                            "vol_tnrt":      float(basic.get("vol_tnrt",    0) or 0),
                            "hts_avls":      int(float(basic.get("hts_avls", 0) or 0)),
                            "stock_name":    basic.get("hts_kor_isnm", ""),
                            "stck_hgpr":     float(basic.get("stck_hgpr", 0) or 0),
                            "stck_sdpr":     float(basic.get("stck_sdpr", 0) or 0),
                            "vol_rate":      float(basic.get("prdy_vrss_vol_rate", 0) or 0),
                        }
                        data.update(self.get_technical_indicators(code))
                        data.update(self.get_investor_trend(code))

                        # 기본 필터
                        if data["change_rate"] >= 29.5:
                            print(" → 상한가 제외"); continue
                        if data["change_rate"] > 15:
                            print(" → 과열 제외"); continue
                        if data["volume"] < 30_000:
                            print(" → 거래량 부족 제외"); continue
                        if data["current_price"] <= 999:
                            print(f" → 동전주 제외 ({data['current_price']:,.0f}원)"); continue
                        mkt_cap = int(float(data.get("hts_avls", 0) or 0))
                        if mkt_cap < 500:
                            print(f" → 소형주 제외 (시총:{mkt_cap:,}억)"); continue
                        if mkt_cap > 50000:
                            print(f" → 대형주 제외 (시총:{mkt_cap:,}억)"); continue
                        if data.get("change_rate", 0) < 3.0:
                            print(f" → 등락률 부족 제외 ({data.get('change_rate', 0):+.2f}%)"); continue
                        if data.get("vol_tnrt", 0) < 2.0:
                            print(f" → 회전율 부족 제외 ({data.get('vol_tnrt', 0):.2f}%)"); continue
                        if data.get("trading_value", 0) < 50:
                            print(f" → 거래대금 부족 제외 ({data.get('trading_value', 0)}억)"); continue
                        # 시간대별 거래량증가율 기준 (0%는 API 미제공으로 간주 → 스킵)
                        _vol_ratio = data.get("volume_ratio", 0)
                        _vol_min   = 100 if now_t < "1030" else 50
                        if _vol_ratio > 0 and _vol_ratio < _vol_min:
                            print(f" → 거래량증가율 부족 제외 ({_vol_ratio:.0f}% < {_vol_min}%)"); continue
                        ma5  = data.get("ma5",  0)
                        ma20 = data.get("ma20", 0)
                        if ma5 > 0 and ma20 > 0 and ma5 < ma20:
                            print(f" → MA 역배열 제외"); continue
                        stck_hgpr = data.get("stck_hgpr", 0)
                        stck_sdpr = data.get("stck_sdpr", 0)
                        if stck_hgpr > 0 and stck_sdpr > 0:
                            if (stck_hgpr - stck_sdpr) / stck_sdpr * 100 < 3.0:
                                print(" → 고가상승 부족 제외"); continue
                        if data.get("vol_rate", 0) < 50:
                            print(f" → 거래량비율 부족 제외 ({data.get('vol_rate', 0):.0f}%)"); continue
                        if data["current_price"] > LIMIT_PER_STOCK:
                            print(f" → 고가 제외 ({data['current_price']:,.0f}원)"); continue

                        rule_score = self.get_rule_score(data)
                        print(f" → 룰:{rule_score}점")
                        rule_candidates.append((code, rule_score, data))

                    except Exception as e:
                        print(f" → 오류: {e}")

                # 룰 상위 10개만 Claude 호출
                rule_candidates.sort(key=lambda x: x[1], reverse=True)
                top_for_claude = rule_candidates[:10]
                rest           = rule_candidates[10:]

                print(f"\n🤖 Claude 분석 대상: {len(top_for_claude)}개")
                for code, rule_score, data in top_for_claude:
                    ai_result = self.get_claude_score(code, data)
                    score     = ai_result["score"]
                    reason    = ai_result["reason"]
                    print(f"   🧠 {code} | 룰:{rule_score}→AI:{score}점 | {reason}")
                    data["ai_reason"] = reason
                    self.score_cache[code] = (score, data)

                for code, rule_score, data in rest:
                    data["ai_reason"] = f"룰점수({rule_score})"
                    self.score_cache[code] = (rule_score, data)

                # 풀에서 빠진 종목 캐시 정리
                current_pool = set(codes)
                removed = [c for c in list(self.score_cache) if c not in current_pool]
                for c in removed:
                    del self.score_cache[c]
                if removed:
                    print(f"🗑️ 캐시 정리: {len(removed)}개 | 잔여: {len(self.score_cache)}개")

                candidates = [
                    (code, score, data)
                    for code, (score, data) in self.score_cache.items()
                    if score >= BUY_SCORE_MIN
                ]

                def sort_key(x):
                    _, score, d = x
                    is_ai = not d.get("ai_reason", "").startswith("룰점수")
                    return (is_ai, score)

                candidates.sort(key=sort_key, reverse=True)
                top10 = candidates[:10]

                print(f"\n🔥 AI TOP{len(top10)} (후보 {len(candidates)}개 중):")
                for code, score, d in top10:
                    tag        = "🤖" if not d.get("ai_reason", "").startswith("룰점수") else "📐"
                    cached_tag = "📦캐시" if code in cached_codes else "🆕신규"
                    print(f"  {cached_tag}{tag} {code} | {score}점 | {d.get('ai_reason', '')}")

                # ── 매수 실행 ─────────────────────────────────
                available_slots = MAX_POSITIONS - len(self.positions)

                if not can_trade:
                    print("🌅 프리장 — 매수 대기")
                elif self.market_status == "weak":
                    print(f"⚠️ 약세장 모드 — 신규 매수 중단")
                elif self.daily_loss_count >= MAX_DAILY_LOSS:
                    print(f"🛑 당일 손절 {self.daily_loss_count}회 — 매수 자동 정지")
                    _st = _read_bot_state()
                    if not _st.get("paused"):
                        self.notify(f"🛑 당일 손절 {self.daily_loss_count}회 도달 — 매수 자동 정지\n!시작 으로 재개")
                        _st["paused"] = True
                        _st["daily_loss"] = self.daily_loss_count
                        with open(BOT_STATE_FILE, "w") as _f:
                            json.dump(_st, _f, ensure_ascii=False)
                elif available_slots <= 0:
                    print("📦 포지션 FULL — 매수 건너뜀")
                else:
                    for code, score, data in top10:
                        if available_slots <= 0:
                            break
                        if code in self.positions:
                            continue
                        if data["current_price"] <= 0:
                            continue
                        if score < score_enter_now:
                            continue
                        if code in self.sold_today:
                            print(f"🚫 재매수 금지 {code} | {self.sold_today[code]} 매도")
                            continue
                        if score < 60:
                            print(f"⏭️ AI 스킵(저점수) {code} | {score}점")
                            continue

                    # =========================
                    # 🤖 AI 최종 판단 (여기 추가)
                    # =========================
                    try:
                        ai = self.haiku_decision(code, data)

                        if ai["action"] != "BUY":
                            print(f"🤖 AI 스킵 {code} | {ai['action']} | {ai['reason']}")
                            continue

                        print(f"🤖 AI 매수 승인 {code} | 신뢰도:{ai['confidence']} | {ai['reason']}")

                    except Exception as e:
                        print(f"⚠️ AI 판단 실패 {code} → 스킵 | {e}")
                        continue

                        print(f"🚀 1차 매수 시도 {code} | {score}점 | {BUY_1ST_AMT:,}원")
                        self.buy(code, data["current_price"], amount=BUY_1ST_AMT)
                        self.peak_tracker[code] = {
                            "peak_rate":  0.0,
                            "stage":      0,
                            "remain_qty": 0,
                            "buy2_done":  False,
                            "buy1_price": data["current_price"],
                        }
                        self.buy_context[code] = {
                            "ai_score":   score,
                            "ai_reason":  data.get("ai_reason", ""),
                            "indicators": data,
                            "stock_name": data.get("stock_name", ""),
                        }
                        available_slots -= 1
                        time.sleep(1)

                # ── 매도 실행 ─────────────────────────────────
                if can_trade:
                    for code, pos in list(self.positions.items()):
                        self._check_sell(code, pos, now_t, position_market_cache)

                # ── 상태 파일 업데이트 ────────────────────────
                # 보유종목 현재가 정리
                positions_detail = {}
                for _code, _pos in self.positions.items():
                    _mdata = position_market_cache.get(_code)
                    _current = float(_mdata.get("stck_prpr", 0)) if _mdata else 0
                    _entry   = _pos["entry_price"]
                    _qty     = _pos["qty"]
                    _rate    = (_current - _entry) / _entry * 100 if _entry > 0 else 0
                    positions_detail[_code] = {
                        "name":        self._code_to_name(_code),
                        "current":     int(_current),
                        "entry_price": int(_entry),
                        "qty":         _qty,
                        "rate":        round(_rate, 2),
                    }

                _write_bot_status({
                    "cash":             cash,
                    "psbl_cash":        psbl_cash,
                    "total_profit":     int(total_profit),
                    "positions":        len(self.positions),
                    "positions_detail": positions_detail,
                    "score_enter":      score_enter_now,
                    "last_update":      now,
                    "code_name_map":    getattr(self, "code_name_map", {}),
                    "market_status":    self.market_status,
                    "market_rate":      self.market_rate,
                    "daily_loss":       self.daily_loss_count,
                })

                time.sleep(LOOP_SLEEP)

            except KeyboardInterrupt:
                perf     = self._get_recent_performance(limit=20)
                stop_msg = (
                    f"🛑 [영암9] 봇 종료\n"
                    f"⏰ {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                )
                if perf:
                    stop_msg += (
                        f"📊 최근 {perf['total']}건 성과\n"
                        f"  승률: {perf['win_rate']}% | 평균: {perf['avg_profit']:+.2f}%\n"
                        f"  최고: {perf['best']:+.2f}% | 최저: {perf['worst']:+.2f}%"
                    )
                self.notify(stop_msg)
                break

            except Exception as e:
                print(f"🚨 루프 오류: {e}")
                time.sleep(5)


# ============================================================
# 진입점
# ============================================================
if __name__ == "__main__":
    KIS10_RealTrade().run()
