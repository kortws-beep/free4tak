"""
daybot.py — 영암9 단타봇 (당일청산 회전매매)
================================================================
[이 파일이 하는 일 — 비개발자용 설명]

sbot/sbo2(스윙, 며칠~1주일 보유)와 달리, daybot은 하루 안에 사고 파는
순수 단타봇입니다.
- 대상: 키움 조건검색 3개(주도주검색식3/단타000/장개장직후 종목찾기)
  중 2개 이상 겹친 종목 우선
- 매수조건: 위 조건검색 통과 + 당일 등락률 양수(주도주검색식3은 하락
  종목도 걸릴 수 있어 제외) + 호가창 매도잔량이 매수잔량의 3배 이상
  ("눌린 스프링")일 때만 매수 진행
- 매수금액: 1종목당 100만원+, 최대 2종목 동시보유
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

MAX_POSITIONS    = 2                  # 1~2종목 몰빵회전
BUY_AMT_PER_SLOT = 1_000_000          # 종목당 100만원+ (부족하면 kis_api.buy()가 자체적으로 최소1주까지 축소시도)
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

CONDITION_KEYWORDS = ["주도주검색식3", "단타000", "장개장직후 종목찾기"]
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
        self._is_paused       = False

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
        write_state(BOT_STATE_FILE, {
            "positions":        self.positions,
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
        self.positions[code] = {
            "entry_price": price, "qty": qty, "buy_time": now,
            "source_tier": source_tier, "buy_tag": source_tier,
            "peak_price": None,   # +2.5% 도달 전까지는 None(트레일링 미활성)
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
                self.positions.pop(code, None)
                self._ws.unsubscribe_price(code)
            else:
                real_qty = self._ws.positions.get(code, {}).get("qty")
                if real_qty and code in self.positions:
                    self.positions[code]["qty"] = real_qty
            self._pending_orders.pop(code, None)

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

    def _rank_candidates(self, codes: list, code_multi_tag_map: dict) -> list:
        """1순위 겹침종목 → 2순위 단타000단독 → 3순위 나머지단독+scout fallback."""
        tier1, tier2, tier3 = [], [], []
        for code in codes:
            tags = code_multi_tag_map.get(code, [])
            if len(tags) >= 2:
                tier1.append(code)
            elif tags == ["단타000"]:
                tier2.append(code)
            elif tags:
                tier3.append(code)

        for code in self._load_scout_tier3_picks():
            if code not in tier1 and code not in tier2 and code not in tier3:
                tier3.append(code)

        return tier1 + tier2 + tier3

    def _run_candidate_scan_and_maybe_buy(self):
        codes, code_multi_tag_map = self._scan_conditions()
        if not codes:
            return
        ranked = self._rank_candidates(codes, code_multi_tag_map)
        if not ranked:
            return

        held_elsewhere = set()
        if get_all_positions:
            try:
                held_elsewhere = {p["code"] for p in get_all_positions()
                                   if p["bot_type"] != "daybot"}
            except Exception:
                pass

        tags_by_code = code_multi_tag_map
        for code in ranked:
            if len(self.positions) >= MAX_POSITIONS:
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
            tags = tags_by_code.get(code, [])
            if len(tags) >= 2:
                tier = "tier1_overlap"
            elif tags == ["단타000"]:
                tier = "tier2_danta000"
            else:
                tier = "tier3_fallback"
            self._do_buy(code, self._name(code), price, tier)

    # ============================================================
    # 메인 루프
    # ============================================================
    def run(self):
        self._notify("🚀 [DAYBOT] 단타봇 가동", critical=True)
        print(f"🚀 [DAYBOT] 단타봇 가동 | 최대 {MAX_POSITIONS}종목 | "
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

                # 10) 포지션 실시간감시
                self._check_all_positions_for_exit()

                # 11) 후보스캔(240초 주기, 슬롯 여유+매수시간대일 때만, 정지중이면 스킵)
                if (not self._is_paused
                        and len(self.positions) < MAX_POSITIONS
                        and BUY_START_TIME <= now_t <= BUY_END_TIME
                        and time.time() - self._last_scan_ts >= SCAN_INTERVAL_SEC):
                    self._run_candidate_scan_and_maybe_buy()
                    self._last_scan_ts = time.time()

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
