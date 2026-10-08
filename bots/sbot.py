"""
sbot.py — 영암9 스윙봇 메인 (전면 재구성판)
================================================================
[이 파일이 하는 일 — 비개발자용 설명]

스윙봇은 하루 안에 사고 파는 단타와 달리, 며칠~1주일 보유하는 봇입니다.
- 대상: 시총 1조~20조 중대형주 (안정적인 추세 종목)
- 매수금액: 1종목당 200만원 (단타의 10배)
- 보유종목: 최대 3개 (큰 자금 집중 투자)
- 매도기준: ATR 추세추종 (손절 매수가-ATR×2, 목표1 매수가+ATR×3(상한+20%) → 50%익절 후 트레일링)

[적용된 개선사항]
[★ 치명적 버그 수정]
1. 매수 직후 self.positions 즉시 업데이트
2. buy_context는 전량 매도 시만 삭제 (부분 매도 보호)
3. peak_tracker 매수 직후 즉시 초기화
4. today 변수 휴장일 체크 시 NameError 방지

[★ 손실 방어]
5. 본절 보호 — 1차 익절 후 본전 깨지면 청산
6. ATR 기반 동적 손절선
7. 동적 매수 임계치 (최근 승률 따라 자동 조정)

[★ 수익 극대화]
8. 포지션 사이징 (점수 비례 매수금액)
9. 약세장 + 강세 종목 매수 허용
10. 추세 강한 종목은 양봉 조건 면제

[모듈 구조]
  sbot.py          ← 메인 루프 (이 파일)
  kis_api.py       ← 한투 API (검증됨, 그대로) — ★2026-09-29 키움 제거,
                       sbot은 한투(KIS)만 사용(대장 결정, 단타봇용으로 분리)
  notifier.py      ← 디스코드 알림 (재시도 강화)
  sbot_strategy.py ← 스윙 전략 (본절보호/effective_entry)
  sbot_analyzer.py ← AI 분석 (점수 분포 명확)
  sbot_db.py       ← 매매이력 DB (WAL 모드)
  common_utils.py  ← 공통 헬퍼
  risk_manager.py  ← 포지션 사이징
"""
import sys as _sys
import os as _os
_BASE = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
# ★ 2026-09-29: sbot×sbo2 통합 — core/candidate_pool.py가 lina_bot의
#   swing_master/trend_analyzer/kr_theme_finance.db를 재사용하므로
#   lina_bot 추가(intelligence/youtube_stock_monitor.py가 이미 쓰던
#   것과 동일 패턴).
for _d in ["core", "intelligence", "interface", "bots", "lina_bot", ""]:
    _p = _os.path.join(_BASE, _d)
    if _p not in _sys.path:
        _sys.path.insert(0, _p)

import os
import time
import pathlib
import json
import datetime
from dotenv import load_dotenv
import sqlite3 as _sqlite3

# ── Heartbeat 설정 ────────────────────────────────────
HB_FILE      = "/tmp/hb_sbot"          # heartbeat 파일
API_FAIL_MAX = 3                        # API 연속 실패 허용 횟수

from common_utils  import (
    now_kst, now_hhmm, now_hms, today_str,
    is_weekend, safe_int, safe_float,
    read_state, write_state, update_state,
    fmt_won, fmt_pct,
    extract_claude_text,
    check_api_health,
)
from kis_api       import KisAPI
from notifier      import Notifier
from sbot_strategy import SwingStrategy
from sbot_analyzer import SwingAnalyzer
from sbot_db       import SwingDB
from risk_manager  import RiskManager
import candidate_pool as _cpool   # sbot×sbo2 통합 — 모멘텀/추세/완화/유튜브 소스
try:
    from account_sync import sync_positions as _sync_positions
except ImportError:
    _sync_positions = None
    print("⚠️ account_sync 없음 → DB 정합성 체크 비활성")
load_dotenv('/home/free4tak/k-bot/stock_bot/.env')
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
SECTOR_MONITOR_DB = '/home/free4tak/k-bot/stock_bot/sector_monitor.db'

# ============================================================
# sbot 전용 — 테마 지속성 + 군집도 필터 (5분 캐시)
# ============================================================
_swing_theme_cache: dict = {}
_swing_theme_ts: float = 0.0

def get_swing_theme_bonus(code: str, theme_group_map: dict) -> tuple:
    """
    sbot 전용 테마 가산점.
    ★ 분석 결과 기반:
      3일 이상 강세 + 군집도 70%↑ → +10점 (스윙 적합 테마)
      3일 이상 강세 + 군집도 50%↑ → +5점
    반환: (보너스점수, 이유)
    """
    global _swing_theme_cache, _swing_theme_ts
    import time as _t
    import os as _os

    if not _os.path.exists(SECTOR_MONITOR_DB):
        return 0, ""

    # 5분 캐시 갱신
    if _t.time() - _swing_theme_ts > 300:
        try:
            conn = _sqlite3.connect(SECTOR_MONITOR_DB, timeout=3)
            conn.execute("PRAGMA query_only = ON")
            rows = conn.execute("""
                SELECT theme_nm,
                       COUNT(DISTINCT date(ts)) as days,
                       AVG(CAST(rising_num AS REAL)/total_num*100) as cluster,
                       AVG(trde_amt) as trde
                FROM sector_flow
                WHERE flu_rt > 0.5
                  AND ts >= datetime('now', 'localtime', '-7 days')
                  AND total_num > 0
                GROUP BY theme_nm
                HAVING days >= 3
                ORDER BY days DESC, cluster DESC
            """).fetchall()
            conn.close()
            _swing_theme_cache = {}
            for theme_nm, days, cluster, trde in rows:
                if cluster >= 70 and trde >= 500:
                    _swing_theme_cache[theme_nm] = (10, f"스윙테마({days}일강세,군집{cluster:.0f}%)")
                elif cluster >= 50:
                    _swing_theme_cache[theme_nm] = (5, f"스윙테마({days}일강세,군집{cluster:.0f}%)")
            _swing_theme_ts = _t.time()
            print(f"📊 스윙 테마 캐시 갱신: {len(_swing_theme_cache)}개")
        except Exception as e:
            print(f"⚠️ 스윙 테마 조회 오류: {e}")
            return 0, ""

    # stock_momentum DB에서 종목의 최근 테마 확인
    try:
        conn = _sqlite3.connect(SECTOR_MONITOR_DB, timeout=3)
        conn.execute("PRAGMA query_only = ON")
        row = conn.execute("""
            SELECT theme_nm
            FROM stock_momentum
            WHERE code = ?
              AND ts >= datetime('now', 'localtime', '-30 minutes')
            ORDER BY ts DESC
            LIMIT 1
        """, (code,)).fetchone()
        conn.close()
        if row:
            theme_nm = row[0]
            if theme_nm in _swing_theme_cache:
                bonus, reason = _swing_theme_cache[theme_nm]
                return bonus, reason
    except Exception as e:
        print(f"⚠️ 종목 테마 조회 오류 {code}: {e}")
    return 0, ""


# ============================================================
# 상수 (튜닝 포인트)
# ============================================================
# ★ 2026-09-29: sbot×sbo2 통합(대장 결정) — sbot이 생존봇, sbo2 계좌/자본
#   흡수. 6슬롯×약165만원, 시드머니 목표 1,000만원(SEED_MONEY_TARGET,
#   코드로 강제하진 않음 — 실계좌 잔액이 실제 제약). 종목풀도 sbot 원천
#   (KIS new그룹) + sbo2 원천(모멘텀/추세/완화/유튜브) + S7을 전부
#   하나의 점수순 통합풀로 병합(core/candidate_pool.py 참고). 키움
#   조건검색 소스는 완전 제거(아래 SLOT 상수 정의부 참고).
MAX_POSITIONS    = 6              # 4→6 (통합 후 슬롯구조, 대장 결정)
SEED_MONEY_TARGET = 10_000_000    # 문서용 — 코드에서 강제하지 않음, 실계좌 잔액이 실제 제약

# ★ 대형주 급락매수(S7) — 2026-09-29부로 전용슬롯/전용예산 폐지, 통합
#   후보풀의 한 소스(SLOT_S7)로 편입되어 나머지 슬롯과 동일하게 경쟁
#   (대장 결정 — "S7용 슬롯등 특정 슬롯은 없애고 6종목 풀로 가자").
#   MEGA_CAP_CODES/DROP_THRESHOLD/LOOKBACK_DAYS는 감지 조건으로 계속
#   사용, MEGA_CAP_BUY_AMT/CHECK_INTERVAL은 폐지(사이징은 다른 신규
#   슬롯과 동일하게 BUY_1ST_AMT_BASE 고정금액, 감지주기는 캐시로 대체).
MEGA_CAP_CODES = {
    "005930": "삼성전자",
    "000660": "SK하이닉스",
    "402340": "SK스퀘어",
    "005935": "삼성전자우",
    "009150": "삼성전기",
    "032830": "삼성생명",
    "028260": "삼성물산",
    "005380": "현대차",
}
MEGA_CAP_DROP_THRESHOLD = -0.10   # 10일 최고가 대비 -10%
MEGA_CAP_LOOKBACK_DAYS  = 10
MEGA_CAP_REFRESH_SEC    = 1800    # S7 후보 재조회 캐시(30분) — 8종목 OHLC 매루프 조회 방지
BUY_1ST_AMT_BASE = 1_650_000    # 1차 매수 기본 금액 (2026-09-29: 150만→165만,
                                 # sbot×sbo2 통합 6슬롯 구조 반영. 켈리+ATR로
                                 # 이 기준값에서 유동적으로 조정됨(new 슬롯만))
# ★ 2026-09-29: 대장 지적 — sbo2 자금이체 전 현금이 쪼들릴 때, 고정금액
#   슬롯(모멘텀/추세/완화/유튜브/S7)이 MIN_ANALYSIS_CASH(20만원)만 넘으면
#   남은 푼돈 그대로 사버려서 목표(165만원)의 13%짜리 스크랩 포지션(세방
#   21만원 매수)이 슬롯 하나를 낭비하는 문제 발견. 고정금액 슬롯은 목표
#   금액의 이 비율 미만이면 그냥 패스(다음 루프에 현금 늘면 재시도) —
#   켈리기반 KISNEW 슬롯은 원래도 가변사이징이 의도된 설계라 대상 아님.
FLAT_BUY_MIN_RATIO = 0.5

# ── 통합 후보풀 슬롯 상수 ────────────────────────────────────
# sbot 자체 소스(KIS new그룹/S7) + core/candidate_pool.py의 sbo2 원천
# 소스(momentum/trend/light/youtube)를 합쳐 하나의 점수순 랭킹으로
# 매수 우선순위를 정한다.
# ★ 2026-09-29: 키움 조건검색 소스(SLOT_KIWOOM) 완전 제거(대장 결정 —
#   "키움은 다 빼버리고 SBOT은 한투로만 가자. 키움은 단타봇용하고
#   다른 우리가 추출하는 것으로 사용하자"). sbot은 이제 KIS(한투) API만
#   사용 — 키움은 이 봇에서 완전히 빠지고 향후 별도 단타봇 몫으로 남김.
SLOT_KISNEW = "kisnew"     # KIS "new" 관심그룹
SLOT_S7     = "s7"         # 대형주 급락매수(S7)
SLOT_LABEL = {
    SLOT_KISNEW: "KIS관심그룹",
    SLOT_S7:     "S7급락매수",
}
# ★ 추세(SLOT_TREND)는 실측 평균(~112)이 자체 경쟁력 있어 플로어 없음
#   (sbo2 2026-09-25 기준과 동일). new그룹은 BUY_SCORE_ENTER(85) 사전
#   게이트를 통과한 후보만 여기 들어오므로, 그 후보들도 다른 슬롯과
#   동등하게 경쟁하도록 110 플로어 적용(대장 결정 — "85+ 통과후 110
#   플로어 적용").
RANK_SCORE_FLOOR = {
    "momentum":  110,   # candidate_pool.SLOT_MOMENTUM
    "light":     110,   # candidate_pool.SLOT_LIGHT
    "youtube":   110,   # candidate_pool.SLOT_YOUTUBE
    SLOT_KISNEW: 110,
    SLOT_S7:     110,
}
# ★ 1차 익절 후 슬롯 반환 보너스 적용 현금 하한선 — 기존 100만원에서
#   50만원으로 완화(대장 지정 — 슬롯이 165만원으로 커진 것과 별개로
#   적은 잔액도 기회를 놓치지 않게).
BONUS_SLOT_MIN_CASH = 500_000
# ★ 2026-07-03: 주문가능금액이 이 밑이면 신규 후보 분석 자체를 건너뜀
#   (sbo2의 MIN_BUY_CHECK_CASH와 동일 목적 — 슬롯은 남아도 살 돈이
#   없으면 종목풀 전체를 API로 조회/분석할 필요가 없음. 이 분석
#   폭주가 KIS "초당 거래건수 초과" 재시작의 원인 중 하나였음)
MIN_ANALYSIS_CASH = 200_000
BUY_SCORE_MIN    = 45             # 후보 최소 점수
# ★ 2026-08-18: 85→70 (사용자 지적 — 슬롯이 비어도 매수가 거의 안 나옴).
#   원인 확인: 최근 실거래 AI 스코어링 300건 중 85점 이상은 5건(1.7%)뿐,
#   평균 57.9점 — 85점은 사실상 상위 1.7%짜리 완벽한 신호만 통과시키는
#   기준이었음(07-17 백테스트 근거였던 85점은 rule_proxy 스코어 기준이라
#   실제 라이브 AI 스코어링 분포와는 다름). 70점이면 상위 13%(300건 중
#   39건) 통과 — 거래량 확보하면서도 평균(57.9점)보단 확실히 위.
BUY_SCORE_ENTER  = 85             # 매수 진입 기준점 (★2026-09-19: 백테스트 재확인 — 75→85가 +28.71%→+41.58%로 뚜렷이 우수, 07/17에 이미 한번 올렸던 값이 어느 시점에 70으로 되돌아가있던 것 재수정)
LOOP_SLEEP       = 60
POOL_SIZE        = 100
# ★ 2026-09-03: 매수 직후 한투 잔고 정산지연 동안 수동매도 오판 방지
#   (sbo2의 BUY_SYNC_GUARD_SEC와 동일 값/의도)
BUY_SYNC_GUARD_SEC = 90
# ★ 2026-10-06: 매도 직후 잔고에 남아 보이는 종목을 "정산지연 잔재"로 보고
#   재입양을 막는 시간(기존엔 당일 내내 막아서, 실제로 안 팔린 손절 종목이
#   손절선 없이 하루를 보냈음). 이 시간이 지나도 잔고에 있으면 미체결로 보고
#   이전 peak_tracker 그대로 다시 감시 + critical 알림.
SELL_SYNC_GUARD_SEC = 300
# ★ 2026-10-06: 휴장/장외 대기를 300초로 자면 하트비트 워치독 기준(300초
#   무갱신)과 맞물려 가끔 재시작당함 — 기준보다 충분히 짧게.
IDLE_SLEEP_SEC = 60

