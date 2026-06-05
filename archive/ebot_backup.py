"""
ebot.py — 영암9 종가베팅 봇 (전면 재구성판)
================================================================
[이 파일이 하는 일 — 비개발자용 설명]

종가베팅(End-Of-Day) 전략:
- 장 마감 직전(15:10~15:20) 1종목 매수
- 다음날 아침(08:00~09:10) 매도
- 익절 +5% / 손절 -2%

[왜 이런 전략?]
- 장 마감 직전 종가에 가까운 가격은 다음날 시초가 변동을 잘 반영
- 강세주는 시초가 갭 상승, 약세주는 빠르게 손절 가능

[적용된 개선사항]
1. ★ today 변수 루프 시작 시 정의 (NameError 방지)
2. ★ atomic 상태파일 쓰기 (common_utils 사용)
3. ★ 디스코드 알림 재시도 (notifier 사용)
4. ★ 시간외 매도 실패 시 자동 시장가 폴백
5. ★ 미체결 주문 추적 강화

[모듈 구조]
  ebot.py        ← 메인 (이 파일)
  kis_api.py     ← 한투 API (검증됨)
  kiwoom_api.py  ← 키움 API (검증됨)
  notifier.py    ← 디스코드 (재시도 강화)
  common_utils.py← 공통 헬퍼

[실행]
  python3 ebot.py
"""
import os
import time
import json
import asyncio
import sqlite3
import datetime
import requests
from dotenv import load_dotenv

from common_utils import (
    now_kst, now_hhmm, now_hms, today_str,
    is_weekend, safe_int, safe_float,
    read_state, write_state, update_state,
    fmt_won, fmt_pct, get_hoga_unit,
)
from kis_api    import KisAPI
from kiwoom_api import KiwoomAPI
from notifier   import Notifier

load_dotenv()


# ============================================================
# 상수
# ============================================================
BUY_AMOUNT    = 200_000   # 1종목당 20만원
MAX_POSITIONS = 1         # 항상 1종목만

# 시간대
BUY_START_TIME  = "1510"   # 매수 시작
BUY_END_TIME    = "1520"   # 매수 마감
PRE_SELL_START  = "0800"   # 장전 시간외 매도 시작
PRE_SELL_END    = "0900"   # 장전 시간외 매도 마감
REG_SELL_START  = "0900"   # 정규장 매도 시작
REG_SELL_END    = "0910"   # 정규장 매도 마감

# 매도 기준
TAKE_PROFIT  =  0.05   # 익절 +5%
STOP_LOSS    = -0.02   # 손절 -2%

SLEEP_INTERVAL = 30

# 키움 조건검색식 키워드
EOD_COND_KEYWORD = "주도주,종가매매"

# 파일
TRADE_HIST_DB  = "ebot_trade_history.db"
BOT_STATE_FILE = "ebot_state.json"


# ============================================================
# 상태 파일 헬퍼
# ============================================================
def _read_state() -> dict:
    return read_state(BOT_STATE_FILE, default={
        "paused":      False,
        "pending_cmd": None,
        "cmd_result":  None,
    })

def _update_state(**kwargs):
    update_state(BOT_STATE_FILE, **kwargs)

def _write_status(status: dict):
    state = _read_state()
    state["last_status"] = status
    state["last_update"] = now_hms()
    write_state(BOT_STATE_FILE, state)


# ============================================================
# DB 헬퍼 (WAL 모드)
# ============================================================
def _db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(TRADE_HIST_DB, timeout=15)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=10000")
    return conn

def init_db():
    try:
        conn = _db_connect()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                code        TEXT    NOT NULL,
                stock_name  TEXT,
                buy_price   REAL    NOT NULL,
                buy_time    TEXT    NOT NULL,
                sell_price  REAL,
                sell_time   TEXT,
                qty         INTEGER NOT NULL,
                profit_rate REAL,
                sell_reason TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ebot_sell ON trades(sell_time)")
        conn.commit(); conn.close()
        print(f"✅ 종가봇 DB 초기화 ({TRADE_HIST_DB})")
    except Exception as e:
        print(f"❌ DB 초기화 오류: {e}")

