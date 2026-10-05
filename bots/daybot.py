"""
daybot.py — 영암9 단타봇 (회전매매, 최대 3일 보유)
================================================================
[이 파일이 하는 일 — 비개발자용 설명]

sbot/sbo2(스윙, 며칠~1주일 보유)보다는 짧고, 순수 당일청산보다는
여유있게 — daybot은 짧은 호흡의 회전매매 봇입니다.
- 대상: 키움 조건검색 3개(주도주검색식3/단타000/3개월수급 당일주도주)
  중 2개 이상 겹친 종목 우선. ★ 2026-10-02 대장 지정 — 겹침이 없으면
  주도주검색식3 단독만 매수("시장 관심을 받는 중"이라는 근거), 단타000/
  3개월수급 당일주도주 단독 히트는 더 이상 단독으로는 매수 안 함(겹침에
  포함되면 당연히 매수) — 단독히트 edge가 약하다는 정황 확인 후 보수화
  ★ 2026-10-05: "090930타점 시가이탈 오전중저가이탈"을 "3개월수급
  당일주도주"(3~6개월 전 거래 마른 소외주가 최근 3개월 내 2000억+
  수급유입 후 오늘 아침 무릎자리 2차 시세 포착용, 대장 신규 설계)로 교체.
- 매수조건: 위 조건검색 통과 + 당일 등락률(09:40 이전 "1차"는 3~8%,
  이후 "2차"는 0~15%) + 호가창 매도잔량이 매수잔량의 3배 이상
  ("눌린 스프링")일 때만 매수 진행. 익절(트레일링)은 1차/2차 구분 없음
- 매수금액: 1종목당 100만원(★ 2026-10-02 대장 지정 — 승률 50%+ 달성하면
  150만원으로 재상향 검토), 기본 3종목 동시보유(매수가능금액 50만원+면
  4번째 보너스슬롯)
- 매도기준: 손절 -3.5% 고정, 익절은 +2.5% 도달시 즉시매도 대신 트레일링
  스탑 전환(급등주는 10%+ 가는 경우가 많아서) — 고점 대비 -2% 밀리면 매도
- 매매시간: 08:00(프리장)~19:30(매수마감), 포지션 감시는 19:50까지
- 보유기간: ★ 2026-10-02 대장 지정 — 당일 EOD 강제청산 폐지("가랑비에
  옷 젖는다" — 하루만에 강제로 끊어내다 매일 조금씩 손실이 쌓이는 패턴
  확인). 트레일링(+2.5%) 진입 전까지는 최대 3영업일 보유 후
  손익 무관 강제청산, 트레일링 진입 후(이미 수익중)는 기한 없이
  트레일링 로직에만 맡김(수익나는 종목을 날짜 때문에 억지로 끊지 않음)

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
    read_state, update_state,
)
from kis_api import KisAPI
from kis_websocket import KisWebSocket
from kiwoom_api import KiwoomAPI
from notifier import Notifier
from daybot_db import DayTradeDB

load_dotenv(_os.path.join(_BASE, ".env"))

# ★ 2026-10-03 대장 지정 — "2번째 매수부터는 섹터교체로 새로 뜬 대장주를
#   빠르게 잡아야" 설계과제(10-01) 구현. intelligence/sector_monitor.py의
#   detect_baton_touch()를 재사용(market_concentration.py/day_trade_scout.py
#   와 동일 패턴) — 키움 API를 전혀 안 쓰고 sector_monitor가 이미 수집해둔
#   KIS 기반 데이터만 읽으므로 daybot 자체 조건검색 부담과 무관.
try:
    import sqlite3 as _sqlite3
    from sector_monitor import detect_baton_touch as _detect_baton_touch
    from sector_monitor import DB_PATH as _SECTOR_DB_PATH
except Exception:
    _detect_baton_touch = None
    _SECTOR_DB_PATH = None

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

BASE_MAX_POSITIONS = 3                # ★ 2026-10-02 대장 지정(2→3) — 종목당 매수금액을
                                       # 낮추는 대신 슬롯 수를 늘려 분산
# ★ 2026-10-01 대장 지정 — 매수가능금액이 이 이상이면 4번째 보너스슬롯
#   오픈(sbot/sbo2의 BONUS_SLOT_MIN_CASH와 동일 컨셉 — 여유자금 놀리지
#   않기). MAX_POSITIONS는 보너스 포함 상한값 — 메인루프의 "스캔을
#   시작할지" 판단에 쓰고, 실제 슬롯이 몇 개까지 열리는지는 매 스캔 시점
#   _run_candidate_scan_and_maybe_buy()가 실시간 잔고로 다시 판단한다.
BONUS_SLOT_MIN_CASH = 500_000
MAX_POSITIONS = BASE_MAX_POSITIONS + 1
BUY_AMT_PER_SLOT = 1_000_000          # ★ 2026-10-02 대장 지정(150만→100만) — 손실이 계속
                                       # 쌓이는 걸 보고 종목당 금액을 낮춰 리스크 축소. 승률
                                       # 50%+ 달성하면 150만으로 재상향 검토(대장 명시).
                                       # 부족하면 kis_api.buy()가 자체적으로 최소1주까지 축소시도.
TAKE_PROFIT_PCT  = 2.5                # 익절 +2~3% 중간값
STOP_LOSS_PCT    = -3.5               # 손절 -3~4% 중간값
# ★ 2026-09-29 밤 대장 지정 — 급등주 특성상 오르면 10%+ 가는 경우가
#   많아서, +2.5% 도달해도 바로 전량매도하지 말고 트레일링스탑으로
#   전환해서 더 큰 상승을 노림. 손절(-3.5%)은 트레일링 전환 전까지만
#   유효 — 일단 +2.5%를 찍고 나면 트레일링(고점대비 하락폭)만으로 매도
#   판단(원 손절선은 그 시점부터 현재가보다 한참 아래라 실질적으로
#   트레일링이 항상 더 타이트해서 자연스럽게 대체됨).
TRAILING_STOP_PCT = 2.0                # 고점 대비 이만큼 밀리면 매도("여유있게")
# ★ 2026-10-03 대장 지정 — 고점이 2.5~4.5% 구간일 때 2.0% 트레일링을
#   그대로 쓰면 2.5%에서 꺾이는 경우 수수료 공제 후 거의 본전(+0.27%)이
#   되는 문제가 있어, 이 구간만 트레일링폭을 좁혀(1.5%) 최소 수익을
#   확보하고, 4.5%를 넘는 급등주는 기존(2.0%)대로 여유를 준다.
TRAILING_STOP_PCT_TIGHT  = 1.5          # 고점 변동률 2.5~4.5% 구간 전용(타이트)
TRAILING_STOP_WIDEN_PCT  = 4.5          # 고점 변동률이 이 초과면 TRAILING_STOP_PCT(2.0%) 적용
MIN_LOCKED_PROFIT_PCT    = 1.0          # 트레일링 매도가의 최소 보장 수익률(세전, 하한선)

# ★ 2026-09-29 대장 지정 — 한투 애프터마켓 개편(09-14, 20시까지 정규장과
#   동일 실시간매칭) 반영해 포지션 감시/청산 시간대를 08:00(프리장)~19:50까지 확장.
#   kis_api.py의 buy()/sell()이 08:00~09:00 구간은 ORD_DVSN=62(시간외단일가),
#   09:00~20:00은 00/01(정규장)로 이미 내부 분기하므로 daybot은 신경쓸 필요 없음.
SESSION_START    = "0800"             # 이 시간대 밖이면 루프 자체를 idle
SESSION_END      = "1950"
# ★ 2026-10-01 대장 확인 — 프리장/애프터장엔 쓸만한 실시간 후보소스가
#   없음(키움 조건검색은 정규장 데이터 기준이라 장외 시간엔 전부 응답
#   타임아웃만 발생 — 아래 SCAN_END_TIME 코멘트 참고). 유일한 장외 후보
#   소스인 유튜브 라이브 모니터는 daybot이 아닌 sbo2쪽에 연동돼 있어
#   당장은 쓸 수 없음. 그래서 신규매수는 정규장(09:00~15:30)에만 하고,
#   보유종목 감시/청산(손절·익절·수동매도감지·EOD청산)만 프리장~애프터장
#   전체(08:00~19:50, SESSION_START~SESSION_END)로 확장해둔다.
BUY_START_TIME   = "0900"
BUY_END_TIME     = "1930"             # 19:30 이후 신규매수 중단 — EOD청산 전 버퍼(SCAN_END_TIME이 더 일찍 막아 사실상 도달 안 함)
# ★ 2026-10-01 대장 확인 — 키움 조건검색은 정규장(09:00~15:30) 데이터 기준이라
#   장마감 후엔 서버가 응답을 안 줌(전부 타임아웃). 애프터장(15:30~19:30)에
#   신규후보 스캔을 계속 돌려봐야 3개조건×최대3회×65초만 허비하므로 후보
#   스캔 자체를 정규장 마감 시각에 멈춘다. 보유종목 감시/EOD청산/수동매도감지는
#   무관 — 계속 돈다.
SCAN_END_TIME    = "1530"
# ★ 2026-10-02 대장 지정 — 당일 EOD 강제청산 폐지, 대신 "트레일링
#   미진입(아직 +2.5% 못 찍은) 상태로 3영업일 지나면 손익 무관 강제청산"
#   으로 교체("가랑비에 옷 젖는다" — 매일 EOD에 억지로 끊다 손실만
#   누적되던 패턴 확인). 이미 트레일링 진입(수익중)인 종목은 기한
#   없음 — 날짜 때문에 수익나는 포지션을 억지로 끊지 않는다.
#   ★ 대장 재지적(달력일 아니고 영업일이어야 함) — 포지션별로 날짜를
#   빼는 대신, 메인루프의 일일초기화(새 날 감지, 주말/휴장일이면 그
#   지점 자체에 도달 못 함)에 맞춰 보유중인 포지션마다 held_trading_days
#   를 매 실제 거래일마다 +1씩 올리는 방식으로 구현(아래 _do_buy/
#   run() 참고) — 주말·휴장일은 그 자리(continue)에서 걸러져 자동으로
#   카운트에서 빠짐, 별도 거래소 캘린더 조회 불필요.
HOLD_DAYS_LIMIT = 3

HOGA_ASK_BID_RATIO_MIN = 3.0          # ★ 대장 지정 — 매도잔량이 매수잔량의 3배 이상("눌린
                                       #   스프링", core/kis_api.py:get_hoga() 자체 docstring
                                       #   표현)일 때만 매수 진행. 미달이면 스킵.
# ★ 2026-10-01 대장 지정 — 대장의 실제 수동단타 기준 재정의: 09:40
#   이전("1차") 진입은 등락률 3~8% 구간만, 09:40 이후("2차") 진입은
#   더 넓은 0~15% 구간 허용(1차는 막 확인된 초입 모멘텀, 2차는 이미
#   어느정도 오른 종목도 받아들임 — 단, 익절은 1차/2차 구분 없이 기존
#   트레일링스탑 그대로: "간댕이가 작아서 짧게 먹고 나오는" 대장 본인의
#   수동매매 습관을 봇에 그대로 옮기지 말고, 10~20%까지 가는 건 끝까지
#   타게 둔다는 명시적 결정).
EARLY_ENTRY_CUTOFF_TIME = "0940"
EARLY_MIN_CHANGE_PCT = 3.0
EARLY_MAX_CHANGE_PCT = 8.0
LATE_MAX_CHANGE_PCT  = 15.0

SCAN_INTERVAL_SEC = 240               # 조건검색 풀사이클(3개조건) 주기 — 65초 재시도
                                       # 백오프까지 감안한 안전마진(core/kiwoom_api.py 참고)
LOOP_SLEEP_SEC     = 5                # 포지션감시/EOD체크용 빠른 루프 틱
PENDING_ORDER_TIMEOUT_SEC = 30        # 미체결 주문 취소 판단 기준(daybot 5초루프 기준 조정값)
# ★ 2026-10-02 대장 지정(20만→50만) — 손절이 반복돼 매수가능금액이
#   BUY_AMT_PER_SLOT(100만원)에 못 미치더라도, 50만원 이상만 남아있으면
#   그 금액 그대로 매수 진행(_do_buy()의 `min(BUY_AMT_PER_SLOT, psbl_cash)`
#   가 이미 부족분만큼 축소해서 사므로 이 상수는 "너무 작은 금액으로
#   사버리는 걸 막는 하한선" 역할). 이 밑이면 해당 매수 시도만 스킵
#   (스캔 자체를 멈추진 않음 — 다른 후보/다음 스캔주기는 계속 돈다).
MIN_ANALYSIS_CASH  = 500_000

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
#   조건). use_keywords는 부분일치라 조건식 이름 전체를 안 써도 되지만,
#   이 밑 _rank_candidates()에서 code_multi_tag_map에 기록되는 값은
#   매칭된 조건식의 전체 이름이라 그쪽은 전체 이름으로 비교해야 함.
# ★ 2026-10-05 대장 지정 — "090930타점 시가이탈 오전중저가이탈"을
#   "3개월수급 당일주도주"로 교체(대장이 새로 설계한 검색식 — 3~6개월
#   전 거래 마른 소외주가 최근 3개월 내 2000억+ 수급유입 후 오늘 아침
#   무릎자리(3~12%)에서 2차 시세 시작하는 종목 포착용).
CONDITION_KEYWORDS = ["주도주검색식3", "단타000", "3개월수급 당일주도주"]
COND_3MONTH_LEADER = "3개월수급 당일주도주"
# ★ "5본봉거래대금단타"는 대장이 수동단타에서 안 쓰던 검색식이라 제외

# ★ 2026-10-05 대장 지정 — "3개월수급 당일주도주"는 거래대금만 보기
#   때문에 세력의 "조용한 매집"과 "한탕 치고 빠진 설거지"를 구분 못 함
#   (대장 지적). 키움 조건검색식 문법은 "특정 날짜를 찾아 그 날짜의
#   다른 값을 참조"하는 2단계 로직을 지원 안 해서(범위 내 집계만 가능)
#   조건식 자체엔 못 넣고, 여기서 _check_spike_quality()로 보강한다.
SPIKE_LOOKBACK_DAYS   = 60       # 스파이크(최대거래대금일) 탐색 범위 — 조건식 B의 60봉과 동일
SPIKE_MIN_VALUE_WON   = 200_000_000_000  # 2000억 — 조건식 B와 동일 기준
# ★ 2026-10-05 대장 공유 코드 참고 — 65.0%로 조정(원래 2/3=66.7%였으나
#   우리기술투자 실측값 65.2%가 딱 걸쳐서, 대장이 짠 기준값 65.0%로 맞춤).
SPIKE_CLOSE_POS_MIN   = 0.65     # 스파이크일 종가가 당일 변동폭 상위 65% 안에 있어야 함(기준1)
SPIKE_RETRACE_MAX_PCT = 1.05     # 현재가가 스파이크 이전 5일 평균 종가의 105% 이하로 돌아오면 탈락(기준2)
SPIKE_MIN_DAY_RETURN_PCT = 7.0   # ★ 2026-10-05 대장 공유 코드에서 추가 — 스파이크일 전일종가
                                 #   대비 등락률이 이 미만이면 "거래대금만 터지고 주가는 그대로"인
                                 #   가짜 매집으로 간주(기준3)
# ★ 2026-10-05 대장 지정 — "3개월수급 당일주도주"는 장 초반 수급쏠림을
#   보는 패턴이라, 10시 이후에 뒤늦게 올라타는 건 가짜(단순 눌림목
#   되돌림이나 뒷북 추격)일 가능성이 크다는 판단 — 이 소스만 매수를
#   10시까지로 제한한다(다른 3개 소스는 BUY_END_TIME까지 그대로).
COND_3MONTH_LEADER_BUY_CUTOFF_TIME = "1000"

# ★ 2026-10-02 대장 지적 — 동국산업이 실제로는 단타000+090930타점 둘 다에
#   뜬 진짜 겹침종목이었는데, 기존엔 _scan_conditions()가 매 스캔(240초)
#   마다 code_multi_tag_map을 새로 빈 dict로 만들어서 "이번 한 번의 스캔
#   안에서 동시에 잡힌" 경우만 겹침으로 인정했음 — 조건마다 응답
#   타임아웃이 잦고(core/kiwoom_api.py) 종목이 몇 분 간격으로 조건을
#   들락날락하다 보니, 실제로는 겹치는데 스캔 타이밍이 어긋나 단독
#   히트로만 기록되는 경우가 있었음(이게 오늘 승패가 거의 반반(한솔
#   +4만 vs 동국산업 -4만)이었던 이유 중 하나로 추정). 최근 N분 내
#   관측된 태그를 전부 모아서 겹침 판정하도록 교체.
OVERLAP_WINDOW_SEC = 1200   # 최근 20분(스캔 240초 기준 약 5사이클) 내 태그는 전부 겹침 판정에 합산

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
        self._ws_paused       = False   # ★ 2026-10-06 — 주말/휴장일엔 웹소켓도 같이 쉼(아래 run() 참고)
        self._last_scan_ts    = 0.0
        self._last_manual_check_ts = 0.0
        self._is_paused       = False
        # ★ 2026-10-02 대장 지적 — 스캔 사이클(240초)을 넘나드는 겹침종목
        #   탐지용. {code: {tag: last_seen_ts}} — OVERLAP_WINDOW_SEC 안의
        #   태그를 전부 모아서 겹침(2개 이상 조건) 판정(아래 _rank_candidates
        #   참고). 재시작시 휘발돼도 무방(겹침은 그날그날의 실시간 신호라
        #   영구 보존 불필요) — 상태파일에 저장 안 함.
        self._recent_tags: dict = {}
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
        # ★ 2026-10-03 — write_state()(전체덮어쓰기)를 쓰고 있었던 버그:
        #   매 루프(5초)마다 여기서 positions/sold_today 등 5개 키만 있는
        #   dict로 상태파일 전체를 갈아치워서, 키키가 그 사이에 써놓은
        #   paused/pending_cmd/cmd_result가 한 루프 안에 지워짐(!daybot정지
        #   가 5초 만에 자동 해제되던 원인). update_state()(부분병합+락)로
        #   교체 — sbot.py가 이미 쓰는 검증된 패턴과 동일.
        update_state(BOT_STATE_FILE,
            positions=positions_snapshot,
            sold_today=self.sold_today,
            sold_today_date=self._sold_today_date,
            code_name_map=self.code_name_map,
            last_update=now_hms(),
        )

    def _restore_state(self):
        """재시작 시 daybot 자신의 상태를 복구하고 실계좌와 교차확인.
        ★ 대원전선처럼 daybot이 모르는 종목은 절대 자동입양하지 않는다
        (core/account_sync.py::sync_positions()는 계좌의 모든 미인식
        종목을 자동으로 흡수하는 구조라 여기선 재사용 금지)."""
        saved = _read_state()
        self.positions       = saved.get("positions", {})
        self.sold_today      = saved.get("sold_today", {})
        self._sold_today_date = saved.get("sold_today_date", today_str())
        self.code_name_map.update(saved.get("code_name_map", {}))

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
    # 디스코드 명령 처리 (키키 !daybot매도 등)
    # ============================================================
    def _handle_pending_command(self, st: dict):
        """★ 2026-10-03 신규 — interface/kiki_cmd.py의 cmd_sell()이
        pending_cmd={"type":"sell","code":...}를 상태파일에 써놓고
        cmd_result를 기다리는데, daybot은 이걸 소비하는 로직이 아예
        없어서 !daybot매도가 항상 "응답 없음"으로 타임아웃됐음(sbot.py의
        _handle_pending_command와 동일 패턴으로 신설). 호출부는 sbot과
        동일하게 주말/휴장 continue보다 먼저 와야 한다(그래야 장외에
        보낸 명령도 바로 처리됨 — 08-30에 sbot에서 겪은 것과 같은 유형의
        버그를 daybot에서는 애초에 피해간다)."""
        pending = st.get("pending_cmd")
        if not pending:
            return
        if pending.get("type") != "sell":
            return

        sell_code = pending.get("code", "")
        if sell_code not in self.positions:
            update_state(BOT_STATE_FILE,
                         cmd_result=f"⚠️ {sell_code} daybot 보유 중이 아님",
                         pending_cmd=None)
            return

        price = self._get_current_price(sell_code)
        if price <= 0:
            update_state(BOT_STATE_FILE,
                         cmd_result=f"⚠️ {sell_code} 시세조회 실패 — 다음 명령 재시도 필요",
                         pending_cmd=None)
            return

        qty = self.positions[sell_code]["qty"]
        self._do_sell(sell_code, qty, "즉시매도(AI비서)", price)
        update_state(BOT_STATE_FILE,
                     cmd_result=f"✅ [DAYBOT] {sell_code} 즉시매도 명령 전달 완료",
                     pending_cmd=None)

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
        체크, 진입 후엔 고점(peak_price) 대비 트레일링 하락시에만 매도
        — 원 손절선은 진입 시점부터 현재가보다 한참 아래라 트레일링이
        항상 먼저 걸리므로 별도 분기 불필요.
        ★ 2026-10-03 대장 지정 — 고점 변동률 2.5~4.5% 구간은
        TRAILING_STOP_PCT_TIGHT(1.5%)로 좁혀서 수수료 공제 후에도
        최소 수익을 확보(2.5%에서 바로 꺾이면 기존 2.0% 트레일링으론
        거의 본전이었음), 4.5% 초과 급등주는 기존 TRAILING_STOP_PCT
        (2.0%)로 여유를 준다. MIN_LOCKED_PROFIT_PCT(1.0%)는 혹시
        모를 경우를 대비한 최종 하한선.
        ★ 2026-10-02 대장 지정 — 당일 EOD 강제청산 폐지, 대신 트레일링
        미진입 상태로 HOLD_DAYS_LIMIT(3일) 지나면 손익 무관 강제청산
        (손실 종목을 매일 억지로 끊다 조금씩 손실이 쌓이던 패턴 방지).
        트레일링 진입(이미 수익중) 종목은 기한 체크 자체를 안 함."""
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
                peak_rate  = (pos["peak_price"] - entry) / entry * 100
                trail_pct  = (TRAILING_STOP_PCT if peak_rate > TRAILING_STOP_WIDEN_PCT
                              else TRAILING_STOP_PCT_TIGHT)
                trail_stop = pos["peak_price"] * (1 - trail_pct / 100)
                floor_price = entry * (1 + MIN_LOCKED_PROFIT_PCT / 100)
                trail_stop  = max(trail_stop, floor_price)
                if current <= trail_stop:
                    self._do_sell(code, pos["qty"],
                                  f"트레일링청산(고점{pos['peak_price']:,.0f}대비"
                                  f"-{trail_pct:.1f}%, 총{rate:+.2f}%)", current)
                continue

            if rate >= TAKE_PROFIT_PCT:
                pos["peak_price"] = current
                print(f"📈 [daybot] {code} +{rate:.2f}% 도달 — 트레일링 모드 전환 "
                      f"(고점:{current:,.0f}, -{TRAILING_STOP_PCT_TIGHT:.1f}% 밀리면 매도)")
                continue
            if rate <= STOP_LOSS_PCT:
                self._do_sell(code, pos["qty"], f"손절({rate:.2f}%)", current)
                continue

            held = pos.get("held_trading_days", 0)
            if held >= HOLD_DAYS_LIMIT:
                self._do_sell(code, pos["qty"],
                              f"보유기한청산({held}영업일, {rate:+.2f}%)", current)

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
                "held_trading_days": 0,  # ★ 보유기한청산용 — 실제 영업일만 셈(아래 일일초기화 참고)
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

        pos = self.positions.get(code, {})
        entry_price = pos.get("entry_price", price)
        # ★ 2026-10-02 대장 지정 — sbot/sbo2/cbot과 동일 정책(09-21 통일)으로
        #   맞춤: 손실/본절(수익 없음)로 판 종목만 당일 재매수 금지, 소규모
        #   익절이라도 수익 났으면 당일 재매수 허용(회전매매 취지상 같은
        #   종목이 다시 신호를 줄 수 있음). reason 문자열 매칭이 아니라
        #   실제 손익 부호로 판단(sbot.py와 동일 이유).
        is_loss = price <= entry_price

        self.db.save_sell(code, price, reason)
        if _master_record:
            _master_record(bot_type="daybot", code=code, stock_name=name,
                            buy_price=entry_price,
                            sell_price=price, qty=qty, sell_reason=reason,
                            buy_tag=pos.get("buy_tag", ""))
        if _master_remove:
            _master_remove("daybot", code)

        with self._positions_lock:
            self.positions.pop(code, None)
        self._pending_orders.pop(code, None)
        if is_loss:
            self.sold_today[code] = now_hms()
        self._ws.unsubscribe_price(code)

        emoji = "💔" if is_loss else "💰"
        self._notify(f"{emoji} [daybot] 매도 {code}({name}) | {reason} @{price:,.0f}원")
        print(f"{emoji} [daybot] 매도 {code}({name}) | {reason} @{price:,.0f}원")

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
        (재구현 금지 — code_multi_tag_map이 이미 겹침추적 해줌).
        ★ 2026-10-02 — 이번 스캔에서 관측된 태그를 self._recent_tags에
        누적(+타임스탬프)하고, OVERLAP_WINDOW_SEC보다 오래된 태그는 버려서
        스캔 사이클을 넘나드는 겹침도 잡아낸다(위 OVERLAP_WINDOW_SEC
        코멘트 참고)."""
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

        now_ts = time.time()
        for code, tags in code_multi_tag_map.items():
            bucket = self._recent_tags.setdefault(code, {})
            for tag in tags:
                bucket[tag] = now_ts
        for code in list(self._recent_tags.keys()):
            bucket = self._recent_tags[code]
            for tag in list(bucket.keys()):
                if now_ts - bucket[tag] > OVERLAP_WINDOW_SEC:
                    del bucket[tag]
            if not bucket:
                del self._recent_tags[code]

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
        """★ 2026-10-03 대장 재지정 — 4개 소스(주도주검색식3/단타000/
        3개월수급 당일주도주/섹터로테이션) 기준으로 전면 재설계:
        1순위 겹침(4개 소스 중 2개 이상 동시충족) → 2순위 주도주/3개월
        수급 당일주도주 단독(★ 2026-10-05: 3개월수급도 주도주와 동급으로
        승격 — _check_spike_quality()와 10시 매수마감으로 품질 통제함)
        → 3순위 섹터 단독 → 4순위(마지막) 단타000 단독 → scout fallback
        (최후, 후보고갈 방지용).
        ★ 10-02엔 단타000/090930타점 단독 히트를 아예 매수 안 했는데,
        이번에 "마지막 순위"로 재활성화 — 완전 배제가 아니라 우선순위
        최하단으로 내림(섹터를 4번째 소스로 추가해 겹침 풀이 넓어진 것도
        감안).
        ★ 겹침 판정은 이번 스캔의 code_multi_tag_map이 아니라
        self._recent_tags(스캔 여러 사이클 누적, OVERLAP_WINDOW_SEC 윈도우)
        사용 — 사이클이 갈려도 겹침을 놓치지 않게(10-02 동국산업 사례).
        반환값: (순위리스트, {code: source_label})."""
        sector_codes = self._get_sector_rotation_boost_codes()

        tier1, tier2, tier3, tier4 = [], [], [], []
        source_label = {}
        for code in codes:
            tags = set(self._recent_tags.get(code, {}).keys()) or set(code_multi_tag_map.get(code, []))
            if code in sector_codes:
                tags = tags | {"섹터"}
            if len(tags) >= 2:
                tier1.append(code)
                source_label[code] = "tier1_overlap"
            elif tags == {"주도주검색식3"}:
                tier2.append(code)
                source_label[code] = "tier2_주도주"
            elif tags == {COND_3MONTH_LEADER}:
                # ★ 2026-10-05 대장 지정 — "3개월수급 당일주도주" 단독
                #   히트를 주도주검색식3과 동급(tier2)으로 승격. 매집/
                #   설거지 2차 필터(_check_spike_quality)+10시 매수마감
                #   (COND_3MONTH_LEADER_BUY_CUTOFF_TIME)으로 품질을 이미
                #   따로 통제하므로 단타000/090930타점급 최하위에 둘
                #   이유가 없다는 판단.
                tier2.append(code)
                source_label[code] = "tier2_3개월수급"
            elif tags == {"섹터"}:
                tier3.append(code)
                source_label[code] = "tier3_섹터"
            elif tags == {"단타000"}:
                tier4.append(code)
                source_label[code] = "tier4_단타000"

        tier5 = []
        for code in self._load_scout_tier3_picks():
            if code not in tier1 and code not in tier2 and code not in tier3 and code not in tier4:
                tier5.append(code)
                source_label[code] = "tier3_scout"

        return tier1 + tier2 + tier3 + tier4 + tier5, source_label

    def _get_sector_rotation_boost_codes(self) -> set:
        """★ 2026-10-03 — intelligence/sector_monitor.py의 detect_baton_touch()
        로 최근 5분간 "급가속"(flow_rate>30%) 테마를 찾고, 그 테마에 속한
        종목코드를 반환. _rank_candidates()가 이 결과를 "섹터" 태그로
        취급해 4개 소스(주도주검색식3/단타000/3개월수급 당일주도주/섹터)
        겹침판정과 3순위(섹터 단독) 분류에 사용(10-01 설계과제, 10-03 전면 재설계로
        모든 슬롯에 적용 — 처음엔 2번째 슬롯부터만이었는데 섹터를 정식
        소스로 승격하며 구분 없앰). 실패해도 조용히 빈 set 반환 — 이
        기능이 daybot 핵심 매수로직을 막으면 안 됨."""
        if _detect_baton_touch is None or _SECTOR_DB_PATH is None:
            return set()
        try:
            conn = _sqlite3.connect(_SECTOR_DB_PATH, timeout=5)
            signals = _detect_baton_touch(conn)
            accel_themes = {s["theme_nm"] for s in signals if "급가속" in s.get("status", "")}
            if not accel_themes:
                conn.close()
                return set()
            placeholders = ",".join("?" * len(accel_themes))
            rows = conn.execute(f"""
                SELECT DISTINCT code FROM stock_momentum
                WHERE theme_nm IN ({placeholders})
                AND ts >= datetime('now', '-10 minutes', 'localtime')
            """, tuple(accel_themes)).fetchall()
            conn.close()
            if accel_themes:
                print(f"🔥 [daybot] 섹터로테이션 감지: {', '.join(accel_themes)}")
            return {r[0] for r in rows}
        except Exception as e:
            print(f"⚠️ [daybot] 섹터로테이션 체크 오류: {e}")
            return set()

    def _check_spike_quality(self, code: str, current_price: float) -> tuple:
        """★ 2026-10-05 — "3개월수급 당일주도주"(COND_3MONTH_LEADER) 후보
        전용 2차 필터. 조건식은 거래대금만 보므로, 최근 SPIKE_LOOKBACK_DAYS
        내 최대거래대금일(스파이크)을 찾아 세 가지를 확인한다:
        기준1(캔들 모양) — 스파이크일 종가가 당일 변동폭 상위 65% 안에
        있어야 함(위꼬리 길게 달고 밀린 "설거지"면 탈락).
        기준2(눌림목 건전성) — 현재가가 스파이크 이전 수준으로 거의
        되돌아왔으면 탈락("세력이 이미 털고 나간" 정황).
        기준3(당일 상승률) — 스파이크일 전일종가 대비 등락률이
        SPIKE_MIN_DAY_RETURN_PCT 미만이면 탈락("거래대금만 터지고 주가는
        그대로"인 매물소화 실패 의심 — 대장 공유 코드 아이디어 반영).
        ★ daily는 get_daily_ohlc() 계약대로 "최신→과거" 순서가 보장돼야
        기준3의 "전일종가"(daily[spike_idx+1])가 맞게 나온다.
        데이터 부족/조회 실패시엔 통과시킨다(섹터로테이션과 동일 원칙 —
        보조 필터가 핵심 매수로직을 막으면 안 됨).
        반환: (통과여부, 사유)"""
        try:
            daily = self.api.get_daily_ohlc(code, days=SPIKE_LOOKBACK_DAYS + 30)
        except Exception as e:
            return True, f"일봉조회오류(통과): {e}"
        if len(daily) < 30:
            return True, "일봉데이터부족(통과)"

        # index 0 = 오늘(아직 형성중일 수 있어 스파이크 탐색에서 제외)
        window = daily[1:1 + SPIKE_LOOKBACK_DAYS]
        if len(window) < 10:
            return True, "스파이크 탐색기간 부족(통과)"

        spike = max(window, key=lambda r: r["close"] * r["volume"])
        spike_value = spike["close"] * spike["volume"]
        if spike_value < SPIKE_MIN_VALUE_WON:
            return True, "스파이크기준미달(조건식에서 이미 필터됨, 통과)"

        spike_idx = daily.index(spike)

        candle_range = spike["high"] - spike["low"]
        close_pos = ((spike["close"] - spike["low"]) / candle_range
                     if candle_range > 0 else 1.0)
        if close_pos < SPIKE_CLOSE_POS_MIN:
            return False, f"스파이크일 종가위치{close_pos:.0%}(설거지의심)"

        if spike_idx + 1 < len(daily):
            prev_close = daily[spike_idx + 1]["close"]
            day_return = ((spike["close"] - prev_close) / prev_close * 100
                          if prev_close > 0 else 0)
            if day_return < SPIKE_MIN_DAY_RETURN_PCT:
                return False, f"스파이크일 상승률{day_return:.1f}%(대금 대비 미달)"

        pre_spike_bars = daily[spike_idx + 1: spike_idx + 6]
        if len(pre_spike_bars) < 3:
            return True, "스파이크이전데이터부족(통과)"
        pre_spike_baseline = sum(r["close"] for r in pre_spike_bars) / len(pre_spike_bars)
        if current_price <= pre_spike_baseline * SPIKE_RETRACE_MAX_PCT:
            return False, (f"현재가{current_price:,.0f}가 스파이크이전수준"
                            f"{pre_spike_baseline:,.0f}근처로복귀(설거지의심)")

        return True, "매집패턴확인"

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
            # ★ 2026-09-30/10-01 대장 지정 — 주도주검색식3은 거래대금 등
            #   기준이라 당일 하락 중인 종목도 걸릴 수 있어 제외. 09:40
            #   이전("1차")엔 막 초입 모멘텀만(3~8%), 이후("2차")엔 이미
            #   어느정도 오른 종목까지 허용(0~15%) — 대장의 실제 수동단타
            #   진입기준을 그대로 반영.
            tier = source_label.get(code, "unknown")
            mdata = self.api.get_market_data(code) or {}
            try:
                price = float(mdata.get("stck_prpr", 0) or 0)
                chg   = float(mdata.get("prdy_ctrt", 0) or 0)
            except (TypeError, ValueError):
                continue
            if price <= 0:
                continue
            if now_hhmm() < EARLY_ENTRY_CUTOFF_TIME:
                if not (EARLY_MIN_CHANGE_PCT <= chg <= EARLY_MAX_CHANGE_PCT):
                    print(f"⏭️ [daybot] {code} 패스 — 1차구간(09:40전) 등락률 {chg:+.2f}%"
                          f"가 {EARLY_MIN_CHANGE_PCT}~{EARLY_MAX_CHANGE_PCT}% 범위 밖")
                    self.db.log_candidate(code, self._name(code), tier, price, chg,
                                           skip_reason="등락률범위밖", raw_market_data=mdata)
                    continue
            else:
                if not (0 < chg <= LATE_MAX_CHANGE_PCT):
                    print(f"⏭️ [daybot] {code} 패스 — 2차구간(09:40후) 등락률 {chg:+.2f}%"
                          f"가 0~{LATE_MAX_CHANGE_PCT}% 범위 밖")
                    self.db.log_candidate(code, self._name(code), tier, price, chg,
                                           skip_reason="등락률범위밖", raw_market_data=mdata)
                    continue
            # ★ 2026-10-05 대장 지정 — "3개월수급 당일주도주" 태그가 붙은
            #   후보만 (1) 10시 이후 매수 금지 + (2) 매집/설거지 2차 필터
            #   (_check_spike_quality) 적용. 다른 소스(주도주검색식3/
            #   단타000/섹터) 단독 후보는 "과거 거래대금 스파이크" 개념
            #   자체가 없어 해당 없음.
            code_tags = set(self._recent_tags.get(code, {}).keys()) or set(code_multi_tag_map.get(code, []))
            if COND_3MONTH_LEADER in code_tags:
                if now_hhmm() >= COND_3MONTH_LEADER_BUY_CUTOFF_TIME:
                    print(f"⏭️ [daybot] {code} 패스 — 3개월수급 10시 매수마감 경과")
                    self.db.log_candidate(code, self._name(code), tier, price, chg,
                                           skip_reason="3개월수급10시마감경과", raw_market_data=mdata)
                    continue
                ok_spike, spike_reason = self._check_spike_quality(code, price)
                if not ok_spike:
                    print(f"⏭️ [daybot] {code} 패스 — {spike_reason}")
                    self.db.log_candidate(code, self._name(code), tier, price, chg,
                                           skip_reason=spike_reason, raw_market_data=mdata)
                    continue
            # ★ 2026-09-29 대장 지정 — 매도잔량이 매수잔량의 3배 이상
            #   ("눌린 스프링", core/kis_api.py:get_hoga() 자체 표현)일
            #   때만 매수 진행. get_hoga()는 이미 있던 기존 메서드 재사용
            #   (sbot이 AI참고용으로만 쓰던 걸 daybot은 매수게이트로 사용).
            hoga = self.api.get_hoga(code) or {}
            if hoga.get("ask_bid_ratio", 0) < HOGA_ASK_BID_RATIO_MIN:
                print(f"⏭️ [daybot] {code} 패스 — 매도/매수잔량비 "
                      f"{hoga.get('ask_bid_ratio', 0):.2f} < {HOGA_ASK_BID_RATIO_MIN}")
                self.db.log_candidate(code, self._name(code), tier, price, chg,
                                       ask_bid_ratio=hoga.get("ask_bid_ratio", 0),
                                       skip_reason="호가비율미달",
                                       raw_market_data=mdata, raw_hoga_data=hoga)
                continue
            self.db.log_candidate(code, self._name(code), tier, price, chg,
                                   ask_bid_ratio=hoga.get("ask_bid_ratio", 0),
                                   bought=True, raw_market_data=mdata, raw_hoga_data=hoga)
            self._do_buy(code, self._name(code), price, tier)

    # ============================================================
    # 메인 루프
    # ============================================================
    def run(self):
        self._notify("🚀 [DAYBOT] 단타봇 가동", critical=True)
        print(f"🚀 [DAYBOT] 단타봇 가동 | 기본 {BASE_MAX_POSITIONS}종목"
              f"(+매수가능금액 {BONUS_SLOT_MIN_CASH:,}원 이상시 1종목 보너스) | "
              f"익절+{TAKE_PROFIT_PCT}% 손절{STOP_LOSS_PCT}% | "
              f"미익절 {HOLD_DAYS_LIMIT}일 경과시 강제청산")
        self._restore_state()

        while True:
            try:
                today = today_str()
                now_t = now_hhmm()

                # 1) heartbeat — 항상 최우선(continue 게이트보다 앞)
                pathlib.Path(HB_FILE).touch()

                # 2) 토큰 갱신 — 역시 continue 게이트보다 앞
                self.api.refresh_token_if_needed()

                # 2-1) 키키 !daybot매도 등 명령 처리 — sbot과 동일하게
                #      주말/휴장 continue보다 먼저 처리(장외에 보낸
                #      명령도 다음 개장까지 묵히지 않기 위함).
                self._handle_pending_command(_read_state())

                # 3) 주말
                # ★ 2026-10-06 대장 지적 — 주말/휴장일에도 웹소켓(H0STCNI0/
                #   H0STCNT0)은 __init__에서 한 번 start()된 채 메인루프와
                #   무관하게 계속 돌아서, KIS 서버가 휴장일엔 연결을 끊어
                #   버리니 "연결종료→5초후재연결" 스팸이 계속 찍히고 있었음.
                #   메인루프가 쉬는 동안 웹소켓도 같이 멈췄다가, 정상 개장일로
                #   돌아오면 다시 start()한다.
                if is_weekend():
                    if not self._ws_paused:
                        self._ws.stop(); self._ws_paused = True
                    time.sleep(300); continue

                # 4) 휴장일 (None-safe — 판단불가면 캐시 안 하고 다음 루프 재시도)
                if self._holiday_checked != today:
                    _open = self.api.is_market_open()
                    if _open is not None:
                        self._is_holiday = not _open
                        self._holiday_checked = today
                if self._is_holiday:
                    if not self._ws_paused:
                        self._ws.stop(); self._ws_paused = True
                    time.sleep(300); continue

                # ★ 웹소켓 재개는 아래 6)번(세션시간 체크) 통과 시점에서만
                #   한다 — 여기서 바로 재개하면 "주말/휴장은 아니지만 아직
                #   세션 시작 전(예: 새벽 3시)"인 구간에서 재개→6)번 게이트
                #   에 바로 걸려 재정지, 매 루프 start/stop이 반복되는
                #   낭비가 생김.

                # 4-1) 키키 !daybot정지/!daybot시작 반영 — sbot과 동일 패턴:
                #      정지돼도 보유종목 매도체크는 계속 돌고, 신규매수(9번)만 멈춘다.
                self._is_paused = _read_state().get("paused", False)

                # 5) 일일 초기화 — 새 날이면 당일 관련 플래그 리셋.
                #    ★ 이 지점에 도달했다는 것 자체가 "오늘은 주말도 휴장일도
                #    아니다"(3)/4)번에서 이미 걸러짐)라는 뜻이므로, 보유중인
                #    포지션의 held_trading_days를 여기서 +1 해도 주말·휴장일이
                #    끼어드는 전환(예: 금요일→월요일)은 자동으로 1회만 카운트됨
                #    (보유기한청산 — HOLD_DAYS_LIMIT 참고, 2026-10-02 대장
                #    지정: 달력일 아니고 영업일 기준이어야 함).
                if today != self._sold_today_date:
                    self.sold_today = {}
                    self._sold_today_date = today
                    with self._positions_lock:
                        for pos in self.positions.values():
                            pos["held_trading_days"] = pos.get("held_trading_days", 0) + 1

                # 6) 세션 외 시간 (19:50~다음날 08:00) — EOD 강제청산 없이
                #    그냥 장외엔 감시를 쉰다(최대 3일 보유 허용이므로 매일
                #    밤 억지로 정리할 필요가 없어짐, 2026-10-02 대장 지정).
                #    ★ 2026-10-06 — 이 시간대도 웹소켓 같이 멈춤(위 주말/
                #    휴장일과 동일 이유 — KIS가 장외에 연결을 끊어서 계속
                #    재연결 스팸이 찍힘).
                if not (SESSION_START <= now_t <= SESSION_END):
                    if not self._ws_paused:
                        self._ws.stop(); self._ws_paused = True
                    time.sleep(60); continue

                if self._ws_paused:
                    self._ws.start(); self._ws_paused = False

                # 7) 미체결 주문 정리
                self._check_pending_orders()

                # 7-1) 수동매도 감지(60초 주기) — 대장이 HTS/MTS로 직접
                #      매도할 계획이라 daybot이 좀비 포지션을 안 만들게
                if time.time() - self._last_manual_check_ts >= MANUAL_SELL_CHECK_INTERVAL_SEC:
                    self._check_manual_sells()
                    self._last_manual_check_ts = time.time()

                # 8) 포지션 실시간감시 (손절/트레일링/보유기한청산 전부 포함)
                self._check_all_positions_for_exit()

                # 9) 후보스캔(240초 주기, 슬롯 여유+매수시간대일 때만, 정지중이면 스킵)
                #     ★ 백그라운드 스레드로 실행 — 키움 조건검색이 타임아웃/
                #     재시도로 몇 분씩 걸려도 메인루프(heartbeat/포지션감시)는
                #     계속 돈다. 이전 스캔이 아직 안 끝났으면 새로 안 띄움
                #     (중복실행 방지).
                if (not self._is_paused
                        and len(self.positions) < MAX_POSITIONS
                        and BUY_START_TIME <= now_t <= SCAN_END_TIME
                        and time.time() - self._last_scan_ts >= SCAN_INTERVAL_SEC
                        and (self._scan_thread is None or not self._scan_thread.is_alive())):
                    self._last_scan_ts = time.time()
                    self._scan_thread = threading.Thread(
                        target=self._run_candidate_scan_and_maybe_buy, daemon=True)
                    self._scan_thread.start()

                # 10) 상태 저장
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