REG_MARKET_START = "0900"
REG_MARKET_END   = "1530"
# ★ 2026-10-08 대장 지정 — "정규장에서만 처리하자"(daybot에서 먼저 발견 —
#   장종료동시마감 15:20~15:30엔 시장가주문이 "129 주문불가시간"으로 거부,
#   애프터마켓도 AFTERHOURS_ORD_DVSN 코드가 거부돼 재시도만 쌓임). sbot도
#   같은 core/kis_api.py::sell()을 쓰므로 동일 위험 — 15:20 이후 매도
#   시도 자체를 보류, 다음 정규장까지 대기.
REGULAR_SELL_END_TIME = "1520"
# ★ 2026-08-21: 09:10→09:20 — 시장 쏠림 안전check(intelligence/
#   market_safety_stop.py)가 09:19에 시장폭(breadth_ratio) 판단해서
#   위험하면 매수 시작 전에 봇을 정지시키는데, 그 판단 자체가 개장
#   직후 5분 데이터로는 신뢰도가 낮아 09:19까지 데이터를 모아야 함
#   (사용자 결정 — S7 대형주만 오르고 나머지는 다 빠지는 쏠림장을
#   겪은 뒤 안전장치로 도입).
BUY_START_TIME   = "0920"         # ★ 09:20 이후 매수
# ★ 2026-09-19: KRX가 NXT처럼 20시까지 연장돼서(대장 확인) 매수도
#   정규장(15:30) 마감 이후 계속 열어두도록 변경 — 08:00~09:20은 기존대로
#   매도체크만, 09:20~20:00은 매수/매도 둘 다.
BUY_END_TIME     = "2000"         # ★ 09:20~20:00 매수 가능
SELL_CHECK_START = "0800"         # ★ 08:00부터 매도 체크
SELL_CHECK_END   = "2000"         # ★ 20:00까지 매도 체크
SLEEP_INTERVAL   = 60

# ★ 2026-10-06 대장 지정 — 1차매수가 예산(목표금액)을 다 못 채우고
#   끝나는 경우(주문가능금액 부족/고가종목 호가단위 때문에 1주만 사고
#   끝나는 등, 058610/011070 사례로 발견)가 있어, 정규장 마감 후
#   애프터마켓 시간대에 한 번 더 확인해서 목표금액까지 보충매수한다.
#   가격이 매수가보다 오르면(추격매수 위험) 건너뛴다 — "동일가나
#   이하가격에서만 재매수"(대장 지정).
EVENING_TOPUP_START       = "1530"   # 정규장 마감 이후부터
EVENING_TOPUP_MIN_FILL_RATIO  = 0.7  # 목표금액의 70% 미만이면 "미달"로 간주

# 약세장 방어
MARKET_WEAK_THRESH = -2.0   # -1.5%→-2.0% 완화 (nbot과 통일)
MARKET_STOP_THRESH = -4.5   # -3%→-4.5% 완화 (nbot과 통일)
MAX_DAILY_LOSS     = 5
# 종목 기준
MKT_CAP_MIN = 10000     # 1조원 (스윙은 대형주)
MKT_CAP_MAX = 100000000    # 제외 없음
MIN_PRICE   = 5000
MAX_PRICE   = 3_000_000

BOT_STATE_FILE = "sbot_state.json"


# ============================================================
# 상태 파일 헬퍼
# ============================================================
def _read_state() -> dict:
    return read_state(BOT_STATE_FILE, default={
        "paused":      False,
        "score_enter": BUY_SCORE_ENTER,
        "pending_cmd": None,
        "cmd_result":  None,
    })

def _update_state(**kwargs):
    update_state(BOT_STATE_FILE, **kwargs)

def _write_cmd_result(result: str):
    _update_state(cmd_result=result, pending_cmd=None)

def _write_status(status: dict, peak_tracker: dict = None):
    # ★ 2026-09-03: 기존엔 "읽기 → 수정 → 전체 덮어쓰기"를 락 없이 수동으로
    #   해서, 이 사이에 키키가 pending_cmd를 쓰면 그 변경사항이 사라지는
    #   레이스컨디션이 있었음(재점검 리포트로 발견 — common_utils.py 자체
    #   주석에 이미 "!일시중단 명령 유실" 실사례가 남아있는 버그 클래스).
    #   _update_state()(파일락 보호)로 교체 — 여기서 안 건드리는 키(예:
    #   pending_cmd/cmd_result/paused)는 자동으로 안전하게 보존됨.
    kwargs = {"last_status": status, "last_update": now_hms()}
    # ★ peak_tracker 영속화 (2026-06-28 추가) — 재시작 시 손절가/목표가/
    #   stage/buy_date가 전부 초기화되던 문제 방지. None이 아닐 때만 갱신
    #   (호출하지 않는 다른 경로에서 값이 날아가지 않도록 보호).
    if peak_tracker is not None:
        kwargs["peak_tracker"] = peak_tracker
    _update_state(**kwargs)