def save_buy(code, stock_name, buy_price, qty):
    try:
        now  = datetime.datetime.now().isoformat(timespec="seconds")
        conn = _db_connect()
        conn.execute("""
            INSERT INTO trades (code, stock_name, buy_price, buy_time, qty)
            VALUES (?,?,?,?,?)
        """, (code, stock_name, buy_price, now, qty))
        conn.commit(); conn.close()
    except Exception as e:
        print(f"⚠️ 매수이력 저장 오류: {e}")

def save_sell(code, sell_price, sell_reason):
    try:
        now  = datetime.datetime.now().isoformat(timespec="seconds")
        conn = _db_connect()
        row  = conn.execute("""
            SELECT id, buy_price FROM trades
            WHERE code=? AND sell_price IS NULL
            ORDER BY id DESC LIMIT 1
        """, (code,)).fetchone()
        if not row:
            conn.close(); return
        trade_id, buy_price = row
        profit_rate = (sell_price - buy_price) / buy_price * 100 if buy_price else 0
        conn.execute("""
            UPDATE trades SET sell_price=?, sell_time=?, profit_rate=?, sell_reason=?
            WHERE id=?
        """, (sell_price, now, round(profit_rate, 2), sell_reason, trade_id))
        conn.commit(); conn.close()
        emoji = "✅" if profit_rate >= 0 else "❌"
        print(f"   {emoji} 이력저장 {code} | {profit_rate:+.2f}% | {sell_reason}")
    except Exception as e:
        print(f"⚠️ 매도이력 저장 오류: {e}")

def get_today_realized(today: str = None) -> int:
    if not today:
        today = today_str()
    try:
        conn = _db_connect()
        rows = conn.execute("""
            SELECT buy_price, sell_price, qty FROM trades
            WHERE sell_price IS NOT NULL AND sell_time >= ?
        """, (today,)).fetchall()
        conn.close()
        return sum(int((sp - bp) * qty) for bp, sp, qty in rows
                   if sp is not None and bp is not None)
    except Exception:
        return 0


