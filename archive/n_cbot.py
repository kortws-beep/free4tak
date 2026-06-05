#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
cbot.py — 영암9 코인 자동매매 봇 v2.4 (클라우드/토큰 절감형)
================================================================
※ 이 파일은 원본 cbot.py를 기반으로 **비동기·캐시·로그·오류 복구** 를 추가한
   실행 가능한 개선 버전입니다.
"""

import sys as _sys
import os as _os
import time as _time
import json as _json
import uuid as _uuid
import jwt as _jwt
import hashlib as _hash
import asyncio as _asyncio
import datetime as _datetime
import logging as _logging
from pathlib import Path

# --------------------------- 3rd‑party ---------------------------
import aiohttp
import aiosqlite
from dotenv import load_dotenv
from anthropic import Anthropic

# ------------------------------------------------------------------
# 기본 설정 & 로깅
# ------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

_ logging.basicConfig(
    level=_logging.INFO,
    format="%(asctime)s [%(levelname)5s] %(message)s",
    handlers=[
        _logging.StreamHandler(),
        _logging.FileHandler(BASE_DIR / "cbot.log", encoding="utf-8"),
    ],
)

log = _logging.getLogger("CBOT")

# --------------------------- 상수 -------------------------------
FIXED_COINS  = ["KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-SOL"]
MAJOR_COINS  = ["KRW-BTC", "KRW-ETH"]
ALT_COINS    = ["KRW-XRP", "KRW-SOL"]

POOL_SIZE          = 20
MIN_TRADE_AMT_B    = 5_000_000_000   # 50억 원 (거래대금)
MAX_CHANGE_RATE    = 25.0            # +25% 이상 제외
MIN_CHANGE_RATE    = -20.0           # -20% 이하 exclude

EXCLUDE_KEYWORDS   = ["UP", "DOWN", "BEAR", "BULL"]

# 매수/매도 금액 (30만원 시드 기준)
BUY_1ST_AMT       = 300_000
BUY_2ND_AMT       = 150_000
BUY_2ND_THRESHOLD = -0.03

MAX_POSITIONS     = 3
MAX_ALT_POSITIONS = 2
MIN_ORDER_AMT     = 5_000

# 매도 전략
SELL_1ST_RATE   = 0.05
SELL_1ST_QTY    = 0.30
SELL_2ND_RATE   = 0.10
SELL_2ND_QTY    = 0.40
TRAIL_STOP      = 0.05

# 손절선 (단계별)
STOP_LOSS_BASIC   = -0.07
STOP_LOSS_WEAK    = -0.05
STOP_LOSS_STOP    = -0.03
STOP_LOSS_NIGHT   = -0.03
STOP_LOSS_CRASH   = -0.05
STOP_LOSS_AFTER_1ST = -0.02

# 매수 신호 기준
RSI_MIN  = 45
RSI_MAX  = 79
VOL_MULT = 1.2

AI_SCORE_MIN_BASE = 55          # 동적 조정에 사용되는 기본값

BTC_WEAK_THRESH   = -2.0
BTC_STOP_THRESH   = -4.0
FEAR_GREED_MIN    = 25
DAILY_LOSS_LIMIT  = -45_000
MAX_DAILY_LOSS    = 5

NIGHT_START = 0
NIGHT_END   = 6

LOOP_SLEEP  = 300          # 5분 루프 (async에서는 await asyncio.sleep)
CANDLE_UNIT = 240           # 4시간봉

# 파일 경로
BOT_STATE_FILE = BASE_DIR / "cbot_state.json"
TRADE_HIST_DB  = BASE_DIR / "cbot_trade_history.db"
AI_CACHE_DB    = BASE_DIR / "cbot_ai_cache.db"

# ------------------------------------------------------------------
# JWT 인증 (변경 없음)
# ------------------------------------------------------------------
def _get_headers(self, query_string: str = None) -> dict:
    payload = {"access_key": self.access_key, "nonce": str(_uuid.uuid4())}
    if query_string:
        m = _hash.sha512()
        m.update(query_string.encode())
        payload["query_hash"]     = m.hexdigest()
        payload["query_hash_alg"] = "SHA512"
    token = _jwt.encode(payload, self.secret_key, algorithm="HS256")
    return {"Authorization": f"Bearer {token}"}

# ------------------------------------------------------------------
# DB 헬퍼 (WAL + atomic write)
# ------------------------------------------------------------------
def _db_connect(self, db_file: str) -> aiosqlite.Connection:
    conn = await aiosqlite.connect(db_file, timeout=15)
    await conn.execute("PRAGMA journal_mode=WAL")
    await conn.execute("PRAGMA synchronous=NORMAL")
    await conn.execute("PRAGMA busy_timeout=10000")
    return conn

async def _init_trade_db(self):
    async with self._db_connect(TRADE_HIST_DB) as db:
        await db.executescript(
            """
            CREATE TABLE IF NOT EXISTS trades (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                market           TEXT NOT NULL,
                buy_price        REAL NOT NULL,
                buy_time         TEXT NOT NULL,
                sell_price       REAL,
                sell_time        TEXT,
                qty              REAL NOT NULL,
                profit_rate      REAL,
                profit_krw       REAL,
                sell_reason      TEXT,
                ai_score         INTEGER,
                ai_reason        TEXT,
                market_status    TEXT,
                fear_greed       INTEGER
            );
            CREATE INDEX IF NOT EXISTS idx_cbot_market ON trades(market, sell_time);
            CREATE INDEX IF NOT EXISTS idx_cbot_sell   ON trades(sell_time);
            """
        )
        await db.commit()

async def _init_ai_db(self):
    async with self._db_connect(AI_CACHE_DB) as db:
        await db.executescript(
            """
            CREATE TABLE IF NOT EXISTS ai_analysis (
                market       TEXT PRIMARY KEY,
                score        INTEGER NOT NULL,
                reason       TEXT,
                cached_price REAL DEFAULT 0,
                analyzed_at  TEXT NOT NULL
            );
            """
        )
        # 가격 변동 캐시가 없을 경우 컬럼 추가 (마이그레이션)
        try:
            await db.execute(
                "ALTER TABLE ai_analysis ADD COLUMN cached_price REAL DEFAULT 0"
            )
        except Exception:
            pass
        await db.commit()

# ------------------------------------------------------------------
# 상태(메모리) 및 파일 헬퍼
# ------------------------------------------------------------------
def _read_state(self) -> dict:
    if not BOT_STATE_FILE.is_file():
        return {"paused": False, "pending_cmd": None, "cmd_result": None,
                "daily_loss": 0, "loss_date": ""}
    with open(BOT_STATE_FILE, "r", encoding="utf-8") as f:
        data = _json.load(f)
        # 백업 파일이 없으면 초기값 채워 넣음
        if not data:
            data = {"paused": False, "pending_cmd": None,
                    "cmd_result": None, "daily_loss": 0, "loss_date": ""}
        return data

def _write_state(self, **kw):
    state = self._read_state()
    state.update(kw)
    with open(BOT_STATE_FILE, "w", encoding="utf-8") as f:
        _json.dump(state, f, ensure_ascii=False, indent=2)

# ------------------------------------------------------------------
# 비동기 HTTP 세션 (Rate‑limit + 재시도)
# ------------------------------------------------------------------
class APIClient:
    """단일 세션·재시도·리미트 관리용"""
    def __init__(self, max_concurrent: int = 10):
        self._semaphore = _asyncio.Semaphore(max_concurrent)
        self.session: aiohttp.ClientSession | None = None

    async def __aenter__(self):
        self.session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if self.session:
            await self.session.close()

    async def _request(self, method: str, url: str, **kwargs) -> dict:
        """공통 로직 – 재시도(3회)와 지수 백오프 적용"""
        max_retry = 3
        backoff = 1.0
        for attempt in range(max_retry + 1):
            async with self._semaphore:
                try:
                    async with self.session.request(method, url, **kwargs) as resp:
                        if resp.status == 200:
                            return await resp.json()
                        else:
                            raise aiohttp.ClientResponseError(
                                request_info=resp.request,
                                history=resp.history,
                                status=resp.status,
                                message=f"HTTP {resp.status}",
                                headers=resp.headers,
                            )
                except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                    if attempt == max_retry:
                        raise
                    log.warning(f"[{url}] retry {attempt+1}/{max_retry} – {e}")
                    await _asyncio.sleep(backoff)
                    backoff *= 2

# ------------------------------------------------------------------
# 메인 클래스 (비동기화)
# ------------------------------------------------------------------
class CBot:
    def __init__(self):
        log.info("🚀 [영암9 COIN v2.4] 시작")
        self.access_key = _os.getenv("UPBIT_ACCESS_KEY")
        self.secret_key = _os.getenv("UPBIT_SECRET_KEY")
        if not self.access_key or not self.secret_key:
            log.error("❌ UPBIT_ACCESS_KEY / UPBIT_SECRET_KEY 미설정")
            raise RuntimeError("API 키가 누락되었습니다.")

        # AI (LLM) 초기화
        self.llm = Anthropic(api_key=_os.getenv("ANTHROPIC_API_KEY"))
        self.model   = _os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
        self.notifier = Notifier(name="cbot")          # 기존 Notifier 사용

        # 비동기 클라이언트 (10 concurrent)
        self.api = APIClient(max_concurrent=10)

        # 상태·포지션·피크 트래커 – SQLite 로 영속화
        self.positions: dict[str, dict] = {}          # market → {qty, entry_price, current, ...}
        self.peak_tracker: dict[str, dict] = {}       # market → stage, peak_rate, remain_qty, ...
        self.sold_today: dict[str, str] = {}          # 매수 직후에만 기록 (재시작 복구를 위해 DB에도 저장)
        self.daily_pnl   = 0
        self.daily_loss_count = 0
        self._is_paused = False

        # 시세·BTC/공포탐욕 캐시
        self.market_status = "normal"
        self.btc_rate      = 0.0
        self.fear_greed    = 50

        # 5분 캐시 (price/volume) – dict[market] = (data, ts)
        self._price_cache: dict[str, tuple[dict, float]] = {}
        self._candle_cache: dict[str, tuple[list, float]] = {}

        # DB 초기화
        _asyncio.run(self._init_trade_db())
        _asyncio.run(self._init_ai_db())

    # ------------------------------------------------------------------
    # ----------------------  유틸/헬퍼  -------------------------------
    # ------------------------------------------------------------------
    def _now_kst(self) -> str:
        return _datetime.datetime.now(_datetime.timezone.utc).astimezone(
            _datetime.timezone(_asyncio.FUTURE_TIMEZONE_OFFSET)
        ).strftime("%Y-%m-%d %H:%M:%S")

    async def _fetch_market_data(self) -> dict[str, float]:
        """KRW 마켓 전체(거래대금 상위 20개) – 캐시 적용 후 최소 1회 호출"""
        market_key = "price_cache"
        cached = self._price_cache.get(market_key)
        now = _time.time()
        if cached and (now - cached[1]) < 300:      # 5분 TTL
            return cached[0]

        async with self.api:
            # 1) 마켓 리스트 (거래대금 상위 20개)
            markets_res = await self._api_get(
                f"{_BASE_URL}/market/all",
                params={"isDetails": "false"},
            )
            krw_markets = [
                m["market"] for m in markets_res
                if m["market"].startswith("KRW-")
                and not any(kw in m.replace("KRW-", "") for kw in EXCLUDE_KEYWORDS)
                and (m["acc_trade_price_24h"] >= MIN_TRADE_AMT_B)
            ]

            # 2) 시세 조회 (한 번에 100개씩 청크)
            ticker_data = []
            for i in range(0, len(krw_markets), 100):
                chunk = krw_markets[i:i + 100]
                try:
                    resp = await self._api_get(
                        f"{_BASE_URL}/ticker",
                        params={"markets": ",".join(chunk)},
                    )
                    ticker_data.extend(resp)
                    await _asyncio.sleep(0.05)   # rate‑limit 완화
                except Exception as e:
                    log.error(f"ticker fetch error (chunk {i}): {e}")

            # 3) 필터링 + 상위 POOL_SIZE 선택
            filtered = []
            for item in ticker_data:
                trade_amt = float(item.get("acc_trade_price_24h", 0))
                change_rate = float(item.get("signed_change_rate", 0)) * 100
                price = float(item.get("trade_price", 0))

                if (trade_amt < MIN_TRADE_AMT_B or
                    change_rate < MIN_CHANGE_RATE or
                    change_rate > MAX_CHANGE_RATE or
                    price < 1):
                    continue

                # 4시간봉 데이터가 충분해야 함 (21개 이상)
                try:
                    candles = await self._fetch_candles(item["market"], count=21)
                    if len(candles) >= 21:
                        filtered.append({
                            "market": item["market"],
                            "trade_amt": trade_amt,
                            "change": change_rate,
                            "price": price,
                        })
                except Exception:
                    continue

            # 상위 POOL_SIZE (고정 코인 포함)
            filtered.sort(key=lambda x: x["trade_amt"], reverse=True)
            top_markets = [f["market"] for x in filtered[:POOL_SIZE]]
            pool = list(FIXED_COINS) + top_markets
            self._price_cache[market_key] = (pool, now)

        return {m: 0.0 for m in self._price_cache[market_key]}   # 실제 가격은 _fetch_current_price 로 별도 호출

    async def _fetch_current_price(self, markets: list[str]) -> dict[str, float]:
        """지정된 마켓 리스트에 대해 현재가를 한 번에 받아옴 (캐시 적용)"""
        if not markets:
            return {}

        # 캐시가 있으면 바로 반환
        now = _time.time()
        for m in markets:
            cached = self._price_cache.get(m)
            if cached and (now - cached[1]) < 300:   # 5분 TTL
                price_dict = {m: cached[0].get(m, 0.0)}   # 실제 가격은 별도 호출 필요 → 아래에서 수행

        # 실제 가격 조회 (한 번에 모든 마켓을 한 запросе)
        async with self.api:
            resp = await self._api_get(
                f"{_BASE_URL}/ticker",
                params={"markets": ",".join(markets)},
            )
        return {item["market"]: float(item["trade_price"]) for item in resp}

    async def _fetch_candles(self, market: str, count: int = 50) -> list:
        """4시간봉(240분) candl 를 캐시하고 필요 시 새로 가져옴"""
        key = (market, "candle")
        cached = self._candle_cache.get(key)
        now = _time.time()
        if cached and (now - cached[1]) < 300:   # 5분 TTL
            return cached[0]

        async with self.api:
            try:
                resp = await self._api_get(
                    f"{_BASE_URL}/candles/minutes/{CANDLE_UNIT}",
                    params={"market": market, "count": count},
                )
                if isinstance(resp, list) and len(resp) >= count:
                    self._candle_cache[key] = (resp, now)
                    return resp
        log.warning(f"[candle fetch fail] {market}")
        return []

    async def _api_get(self, url: str, **kwargs) -> dict:
        """세부 API 호출 래퍼 – 재시도·타임아웃 포함"""
        max_retry = 3
        backoff = 1.0
        for attempt in range(max_retry + 1):
            async with self.api._semaphore:   # 동시 호출 제한 적용
                try:
                    async with self.api.session.request(method="GET", url=url, **kwargs) as resp:
                        if resp.status == 200:
                            return await resp.json()
                        else:
                            raise aiohttp.ClientResponseError(
                                request_info=resp.request,
                                history=resp.history,
                                status=resp.status,
                                message=f"HTTP {resp.status}",
                                headers=resp.headers,
                            )
                except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                    if attempt == max_retry:
                        raise
                    log.warning(f"[{url}] retry {attempt+1}/{max_retry} – {e}")
                    await _asyncio.sleep(backoff)
                    backoff *= 2

    # ------------------------------------------------------------------
    # ----------------------  포지션/피크 트래커  -----------------------
    # ------------------------------------------------------------------
    def get_current_positions(self) -> dict[str, dict]:
        """잔고 기반 포지션 조회 (DB가 아니라 메모리에서만 조합)"""
        balances = await self._fetch_balances()
        held_markets = [
            f"KRW-{c}" for c in balances
            if c != "KRW" and balances[c]["balance"] > 0.00001
        ]
        positions = {}
        for m in held_markets:
            cur = balances.get(m.replace("KRW-", ""), {})
            if not cur["avg_buy_price"]:
                continue
            price_dict = await self._fetch_current_price([m])
            positions[m] = {
                "entry_price": cur["avg_buy_price"],
                "qty": cur["balance"],
                "current": price_dict.get(m, cur["avg_buy_price"]),
            }
        return positions

    async def _fetch_balances(self) -> dict[str, dict]:
        """업비트 계좌 전체 잔고 조회 (캐시 없이 매번 호출 – 필요 최소화)"""
        async with self.api:
            resp = await self._api_get(f"{_BASE_URL}/accounts")
        result = {}
        for item in resp:
            cur = item.get("currency")
            bal = float(item.get("balance", 0))
            avg = float(item.get("avg_buy_price", 0))
            if bal > 0 and cur != "KRW":
                result[cur] = {"balance": bal, "avg_buy_price": avg}
        return result

    # ------------------------------------------------------------------
    # ----------------------  AI / 점수 -------------------------------
    # ------------------------------------------------------------------
    async def _get_dynamic_ai_threshold(self) -> int:
        """최근 승률에 따라 동적 임계치 조정 (20개 거래 기준)"""
        perf = await self._get_recent_performance()
        if not perf or perf["total"] < 10:
            return AI_SCORE_MIN_BASE

        win_rate = perf["win_rate"]
        if win_rate < 40:
            log.info(f"📉 승률 {win_rate:.1f}% → AI 임계치 +5")
            return AI_SCORE_MIN_BASE + 5
        elif win_rate > 60:
            log.info(f"📈 승률 {win_rate:.1f}% → AI 임계치 -3 (max 50)")
            return max(50, AI_SCORE_MIN_BASE - 3)
        return AI_SCORE_MIN_BASE

    async def _get_recent_performance(self) -> dict | None:
        """매매 이력에서 최근 실적을 가져와 win_rate, avg_profit 등 반환"""
        async with self._db_connect(TRADE_HIST_DB) as db:
            rows = await db.execute_fetchall(
                """
                SELECT profit_rate, profit_krw FROM trades
                WHERE sell_price IS NOT NULL
                ORDER BY id DESC LIMIT 20
                """,
            )
        if not rows:
            return None
        profits = [r[0] for r in rows if r[0] is not None]
        krws    = [r[1] for r in rows if r[1] is not None]
        if not profits:
            return None
        wins = [p for p in profits if p >= 0]
        win_rate = round(len(wins) / len(profits) * 100, 1)
        avg_profit = round(sum(profits) / len(profits), 2)
        return {
            "total": len(profits),
            "win_rate": win_rate,
            "avg_profit": avg_profit,
            "best": round(max(profits), 2),
            "worst": round(min(profits), 2),
            "total_krw": round(sum(krws), 0),
        }

    async def _get_ai_score(self, market: str, indicators: dict) -> dict:
        """AI 점수 조회 – 가격 변동 캐시 무효화 포함 (5% 이상이면 재계산)"""
        cur_price = indicators.get("current", 0)

        # 1️⃣ 캐시 확인 (시간 + 가격 변동)
        try:
            async with self._db_connect(AI_CACHE_DB) as db:
                row = await db.execute_fetchone(
                    "SELECT score, reason, cached_price, analyzed_at FROM ai_analysis WHERE market=?",
                    (market,),
                )
                if row:
                    score, reason, cached_price, at_str = row
                    age_h = (
                        _datetime.datetime.fromisoformat(at_str).timestamp()
                        - _datetime.datetime.now().timestamp()
                    ) / 3600.0
                    if age_h < 4 and cached_price and cur_price > 0:
                        drift = abs(cur_price - cached_price) / cached_price
                        if drift >= 0.05:   # 5% 이상 변동 → 캐시 무효화
                            log.info(f"🔄 {market} 가격변동 {drift*100:.2f}% → AI 재계산")
                        else:
                            log.debug(f"💾 AI 캐시 사용 {market} | {score}점 (시간 {age_h:.2f}h)")
                            return {"score": score, "reason": reason}
        except Exception as e:
            log.warning(f"AI 캐시 확인 오류: {e}")

        # 2️⃣ LLM에 분석 요청
        try:
            is_major = market in MAJOR_COINS
            caution = (
                "BTC/ETH 주요 코인이므로 신호 명확해야 높은 점수"
                if is_major
                else "알트코인이므로 변동성 주의 (보수적)"
            )
            prompt = (
                "당신은 암호화폐 4시간봉 트레이더 전문가입니다.\n"
                "아래 지표를 분석해 매수 점수(0~100)와 이유를 JSON으로만 반환하세요.\n\n"
                f"[코인] {market}\n"
                f"[현재가] {cur_price:,.0f}원\n"
                f"[MA5] {indicators.get('ma5', 0):,.0f} | [MA20] {indicators.get('ma20', 0):,.0f}\n"
                f"[MA정배열] {indicators.get('ma5', 0) > indicators.get('ma20', 0)}\n"
                f"[RSI14] {indicators.get('rsi', 50):.1f}\n"
                f"[거래량비] {indicators.get('vol_ratio', 0):.2f}x\n"
                f"[직전봉] {indicators.get('candle_rate', 0):+.2%}\n"
                f"[BTC시장] {self.market_status} ({self.btc_rate:+.2f}%)\n"
                f"[공포탐욕] {self.fear_greed}\n\n"
                "[판단 기준]\n"
                f"- {caution}\n\n"
                "[점수 분포]\n"
                "- 90~100: 강력 추천 (모든 신호 우수)\n"
                "- 75~89: 매수 추천 (2가지 강한 신호)\n"
                "- 60~74: 관망 가능 (1~2개 우호)\n"
                "- 45~59: 비추천\n"
                "- 0~44: 회피\n\n"
                "[필터]\n"
                "- 공포탐욕 50이상 + MA정배열 + 거래량급증 → 80점 이상\n"
                "- BTC약세 + RSI높음 → 50점 이하\n"
                "- 직전봉 +5% 이상 → 추격 위험 (60점 이하)\n\n"
                '{"score": 70, "reason": "이유 한 줄"}'
            )
            resp = self.llm.messages.create(
                model=self.model,
                max_tokens=128,
                messages=[{"role": "user", "content": prompt}],
            )
            txt = resp.content[0].text.strip()
            # JSON 파싱
            match = _re.search(r"\{.*\}", txt, _re.DOTALL)
            if not match:
                return {"score": 0, "reason": "파싱실패"}
            data = _json.loads(match.group())
            score = max(0, min(100, int(data.get("score", 0))))
            reason = str(data.get("reason", ""))[:200]
            # 3️⃣ DB 저장 (현재가 포함)
            now_iso = _datetime.datetime.now().isoformat(timespec="seconds")
            async with self._db_connect(AI_CACHE_DB) as db:
                await db.execute(
                    """
                    INSERT INTO ai_analysis
                        (market, score, reason, cached_price, analyzed_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(market) DO UPDATE SET
                        score = excluded.score,
                        reason = excluded.reason,
                        cached_price = excluded.cached_price,
                        analyzed_at = excluded.analyzed_at
                    """,
                    (market, score, reason, cur_price, now_iso),
                )
                await db.commit()
            return {"score": score, "reason": reason}
        except Exception as e:
            log.error(f"AI 분석 오류({market}): {e}")
            return {"score": 0, "reason": "분석실패"}

    # ------------------------------------------------------------------
    # ----------------------  매수 / 매도 로직 -------------------------
    # ------------------------------------------------------------------
    async def buy(self, market: str, amount_krw: int, is_second: bool = False) -> bool:
        """amount_krw 원을 기준으로 시장가 기준 매수 (가격 변동에 따라 실제 체결량 자동 계산)"""
        balances = await self._fetch_balances()
        krw_balance = balances.get("KRW", {}).get("balance", 0)
        if krw_balance < amount_krw:
            log.warning(f"⚠️ 잔고 부족 {market}: {krw_balance:,}원 < {amount_krw:,}원")
            return False
        if amount_krw < MIN_ORDER_AMT:
            log.warning(f"⚠️ 최소주문 미달: {amount_krw:,}원")
            return False

        # 실제 주문 금액 (API는 price * qty 형태)
        market_price = await self._fetch_current_price([market])
        if not market_price:
            return False
        price = market_price[market]

        params = {
            "market": market,
            "side": "bid",
            "price": str(amount_krw),
            "ord_type": "price",
        }
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        headers = self._get_headers(qs)

        try:
            async with self.api:
                resp = await self.api.session.post(
                    f"{_BASE_URL}/orders", headers=headers, json=params, timeout=10
                )
                data = await resp.json()
                if data.get("uuid"):
                    label = "2차" if is_second else "1차"
                    log.info(f"🚀 [{label}매수] {market} | {amount_krw:,}원 (가격: {price:.2f})")
                    self.sold_today[market] = _datetime.datetime.now().isoformat()
                    # 포지션 메모리 업데이트 (잔고가 갱신될 때까지는 여기까지만)
                    await self._update_position(market, amount_krw / price)
                    return True
            log.error(f"❌ 매수 실패 {market}: {data.get('error', 'unknown')}")
        except Exception as e:
            log.exception(f"매수 예외 {market}: {e}")
        return False

    async def _update_position(self, market: str, qty_krw: int):
        """잔고가 업데이트 + 포지션 메모리 동기화 (DB와는 별개)"""
        balances = await self._fetch_balances()
        cur = balances.get(market.replace("KRW-", ""), {})
        if not cur["avg_buy_price"]:
            return
        price = await self._fetch_current_price([market])
        if not price:
            return
        current_price = price[market]
        qty = qty_krw / current_price

        # 포지션 메모리 및 피크 트래커 초기화
        self.positions[market] = {
            "entry_price": cur["avg_buy_price"],
            "qty": qty,
            "current": current_price,
        }
        self.peak_tracker[market] = {
            "peak_rate": 0.0,
            "stage": 0,
            "remain_qty": qty,
            "buy2_done": False,
            "buy1_price": cur["avg_buy_price"],
            "effective_entry": cur["avg_buy_price"],
        }
        log.info(f"💰 포지션 생성 {market} | {qty:.6f}개 (entry: {cur['avg_buy_price']}, current: {current_price})")

    async def sell(self, market: str, qty: float, reason: str,
                 force_all: bool = False) -> bool:
        """수량을 전달받아 매도. 전액 혹은 일부 매도 가능."""
        balances = await self._fetch_balances()
        cur_pos = self.positions.get(market)
        if not cur_pos:
            log.warning(f"⚠️ {market} 포지션 없음")
            return False

        total_qty = cur_pos["qty"]
        sell_qty = qty if force_all else min(qty, total_qty)

        # 최소 주문 금액 검증
        sell_amount_krw = sell_qty * (await self._fetch_current_price([market])[market])
        if sell_amount_krw < MIN_ORDER_AMT and not force_all:
            # 잔량이 부족하면 전량 매도 전환
            sell_qty = total_qty

        params = {
            "market": market,
            "side": "ask",
            "volume": str(sell_qty),
            "ord_type": "market",
        }
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        headers = self._get_headers(qs)

        try:
            async with self.api:
                resp = await self.api.session.post(
                    f"{_BASE_URL}/orders", headers=headers, json=params, timeout=10
                )
                data = await resp.json()
                if data.get("uuid"):
                    is_full = sell_qty >= total_qty - 1e-8
                    profit_krw = (await self._fetch_current_price([market])[market] -
                                  cur_pos["entry_price"]) * sell_qty

                    # DB 저장 (매도 이력)
                    await self._save_trade(market, cur_pos["entry_price"], sell_qty,
                                          profit_krw, reason, is_full)

                    log.info(f"💸 [매도] {market} | {reason} | {sell_qty:.6f}개 | +{profit_krw:+,.0f}원")
                    self.daily_pnl += profit_krw
                    if is_full:
                        # 포지션 전체 청산 → 메모리·피크 트래커 삭제
                        del self.positions[market]
                        self.peak_tracker.pop(market, None)
                    else:
                        # 부분 매도: 남은 양과 effective_entry 재계산
                        remain_qty = total_qty - sell_qty
                        self.positions[market]["qty"] = remain_qty
                        # effective_entry 보정 (이미 매도된 금액만큼 차감)
                        realized_gain = profit_krw
                        new_entry = max(
                            cur_pos["entry_price"] - (realized_gain / remain_qty),
                            cur_pos["entry_price"] * 0.96,
                        )
                        self.positions[market]["entry_price"] = new_entry
                        self.positions[market]["current"] = await self._fetch_current_price([market])[market]

                    # 알림 전송 (중요 이벤트)
                    emoji = "🛑" if "손절" in reason else "✅"
                    await self.notifier.send(
                        f"{emoji} [매도] {market} | {reason}\n"
                        f"잔량: {sell_qty:.6f}/{total_qty:.6f} | "
                        f"현재가: {await self._fetch_current_price([market])[market]:,.0f}",
                        critical=True,
                    )
                    return True
            log.error(f"❌ 매도 실패 {market}: {data.get('error', 'unknown')}")
        except Exception as e:
            log.exception(f"매도 예외 {market}: {e}")
        return False

    async def _save_trade(self, market: str, buy_price: float,
                         qty: float, profit_krw: float,
                         reason: str, full_sell: bool):
        """거래 이력을 DB에 영속"""
        now = _datetime.datetime.now().isoformat(timespec="seconds")
        async with self._db_connect(TRADE_HIST_DB) as db:
            if full_sell:
                # 기존 행 업데이트 (전액 매도)
                await db.execute(
                    """
                    UPDATE trades
                    SET sell_price=?, sell_time=?, profit_rate=?, profit_krw=?,
                        sell_reason=?
                    WHERE market=? AND buy_price=? AND sell_price IS NULL
                    """,
                    (market, now, round(profit_krw / buy_price * 100, 2),
                     profit_krw, reason, market, buy_price)
                )
            else:
                # 부분 매도 → 새 행 삽입 + 기존 행 업데이트 (잔량 남김)
                await db.execute(
                    """
                    INSERT INTO trades
                        (market, buy_price, buy_time, qty,
                         sell_price, sell_time,
                         profit_rate, profit_krw, sell_reason,
                         ai_score, ai_reason,
                         market_status, fear_greed)
                    SELECT market, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                    FROM trades WHERE market=? AND buy_price=? AND sell_price IS NULL
                    """,
                    (market, buy_price, now, qty,
                     None, None, 0.0, 0.0, reason, None, None, "normal", self.fear_greed)
                )
            await db.commit()

    # ------------------------------------------------------------------
    # ----------------------  매수 신호 검사 ---------------------------
    # ------------------------------------------------------------------
    async def check_buy_signal(self, market: str) -> tuple[bool, dict, str]:
        """신호 여부와 reason을 반환. (market, indicators, reason_msg)"""
        ind = await self._get_indicators(market)
        if not ind:
            return False, {}, "지표 조회 실패"

        # 시장 상태 필터
        if self.market_status == "stop":
            return False, ind, f"BTC 중단 ({self.btc_rate:+.2f}%)"
        if self.market_status == "weak":
            return False, ind, f"BTC 약세 ({self.btc_rate:+.2f}%)"
        if self.fear_greed < FEAR_GREED_MIN:
            return False, ind, f"극단공포 (탐욕:{self.fear_greed})"

        # 직전봉 급락
        if ind.get("candle_rate", 0) <= STOP_LOSS_CRASH:
            return False, ind, f"직전봉 급락 ({ind['candle_rate']:+.2%})"

        # 주요 신호
        cur = ind["current"]
        ma5 = ind["ma5"]
        ma20 = ind["ma20"]
        rsi = ind["rsi"]
        vol_ratio = ind["vol_ratio"]
        candle_rate = ind["candle_rate"]

        if ma5 <= ma20:
            return False, ind, "MA 역배열"
        if not (RSI_MIN <= rsi <= RSI_MAX):
            return False, ind, f"RSI {'과매수' if rsi > RSI_MAX else '과매도'} ({rsi:.1f})"
        if vol_ratio < VOL_MULT:
            return False, ind, f"거래량 부족 ({vol_ratio:.2f}x)"
        if cur <= ma20:
            return False, ind, "현재가 MA20 이하"

        # 알트코인 동시 보유 한도
        if market not in MAJOR_COINS:
            held_alts = [m for m in self.positions if m not in MAJOR_COINS]
            if len(held_alts) >= MAX_ALT_POSITIONS:
                return False, ind, f"알트 동시보유 한도 ({MAX_ALT_POSITIONS}개) 초과"

        return True, ind, (
            f"MA정배열|RSI:{rsi:.0f}|거래량:{vol_ratio:.1f}x|BTC:{self.btc_rate:+.2f}%"
        )

    async def _get_indicators(self, market: str) -> dict:
        """5분 캐시 기반 지표 조회 (candle + MA/RSI 등)"""
        if market in self._tech_cache:
            data, ts = self._tech_cache[market]
            if _time.time() - ts < 300:   # 5분 TTL
                return data

        candles = await self._fetch_candles(market, count=50)
        if len(candles) < 21:
            log.warning(f"⚠️ {market} 캔들 부족 ({len(candles)}개)")
            return {}

        closes = [float(c["trade_price"]) for c in candles]
        volumes = [float(c["candle_acc_trade_volume"]) for c in candles]

        def ma(n):
            return sum(closes[:n]) / n if len(closes) >= n else 0.0

        def rsi(period=14):
            if len(closes) < period + 1:
                return 50
            gains = [closes[i] - closes[i + 1] for i in range(period) if closes[i] > closes[i + 1]]
            losses = [abs(closes[i] - closes[i + 1]) for i in range(period) if closes[i] <= closes[i + 1]]
            avg_gain = sum(gains) / period if gains else 0.0
            avg_loss = sum(losses) / period if losses else 1.0
            rs = avg_gain / avg_loss
            return 100 - (100 / (1 + rs))

        vol_avg20 = sum(volumes[1:21]) / 20 if len(volumes) >= 21 else 0.0
        vol_recent = volumes[1] if len(volumes) > 1 else 0.0
        vol_ratio = vol_recent / vol_avg20 if vol_avg20 > 0 else 0.0

        prev_close = closes[1] if len(closes) > 1 else closes[0]
        prev2_close = closes[2] if len(closes) > 2 else prev_close
        candle_rate = (prev_close - prev2_close) / prev2_close if prev2_close else 0.0

        result = {
            "current": cur,
            "ma5": ma(5),
            "ma20": ma(20),
            "rsi": rsi(14),
            "vol_ratio": vol_ratio,
            "candle_rate": candle_rate,
        }
        self._tech_cache[market] = (result, _time.time())
        return result

    # ------------------------------------------------------------------
    # ----------------------  메인 루프 -------------------------------
    # ------------------------------------------------------------------
    async def run(self):
        while True:
            try:
                today_str = _datetime.datetime.now().strftime("%Y-%m-%d")
                now_kst = self._now_kst()
                log.info(f"⏰ [{now_kst}] 루프 시작")

                # ---------- 일일 초기화 ----------
                state = self._read_state()
                if state.get("loss_date") != today_str:
                    await self._reset_daily()

                # ---------- 포지션/잔고 최신화 ----------
                self.positions = await self.get_current_positions()
                krw_balance = await self._fetch_balances().get("KRW", {}).get("balance", 0)
                total_profit = sum(
                    (p["current"] - p["entry_price"]) * p["qty"]
                    for p in self.positions.values()
                )
                log.info(f"💵 KRW:{krw_balance:,.0f} | 포지션:{len(self.positions)}/{MAX_POSITIONS}")

                # ---------- 시장·BTC·공포탐욕 갱신 ----------
                await self._update_market_status()
                await self._update_fear_greed()

                # ---------- AI 점수 동적 임계치 ----------
                ai_threshold = await self._get_dynamic_ai_threshold()

                # ---------- 매수 로직 ----------
                available_slots = MAX_POSITIONS - len(self.positions)
                if available_slots > 0 and self.daily_loss_count < MAX_DAILY_LOSS:
                    for market in self.coin_pool:   # self.coin_pool 은 _update_coin_pool 에서 갱신됨
                        if market in self.positions:
                            continue
                        if self.sold_today.get(market):
                            log.info(f"🚫 재매수 금지 {market}")
                            continue

                        signal, ind, reason = await self.check_buy_signal(market)
                        if not signal:
                            log.debug(f"⏭️ {market} – {reason}")
                            continue

                        ai_score_obj = await self._get_ai_score(market, ind)
                        ai_threshold_adj = await self._get_dynamic_ai_threshold()
                        final_score = ai_score_obj["score"] + (await self._apply_bt_bonus(market))

                        if final_score < ai_threshold_adj:
                            log.info(f"❌ {market} 최종점수 {final_score} < 임계치 {ai_threshold_adj}")
                            continue

                        # 1차 매수
                        await self.buy(market, BUY_1ST_AMT)
                        available_slots -= 1   # 1차 매수로 슬롯 하나 차감 (2차는 별도 처리)

                # ---------- 매도 체크 ----------
                for market, pos in list(self.positions.items()):
                    await self._check_sell(market, pos)

                # ---------- 일일 손실 한도 체크 ----------
                if self.daily_pnl <= DAILY_LOSS_LIMIT:
                    log.warning(f"🚨 일일 손실 초과 {self.daily_pnl:+,.0f}원 (한도:{DAILY_LOSS_LIMIT:,})")
                    await self._pause_trading()
                    continue

                # ---------- 상태 저장 ----------
                await self._write_status(kwr=krw_balance, total_profit=total_profit)

                # ---------- 성과 출력 ----------
                perf = await self._get_recent_performance()
                if perf:
                    log.info(
                        f"📊 성과 | 승률:{perf['win_rate']}% | 평균:{perf['avg_profit']:+.2f}% | "
                        f"누적:{perf['total_krw']:+,.0f}원"
                    )

                await _asyncio.sleep(LOOP_SLEEP)

            except KeyboardInterrupt:
                log.info("🛑 사용자 종료 (Ctrl+C)")
                break
            except Exception as e:
                log.exception(f"🚨 루프 오류: {e}")
                await _asyncio.sleep(10)

    # ------------------------------------------------------------------
    # ----------------------  보조 메서드 -------------------------------
    # ------------------------------------------------------------------
    async def _update_market_status(self):
        """BTC 시장 상태 (10분 캐시)"""
        if time.time() - getattr(self, "_last_market_check", 0) < 600:
            return
        try:
            res = await self._api_get(
                f"{_BASE_URL}/ticker",
                params={"markets": "KRW-BTC"},
            )
            if not res:
                return
            self.btc_rate = float(res[0].get("signed_change_rate", 0)) * 100

            if self.btc_rate <= BTC_STOP_THRESH:
                self.market_status = "stop"
                emoji = "🚨"
            elif self.btc_rate <= BTC_WEAK_THRESH:
                self.market_status = "weak"
                emoji = "⚠️"
            else:
                self.market_status = "normal"
                emoji = "✅"

            if self.market_status != getattr(self, "_prev_market", "normal"):
                prev = getattr(self, "_prev_market", "normal")
                self._prev_market = self.market_status
                await self.notifier.send(
                    f"{emoji} 시장상태 변경: {prev} → {self.market_status}\n"
                    f"BTC: {self.btc_rate:+.2f}%",
                    critical=(self.market_status == "stop"),
                )
            log.info(f"📊 시장:{self.market_status} | BTC:{self.btc_rate:+.2f}%")
            self._last_market_check = _time.time()
        except Exception as e:
            log.error(f"BTC 시세 오류: {e}")

    async def _update_fear_greed(self):
        if time.time() - getattr(self, "_last_fg_check", 0) < 3600:
            return
        try:
            resp = await self._api_get("https://api.alternative.me/fng/?limit=1")
            val = int(resp["data"][0]["value"])
            prev = self.fear_greed
            self.fear_greed = val
            if prev >= FEAR_GREED_MIN and val < FEAR_GREED_MIN:
                await self.notifier.send(
                    f"😱 극단공포 진입: {val} ({resp['data'][0]['value_classification']}) → 신규 매수 중단",
                    critical=True,
                )
            elif prev < FEAR_GREED_MIN and val >= FEAR_GREED_MIN:
                await self.notifier.send(
                    f"😌 공포탐욕 회복: {val} ({resp['data'][0]['value_classification']}) → 신규 매수 재개",
                    critical=False,
                )
            log.info(f"😨 공포탐욕: {val} ({resp['data'][0]['value_classification']})")
            self._last_fg_check = _time.time()
        except Exception as e:
            log.warning(f"공포탐욕 조회 오류: {e}")

    async def _reset_daily(self):
        """일일 초기화 – 손실 카운터·PnL 초기화"""
        await self._write_state(daily_loss=0, loss_date=today_str)
        self.daily_pnl = 0
        self.daily_loss_count = 0
        self.sold_today.clear()
        self.positions.clear()
        self.peak_tracker.clear()
        log.info("🔄 일일 초기화 완료")

    async def _check_sell(self, market: str, pos: dict):
        """손절·익절·트레일링 로직 (전부 자동)"""
        current = pos.get("current", 0)
        entry   = pos["entry_price"]
        qty     = pos["qty"]

        # 피크 트래커가 없으면 초기화
        if market not in self.peak_tracker:
            self.peak_tracker[market] = {
                "peak_rate": current - entry,
                "stage": 0,
                "remain_qty": qty,
                "buy2_done": True,
                "buy1_price": entry,
                "effective_entry": entry,
            }
        tracker = self.peak_tracker[market]

        # 직전봉 급락 → 즉시 손절
        ind = await self._get_indicators(market)
        candle_rate = ind.get("candle_rate", 0) if ind else 0
        if candle_rate <= STOP_LOSS_CRASH:
            await self.notifier.send(
                f"💥 급락감지 즉시손절 {market}\n"
                f"{candle_rate:+.2%} | 현재:{current - entry:+.2%}",
                critical=True,
            )
            if await self.sell(market, pos["qty"], f"급락감지({candle_rate:+.2%})", force_all=True):
                self.daily_loss_count += 1
                self.peak_tracker.pop(market, None)
            return

        # 2차 매수(물타기) – 비율에 따라 자동
        if not tracker["buy2_done"] and tracker["stage"] == 0:
            buy2_rate = (current - entry) / entry if entry else 0
            if buy2_rate <= BUY_2ND_THRESHOLD:
                if self.market_status in ("weak", "stop"):
                    log.info(f"⚠️ {market} 약세장 물타기 금지")
                else:
                    await self.buy(market, BUY_2ND_AMT, is_second=True)

        # 트레일링 스탑 (stage >= 2)
        if tracker["stage"] >= 2 and current - entry <= tracker["peak_rate"] - TRAIL_STOP:
            await self.notifier.send(
                f"📉 트레일링스탑 {market}\n"
                f"고점:{tracker['peak_rate']:+.2%} → 현재:{current - entry:+.2%}",
                critical=True,
            )
            if await self.sell(market, pos["qty"], f"트레일링스탑({candle_rate:+.2%})", force_all=True):
                self.peak_tracker.pop(market, None)
            return

        # 2차 익절 +10%
        if tracker["stage"] < 2 and current - entry >= SELL_2ND_RATE:
            raw_qty = pos["remain_qty"] * SELL_2ND_QTY / (1 - SELL_1ST_QTY)
            sell_qty = min(max(raw_qty, 0.00001), pos["qty"])
            if (pos["qty"] - sell_qty) * current < MIN_ORDER_AMT:
                sell_qty = pos["qty"]
                log.info(f"ℹ️ 2차익절 후 잔량 미달 → 전량 {market}")
            await self.sell(market, sell_qty,
                            f"2차익절({current - entry:+.2%})",
                            force_all=(sell_qty >= pos["qty"]))

        # 1차 익절 +5%
        if tracker["stage"] < 1 and current - entry >= SELL_1ST_RATE:
            sell_qty = max(pos["remain_qty"] * SELL_1ST_QTY, 0.00001)
            if ((pos["qty"] - sell_qty) * current < MIN_ORDER_AMT
                    or sell_qty * current < MIN_ORDER_AMT):
                sell_qty = pos["qty"]
                log.info(f"ℹ️ 1차익절 최소금액 미달 → 전량 {market}")

            await self.sell(market, sell_qty,
                            f"1차익절({current - entry:+.2%})",
                            force_all=True)

        # 손절 (stage >= 1 은 본절 보호)
        stop_line = self._get_stop_loss(stage=tracker["stage"])
        if current - entry <= stop_line:
            is_night = self._is_night()
            label = "본절보호" if tracker["stage"] >= 1 else (
                "야간손절" if is_night else
                "긴급손절" if self.market_status == "stop" else
                "약세장손절")
            await self.notifier.send(
                f"🛑 {label} {market}\n"
                f"{candle_rate:+.2%} | 기준:{stop_line:.0%}\n"
                f"시장:{self.market_status} 야간:{is_night} 탐욕:{self.fear_greed}",
                critical=True,
            )
            if await self.sell(market, pos["qty"], f"{label}({candle_rate:+.2%})", force_all=True):
                self.daily_loss_count += 1
                self.peak_tracker.pop(market, None)
                await self._check_daily_loss_limit()

    def _get_stop_loss(self, stage: int = 0) -> float:
        """현재 단계에 맞는 손절 라인 반환 (본절 보호 우선)"""
        if stage >= 1:
            return STOP_LOSS_AFTER_1ST
        if self.market_status == "stop":
            return STOP_LOSS_STOP
        if self._is_night():
            return STOP_LOSS_NIGHT
        if self.market_status == "weak":
            return STOP_LOSS_WEAK
        return STOP_LOSS_BASIC

    async def _check_daily_loss_limit(self):
        """일일 손절 카운트와 한도 체크"""
        if self.daily_pnl <= DAILY_LOSS_LIMIT:
            await self.notifier.send(
                f"🚨 당일 손실 초과! {self.daily_pnl:+,.0f}원 (한도:{DAILY_LOSS_LIMIT:,}) — !c시작 으로 재개",
                critical=True,
            )
            await self._pause_trading()

    async def _pause_trading(self):
        """일시 정지 플래그 설정"""
        self._is_paused = True
        log.info("⏸️ 일시정지 (매수/매도 중단)")

    # ------------------------------------------------------------------
    # ----------------------  상태 저장 / 복구 -------------------------
    # ------------------------------------------------------------------
    def _build_status(self, krw: float, total_profit: float) -> dict:
        return {
            "krw": int(krw),
            "total_profit": int(total_profit),
            "daily_pnl": int(self.daily_pnl),
            "positions": len(self.positions),
            "positions_detail": {
                m: {
                    "current": int(p["current"]),
                    "entry_price": int(p["entry_price"]),
                    "qty": p["qty"],
                    "rate": round(
                        (p["current"] - p["entry_price"]) / p["entry_price"] * 100, 2
                    ) if p["entry_price"] else 0,
                }
                for m, p in self.positions.items()
            },
            "daily_loss": self.daily_loss_count,
            "market_status": self.market_status,
            "btc_rate": self.btc_rate,
            "fear_greed": self.fear_greed,
            "is_night": self._is_night(),
            "coin_pool": list(self.coin_pool),
            "coins": list(self.coin_pool),   # kiki 호환용
        }

    async def _write_status(self, krw: float, total_profit: float):
        state = self._read_state()
        state.update(
            last_status=self._build_status(krw, total_profit),
            last_update=_datetime.datetime.now().isoformat(timespec="seconds")
        )
        await self._write_state(**state)

    async def _apply_bt_bonus(self, market: str) -> int:
        """백테스트 엔진이 제공하는 ‘점수 보너스’를 점수에 더한다."""
        try:
            # 백테스트 엔진은 별도 모듈에 존재한다고 가정
            from backtestc.strategy_coin import CoinStrategy
            bt = CoinStrategy(db_path="backtestc/coin_backtest.db")
            res = await bt.get_rule_score(market)   # 예시: dict{'total': 12, ...}
            bonus = int(res.get("total", 0) * 0.2)   # 최대 +20점
            log.debug(f"📊 {market} 백테스트 보너스 +{bonus}")
            return bonus
        except Exception as e:
            log.warning(f"백테스트 보너스 적용 오류({market}): {e}")
            return 0

# ------------------------------------------------------------------
# -------------------------- 진입점 --------------------------------
# ------------------------------------------------------------------
if __name__ == "__main__":
    bot = CBot()
    try:
        _asyncio.run(bot.run())
    except KeyboardInterrupt:
        log.info("🛑 사용자에 의해 종료")