# ============================================================
# 메인 봇 클래스
# ============================================================
class SBot:
    """스윙봇 본체."""

    def __init__(self):
        print("🚀 [영암9 SWING] 스윙봇 가동")

        # ── KIS API: 별도 계좌(KIS_*2 환경변수) ──────────────
        self.api = KisAPI(
            appkey=os.getenv("KIS_APPKEY2"),
            secret=os.getenv("KIS_SECRET2"),
            cano  =os.getenv("KIS_CANO2"),
            acnt  =os.getenv("KIS_ACNT_PRDT_CD2"),
        )
        self.notifier  = Notifier(name="sbot")
        self.strategy  = SwingStrategy()
        self.ai        = SwingAnalyzer()
        self.db        = SwingDB()
        self.risk      = RiskManager(
            base_buy_amt         = BUY_1ST_AMT_BASE,
            max_daily_loss_count = MAX_DAILY_LOSS,
        )

        self.ai.init_db()
        self.db.init_db()

        # ── 거래 상태 ─────────────────────────────────────
        self.positions      = {}
        self._pending_orders = {}   # 미체결 주문 추적
        self.score_cache    = {}
        self.buy_context    = {}
        self.peak_tracker   = {}
        self.sold_today     = {}
        self.code_name_map  = {}
        self.atr_cache      = {}
        # ★ 2026-09-03: 재점검 리포트로 발견 — sbo2엔 있는 매수직후 동기화
        #   보호(BUY_SYNC_GUARD)가 sbot엔 없어서, 한투 정산지연으로 방금
        #   매수한 종목이 잔고조회에 아직 안 잡히면 바로 "수동매도"로
        #   오판해 포지션을 지워버리는 위험이 있었음(sbo2에서 올해 3번
        #   실전으로 겪은 것과 같은 버그 클래스). sbo2의 _buy_sync_guard
        #   패턴을 이식.
        self._buy_sync_guard = {}   # {code: 매수시각(epoch)}
        self._recent_sells   = {}   # {code: {"ts", "tracker", "reason"}} — SELL_SYNC_GUARD_SEC 참고
        self.api_fail_count = 0    # ★ API 연속 실패 카운터
        self._last_psbl_cash = 0   # ★ 2026-10-06: 상태파일 표시용(키키 !상태 "주문가능")

        # ── 메모리 캐시 ─────────────────────────────────
        self._tech_cache = {}
        self._flow_cache = {}

        # ── 일일 상태 ─────────────────────────────────────
        self._sold_today_date  = today_str()
        self._holiday_checked  = ""
        self._is_holiday       = False
        self._ws_paused        = False  # ★ 2026-10-06 — 주말/휴장일/장외엔 웹소켓도 같이 쉼(daybot과 동일 패턴)
        self._is_paused        = False

        # ── 시장 상태 ─────────────────────────────────────
        self.market_status     = "normal"
        # ★ 2026-06-30: 웹소켓 실제 연동 — 매 루프(60초마다) REST API로
        #   잔고/예수금을 조회하던 것을 체결통보 기반 실시간 갱신으로 대체.
        #   sbot이 분석하는 종목 풀(보통 50개 안팎) × 시세/호가 조회와
        #   합쳐져 "API 호출빈도 초과" watchdog 재시작이 빈번했는데, 그중
        #   잔고/예수금 조회(매 루프 최소 2회) 비중을 없애는 게 목적.
        #   기존엔 self._ws가 항상 None으로만 초기화되고 실제로 생성되는
        #   코드가 없어 1391번째 줄의 웹소켓 우선 사용 분기가 죽은 코드였음.
        try:
            from kis_websocket import KisWebSocket
            self._ws = KisWebSocket(
                appkey=os.getenv("KIS_APPKEY2"),
                secret=os.getenv("KIS_SECRET2"),
                cano  =os.getenv("KIS_CANO2"),
                acnt  =os.getenv("KIS_ACNT_PRDT_CD2"),
            )
            self._ws.start()
        except Exception as e:
            print(f"⚠️ 웹소켓 초기화 실패 — REST API 폴백 모드: {e}")
            self._ws = None
        self._kospi_low         = 0.0   # ★ 코스피 최저점 추적
        self._rebound_count     = 0     # ★ 연속 반등 횟수
        self._prefer_kosdaq     = False  # ★ 코스닥 강세 시 우선
        self.market_rate       = 0.0
        self.daily_loss_count  = 0
        self.new_codes_list    = []
        self.code_tag_map      = {}   # {code: 검색식명} buy_tag 추적용
        self._last_market_check = 0
        self._last_megacap_check = 0
        self._megacap_cache      = []    # S7 후보 캐시(30분)
        # ★ 2026-09-29: sbot×sbo2 통합 — 모멘텀/추세/완화/유튜브 후보풀
        #   캐시(sbo2._cand_date/self.candidates 패턴 포팅, core/
        #   candidate_pool.py의 refresh_*() 함수와 짝을 이룸)
        self._pool_candidates    = []
        self._pool_cand_date     = ""

    # ============================================================
    # 알림
    # ============================================================
    def _notify(self, msg: str, critical: bool = False):
        self.notifier.send(f"[SBOT] {msg}", critical=critical)

    def _name(self, code: str) -> str:
        return self.code_name_map.get(code, code)

    # ============================================================
    # 시장 상태
    # ============================================================
    def _check_opening_crash(self, now_t: str) -> bool:
        """장 초반 급락 감지 (09:00~09:20, -3% 이하 + 계속 하락)."""
        if now_t > "0920":
            return False
        kospi = self.market_rate
        if kospi > -3.0:
            return False
        if not hasattr(self, '_prev_kospi'):
            self._prev_kospi = kospi
            return False
        is_falling = kospi < self._prev_kospi
        self._prev_kospi = kospi
        if is_falling:
            print(f"🚨 [SWING] 장 초반 급락! 코스피:{kospi:+.2f}% → stop 강제 전환")
            return True
        return False

    def _update_market_status(self):
        idx   = self.api.get_market_index()
        kospi = idx.get("kospi", 0.0)
        if kospi == 0.0:
            print(f"⚠️ 시장지수 조회 실패 — 기존 유지: {self.market_status}")
            return
        self.market_rate = kospi
        # ★ 2026-10-06: stop모드 반등판정(케이스1/2/3)이 self.kosdaq_rate를 읽는데
        #   설정하는 곳이 없어 항상 0.0 → 폭락장에도 늘 "코스닥선방"으로 판정됐음
        self.kosdaq_rate = idx.get("kosdaq", 0.0)

        if   kospi <= MARKET_STOP_THRESH: status = "stop"
        elif kospi <= MARKET_WEAK_THRESH: status = "weak"
        else:                             status = "normal"

        if status != self.market_status:
            self._notify(
                f"시장상태 변경: {self.market_status}→{status} | "
                f"코스피:{kospi:+.2f}%",
                critical=(status == "stop"),
            )
        self.market_status = status
        print(f"📊 시장: {status} | 코스피:{kospi:+.2f}%")

    # ============================================================
    # new 그룹 종목 조회
    # ============================================================
    def _load_new_codes(self):
        """한투 관심그룹 'new'에서 신규 추천 종목 로딩"""
        hts_id = os.getenv("KIS_HTS_ID2", os.getenv("KIS_HTS_ID", ""))
        if not hts_id:
            return
        groups = self.api.get_watchlist_groups(hts_id)
        target = next(
            ((gc, gn) for gc, gn in groups.items()
             if gn.lower() in ("new", "신규추천", "신규", "new추천")),
            None,
        )
        if not target:
            print("  ⚠️ 'new' 관심그룹 없음")
            return
        grp_code, grp_name = target
        print(f"  🆕 new그룹 발견: [{grp_code}]{grp_name}")
        stocks = self.api.get_watchlist_stocks(grp_code, hts_id, self.code_name_map)
        self.new_codes_list = [c for c, _ in stocks]
        print(f"  🆕 new그룹 종목: {len(self.new_codes_list)}개")

    # ============================================================
    # 종목 풀 조회
    # ============================================================
    def _get_pool(self) -> list:
        """KIS 'new' 관심그룹 종목만 사용.
        ★ 2026-09-29: sbot×sbo2 통합(대장 결정) — "키움은 다 빼버리고
          SBOT은 한투로만 가자. 키움은 단타봇용하고 다른 우리가 추출하는
          것으로 사용하자." 기존 키움 조건검색("주도주검색식3") 소스를
          완전 제거, KIS 'new' 관심그룹(SLOT_KISNEW)만 남김. 부수효과:
          이 조건검색식을 sbo2도 과거에 같이 썼다가 동시호출 API충돌로
          09-19에 sbo2 쪽만 뺐던 적이 있는데, 이번에 sbot에서도 완전히
          빠지면서 그 충돌 우려 자체가 근본적으로 사라짐."""
        try:
            self._load_new_codes()
            for nc in self.new_codes_list:
                if nc not in self.code_tag_map:
                    self.code_tag_map[nc] = "expert"  # new그룹=전문가추천
            result = self.new_codes_list[:POOL_SIZE]
            print(f"🎯 스윙 종목 풀(KIS new): {len(result)}개")
            return result
        except Exception as e:
            print(f"⚠️ new 그룹 오류: {e}")
        return []

    # ============================================================
    # 매수 / 매도 (★ 핵심 개선)
    # ============================================================
    def _do_buy(self, code: str, price: float, amount: int,
                is_second: bool = False) -> bool:
        """
        매수 주문 실행. 주문 접수 여부를 bool로 반환(★ 2026-10-06 — 호출부가
        성공 여부를 몰라 실패해도 "매수 완료"를 보고/주문가능금액 차감을 못 했음).
        ★ 개선: 매수 직후 self.positions 즉시 반영 → 다음 매도 체크에서 누락 방지.
        """
        ok, orgno, odno, qty = self.api.buy(code, price, amount, self.code_name_map)
        if not ok or qty <= 0:
            return False
        # ★ 미체결 주문 등록 — ★ 2026-10-06: 2차매수 여부도 같이 기억(미체결
        #   취소 시 1차분까지 통째로 지우던 버그 방지, 아래 취소 루프 참고)
        self._pending_orders[code] = (orgno or "", odno or "", qty, is_second)
        # ★ 2026-09-03: 매수직후 동기화 보호 시작 — 아래 BUY_SYNC_GUARD_SEC 참고
        self._buy_sync_guard[code] = time.time()

        ctx = self.buy_context.get(code, {})
        # ★ 2026-06-29: qty는 더 이상 amount/price로 추정하지 않고 buy()가
        #   반환한 실제 주문 수량을 그대로 사용. 기존 추정 계산(호가단위
        #   보정 전 가격으로 나눔)은 buy() 내부의 정확한 계산(호가단위
        #   보정 + 수수료 반영)과 약 2% 확률로 어긋나, 실제보다 많은 qty가
        #   self.positions에 기록되어 매도 시 "주문가능수량 초과" 에러로
        #   이어질 수 있었음.

        # ★ 매수 직후 메모리 반영
        if not is_second:
            # ★ 2026-10-06 — target_amount(원래 의도했던 예산) 저장. 저녁
            #   보충매수(_run_evening_topup)가 실제 체결금액과 비교해
            #   미달분을 판단하는 데 씀. 재시작시 _sync_positions()가
            #   positions를 실계좌 기준으로 통째로 재구성해서 이 필드는
            #   없어질 수 있음(허용 — 저녁 보충매수는 soft-optimization이라
            #   재시작 사이에 놓쳐도 안전상 문제 없음).
            self.positions[code] = {"entry_price": price, "qty": qty, "buy_date": today_str(),
                                     "target_amount": amount}
        else:
            existing = self.positions.get(code, {"entry_price": price, "qty": 0})
            old_qty  = existing["qty"]
            old_avg  = existing["entry_price"]
            new_qty  = old_qty + qty
            if new_qty > 0:
                new_avg = (old_avg * old_qty + price * qty) / new_qty
                self.positions[code] = {"entry_price": new_avg, "qty": new_qty}

        tag = " 🆕new" if code in self.new_codes_list else ""
        self._notify(
            f"🚀 매수 {code}({self._name(code)}) | {fmt_won(amount)} | "
            f"{price:,.0f}원 | {qty}주{tag}",
            critical=True,
        )

        # DB 저장
        # ★ 2026-09-29: sbot×sbo2 통합 — atr_val_at_entry 포렌식 컬럼
        #   기록(sbo2_trades.db와 동일 목적). grade는 buy_tag와 동일값이라
        #   save_buy() 안에서 buy_tag로 자동 폴백(명시 전달 불필요).
        _atr_rate_for_db = self._get_atr_rate(code)
        _atr_val_for_db  = round(price * _atr_rate_for_db, 2) if _atr_rate_for_db else 0.0
        # ★ 2026-10-06: 2차매수는 기존 미청산 행에 합산(sbot_db.add_to_open_buy 참고)
        merged = is_second and self.db.add_to_open_buy(code, price, qty)
        if not merged:
            self.db.save_buy(
                code      = code,
                buy_price = price,
                qty       = qty,
                ai_score  = ctx.get("ai_score", 0),
                ai_reason = ctx.get("ai_reason", ""),
                stock_name= self._name(code),
                buy_tag   = self.code_tag_map.get(code, "unknown"),  # ★ 검색식명/슬롯출처
                atr_val_at_entry = _atr_val_for_db,
            )

        # ★ master_positions 등록
        # ★ 2026-07-06: 2차매수(is_second=True) 시 entry_price/qty에 이번
        #   체결분(price/qty)만 넘겨서, self.positions[code]엔 정확히
        #   평균단가/합산수량이 반영되는데도 master_db엔 2차매수 가격이
        #   전체 진입가인 것처럼 덮어써지는 버그가 있었음 (HPSP가 실제
        #   -23.9%인데 master_db엔 entry_price가 잘못 저장돼 -47.2%로
        #   보이는 사고로 발견됨). self.positions[code]의 확정된 값을
        #   그대로 사용하도록 수정.
        if _master_upsert:
            try:
                ctx2 = self.buy_context.get(code, {})
                _pos_now = self.positions.get(code, {"entry_price": price, "qty": qty})
                _master_upsert(
                    bot_type      = 'sbot',
                    code          = code,
                    stock_name    = self._name(code),
                    entry_price   = _pos_now["entry_price"],
                    current_price = price,
                    qty           = _pos_now["qty"],
                    buy_time      = ctx2.get('buy_time', ''),
                    buy_tag       = ctx2.get('buy_tag', ''),
                    ai_score      = ctx2.get('ai_score', 0),
                )
            except Exception as _e:
                print(f'⚠️ master_positions upsert 오류: {_e}')
        return True

    def _do_sell(self, code: str, qty: int, reason: str, sell_price: float) -> bool:
        """
        매도 주문 실행.
        ★ 개선: 부분 매도 시 buy_context를 절대 삭제하지 않음 (전량일 때만).
        ★ 2026-09-04: 반환값이 없어(항상 암묵적 None) sbot_strategy.py의
        check_sell()이 목표1 매도 성공/실패를 구분 못 하고 무조건 stage를
        올리던 버그(sbo2에서 09-03에 먼저 발견/수정됐던 것과 동일 클래스,
        당시 외부 리포트가 sbot에서는 이 항목을 안 짚어 놓쳤었음)를
        고치기 위해 bool 반환 추가.
        """
        if qty <= 0:
            return False

        # ★ 2026-10-08 대장 지정 — 정규장 끝(15:20) 이후엔 매도 시도 자체를
        #   보류(daybot과 동일 이유 — 위 REGULAR_SELL_END_TIME 주석 참고).
        if now_hhmm() >= REGULAR_SELL_END_TIME:
            return False

        # ★ 2026-10-08 — 위 REGULAR_SELL_END_TIME(15:20) 게이트로 이 시점엔
        #   항상 정규장이라 애프터장 분기(2026-10-06에 추가했던 "price=0
        #   합성가")는 더 이상 도달 못 함 — 그냥 지정가 그대로 사용.
        ok = self.api.sell(code, qty, price=int(sell_price))
        if not ok:
            return False

        # 전량/부분 매도 판단
        current_pos  = self.positions.get(code, {})
        held_qty     = current_pos.get("qty", 0)
        is_full_sell = (qty >= held_qty)

        # ★ 2026-09-21: reason 문자열("손절"/"본절") 매칭 대신 실제 손익
        #   부호로 판단(대장 지적 — "손절했거나 수익을 못내고 팔았을경우만
        #   재매수 금지, 소규모 익절이라도 수익나면 재매수금지 안 걸리게").
        #   문자열 매칭은 "미너비니200일이탈" 등 손실 사유를 놓칠 수 있었음.
        entry_price = current_pos.get("entry_price", sell_price)
        is_loss = sell_price <= entry_price
        emoji   = "💔" if is_loss else "💰"
        self._notify(
            f"{emoji} 매도 {code}({self._name(code)}) | {reason} | {qty}주",
            critical=True,
        )

        # DB 저장
        # ★ 2026-09-29: sbot×sbo2 통합 — stage_reached 포렌식 컬럼 기록
        #   (sbo2_trades.db와 동일 목적, 매도 시점까지 도달한 목표단계).
        _stage_for_db = self.peak_tracker.get(code, {}).get("stage", 0)
        self.db.save_sell(code, sell_price, reason,
                         sold_qty=0 if is_full_sell else qty,
                         stage_reached=_stage_for_db)
        # ★ master_trades 기록
        if _master_record:  # 전량 + 분할매도 모두 기록
            ctx = self.buy_context.get(code, {})
            try:
                import datetime as _dt
                buy_t  = ctx.get("buy_time", "")
                sell_t = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                hold_d = 0
                if buy_t:
                    try:
                        bd = _dt.datetime.fromisoformat(buy_t).date()
                        hold_d = (_dt.date.today() - bd).days
                    except Exception:
                        pass
                _master_record(
                    bot_type="sbot", code=code,
                    stock_name=self._name(code),
                    buy_price=current_pos.get("entry_price", sell_price),
                    sell_price=sell_price, qty=qty,
                    sell_reason=reason,
                    buy_time=buy_t, sell_time=sell_t,
                    ai_score=ctx.get("ai_score"),
                    ai_reason=ctx.get("ai_reason", ""),
                    market_status=self.market_status,
                    hold_days=hold_d,
                    is_partial=not is_full_sell,
                )
            except Exception as _e:
                print(f"⚠️ master_db 기록 오류: {_e}")

        # ★ 핵심: 전량 매도일 때만 컨텍스트 정리
        if is_full_sell:
            # ★ 2026-10-06: 접수≠체결 — SELL_SYNC_GUARD_SEC 뒤에도 잔고에 남아
            #   있으면 이 tracker로 다시 감시(메인루프 잔고동기화 참고)
            self._recent_sells[code] = {
                "ts": time.time(),
                "tracker": dict(self.peak_tracker.get(code, {})),
                "reason": reason,
            }
            self.buy_context.pop(code, None)
            self.positions.pop(code, None)
            # ★ master_positions 삭제
            if _master_remove:
                _master_remove("sbot", code)
        else:
            # 부분 매도: 잔량만 갱신 (entry_price 유지)
            remain = held_qty - qty
            self.positions[code] = {
                "entry_price": current_pos.get("entry_price", sell_price),
                "qty":         remain,
            }
            # ★ peak_tracker 잔량 동기화
            if code in self.peak_tracker:
                self.peak_tracker[code]["remain_qty"] = remain
                print(f"🔄 peak_tracker 잔량 동기화: {code} → {remain}주")
            # ★ master_positions 잔량 갱신
            if _master_upsert:
                _master_upsert(
                    bot_type="sbot", code=code,
                    qty=remain,
                    stage=self.peak_tracker.get(code, {}).get("stage", 0),
                )

        # ★ 손실/본절(수익 없음)만 당일 재매수 금지 — 소규모 익절도 재진입 허용
        if is_loss:
            self.sold_today[code] = now_hms()
            print(f"🚫 [SWING] {code} 손실/본절 매도 → 당일 재매수 금지")

        # 상태 파일에도 sold_today 저장
        # ★ 2026-09-03: 락 없는 수동 read+write → 안전한 _update_state로 교체
        _update_state(sold_today=self.sold_today, sold_today_date=today_str())
        return True

    def _do_loss(self):
        """손절 카운터 +1"""
        self.daily_loss_count += 1
        print(f"📉 [SWING] 당일 손절 누적: {self.daily_loss_count}회")
        _update_state(daily_loss=self.daily_loss_count, loss_date=today_str())

    # ============================================================
    # ATR 계산 (스윙은 일봉 변동성)
    # ============================================================
    def _get_atr_rate(self, code: str) -> float:
        """ATR/현재가 비율. ★ 2026-09-04: sbo2와 공유되는
        risk_manager.RiskManager.get_atr_rate_cached()로 위임 —
        기존엔 실패/0도 성공과 똑같이 30분씩 캐싱해 일시적 조회실패가
        30분 내내 ATR=0으로 오인되는 버그가 있었음(sbo2는 08-10에
        이미 고쳤었는데 sbot엔 이식이 안 돼 있었음). 공유 헬퍼로
        이전하며 같이 해결."""
        return self.risk.get_atr_rate_cached(self.api, self.atr_cache, code)

    # ============================================================
    # peak_tracker 항목 생성 (★ 공통 헬퍼 — 2026-06-28 신규)
    # ============================================================
    def _make_peak_tracker_entry(self, entry_price: float,
                                 atr_rate: float = 0.0,
                                 buy_date: str = None,
                                 buy2_done: bool = False) -> dict:
        """
        peak_tracker[code]에 들어갈 딕셔너리를 항상 동일한 필드 구성으로 생성.

        ★ 배경: 과거에는 매수 경로(일반매수/수동매수/5대장주)마다 peak_tracker를
        직접 만들어 일부 필드(buy_date, stop_price, target1 등)가 누락되는 경우가
        있었음. 누락 시:
          - buy_date 누락 → sbot_strategy.check_sell()의 25일 보유기한 매도가
            평생 작동하지 않음 (tracker가 이미 존재해 자동 채움 로직을 안 탐)
          - stop_price/target1/target_next 누락 → check_sell()에서 KeyError →
            그 종목 이후의 모든 보유종목 매도체크가 그 루프에서 스킵됨

        buy2_done: 물타기(2차매수) 허용 여부. 일반/수동매수는 False(물타기 허용),
                   5대장주처럼 추가매수를 안 쓰는 경로는 True로 호출.

        이 헬퍼 하나로 모든 매수 경로를 통일해 위 문제를 근본적으로 방지.
        """
        levels = self.strategy.calc_atr_levels(entry_price, atr_rate)
        return {
            "peak_rate":   0.0,
            "peak_price":  entry_price,
            "stage":       0,
            "buy2_done":   buy2_done,
            "buy1_price":  entry_price,
            "stop_price":  levels["stop_price"],
            "target1":     levels["target1"],
            "target_next": levels["target1"],
            "atr_val":     levels["atr_val"],
            "buy_date":    buy_date or today_str(),
        }

    # ============================================================
    # API 헬스체크 (연속 실패 시 재시작)
    # ============================================================
    def _check_api_health(self, success: bool):
        """API 호출 성공/실패 추적 — 연속 실패 시 재시작.
        ★ 2026-09-04: sbo2와 완전히 동일하던 로직을 common_utils 공유
        헬퍼로 통합."""
        self.api_fail_count = check_api_health(
            self.api_fail_count, success, self._notify, "[SWING]",
            max_fail=API_FAIL_MAX,
        )

    # ============================================================
    # 일일 초기화
    # ============================================================
    def _daily_reset(self, today: str):
        self.sold_today        = {}
        self._sold_today_date  = today
        self.daily_loss_count  = 0
        self.market_status     = "normal"
        self._tech_cache       = {}
        self._flow_cache       = {}
        self.new_codes_list    = []
        self.code_tag_map      = {}   # {code: 검색식명} buy_tag 추적용
        self.atr_cache         = {}
        self.api._mkt_cache    = {}
        _update_state(
            sold_today={}, sold_today_date=today,
            daily_loss=0, loss_date=today,
        )
        print("🔄 [SWING] 일일 초기화 완료")

    # ============================================================
    # 디스코드 명령 처리
    # ============================================================
    def _handle_pending_command(self, st: dict):
        pending = st.get("pending_cmd")
        if not pending:
            return

        cmd_type = pending.get("type")

        if cmd_type == "sell":
            sell_code = pending.get("code", "")
            if sell_code in self.positions:
                mdata   = self.api.get_market_data(sell_code)
                s_price = safe_float(mdata.get("stck_prpr", 0)) if mdata else 0
                # ★ 2026-10-06: 결과를 안 보고 항상 "완료"라고 답하던 문제 수정
                #   (이 명령은 주말/장외에도 처리돼 주문 거부가 흔함)
                ok = self._do_sell(
                    sell_code,
                    self.positions[sell_code]["qty"],
                    "즉시매도(AI비서)",
                    s_price,
                )
                if ok:
                    self.peak_tracker.pop(sell_code, None)
                    _write_cmd_result(f"✅ [SWING] {sell_code} 매도주문 접수 (잔고로 체결 재확인)")
                else:
                    _write_cmd_result(f"❌ [SWING] {sell_code} 매도주문 실패 — 장 운영시간/주문가능수량 확인 필요")
            else:
                _write_cmd_result(f"⚠️ {sell_code} 보유 중이 아님")

        elif cmd_type == "buy":
            buy_code = pending.get("code", "")
            buy_qty  = safe_int(pending.get("qty", 0))
            if buy_qty <= 0:
                _write_cmd_result("⚠️ 수량 오류")
                return
            mdata = self.api.get_market_data(buy_code)
            if not mdata:
                _write_cmd_result(f"⚠️ {buy_code} 시세 조회 실패")
                return
            cur = safe_float(mdata.get("stck_prpr", 0))
            if cur <= 0:
                _write_cmd_result(f"⚠️ {buy_code} 현재가 없음")
                return

            self.buy_context[buy_code] = {
                "ai_score": 0, "ai_reason": "수동매수",
                "stock_name": self._name(buy_code),
            }
            if not self._do_buy(buy_code, cur, int(cur * buy_qty * 1.01)):
                _write_cmd_result(f"❌ [SWING] {buy_code} 매수주문 실패 — 장 운영시간/주문가능금액 확인 필요")
                return
            # ★ 공통 헬퍼로 통일 — stop_price/target1/atr_val/buy_date 등
            #   필수 필드 누락 방지 (과거엔 일부만 채워 다음 매도체크에서
            #   KeyError 발생 → 그 이후 보유종목 매도체크 전체가 스킵되는 버그)
            _atr_rate = self._get_atr_rate(buy_code)
            self.peak_tracker[buy_code] = self._make_peak_tracker_entry(
                entry_price=cur, atr_rate=_atr_rate,
            )
            _write_cmd_result(f"✅ [SWING] {buy_code} {buy_qty}주 매수주문 접수")

        elif cmd_type == "hold":
            # ★ 2026-08-30 신설 — 사용자 요청: 특정 종목 손절체크만 제외
            #   ("!h 종목명이나코드" → 홀드 설정, 트레일링/목표달성 로직은
            #   그대로 작동, 손절가 이탈만 무시). peak_tracker에 저장해야
            #   실계좌 동기화(positions.clear()+update)에도 안 날아감.
            hold_code = pending.get("code", "")
            hold_val  = pending.get("value", True)
            if hold_code in self.positions:
                self.peak_tracker.setdefault(hold_code, {})["hold"] = hold_val
                # ★ 2026-08-30: peak_tracker 영속화는 원래 루프 후반부
                #   _write_status() 호출 시점에만 이뤄지는데, 그 호출은
                #   장외/주말/휴장일 분기의 continue보다 뒤에 있어서 그
                #   시간대엔 아예 실행되지 않음 — 여기서 즉시 저장해야
                #   설정 직후 재시작해도 살아남음(사용자 실사례로 확인:
                #   주말에 설정한 hold가 재시작 후 사라져 있었음).
                _update_state(peak_tracker=self.peak_tracker)
                label = "설정" if hold_val else "해제"
                _write_cmd_result(
                    f"✅ [SWING] {hold_code} 홀드 {label} "
                    f"(손절체크 {'제외' if hold_val else '포함'}, 트레일링/목표는 그대로)"
                )
            else:
                _write_cmd_result(f"⚠️ {hold_code} 보유 중이 아님")

    # ============================================================
    # 한 종목 분석
    # ============================================================
    def _analyze_one_code(self, code: str) -> tuple:
        """한 종목 분석 → (data, rule_score) 반환. 부적격은 (None, 0)"""
        # ★ 병렬 API 호출 (시세 + 호가 동시 조회)
        from concurrent.futures import ThreadPoolExecutor
        basic = hoga_data = None
        with ThreadPoolExecutor(max_workers=2) as ex:
            f_basic = ex.submit(self.api.get_market_data, code)
            f_hoga  = ex.submit(self.api.get_hoga, code)
            basic     = f_basic.result()
            hoga_data = f_hoga.result()
        if not basic:
            return None, 0
        try:
            data = {
                "current_price": safe_float(basic.get("stck_prpr",  0)),
                "change_rate":   safe_float(basic.get("prdy_ctrt",  0)),
                "trading_value": safe_int(basic.get("acml_tr_pbmn", 0)) // 100_000_000,
                "volume":        safe_int(basic.get("acml_vol",     0)),
                "mkt_cap":       safe_int(basic.get("hts_avls",     0)),
                "stock_name":    basic.get("hts_kor_isnm", ""),
                "stck_hgpr":     safe_float(basic.get("stck_hgpr",  0)),
                # ★ VI 발동 상태 코드 (51=VI발동, 55=정상)
                "iscd_stat_cls_code": basic.get("iscd_stat_cls_code", "55"),
            }
            data.update(self.api.get_technical_indicators(code, self._tech_cache))
            data.update(self.api.get_investor_trend(code, self._flow_cache))
            # ★ 호가잔량 (병렬 조회 결과 적용)
            if hoga_data:
                data["total_ask_rsqn"] = hoga_data.get("total_ask_rsqn", 0)
                data["total_bid_rsqn"] = hoga_data.get("total_bid_rsqn", 0)
                data["ask_bid_ratio"]  = hoga_data.get("ask_bid_ratio", 0)

            is_new = code in self.new_codes_list

            # ── 기본 필터 ──────────────────────────────────
            if data["change_rate"] >= 29.5:
                print(" → 상한가"); return None, 0

            # ★ 매수 필터를 strategy 모듈에 위임
            passes, reason = self.strategy.passes_buy_filter(data, is_new=is_new)
            if not passes:
                print(f" → {reason}"); return None, 0

            if data["current_price"] < MIN_PRICE:
                print(" → 저가주"); return None, 0
            if data["current_price"] > MAX_PRICE:
                print(" → 고가"); return None, 0

            # 고점 대비 -5% 이상 하락 제외 (이미 꺾인 종목)
            hg  = data["stck_hgpr"]
            cur = data["current_price"]
            if hg > 0 and (cur - hg) / hg < -0.05:
                print(f" → 고점 대비 -5% 이상 하락"); return None, 0

            # 시총/거래대금 필터 (new는 면제)
            mkt_cap = data["mkt_cap"]
            if not is_new:
                if mkt_cap < MKT_CAP_MIN:
                    print(f" → 소형주({mkt_cap:,}억)"); return None, 0
                if mkt_cap > MKT_CAP_MAX:
                    print(f" → 초대형주({mkt_cap:,}억)"); return None, 0
                if data["trading_value"] < 100:
                    print(" → 거래대금 부족"); return None, 0

            rule_score = self.strategy.get_rule_score(data)
            print(f" → 룰:{rule_score}점" + (" 🆕" if is_new else ""))
            return data, rule_score
        except Exception as e:
            print(f" → 오류: {e}")
            return None, 0

    # ============================================================
    # 분석 + 매수 실행
    # ============================================================
    def _run_analysis(self, codes: list, now_t: str, score_enter: int,
                      psbl_cash: int):
        new_codes    = [c for c in codes if c not in self.score_cache]
        cached_codes = [c for c in codes if c in self.score_cache]
        print(f"\n🔄 [SWING] 분석: 신규 {len(new_codes)}개 | 캐시 {len(cached_codes)}개")

        # 1) 룰 점수 계산
        # ★ 2026-06-30: 종목당 2~4회 API 호출(시세/호가/수급)이 딜레이 없이
        #   연속 실행되면, 재시작 직후나 종목풀 대량교체 시(40개+ 신규)
        #   순식간에 100~200회가 몰려 한투 서버가 "초당 거래건수 초과"로
        #   거부하거나, 더 심하면 연결 자체를 강제로 끊어버리는
        #   (RemoteDisconnected) 사고가 실제로 발생함. 종목 간 짧은
        #   딜레이를 넣어 순간 호출량을 분산.
        # ★ 2026-07-01: 장 개장 직후(09:00~09:20)에 재시작이 가장 잦음 —
        #   이 시간대는 신규종목 분석이 몰리므로 딜레이를 0.15 → 0.3초로
        #   강화.
        _open_hour = now_t[:4] <= "0920"
        ANALYSIS_DELAY_SEC = 0.3 if _open_hour else 0.15
        if _open_hour and new_codes:
            print(f"   🕐 장 개장 시간대 — 분석 딜레이 {ANALYSIS_DELAY_SEC}초 적용 ({len(new_codes)}개)")
        rule_candidates = []
        for idx, code in enumerate(new_codes):
            print(f"🔎 분석 {idx+1}/{len(new_codes)}: {code}", end="")
            data, rule_score = self._analyze_one_code(code)
            if data is not None:
                rule_candidates.append((code, rule_score, data))
            if idx < len(new_codes) - 1:
                time.sleep(ANALYSIS_DELAY_SEC)

        # 2) 상위 10개 AI 분석
        rule_candidates.sort(key=lambda x: x[1], reverse=True)
        top_ai = rule_candidates[:10]
        rest   = rule_candidates[10:]

        print(f"\n🤖 AI 분석: {len(top_ai)}개")
        for code, rule_score, data in top_ai:
            ai_result = self.ai.analyze(code, data, self.new_codes_list)
            score     = ai_result["score"]
            reason    = ai_result["reason"]
            score, bonus = self.strategy.apply_new_bonus(code, score, self.new_codes_list)
            if bonus:
                reason = f"{reason} | {bonus}"
            # ★ 스윙 테마 지속성 가산점
            sw_bonus, sw_reason = get_swing_theme_bonus(code, {})
            if sw_bonus > 0:
                score = min(100, score + sw_bonus)
                reason = f"{reason} | {sw_reason}"
            print(f"   🧠 {code} | 룰:{rule_score}→AI:{score}점 | {reason}")
            data["ai_reason"] = reason
            self.score_cache[code] = (score, data)

        # 3) AI 분석 안 한 종목은 룰 점수 + new 가점만
        for code, rule_score, data in rest:
            score, bonus = self.strategy.apply_new_bonus(code, rule_score, self.new_codes_list)
            # ★ 스윙 테마 지속성 가산점
            sw_bonus, sw_reason = get_swing_theme_bonus(code, {})
            if sw_bonus > 0:
                score = min(100, score + sw_bonus)
                bonus = f"{bonus} | {sw_reason}" if bonus else sw_reason
            data["ai_reason"] = f"룰점수({rule_score})" + (f" | {bonus}" if bonus else "")
            self.score_cache[code] = (score, data)

        # 4) 캐시 정리
        pool_set = set(codes)
        for c in [c for c in list(self.score_cache) if c not in pool_set]:
            del self.score_cache[c]

        # 5) 매수 후보 + 시간대 보정
        candidates = []
        for code, (score, data) in self.score_cache.items():
            if score < BUY_SCORE_MIN:
                continue
            adjusted = score + self.risk.time_score_modifier(now_t)
            candidates.append((code, adjusted, data))

        def sort_key(x):
            code, _, d = x
            return (
                code in self.new_codes_list,
                not d.get("ai_reason", "").startswith("룰점수"),
                _,
            )
        candidates.sort(key=sort_key, reverse=True)
        top10 = candidates[:10]

        cached_codes_set = set(cached_codes)
        print(f"\n🔥 SWING TOP{len(top10)}:")
        for code, score, d in top10:
            tag = " 🆕" if code in self.new_codes_list else ""
            ct  = "📦" if code in cached_codes_set else "🆕"
            print(f"  {ct} {code}({self._name(code)}){tag} | "
                  f"{score}점 | {d.get('ai_reason','')}")

        # ★ 2026-10-06 — 미너비니 AI추천(실시간 데이터 검증 없이 AI
        #   기억에만 의존해 1종목 추천하던 기능) 완전 제거(대장 결정 —
        #   검증 안 된 추천으로 실거래 들어가는 환각 위험 > Haiku 비용
        #   절감 정도로는 못 바꿀 리스크). 기존 6)번 블록+_get_minervini_pick
        #   메서드 삭제.

        # 7) ★ 2026-09-29: 여기서 직접 매수실행하지 않고, 키움/new 통합
        #   후보(BUY_SCORE_ENTER 사전게이트 통과분만)를 정규화된 dict로
        #   반환 — 통합후보풀(_get_unified_candidates)이 모멘텀/추세/완화/
        #   유튜브/S7과 합쳐서 점수순 랭킹 후 한 번에 매수실행한다.
        return self._normalize_kisnew_candidates(top10, score_enter)

    def _normalize_kisnew_candidates(self, top10: list, score_enter: int) -> list:
        """(code, score, data) 튜플 리스트를 통합 후보풀 dict 포맷으로 변환,
        BUY_SCORE_ENTER 사전게이트(score_enter 미만 제외)를 여기서 적용.
        ★ 2026-09-29: 키움 소스 제거 이후 _get_pool()이 KIS new그룹만
        반환하므로 grade는 항상 SLOT_KISNEW."""
        normalized = []
        for code, score, data in top10:
            if score < score_enter:
                continue
            normalized.append({
                "code":     code,
                "name":     data.get("stock_name") or self._name(code),
                "grade":    SLOT_KISNEW,
                "score":    score,
                "curr":     data.get("current_price", 0),
                "is_new":   True,
                "ai_reason": data.get("ai_reason", ""),
                "raw_data": data,
            })
        return normalized

    def _get_megacap_candidates(self, psbl_cash: int) -> list:
        """S7(대형주 급락) 후보 생성 — 2026-09-29부로 전용슬롯/예산 폐지,
        통합 후보풀의 한 소스(SLOT_S7)로 편입. 30분 캐시(8종목 OHLC를
        매루프 조회하지 않도록)."""
        now = time.time()
        if now - self._last_megacap_check < MEGA_CAP_REFRESH_SEC:
            return self._megacap_cache
        self._last_megacap_check = now

        # 이미 S7 중 보유중인 종목이 있으면 후보 생성 스킵(과집중 방지)
        if any(c in self.positions for c in MEGA_CAP_CODES):
            self._megacap_cache = []
            return self._megacap_cache
        if psbl_cash < MIN_ANALYSIS_CASH:
            self._megacap_cache = []
            return self._megacap_cache

        drops = []
        for code, name in MEGA_CAP_CODES.items():
            try:
                ohlc = self.api.get_daily_ohlc(code, days=MEGA_CAP_LOOKBACK_DAYS)
                if not ohlc or len(ohlc) < 3:
                    continue
                highs = [c["high"] for c in ohlc if c.get("high", 0) > 0]
                if not highs:
                    continue
                recent_high = max(highs)
                mdata = self.api.get_market_data(code)
                if not mdata:
                    continue
                current = float(mdata.get("stck_prpr", 0))
                if current <= 0 or recent_high <= 0:
                    continue
                drop_rate = (current - recent_high) / recent_high
                if drop_rate <= MEGA_CAP_DROP_THRESHOLD:
                    drops.append((drop_rate, code, name, current))
            except Exception as e:
                print(f"⚠️ S7 {name} 조회 오류: {e}")
                continue

        if not drops:
            self._megacap_cache = []
            return self._megacap_cache

        # 가장 많이 빠진 종목 1개만 후보로
        drops.sort(key=lambda x: x[0])
        drop_rate, code, name, current = drops[0]
        self._megacap_cache = [{
            "code":      code,
            "name":      name,
            "grade":     SLOT_S7,
            "score":     75,   # candidate_pool의 모멘텀과 동급 취급
            "curr":      current,
            "is_new":    False,
            "ai_reason": f"S7급락매수(10일최고대비{drop_rate:+.1%})",
            "raw_data":  None,
        }]
        return self._megacap_cache

    def _normalize_pool_candidate(self, c: dict) -> dict:
        """candidate_pool(모멘텀/추세/완화/유튜브) 스타일 dict(name 키)를
        통합 후보풀 dict 포맷(code 키)으로 변환."""
        code = _cpool.get_stock_code(c["name"])
        return {
            "code":     code,
            "name":     c["name"],
            "grade":    c["grade"],
            "score":    c["score"],
            "curr":     c.get("curr", 0),
            "is_new":   False,
            "ai_reason": "/".join(c.get("themes", [])) if c.get("themes") else "",
            "raw_data": None,
        }

    def _rank_score(self, c: dict) -> float:
        return max(c["score"], RANK_SCORE_FLOOR.get(c["grade"], 0))

    def _refresh_pool_candidates(self):
        """모멘텀/추세/완화/유튜브 후보 캐시 갱신 — sbo2._refresh_candidates()
        패턴 포팅. 전체(추세/완화 포함)는 하루 1회, 모멘텀/유튜브는 API
        호출이 가벼워서 매루프 독립 갱신."""
        held_codes = set(self.positions.keys())
        held_names = {p.get("name", "") for p in self.positions.values()}
        # positions는 code 키인데 candidate_pool의 refresh 함수들은
        # sbo2 스타일로 {code_or_name: {"name":...}} 형태를 기대 — sbot의
        # positions는 name을 안 들고 있으므로 code_name_map으로 보강한
        # 임시 뷰를 만들어 전달.
        positions_view = {
            code: {"name": self.code_name_map.get(code, code)}
            for code in self.positions
        }

        self._pool_candidates, self._pool_cand_date, changed = _cpool.refresh_full_candidates(
            self._pool_candidates, self._pool_cand_date, positions_view, api=self.api,
        )
        self._pool_candidates, _ = _cpool.refresh_momentum_candidates(
            self._pool_candidates, positions_view,
        )
        self._pool_candidates, _ = _cpool.refresh_youtube_candidates(
            self._pool_candidates, positions_view, MAX_POSITIONS, api=self.api,
        )

    def _get_unified_candidates(self, now_t: str, score_enter: int, psbl_cash: int) -> list:
        """모든 소스(키움/new/모멘텀/추세/완화/유튜브/S7)를 하나의 점수순
        랭킹 리스트로 병합."""
        all_candidates = []

        codes = self._get_pool()
        if not codes:
            print("⚠️ 종목 풀 없음(키움/new)")
        else:
            kiwoom_kisnew = self._run_analysis(codes, now_t, score_enter, psbl_cash)
            if kiwoom_kisnew:
                all_candidates += kiwoom_kisnew

        self._refresh_pool_candidates()
        all_candidates += [self._normalize_pool_candidate(c) for c in self._pool_candidates]

        all_candidates += self._get_megacap_candidates(psbl_cash)

        # code 중복 제거(같은 종목이 여러 소스에서 나올 경우 더 높은
        # rank_score를 쓰는 쪽만 유지)
        best_by_code = {}
        for c in all_candidates:
            if not c.get("code"):
                continue
            code = c["code"]
            if code not in best_by_code or self._rank_score(c) > self._rank_score(best_by_code[code]):
                best_by_code[code] = c

        buyable = sorted(best_by_code.values(), key=self._rank_score, reverse=True)
        by_grade = {}
        for c in buyable:
            by_grade[c["grade"]] = by_grade.get(c["grade"], 0) + 1
        summary = " ".join(f"{SLOT_LABEL.get(g, g)}{n}" for g, n in by_grade.items())
        print(f"   매수후보(통합): {summary or '없음'}")
        return buyable

    def _execute_buys_unified(self, buyable: list, now_t: str, psbl_cash: int):
        """매수 가능한 종목 실제 주문 — 통합 후보풀(키움/new/모멘텀/추세/
        완화/유튜브/S7) 전체를 점수순으로 순회하며 슬롯이 찰 때까지 매수.
        buyable은 이미 _rank_score 기준 내림차순 정렬된 상태로 전달됨."""

        # 1차 익절 후 슬롯 반환 (주문가능금액 BONUS_SLOT_MIN_CASH 이상일 때만)
        익절중 = sum(
            1 for c in self.positions
            if self.peak_tracker.get(c, {}).get("stage", 0) >= 1
        )
        보너스 = 익절중 if psbl_cash >= BONUS_SLOT_MIN_CASH else 0
        slots = MAX_POSITIONS - len(self.positions) + 보너스
        if 익절중:
            print(f"  ♻️ 익절진행중 {익절중}종목 슬롯 반환 → 가용:{slots}")

        if now_t < BUY_START_TIME:
            print(f"⏳ {BUY_START_TIME} 이전 — 매수 대기 중")
            return

        # 일일 손실 한도
        should_stop, reason = self.risk.should_stop_trading(self.daily_loss_count)
        if should_stop:
            print(f"🛑 [SWING] {reason} — 매수 정지")
            st = _read_state()
            if not st.get("paused"):
                self._notify(f"🛑 {reason}\n!시작 으로 재개", critical=True)
                _update_state(paused=True)
            return

        if slots <= 0:
            print("📦 [SWING] 포지션 FULL")
            return

        for cand in buyable:
            if slots <= 0:
                break
            code, grade, score = cand["code"], cand["grade"], cand["score"]
            if not code:
                continue
            if code in self.positions:
                continue
            if cand["curr"] <= 0:
                continue
            if code in self.sold_today:
                print(f"🚫 [SWING] 재매수 금지 {code}")
                continue

            # ★ 시장 상태 체크 (약세장이라도 new 종목은 허용)
            is_new = cand["is_new"]
            allow, reason = self.risk.allow_buy_in_market(
                self.market_status, is_sector_match=is_new,
            )
            if not allow:
                print(f"⚠️ {reason} {code}")
                continue
            if reason:
                print(f"✅ {reason} {code}")

            # ★ 2026-09-29: 매수금액 산정 이원화(대장 결정 — "원천별 이원화
            #   유지") — new 슬롯은 기존 RiskManager(켈리/AI점수 기반)
            #   그대로, 나머지 신규 슬롯(모멘텀/추세/완화/유튜브/S7)은
            #   sbo2식 단순 고정금액(현금캡만 적용).
            atr_rate = self._get_atr_rate(code)
            if grade == SLOT_KISNEW:
                buy_amount = self.risk.calc_buy_amount(
                    score=score, atr_rate=atr_rate,
                    is_theme=is_new, psbl_cash=psbl_cash,
                    code=code,                           # ★ 켈리: 종목별 성과 반영
                    db_path="sbot_trade_history.db",     # ★ 켈리: sbot DB 사용
                )
            else:
                # ★ 2026-10-06 대장 지정 — 마지막 슬롯은 다음 슬롯을 위해
                #   현금을 아낄 이유가 없으니, 목표금액(BUY_1ST_AMT_BASE)
                #   캡 없이 가용현금 최대까지 매수(스크랩매수 방지 체크는
                #   그대로 유지).
                is_last_slot = (slots == 1)
                buy_amount = psbl_cash if is_last_slot else min(BUY_1ST_AMT_BASE, psbl_cash)
                # ★ 2026-09-29: 목표 슬롯금액의 FLAT_BUY_MIN_RATIO 미만이면
                #   스크랩 매수 방지(대장 지적 — 세방 21만원 매수 건)
                if buy_amount < BUY_1ST_AMT_BASE * FLAT_BUY_MIN_RATIO:
                    print(f"⏭️ [SWING] {code} 패스 — 가용현금({buy_amount:,}원)이 "
                          f"목표 슬롯금액({BUY_1ST_AMT_BASE:,}원)의 "
                          f"{FLAT_BUY_MIN_RATIO:.0%} 미만")
                    continue

            # ★ 2026-10-06: 후보의 curr는 아침 분석(score_cache)이나 하루1회
            #   후보풀 갱신 때 가격이라 오후엔 크게 어긋남 — 그 가격+1호가
            #   지정가는 안 잡혀 취소→sold_today로 그날 재매수까지 막혔음.
            #   실제 주문 직전 현재가로 다시 조회.
            _fresh = self.api.get_market_data(code)
            _fresh_px = safe_float(_fresh.get("stck_prpr", 0)) if _fresh else 0
            if _fresh_px <= 0:
                print(f"⏭️ [SWING] {code} 패스 — 주문 직전 현재가 조회 실패")
                continue
            cand = dict(cand, curr=_fresh_px)

            # ★ 1주도 못 사면 패스
            if buy_amount < cand["curr"]:
                print(f"⏭️ [SWING] {code} 패스 — 예산({buy_amount:,}원) < 주가({cand['curr']:,.0f}원)")
                continue

            tag = " 🆕new" if is_new else f" [{SLOT_LABEL.get(grade, grade)}]" if grade != SLOT_KISNEW else ""
            print(f"🚀 [SWING] 매수 {code} | {score}점 | {fmt_won(buy_amount)}{tag}"
                  + (f" | ATR{atr_rate*100:.1f}%" if atr_rate else ""))

            self.buy_context[code] = {
                "ai_score":   score,
                "ai_reason":  cand["ai_reason"],
                "stock_name": cand["name"],
            }
            # ★ 신규 슬롯(모멘텀/추세/완화/유튜브/S7) 출처를 buy_tag로 남김
            #   (new는 _get_pool()이 이미 "expert"로 태깅함)
            if grade != SLOT_KISNEW:
                self.code_tag_map[code] = grade
                if code not in self.code_name_map:
                    self.code_name_map[code] = cand["name"]

            # ★ sbo2 교차 보유 방지 — master_db 기반 (2026-07-02)
            #   기존엔 sbo2_state.json을 직접 열어 읽었음(파일 스키마 의존 +
            #   락 없음). 두 봇 다 매수/매도마다 이미 기록하는
            #   master_db(master_positions)를 단일 기준으로 사용.
            #   조회 실패 시엔 기존과 동일하게 "교차 보유 없음"으로 보고
            #   매수를 막지는 않음(부가 안전장치 — 매수 자체를 중단시킬
            #   이유는 아님). ★ 2026-09-29: sbo2 통합/퇴역 이후에도 잔재
            #   데이터 방어용으로 당분간 유지(계획 문서 리스크 섹션 참고).
            sbo2_pos = set()
            if get_all_positions:
                try:
                    sbo2_pos = {p["code"] for p in get_all_positions() if p["bot_type"] == "sbo2"}
                except Exception as _e:
                    print(f"⚠️ sbo2 포지션 조회 오류: {_e}")
            if code in sbo2_pos:
                print(f"⛔ {code} sbo2 보유 중 — sbot 매수 제외")
                continue
            if not self._do_buy(code, cand["curr"], buy_amount):
                continue
            # ★ 2026-10-06: 한 루프에서 여러 종목을 살 때 주문가능금액을 안 줄여서
            #   FLAT_BUY_MIN_RATIO(스크랩매수 방지) 체크가 두 번째부터 무력화됐음
            psbl_cash = max(0, psbl_cash - buy_amount)

            # ★ peak_tracker 즉시 초기화 (v3 — ATR 추세추종)
            # ★ 공통 헬퍼로 통일 — 기존엔 buy_date 필드가 빠져 있어서
            #   25일 보유기한 매도 로직이 이 종목에는 평생 작동하지 않는
            #   버그가 있었음 (sbot_strategy.check_sell의 tracker 자동
            #   초기화 분기는 code가 peak_tracker에 "없을 때만" 실행되는데,
            #   여기서 이미 채워 넣으니 그 분기가 다시는 안 돔). ★ 2026-09-29:
            #   신규 슬롯도 예외 없이 이 헬퍼를 거치도록 통일(계획 문서
            #   리스크 섹션 — peak_tracker 필드 누락시 그 루프 이후 모든
            #   보유종목 매도체크가 스킵되는 실제 버그 이력 있는 지점).
            _atr_rate = self._get_atr_rate(code)
            self.peak_tracker[code] = self._make_peak_tracker_entry(
                entry_price=cand["curr"], atr_rate=_atr_rate,
            )
            slots -= 1
            time.sleep(1)

    # ============================================================
    # 저녁 보충매수 (목표금액 미달분 채우기)
    # ============================================================
    def _run_evening_topup(self, now_t: str, psbl_cash: int):
        """★ 2026-10-06 대장 지정 — 당일 1차매수한 종목 중 목표금액
        (target_amount)의 EVENING_TOPUP_MIN_FILL_RATIO 미만만 체결된
        것을, 정규장 마감(15:30) 이후 애프터마켓 시간대에 한 번
        확인해서 나머지를 보충매수한다. 하루 1종목당 1회만(topup_done).
        ★ 2026-10-06 재조정(대장 지정) — "동일가나 이하가격에서만
        재매수"로 단순화(물타기든 추격매수든 상단은 절대 안 넘고, 하단은
        제한 없음 — 밑으로 더 내려가도 손절은 별도 로직이 독립적으로
        돌고 있어 이 함수와 무관). 가격이 진입가보다 높으면 이번 루프는
        건너뛰고(topup_done 안 찍음) 다음 루프에 다시 확인 — 저녁 동안
        가격이 내려오면 그때 채운다."""
        if now_t < EVENING_TOPUP_START:
            return
        for code, pos in list(self.positions.items()):
            if pos.get("buy_date") != today_str():
                continue
            if pos.get("topup_done"):
                continue
            target = pos.get("target_amount", 0)
            if target <= 0:
                # ★ target_amount 없음(재시작으로 소실 등) — 판단 불가, 조용히 스킵
                pos["topup_done"] = True
                continue
            entry  = pos.get("entry_price", 0)
            qty    = pos.get("qty", 0)
            if entry <= 0 or qty <= 0:
                continue
            filled = entry * qty
            if filled >= target * EVENING_TOPUP_MIN_FILL_RATIO:
                pos["topup_done"] = True
                continue
            if psbl_cash < MIN_ANALYSIS_CASH:
                continue  # 현금 부족 — 다음 루프(저녁 동안 반복)에 재시도

            mdata = self.api.get_market_data(code)
            current = safe_float(mdata.get("stck_prpr", 0)) if mdata else 0
            if current <= 0:
                continue

            if current > entry:
                # 대장 지정 — 동일가나 이하만 재매수(추격매수 방지).
                # 다음 루프에 다시 확인(topup_done 안 찍음).
                continue

            shortfall = min(int(target - filled), psbl_cash)
            if shortfall < current:
                pos["topup_done"] = True
                continue

            print(f"🌙 [SWING] {code}({self._name(code)}) 저녁보충매수 — "
                  f"목표{fmt_won(target)} 중 {fmt_won(filled)}만 체결 → {fmt_won(shortfall)} 보충 시도")
            old_buy_date = pos.get("buy_date")
            ok = self._do_buy(code, current, shortfall, is_second=True)
            # ★ _do_buy(is_second=True)는 entry_price/qty만 남기고 나머지
            #   필드를 지우므로(기존 2차매수(물타기) 경로와 공유하는
            #   한계), buy_date/topup_done을 다시 채워 넣는다.
            if code in self.positions:
                self.positions[code]["buy_date"]   = old_buy_date
                self.positions[code]["topup_done"] = True
            if ok:
                psbl_cash = max(0, psbl_cash - shortfall)

    # ============================================================
    # 매도 체크
    # ============================================================
    def _get_vol_ratio(self, code: str, mdata: dict) -> float:
        """
        거래량 전일 대비 비율(%) 조회.

        우선순위:
          1. sector_monitor.db stock_momentum.vol_ratio (30초 실시간)
          2. KIS API mdata["vol_inrt"] (거래량 전일비 %)
          3. 0.0 반환 (데이터 없음 → check_sell 에서 조건 통과)

        캐시: 30초
        """
        now_ts = time.time()
        if not hasattr(self, "_vol_ratio_cache"):
            self._vol_ratio_cache = {}
        cached = self._vol_ratio_cache.get(code)
        if cached and now_ts - cached[1] < 30:
            return cached[0]

        # ── 우선순위 1: sector_monitor.db ─────────────────
        try:
            import sqlite3 as _sl
            _sm_db = "/home/free4tak/k-bot/stock_bot/intelligence/sector_monitor.db"
            if not os.path.exists(_sm_db):
                _sm_db = os.path.join(
                    os.path.dirname(os.path.abspath(__file__)),
                    "..", "intelligence", "sector_monitor.db"
                )
            if os.path.exists(_sm_db):
                _conn = _sl.connect(_sm_db, timeout=3)
                _conn.execute("PRAGMA query_only = ON")
                row = _conn.execute("""
                    SELECT vol_ratio FROM stock_momentum
                    WHERE code = ?
                    ORDER BY ts DESC LIMIT 1
                """, (code,)).fetchone()
                _conn.close()
                if row and row[0] and float(row[0]) > 0:
                    vr = float(row[0])
                    self._vol_ratio_cache[code] = (vr, now_ts)
                    return vr
        except Exception as _e:
            print(f"⚠️ sector_monitor vol_ratio 조회 오류 {code}: {_e}")

        # ── 우선순위 2: KIS API mdata vol_inrt ────────────
        # vol_inrt: 거래량 전일 대비 증감율(%)
        # 증감율 50% → vol_ratio 150% (전일 대비 1.5배)
        try:
            vi = float(mdata.get("vol_inrt", 0) or 0)
            if vi != 0:
                vr = 100.0 + vi
                self._vol_ratio_cache[code] = (vr, now_ts)
                return vr
        except Exception:
            pass

        # ── 우선순위 3: 데이터 없음 ───────────────────────
        self._vol_ratio_cache[code] = (0.0, now_ts)
        return 0.0

    def _check_all_sells(self, pos_mkt_cache: dict):
        """모든 보유 종목 매도 체크"""
        for code, pos in list(self.positions.items()):
            mdata = pos_mkt_cache.get(code) or self.api.get_market_data(code)
            if not mdata:
                continue
            tech     = self._tech_cache.get(code, ({}, 0))
            ma20     = tech[0].get("ma20", 0) if isinstance(tech, tuple) else 0
            atr_rate = self._get_atr_rate(code)

            # ★ vol_ratio 실제 조회 (sector_monitor.db → KIS API 순서)
            vol_ratio = self._get_vol_ratio(code, mdata)
            # ★ 스윙봇 — market_status "normal" 고정
            # 약세/stop 모드 손절선 축소(-3%) 방지 → 원래 손절선(-7%) 유지
            self.strategy.check_sell(
                code, pos, mdata, "normal",
                self.peak_tracker, self._is_paused,
                lambda c, p, a: self._do_buy(c, p, a, is_second=True),
                lambda c, q, r, sp: self._do_sell(c, q, r, sp),
                self._do_loss,
                ma20=ma20, atr_rate=atr_rate,
                vol_ratio=vol_ratio,
            )
            # ★ 2026-07-06: 기간(보유일수) 기반 강제청산 로직 제거 — 최근 장세에서
            #   ATR 손절/트레일링/목표가로 이미 충분히 관리되는 포지션을 보유일수만
            #   초과했다는 이유로 손실 구간에서 강제로 털어버리는 부작용이 반복돼
            #   ATR 기반 판단으로만 가기로 함 (사용자 결정).
            # ★ 2026-10-06: 미너비니 200일선 이탈 청산 블록 제거 — 미너비니 추천
            #   자체가 폐기됐고(buy_tag="minervini"가 더는 안 생김) 원래도 buy_tag를
            #   저장하지 않아 한 번도 실행된 적 없는 코드였음.

    # ============================================================
    # 메인 루프
    # ============================================================
    def run(self):
        self._notify(
            f"🚀 [영암9 SWING1] 스윙봇 가동\n"
            f"⏰ {now_kst().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"💰 1차:{fmt_won(BUY_1ST_AMT_BASE)} / 최대 {MAX_POSITIONS}종목\n"
            f"🎯 ATR×3 목표가 상향추종 | 손절:ATR×2 | 1차달성시 50%매도+상향\n"
            f"⏳ 매수: {BUY_START_TIME} 이후",
            critical=True,
        )
        self._is_paused = False
        self._last_market_check = 0


        # ★ 실계좌 ↔ DB 정합성 체크
        if _sync_positions:
            try:
                real = _sync_positions(
                    self.api,
                    "sbot_trade_history.db",
                    self._notify,
                    bot_type="sbot",
                )
                if real:
                    self.positions.clear()
                    self.positions.update(real)
            except Exception as e:
                print(f"⚠️ DB 정합성 체크 오류: {e}")

        # ★ peak_tracker 복원 (2026-06-28 추가)
        # 과거엔 peak_tracker가 메모리에만 있어 재시작마다 모든 보유종목의
        # stage(익절 단계)/손절가/목표가/buy_date가 초기값으로 리셋되는
        # 문제가 있었음 (트레일링 진행 중이던 종목이 손절폭이 다시 좁아지는 등).
        # sbot_state.json에 저장된 peak_tracker를 불러와 실제 보유종목과
        # 대조 — 실계좌에 없는 잔재는 버리고, 실계좌에 있는데 저장값이
        # 없는 종목(완전 신규/수동매수 후 첫 재시작)은 헬퍼로 새로 생성.
        #
        # (2026-06-29 메모: 한때 target1이 entry+atr_val*3과 다르면
        # "오염 데이터"로 보고 재생성하는 값 정합성 검증을 추가했었으나,
        # sbot_strategy.py의 TARGET1_CAP_RATE(+20% 상한 캡, 2026-06-23
        # 추가)을 놓치고 분석한 착오였음 — 고변동성 종목은 ATR×3과 +20%
        # 중 작은 값을 쓰는 게 정상이라 단순 entry+atr_val*3 비교로는
        # 정상 데이터를 오탐함. 캡 상수에 의존하면 sbot_strategy.py 쪽
        # 계산 로직이 바뀔 때마다 같이 고쳐야 하는 결합도 생겨, 값 검증은
        # 제거하고 원래 목적이던 필드 존재 검증만 유지.)
        _PT_REQUIRED_FIELDS = {
            "stage", "stop_price", "target1", "target_next", "atr_val",
            "buy2_done", "buy1_price", "peak_rate", "peak_price", "buy_date",
        }

        try:
            _saved_pt = _read_state().get("peak_tracker", {}) or {}
        except Exception as e:
            print(f"⚠️ peak_tracker 복원 오류: {e}")
            _saved_pt = {}
        restored, created, repaired = 0, 0, 0
        for _code, _pos in self.positions.items():
            _saved_entry = _saved_pt.get(_code)
            _entry_price = _pos.get("entry_price", 0)
            # ★ 저장된 항목이 있어도 필수 필드가 빠져 있으면(과거 버그로
            #   생성된 불완전한 데이터) 그대로 쓰지 않고 새로 생성 —
            #   안 그러면 재시작 한 번에 KeyError 버그가 다시 살아남
            if _saved_entry and _PT_REQUIRED_FIELDS.issubset(_saved_entry.keys()):
                self.peak_tracker[_code] = _saved_entry
                restored += 1
            else:
                if _entry_price > 0:
                    _atr_rate = self._get_atr_rate(_code)
                    self.peak_tracker[_code] = self._make_peak_tracker_entry(
                        entry_price=_entry_price, atr_rate=_atr_rate,
                        buy_date=_pos.get("buy_date"),
                    )
                    if _saved_entry:
                        repaired += 1
                    else:
                        created += 1
        if restored or created or repaired:
            print(f"📦 peak_tracker 복원: 기존유지 {restored}건 / "
                  f"신규생성 {created}건 / 불완전복구 {repaired}건")

        # ★ 2026-08-31: code_name_map 복원 — 이 맵은 저장은 되는데(_write_status
        #   호출 시 last_status 하위에 저장됨) 재시작 시 복원 로직이 아예 없어서
        #   매 재시작마다 비어버렸음. 이름은 매수 후보 분석 파이프라인
        #   (_analyze_one_code)을 거친 종목만 채워지는데, 이미 보유 중인 종목은
        #   재시작 후 다시 분석 대상이 될 일이 없어 콘솔/KiKi 상태에 종목명 대신
        #   코드만 계속 표시되는 버그로 이어졌음(사용자 지적 — "SBOT의 종목명
        #   표시해 줄 수 있니?"). 시세조회 API(get_market_data)의 hts_kor_isnm
        #   필드로 보충하려 했으나, 08~09시 NXT 시간대엔 이 필드 자체가 응답에
        #   빠져 있어(실측 확인) 신뢰 불가 — sbo2와 동일하게 로컬 DB
        #   (kr_theme_finance.db, 장 시간과 무관)로 조회하도록 변경.
        self.code_name_map.update(_read_state().get("last_status", {}).get("code_name_map", {}) or {})
        _name_backfilled = 0
        for _code in self.positions:
            if _code not in self.code_name_map:
                try:
                    import re as _re
                    _db   = os.path.join(_BASE, "lina_bot", "kr_theme_finance.db")
                    _conn = _sqlite3.connect(_db, timeout=5)
                    _row  = _conn.execute(
                        "SELECT stock_name FROM kr_theme_stocks WHERE stock_name LIKE ? LIMIT 1",
                        (f"%{_code}%",),
                    ).fetchone()
                    _conn.close()
                    if _row:
                        _clean = _re.sub(r'(KOSPI|KOSDAQ).*|\d{6}', '', _row[0]).strip()
                        if _clean:
                            self.code_name_map[_code] = _clean
                            _name_backfilled += 1
                except Exception:
                    pass
        if _name_backfilled:
            print(f"🏷️ 종목명 보충 조회: {_name_backfilled}건")

        while True:
            try:
                # ★ today를 루프 맨 앞에서 정의
                today = today_str()
                now_t = now_hhmm()
                now   = now_hms()

                # ★ 2026-09-09: heartbeat 갱신도 주말/휴장/장외 continue보다
                #   먼저 실행해야 함 — 예전엔 "장외 대기" continue 뒤(정규장
                #   진입 후)에만 있어서, 20시 넘으면 이 줄까지 아예 도달을
                #   못 해 heartbeat가 밤새 하나도 안 찍혔음. heartbeat
                #   watchdog(30초 주기, 300초 무갱신시 강제재시작)이 이걸
                #   "죽었다"고 오판해서 매일 밤 20:05 전후로 sbot이 자동
                #   재시작당하고 있었음(실제 장애 아님, 대장이 로그 보고
                #   발견 — "20:05분에 재기동 루틴 있니?"). 토큰갱신 때와
                #   똑같은 구조의 버그.
                pathlib.Path(HB_FILE).touch()

                # ★ 2026-09-07: 토큰 갱신도 주말/휴장/장외 continue보다
                #   먼저 실행해야 함 — 예전엔 아래쪽(계좌 조회 직전)에
                #   있어서, 심야에 "장외 대기" continue에 매 루프 걸리면
                #   이 줄까지 도달을 못 해 토큰이 실제로 만료돼도 갱신이
                #   안 됐음(재부팅 직후 물려받은 캐시토큰이 자정에 만료,
                #   다음날 아침 장중까지 chk-holiday/잔고조회 등 전부
                #   조용히 실패했던 2026-09-07 새벽 실사례).
                self.api.refresh_token_if_needed()

                # ★ 2026-08-30: 주말/휴장일에도 KiKi 명령(홀드/매도/정지 등)은
                #   처리해야 함 — 기존엔 이 체크가 주말/휴장 早리턴 뒤에 있어서
                #   pending_cmd가 다음 개장일까지 무한 대기했음(사용자 신고 —
                #   주말에 "!h 종목" 실행했더니 "응답 없음", 알고보니 매 루프
                #   시작하자마자 continue로 건너뛰어 처리 자체가 안 됐던 것).
                self._handle_pending_command(_read_state())

                # ── 주말 ─────────────────────────────────
                # ★ 2026-10-06 대장 지정 — daybot에서 먼저 발견/수정한 것과
                #   동일 패턴 적용: self._ws가 __init__에서 한 번 start()된
                #   뒤 메인루프의 주말/휴장/장외 판단과 무관하게 계속
                #   재연결을 시도해서(KIS가 장외엔 연결을 끊음), 불필요한
                #   "연결종료→재연결" 로그가 찍히고 있었음(실측: sbot도
                #   가끔씩 끊겼다 재연결됨). 메인루프가 쉬는 동안 웹소켓도
                #   같이 멈췄다가 정상 세션시간 복귀하면 재개한다.
                if is_weekend():
                    if self._ws and not self._ws_paused:
                        self._ws.stop(); self._ws_paused = True
                    print(f"😴 [{now}] 주말 — 장 없음")
                    time.sleep(SLEEP_INTERVAL); continue

                # ── 휴장일 ───────────────────────────────
                # ★ 2026-08-17: is_market_open()이 None(API 실패/판단불가)이면
                #   그날 캐시하지 않고 다음 루프에 재시도 — 예전엔 실패 시에도
                #   무조건 "휴장 아님"으로 캐시해서, 하필 그날 첫 체크가 실패하면
                #   진짜 휴장일에도 하루 종일(다음 재시작 전까지) 정상 개장으로
                #   착각한 채 도는 사고가 있었음(08-17 광복절 대체공휴일 실사례).
                if self._holiday_checked != today:
                    _open = self.api.is_market_open()
                    if _open is None:
                        print(f"⚠️ [{now}] 휴장일 판단 실패 — 다음 루프 재시도")
                    else:
                        self._is_holiday      = not _open
                        self._holiday_checked = today
                        if self._is_holiday:
                            self._notify(f"🎌 오늘은 휴장일 — 봇 대기")
                if self._is_holiday:
                    if self._ws and not self._ws_paused:
                        self._ws.stop(); self._ws_paused = True
                    print(f"🎌 [{now}] 휴장일 — 대기 중...")
                    time.sleep(IDLE_SLEEP_SEC); continue

                # ── 시간대별 동작 ─────────────────────────
                is_reg      = REG_MARKET_START <= now_t <= REG_MARKET_END
                is_sell_ok  = SELL_CHECK_START <= now_t <= SELL_CHECK_END
                is_buy_ok   = BUY_START_TIME <= now_t <= BUY_END_TIME

                if not is_sell_ok:
                    # ★ 2026-10-06 — 웹소켓 재개는 여기(장외 게이트 통과
                    #   시점)에서만 한다. 휴장 게이트 직후에 바로 재개하면
                    #   "휴장은 아니지만 아직 장외(20시 이후)"인 구간에서
                    #   재개→이 게이트에 바로 걸려 재정지, 매 루프
                    #   start/stop이 반복되는 낭비가 생김(daybot에서 먼저
                    #   확인된 패턴).
                    if self._ws and not self._ws_paused:
                        self._ws.stop(); self._ws_paused = True
                    print(f"😴 [{now}] 장외 대기 (20시 이후)...")
                    time.sleep(IDLE_SLEEP_SEC); continue

                if self._ws and self._ws_paused:
                    self._ws.start(); self._ws_paused = False

                print(f"\n📈 [SWING] {'정규장' if is_reg else ('매수/매도' if is_buy_ok else '장전/후 매도체크')} [{now}]")

                st              = _read_state()
                self._is_paused = st.get("paused", False)
                # ★ 2026-10-06: 키키 !리스크중단(전봇 긴급중단)을 sbot은 확인 안 해서
                #   "전봇 중단"이라고 답해도 계속 매수했음 — 신규매수만 멈추고
                #   보유종목 매도체크는 일시중단 분기에서 그대로 계속된다.
                if not self._is_paused:
                    try:
                        from master_db import is_paused_all as _is_paused_all
                        if _is_paused_all():
                            self._is_paused = True
                            print("🚨 [SWING] 통합 리스크 긴급중단 — 매수 중단(매도체크는 계속)")
                    except Exception:
                        pass

                # ── 일일 초기화 ──────────────────────────
                if today != self._sold_today_date:
                    self._daily_reset(today)
                else:
                    if not self.sold_today:
                        saved = st.get("sold_today", {})
                        if saved and st.get("sold_today_date") == today:
                            self.sold_today = saved

                # ── 동적 매수 임계치 (스윙은 db.SwingDB.get_recent_performance) ──
                # ★ 2026-08-16: 건수(20건) 기준 → 날짜(30일) 기준으로 변경.
                #   자세한 사유는 core/sbot_db.py의 get_recent_performance 참고.
                base_score = st.get("score_enter", BUY_SCORE_ENTER)
                perf       = self.db.get_recent_performance(days=30)
                if perf and perf["total"] >= 10:
                    if perf["win_rate"] < 40:
                        score_enter = base_score + 5
                        print(f"   📉 최근승률 {perf['win_rate']}% 낮음 → 기준점 +5")
                    elif perf["win_rate"] > 60:
                        score_enter = max(50, base_score - 3)
                        print(f"   📈 최근승률 {perf['win_rate']}% 높음 → 기준점 -3")
                    else:
                        score_enter = base_score
                else:
                    score_enter = base_score

                # 손절 카운터 리셋
                # ★ 2026-08-19: "and loss_date != today" 조건 제거 — 이 조건
                #   때문에 KiKi "!s시작"(cmd_pause의 daily_loss=0 리셋)이
                #   무력화되고 있었음. cmd_pause는 리셋할 때 loss_date를
                #   "오늘"로 같이 쓰는데(재개 시점 기록 목적), 이 체크는
                #   반대로 loss_date가 "오늘이 아닐 때만" 동기화하도록 돼
                #   있어서 — 정작 그날 리셋해야 하는 유일한 시나리오(수동
                #   재개)에서 매번 조건이 거짓이 돼 self.daily_loss_count가
                #   메모리에서 절대 안 풀리고, 다음 루프에서 should_stop_
                #   trading()이 여전히 True → 1073행에서 paused=True로 바로
                #   되돌려써서 "!s시작"이 30초 안에 저절로 취소된 것처럼
                #   보였음(사용자 신고 — "키키가 정지나 시작을 못한다").
                #   _daily_reset()의 자정 리셋은 self.daily_loss_count를
                #   직접 0으로 만들어서 이 체크와 무관하게 이미 처리됨 —
                #   그러니 날짜 조건 없이 "파일이 0인데 메모리는 아직
                #   0이 아니면 동기화"만으로 충분하고 안전함.
                if st.get("daily_loss") == 0 and self.daily_loss_count > 0:
                    self.daily_loss_count = 0
                    print("♻️ 손절카운터 초기화")

                # ── 디스코드 명령 ────────────────────────
                self._handle_pending_command(st)

                # ── 토큰 갱신 ────────────────────────────
                self.api.refresh_token_if_needed()

                # ── 계좌 ─────────────────────────────────
                # ★ 2026-06-30: 예수금은 웹소켓(체결통보 기반) 우선 사용 —
                #   is_healthy() 체크 추가해 연결이 끊기거나 오래
                #   갱신 안 됐으면(5분 이상) 안전하게 REST로 폴백.
                #   잔고(보유종목) 자체는 buy_date 등 메타데이터 보존이
                #   중요하고 수동매매 빈도가 높아 당분간 REST 유지 —
                #   예수금만 먼저 webosocket화해 API 호출 1회를 줄임.
                _ws_ok = self._ws and self._ws.is_healthy() and self._ws.cash > 0
                cash = self._ws.cash if _ws_ok else self.api.get_buyable_cash()
                new_pos = self.api.get_current_positions()
                # ★ None = 진짜 API 조회 실패 / {} = 정상응답인데 보유종목 0개 (구분 필수!)
                # ★ 2026-07-01 추가: {} 방어 — 기존에 보유종목이 있는데 빈 dict가
                #   오면 API 오류로 간주해 positions를 덮어쓰지 않음.
                #   한투 API가 "초당 거래건수 초과" 등 오류 시 {} 를 반환하는 경우가
                #   있어 positions.clear()가 실행돼 모든 보유종목이 사라지는
                #   치명적 버그가 실제로 발생했음 (035420 손절 직후 잔고조회
                #   실패로 positions=[] 됐다가 watchdog 재시작 루핑).
                _pos_suspicious = (new_pos == {} and len(self.positions) > 0)
                if new_pos is None or _pos_suspicious:
                    if _pos_suspicious:
                        print(f"⚠️ 잔고조회 결과 빈값({{}}), 기존 {len(self.positions)}종목 보유 중 "
                              f"→ API 오류로 간주, 기존 positions 유지")
                    else:
                        print("⚠️ 실계좌 잔고 조회 실패(None) — 기존 positions 유지")
                    self._check_api_health(False)
                else:
                    self._check_api_health(True)
                    # ★ 수동매도 감지 — 이전 포지션에 있었는데 실계좌에 없으면 감지
                    # ★ 수동매도는 재매수 허용 — sold_today 등록 안 함
                    # ★ 2026-09-03: 매수직후 동기화 보호 — sbo2가 올해 3번
                    #   실전에서 겪었던 것과 같은 버그(한투 정산지연 동안
                    #   방금 산 종목이 잔고에 아직 안 잡혀 "수동매도"로
                    #   오판 → 포지션 삭제)를 sbot에도 방지. BUY_SYNC_GUARD_SEC
                    #   동안은 new_pos에 없어도 수동매도 후보에서 제외.
                    _now_ts = time.time()
                    _guarded_codes = []
                    _manual_sold = []
                    for _code in list(self.positions.keys()):
                        if _code in new_pos or _code in self.sold_today:
                            continue
                        _guard_until = self._buy_sync_guard.get(_code, 0) + BUY_SYNC_GUARD_SEC
                        if _now_ts < _guard_until:
                            print(f"   🛡️ {self._name(_code)}({_code}) 매수직후 동기화 보호 중 "
                                  f"— 수동매도 감지 스킵")
                            _guarded_codes.append(_code)
                            continue
                        _manual_sold.append(_code)
                    # ★ 2026-08-15: 수동매도가 감지만 되고 DB에 전혀 기록되지
                    #   않던 문제 수정(사용자 지적 — "모든 거래가 우리 디비에
                    #   기록되어야 의미있는 통계가 만들어질거야, 백테스터등에도
                    #   활용될수 있고"). 실계좌 기간별손익 API로 정확한 매도가/
                    #   실현손익을 가져와 sbot_trade_history.db에도 남긴다.
                    _profit_rows = {}
                    if _manual_sold:
                        _today_ymd = datetime.datetime.now().strftime("%Y%m%d")
                        _pdata = self.api.get_period_trade_profit(_today_ymd, _today_ymd)
                        _profit_rows = {r["pdno"]: r for r in _pdata.get("trades", [])}
                    for _code in _manual_sold:
                        print(f"🔍 수동매도 감지: {_code} → 재매수 허용")
                        _old_pos = self.positions.get(_code, {})
                        _row = _profit_rows.get(_code)
                        if _row and int(_row.get("sll_qty", 0) or 0) > 0:
                            _sell_price = float(_row.get("sll_pric", 0) or 0)
                            _buy_price  = float(_row.get("pchs_unpr", 0) or 0) or _old_pos.get("entry_price", 0)
                            _sell_qty   = int(_row.get("sll_qty", 0) or 0) or _old_pos.get("qty", 0)
                        else:
                            # 기간별손익 API에 아직 반영 안 됨 — 최근 시세로 추정 기록
                            _mdata = self.api.get_market_data(_code)
                            _sell_price = safe_float(_mdata.get("stck_prpr", 0)) if _mdata else _old_pos.get("entry_price", 0)
                            _buy_price  = _old_pos.get("entry_price", 0)
                            _sell_qty   = _old_pos.get("qty", 0)
                        self.db.save_manual_trade(
                            _code, self._name(_code), _buy_price, _sell_price,
                            _sell_qty, "수동매도")
                        # ★ 2026-07-02: master_positions 정리 누락 수정 —
                        #   기존엔 여기서 감지만 하고 지우질 않아 수동매도된
                        #   종목이 master_positions에 유령으로 계속 남아있었음
                        #   (sbo2는 이미 정리하고 있었음, sbot만 누락).
                        if _master_remove:
                            _master_remove("sbot", _code)

                    # ★ 2026-09-05: 수동 일부매도(부분매도) 감지 — 기존엔
                    #   전량매도(코드가 new_pos에서 완전히 사라짐)만
                    #   감지/기록하고, 수량만 줄어드는 부분매도는 조용히
                    #   qty만 갱신될 뿐 거래이력DB에 전혀 안 남았음(사용자
                    #   지적). self.db.save_sell()은 이미 목표1 50%익절
                    #   때부터 "판 수량 < 원본 수량"이면 DB 행을 분할하는
                    #   로직이 있어 그대로 재사용, sell_price는 정확한
                    #   체결가를 알 방법이 없어 감지 시점 최신 시세로 추정.
                    for _code in list(self.positions.keys()):
                        if _code not in new_pos:
                            continue
                        _guard_until = self._buy_sync_guard.get(_code, 0) + BUY_SYNC_GUARD_SEC
                        if _now_ts < _guard_until:
                            continue
                        _old_qty = self.positions[_code].get("qty", 0)
                        _new_qty = new_pos[_code].get("qty", 0)
                        if 0 < _new_qty < _old_qty:
                            _sold_qty = _old_qty - _new_qty
                            _mdata = self.api.get_market_data(_code)
                            _sell_price = (safe_float(_mdata.get("stck_prpr", 0))
                                           if _mdata else self.positions[_code].get("entry_price", 0))
                            self.db.save_sell(_code, _sell_price, "수동일부매도",
                                               sold_qty=_sold_qty)
                            print(f"   🔍 수동 일부매도 감지: {self._name(_code)}"
                                  f"({_code}) {_sold_qty}주 @ {_sell_price:,.0f}원")

                    # ★ 2026-09-05: sbo2가 08-11에 겪은 "한화엔진 실사례"와
                    #   동일한 위험 방지 — 오늘 이미 매도(주로 손절/본절,
                    #   sold_today 등록분)한 종목이 KIS 잔고API 정산지연으로
                    #   new_pos에 잠깐 남아있으면, 기존엔 무조건
                    #   self.positions.update(new_pos)로 그대로 되살아나
                    #   방금 자른 손실 포지션을 재보유중으로 오인할 위험이
                    #   있었음(sbot은 blunt clear+update라 sbo2보다 이 위험에
                    #   더 그대로 노출돼 있었음). 아직 self.positions에 없던
                    #   (=진짜 신규가 아니라 정산지연 잔재로 보이는) 코드만
                    #   골라서 제외.
                    # ★ 2026-10-06: 위 보호를 SELL_SYNC_GUARD_SEC 안으로 한정 —
                    #   기존엔 sold_today면 당일 내내 재입양을 막아서, 매도주문이
                    #   접수만 되고 안 잡힌 종목(애프터장 지정가 등)이나 미체결
                    #   취소 전에 일부 체결된 매수분이 손절선 없이 하루를 보냈음.
                    #   보호시간이 지나도 잔고에 있으면 진짜 보유로 보고 다시
                    #   감시(매도했던 종목이면 그때의 peak_tracker로 복원 + 알림).
                    _resurrect_risk = {
                        c for c in new_pos
                        if c not in self.positions and c in self._recent_sells
                        and _now_ts - self._recent_sells[c]["ts"] < SELL_SYNC_GUARD_SEC
                    }
                    for _c in _resurrect_risk:
                        print(f"   ⏭️ {self._name(_c)}({_c}) 방금 매도 — "
                              f"정산 지연으로 보이는 잔고, 재입양 보류")
                    for _c in new_pos:
                        if _c in self.positions or _c in _resurrect_risk:
                            continue
                        _rs = self._recent_sells.pop(_c, None)
                        if _rs:
                            if _rs.get("tracker"):
                                self.peak_tracker[_c] = _rs["tracker"]
                            self._notify(
                                f"⚠️ {self._name(_c)}({_c}) 매도주문({_rs.get('reason', '')}) 후 "
                                f"{SELL_SYNC_GUARD_SEC}초가 지나도 잔고에 남아있음(미체결) — 다시 감시",
                                critical=True,
                            )
                    for _c in [c for c, v in self._recent_sells.items()
                               if _now_ts - v["ts"] >= SELL_SYNC_GUARD_SEC]:
                        self._recent_sells.pop(_c, None)   # 체결 확인됨(잔고에서 사라짐)
                    _filtered_new_pos = {
                        c: p for c, p in new_pos.items() if c not in _resurrect_risk
                    }
                    _guarded_positions = {c: self.positions[c] for c in _guarded_codes
                                          if c in self.positions}
                    self.positions.clear()
                    self.positions.update(_filtered_new_pos)
                    # ★ 2026-09-03: 보호 중인 종목은 new_pos에 없어도(정산지연)
                    #   메모리 포지션을 그대로 유지 — 위 가드 스킵과 짝을 이룸.
                    for _code, _pos in _guarded_positions.items():
                        self.positions.setdefault(_code, _pos)
                    # ★ 2026-09-09: sbot의 self._name()은 code_name_map만 보는데
                    #   (키움 조건검색으로만 채워짐), 이건 조건검색에 안 걸린
                    #   수동매수/장기보유 종목은 화면에 코드 그대로 표시되는
                    #   원인이었음(sbo2의 448900 실사례로 발견 — kis_api.py
                    #   get_current_positions()가 한투 실시간 이름(prdt_name)을
                    #   버리고 있던 게 근본원인, 그건 core에서 수정). 여기서도
                    #   실계좌 데이터의 이름을 code_name_map에 반영해 화면에
                    #   반영되게 함.
                    for _code, _pos in self.positions.items():
                        _real_name = _pos.get("name", "")
                        if _real_name and _real_name != _code:
                            self.code_name_map[_code] = _real_name
                psbl_cash      = self.api.get_psbl_order_cash("005930")
                if psbl_cash <= 0:
                    psbl_cash = cash
                self._last_psbl_cash = psbl_cash
                _ws_tag = "WS" if _ws_ok else "REST"
                print(f"\n⏰ {now} | 💵 예수금[{_ws_tag}]: {cash:,} | 💰 주문가능: {psbl_cash:,}")

                # ── 보유종목 ─────────────────────────────
                pos_mkt_cache = {}
                total_profit  = 0
                print("📦 [SWING] 보유종목")
                for code, pos in self.positions.items():
                    data = self.api.get_market_data(code)
                    if not data:
                        continue
                    pos_mkt_cache[code] = data
                    cur    = safe_float(data.get("stck_prpr", 0))
                    entry  = pos["entry_price"]
                    qty    = pos["qty"]
                    profit = (cur - entry) * qty
                    rate   = (cur - entry) / entry * 100 if entry > 0 else 0
                    total_profit += profit
                    stop  = self.peak_tracker.get(code, {}).get("stop_price",
                              pos.get("stop_price", 0))
                    tgt   = self.peak_tracker.get(code, {}).get("target_next",
                              pos.get("tgt_price", 0))
                    grade = pos.get("grade", "스윙")
                    hold_mark = "⭐" if self.peak_tracker.get(code, {}).get("hold", False) else "  "
                    print(f"{hold_mark}💼 {self._name(code)}({grade}) {rate:+.2f}% | "
                          f"현재:{int(cur):,} | 손절:{int(stop):,} 목표:{int(tgt):,}")
                    # ★ master_positions 현재가 갱신 (대시보드)
                    if _master_upsert and cur > 0:
                        try:
                            _master_upsert(
                                bot_type='sbot', code=code,
                                current_price=cur,
                                qty=qty,
                                stage=self.peak_tracker.get(code,{}).get('stage',0),
                            )
                        except Exception: pass
                print(f"📈 총손익: {int(total_profit):,}원")

                # ── 시장 상태 (5분마다) ────────────────────
                if time.time() - self._last_market_check > 300:
                    self._update_market_status()
                    self._last_market_check = time.time()
                    # ★ 장 초반 급락 안전장치 (sbot은 09:20까지)
                    if self._check_opening_crash(now_t):
                        self.market_status = "stop"
                        if self._kospi_low == 0.0 or self.market_rate < self._kospi_low:
                            self._kospi_low = self.market_rate

                # ★ 미체결 주문 취소 (1루프 이상 경과)
                # ★ 2026-09-03: 기존 "1) 체결완료 종목 pending에서 먼저
                #   제거" 단계가 _do_buy()/_check_megacap_dip_buy()가 체결
                #   확인 전에 이미 self.positions를 채워놓는 것과 겹쳐서,
                #   이 취소로직 자체가 실행될 기회가 없었음(재점검 리포트로
                #   발견 — sbo2와 완전히 동일한 버그). 그 단계를 삭제하고,
                #   cancel_order() 성공/실패로 실제 체결여부를 판단하도록
                #   수정 — 성공(=진짜 미체결이었음)했을 때만 포지션/
                #   buy_context/peak_tracker 정리 + sold_today 등록,
                #   실패(=이미 체결된 것으로 보임)면 그대로 정상 포지션으로
                #   둔다(잘못 정리하면 트레일링 진행 이력이 날아감).
                # ★ 2026-10-06: 시장 stop모드의 continue보다 앞으로 이동 — 기존엔
                #   stop모드 동안 미체결 주문이 하나도 취소되지 않았음.
                for _code, (_orgno, _odno, _qty, *_rest) in list(self._pending_orders.items()):
                    _is_second = bool(_rest and _rest[0])
                    if _odno and _is_second:
                        # ★ 2026-10-06: 2차매수(물타기) 미체결은 그 주문만 취소 —
                        #   기존엔 1차매수분까지 포지션/tracker를 통째로 지우고
                        #   sold_today로 막아서 보유 종목 전체가 감시에서 빠졌음.
                        #   수량은 다음 루프 잔고동기화가 실계좌 기준으로 맞춘다.
                        if self.api.cancel_order(_orgno, _odno, _code, _qty):
                            print(f"🚫 [SWING] 2차매수 미체결 취소: {_code}({self._name(_code)}) — 기존 보유분은 유지")
                    elif _odno:
                        print(f"🚫 [SWING] 미체결 취소: {_code}({self._name(_code)}) odno:{_odno}")
                        ok = self.api.cancel_order(_orgno, _odno, _code, _qty)
                        if ok:
                            self._notify(
                                f"🚫 [SWING] 미체결 취소\n"
                                f"종목: {_code}({self._name(_code)})\n"
                                f"사유: 1루프 내 미체결 → 자금 반환"
                            )
                            # ★ 재매수 방지 — sold_today 등록 (진짜 미체결이었을 때만)
                            self.sold_today[_code] = now_hms()
                            # ★ 잔재 정리 (진짜 미체결이었을 때만)
                            self.buy_context.pop(_code, None)
                            self.peak_tracker.pop(_code, None)
                            self.positions.pop(_code, None)
                            self._buy_sync_guard.pop(_code, None)
                        else:
                            print(f"   ℹ️ {_code}({self._name(_code)}) 취소 실패 — "
                                  f"이미 체결된 것으로 보여 정상 포지션으로 유지")
                    self._pending_orders.pop(_code, None)

                # ── 시장 stop ─────────────────────────────
                if self.market_status == "stop":
                    print(f"🚨 [SWING] 시장 중단 모드 | 코스피:{self.market_rate:+.2f}%")
                    for _c in list(self.positions):
                        _d = self.api.get_market_data(_c)
                        if _d: pos_mkt_cache[_c] = _d
                    self._check_all_sells(pos_mkt_cache)

                    # ★ 반등 감지 매수 — 2번 연속 반등 OR 코스닥 강세+1번 반등
                    kospi_now = self.market_rate
                    if self._kospi_low == 0.0 or kospi_now < self._kospi_low:
                        self._kospi_low = kospi_now
                        self._rebound_count = 0
                    kospi_rebound = kospi_now - self._kospi_low
                    kosdaq_strong = getattr(self, 'kosdaq_rate', 0.0) > -1.0

                    if kospi_rebound >= 1.0:
                        self._rebound_count += 1
                    else:
                        self._rebound_count = 0

                    print(f"📉 [SWING] 최저:{self._kospi_low:+.2f}% 반등:{kospi_rebound:+.2f}% "
                          f"연속:{self._rebound_count}회 코스닥강세:{kosdaq_strong}")

                    avail = MAX_POSITIONS - len(self.positions)
                    kospi_now  = self.market_rate
                    kosdaq_now = getattr(self, 'kosdaq_rate', 0.0)
                    kosdaq_strong = kosdaq_now > -1.0
                    kospi_strong  = kospi_now  > -1.0

                    if kosdaq_strong and not kospi_strong:
                        rebound_ok = self._rebound_count >= 1
                        self._prefer_kosdaq = True
                        case_label = "[SWING]케이스1(코스닥선방)"
                    elif kospi_strong and not kosdaq_strong:
                        rebound_ok = self._rebound_count >= 1
                        self._prefer_kosdaq = False
                        case_label = "[SWING]케이스2(코스피선방)"
                    else:
                        rebound_ok = self._rebound_count >= 2
                        self._prefer_kosdaq = kosdaq_now > kospi_now
                        case_label = "[SWING]케이스3(동반폭락)"

                    if rebound_ok and avail > 0 and psbl_cash >= BUY_1ST_AMT_BASE:
                        print(f"🔄 {case_label} 반등({self._rebound_count}회) — 매수 허용!")
                        # ★ 반등 시 일반 분석 루프 진행
                    else:
                        self._save_status(cash, total_profit, score_enter, now, pos_mkt_cache)
                        time.sleep(LOOP_SLEEP); continue

                # ── 일시중단 ──────────────────────────────
                if self._is_paused:
                    print("⏸️ [SWING] 일시중단 — 매도 체크만")
                    self._check_all_sells(pos_mkt_cache)
                    self._save_status(cash, total_profit, score_enter, now, pos_mkt_cache)
                    time.sleep(LOOP_SLEEP); continue

                # ── 종목 풀 ───────────────────────────────
                # ★ 09:10 이전이면 매수 스킵 (매도 체크만)
                if not is_buy_ok:
                    print(f"⏳ [SWING] {BUY_START_TIME} 이전 — 매도 체크만")
                    self._check_all_sells(pos_mkt_cache)
                    self._save_status(cash, total_profit, score_enter, now, pos_mkt_cache)
                    time.sleep(LOOP_SLEEP); continue

                # ── 분석 + 매수 ───────────────────────────
                # ★ 2026-09-10: 슬롯/자금 부족 판단을 종목풀 조회보다 먼저
                #   하도록 순서 유지 — 포지션이 꽉 찼으면 애초에 조회 자체를
                #   건너뜀(키움 조건검색 타임아웃/재시도로 루프가 몇 분씩
                #   걸리는 문제 방지, 대장 신고로 발견됐던 지점).
                # ★ 2026-09-29: sbot×sbo2 통합 — new/모멘텀/추세/완화/
                #   유튜브/S7 전체를 _get_unified_candidates()가 하나의
                #   점수순 랭킹으로 병합, _execute_buys_unified()가 한 번에
                #   순회하며 매수(등급별 사이징/S7전용예산 폐지 등은
                #   각 헬퍼 안에서 처리됨). 키움은 완전 제거, KIS(한투)만 사용.
                익절중 = sum(
                    1 for c in self.positions
                    if self.peak_tracker.get(c, {}).get("stage", 0) >= 1
                )
                보너스 = 익절중 if psbl_cash >= BONUS_SLOT_MIN_CASH else 0
                avail_slots = MAX_POSITIONS - len(self.positions) + 보너스
                if avail_slots <= 0:
                    print(f"⛔ 슬롯 없음 ({len(self.positions)}/{MAX_POSITIONS}) — 종목검색/신규분석 스킵")
                elif psbl_cash < MIN_ANALYSIS_CASH:
                    print(f"💰 주문가능({psbl_cash:,}원) < 최소기준({MIN_ANALYSIS_CASH:,}원) "
                          f"— 종목검색/신규분석 스킵")
                else:
                    buyable = self._get_unified_candidates(now_t, score_enter, psbl_cash)
                    if buyable:
                        self._execute_buys_unified(buyable, now_t, psbl_cash)

                # ── 매도 체크 ─────────────────────────────
                self._check_all_sells(pos_mkt_cache)

                # ── 저녁 보충매수 (15:30 이후, 목표금액 미달분) ──────
                self._run_evening_topup(now_t, psbl_cash)

                # ── 상태 저장 ─────────────────────────────
                self._save_status(cash, total_profit, score_enter, now, pos_mkt_cache)

                time.sleep(LOOP_SLEEP)

            except KeyboardInterrupt:
                self._notify(
                    f"🛑 [SWING] 봇 종료 | "
                    f"{now_kst().strftime('%Y-%m-%d %H:%M:%S')}",
                    critical=True,
                )
                break
            except Exception as e:
                print(f"🚨 [SWING] 루프 오류: {e}")
                import traceback; traceback.print_exc()
                time.sleep(5)

    # ============================================================
    # 상태 저장
    # ============================================================
    def _save_status(self, cash: int, total_profit: float,
                     score_enter: int, now: str, pos_mkt_cache: dict = None):
        _write_status({
            "cash":          cash,
            # ★ 2026-10-06: 키키 !상태가 이 키를 읽는데 저장을 안 해서 항상 "주문가능: 0원"
            "psbl_cash":     self._last_psbl_cash,
            "total_profit":  int(total_profit),
            "positions":     len(self.positions),
            "score_enter":   score_enter,
            "last_update":   now,
            "market_status": self.market_status,
            "market_rate":   self.market_rate,
            "daily_loss":    self.daily_loss_count,
            "code_name_map": self.code_name_map,
            "new_codes":     self.new_codes_list,
            "positions_detail": {
                code: {
                    "name": self.code_name_map.get(code, code),
                    "entry_price": int(pos.get("entry_price", 0)),
                    "current": int(float((pos_mkt_cache or {}).get(code, {}).get("stck_prpr", 0) or pos.get("entry_price", 0))),
                    "rate": round((float((pos_mkt_cache or {}).get(code, {}).get("stck_prpr", 0) or pos.get("entry_price", 0)) - pos.get("entry_price", 0)) / max(pos.get("entry_price", 1), 1) * 100, 2),
                    "qty": pos.get("qty", 0),
                    "buy_tag": "",
                }
                for code, pos in self.positions.items()
            },
        }, peak_tracker=self.peak_tracker)


# ============================================================
# 진입점
# ============================================================
if __name__ == "__main__":
    SBot().run()
