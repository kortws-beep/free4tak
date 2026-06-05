"""
cbot.py — 영암9 코인 자동매매 봇 v2.2 (업비트 기반)
========================================================
[종목 풀 전략]
  업비트 KRW 마켓 전체 시세 조회 → 거래대금 상위 20개 자동 선별
  - BTC/ETH/XRP/SOL 은 항상 포함 (고정 우선순위)
  - 거래대금 50억 미만 잡코인 제외
  - 24시간 등락률 -20% 이하 폭락 코인 제외
  - 상한가(+30%) 근접 코인 제외
  - ETF/레버리지 코인 제외 (UP/DOWN 포함 종목명)
  - 5분마다 갱신

[시드 설정 — 30만원 기준]
  1차 매수: 5만원 / 2차 매수: 3만원
  최대 보유: 2코인 / 일일 손실 한도: -4.5만원

[전략]
  기준봉: 4시간봉 / 보유기간: 1일 내외

[매수 신호 - 아래 조건 모두 충족 시]
  1. 4시간봉 MA5 > MA20 (단기 상승 추세)
  2. RSI(14) 40~70
  3. 직전 4시간봉 거래량 > 20봉 평균 × 1.3배
  4. 현재가 > MA20
  5. BTC 시장 상태 normal
  6. 공포탐욕지수 25 이상
  7. 당일 미보유 + 미매도

[매도 전략]
  1차 익절 +5% → 30% / 2차 익절 +10% → 40%
  트레일링 스탑 고점 -5% / 기본 손절 -7%
  야간(00~06시) -3% / 급락(직전봉 -5%) 즉시 손절

[리스크 관리]
  BTC -2% → weak / BTC -4% → stop
  공포탐욕 25 이하 / 일손실 -4.5만원 / 당일 손절 2회

[변경 이력]
  2026-05-01 v1 최초 생성
  2026-05-01 v2 리스크 대응 전면 강화
  2026-05-01 v2.1 30만원 소액 시드 최적화
  2026-05-01 v2.2 종목 풀 자동 확장
    - 업비트 전체 KRW 마켓 거래대금 상위 20개 자동 조회
    - BTC/ETH/XRP/SOL 고정 우선 포함
    - 잡코인 필터 (거래대금 50억 미만, 폭락, 상한가 근접 제외)
    - 종목 풀 5분마다 갱신
    - 알트코인(BTC/ETH 외) 동시 보유 최대 1개 제한 추가
"""

import os
import time
import json
import uuid
import jwt
import sqlite3
import hashlib
import requests
import datetime
from dotenv import load_dotenv
from anthropic import Anthropic

load_dotenv()


# ============================================================
# 상수
# ============================================================
# 고정 우선 코인 (항상 풀에 포함)
FIXED_COINS  = ["KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-SOL"]
MAJOR_COINS  = ["KRW-BTC", "KRW-ETH"]
ALT_COINS    = ["KRW-XRP", "KRW-SOL"]

# 종목 풀 설정
POOL_SIZE         = 20       # 거래대금 상위 20개
MIN_TRADE_AMT_B   = 5_000_000_000  # 최소 거래대금 50억 (원)
MAX_CHANGE_RATE   = 28.0    # 상한가 근접 제외 (+28% 이상)
MIN_CHANGE_RATE   = -20.0   # 폭락 코인 제외 (-20% 이하)

# 레버리지/ETF 제외 키워드
EXCLUDE_KEYWORDS = ["UP", "DOWN", "BEAR", "BULL"]

# 매수/매도 금액
BUY_1ST_AMT       = 100_000
BUY_2ND_AMT       = 50_000
BUY_2ND_THRESHOLD = -0.03
MAX_POSITIONS      = 3
MAX_ALT_POSITIONS  = 1       # BTC/ETH 외 알트 동시 보유 최대 1개
MIN_ORDER_AMT      = 5_000

# ── 매도 전략 ───────────────────────────────────────────────
SELL_1ST_RATE    = 0.05
SELL_1ST_QTY     = 0.30
SELL_2ND_RATE    = 0.10
SELL_2ND_QTY     = 0.40
TRAIL_STOP       = 0.05
STOP_LOSS_BASIC  = -0.07
STOP_LOSS_WEAK   = -0.05
STOP_LOSS_STOP   = -0.03
STOP_LOSS_NIGHT  = -0.03
STOP_LOSS_CRASH  = -0.05

# ── 매수 신호 기준 ──────────────────────────────────────────
RSI_MIN  = 40
RSI_MAX  = 70
VOL_MULT = 1.3

# ── 시장/리스크 ─────────────────────────────────────────────
BTC_WEAK_THRESH  = -2.0
BTC_STOP_THRESH  = -4.0
FEAR_GREED_MIN   = 25
DAILY_LOSS_LIMIT = -60_000
MAX_DAILY_LOSS   = 2

NIGHT_START = 0
NIGHT_END   = 6

LOOP_SLEEP  = 300
CANDLE_UNIT = 240

BOT_STATE_FILE = "cbot_state.json"
TRADE_HIST_DB  = "cbot_trade_history.db"
AI_CACHE_DB    = "cbot_ai_cache.db"
AI_CACHE_HOURS = 4

BASE_URL = "https://api.upbit.com/v1"


# ============================================================
# 상태 파일 헬퍼
# ============================================================
def _read_state() -> dict:
    try:
        if os.path.exists(BOT_STATE_FILE):
            with open(BOT_STATE_FILE, "r") as f:
                return json.load(f)
    except Exception:
        pass
    return {"paused": False, "pending_cmd": None, "cmd_result": None}

def _write_state(state: dict):
    try:
        with open(BOT_STATE_FILE, "w") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"⚠️ 상태 파일 저장 오류: {e}")

def _write_status(status: dict):
    try:
        state = _read_state()
        state["last_status"] = status
        state["last_update"] = datetime.datetime.now().strftime("%H:%M:%S")
        _write_state(state)
    except Exception as e:
        print(f"⚠️ 상태 저장 오류: {e}")

