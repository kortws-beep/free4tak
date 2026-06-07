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
import json
import asyncio
import datetime
from dotenv import load_dotenv
import sqlite3 as _sqlite3

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
MAX_POSITIONS    = 3              # 최대 보유 종목
BUY_1ST_AMT_BASE = 330_000      # 1차 매수 기본 금액 (점수 따라 ±)
BUY_SCORE_MIN    = 45             # 후보 최소 점수
BUY_SCORE_ENTER  = 80             # 매수 진입 기준점
LOOP_SLEEP       = 60
POOL_SIZE        = 100

REG_MARKET_START = "0900"
REG_MARKET_END   = "1530"
BUY_START_TIME   = "0920"         # 09:20 이후만 매수
SLEEP_INTERVAL   = 60

# 키움 조건검색식에서 단타용 키워드는 제외 (스윙엔 부적합)
SKIP_COND_KEYWORDS = ["종가","단타", "장개장", "직후", "시가이탈", "오전중저가", "090930", "당일고가"]

# 약세장 방어
MARKET_WEAK_THRESH = -1.5
MARKET_STOP_THRESH = -3.0
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

def _write_status(status: dict):
    state = _read_state()
    state["last_status"] = status
    state["last_update"] = now_hms()
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
        self._ws               = None  # 웹소켓 (미사용 시 None)
        self._kospi_low         = 0.0   # ★ 코스피 최저점 추적
        self._rebound_count     = 0     # ★ 연속 반등 횟수
        self._prefer_kosdaq     = False  # ★ 코스닥 강세 시 우선
        self.market_rate       = 0.0
        self.daily_loss_count  = 0
        self.new_codes_list    = []
        self._last_market_check = 0

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
        ok, orgno, odno = self.api.buy(code, price, amount, self.code_name_map)
        if not ok:
            return
        # ★ 미체결 주문 등록
        self._pending_orders[code] = (orgno or "", odno or "", amount // price if price > 0 else 0)

        ctx = self.buy_context.get(code, {})
        qty = max(int(amount / price), 1) if price > 0 else 0

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

        ok = self.api.sell(code, qty)
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

        self.sold_today[code] = now_hms()

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
            self.peak_tracker[buy_code] = {
                "peak_rate": 0.0, "stage": 0,
                "remain_qty": buy_qty, "buy2_done": True,
                "buy1_price": cur, "effective_entry": cur,
            }
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
        rule_candidates = []
        for idx, code in enumerate(new_codes):
            print(f"🔎 분석 {idx+1}/{len(new_codes)}: {code}", end="")
            data, rule_score = self._analyze_one_code(code)
            if data is not None:
                rule_candidates.append((code, rule_score, data))

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

        # 6) ★ 미너비니 방식 AI 추천 1종목 추가 (슬롯 여유 있을 때만)
        try:
            익절중 = sum(
                1 for c in self.positions
                if self.peak_tracker.get(c, {}).get("stage", 0) >= 1
            )
            avail = MAX_POSITIONS - len(self.positions) + 익절중
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

        # 1차 익절 후 슬롯 반환
        익절중 = sum(
            1 for c in self.positions
            if self.peak_tracker.get(c, {}).get("stage", 0) >= 1
        )
        slots = MAX_POSITIONS - len(self.positions) + 익절중
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
            # ★ nbot 교차 보유 방지
            try:
                from common_utils import read_state as _read_state
                nbot_st  = _read_state("nbot")
                nbot_pos = set(nbot_st.get("last_status", {}).get("positions_detail", {}).keys())
            except Exception:
                nbot_pos = set()
            if code in nbot_pos:
                print(f"⛔ {code} nbot 보유 중 — sbot 매수 제외")
                continue
            self._do_buy(code, data["current_price"], buy_amount)

            # ★ peak_tracker 즉시 초기화
            self.peak_tracker[code] = {
                "peak_rate":       0.0,
                "stage":           0,
                "remain_qty":      max(int(buy_amount / data["current_price"]), 1),
                "buy2_done":       False,
                "buy1_price":      data["current_price"],
                "effective_entry": data["current_price"],
            }
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

                # ── 정규장 시간 ───────────────────────────
                is_reg = REG_MARKET_START <= now_t <= REG_MARKET_END
                if not is_reg:
                    print(f"😴 [{now}] 장외 대기...")
                    time.sleep(SLEEP_INTERVAL); continue

                print(f"\n📈 [SWING] 정규장 [{now}]")

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
                cash           = (self._ws.cash if self._ws and self._ws.cash > 0 else self.api.get_buyable_cash())
                new_pos = self.api.get_current_positions()
                # ★ 수동매도 감지 — 이전 포지션에 있었는데 실계좌에 없으면 sold_today 추가
                for _code in list(self.positions.keys()):
                    if _code not in new_pos and _code not in self.sold_today:
                        self.sold_today[_code] = now_hms()
                        print(f"🔍 수동매도 감지: {_code} → sold_today 추가")
                self.positions.clear()
                self.positions.update(new_pos)
                psbl_cash      = self.api.get_psbl_order_cash("005930")
                if psbl_cash <= 0:
                    psbl_cash = cash
                print(f"\n⏰ {now} | 💵 예수금: {cash:,} | 💰 주문가능: {psbl_cash:,}")

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
                    print(f"  💰 {code}({self._name(code)}) | {rate:+.2f}% | {qty}주")
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
                avail_slots = MAX_POSITIONS - len(self.positions) + 익절중
                if avail_slots <= 0:
                    print(f"⛔ 슬롯 없음 ({len(self.positions)}/{MAX_POSITIONS}) — 신규 분석 스킵")
                else:
                    self._run_analysis(codes, now_t, score_enter, psbl_cash)

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
        })


# ============================================================
# 진입점
# ============================================================
if __name__ == "__main__":
    SBot().run()
