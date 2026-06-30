"""
sbot.py — 영암9 스윙봇 메인 (전면 재구성판)
================================================================
[이 파일이 하는 일 — 비개발자용 설명]

스윙봇은 하루 안에 사고 파는 단타와 달리, 며칠~1주일 보유하는 봇입니다.
- 대상: 시총 1조~20조 중대형주 (안정적인 추세 종목)
- 매수금액: 1종목당 200만원 (단타의 10배)
- 보유종목: 최대 3개 (큰 자금 집중 투자)
- 매도기준: 1차 +8%, 2차 +15%, 손절 -7%

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
  kis_api.py       ← 한투 API (검증됨, 그대로)
  kiwoom_api.py    ← 키움 API (검증됨, 그대로)
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
for _d in ["core", "intelligence", "interface", "bots", ""]:
    _p = _os.path.join(_BASE, _d)
    if _p not in _sys.path:
        _sys.path.insert(0, _p)

import os
import time
import pathlib
import json
import asyncio
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
)
from kis_api       import KisAPI
from kiwoom_api    import KiwoomAPI
from notifier      import Notifier
from sbot_strategy import SwingStrategy
from sbot_analyzer import SwingAnalyzer
from sbot_db       import SwingDB
from risk_manager  import RiskManager
try:
    from account_sync import sync_positions as _sync_positions
except ImportError:
    _sync_positions = None
    print("⚠️ account_sync 없음 → DB 정합성 체크 비활성")
try:
    from telegram_monitor import get_stock_event_bonus as _get_disclosure_bonus
except ImportError:
    def _get_disclosure_bonus(code, bot_type="sbot"): return 0, ""
    print("⚠️ telegram_monitor 없음 → 공시 가산점 비활성")

load_dotenv('/home/free4tak/k-bot/stock_bot/.env')
try:
    from master_db import (
        record_trade    as _master_record,
        upsert_position as _master_upsert,
        remove_position as _master_remove,
    )
except Exception:
    _master_record = None
    _master_upsert = None
    _master_remove = None
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
MAX_POSITIONS    = 5              # 최대 보유 종목 (3→5)

# ★ 5대장주 전용 슬롯 (2026-06-23 추가) — 기존 MAX_POSITIONS와 별개로 운영
#   최근 10일 최고가 대비 -15% 하락 시 매수, ATR 추세추종 로직에 편입
MEGA_CAP_CODES = {
    "005930": "삼성전자",
    "000660": "SK하이닉스",
    "009150": "삼성전기",
    "402340": "SK스퀘어",
    "005380": "현대차",
}
MEGA_CAP_DROP_THRESHOLD = -0.15   # 10일 최고가 대비 -15%
MEGA_CAP_LOOKBACK_DAYS  = 10
MEGA_CAP_BUY_AMT        = 1_000_000
MEGA_CAP_CHECK_INTERVAL = 1800    # 30분마다 체크
BUY_1ST_AMT_BASE = 1_000_000    # 1차 매수 기본 금액 (330K→100만, 5종목)
BUY_SCORE_MIN    = 45             # 후보 최소 점수
BUY_SCORE_ENTER  = 85             # 매수 진입 기준점 (백테스트 검증: PF 2.07, 승률44.9%)
LOOP_SLEEP       = 60
POOL_SIZE        = 100

REG_MARKET_START = "0900"
REG_MARKET_END   = "1530"
BUY_START_TIME   = "0910"         # ★ 09:10 이후 매수
SELL_CHECK_START = "0800"         # ★ 08:00부터 매도 체크
SELL_CHECK_END   = "2000"         # ★ 20:00까지 매도 체크
SLEEP_INTERVAL   = 60

# 키움 조건검색식에서 단타용 키워드는 제외 (스윙엔 부적합)
SKIP_COND_KEYWORDS = ["종가","단타", "장개장", "직후", "시가이탈", "오전중저가", "090930", "당일고가"]

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
    state = _read_state()
    state["last_status"] = status
    state["last_update"] = now_hms()
    # ★ peak_tracker 영속화 (2026-06-28 추가) — 재시작 시 손절가/목표가/
    #   stage/buy_date가 전부 초기화되던 문제 방지. None이 아닐 때만 갱신
    #   (호출하지 않는 다른 경로에서 값이 날아가지 않도록 보호).
    if peak_tracker is not None:
        state["peak_tracker"] = peak_tracker
    write_state(BOT_STATE_FILE, state)


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
        self.kiwoom    = KiwoomAPI()
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
        self.api_fail_count = 0    # ★ API 연속 실패 카운터

        # ── 메모리 캐시 ─────────────────────────────────
        self._tech_cache = {}
        self._flow_cache = {}

        # ── 일일 상태 ─────────────────────────────────────
        self._sold_today_date  = today_str()
        self._holiday_checked  = ""
        self._is_holiday       = False
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

        if self.kiwoom.enabled:
            print(f"✅ 키움 연동 활성화 | 단타 제외: {SKIP_COND_KEYWORDS}")

    # ============================================================
    # 알림
    # ============================================================
    def _notify(self, msg: str, critical: bool = False):
        self.notifier.send(f"[SWING] {msg}", critical=critical)

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
        """키움 조건검색식 (단타 키워드 제외) + new 그룹 종목 합성"""
        if not self.kiwoom.enabled:
            print("⚠️ 키움 없음 — 빈 풀")
            return []
        try:
            loop  = asyncio.new_event_loop()
            codes = loop.run_until_complete(
                self.kiwoom.get_condition_codes(
                    use_keywords=None,           # 모든 조건검색식 가져옴
                    skip_keywords=SKIP_COND_KEYWORDS,  # 단타 제외
                    code_name_map=self.code_name_map,
                    code_tag_map=self.code_tag_map,   # ★ 검색식명 태그 저장
                )
            )
            loop.close()

            if codes:
                st = _read_state()
                # 관심종목 추가
                for wc in st.get("watchlist", []):
                    if wc not in codes and wc.isdigit():
                        codes.append(wc)

                # new 그룹 추가
                try:
                    self._load_new_codes()
                    added = 0
                    for nc in self.new_codes_list:
                        if nc not in codes:
                            codes.append(nc); added += 1
                            if nc not in self.code_tag_map:
                                self.code_tag_map[nc] = "expert"  # new그룹=전문가추천
                    if added:
                        print(f"  🆕 new 종목 {added}개 풀 추가")
                except Exception as e:
                    print(f"⚠️ new 그룹 오류: {e}")

                result = codes[:POOL_SIZE]
                print(f"🎯 스윙 종목 풀: {len(result)}개")
                return result
        except Exception as e:
            print(f"⚠️ 키움 오류: {e}")
            self.kiwoom.reset_token()  # ★ 토큰 초기화 → 다음 호출 시 재발급
        return []

    # ============================================================
    # 매수 / 매도 (★ 핵심 개선)
    # ============================================================
    def _do_buy(self, code: str, price: float, amount: int,
                is_second: bool = False):
        """
        매수 주문 실행.
        ★ 개선: 매수 직후 self.positions 즉시 반영 → 다음 매도 체크에서 누락 방지.
        """
        ok, orgno, odno, qty = self.api.buy(code, price, amount, self.code_name_map)
        if not ok or qty <= 0:
            return
        # ★ 미체결 주문 등록
        self._pending_orders[code] = (orgno or "", odno or "", qty)

        ctx = self.buy_context.get(code, {})
        # ★ 2026-06-29: qty는 더 이상 amount/price로 추정하지 않고 buy()가
        #   반환한 실제 주문 수량을 그대로 사용. 기존 추정 계산(호가단위
        #   보정 전 가격으로 나눔)은 buy() 내부의 정확한 계산(호가단위
        #   보정 + 수수료 반영)과 약 2% 확률로 어긋나, 실제보다 많은 qty가
        #   self.positions에 기록되어 매도 시 "주문가능수량 초과" 에러로
        #   이어질 수 있었음.

        # ★ 매수 직후 메모리 반영
        if not is_second:
            self.positions[code] = {"entry_price": price, "qty": qty, "buy_date": today_str()}
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
        self.db.save_buy(
            code      = code,
            buy_price = price,
            qty       = qty,
            ai_score  = ctx.get("ai_score", 0),
            ai_reason = ctx.get("ai_reason", ""),
            stock_name= self._name(code),
            buy_tag   = self.code_tag_map.get(code, "unknown"),  # ★ 검색식명
        )

        # ★ master_positions 등록
        if _master_upsert:
            try:
                ctx2 = self.buy_context.get(code, {})
                _master_upsert(
                    bot_type      = 'sbot',
                    code          = code,
                    stock_name    = self._name(code),
                    entry_price   = price,
                    current_price = price,
                    qty           = qty,
                    buy_time      = ctx2.get('buy_time', ''),
                    buy_tag       = ctx2.get('buy_tag', ''),
                    ai_score      = ctx2.get('ai_score', 0),
                )
            except Exception as _e:
                print(f'⚠️ master_positions upsert 오류: {_e}')

        if not is_second:
            self.sold_today[code] = now_hms()

    def _do_sell(self, code: str, qty: int, reason: str, sell_price: float):
        """
        매도 주문 실행.
        ★ 개선: 부분 매도 시 buy_context를 절대 삭제하지 않음 (전량일 때만).
        """
        if qty <= 0:
            return

        ok = self.api.sell(code, qty, price=int(sell_price))
        if not ok:
            return

        # 전량/부분 매도 판단
        current_pos  = self.positions.get(code, {})
        held_qty     = current_pos.get("qty", 0)
        is_full_sell = (qty >= held_qty)

        is_loss = "손절" in reason or "본절" in reason
        emoji   = "💔" if is_loss else "💰"
        self._notify(
            f"{emoji} 매도 {code}({self._name(code)}) | {reason} | {qty}주",
            critical=True,
        )

        # DB 저장
        self.db.save_sell(code, sell_price, reason,
                         sold_qty=0 if is_full_sell else qty)
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

        # ★ 손절/본절만 당일 재매수 금지 — 익절/수동매도는 재진입 허용
        if is_loss:
            self.sold_today[code] = now_hms()
            print(f"🚫 [SWING] {code} 손절/본절 → 당일 재매수 금지")

        # 상태 파일에도 sold_today 저장
        st = _read_state()
        st["sold_today"]      = self.sold_today
        st["sold_today_date"] = today_str()
        write_state(BOT_STATE_FILE, st)

    def _do_loss(self):
        """손절 카운터 +1"""
        self.daily_loss_count += 1
        print(f"📉 [SWING] 당일 손절 누적: {self.daily_loss_count}회")
        _update_state(daily_loss=self.daily_loss_count, loss_date=today_str())

    # ============================================================
    # ATR 계산 (스윙은 일봉 변동성)
    # ============================================================
    def _get_atr_rate(self, code: str) -> float:
        """ATR/현재가 비율 (30분 캐시)"""
        if code in self.atr_cache:
            cached_rate, ts = self.atr_cache[code]
            if time.time() - ts < 1800:
                return cached_rate
        try:
            ohlc = self.api.get_daily_ohlc(code, days=20) if hasattr(self.api, 'get_daily_ohlc') else []
            if not ohlc:
                self.atr_cache[code] = (0, time.time())
                return 0
            atr_rate = self.risk.calc_atr_rate(ohlc, period=14)
            self.atr_cache[code] = (atr_rate, time.time())
            return atr_rate
        except Exception:
            return 0

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
        """API 호출 성공/실패 추적 — 연속 실패 시 재시작"""
        if success:
            self.api_fail_count = 0
        else:
            self.api_fail_count += 1
            print(f"⚠️ [SWING] API 실패 {self.api_fail_count}/{API_FAIL_MAX}회")
            if self.api_fail_count >= API_FAIL_MAX:
                print(f"🚨 [SWING] API 연속 {API_FAIL_MAX}회 실패 → 재시작")
                self._notify("🚨 [SWING] API 연속 실패 → 자동 재시작", critical=True)
                import sys; sys.exit(1)  # systemd Restart=on-failure 트리거

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
                self._do_sell(
                    sell_code,
                    self.positions[sell_code]["qty"],
                    "즉시매도(AI비서)",
                    s_price,
                )
                _write_cmd_result(f"✅ [SWING] {sell_code} 즉시매도 완료")
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
            self._do_buy(buy_code, cur, int(cur * buy_qty * 1.01))
            # ★ 공통 헬퍼로 통일 — stop_price/target1/atr_val/buy_date 등
            #   필수 필드 누락 방지 (과거엔 일부만 채워 다음 매도체크에서
            #   KeyError 발생 → 그 이후 보유종목 매도체크 전체가 스킵되는 버그)
            _atr_rate = self._get_atr_rate(buy_code)
            self.peak_tracker[buy_code] = self._make_peak_tracker_entry(
                entry_price=cur, atr_rate=_atr_rate,
            )
            _write_cmd_result(f"✅ [SWING] {buy_code} {buy_qty}주 매수 완료")

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
    # 미너비니 방식 AI 추천
    # ============================================================
    def _get_minervini_pick(self, exclude_codes: set) -> str:
        """
        미너비니 방식으로 AI에게 1종목 추천 요청.
        조건: 200일선 위 + 52주 신고가 근처 + 실적 성장 + 겹치지 않는 종목
        """
        try:
            exclude_list = ", ".join(exclude_codes) if exclude_codes else "없음"
            prompt = (
                "당신은 마크 미너비니 스타일의 한국 주식 스윙 트레이더입니다.\n"
                "아래 조건을 모두 만족하는 한국 주식 1종목만 추천하세요.\n\n"
                "[선정 조건]\n"
                "1) 200일 이동평균선 위에서 거래 중 (장기 상승 추세)\n"
                "2) 52주 신고가 대비 -10% 이내 (신고가 근처)\n"
                "3) 최근 분기 매출 또는 EPS YoY +20% 이상 (실적 성장)\n"
                "4) VCP/컵앤핸들/박스권 등 숨고르기 후 돌파 직전 패턴\n"
                "5) 반도체/2차전지/AI/바이오 등 강세 테마 소속 우선\n\n"
                f"[제외 종목] {exclude_list}\n\n"
                "반드시 아래 JSON으로만 답변:\n"
                '{"code": "종목코드6자리", "reason": "선정이유30자이내"}'
            )
            import anthropic as _ant
            client = _ant.Anthropic()
            msg = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=100,
                messages=[{"role": "user", "content": prompt}],
            )
            import json as _json
            result = _json.loads(msg.content[0].text)
            code = result.get("code", "").strip()
            reason = result.get("reason", "")
            if code and len(code) == 6 and code.isdigit():
                print(f"   🏆 미너비니 AI 추천: {code} | {reason}")
                return code
        except Exception as e:
            print(f"⚠️ 미너비니 AI 오류: {e}")
        return ""

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
        ANALYSIS_DELAY_SEC = 0.15
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
            # ★ 공시 이벤트 가산점 (KIND 채널 실시간)
            disc_bonus, disc_reason = _get_disclosure_bonus(code, bot_type="sbot")
            if disc_bonus != 0:
                score = max(0, min(100, score + disc_bonus))
                reason = f"{reason} | {disc_reason}"
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
            # ★ 공시 이벤트 가산점 (KIND 채널 실시간)
            disc_bonus, disc_reason = _get_disclosure_bonus(code, bot_type="sbot")
            if disc_bonus != 0:
                score = max(0, min(100, score + disc_bonus))
                bonus = f"{bonus} | {disc_reason}" if bonus else disc_reason
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

        # 6) ★ 미너비니 방식 AI 추천 1종목 추가 (슬롯 여유 있을 때만)
        try:
            익절중 = sum(
                1 for c in self.positions
                if self.peak_tracker.get(c, {}).get("stage", 0) >= 1
            )
            보너스 = 익절중 if psbl_cash >= 1_000_000 else 0
            avail = MAX_POSITIONS - len(self.positions) + 보너스
            existing_codes = set(c for c, _, _ in top10)
            existing_codes.update(self.positions.keys())

            if avail > len([c for c, _, _ in top10 if c not in self.positions]):
                miner_code = self._get_minervini_pick(existing_codes)
                if miner_code and miner_code not in existing_codes:
                    miner_data = self.api.get_market_data(miner_code)
                    if miner_data:
                        miner_data["ai_reason"] = "미너비니(200일선+52주신고가+실적)"
                        miner_data["buy_tag"]   = "minervini"
                        top10.append((miner_code, score_enter + 5, miner_data))
                        print(f"  🏆 미너비니 추천: {miner_code}({self._name(miner_code)})")
        except Exception as e:
            print(f"⚠️ 미너비니 추천 오류: {e}")

        # 7) 매수 실행
        self._execute_buys(top10, now_t, score_enter, psbl_cash)

    def _execute_buys(self, top10: list, now_t: str,
                      score_enter: int, psbl_cash: int):
        """매수 가능한 종목 실제 주문"""

        # 1차 익절 후 슬롯 반환 (주문가능금액 100만원 이상일 때만)
        익절중 = sum(
            1 for c in self.positions
            if self.peak_tracker.get(c, {}).get("stage", 0) >= 1
        )
        보너스 = 익절중 if psbl_cash >= 1_000_000 else 0
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

        for code, score, data in top10:
            if slots <= 0:
                break
            if code in self.positions:
                continue
            if data["current_price"] <= 0:
                continue
            if score < score_enter:
                continue
            if code in self.sold_today:
                print(f"🚫 [SWING] 재매수 금지 {code}")
                continue

            # ★ 시장 상태 체크 (약세장이라도 new 종목은 허용)
            is_new = code in self.new_codes_list
            allow, reason = self.risk.allow_buy_in_market(
                self.market_status, is_sector_match=is_new,
            )
            if not allow:
                print(f"⚠️ {reason} {code}")
                continue
            if reason:
                print(f"✅ {reason} {code}")

            # ★ 포지션 사이징
            atr_rate = self._get_atr_rate(code)
            buy_amount = self.risk.calc_buy_amount(
                score=score, atr_rate=atr_rate,
                is_theme=is_new, psbl_cash=psbl_cash,
                code=code,                           # ★ 켈리: 종목별 성과 반영
                db_path="sbot_trade_history.db",     # ★ 켈리: sbot DB 사용
            )

            # ★ 1주도 못 사면 패스
            cur_price = data.get("current_price", 0)
            if cur_price > 0 and buy_amount < cur_price:
                print(f"⏭️ [SWING] {code} 패스 — 예산({buy_amount:,}원) < 주가({cur_price:,}원)")
                continue

            tag = " 🆕new" if is_new else ""
            print(f"🚀 [SWING] 매수 {code} | {score}점 | {fmt_won(buy_amount)}{tag}"
                  + (f" | ATR{atr_rate*100:.1f}%" if atr_rate else ""))

            self.buy_context[code] = {
                "ai_score":   score,
                "ai_reason":  data.get("ai_reason", ""),
                "stock_name": data.get("stock_name", ""),
            }
            # ★ sbo2 교차 보유 방지 (구 nbot 참조 — 경로/구조 모두 sbo2에 맞게 수정)
            try:
                import os as _os, json as _json
                _sbo2_state_path = _os.path.join(
                    _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
                    "lina_bot", "sbo2_state.json")
                sbo2_pos = set()
                if _os.path.exists(_sbo2_state_path):
                    with open(_sbo2_state_path, "r", encoding="utf-8") as _f:
                        sbo2_pos = set(_json.load(_f).get("positions", {}).keys())
            except Exception as _e:
                print(f"⚠️ sbo2 포지션 조회 오류: {_e}")
                sbo2_pos = set()
            if code in sbo2_pos:
                print(f"⛔ {code} sbo2 보유 중 — sbot 매수 제외")
                continue
            self._do_buy(code, data["current_price"], buy_amount)

            # ★ peak_tracker 즉시 초기화 (v3 — ATR 추세추종)
            # ★ 공통 헬퍼로 통일 — 기존엔 buy_date 필드가 빠져 있어서
            #   25일 보유기한 매도 로직이 이 종목에는 평생 작동하지 않는
            #   버그가 있었음 (sbot_strategy.check_sell의 tracker 자동
            #   초기화 분기는 code가 peak_tracker에 "없을 때만" 실행되는데,
            #   여기서 이미 채워 넣으니 그 분기가 다시는 안 돔)
            _entry    = data["current_price"]
            _atr_rate = self._get_atr_rate(code)
            self.peak_tracker[code] = self._make_peak_tracker_entry(
                entry_price=_entry, atr_rate=_atr_rate,
            )
            slots -= 1
            time.sleep(1)

    # ============================================================
    # 매도 체크
    # ============================================================
    def _is_over_hold(self, code: str, pos: dict, max_days: int = 11) -> bool:
        """영업일 기준 max_days 초과 보유 여부"""
        try:
            import datetime as _dt
            buy_date_str = pos.get("buy_date", "")
            if not buy_date_str:
                return False
            buy_date = _dt.datetime.strptime(buy_date_str, "%Y-%m-%d").date()
            today    = _dt.date.today()
            # 영업일 계산 (토/일 제외)
            bdays = 0
            cur = buy_date
            while cur < today:
                cur += _dt.timedelta(days=1)
                if cur.weekday() < 5:  # 월~금
                    bdays += 1
            return bdays >= max_days
        except Exception:
            return False

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
            # ★ 장기보유 청산 (미너비니 종목은 20영업일, 일반은 11영업일)
            buy_tag   = self.buy_context.get(code, {}).get("buy_tag", "")
            is_miner  = (buy_tag == "minervini")
            max_days  = 20 if is_miner else 11
            if self._is_over_hold(code, pos, max_days=max_days):
                cur_price = float(mdata.get("stck_prpr", 0))
                entry     = pos["entry_price"]
                rate      = (cur_price - entry) / entry if entry else 0
                # 미너비니: 수익 +3% 이하면 청산 / 일반: +2% 이하
                thresh = 0.03 if is_miner else 0.02
                if rate <= thresh:
                    self._do_sell(code, pos["qty"],
                                  f"장기보유청산({rate:+.2%})", cur_price)
                    print(f"📅 {code} {max_days}영업일 초과 → 장기보유청산 ({rate:+.2%})")
            # ★ 미너비니 종목: 200일선 이탈 시 즉시 청산
            if is_miner and ma20 > 0:
                cur_price = float(mdata.get("stck_prpr", 0))
                ma60 = float(mdata.get("ma60", 0) or 0)
                if ma60 > 0 and cur_price < ma60 * 0.97:
                    entry = pos["entry_price"]
                    rate  = (cur_price - entry) / entry if entry else 0
                    self._do_sell(code, pos["qty"],
                                  f"미너비니200일이탈({rate:+.2%})", cur_price)
                    print(f"📉 {code} 200일선 이탈 → 미너비니 청산 ({rate:+.2%})")

    # ============================================================
    # 메인 루프
    # ============================================================
    def run(self):
        self._notify(
            f"🚀 [영암9 SWING] 스윙봇 가동\n"
            f"⏰ {now_kst().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"💰 1차:{fmt_won(BUY_1ST_AMT_BASE)} / 최대 {MAX_POSITIONS}종목\n"
            f"🎯 익절:+8%/+15%/+25% | 손절:-7% (1차후 본절-3%)\n"
            f"⏳ 매수: {BUY_START_TIME} 이후\n"
            f"⛔ 단타 제외: {SKIP_COND_KEYWORDS}",
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


        while True:
            try:
                # ★ today를 루프 맨 앞에서 정의
                today = today_str()
                now_t = now_hhmm()
                now   = now_hms()

                # ── 주말 ─────────────────────────────────
                if is_weekend():
                    print(f"😴 [{now}] 주말 — 장 없음")
                    time.sleep(SLEEP_INTERVAL); continue

                # ── 휴장일 ───────────────────────────────
                if self._holiday_checked != today:
                    self._is_holiday      = not self.api.is_market_open()
                    self._holiday_checked = today
                    if self._is_holiday:
                        self._notify(f"🎌 오늘은 휴장일 — 봇 대기")
                if self._is_holiday:
                    print(f"🎌 [{now}] 휴장일 — 대기 중...")
                    time.sleep(300); continue

                # ── 시간대별 동작 ─────────────────────────
                is_reg      = REG_MARKET_START <= now_t <= REG_MARKET_END
                is_sell_ok  = SELL_CHECK_START <= now_t <= SELL_CHECK_END
                is_buy_ok   = REG_MARKET_START <= now_t <= REG_MARKET_END and now_t >= BUY_START_TIME

                if not is_sell_ok:
                    print(f"😴 [{now}] 장외 대기 (20시 이후)...")
                    time.sleep(300); continue

                print(f"\n📈 [SWING] {'정규장' if is_reg else '장전/후 매도체크'} [{now}]")

                # ── Heartbeat 기록 ────────────────────────
                pathlib.Path(HB_FILE).touch()

                st              = _read_state()
                self._is_paused = st.get("paused", False)

                # ── 일일 초기화 ──────────────────────────
                if today != self._sold_today_date:
                    self._daily_reset(today)
                else:
                    if not self.sold_today:
                        saved = st.get("sold_today", {})
                        if saved and st.get("sold_today_date") == today:
                            self.sold_today = saved

                # ── 동적 매수 임계치 (스윙은 db.SwingDB.get_recent_performance) ──
                base_score = st.get("score_enter", BUY_SCORE_ENTER)
                perf       = self.db.get_recent_performance(limit=20)
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
                if (st.get("daily_loss") == 0 and self.daily_loss_count > 0
                        and st.get("loss_date") != today):
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
                if new_pos is None:
                    print("⚠️ 실계좌 잔고 조회 실패 — 캐시(기존 positions) 유지, 이번 루프 동기화 스킵")
                    self._check_api_health(False)
                else:
                    self._check_api_health(True)
                    # ★ 수동매도 감지 — 이전 포지션에 있었는데 실계좌에 없으면 감지
                    # ★ 수동매도는 재매수 허용 — sold_today 등록 안 함
                    for _code in list(self.positions.keys()):
                        if _code not in new_pos and _code not in self.sold_today:
                            print(f"🔍 수동매도 감지: {_code} → 재매수 허용")
                    self.positions.clear()
                    self.positions.update(new_pos)
                psbl_cash      = self.api.get_psbl_order_cash("005930")
                if psbl_cash <= 0:
                    psbl_cash = cash
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
                    print(f"  💼 {self._name(code)}({grade}) {rate:+.2f}% | "
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
                # ★ 미체결 주문 취소 (1루프 이상 경과)
                # 1) 체결 완료된 종목 pending에서 먼저 제거
                for _code in list(self.positions.keys()):
                    self._pending_orders.pop(_code, None)
                # 2) 남은 pending = 미체결 → 취소
                for _code, (_orgno, _odno, _qty) in list(self._pending_orders.items()):
                    if _odno:
                        print(f"🚫 [SWING] 미체결 취소: {_code}({self._name(_code)}) odno:{_odno}")
                        ok = self.api.cancel_order(_orgno, _odno, _code, _qty)
                        if ok:
                            self.notify(
                                f"🚫 [SWING] 미체결 취소\n"
                                f"종목: {_code}({self._name(_code)})\n"
                                f"사유: 1루프 내 미체결 → 자금 반환"
                            )
                        # ★ 재매수 방지 — sold_today 등록
                        self.sold_today[_code] = now_hms()
                        # ★ 잔재 정리
                        self.buy_context.pop(_code, None)
                        self.peak_tracker.pop(_code, None)
                    self._pending_orders.pop(_code, None)

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

                codes = self._get_pool()
                if not codes:
                    print("⚠️ 종목 풀 없음")
                    time.sleep(LOOP_SLEEP); continue

                # ── 분석 + 매수 ───────────────────────────
                # ★ 슬롯 없으면 신규 분석 스킵 (캐시는 유지)
                익절중 = sum(
                    1 for c in self.positions
                    if self.peak_tracker.get(c, {}).get("stage", 0) >= 1
                )
                # ★ 주문가능금액 100만원 이상일 때만 보너스 슬롯 적용
                보너스 = 익절중 if psbl_cash >= 1_000_000 else 0
                avail_slots = MAX_POSITIONS - len(self.positions) + 보너스
                if avail_slots <= 0:
                    print(f"⛔ 슬롯 없음 ({len(self.positions)}/{MAX_POSITIONS}) — 신규 분석 스킵")
                else:
                    self._run_analysis(codes, now_t, score_enter, psbl_cash)

                # ── 5대장주 급락 매수 (30분마다, 정규장 중) ──
                if (is_buy_ok and
                        time.time() - self._last_megacap_check > MEGA_CAP_CHECK_INTERVAL):
                    try:
                        self._check_megacap_dip_buy(psbl_cash)
                    except Exception as e:
                        print(f"⚠️ 5대장주 체크 오류: {e}")
                    self._last_megacap_check = time.time()

                # ── 매도 체크 ─────────────────────────────
                self._check_all_sells(pos_mkt_cache)

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
    # ★ 5대장주 급락 매수 (전용 슬롯, 2026-06-23 추가)
    # ============================================================
    def _check_megacap_dip_buy(self, psbl_cash: int):
        """
        삼성전자/SK하이닉스/삼성전기/SK스퀘어/현대차 — 5대장주 중
        최근 10일 최고가 대비 -15% 이상 하락한 종목이 있으면 1개 매수.
        기존 MAX_POSITIONS 슬롯과는 완전히 별개(전용 1슬롯).
        매수 후에는 일반 positions/peak_tracker에 합류시켜
        기존 ATR 추세추종(_check_all_sells)이 그대로 관리하게 함.
        """
        # 이미 5대장주 중 보유중인 종목이 있으면 스킵 (전용슬롯 1개)
        held_megacaps = [c for c in MEGA_CAP_CODES if c in self.positions]
        if held_megacaps:
            return

        candidates = []
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
                    candidates.append((drop_rate, code, name, current, mdata))
            except Exception as e:
                print(f"⚠️ 5대장주 {name} 조회 오류: {e}")
                continue

        if not candidates:
            return

        # 가장 많이 빠진 종목 1개만 매수
        candidates.sort(key=lambda x: x[0])
        drop_rate, code, name, current, mdata = candidates[0]

        amount = min(MEGA_CAP_BUY_AMT, psbl_cash)
        if amount < current:
            print(f"⏭️ 5대장주 {name} 패스 — 예산({amount:,}) < 주가({current:,.0f})")
            return

        ok, orgno, odno, qty = self.api.buy(code, current, amount, {code: name})
        if not ok or qty <= 0:
            print(f"❌ 5대장주 매수 실패: {name}")
            return

        print(f"🛒 [5대장주 급락매수] {name}({code}) | 10일최고대비:{drop_rate:+.1%} | "
              f"{qty}주 @ {current:,.0f}")
        self._notify(
            f"🛒 [5대장주 급락매수] {name}\n"
            f"10일 최고가 대비: {drop_rate:+.1%}\n"
            f"{qty}주 @ {current:,.0f}원",
            critical=True,
        )

        self.positions[code] = {"entry_price": current, "qty": qty}
        self._pending_orders[code] = (orgno or "", odno or "", qty)

        # ATR 기반 손절/목표가 — 기존 추세추종 로직에 그대로 편입
        # ★ 공통 헬퍼로 통일 (기존 자체 ATR 재계산 코드 제거 — _get_atr_rate와
        #   동일한 risk.calc_atr_rate 기반이라 중복이었음)
        _atr_rate = self._get_atr_rate(code)
        self.peak_tracker[code] = self._make_peak_tracker_entry(
            entry_price=current, atr_rate=_atr_rate, buy2_done=True,
        )
        if code not in self.code_name_map:
            self.code_name_map[code] = name

    # ============================================================
    # 상태 저장
    # ============================================================
    def _save_status(self, cash: int, total_profit: float,
                     score_enter: int, now: str, pos_mkt_cache: dict = None):
        _write_status({
            "cash":          cash,
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