# ============================================================
# 메인 봇 클래스
# ============================================================
class EBot:
    """종가베팅 봇."""

    def __init__(self):
        print("🌆 [영암9 EOD] 종가베팅봇 가동")

        self.api      = KisAPI()
        self.kiwoom   = KiwoomAPI()
        self.notifier = Notifier(name="ebot")

        init_db()

        self.positions      = {}    # {code: {entry_price, qty, stock_name}}
        self.bought_today   = False
        self.sold_today     = False
        self.buy_date       = ""
        self.pre_sell_tried = False
        self.code_name_map  = {}

        # ★ today 변수 — 클래스 속성으로 미리 정의 (NameError 방지)
        self._today           = today_str()
        self._holiday_checked = ""
        self._is_holiday      = False

        if self.kiwoom.enabled:
            print(f"✅ 키움 연동 | 조건검색 키워드: '{EOD_COND_KEYWORD}'")
        else:
            print("⚠️ 키움 API 없음 — 종목 선정 불가")

    # ============================================================
    # 알림
    # ============================================================
    def _notify(self, msg: str, critical: bool = False):
        self.notifier.send(f"[EOD] {msg}", critical=critical)

    def _name(self, code: str) -> str:
        return self.code_name_map.get(code, code)

    # ============================================================
    # 키움 종가매매 조건검색식 종목 조회
    # ============================================================
    async def _get_eod_codes(self) -> list:
        """키움 WebSocket으로 '종가매매' 조건검색식 결과 조회"""
        import websockets as _ws
        token = self.kiwoom.get_token()
        if not token:
            return []

        codes = []
        seen  = set()
        try:
            async with _ws.connect(
                "wss://api.kiwoom.com:10000/api/dostk/websocket"
            ) as ws:
                await ws.send(json.dumps({"trnm": "LOGIN", "token": token}))
                res = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
                if res.get("return_code") != 0:
                    print("⚠️ 키움 로그인 실패")
                    return []

                # 조건검색식 목록
                await ws.send(json.dumps({"trnm": "CNSRLST"}))
                cond_list = []
                while True:
                    try:
                        res = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
                        if res.get("trnm") == "PING":
                            await ws.send(json.dumps(res)); continue
                        if res.get("trnm") == "CNSRLST":
                            cond_list = res.get("data", []); break
                    except asyncio.TimeoutError:
                        break

                print(f"  🔍 키움 조건검색식: {len(cond_list)}개")

                # "종가매매" 키워드만 사용
                for cond in cond_list:
                    seq  = cond[0] if isinstance(cond, list) else cond.get("seq", "")
                    name = cond[1] if isinstance(cond, list) else cond.get("name", "")

                    if EOD_COND_KEYWORD not in name:
                        print(f"  ⏭️ 제외: [{seq}]{name}")
                        continue

                    print(f"  ✅ 종가매매 조건검색: [{seq}]{name}")
                    await ws.send(json.dumps({
                        "trnm": "CNSRREQ", "seq": seq,
                        "search_type": "0", "stex_tp": "K",
                    }))
                    fetched = 0
                    while True:
                        try:
                            res = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
                            if res.get("trnm") == "PING":
                                await ws.send(json.dumps(res)); continue
                            if res.get("return_code") != 0:
                                break
                            for item in (res.get("data") or []):
                                raw = (item.get("9001", "") if isinstance(item, dict)
                                       else (item[0] if item else ""))
                                code = raw.lstrip("A") if raw.startswith("A") else raw
                                iname = (item.get("302", "") if isinstance(item, dict)
                                         else (item[1] if len(item) > 1 else ""))
                                if code and code not in seen:
                                    seen.add(code); codes.append(code)
                                    self.code_name_map[code] = iname
                                    fetched += 1
                            if res.get("cont_yn") != "Y":
                                break
                        except asyncio.TimeoutError:
                            break
                    print(f"  📊 종가매매 종목: {fetched}개")
        except Exception as e:
            print(f"⚠️ 키움 WebSocket 오류: {e}")
        return codes

    # ============================================================
    # 종목 선정 (1순위 종목 1개 선정)
    # ============================================================
    def _select_best(self, codes: list) -> tuple:
        """
        조건검색 결과 중 가장 좋은 1종목 선정.
        기준: 등락률 높음 + 거래대금 충분 + 시총 적정
        """
        candidates = []
        for code in codes[:30]:  # 최대 30개 분석
            data = self.api.get_market_data(code)
            if not data:
                continue
            try:
                change = safe_float(data.get("prdy_ctrt",  0))
                value  = safe_int(data.get("acml_tr_pbmn", 0)) // 100_000_000
                price  = safe_float(data.get("stck_prpr",  0))
                mkt_cap = safe_int(data.get("hts_avls", 0))

                if price <= 0:        continue
                if value < 30:        continue   # 거래대금 30억 미만
                if change < 0:        continue   # 음봉 제외
                if mkt_cap < 500:     continue   # 너무 작은 시총 제외
                # ★ 추가 필터: 너무 많이 오른 종목 제외 (다음날 갭하락 위험)
                if change > 15:       continue

                candidates.append((code, change, value, data))
                print(f"  📊 후보: {code}({self._name(code)}) | "
                      f"{change:+.2f}% | {value:,}억 | 시총{mkt_cap:,}억")
            except Exception:
                continue

        if not candidates:
            return None, None

        # 등락률 + 거래대금 종합 점수 (상승 강도 우선, 거래대금 보조)
        def score_func(x):
            _, change, value, _ = x
            return change * 1.0 + min(value / 100, 5)  # 거래대금은 최대 +5점

        candidates.sort(key=score_func, reverse=True)
        best_code, best_change, best_value, best_data = candidates[0]
        print(f"  🎯 선정: {best_code}({self._name(best_code)}) | "
              f"{best_change:+.2f}% | {best_value:,}억")
        return best_code, best_data

    # ============================================================
    # 매수 (지정가)
    # ============================================================
    def _do_buy(self, code: str, price: float):
        psbl = self.api.get_psbl_order_cash(code)
        if psbl <= 0:
            print(f"⚠️ 주문가능금액 없음")
            return False

        order_cash = min(psbl, BUY_AMOUNT)

        # 호가 단위에 맞춰 1호가 위로 (체결률 향상)
        hoga = get_hoga_unit(price)
        limit_price = int(price / hoga) * hoga + hoga

        qty = int(order_cash / (limit_price * 1.00015))
        if qty <= 0:
            print(f"⚠️ 수량 부족")
            return False

        print(f"💡 종가매수 {code} | {fmt_won(order_cash)} | {qty}주 | {limit_price:,}원")

        url  = f"{self.api.base_url}/uapi/domestic-stock/v1/trading/order-cash"
        data = {
            "CANO":         self.api.cano,
            "ACNT_PRDT_CD": self.api.acnt,
            "PDNO":         code,
            "ORD_QTY":      str(qty),
            "ORD_UNPR":     str(limit_price),
            "ORD_DVSN":     "00",  # 지정가
        }
        headers = {
            "authorization": f"Bearer {self.api.token}",
            "appkey":        self.api.appkey,
            "appsecret":     self.api.secret,
            "tr_id":         "TTTC0802U",
            "hashkey":       self.api.get_hashkey(data),
        }
        try:
            res = requests.post(url, headers=headers, data=json.dumps(data),
                              timeout=10).json()
            if res.get("rt_cd") == "0":
                self._notify(
                    f"🌆 종가매수 {code}({self._name(code)}) | "
                    f"{qty}주 | {fmt_won(order_cash)} | {limit_price:,}원",
                    critical=True,
                )
                save_buy(code, self._name(code), price, qty)
                self.positions[code] = {
                    "entry_price": price,
                    "qty":         qty,
                    "stock_name":  self._name(code),
                }
                self.bought_today = True
                self.buy_date     = today_str()
                return True
            else:
                print(f"❌ 매수 실패: {res.get('msg1', '알 수 없는 오류')}")
                return False
        except Exception as e:
            print(f"❌ 매수 예외: {e}")
            return False

    # ============================================================
    # 매도
    # ============================================================
    def _do_sell(self, code: str, qty: int, reason: str,
                 sell_price: float, is_pre_market: bool = False):
        """
        is_pre_market=True  → 장전 시간외 단일가 (ORD_DVSN: "05")
        is_pre_market=False → 시장가 (ORD_DVSN: "01")
        """
        ord_dvsn = "05" if is_pre_market else "01"
        url  = f"{self.api.base_url}/uapi/domestic-stock/v1/trading/order-cash"
        data = {
            "CANO":         self.api.cano,
            "ACNT_PRDT_CD": self.api.acnt,
            "PDNO":         code,
            "ORD_QTY":      str(qty),
            "ORD_UNPR":     "0",
            "ORD_DVSN":     ord_dvsn,
        }
        headers = {
            "authorization": f"Bearer {self.api.token}",
            "appkey":        self.api.appkey,
            "appsecret":     self.api.secret,
            "tr_id":         "TTTC0801U",
            "hashkey":       self.api.get_hashkey(data),
        }
        try:
            res = requests.post(url, headers=headers, data=json.dumps(data),
                              timeout=10).json()
            if res.get("rt_cd") == "0":
                tag = "(장전단일가)" if is_pre_market else "(시장가)"
                is_loss = "손절" in reason
                emoji   = "💔" if is_loss else "💰"
                self._notify(
                    f"{emoji} 종가매도{tag} {code}({self._name(code)}) | {reason}",
                    critical=True,
                )
                save_sell(code, sell_price, reason)
                self.positions.pop(code, None)
                self.sold_today = True
                return True
            else:
                print(f"❌ 매도 실패: {res.get('msg1', '알 수 없는 오류')}")
                return False
        except Exception as e:
            print(f"❌ 매도 예외: {e}")
            return False

    # ============================================================
    # 미체결 주문 취소
    # ============================================================
    def _cancel_unfilled_orders(self, code: str):
        """장전 시간외 미체결 주문 취소"""
        url = f"{self.api.base_url}/uapi/domestic-stock/v1/trading/inquire-psbl-rvsecncl"
        headers = {
            "authorization": f"Bearer {self.api.token}",
            "appkey":        self.api.appkey,
            "appsecret":     self.api.secret,
            "tr_id":         "TTTC8036R",
        }
        params = {
            "CANO":           self.api.cano,
            "ACNT_PRDT_CD":   self.api.acnt,
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
            "INQR_DVSN_1":    "0",
            "INQR_DVSN_2":    "0",
        }
        try:
            res    = requests.get(url, headers=headers, params=params,
                                  timeout=10).json()
            orders = res.get("output", [])
            cancelled = 0
            for order in orders:
                if order.get("pdno") != code:
                    continue
                ord_no = order.get("odno", "")
                if not ord_no:
                    continue
                cancel_data = {
                    "CANO":               self.api.cano,
                    "ACNT_PRDT_CD":       self.api.acnt,
                    "KRX_FWDG_ORD_ORGNO": order.get("krx_fwdg_ord_orgno", ""),
                    "ORGN_ODNO":          ord_no,
                    "ORD_DVSN":           "02",
                    "RVSE_CNCL_DVSN_CD":  "02",
                    "ORD_QTY":            "0",
                    "ORD_UNPR":           "0",
                    "QTY_ALL_ORD_YN":     "Y",
                }
                cancel_headers = {**headers, "tr_id": "TTTC0803U",
                                  "hashkey": self.api.get_hashkey(cancel_data)}
                cancel_res = requests.post(
                    f"{self.api.base_url}/uapi/domestic-stock/v1/trading/order-rvsecncl",
                    headers=cancel_headers, data=json.dumps(cancel_data),
                    timeout=10,
                ).json()
                if cancel_res.get("rt_cd") == "0":
                    cancelled += 1
                    print(f"  🗑️ 미체결 취소: {code} 주문번호:{ord_no}")
            if cancelled:
                print(f"  ✅ {cancelled}건 취소 완료")
                self._notify(f"🗑️ 미체결 {cancelled}건 취소 완료")
        except Exception as e:
            print(f"⚠️ 미체결 취소 오류: {e}")

    # ============================================================
    # 메인 루프
    # ============================================================
    def run(self):
        # ★ 15:00 이전이면 대기 — nbot/sbot과 API 충돌 방지
        # start.sh에서 ebot도 함께 실행되지만, 15:00까지 루프 진입 안 함
        EBOT_START_TIME = "1500"
        while True:
            now_t = now_hhmm()
            # 전날 보유 포지션 있으면(다음날 아침 매도 처리) 즉시 진입
            if now_t >= EBOT_START_TIME or self.positions:
                break
            # 주말이면 대기 없이 바로 진입 (어차피 주말 체크에서 걸림)
            if is_weekend():
                break
            print(f"⏳ [EOD] 15:00 대기 중... (현재 {now_t[:2]}:{now_t[2:]})"
                  f" | nbot/sbot API 충돌 방지")
            time.sleep(60)

        self._notify(
            f"🌆 [영암9 EOD] 종가베팅봇 가동\n"
            f"⏰ {now_kst().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"💰 매수: {fmt_won(BUY_AMOUNT)} / 1종목\n"
            f"🎯 익절: +{TAKE_PROFIT*100:.0f}% | 손절: {STOP_LOSS*100:.0f}%\n"
            f"📋 조건검색: '{EOD_COND_KEYWORD}'\n"
            f"🌅 매도: 08:00 장전단일가 → 09:00 시장가",
            critical=True,
        )

        while True:
            try:
                # ★ 핵심: today를 루프 맨 앞에서 정의 (NameError 방지)
                today = today_str()
                now_t = now_hhmm()
                now   = now_hms()

                # ── 주말 ─────────────────────────────────────
                if is_weekend():
                    print(f"😴 [{now}] 주말 — 대기")
                    time.sleep(300); continue

                # ── 휴장일 (하루 1회) ──────────────────────────
                if self._holiday_checked != today:
                    self._is_holiday      = not self.api.is_market_open()
                    self._holiday_checked = today
                    if self._is_holiday:
                        self._notify("🎌 오늘은 휴장일 — 봇 대기")
                if self._is_holiday:
                    print(f"🎌 [{now}] 휴장일 — 대기 중...")
                    time.sleep(300); continue

                # ── 일일 초기화 ────────────────────────────────
                if today != self._today:
                    self._today         = today
                    self.bought_today   = False
                    self.sold_today     = False
                    self.pre_sell_tried = False
                    self.api._mkt_cache = {}
                    print("🔄 [EOD] 일일 초기화 완료")

                self.api.refresh_token_if_needed()

                # ── 보유 포지션 동기화 ────────────────────────
                if self.positions:
                    real_pos = self.api.get_current_positions()
                    for code in list(self.positions.keys()):
                        if code not in real_pos:
                            print(f"  ℹ️ {code} 포지션 없음 (매도 완료 확인)")
                            self.positions.pop(code, None)
                            self.sold_today = True

                print(f"\n🌆 [{now}] | 보유:{len(self.positions)}종목 | "
                      f"매수:{self.bought_today} | 매도:{self.sold_today}")

                # ============================================================
                # 시간대별 처리
                # ============================================================

                # ── 장전 시간외 매도 (08:00~09:00) ────────────
                if PRE_SELL_START <= now_t < PRE_SELL_END:
                    if self.positions and not self.pre_sell_tried:
                        print("🌅 장전 시간외 매도 시도...")
                        for code, pos in list(self.positions.items()):
                            qty   = pos["qty"]
                            mdata = self.api.get_market_data(code)
                            cur   = (safe_float(mdata.get("stck_prpr", 0))
                                     if mdata else pos["entry_price"])
                            rate  = ((cur - pos["entry_price"]) / pos["entry_price"]
                                     if pos["entry_price"] else 0)
                            ok = self._do_sell(code, qty, f"장전단일가({rate:+.2%})",
                                              cur, is_pre_market=True)
                            if ok:
                                self._notify(
                                    f"🌅 장전단일가 매도 접수 {code}({self._name(code)}) | "
                                    f"{rate:+.2%}",
                                )
                        self.pre_sell_tried = True

                # ── 정규장 매도 (09:00~09:10) ─────────────────
                elif REG_SELL_START <= now_t <= REG_SELL_END:
                    # 장전 미체결 주문 취소 (시장가로 다시 보내기 위해)
                    if self.pre_sell_tried and self.positions and now_t == REG_SELL_START:
                        for code in list(self.positions.keys()):
                            self._cancel_unfilled_orders(code)
                        time.sleep(2)

                    if self.positions:
                        print("📈 정규장 매도 체크...")
                        for code, pos in list(self.positions.items()):
                            mdata = self.api.get_market_data(code)
                            if not mdata:
                                continue
                            cur   = safe_float(mdata.get("stck_prpr", 0))
                            entry = pos["entry_price"]
                            qty   = pos["qty"]
                            if entry == 0 or cur == 0:
                                continue
                            rate  = (cur - entry) / entry

                            if rate >= TAKE_PROFIT:
                                self._do_sell(code, qty, f"익절({rate:+.2%})", cur)
                            elif rate <= STOP_LOSS:
                                self._do_sell(code, qty, f"손절({rate:+.2%})", cur)
                            elif now_t >= "0905":
                                # 09:05 이후 강제 시장가 매도
                                self._notify(f"⏰ 시간 도래 강제매도 {code} | {rate:+.2%}")
                                self._do_sell(code, qty, f"시간매도({rate:+.2%})", cur)

                # ── 정규장 중 익절/손절 체크 (09:10~15:10) ─────
                elif "0910" <= now_t < BUY_START_TIME:
                    if self.positions:
                        for code, pos in list(self.positions.items()):
                            mdata = self.api.get_market_data(code)
                            if not mdata:
                                continue
                            cur   = safe_float(mdata.get("stck_prpr", 0))
                            entry = pos["entry_price"]
                            qty   = pos["qty"]
                            if entry == 0 or cur == 0:
                                continue
                            rate  = (cur - entry) / entry

                            if rate >= TAKE_PROFIT:
                                print(f"✅ 익절 {code} | {rate:+.2%}")
                                self._do_sell(code, qty, f"익절({rate:+.2%})", cur)
                            elif rate <= STOP_LOSS:
                                print(f"🛑 손절 {code} | {rate:+.2%}")
                                self._do_sell(code, qty, f"손절({rate:+.2%})", cur)
                            else:
                                print(f"  📊 {code}({self._name(code)}) | "
                                      f"{rate:+.2%} | 대기 중")

                # ── 종가매수 시간대 (15:10~15:20) ─────────────
                elif BUY_START_TIME <= now_t <= BUY_END_TIME:
                    if not self.bought_today and not self.positions:
                        print(f"🌆 종가매수 시간! ({now_t})")

                        if not self.kiwoom.enabled:
                            print("⚠️ 키움 없음 — 종목 선정 불가")
                        else:
                            loop  = asyncio.new_event_loop()
                            codes = loop.run_until_complete(self._get_eod_codes())
                            loop.close()

                            if not codes:
                                print("⚠️ 종가매매 조건검색 결과 없음")
                            else:
                                print(f"  📋 종가매매 후보: {len(codes)}개")
                                best_code, best_data = self._select_best(codes)
                                if best_code and best_data:
                                    cur = safe_float(best_data.get("stck_prpr", 0))
                                    if cur > 0:
                                        self._do_buy(best_code, cur)
                                    else:
                                        print(f"⚠️ {best_code} 현재가 없음")
                                else:
                                    print("⚠️ 적합한 종목 없음")
                    elif self.bought_today:
                        print("  ✅ 오늘 이미 매수 완료")
                    elif self.positions:
                        # 기보유 포지션 익절/손절 체크
                        for code, pos in list(self.positions.items()):
                            mdata = self.api.get_market_data(code)
                            if not mdata:
                                continue
                            cur   = safe_float(mdata.get("stck_prpr", 0))
                            entry = pos["entry_price"]
                            qty   = pos["qty"]
                            if entry == 0 or cur == 0:
                                continue
                            rate  = (cur - entry) / entry
                            print(f"  📊 {code}({self._name(code)}) | {rate:+.2%}")

                            if rate >= TAKE_PROFIT:
                                self._do_sell(code, qty, f"익절({rate:+.2%})", cur)
                            elif rate <= STOP_LOSS:
                                self._do_sell(code, qty, f"손절({rate:+.2%})", cur)

                # ── 그 외 시간 (대기) ──────────────────────────
                else:
                    if self.positions:
                        for code, pos in list(self.positions.items()):
                            mdata = self.api.get_market_data(code)
                            if not mdata:
                                continue
                            cur   = safe_float(mdata.get("stck_prpr", 0))
                            entry = pos["entry_price"]
                            if entry == 0 or cur == 0:
                                continue
                            rate  = (cur - entry) / entry
                            print(f"  📊 {code}({self._name(code)}) | "
                                  f"{rate:+.2%} | 대기 중")
                    else:
                        if now_t < BUY_START_TIME:
                            print(f"  ⏳ 종가매수 대기 ({BUY_START_TIME} 이후)")
                        elif now_t > BUY_END_TIME and not self.bought_today:
                            print("  😴 오늘 종가매수 없음")
                        else:
                            print("  😴 대기 중...")

                # ── 상태 저장 ───────────────────────────────
                today_profit = get_today_realized()
                _write_status({
                    "positions":     len(self.positions),
                    "bought_today":  self.bought_today,
                    "sold_today":    self.sold_today,
                    "today_profit":  today_profit,
                    "last_update":   now,
                    "code_name_map": self.code_name_map,
                    "positions_detail": {
                        code: {
                            "name":        pos["stock_name"],
                            "entry_price": int(pos["entry_price"]),
                            "qty":         pos["qty"],
                        }
                        for code, pos in self.positions.items()
                    },
                })

                time.sleep(SLEEP_INTERVAL)

            except KeyboardInterrupt:
                today_profit = get_today_realized()
                self._notify(
                    f"🛑 [EOD] 봇 종료\n"
                    f"⏰ {now_kst().strftime('%Y-%m-%d %H:%M:%S')}\n"
                    f"💰 오늘 실현손익: {today_profit:+,}원",
                    critical=True,
                )
                break
            except Exception as e:
                print(f"🚨 [EOD] 루프 오류: {e}")
                import traceback; traceback.print_exc()
                time.sleep(5)


# ============================================================
# 진입점
# ============================================================
if __name__ == "__main__":
    EBot().run()
