"""
daybot.py — 영암9 단타봇 (당일청산 회전매매)
================================================================
[이 파일이 하는 일 — 비개발자용 설명]

sbot/sbo2(스윙, 며칠~1주일 보유)와 달리, daybot은 하루 안에 사고 파는
순수 단타봇입니다.
- 대상: 키움 조건검색 3개(주도주검색식3/단타000/090930타점 시가이탈 오전중저가이탈)
  중 2개 이상 겹친 종목 우선
- 매수조건: 위 조건검색 통과 + 당일 등락률 양수(주도주검색식3은 하락
  종목도 걸릴 수 있어 제외) + 호가창 매도잔량이 매수잔량의 3배 이상
  ("눌린 스프링")일 때만 매수 진행
- 매수금액: 1종목당 150만원+, 기본 2종목 동시보유(매수가능금액 50만원+면 3번째 보너스슬롯)
- 매도기준: 손절 -3.5% 고정, 익절은 +2.5% 도달시 즉시매도 대신 트레일링
  스탑 전환(급등주는 10%+ 가는 경우가 많아서) — 고점 대비 -2% 밀리면 매도
- 매매시간: 08:00(프리장)~19:30(매수마감), 19:50부터 무조건 전량 강제청산
- 보유기간: 당일청산 원칙 — 단, 하한가/거래정지 등으로 19:50 강제청산이
  실패한 종목은 그날 밤 재시도하지 않고 익일 09:00에 딱 한 번 더 시도

[아키텍처 — 키움 스크리닝 + KIS 실행 하이브리드]
키움은 조건검색(스크리닝) 전용으로만 사용 — 주문/체결통보 기능이
전혀 없어(조사로 확인됨) 매수/매도/실시간감시는 전부 KIS(한투)로 한다.
실행계좌는 sbo2가 쓰던 계좌(무접미사 KIS_* 환경변수)를 재사용 —
이 계좌엔 매도 안 한 대원전선(006340)이 그대로 남아있으니 daybot은
이 종목을 절대 건드리지 않는다(자기가 산 종목만 self.positions로
추적, 계좌 전체 보유종목과 절대 혼동 금지).

[모듈 구조]
  daybot.py        ← 메인 루프 (이 파일)
  kis_api.py       ← 한투 API (매수/매도/취소, sbot과 동일 모듈)
  kis_websocket.py ← 실시간 체결통보(H0STCNI0)+체결가(H0STCNT0, 신규)
  kiwoom_api.py    ← 조건검색 전용(get_condition_codes)
  daybot_db.py     ← 매매이력 DB
  common_utils.py  ← 공통 헬퍼
================================================================
"""
import sys as _sys
import os as _os
_BASE = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
for _d in ["core", "intelligence", "interface", "bots", ""]:
    _p = _os.path.join(_BASE, _d)
    if _p not in _sys.path:
        _sys.path.insert(0, _p)

import os
import time
import json
import asyncio
import pathlib
import datetime
import threading
from dotenv import load_dotenv

HB_FILE = "/tmp/hb_daybot"

from common_utils import (
    now_hhmm, now_hms, today_str, is_weekend,
    read_state, write_state,
)
from kis_api import KisAPI
from kis_websocket import KisWebSocket
from kiwoom_api import KiwoomAPI
from notifier import Notifier
from daybot_db import DayTradeDB

load_dotenv(_os.path.join(_BASE, ".env"))

try:
    from master_db import (
        record_trade    as _master_record,
        upsert_position as _master_upsert,
        remove_position as _master_remove,
        get_all_positions,
    )
except Exception:
    _master_record = None
    _master_upsert = None
    _master_remove = None
    get_all_positions = None
    print("⚠️ master_db 없음 → 크로스보유가드/대시보드 연동 비활성")


# ============================================================
# 상수
# ============================================================
BOT_STATE_FILE = "daybot_state.json"

BASE_MAX_POSITIONS = 2                # 기본 2종목 몰빵회전
# ★ 2026-10-01 대장 지정 — 매수가능금액이 이 이상이면 3번째 보너스슬롯
#   오픈(sbot/sbo2의 BONUS_SLOT_MIN_CASH와 동일 컨셉 — 여유자금 놀리지
#   않기). MAX_POSITIONS는 보너스 포함 상한값 — 메인루프의 "스캔을
#   시작할지" 판단에 쓰고, 실제 슬롯이 3개까지 열리는지는 매 스캔 시점
#   _run_candidate_scan_and_maybe_buy()가 실시간 잔고로 다시 판단한다.
BONUS_SLOT_MIN_CASH = 500_000
MAX_POSITIONS = BASE_MAX_POSITIONS + 1
BUY_AMT_PER_SLOT = 1_500_000          # ★ 2026-10-01 대장 지정 — 단타계좌 자본이 대원전선
                                       # 매도(손실처리) 후 약 330만원으로 늘어나서 100만→150만
                                       # 상향(2슬롯×150만=300만, 여유 있게 들어감). 부족하면
                                       # kis_api.buy()가 자체적으로 최소1주까지 축소시도.