def _write_cmd_result(result: str):
    try:
        state = _read_state()
        state["cmd_result"]  = result
        state["pending_cmd"] = None
        _write_state(state)
    except Exception as e:
        print(f"⚠️ 명령 결과 저장 오류: {e}")


# ============================================================
# 메인 클래스
# ============================================================
class CBot:

    def __init__(self):
        print("🚀 [영암9 COIN v2.2] 코인 자동매매 봇 가동 (종목 풀 자동 확장)")

        self.access_key = os.getenv("UPBIT_ACCESS_KEY")
        self.secret_key = os.getenv("UPBIT_SECRET_KEY")
        if not self.access_key or not self.secret_key:
            print("❌ UPBIT_ACCESS_KEY / UPBIT_SECRET_KEY 환경변수 없음!")

        self.llm = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

        self.positions        = {}
        self.peak_tracker     = {}
        self.sold_today       = {}
        self._sold_today_date = datetime.datetime.now().strftime("%Y-%m-%d")
        self.daily_loss_count = 0
        self.daily_pnl        = 0
        self._is_paused       = False

        self.market_status = "normal"
        self.btc_rate      = 0.0
        self.fear_greed    = 50

        # 종목 풀 (5분 캐시)
        self.coin_pool      = list(FIXED_COINS)
        self._pool_cache_ts = 0

        self._candle_cache         = {}
        self._tech_cache           = {}
        self._last_market_check    = 0
        self._last_feargreed_check = 0

        self._init_trade_db()
        self._init_ai_db()

    # ============================================================
    # JWT 인증
    # ============================================================
    def _get_headers(self, query_string: str = None) -> dict:
        payload = {"access_key": self.access_key, "nonce": str(uuid.uuid4())}
        if query_string:
            m = hashlib.sha512()
            m.update(query_string.encode())
            payload["query_hash"]     = m.hexdigest()
            payload["query_hash_alg"] = "SHA512"
        token = jwt.encode(payload, self.secret_key, algorithm="HS256")
        return {"Authorization": f"Bearer {token}"}

    # ============================================================
    # DB 초기화
    # ============================================================
    def _init_trade_db(self):
        try:
            conn = sqlite3.connect(TRADE_HIST_DB)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    market        TEXT NOT NULL,
                    buy_price     REAL NOT NULL,
                    buy_time      TEXT NOT NULL,
                    sell_price    REAL,
                    sell_time     TEXT,
                    qty           REAL NOT NULL,
                    profit_rate   REAL,
                    profit_krw    REAL,
                    sell_reason   TEXT,
                    ai_score      INTEGER,
                    ai_reason     TEXT,
                    market_status TEXT,
                    fear_greed    INTEGER
                )
            """)
            conn.commit(); conn.close()
            print(f"✅ 매매이력 DB ({TRADE_HIST_DB})")
        except Exception as e:
            print(f"❌ DB 오류: {e}")

    def _init_ai_db(self):
        try:
            conn = sqlite3.connect(AI_CACHE_DB)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS ai_analysis (
                    market      TEXT PRIMARY KEY,
                    score       INTEGER NOT NULL,
                    reason      TEXT,
                    analyzed_at TEXT NOT NULL
                )
            """)
            conn.commit(); conn.close()
            print(f"✅ AI DB ({AI_CACHE_DB})")
        except Exception as e:
            print(f"❌ AI DB 오류: {e}")

    def _save_buy_history(self, market, buy_price, qty, ai_score=0, ai_reason=""):
        try:
            now  = datetime.datetime.now().isoformat(timespec="seconds")
            conn = sqlite3.connect(TRADE_HIST_DB)
            conn.execute("""
                INSERT INTO trades
                    (market, buy_price, buy_time, qty, ai_score, ai_reason,
                     market_status, fear_greed)
                VALUES (?,?,?,?,?,?,?,?)
            """, (market, buy_price, now, qty, ai_score, ai_reason,
                  self.market_status, self.fear_greed))
            conn.commit(); conn.close()
        except Exception as e:
            print(f"⚠️ 매수이력 저장 오류: {e}")

    def _save_sell_history(self, market, sell_price, sell_reason) -> float:
        profit_krw = 0.0
        try:
            now  = datetime.datetime.now().isoformat(timespec="seconds")
            conn = sqlite3.connect(TRADE_HIST_DB)
            row  = conn.execute("""
                SELECT id, buy_price, qty FROM trades
                WHERE market=? AND sell_price IS NULL
                ORDER BY id DESC LIMIT 1
            """, (market,)).fetchone()
            if not row:
                conn.close(); return 0.0
            trade_id, buy_price, qty = row
            profit_rate = (sell_price - buy_price) / buy_price * 100 if buy_price else 0
            profit_krw  = (sell_price - buy_price) * qty
            conn.execute("""
                UPDATE trades
                SET sell_price=?, sell_time=?, profit_rate=?, profit_krw=?, sell_reason=?
                WHERE id=?
            """, (sell_price, now, round(profit_rate, 2), round(profit_krw), sell_reason, trade_id))
            conn.commit(); conn.close()
            emoji = "✅" if profit_rate >= 0 else "❌"
            print(f"   {emoji} {market} | {profit_rate:+.2f}% | {profit_krw:+,.0f}원 | {sell_reason}")
        except Exception as e:
            print(f"⚠️ 매도이력 저장 오류: {e}")
        return profit_krw

    def _get_recent_performance(self, limit=20):
        try:
            conn = sqlite3.connect(TRADE_HIST_DB)
            rows = conn.execute("""
                SELECT profit_rate, profit_krw FROM trades
                WHERE sell_price IS NOT NULL ORDER BY id DESC LIMIT ?
            """, (limit,)).fetchall()
            conn.close()
            if not rows: return None
            profits = [r[0] for r in rows if r[0] is not None]
            krws    = [r[1] for r in rows if r[1] is not None]
            if not profits: return None
            wins = [p for p in profits if p >= 0]
            return {
                "total":      len(profits),
                "win_rate":   round(len(wins) / len(profits) * 100, 1),
                "avg_profit": round(sum(profits) / len(profits), 2),
                "best":       round(max(profits), 2),
                "worst":      round(min(profits), 2),
                "total_krw":  round(sum(krws), 0),
            }
        except: return None

    # ============================================================
    # 디스코드 알림
    # ============================================================
    def notify(self, msg):
        print(msg)
        bot_token  = os.getenv("DISCORD_BOT_TOKEN")
        channel_id = os.getenv("DISCORD_CHANNEL_ID")
        if bot_token and channel_id:
            try:
                requests.post(
                    f"https://discord.com/api/v10/channels/{channel_id}/messages",
                    headers={"Authorization": f"Bot {bot_token}"},
                    json={"content": f"[COIN] {msg}"}, timeout=3
                )
            except Exception as e:
                print(f"⚠️ 디스코드 전송 실패: {e}")

    # ============================================================
    # 업비트 API — 잔고/현재가/포지션
    # ============================================================
    def get_balances(self) -> dict:
        try:
            res    = requests.get(
                f"{BASE_URL}/accounts", headers=self._get_headers(), timeout=5
            ).json()
            result = {}
            for item in res:
                cur = item.get("currency")
                bal = float(item.get("balance", 0))
                avg = float(item.get("avg_buy_price", 0))
                if bal > 0:
                    result[cur] = {"balance": bal, "avg_buy_price": avg}
            return result
        except Exception as e:
            print(f"❌ 잔고 조회 오류: {e}"); return {}

    def get_krw_balance(self) -> float:
        return self.get_balances().get("KRW", {}).get("balance", 0)

    def get_current_price(self, markets: list) -> dict:
        try:
            res = requests.get(
                f"{BASE_URL}/ticker",
                params={"markets": ",".join(markets)}, timeout=5
            ).json()
            return {item["market"]: item["trade_price"] for item in res}
        except Exception as e:
            print(f"❌ 현재가 조회 오류: {e}"); return {}

    def get_current_positions(self) -> dict:
        """보유 코인 포지션 조회 — 전체 잔고 기준 (coin_pool 무관)."""
        balances = self.get_balances()
        held_markets = [
            f"KRW-{cur}" for cur in balances
            if cur != "KRW" and balances[cur]["balance"] > 0.00001
               and balances[cur]["avg_buy_price"] > 0
        ]
        if not held_markets:
            return {}
        prices = self.get_current_price(held_markets)
        pos = {}
        for market in held_markets:
            cur  = market.replace("KRW-", "")
            info = balances.get(cur)
            if not info: continue
            pos[market] = {
                "entry_price": info["avg_buy_price"],
                "qty":         info["balance"],
                "current":     prices.get(market, info["avg_buy_price"]),
            }
        return pos

    # ============================================================
    # ★ 종목 풀 자동 조회 (거래대금 상위 20개)
    # ============================================================
    def _update_coin_pool(self):
        """KRW 마켓 전체에서 거래대금 상위 POOL_SIZE개를 자동 선별.
        5분 캐시 적용. 고정 코인(FIXED_COINS)은 항상 포함.
        """
        if time.time() - self._pool_cache_ts < 300:
            return  # 5분 캐시

        try:
            # 1) 업비트 KRW 마켓 목록 조회
            markets_res = requests.get(
                f"{BASE_URL}/market/all", params={"isDetails": "false"}, timeout=5
            ).json()
            krw_markets = [
                m["market"] for m in markets_res
                if m["market"].startswith("KRW-")
            ]

            # 2) 레버리지/ETF 제외
            krw_markets = [
                m for m in krw_markets
                if not any(kw in m.replace("KRW-", "") for kw in EXCLUDE_KEYWORDS)
            ]

            # 3) 전체 시세 조회 (업비트 한 번에 최대 100개)
            # 100개씩 나눠서 조회
            ticker_data = []
            for i in range(0, len(krw_markets), 100):
                chunk = krw_markets[i:i+100]
                try:
                    res = requests.get(
                        f"{BASE_URL}/ticker",
                        params={"markets": ",".join(chunk)}, timeout=5
                    ).json()
                    ticker_data.extend(res)
                    time.sleep(0.1)  # API 레이트 리밋 방지
                except Exception as e:
                    print(f"⚠️ 시세 조회 오류 (chunk {i}): {e}")

            # 4) 필터 적용
            filtered = []
            for item in ticker_data:
                market      = item.get("market", "")
                trade_price = float(item.get("trade_price", 0))
                acc_trade   = float(item.get("acc_trade_price_24h", 0))  # 24시간 거래대금(원)
                change_rate = float(item.get("signed_change_rate", 0)) * 100

                # 거래대금 50억 미만 제외
                if acc_trade < MIN_TRADE_AMT_B:
                    continue
                # 폭락 코인 제외 (-20% 이하)
                if change_rate < MIN_CHANGE_RATE:
                    continue
                # 상한가 근접 제외 (+28% 이상)
                if change_rate > MAX_CHANGE_RATE:
                    continue
                # 1원 미만 제외 (너무 낮은 단가)
                if trade_price < 1:
                    continue

                filtered.append({
                    "market":     market,
                    "trade_amt":  acc_trade,
                    "change":     change_rate,
                    "price":      trade_price,
                })

            # 5) 거래대금 내림차순 정렬 → 상위 POOL_SIZE개
            filtered.sort(key=lambda x: x["trade_amt"], reverse=True)
            top_markets = [f["market"] for f in filtered[:POOL_SIZE]]

            # 6) 고정 코인 우선 병합 (중복 제거)
            pool = list(FIXED_COINS)
            for m in top_markets:
                if m not in pool:
                    pool.append(m)

            # 전체 풀은 POOL_SIZE + len(FIXED_COINS) 이내
            self.coin_pool      = pool[:POOL_SIZE + len(FIXED_COINS)]
            self._pool_cache_ts = time.time()

            new_coins = [m for m in self.coin_pool if m not in FIXED_COINS]
            print(f"🪙 종목 풀 갱신: 총 {len(self.coin_pool)}개")
            print(f"   고정: {', '.join(FIXED_COINS)}")
            if new_coins:
                print(f"   추가: {', '.join(new_coins)}")

        except Exception as e:
            print(f"⚠️ 종목 풀 갱신 오류: {e} — 기존 풀 유지 ({len(self.coin_pool)}개)")

    # ============================================================
    # 시장 상태 — BTC 등락률 (10분 캐시)
    # ============================================================
    def _update_market_status(self):
        if time.time() - self._last_market_check < 600:
            return
        try:
            res = requests.get(
                f"{BASE_URL}/ticker", params={"markets": "KRW-BTC"}, timeout=5
            ).json()
            if not res: return
            self.btc_rate  = float(res[0].get("signed_change_rate", 0)) * 100
            prev_status    = self.market_status

            if self.btc_rate <= BTC_STOP_THRESH:
                self.market_status = "stop"; emoji = "🚨"
            elif self.btc_rate <= BTC_WEAK_THRESH:
                self.market_status = "weak"; emoji = "⚠️"
            else:
                self.market_status = "normal"; emoji = "✅"

            if self.market_status != prev_status:
                self.notify(
                    f"{emoji} 시장상태 변경: {prev_status} → {self.market_status}\n"
                    f"BTC: {self.btc_rate:+.2f}%"
                )
            print(f"📊 시장:{self.market_status} | BTC:{self.btc_rate:+.2f}%")
            self._last_market_check = time.time()
        except Exception as e:
            print(f"⚠️ BTC 시세 오류: {e}")

    # ============================================================
    # 공포탐욕지수 (1시간 캐시)
    # ============================================================
    def _update_fear_greed(self):
        if time.time() - self._last_feargreed_check < 3600:
            return
        try:
            res  = requests.get(
                "https://api.alternative.me/fng/?limit=1", timeout=5
            ).json()
            val  = int(res["data"][0]["value"])
            name = res["data"][0]["value_classification"]
            prev = self.fear_greed
            self.fear_greed = val

            if prev >= FEAR_GREED_MIN and val < FEAR_GREED_MIN:
                self.notify(f"😱 극단공포 진입: {val}({name}) → 신규 매수 중단")
            elif prev < FEAR_GREED_MIN and val >= FEAR_GREED_MIN:
                self.notify(f"😌 공포탐욕 회복: {val}({name}) → 신규 매수 재개")

            print(f"😨 공포탐욕: {val} ({name})")
            self._last_feargreed_check = time.time()
        except Exception as e:
            print(f"⚠️ 공포탐욕 조회 오류: {e} — 기존값 유지({self.fear_greed})")

    # ============================================================
    # 야간 / 손절선
    # ============================================================
    def _is_night(self) -> bool:
        return NIGHT_START <= datetime.datetime.now().hour < NIGHT_END

    def _get_stop_loss(self) -> float:
        if self.market_status == "stop":  return STOP_LOSS_STOP
        if self._is_night():              return STOP_LOSS_NIGHT
        if self.market_status == "weak":  return STOP_LOSS_WEAK
        return STOP_LOSS_BASIC

    # ============================================================
    # 캔들 / 기술지표
    # ============================================================
    def get_candles(self, market: str, count: int = 50) -> list:
        if market in self._candle_cache:
            data, ts = self._candle_cache[market]
            if time.time() - ts < 300:
                return data
        try:
            res = requests.get(
                f"{BASE_URL}/candles/minutes/{CANDLE_UNIT}",
                params={"market": market, "count": count}, timeout=5
            ).json()
            if isinstance(res, list) and res:
                self._candle_cache[market] = (res, time.time())
                return res
        except Exception as e:
            print(f"⚠️ 캔들 조회 오류 {market}: {e}")
        return []

    def get_indicators(self, market: str) -> dict:
        if market in self._tech_cache:
            data, ts = self._tech_cache[market]
            if time.time() - ts < 300:
                return data

        candles = self.get_candles(market, 50)
        if len(candles) < 21: return {}

        closes  = [c["trade_price"]            for c in candles]
        volumes = [c["candle_acc_trade_volume"] for c in candles]

        def ma(n): return sum(closes[:n]) / n if len(closes) >= n else 0

        def rsi(period=14):
            if len(closes) < period + 1: return 50
            gains  = [closes[i] - closes[i+1] for i in range(period) if closes[i] > closes[i+1]]
            losses = [abs(closes[i] - closes[i+1]) for i in range(period) if closes[i] <= closes[i+1]]
            avg_g  = sum(gains)  / period if gains  else 0
            avg_l  = sum(losses) / period if losses else 1
            return 100 - (100 / (1 + (avg_g / avg_l if avg_l else 0)))

        vol_recent  = volumes[1] if len(volumes) > 1 else 0
        vol_avg20   = sum(volumes[1:21]) / 20 if len(volumes) >= 21 else 0
        vol_ratio   = vol_recent / vol_avg20 if vol_avg20 > 0 else 0
        prev_close  = closes[1] if len(closes) > 1 else closes[0]
        prev2_close = closes[2] if len(closes) > 2 else closes[1]
        candle_rate = (prev_close - prev2_close) / prev2_close if prev2_close else 0

        result = {
            "current":     closes[0],
            "ma5":         ma(5),
            "ma20":        ma(20),
            "rsi":         rsi(14),
            "vol_ratio":   vol_ratio,
            "candle_rate": candle_rate,
        }
        self._tech_cache[market] = (result, time.time())
        return result

    # ============================================================
    # 매수 신호 판단
    # ============================================================
    def check_buy_signal(self, market: str) -> tuple:
        ind = self.get_indicators(market)
        if not ind: return False, {}, "지표 조회 실패"

        current     = ind["current"]
        ma5         = ind["ma5"]
        ma20        = ind["ma20"]
        rsi         = ind["rsi"]
        vol_ratio   = ind["vol_ratio"]
        candle_rate = ind["candle_rate"]

        if self.market_status == "stop":
            return False, ind, f"BTC 중단 ({self.btc_rate:+.2f}%)"
        if self.market_status == "weak":
            return False, ind, f"BTC 약세 ({self.btc_rate:+.2f}%)"
        if self.fear_greed < FEAR_GREED_MIN:
            return False, ind, f"극단공포 (탐욕:{self.fear_greed})"
        if candle_rate <= STOP_LOSS_CRASH:
            return False, ind, f"직전봉 급락 ({candle_rate:+.2%})"
        if ma5 <= ma20:
            return False, ind, "MA 역배열"
        if not (RSI_MIN <= rsi <= RSI_MAX):
            return False, ind, f"RSI {'과매수' if rsi > RSI_MAX else '과매도'} ({rsi:.1f})"
        if vol_ratio < VOL_MULT:
            return False, ind, f"거래량 부족 ({vol_ratio:.2f}x)"
        if current <= ma20:
            return False, ind, "현재가 MA20 이하"

        # 알트코인 포지션 제한
        # BTC/ETH 외 알트코인은 동시 1개만 허용
        if market not in MAJOR_COINS:
            holding_alts = [m for m in self.positions if m not in MAJOR_COINS]
            if len(holding_alts) >= MAX_ALT_POSITIONS:
                return False, ind, f"알트 동시보유 한도 ({MAX_ALT_POSITIONS}개)"

        return True, ind, (
            f"MA정배열|RSI:{rsi:.0f}|"
            f"거래량:{vol_ratio:.1f}x|BTC:{self.btc_rate:+.2f}%"
        )

    # ============================================================
    # Claude AI 분석
    # ============================================================
    def get_ai_score(self, market: str, ind: dict) -> dict:
        try:
            conn = sqlite3.connect(AI_CACHE_DB)
            row  = conn.execute(
                "SELECT score, reason, analyzed_at FROM ai_analysis WHERE market=?",
                (market,)
            ).fetchone()
            conn.close()
            if row:
                score, reason, at = row
                age_h = (
                    datetime.datetime.now() - datetime.datetime.fromisoformat(at)
                ).total_seconds() / 3600
                if age_h < AI_CACHE_HOURS:
                    print(f"   💾 AI캐시 {market}|{score}점|{age_h:.1f}h전")
                    return {"score": score, "reason": reason}
        except: pass

        try:
            import re as _re
            # 고정 코인 vs 알트 여부에 따라 보수적 판단 지시
            is_major = market in MAJOR_COINS
            caution  = "BTC/ETH 주요 코인이므로 신호 명확해야 높은 점수" if is_major \
                       else "알트코인이므로 더 보수적으로 판단 (변동성 주의)"

            prompt = (
                "당신은 암호화폐 4시간봉 트레이더 전문가입니다.\n"
                "아래 지표를 분석해 매수 점수(0~100)와 이유를 JSON으로만 반환하세요.\n\n"
                f"[코인] {market}\n"
                f"[현재가] {ind.get('current',0):,}원\n"
                f"[MA5] {ind.get('ma5',0):,.0f} | [MA20] {ind.get('ma20',0):,.0f}\n"
                f"[MA정배열] {ind.get('ma5',0) > ind.get('ma20',0)}\n"
                f"[RSI14] {ind.get('rsi',50):.1f}\n"
                f"[거래량비] {ind.get('vol_ratio',0):.2f}x\n"
                f"[직전봉] {ind.get('candle_rate',0):+.2%}\n"
                f"[BTC시장] {self.market_status} ({self.btc_rate:+.2f}%)\n"
                f"[공포탐욕] {self.fear_greed}\n\n"
                f"[판단 기준]\n"
                f"- {caution}\n"
                "- 공포탐욕 50이상 + MA정배열 + 거래량급증 → 높은 점수\n"
                "- BTC약세 + RSI높음 → 낮은 점수\n"
                "- 직전봉 급등(+5%) 후 추격은 낮은 점수\n\n"
                '{"score": 70, "reason": "이유 한 줄"}'
            )
            res   = self.llm.messages.create(
                model="claude-haiku-4-5-20251001", max_tokens=128,
                messages=[{"role": "user", "content": prompt}]
            )
            text  = res.content[0].text.strip()
            text  = _re.sub(r"```(?:json)?", "", text).strip()
            match = _re.search(r'\{.*\}', text, _re.DOTALL)
            if not match: return {"score": 0, "reason": "파싱실패"}

            result = json.loads(match.group())
            score  = max(0, min(100, int(result.get("score", 0))))
            reason = result.get("reason", "")

            now  = datetime.datetime.now().isoformat(timespec="seconds")
            conn = sqlite3.connect(AI_CACHE_DB)
            conn.execute("""
                INSERT INTO ai_analysis (market, score, reason, analyzed_at)
                VALUES (?,?,?,?)
                ON CONFLICT(market) DO UPDATE SET
                    score=excluded.score, reason=excluded.reason,
                    analyzed_at=excluded.analyzed_at
            """, (market, score, reason, now))
            conn.commit(); conn.close()
            return {"score": score, "reason": reason}
        except Exception as e:
            print(f"⚠️ AI 오류 {market}: {e}")
            return {"score": 0, "reason": "분석실패"}

    # ============================================================
    # 주문 — 매수
    # ============================================================
    def buy(self, market: str, amount: int, is_second: bool = False) -> bool:
        krw = self.get_krw_balance()
        if krw < amount:
            print(f"⚠️ 잔고 부족 {market}: {krw:,.0f}원 < {amount:,}원")
            return False
        if amount < MIN_ORDER_AMT:
            print(f"⚠️ 최소주문 미달: {amount:,}원")
            return False

        if not is_second:
            self.sold_today[market] = None

        params = {
            "market": market, "side": "bid",
            "price": str(amount), "ord_type": "price"
        }
        qs   = "&".join(f"{k}={v}" for k, v in params.items())
        hdrs = self._get_headers(qs)
        hdrs["Content-Type"] = "application/json"

        try:
            res = requests.post(
                f"{BASE_URL}/orders", headers=hdrs, json=params, timeout=5
            ).json()
            if res.get("uuid"):
                label = "2차" if is_second else "1차"
                self.notify(
                    f"🚀 [{label}매수] {market} | {amount:,}원\n"
                    f"시장:{self.market_status} | 공포탐욕:{self.fear_greed} | BTC:{self.btc_rate:+.2f}%"
                )
                return True
            print(f"❌ 매수 실패 {market}: {res.get('error',{}).get('message', res)}")
            return False
        except Exception as e:
            print(f"❌ 매수 예외 {market}: {e}"); return False

    # ============================================================
    # 주문 — 매도 (최소금액 체크 + 전량 전환)
    # ============================================================
    def sell(self, market: str, qty: float, reason: str,
             sell_price: float = 0, force_all: bool = False) -> bool:
        if sell_price > 0:
            amt = qty * sell_price
            if amt < MIN_ORDER_AMT:
                pos_qty = self.positions.get(market, {}).get("qty", 0)
                if not force_all and abs(qty - pos_qty) > 1e-8:
                    print(f"⚠️ 매도금액 미달({amt:,.0f}원) — 잔량 유지 {market}")
                    return False
                qty = pos_qty
                print(f"ℹ️ 최소금액 미달 → 전량 매도 전환 {market}")

        params = {
            "market": market, "side": "ask",
            "volume": str(qty), "ord_type": "market"
        }
        qs   = "&".join(f"{k}={v}" for k, v in params.items())
        hdrs = self._get_headers(qs)
        hdrs["Content-Type"] = "application/json"

        try:
            res = requests.post(
                f"{BASE_URL}/orders", headers=hdrs, json=params, timeout=5
            ).json()
            if res.get("uuid"):
                self.notify(f"💰 [매도] {market} | {reason} | {qty:.6f}개")
                profit_krw          = self._save_sell_history(market, sell_price, reason)
                self.daily_pnl     += profit_krw
                self.sold_today[market] = datetime.datetime.now().strftime("%H:%M:%S")
                print(f"📊 당일PNL: {self.daily_pnl:+,.0f}원 / 한도:{DAILY_LOSS_LIMIT:,}원")
                return True
            print(f"❌ 매도 실패 {market}: {res.get('error',{}).get('message', res)}")
            return False
        except Exception as e:
            print(f"❌ 매도 예외 {market}: {e}"); return False

    # ============================================================
    # 매도 체크
    # ============================================================
    def _check_sell(self, market: str, pos: dict):
        current = pos.get("current", 0)
        entry   = pos["entry_price"]
        qty     = pos["qty"]
        if not (entry and current and qty): return

        rate = (current - entry) / entry

        if market not in self.peak_tracker:
            self.peak_tracker[market] = {
                "peak_rate":  rate, "stage": 0,
                "remain_qty": qty,  "buy2_done": True, "buy1_price": entry,
            }

        tracker    = self.peak_tracker[market]
        stage      = tracker["stage"]
        buy2_done  = tracker.get("buy2_done", True)
        buy1_price = tracker.get("buy1_price", entry)

        if rate > tracker["peak_rate"]:
            tracker["peak_rate"] = rate

        # ── 급락 감지 즉시 손절 ─────────────────────────────
        ind         = self.get_indicators(market)
        candle_rate = ind.get("candle_rate", 0) if ind else 0
        if candle_rate <= STOP_LOSS_CRASH:
            self.notify(
                f"💥 급락감지 즉시손절 {market}\n"
                f"직전봉:{candle_rate:+.2%} | 현재:{rate:+.2%}"
            )
            if self.sell(market, qty, f"급락감지({candle_rate:+.2%})",
                         sell_price=current, force_all=True):
                self.daily_loss_count += 1
                self.peak_tracker.pop(market, None)
                self._check_daily_loss_limit()
            return

        # ── 2차 분할매수 ────────────────────────────────────
        if not buy2_done and stage == 0 and not self._is_paused:
            buy2_rate = (current - buy1_price) / buy1_price if buy1_price else 0
            if buy2_rate <= BUY_2ND_THRESHOLD:
                if self.market_status in ("weak", "stop"):
                    print(f"⚠️ 약세장 물타기 금지 {market}")
                else:
                    print(f"➕ 2차 매수(물타기) {market} | {buy2_rate:+.2%}")
                    if self.buy(market, BUY_2ND_AMT, is_second=True):
                        tracker["buy2_done"] = True
                        self.notify(f"➕ 2차 물타기 {market} | {buy2_rate:+.2%}")

        # ── 트레일링 스탑 ───────────────────────────────────
        if stage >= 2 and rate <= tracker["peak_rate"] - TRAIL_STOP:
            self.notify(
                f"📉 트레일링스탑 {market}\n"
                f"고점:{tracker['peak_rate']:+.2%} → 현재:{rate:+.2%}"
            )
            if self.sell(market, qty, f"트레일링스탑({rate:+.2%})",
                         sell_price=current, force_all=True):
                self.peak_tracker.pop(market, None)
            return

        # ── 2차 익절 +10% ───────────────────────────────────
        if stage < 2 and rate >= SELL_2ND_RATE:
            raw_qty  = tracker["remain_qty"] * SELL_2ND_QTY / (1 - SELL_1ST_QTY)
            sell_qty = min(max(raw_qty, 0.00001), qty)
            if (qty - sell_qty) * current < MIN_ORDER_AMT:
                sell_qty = qty
                print(f"ℹ️ 2차익절 후 잔량 미달 → 전량 {market}")
            self.notify(f"🎯 2차익절 {market} | {rate:+.2%} | {sell_qty:.6f}개")
            if self.sell(market, sell_qty, f"2차익절({rate:+.2%})",
                         sell_price=current, force_all=(sell_qty >= qty)):
                if sell_qty >= qty:
                    self.peak_tracker.pop(market, None)
                else:
                    tracker["stage"] = 2
            return

        # ── 1차 익절 +5% ────────────────────────────────────
        if stage < 1 and rate >= SELL_1ST_RATE:
            sell_qty = max(qty * SELL_1ST_QTY, 0.00001)
            if (qty - sell_qty) * current < MIN_ORDER_AMT or sell_qty * current < MIN_ORDER_AMT:
                sell_qty = qty
                print(f"ℹ️ 1차익절 최소금액 미달 → 전량 {market}")
            self.notify(f"✂️ 1차익절 {market} | {rate:+.2%} | {sell_qty:.6f}개")
            if self.sell(market, sell_qty, f"1차익절({rate:+.2%})",
                         sell_price=current, force_all=True):
                if sell_qty >= qty:
                    self.peak_tracker.pop(market, None)
                else:
                    tracker["stage"]      = 1
                    tracker["remain_qty"] = qty - sell_qty
            return

        # ── 손절 ────────────────────────────────────────────
        stop_line = self._get_stop_loss()
        if rate <= stop_line:
            is_night = self._is_night()
            label    = (
                "야간손절"   if is_night else
                "긴급손절"   if self.market_status == "stop" else
                "약세장손절" if self.market_status == "weak" else
                "손절"
            )
            self.notify(
                f"🛑 {label} {market} | {rate:+.2%} | 기준:{stop_line:.0%}\n"
                f"시장:{self.market_status} | 야간:{is_night} | 탐욕:{self.fear_greed}"
            )
            if self.sell(market, qty, f"{label}({rate:+.2%})",
                         sell_price=current, force_all=True):
                self.daily_loss_count += 1
                self.peak_tracker.pop(market, None)
                self._check_daily_loss_limit()

    def _check_daily_loss_limit(self):
        if self.daily_pnl <= DAILY_LOSS_LIMIT:
            self.notify(
                f"🚨 당일 손실 한도 초과! {self.daily_pnl:+,.0f}원\n"
                f"(한도:{DAILY_LOSS_LIMIT:,}원) — !c시작 으로 재개"
            )
            _st = _read_state()
            _st["paused"] = True
            _write_state(_st)

    # ============================================================
    # 상태 딕셔너리
    # ============================================================
    def _build_status(self, krw: float, total_profit: float) -> dict:
        return {
            "krw":           int(krw),
            "total_profit":  int(total_profit),
            "daily_pnl":     int(self.daily_pnl),
            "positions":     len(self.positions),
            "positions_detail": {
                m: {
                    "current":     int(p["current"]),
                    "entry_price": int(p["entry_price"]),
                    "qty":         p["qty"],
                    "rate":        round(
                        (p["current"] - p["entry_price"]) / p["entry_price"] * 100, 2
                    ) if p["entry_price"] > 0 else 0,
                }
                for m, p in self.positions.items()
            },
            "daily_loss":    self.daily_loss_count,
            "market_status": self.market_status,
            "btc_rate":      self.btc_rate,
            "fear_greed":    self.fear_greed,
            "is_night":      self._is_night(),
            "coin_pool":     self.coin_pool,
            "seed":          "30만원 소액 모드",
        }

    # ============================================================
    # 메인 루프
    # ============================================================
    def run(self):
        self.notify(
            f"🚀 [영암9 COIN v2.2] 종목 풀 자동확장 모드\n"
            f"🪙 거래대금 상위 {POOL_SIZE}개 자동 선별 + 고정 {len(FIXED_COINS)}개\n"
            f"💰 1차:{BUY_1ST_AMT:,}원 / 2차:{BUY_2ND_AMT:,}원 | 최대:{MAX_POSITIONS}코인\n"
            f"🎯 익절:+{SELL_1ST_RATE*100:.0f}%/+{SELL_2ND_RATE*100:.0f}% | "
            f"손절:{STOP_LOSS_BASIC*100:.0f}% | 야간:{STOP_LOSS_NIGHT*100:.0f}%\n"
            f"🛡️ BTC약세:{BTC_WEAK_THRESH:.0f}% | 탐욕MIN:{FEAR_GREED_MIN} | "
            f"일손실:{DAILY_LOSS_LIMIT:,}원"
        )

        while True:
            try:
                now = datetime.datetime.now().strftime("%H:%M:%S")
                print(f"\n⏰ [{now}] 코인봇 루프")

                bot_state       = _read_state()
                self._is_paused = bot_state.get("paused", False)

                # 자정 초기화
                today = datetime.datetime.now().strftime("%Y-%m-%d")
                if today != self._sold_today_date:
                    self.sold_today       = {}
                    self._sold_today_date = today
                    self.daily_loss_count = 0
                    self.daily_pnl        = 0
                    self.market_status    = "normal"
                    self._candle_cache    = {}
                    self._tech_cache      = {}
                    self._pool_cache_ts   = 0  # 종목 풀도 초기화
                    print("🔄 일일 초기화 완료")

                # !c시작 시 카운터 초기화
                saved_loss = bot_state.get("daily_loss", None)
                if saved_loss is not None and saved_loss == 0 and self.daily_loss_count > 0:
                    self.daily_loss_count = 0
                    self.daily_pnl        = 0
                    print("♻️ 손절카운터/손익 초기화")

                # ── 종목 풀 갱신 (5분 캐시) ───────────────────
                self._update_coin_pool()

                # ── 시장 상태 & 공포탐욕 갱신 ─────────────────
                self._update_market_status()
                self._update_fear_greed()

                # ── 포지션 조회 ───────────────────────────────
                self.positions = self.get_current_positions()
                krw            = self.get_krw_balance()
                total_profit   = sum(
                    (p["current"] - p["entry_price"]) * p["qty"]
                    for p in self.positions.values()
                )

                print(
                    f"💵 KRW:{krw:,.0f}원 | 포지션:{len(self.positions)}/{MAX_POSITIONS} | "
                    f"풀:{len(self.coin_pool)}개 | 시장:{self.market_status} | "
                    f"탐욕:{self.fear_greed} | 야간:{self._is_night()} | "
                    f"당일PNL:{self.daily_pnl:+,.0f}원"
                )
                for market, pos in self.positions.items():
                    rate  = (pos["current"] - pos["entry_price"]) / pos["entry_price"] * 100
                    emoji = "📈" if rate >= 0 else "📉"
                    print(
                        f"  {emoji} {market} | {rate:+.2f}% | "
                        f"{pos['qty']:.6f}개 | {pos['current']:,}원"
                    )
                print(f"📈 평가손익: {total_profit:+,.0f}원")
                
                # ── 긴급 매도 명령 ────────────────────────────
                pending = bot_state.get("pending_cmd")
                if pending and pending.get("type") == "sell":
                    sell_market = pending.get("market", "")
                    if sell_market in self.positions:
                        pos = self.positions[sell_market]
                        self.sell(
                            sell_market, pos["qty"],
                            "즉시매도(kiki명령)",
                            sell_price=pos.get("current", 0),
                            force_all=True
                        )
                        _write_cmd_result(f"✅ {sell_market} 즉시매도 완료")
                    else:
                        _write_cmd_result(f"⚠️ {sell_market} 미보유")

                
                # ── BTC stop 상태 ──────────────────────────────
                if self.market_status == "stop":
                    print("🚨 BTC 중단 — 긴급 손절 체크만")
                    for market, pos in list(self.positions.items()):
                        self._check_sell(market, pos)
                    _write_status(self._build_status(krw, total_profit))
                    time.sleep(LOOP_SLEEP); continue

                # ── paused 상태 ───────────────────────────────
                if self._is_paused:
                    print("⏸️ 일시중단 — 매도 체크만")
                    for market, pos in list(self.positions.items()):
                        self._check_sell(market, pos)
                    _write_status(self._build_status(krw, total_profit))
                    time.sleep(LOOP_SLEEP); continue

                # ── 일일 손실 한도 ────────────────────────────
                if self.daily_pnl <= DAILY_LOSS_LIMIT:
                    print(f"🚨 일손실 한도 초과: {self.daily_pnl:+,.0f}원")
                    for market, pos in list(self.positions.items()):
                        self._check_sell(market, pos)
                    _write_status(self._build_status(krw, total_profit))
                    time.sleep(LOOP_SLEEP); continue

                # ── 매수 로직 ─────────────────────────────────
                #available_slots = MAX_POSITIONS - len(self.positions)
                
                익절중 = sum(
                    1 for m in self.positions
                    if self.peak_tracker.get(m, {}).get("stage", 0) >= 1
                )
                available_slots = MAX_POSITIONS - len(self.positions) + 익절중
                if 익절중:
                    print(f"  ♻️ 익절진행중 {익절중}코인 슬롯 반환 → 가용슬롯:{available_slots}")

                if available_slots <= 0:
                    print("📦 포지션 FULL")
                elif self.daily_loss_count >= MAX_DAILY_LOSS:
                    print(f"🛑 당일 손절 {self.daily_loss_count}회 — 매수 중단")
                elif self.fear_greed < FEAR_GREED_MIN:
                    print(f"😱 극단공포({self.fear_greed}) — 매수 중단")
                elif self.market_status == "weak":
                    print(f"⚠️ BTC 약세({self.btc_rate:+.2f}%) — 매수 중단")
                else:
                    for market in self.coin_pool:
                        if available_slots <= 0: break
                        if market in self.positions: continue
                        if self.sold_today.get(market):
                            print(f"🚫 재매수 금지 {market}"); continue

                        signal, ind, reason = self.check_buy_signal(market)
                        if not signal:
                            print(f"  ⏭️ {market} — {reason}"); continue

                        ai_result = self.get_ai_score(market, ind)
                        ai_score  = ai_result["score"]
                        ai_reason = ai_result["reason"]
                        print(f"  🧠 {market} | AI:{ai_score}점 | {ai_reason}")

                        if ai_score < 55:
                            print(f"  ❌ AI점수 부족({ai_score}점)"); continue

                        print(f"🚀 매수 시도 {market} | {ai_score}점 | {BUY_1ST_AMT:,}원")
                        if self.buy(market, BUY_1ST_AMT):
                            buy_price = ind.get("current", 0)
                            est_qty   = BUY_1ST_AMT / buy_price if buy_price else 0
                            self.peak_tracker[market] = {
                                "peak_rate":  0.0, "stage": 0,
                                "remain_qty": est_qty, "buy2_done": False,
                                "buy1_price": buy_price,
                            }
                            self._save_buy_history(
                                market, buy_price, est_qty, ai_score, ai_reason
                            )
                            available_slots -= 1
                            time.sleep(1)

                # ── 매도 체크 ─────────────────────────────────
                for market, pos in list(self.positions.items()):
                    self._check_sell(market, pos)

                _write_status(self._build_status(krw, total_profit))

                perf = self._get_recent_performance()
                if perf:
                    print(
                        f"📊 성과 | 승률:{perf['win_rate']}% | "
                        f"평균:{perf['avg_profit']:+.2f}% | 누적:{perf['total_krw']:+,.0f}원"
                    )

                time.sleep(LOOP_SLEEP)

            except KeyboardInterrupt:
                perf     = self._get_recent_performance()
                stop_msg = (
                    f"🛑 [COIN] 봇 종료 | "
                    f"{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                )
                if perf:
                    stop_msg += (
                        f"\n📊 {perf['total']}건 | 승률:{perf['win_rate']}% | "
                        f"누적:{perf['total_krw']:+,.0f}원"
                    )
                self.notify(stop_msg)
                break
            except Exception as e:
                print(f"🚨 루프 오류: {e}")
                import traceback; traceback.print_exc()
                time.sleep(10)


# ============================================================
# 진입점
# ============================================================
if __name__ == "__main__":
    CBot().run()