TAKE_PROFIT_PCT  = 2.5                # 익절 +2~3% 중간값
STOP_LOSS_PCT    = -3.5               # 손절 -3~4% 중간값
# ★ 2026-09-29 밤 대장 지정 — 급등주 특성상 오르면 10%+ 가는 경우가
#   많아서, +2.5% 도달해도 바로 전량매도하지 말고 트레일링스탑으로
#   전환해서 더 큰 상승을 노림. 손절(-3.5%)은 트레일링 전환 전까지만
#   유효 — 일단 +2.5%를 찍고 나면 트레일링(고점대비 하락폭)만으로 매도
#   판단(원 손절선은 그 시점부터 현재가보다 한참 아래라 실질적으로
#   트레일링이 항상 더 타이트해서 자연스럽게 대체됨).
TRAILING_STOP_PCT = 2.0                # 고점 대비 이만큼 밀리면 매도("여유있게")

# ★ 2026-09-29 대장 지정 — 한투 애프터마켓 개편(09-14, 20시까지 정규장과
#   동일 실시간매칭) 반영해 매수시간을 08:00(프리장)~19:30까지 확장.
#   kis_api.py의 buy()/sell()이 08:00~09:00 구간은 ORD_DVSN=62(시간외단일가),
#   09:00~20:00은 00/01(정규장)로 이미 내부 분기하므로 daybot은 신경쓸 필요 없음.
SESSION_START    = "0800"             # 이 시간대 밖이면 루프 자체를 idle
SESSION_END      = "1950"
BUY_START_TIME   = "0800"
BUY_END_TIME     = "1930"             # 19:30 이후 신규매수 중단 — EOD청산 전 버퍼
FORCE_EOD_TIME   = "1950"             # 19:50부터 무조건 전량강제청산(최우선, 하루 1회만 시도)
CARRYOVER_RETRY_TIME = "0900"         # 전날 19:50 강제청산 실패분 — 익일 이 시각부터 최우선 재시도

HOGA_ASK_BID_RATIO_MIN = 3.0          # ★ 대장 지정 — 매도잔량이 매수잔량의 3배 이상("눌린
                                       #   스프링", core/kis_api.py:get_hoga() 자체 docstring
                                       #   표현)일 때만 매수 진행. 미달이면 스킵.

SCAN_INTERVAL_SEC = 240               # 조건검색 풀사이클(3개조건) 주기 — 65초 재시도
                                       # 백오프까지 감안한 안전마진(core/kiwoom_api.py 참고)
LOOP_SLEEP_SEC     = 5                # 포지션감시/EOD체크용 빠른 루프 틱
PENDING_ORDER_TIMEOUT_SEC = 30        # 미체결 주문 취소 판단 기준(daybot 5초루프 기준 조정값)
MIN_ANALYSIS_CASH  = 200_000          # 이 밑이면 스캔 자체 스킵(API 낭비 방지)

# ★ 2026-10-01 대장 지정 — 대장이 daybot 보유종목을 HTS/MTS로 직접 매도할
#   계획이라("서진하고 대원은 내가 프리장에서 팔면 팔거야") sbot의 수동매도
#   감지 패턴을 이식. REST로 실계좌와 대조(매루프 5초마다 하면 API 낭비).
#   매수직후엔 get_current_positions()의 60초 캐시(core/kis_api.py) 때문에
#   실계좌에 아직 안 잡혀 수동매도로 오판할 수 있어 매수 후 이 시간 동안은
#   검사 제외(sbot의 BUY_SYNC_GUARD_SEC와 동일 취지).
#   ★ 2026-10-01 실거래 중 "초당 거래건수 초과"(잔고조회 API) 발생 —
#   60초 주기가 get_current_positions()의 60초 캐시 경계와 거의 맞물려
#   매번 실제 API를 때리고 있었고, 대장의 동시 HTS/MTS 수동거래까지
#   겹치면 같은 계좌의 초당 한도를 넘기기 쉬움(대장 지적 — "여유를
#   줘야하지 않을까"). 120초로 늘려 호출 빈도 자체를 줄임.
MANUAL_SELL_CHECK_INTERVAL_SEC = 120
BUY_SYNC_GUARD_SEC = 90

# ★ 2026-10-01 대장 지적 — "장개장직후 종목찾기"가 아니라 "090930타점
#   시가이탈 오전중저가이탈"이 맞는 검색식이었음(실제 수동단타에서 쓰던
#   조건). use_keywords는 부분일치라 "090930타점"만 있으면 되지만, 이
#   밑 _rank_candidates()에서 code_multi_tag_map에 기록되는 값은 매칭된
#   조건식의 전체 이름이라 그쪽은 전체 이름으로 비교해야 함.
CONDITION_KEYWORDS = ["주도주검색식3", "단타000", "090930타점"]
COND_090930 = "090930타점 시가이탈 오전중저가이탈"
# ★ "5본봉거래대금단타"는 대장이 수동단타에서 안 쓰던 검색식이라 제외

SCOUT_CANDIDATES_PATH = _os.path.join(_BASE, "intelligence", "day_trade_scout_candidates.json")
SCOUT_STALE_SEC = 7200   # day_trade_scout.py 결과가 이 이상 오래되면 3순위 fallback에서 제외


def _read_state() -> dict:
    return read_state(BOT_STATE_FILE, default={})


class DayBot:

    def __init__(self):
        self.api = KisAPI(
            appkey=os.getenv("KIS_APPKEY"),
            secret=os.getenv("KIS_SECRET"),
            cano  =os.getenv("KIS_CANO"),
            acnt  =os.getenv("KIS_ACNT_PRDT_CD"),
        )   # ★ sbo2가 쓰던 계좌(무접미사) 재사용 — sbot(...2 접미사)과 다른 계좌
        self.notifier = Notifier(name="daybot")
        self.db       = DayTradeDB()
        self.db.init_db()

        # daybot 자신이 산 종목만 추적 — 절대 계좌 전체 보유종목과 혼동 금지
        # (이 계좌엔 매도 안 한 대원전선이 남아있음)
        self.positions: dict     = {}   # {code: {entry_price, qty, buy_time, source_tier, buy_tag}}
        self.code_name_map: dict = {}
        self.sold_today: dict    = {}   # {code: sell_hms} — 당일 재매수 방지
        self._sold_today_date    = ""
        self._pending_orders: dict = {}  # {code: (orgno, odno, qty, placed_ts)}

        self._is_holiday      = False
        self._holiday_checked = ""
        self._last_scan_ts    = 0.0
        self._last_manual_check_ts = 0.0
        self._is_paused       = False
        # ★ 2026-09-30 발견 — 키움 조건검색이 3개 조건 전부 타임아웃나면
        #   최악의 경우(조건당 최대 2회 재시도×65초, core/kiwoom_api.py)
        #   8분 가까이 걸릴 수 있는데, 이게 메인루프 안에서 동기 실행되고
        #   있어서 그동안 heartbeat가 안 찍혀 워치독이 5분마다 daybot을
        #   계속 재시작시키고 있었음(실측: 11:46~15:37 사이 수십 차례).
        #   스캔을 백그라운드 스레드로 분리해서 메인루프(heartbeat/포지션
        #   감시/EOD청산)가 스캔 소요시간과 무관하게 계속 돌게 한다.
        self._scan_thread: threading.Thread = None
        # ★ 스캔이 백그라운드 스레드로 도는 이상, self.positions에 키를
        #   추가/삭제하는 지점(_do_buy/_do_sell)과 그걸 통째로 직렬화하는
        #   _save_state()가 동시에 돌면 "dictionary changed size during
        #   iteration"로 죽을 수 있어 락으로 보호.
        self._positions_lock = threading.Lock()

        # ★ 2026-09-29 대장 지정 — 19:50 EOD청산 실패분(하한가/거래정지 등)은
        #   그날 밤 내내 재시도하지 않고 익일 09:00에 딱 한 번 더 시도.
        self._eod_closed_date       = ""   # 오늘자 19:50 EOD시도를 이미 했는지
        self._carryover_codes: set  = set()  # 전날 EOD청산 실패해서 넘어온 종목
        self._carryover_retried_date = ""   # 오늘자 09:00 이월재시도를 이미 했는지

        self.kiwoom = KiwoomAPI()

        # KIS 웹소켓 — 체결통보(H0STCNI0, 기존) + 실시간 체결가(H0STCNT0, 신규, opt-in)
        self._ws = KisWebSocket(
            appkey=os.getenv("KIS_APPKEY"),
            secret=os.getenv("KIS_SECRET"),
            cano  =os.getenv("KIS_CANO"),
            acnt  =os.getenv("KIS_ACNT_PRDT_CD"),
        )
        self._ws.start()

    # ============================================================
    # 알림
    # ============================================================
    def _notify(self, msg: str, critical: bool = False):
        self.notifier.send(msg, critical=critical)

    def _name(self, code: str) -> str:
        return self.code_name_map.get(code, code)

    # ============================================================
    # 상태 저장/복구
    # ============================================================
    def _save_state(self):
        with self._positions_lock:
            positions_snapshot = dict(self.positions)   # 얕은 복사 — json 직렬화 중
                                                          # 스캔스레드가 키 추가/삭제해도 안전
        write_state(BOT_STATE_FILE, {
            "positions":        positions_snapshot,
            "sold_today":       self.sold_today,
            "sold_today_date":  self._sold_today_date,
            "code_name_map":    self.code_name_map,
            "eod_closed_date":         self._eod_closed_date,
            "carryover_codes":         list(self._carryover_codes),
            "carryover_retried_date":  self._carryover_retried_date,
            "last_update":      now_hms(),
        })

    def _restore_state(self):
        """재시작 시 daybot 자신의 상태를 복구하고 실계좌와 교차확인.
        ★ 대원전선처럼 daybot이 모르는 종목은 절대 자동입양하지 않는다
        (core/account_sync.py::sync_positions()는 계좌의 모든 미인식
        종목을 자동으로 흡수하는 구조라 여기선 재사용 금지). 이월종목
        추적(_carryover_codes 등)도 워치독 재시작에도 살아남도록 같이 복구."""
        saved = _read_state()
        self.positions       = saved.get("positions", {})
        self.sold_today      = saved.get("sold_today", {})
        self._sold_today_date = saved.get("sold_today_date", today_str())
        self.code_name_map.update(saved.get("code_name_map", {}))
        self._eod_closed_date        = saved.get("eod_closed_date", "")
        self._carryover_codes        = set(saved.get("carryover_codes", []))
        self._carryover_retried_date = saved.get("carryover_retried_date", "")

        if self._sold_today_date != today_str():
            self.sold_today = {}
            self._sold_today_date = today_str()

        real_pos = self.api.get_current_positions()
        if real_pos is None:
            print("⚠️ 재시작 시 실계좌 조회 실패 — 저장된 상태 그대로 신뢰")
        else:
            for code in list(self.positions.keys()):
                if code not in real_pos:
                    self._notify(f"⚠️ {code} 재시작 시 계좌에 없음 — 포지션 제거", critical=False)
                    self.positions.pop(code, None)
                else:
                    self.positions[code]["qty"] = real_pos[code]["qty"]

            unknown = set(real_pos.keys()) - set(self.positions.keys())
            if unknown:
                print(f"ℹ️ 계좌 내 daybot 소관 외 종목 (건드리지 않음): {unknown}")

        for code in self.positions:
            self._ws.subscribe_price(code)

        print(f"📦 [daybot] 상태복구 완료 — 보유 {len(self.positions)}종목")

    # ============================================================
    # 가격 조회 (실시간 우선, REST 폴백)
    # ============================================================
    def _get_current_price(self, code: str) -> float:
        tick = self._ws.live_prices.get(code)
        if tick and time.time() - tick["ts"] < 30:
            return tick["price"]
        mdata = self.api.get_market_data(code) or {}
        try:
            return float(mdata.get("stck_prpr", 0) or 0)
        except (TypeError, ValueError):
            return 0.0

    # ============================================================
    # 매도 판단 — 고정% 손절 + (+2.5% 도달 후) 트레일링스탑
    # ============================================================
    def _check_all_positions_for_exit(self):
        """★ 2026-09-29 밤 대장 지정 — 급등주는 오르면 10%+ 가는 경우가
        많아 +2.5% 찍었다고 바로 전량매도하지 않고 트레일링모드로
        전환한다. 트레일링 진입 전까지는 기존처럼 고정 손절(-3.5%)만
        체크, 진입 후엔 고점(peak_price) 대비 TRAILING_STOP_PCT(2%)
        하락시에만 매도 — 원 손절선은 진입 시점부터 현재가보다 한참
        아래라 트레일링이 항상 먼저 걸리므로 별도 분기 불필요."""
        for code in list(self.positions.keys()):
            pos     = self.positions[code]
            current = self._get_current_price(code)
            if current <= 0:
                continue
            entry = pos["entry_price"]
            rate  = (current - entry) / entry * 100 if entry > 0 else 0

            if pos.get("peak_price") is not None:
                if current > pos["peak_price"]:
                    pos["peak_price"] = current
                trail_stop = pos["peak_price"] * (1 - TRAILING_STOP_PCT / 100)
                if current <= trail_stop:
                    self._do_sell(code, pos["qty"],
                                  f"트레일링청산(고점{pos['peak_price']:,.0f}대비"
                                  f"-{TRAILING_STOP_PCT:.1f}%, 총{rate:+.2f}%)", current)
                continue

            if rate >= TAKE_PROFIT_PCT:
                pos["peak_price"] = current
                print(f"📈 [daybot] {code} +{rate:.2f}% 도달 — 트레일링 모드 전환 "
                      f"(고점:{current:,.0f}, -{TRAILING_STOP_PCT:.1f}% 밀리면 매도)")
                continue
            if rate <= STOP_LOSS_PCT:
                self._do_sell(code, pos["qty"], f"손절({rate:.2f}%)", current)

    def _force_close_all(self, reason: str) -> set:
        """전량 강제청산 시도. 반환값은 매도 실패해서 여전히 self.positions에
        남아있는 종목 코드 집합(하한가/거래정지 등) — 호출부가 이월처리
        여부를 판단할 수 있게 함."""
        for code, pos in list(self.positions.items()):
            current = self._get_current_price(code) or pos["entry_price"]
            self._do_sell(code, pos["qty"], reason, current)
        failed = set(self.positions.keys())
        if failed:
            self._notify(f"⚠️ [daybot] {reason} — {len(failed)}종목 매도 실패({failed}), "
                         f"익일 {CARRYOVER_RETRY_TIME[:2]}:{CARRYOVER_RETRY_TIME[2:]} 재시도",
                         critical=True)
        else:
            self._notify(f"🔔 [daybot] {reason} — 전량 청산 완료", critical=True)
        return failed

    # ============================================================
    # 매수/매도 실행
    # ============================================================
    def _do_buy(self, code: str, name: str, price: float, source_tier: str):
        psbl_cash = self.api.get_psbl_order_cash(code, price)
        if psbl_cash < MIN_ANALYSIS_CASH:
            return False
        amount = min(BUY_AMT_PER_SLOT, psbl_cash)
        ok, orgno, odno, qty = self.api.buy(
            code, price, amount, code_name_map=self.code_name_map,
            psbl_cash=psbl_cash,
        )
        if not ok or qty <= 0:
            return False

        now = now_hms()
        with self._positions_lock:
            self.positions[code] = {
                "entry_price": price, "qty": qty, "buy_time": now,
                "source_tier": source_tier, "buy_tag": source_tier,
                "peak_price": None,   # +2.5% 도달 전까지는 None(트레일링 미활성)
                "buy_ts": time.time(),  # ★ 수동매도 오탐 방지 가드용(아래 _check_manual_sells)
            }
        self._pending_orders[code] = (orgno, odno, qty, time.time())
        self.code_name_map[code] = name
        self._ws.subscribe_price(code)

        self.db.save_buy(code, price, qty, stock_name=name, buy_tag=source_tier)
        if _master_upsert:
            _master_upsert(bot_type="daybot", code=code, stock_name=name,
                            entry_price=price, current_price=price, qty=qty,
                            buy_time=now, buy_tag=source_tier)

        self._notify(f"🚀 [daybot] 매수 {code}({name}) | {qty}주 @{price:,.0f}원 | [{source_tier}]")
        print(f"🚀 [daybot] 매수 {code}({name}) | {qty}주 @{price:,.0f}원 | [{source_tier}]")
        return True

    def _do_sell(self, code: str, qty: int, reason: str, price: float):
        name = self._name(code)
        ok = self.api.sell(code, qty)
        if not ok:
            print(f"⚠️ [daybot] 매도 실패 {code} — 다음 루프 재시도")
            return

        self.db.save_sell(code, price, reason)
        if _master_record:
            pos = self.positions.get(code, {})
            _master_record(bot_type="daybot", code=code, stock_name=name,
                            buy_price=pos.get("entry_price", price),
                            sell_price=price, qty=qty, sell_reason=reason,
                            buy_tag=pos.get("buy_tag", ""))
        if _master_remove:
            _master_remove("daybot", code)

        with self._positions_lock:
            self.positions.pop(code, None)
        self._pending_orders.pop(code, None)
        self.sold_today[code] = now_hms()
        self._ws.unsubscribe_price(code)

        self._notify(f"✅ [daybot] 매도 {code}({name}) | {reason} @{price:,.0f}원")
        print(f"✅ [daybot] 매도 {code}({name}) | {reason} @{price:,.0f}원")

    def _check_pending_orders(self):
        """미체결 주문이 PENDING_ORDER_TIMEOUT_SEC 이상 지나면 취소 시도.
        취소 성공(=진짜 미체결) → 포지션/pending 정리, 실패(=이미 체결됨)
        → pending만 정리(포지션은 정상 유지). 부분체결 대비 웹소켓
        체결통보(H0STCNI0)가 알고 있는 실제 체결수량으로 qty 보정
        (ws.positions는 코드별 dict라 daybot이 산 종목 조회는 대원전선과
        섞일 위험 없음 — "모르는 종목 자동입양" 문제와는 다른 얘기)."""
        now_ts = time.time()
        for code, (orgno, odno, qty, placed_ts) in list(self._pending_orders.items()):
            if now_ts - placed_ts < PENDING_ORDER_TIMEOUT_SEC:
                continue
            if not odno:
                self._pending_orders.pop(code, None)
                continue
            ok = self.api.cancel_order(orgno, odno, code, qty)
            if ok:
                print(f"🚫 [daybot] 미체결 취소: {code}")
                with self._positions_lock:
                    self.positions.pop(code, None)
                self._ws.unsubscribe_price(code)
                # ★ 2026-09-30: 취소된 매수는 실제 거래가 아니므로 DB/
                #   master_db 기록도 같이 정리(0035S0 유령거래 실사례)
                self.db.void_buy(code)
                if _master_remove:
                    _master_remove("daybot", code)
            else:
                real_qty = self._ws.positions.get(code, {}).get("qty")
                if real_qty and code in self.positions:
                    self.positions[code]["qty"] = real_qty
            self._pending_orders.pop(code, None)

    def _check_manual_sells(self):
        """★ 2026-10-01 대장 지정 — 대장이 HTS/MTS로 daybot 보유종목을
        직접 매도할 계획이라 sbot의 수동매도 감지 패턴을 이식(bots/sbot.py
        참고). daybot이 추적 중인 포지션이 실계좌에서 사라졌으면 수동매도로
        간주 — DB/master_db 정리 + 재매수 허용(sold_today 등록 안 함).
        60초 주기로만 호출(매루프 5초마다 하면 REST 낭비)."""
        if not self.positions:
            return
        real_pos = self.api.get_current_positions()
        if real_pos is None:
            return  # API 실패 — 다음 체크에서 재시도, 기존 상태 유지

        now_ts = time.time()
        manual_sold = []
        for code in list(self.positions.keys()):
            if code in real_pos:
                continue
            buy_ts = self.positions[code].get("buy_ts", 0)
            if now_ts - buy_ts < BUY_SYNC_GUARD_SEC:
                continue  # 매수직후 — 실계좌 반영 지연일 수 있어 스킵
            manual_sold.append(code)
        if not manual_sold:
            return

        today_ymd = datetime.datetime.now().strftime("%Y%m%d")
        profit_rows = {}
        try:
            pdata = self.api.get_period_trade_profit(today_ymd, today_ymd)
            profit_rows = {r["pdno"]: r for r in pdata.get("trades", [])}
        except Exception:
            pass

        for code in manual_sold:
            print(f"🔍 [daybot] 수동매도 감지: {code} → 재매수 허용")
            old_pos = self.positions.get(code, {})
            name = self._name(code)
            row = profit_rows.get(code)
            if row and int(row.get("sll_qty", 0) or 0) > 0:
                sell_price = float(row.get("sll_pric", 0) or 0)
                buy_price  = float(row.get("pchs_unpr", 0) or 0) or old_pos.get("entry_price", 0)
                sell_qty   = int(row.get("sll_qty", 0) or 0) or old_pos.get("qty", 0)
            else:
                mdata = self.api.get_market_data(code) or {}
                try:
                    sell_price = float(mdata.get("stck_prpr", 0) or 0)
                except (TypeError, ValueError):
                    sell_price = old_pos.get("entry_price", 0)
                buy_price = old_pos.get("entry_price", 0)
                sell_qty  = old_pos.get("qty", 0)

            self.db.save_manual_trade(code, name, buy_price, sell_price, sell_qty,
                                       "수동매도", buy_tag=old_pos.get("buy_tag", "수동"))
            if _master_remove:
                _master_remove("daybot", code)

            with self._positions_lock:
                self.positions.pop(code, None)
            self._pending_orders.pop(code, None)
            self._ws.unsubscribe_price(code)
            # ★ 수동매도는 sold_today에 등록 안 함 — 같은날 재매수 허용(sbot과 동일 정책)

    # ============================================================
    # 종목소스 — 키움 조건검색 스캔 + 우선순위 워터폴
    # ============================================================
    def _scan_conditions(self):
        """core/kiwoom_api.py의 get_condition_codes()를 그대로 재사용
        (재구현 금지 — code_multi_tag_map이 이미 겹침추적 해줌)."""
        if not self.kiwoom.enabled:
            return [], {}
        code_name_map, code_multi_tag_map = {}, {}
        loop = asyncio.new_event_loop()
        try:
            codes = loop.run_until_complete(
                self.kiwoom.get_condition_codes(
                    use_keywords=CONDITION_KEYWORDS,
                    code_name_map=code_name_map,
                    code_multi_tag_map=code_multi_tag_map,
                )
            )
        except Exception as e:
            print(f"⚠️ [daybot] 조건검색 오류: {e}")
            self.kiwoom.reset_token()
            codes = []
        finally:
            loop.close()
        self.code_name_map.update(code_name_map)
        return codes, code_multi_tag_map

    def _load_scout_tier3_picks(self) -> list:
        """day_trade_scout.py가 저장한 공유후보 JSON에서 3순위 fallback
        코드 목록을 읽는다. 오래됐으면(2시간+) 빈 리스트."""
        try:
            with open(SCOUT_CANDIDATES_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            updated = datetime.datetime.fromisoformat(data["updated_at"])
            if (datetime.datetime.now() - updated).total_seconds() > SCOUT_STALE_SEC:
                return []
            for c in data.get("candidates", []):
                self.code_name_map.setdefault(c["code"], c["name"])
            return [c["code"] for c in data.get("candidates", [])]
        except Exception:
            return []

    def _rank_candidates(self, codes: list, code_multi_tag_map: dict):
        """1순위 겹침종목 → 2순위 단타000단독 → 3순위 나머지단독+scout fallback.
        ★ 2026-09-30 대장 지적 — 매수 후 어느 출처에서 나왔는지 구분해야
        나중에 분석하기 쉬움. 기존엔 3순위를 전부 "tier3_fallback"으로
        뭉뚱그렸는데(오늘 실거래 4건이 전부 이 라벨이라 뭐가 실제로
        잘 먹히는지 알 수 없었음), 주도주검색식3단독/장개장직후단독/
        scout후보를 별도 라벨로 분리. 반환값: (순위리스트, {code: source_label})."""
        tier1, tier2, tier3 = [], [], []
        source_label = {}
        for code in codes:
            tags = code_multi_tag_map.get(code, [])
            if len(tags) >= 2:
                tier1.append(code)
                source_label[code] = "tier1_overlap"
            elif tags == ["단타000"]:
                tier2.append(code)
                source_label[code] = "tier2_danta000"
            elif tags == ["주도주검색식3"]:
                tier3.append(code)
                source_label[code] = "tier3_주도주검색식3"
            elif tags == [COND_090930]:
                tier3.append(code)
                source_label[code] = "tier3_090930타점"
            elif tags:
                tier3.append(code)
                source_label[code] = "tier3_기타"

        for code in self._load_scout_tier3_picks():
            if code not in tier1 and code not in tier2 and code not in tier3:
                tier3.append(code)
                source_label[code] = "tier3_scout"

        return tier1 + tier2 + tier3, source_label

    def _run_candidate_scan_and_maybe_buy(self):
        codes, code_multi_tag_map = self._scan_conditions()
        if not codes:
            return
        ranked, source_label = self._rank_candidates(codes, code_multi_tag_map)
        if not ranked:
            return

        held_elsewhere = set()
        if get_all_positions:
            try:
                held_elsewhere = {p["code"] for p in get_all_positions()
                                   if p["bot_type"] != "daybot"}
            except Exception:
                pass

        # ★ 2026-10-01 대장 지정 — 기본 2슬롯 꽉 찼을 때만 매수가능금액을
        #   확인해서 3번째 보너스슬롯 오픈 여부 판단(매 스캔마다 1회, REST
        #   호출 최소화 — 대표종목 005930 기준으로 계좌 전체 여력 조회).
        effective_max = BASE_MAX_POSITIONS
        if len(self.positions) >= BASE_MAX_POSITIONS:
            psbl = self.api.get_psbl_order_cash("005930") or 0
            if psbl >= BONUS_SLOT_MIN_CASH:
                effective_max = BASE_MAX_POSITIONS + 1

        for code in ranked:
            if len(self.positions) >= effective_max:
                break
            if code in self.positions or code in self.sold_today:
                continue
            if code in held_elsewhere:
                continue
            # ★ 2026-09-30 대장 지정 — 주도주검색식3은 거래대금 등 기준이라
            #   당일 하락 중인 종목도 걸릴 수 있음. daybot은 급등주 추종
            #   전략이라 등락률이 음수/보합인 후보는 애초에 제외.
            mdata = self.api.get_market_data(code) or {}
            try:
                price = float(mdata.get("stck_prpr", 0) or 0)
                chg   = float(mdata.get("prdy_ctrt", 0) or 0)
            except (TypeError, ValueError):
                continue
            if price <= 0:
                continue
            if chg <= 0:
                print(f"⏭️ [daybot] {code} 패스 — 등락률 {chg:+.2f}% (하락/보합)")
                continue
            # ★ 2026-09-29 대장 지정 — 매도잔량이 매수잔량의 3배 이상
            #   ("눌린 스프링", core/kis_api.py:get_hoga() 자체 표현)일
            #   때만 매수 진행. get_hoga()는 이미 있던 기존 메서드 재사용
            #   (sbot이 AI참고용으로만 쓰던 걸 daybot은 매수게이트로 사용).
            hoga = self.api.get_hoga(code) or {}
            if hoga.get("ask_bid_ratio", 0) < HOGA_ASK_BID_RATIO_MIN:
                print(f"⏭️ [daybot] {code} 패스 — 매도/매수잔량비 "
                      f"{hoga.get('ask_bid_ratio', 0):.2f} < {HOGA_ASK_BID_RATIO_MIN}")
                continue
            tier = source_label.get(code, "unknown")
            self._do_buy(code, self._name(code), price, tier)

    # ============================================================
    # 메인 루프
    # ============================================================
    def run(self):
        self._notify("🚀 [DAYBOT] 단타봇 가동", critical=True)
        print(f"🚀 [DAYBOT] 단타봇 가동 | 기본 {BASE_MAX_POSITIONS}종목"
              f"(+매수가능금액 {BONUS_SLOT_MIN_CASH:,}원 이상시 1종목 보너스) | "
              f"익절+{TAKE_PROFIT_PCT}% 손절{STOP_LOSS_PCT}% | EOD청산 {FORCE_EOD_TIME}")
        self._restore_state()

        while True:
            try:
                today = today_str()
                now_t = now_hhmm()

                # 1) heartbeat — 항상 최우선(continue 게이트보다 앞)
                pathlib.Path(HB_FILE).touch()

                # 2) 토큰 갱신 — 역시 continue 게이트보다 앞
                self.api.refresh_token_if_needed()

                # 3) 주말
                if is_weekend():
                    time.sleep(300); continue

                # 4) 휴장일 (None-safe — 판단불가면 캐시 안 하고 다음 루프 재시도)
                if self._holiday_checked != today:
                    _open = self.api.is_market_open()
                    if _open is not None:
                        self._is_holiday = not _open
                        self._holiday_checked = today
                if self._is_holiday:
                    time.sleep(300); continue

                # 4-1) 키키 !daybot정지/!daybot시작 반영 — sbot과 동일 패턴:
                #      정지돼도 보유종목 매도체크/EOD청산/이월재시도는 계속
                #      돌고, 신규매수(11번)만 멈춘다.
                self._is_paused = _read_state().get("paused", False)

                # 5) 일일 초기화 — 새 날이면 당일 관련 플래그 전부 리셋
                if today != self._sold_today_date:
                    self.sold_today = {}
                    self._sold_today_date = today
                    self._eod_closed_date = ""
                    self._carryover_retried_date = ""

                # 6) EOD 강제청산 — 19:50부터, 오늘 아직 시도 안 했으면 딱 1회.
                #    실패분(하한가/거래정지 등)은 _carryover_codes에 남겨
                #    그날 밤 내내 재시도하지 않고 익일 09:00으로 넘긴다.
                if (now_t >= FORCE_EOD_TIME and self._eod_closed_date != today
                        and self.positions):
                    self._carryover_codes = self._force_close_all("EOD 강제청산")
                    self._eod_closed_date = today
                    self._save_state()
                    time.sleep(LOOP_SLEEP_SEC); continue

                # 7) 세션 외 시간 (19:50~다음날 08:00)
                if not (SESSION_START <= now_t <= SESSION_END):
                    time.sleep(60); continue

                # 8) 이월종목 익일 09:00 최우선 재시도(하루 1회) — 신규매수보다 먼저.
                #    08:00~09:00 사이 가격이 자연스레 +2.5%/-3.5%에 걸려 이미
                #    정상매도됐으면 still_open이 비어있어 아무 일도 안 함.
                if (self._carryover_codes and now_t >= CARRYOVER_RETRY_TIME
                        and self._carryover_retried_date != today):
                    still_open = self._carryover_codes & set(self.positions.keys())
                    for code in still_open:
                        pos = self.positions[code]
                        current = self._get_current_price(code) or pos["entry_price"]
                        self._do_sell(code, pos["qty"], "이월종목 익일청산", current)
                    self._carryover_codes = set()
                    self._carryover_retried_date = today
                    self._save_state()

                # 9) 미체결 주문 정리
                self._check_pending_orders()

                # 9-1) 수동매도 감지(60초 주기) — 대장이 HTS/MTS로 직접
                #      매도할 계획이라 daybot이 좀비 포지션을 안 만들게
                if time.time() - self._last_manual_check_ts >= MANUAL_SELL_CHECK_INTERVAL_SEC:
                    self._check_manual_sells()
                    self._last_manual_check_ts = time.time()

                # 10) 포지션 실시간감시
                self._check_all_positions_for_exit()

                # 11) 후보스캔(240초 주기, 슬롯 여유+매수시간대일 때만, 정지중이면 스킵)
                #     ★ 백그라운드 스레드로 실행 — 키움 조건검색이 타임아웃/
                #     재시도로 몇 분씩 걸려도 메인루프(heartbeat/포지션감시/
                #     EOD청산)는 계속 돈다. 이전 스캔이 아직 안 끝났으면
                #     새로 안 띄움(중복실행 방지).
                if (not self._is_paused
                        and len(self.positions) < MAX_POSITIONS
                        and BUY_START_TIME <= now_t <= BUY_END_TIME
                        and time.time() - self._last_scan_ts >= SCAN_INTERVAL_SEC
                        and (self._scan_thread is None or not self._scan_thread.is_alive())):
                    self._last_scan_ts = time.time()
                    self._scan_thread = threading.Thread(
                        target=self._run_candidate_scan_and_maybe_buy, daemon=True)
                    self._scan_thread.start()

                # 12) 상태 저장
                self._save_state()

                time.sleep(LOOP_SLEEP_SEC)

            except Exception as e:
                print(f"⚠️ [daybot] 루프 오류: {e}")
                time.sleep(10)


def main():
    bot = DayBot()
    bot.run()


if __name__ == "__main__":
    main()
