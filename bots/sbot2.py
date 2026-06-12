"""
sbot2.py — 영암9 중단기 스윙봇2
================================================================
[컨셉]
  sbot1(단기 10일)과 차별화된 중단기(20일) 전략.
  기술적 타점 + 재료 + 수급이 모두 맞아떨어질 때만 진입.

[sbot1 vs sbot2 차별화]
  sbot1: 단기 모멘텀, 10영업일, +8/+15/+25% 익절, -7% 손절
  sbot2: 추세/눌림목/VCP, 20영업일, +15/+25/+40% 익절, -10% 손절
         같은 종목 동시 보유 금지 (master_db 교차 체크)

[진입 전략]
  [필수] 20일선 위 + 정배열 + 눌림목/VCP
  [가산] 실적 턴어라운드 + 수급 전환 + 공시/텔레그램 연동

[모듈 구조]
  sbot2.py          ← 메인 루프 (이 파일)
  sbot2_strategy.py ← 중단기 전략
  sbot_analyzer.py  ← AI 분석 (공유)
  sbot_db.py        ← DB (공유, 테이블명만 다름)
  kis_api.py        ← 한투 API (공유)
  kiwoom_api.py     ← 키움 API (공유)
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
import datetime
from dotenv import load_dotenv

from common_utils  import (
    now_kst, now_hhmm, now_hms, today_str,
    is_weekend, safe_int, safe_float,
    read_state, write_state, update_state,
    fmt_won, fmt_pct,
)
from kis_api       import KisAPI
from kiwoom_api    import KiwoomAPI
from notifier      import Notifier
from sbot2_strategy import MidSwingStrategy
from sbot_analyzer  import SwingAnalyzer
from sbot_db        import SwingDB
from risk_manager   import RiskManager

try:
    from account_sync import sync_positions as _sync_positions
except ImportError:
    _sync_positions = None
    print("⚠️ account_sync 없음")
try:
    from telegram_monitor import get_stock_event_bonus as _get_disclosure_bonus
except ImportError:
    def _get_disclosure_bonus(code, bot_type="sbot2"): return 0, ""
try:
    from master_db import (
        record_trade    as _master_record,
        upsert_position as _master_upsert,
        remove_position as _master_remove,
        get_all_positions as _get_all_positions,
    )
except Exception:
    _master_record = _master_upsert = _master_remove = _get_all_positions = None

load_dotenv('/home/free4tak/k-bot/stock_bot/.env')

SECTOR_MONITOR_DB = '/home/free4tak/k-bot/stock_bot/sector_monitor.db'

# ============================================================
# 상수
# ============================================================
MAX_POSITIONS    = 5
BUY_1ST_AMT_BASE = 1_000_000    # 100만원 × 5종목 = 500만원
BUY_SCORE_MIN    = 50
BUY_SCORE_ENTER  = 75            # sbot1(80)보다 낮게 — 중단기는 여유있게
LOOP_SLEEP       = 60
POOL_SIZE        = 100

REG_MARKET_START = "0900"
REG_MARKET_END   = "1530"
BUY_START_TIME   = "0910"        # ★ 09:10 이후 매수
SELL_CHECK_START = "0800"        # ★ 08:00부터 매도 체크
SELL_CHECK_END   = "2000"        # ★ 20:00까지 매도 체크

# 키움 조건검색 제외 키워드 (단기용 제외)
SKIP_COND_KEYWORDS = ["종가","단타","장개장","직후","시가이탈",
                       "오전중저가","090930","당일고가"]

# ★ sbot2 전용: 키움에서 사용할 조건식 (new그룹 + 실적호전만)
SBOT2_COND_KEYWORDS = ["실적호전"]  # 이 키워드 포함된 조건식만 사용
SECTOR_MONITOR_DB   = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "sector_monitor.db"
)

# 시장 방어
MARKET_WEAK_THRESH = -2.0
MARKET_STOP_THRESH = -4.5
MAX_DAILY_LOSS     = 3   # 중단기는 더 보수적

# 종목 기준 (중단기 — 중대형주)
MKT_CAP_MIN = 3000      # 3000억 이상
MIN_PRICE   = 3000
MAX_PRICE   = 5_000_000

BOT_STATE_FILE = "sbot2_state.json"


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


# ============================================================
# 메인 봇 클래스
# ============================================================
class SBot2:
    """중단기 스윙봇2 본체."""

    def __init__(self):
        print("🚀 [영암9 MID-SWING] 중단기봇2 가동")

        # ── KIS API (sbot1과 동일 계좌 사용) ──────────────
        # ★ nbot 계좌 사용 (KIS_APPKEY — 기본 계좌)
        self.api = KisAPI(
            appkey=os.getenv("KIS_APPKEY"),
            secret=os.getenv("KIS_SECRET"),
            cano  =os.getenv("KIS_CANO"),
            acnt  =os.getenv("KIS_ACNT_PRDT_CD"),
        )
        self.kiwoom   = KiwoomAPI()
        self.notifier = Notifier(name="sbot2")
        self.strategy = MidSwingStrategy()
        self.ai       = SwingAnalyzer()
        self.db       = SwingDB()   # sbot_trade_history.db 공유 (sbot2 레코드는 ai_reason으로 구분)
        self.risk     = RiskManager(
            base_buy_amt         = BUY_1ST_AMT_BASE,
            max_daily_loss_count = MAX_DAILY_LOSS,
        )

        self.ai.init_db()
        self.db.init_db()

        # ── 거래 상태 ─────────────────────────────────────
        self.positions       = {}
        self._pending_orders = {}
        self.score_cache     = {}
        self.buy_context     = {}
        self.peak_tracker    = {}
        self.sold_today      = {}
        self.code_name_map   = {}
        self.atr_cache       = {}
        self._tech_cache     = {}
        self._vol_ratio_cache = {}

        self.market_status   = "normal"
        self.daily_loss_count = 0
        self._is_paused      = False
        self._last_market_check = 0
        self._prefer_kosdaq  = False
        self._prefer_kospi   = False

        self.coin_pool       = []
        self.new_codes_list  = []

    # ============================================================
    # 알림 헬퍼
    # ============================================================
    def _notify(self, msg: str, critical: bool = False):
        try:
            self.notifier.send(msg)
        except Exception as e:
            print(f"⚠️ 알림 오류: {e}")

    def _name(self, code: str) -> str:
        return self.code_name_map.get(code, code)

    # ============================================================
    # 시장 상태 판단
    # ============================================================
    def _update_market_status(self):
        now_ts = time.time()
        if now_ts - self._last_market_check < 120:
            return
        self._last_market_check = now_ts
        try:
            idx = self.api.get_market_index()
            kospi  = safe_float(idx.get("kospi_rate",  0))
            kosdaq = safe_float(idx.get("kosdaq_rate", 0))
            if   kospi <= MARKET_STOP_THRESH:  status = "stop"
            elif kospi <= MARKET_WEAK_THRESH:  status = "weak"
            else:                              status = "normal"
            if status != self.market_status:
                self._notify(f"📊 시장상태 변경: {self.market_status}→{status} | 코스피:{kospi:.2f}%")
            self.market_status = status
        except Exception as e:
            print(f"⚠️ 시장상태 체크 오류: {e}")

    # ============================================================
    # ★ sbot1 교차 보유 방지
    # ============================================================
    def _is_in_sbot1(self, code: str) -> bool:
        """sbot1(master_db)에서 이미 보유 중인 종목이면 True"""
        if _get_all_positions is None:
            return False
        try:
            all_pos = _get_all_positions()
            sbot1_pos = {p["code"] for p in all_pos
                         if p.get("bot_type") in ("sbot", "sbot1")}
            return code in sbot1_pos
        except Exception:
            return False

    # ============================================================
    # 매수/매도 실행
    # ============================================================
    def _do_buy(self, code: str, price: int, amount: int,
                is_second: bool = False):
        qty = max(1, amount // price)
        _res  = self.api.buy(code, price, price * qty, self.code_name_map)
        if isinstance(_res, (list, tuple)) and len(_res) == 3:
            ok, orgno, odno = _res
        elif isinstance(_res, (list, tuple)) and len(_res) == 2:
            ok, orgno, odno = _res[0], "", ""
        else:
            ok, orgno, odno = False, "", ""
        if not ok:
            print(f"❌ 매수 실패 {code}: {err_msg}")
            return False

        import datetime as _dt
        if not is_second:
            self.positions[code] = {
                "entry_price": price,
                "qty":         qty,
                "buy_date":    _dt.date.today().isoformat(),
            }

        # master_db 기록
        if _master_upsert:
            try:
                _master_upsert("sbot2", code, entry_price=price, qty=qty)
            except Exception as e:
                print(f"⚠️ master_db upsert 오류: {e}")

        self.db.save_buy(
            code       = code,
            buy_price  = price,
            qty        = qty,
            ai_score   = self.score_cache.get(code, (0, {}))[0],
            ai_reason  = self.buy_context.get(code, {}).get("reason", ""),
            stock_name = self._name(code),
        )

        # peak_tracker 초기화
        self.peak_tracker[code] = {
            "peak_rate":       0.0,
            "stage":           0,
            "remain_qty":      qty,
            "buy2_done":       True,
            "buy1_price":      price,
            "effective_entry": price,
            "holding_days":    0,
            "buy_date":        _dt.date.today().isoformat(),
        }

        self._notify(
            f"🛒 [MID] 매수 {code}({self._name(code)})\n"
            f"가격:{price:,}원 | {qty}주 | {amount//10000}만원"
        )
        return True

    def _do_sell(self, code: str, qty: int, reason: str, price: float):
        if qty <= 0:
            return
        ok = self.api.sell(code, int(price), qty, self.code_name_map)
        entry = self.positions.get(code, {}).get("entry_price", price)
        rate  = (price - entry) / entry if entry else 0

        self.db.save_sell(code=code, sell_price=int(price),
                            qty=qty, sell_reason=reason)
        if _master_record:
            try:
                _master_record(code, entry, price, qty, reason, bot_type="sbot2")
            except Exception:
                pass

        remaining = self.positions.get(code, {}).get("qty", qty) - qty
        if remaining <= 0:
            self.positions.pop(code, None)
            self.peak_tracker.pop(code, None)
            self.buy_context.pop(code, None)
            self.sold_today[code] = now_hms()
            if _master_remove:
                try:
                    _master_remove(code, bot_type="sbot2")
                except Exception:
                    pass
        else:
            self.positions[code]["qty"] = remaining
            if code in self.peak_tracker:
                self.peak_tracker[code]["remain_qty"] = remaining

        emoji = "✅" if rate >= 0 else "❌"
        self._notify(
            f"{emoji} [MID] 매도 {code}({self._name(code)})\n"
            f"사유:{reason} | {rate:+.2f}% | {qty}주"
        )

    def _do_loss(self):
        self.daily_loss_count += 1
        if self.daily_loss_count >= MAX_DAILY_LOSS:
            self._is_paused = True
            _update_state(paused=True, loss_date=today_str())
            self._notify(f"⏸️ [MID] 일일 손절 {MAX_DAILY_LOSS}회 → 일시중단")

    # ============================================================
    # 종목 풀 구성 (sbot1 공유)
    # ============================================================
    def _load_new_codes(self):
        """한투 관심그룹 new에서 신규 추천 종목 로딩 (sbot 방식)"""
        try:
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
                print("  ⚠️ [MID] new 관심그룹 없음")
                return
            grp_code, grp_name = target
            print(f"  🆕 [MID] new그룹 발견: [{grp_code}]{grp_name}")
            stocks = self.api.get_watchlist_stocks(grp_code, hts_id, self.code_name_map)
            self.new_codes_list = [c for c, _ in stocks]
            print(f"  🆕 [MID] new그룹 종목: {len(self.new_codes_list)}개")
        except Exception as e:
            print(f"⚠️ [MID] new 그룹 로드 오류: {e}")
            self.new_codes_list = []

    def _build_pool(self) -> list:
        """
        sbot2 종목 풀 구성:
          1. sector_monitor.db 등장 빈도 상위 종목 (주도섹터 대장주)
          2. 키움 new그룹
          3. 키움 실적호전 조건식
        """
        import sqlite3 as _sl

        codes = []

        # ── 1. sector_monitor 주도섹터 대장주 ─────────────────
        try:
            conn = _sl.connect(SECTOR_MONITOR_DB, timeout=5)
            rows = conn.execute("""
                SELECT code, AVG(trde_amt) as amt, COUNT(*) as cnt
                FROM stock_momentum
                WHERE ts >= datetime('now', '-30 days')
                GROUP BY code
                HAVING cnt >= 3
                ORDER BY amt DESC
                LIMIT 200
            """).fetchall()
            conn.close()
            sector_codes = [r[0] for r in rows]
            codes.extend(sector_codes)
            print(f"  📊 [MID] sector_monitor: {len(sector_codes)}개")
        except Exception as e:
            print(f"⚠️ [MID] sector_monitor 오류: {e}")

        # ── 2. 키움 new그룹 + 실적호전 조건식 ────────────────
        if self.kiwoom.enabled:
            try:
                import asyncio as _asyncio
                loop  = _asyncio.new_event_loop()
                cond_codes = loop.run_until_complete(
                    self.kiwoom.get_condition_codes(
                        use_keywords=SBOT2_COND_KEYWORDS,  # 실적호전만
                        skip_keywords=SKIP_COND_KEYWORDS,
                        code_name_map=self.code_name_map,
                    )
                )
                loop.close()
                for c in cond_codes:
                    if c not in codes:
                        codes.append(c)
                print(f"  📋 [MID] 실적호전 조건: {len(cond_codes)}개")
            except Exception as e:
                print(f"⚠️ [MID] 조건검색 오류: {e}")

            # new 그룹
            try:
                self._load_new_codes()
                added = 0
                for nc in self.new_codes_list:
                    if nc not in codes:
                        codes.append(nc); added += 1
                if added:
                    print(f"  🆕 [MID] new그룹: {added}개")
            except Exception as e:
                print(f"⚠️ [MID] new그룹 오류: {e}")

        # ── 3. sbot1 교차 제외 + 현재 보유 제외 ──────────────
        if _get_all_positions:
            try:
                sbot1_codes = {p["code"] for p in _get_all_positions()
                               if p.get("bot_type") in ("sbot", "sbot1")}
                before = len(codes)
                codes = [c for c in codes if c not in sbot1_codes]
                if before != len(codes):
                    print(f"  ⏭️ [MID] sbot1 교차 제외: {before-len(codes)}개")
            except Exception:
                pass

        codes = list(dict.fromkeys(c for c in codes if c not in self.positions))
        result = codes[:POOL_SIZE]
        print(f"🎯 [MID] 종목 풀: {len(result)}개")
        return result

    # ============================================================
    # 개별 종목 분석
    # ============================================================
    def _analyze_one_code(self, code: str) -> tuple:
        """(data, rule_score) 반환. 실패 시 (None, 0)"""
        try:
            basic = self.api.get_market_data(code)
            if not basic:
                return None, 0

            cur     = safe_float(basic.get("stck_prpr",  0))
            change  = safe_float(basic.get("prdy_ctrt",  0))
            tvol    = safe_int(basic.get("acml_tr_pbmn",  0)) // 100_000_000
            mkt_cap = safe_int(basic.get("hts_avls",      0))

            if cur < MIN_PRICE or cur > MAX_PRICE:
                print(f" ⏭️ 가격 범위 제외 {code}")
                return None, 0
            if mkt_cap < MKT_CAP_MIN:
                print(f" ⏭️ 시총 제외 {code}")
                return None, 0

            # 기술적 지표
            tech = self.api.get_technical_indicators(code, self.atr_cache)
            if not tech:
                return None, 0

            ma5   = safe_float(tech.get("ma5",   0))
            ma20  = safe_float(tech.get("ma20",  0))
            ma60  = safe_float(tech.get("ma60",  0))
            ma120 = safe_float(tech.get("ma120", 0))
            rsi   = safe_float(tech.get("rsi",   50))
            bb_width = safe_float(tech.get("bb_width", 0))

            self._tech_cache[code] = (tech, time.time())
            self.code_name_map[code] = basic.get("hts_kor_isnm", code)

            # 52주 고가/저가
            high_52w = safe_float(basic.get("stck_drgn_pbmn", 0) or
                                   basic.get("w52_hgpr",       0))
            low_52w  = safe_float(basic.get("stck_drwn_pbmn", 0) or
                                   basic.get("w52_lwpr",       0))

            # 수급 (5일 + ★ 15일 누적)
            foreign_5d   = safe_float(tech.get("foreign_5d",      0))
            orgn_5d      = safe_float(tech.get("institution_5d",  0))
            foreign_15d  = safe_float(tech.get("foreign_15d",     0))
            orgn_15d     = safe_float(tech.get("institution_15d", 0))

            # 실적/펀더멘탈
            roe    = safe_float(basic.get("roe",   0))
            op_yoy = safe_float(basic.get("op_yoy", 0))

            # 거래량 비율
            vol_ratio = self._get_vol_ratio(code, basic)

            data = {
                "current_price":    cur,
                "change_rate":      change,
                "trading_value":    tvol,
                "mkt_cap":          mkt_cap,
                "ma5":              ma5,
                "ma20":             ma20,
                "ma60":             ma60,
                "ma120":            ma120,
                "rsi":              rsi,
                "bb_width":         bb_width,
                "high_52w":         high_52w,
                "low_52w":          low_52w,
                "foreign_5d":       foreign_5d,
                "institution_5d":   orgn_5d,
                "foreign_15d":      foreign_15d,     # ★ 15일 누적
                "institution_15d":  orgn_15d,        # ★ 15일 누적
                "foreign_today":    safe_float(basic.get("frgn_ntby_qty", 0)),
                "orgn_today":       safe_float(basic.get("orgn_ntby_qty", 0)),
                "volume_ratio":     vol_ratio,
                "roe":              roe,
                "op_yoy":           op_yoy,
                "iscd_stat_cls_code": basic.get("iscd_stat_cls_code", "55"),
                "stck_oprc":        safe_float(basic.get("stck_oprc", 0)),
                "prdy_clpr":        safe_float(basic.get("prdy_clpr", 0)),
            }

            # 필터 통과 체크
            ok, reason = self.strategy.passes_buy_filter(data)
            if not ok:
                print(f" ⏭️ {code} — {reason}")
                return None, 0

            rule_score = self.strategy.get_rule_score(data)
            print(f" [{code}] 룰점수:{rule_score}")
            return data, rule_score

        except Exception as e:
            print(f"⚠️ 분석 오류 {code}: {e}")
            return None, 0

    def _get_vol_ratio(self, code: str, mdata: dict) -> float:
        now_ts = time.time()
        cached = self._vol_ratio_cache.get(code)
        if cached and now_ts - cached[1] < 300:
            return cached[0]
        try:
            vi = float(mdata.get("vol_inrt", 0) or 0)
            if vi != 0:
                vr = 100.0 + vi
                self._vol_ratio_cache[code] = (vr, now_ts)
                return vr
        except Exception:
            pass
        self._vol_ratio_cache[code] = (0.0, now_ts)
        return 0.0

    # ============================================================
    # 분석 + 매수
    # ============================================================
    def _run_analysis(self, codes: list, now_t: str,
                      score_enter: int, psbl_cash: int):
        new_codes    = [c for c in codes if c not in self.score_cache]
        cached_codes = [c for c in codes if c in self.score_cache]
        print(f"\n🔄 [MID] 분석: 신규 {len(new_codes)}개 | 캐시 {len(cached_codes)}개")

        # 1) 룰 점수
        rule_candidates = []
        for idx, code in enumerate(new_codes):
            print(f"🔎 [MID] {idx+1}/{len(new_codes)}: {code}", end="")
            data, rule_score = self._analyze_one_code(code)
            if data is not None:
                rule_candidates.append((code, rule_score, data))

        # 2) 상위 10개 AI 분석
        rule_candidates.sort(key=lambda x: x[1], reverse=True)
        top_ai = rule_candidates[:10]
        rest   = rule_candidates[10:]

        for code, rule_score, data in top_ai:
            ai_result = self.ai.analyze(code, data, self.new_codes_list)
            score     = ai_result["score"]
            reason    = ai_result["reason"]
            score, bonus = self.strategy.apply_bonus(code, score, self.new_codes_list)
            if bonus:
                reason = f"{reason} | {bonus}"
            # 공시 가산점
            disc_bonus, disc_reason = _get_disclosure_bonus(code, bot_type="sbot2")
            if disc_bonus != 0:
                score = max(0, min(100, score + disc_bonus))
                reason = f"{reason} | {disc_reason}"
            print(f"   🧠 [MID] {code} | 룰:{rule_score}→AI:{score}점 | {reason}")
            data["ai_reason"] = reason
            self.score_cache[code] = (score, data)

        for code, rule_score, data in rest:
            score, bonus = self.strategy.apply_bonus(code, rule_score, self.new_codes_list)
            disc_bonus, disc_reason = _get_disclosure_bonus(code, bot_type="sbot2")
            if disc_bonus != 0:
                score = max(0, min(100, score + disc_bonus))
                bonus = f"{bonus} | {disc_reason}" if bonus else disc_reason
            data["ai_reason"] = f"룰점수({rule_score})" + (f" | {bonus}" if bonus else "")
            self.score_cache[code] = (score, data)

        # 캐시 정리
        pool_set = set(codes)
        for c in list(self.score_cache):
            if c not in pool_set:
                del self.score_cache[c]

        # 3) 매수 후보
        candidates = [(code, score, data)
                      for code, (score, data) in self.score_cache.items()
                      if score >= BUY_SCORE_MIN]
        candidates.sort(key=lambda x: x[1], reverse=True)

        # 4) 매수 실행
        avail = MAX_POSITIONS - len(self.positions)
        if avail <= 0:
            print(f"⛔ [MID] 슬롯 없음 ({len(self.positions)}/{MAX_POSITIONS})")
            return

        for code, score, data in candidates:
            if avail <= 0:
                break
            if score < score_enter:
                continue
            if code in self.sold_today:
                print(f"  ⏭️ {code} — 당일 매도 종목")
                continue
            if self._is_in_sbot1(code):
                print(f"  ⏭️ {code} — sbot1 보유 중 (교차 방지)")
                continue

            cur  = int(data.get("current_price", 0))
            amt  = self.risk.calc_buy_amount(score=score, psbl_cash=psbl_cash)
            if psbl_cash < amt:
                print(f"  ⏭️ {code} — 자금 부족")
                continue

            self.buy_context[code] = {
                "reason":  data.get("ai_reason", ""),
                "buy_tag": "mid_swing",
            }
            if self._do_buy(code, cur, amt):
                psbl_cash -= amt
                avail     -= 1

    # ============================================================
    # 매도 체크
    # ============================================================
    def _check_all_sells(self, pos_mkt_cache: dict):
        for code, pos in list(self.positions.items()):
            mdata = pos_mkt_cache.get(code) or self.api.get_market_data(code)
            if not mdata:
                continue
            tech  = self._tech_cache.get(code, ({}, 0))
            ma20  = tech[0].get("ma20", 0) if isinstance(tech, tuple) else 0
            vol_ratio = self._get_vol_ratio(code, mdata)

            # ★ 긴급손절 — 손실 종목만 (-3% 이하)
            if self.market_status == "stop":
                entry = pos.get("entry_price", 0)
                cur   = float(mdata.get("stck_prpr", 0))
                if entry > 0 and cur > 0:
                    pnl = (cur - entry) / entry
                    if pnl <= -0.03:
                        self._do_sell(code, pos["qty"],
                                      f"긴급손절(약세장){pnl:+.1%}", cur)
                        self._do_loss()
                        self.peak_tracker.pop(code, None)
                        continue
                    else:
                        print(f"  ⏸️ {code} 긴급손절 제외 ({pnl:+.1%})")

            tech2 = self._tech_cache.get(code, ({}, 0))
            ma200 = tech2[0].get("ma200", 0) if isinstance(tech2, tuple) else 0
            self.strategy.check_sell(
                code, pos, mdata, self.market_status,
                self.peak_tracker, self._is_paused,
                lambda c, p, a: self._do_buy(c, p, a, is_second=True),
                lambda c, q, r, sp: self._do_sell(c, q, r, sp),
                self._do_loss,
                ma20=ma20, ma200=ma200, vol_ratio=vol_ratio,
            )

    # ============================================================
    # 미체결 취소
    # ============================================================
    def _cancel_pending_orders(self):
        for code in list(self.positions.keys()):
            self._pending_orders.pop(code, None)
        for code, (orgno, odno, qty) in list(self._pending_orders.items()):
            if odno:
                print(f"🚫 [MID] 미체결 취소: {code}({self._name(code)})")
                ok = self.api.cancel_order(orgno, odno, code, qty)
                if ok:
                    self._notify(
                        f"🚫 [MID] 미체결 취소\n"
                        f"종목: {code}({self._name(code)})\n"
                        f"사유: 1루프 내 미체결"
                    )
                self.sold_today[code] = now_hms()
                self.buy_context.pop(code, None)
                self.peak_tracker.pop(code, None)
            self._pending_orders.pop(code, None)

    # ============================================================
    # 날짜 변경 — 손절 카운터 리셋
    # ============================================================
    def _check_daily_reset(self, today: str):
        st = _read_state()
        loss_date = st.get("loss_date", "")
        if loss_date and loss_date != today and self.daily_loss_count > 0:
            self.daily_loss_count = 0
            self._is_paused = False
            _update_state(paused=False, daily_loss=0, loss_date=today)
            print(f"♻️ [MID] 날짜변경({loss_date}→{today}) — 손절카운터 리셋")

    # ============================================================
    # ★ 디스코드 명령 처리
    # ============================================================
    def _handle_pending_command(self, st: dict):
        """kiki !매도 / !매수 / !시작 명령 처리"""
        pending = st.get("pending_cmd")
        if not pending:
            return

        cmd_type = pending.get("type", "")
        _update_state(pending_cmd=None)  # 즉시 클리어

        if cmd_type == "sell":
            code = pending.get("code", "")
            if code in self.positions:
                pos = self.positions[code]
                mdata = self.api.get_market_data(code)
                cur = float(mdata.get("stck_prpr", pos["entry_price"])) if mdata else pos["entry_price"]
                self._do_sell(code, pos["qty"], "즉시매도(kiki명령)", cur)
                _update_state(cmd_result=f"✅ {code} {pos['qty']}주 매도 완료")
                print(f"✅ [MID] kiki 매도 명령 처리: {code}")
            else:
                _update_state(cmd_result=f"⚠️ {code} 보유 중이지 않음")

        elif cmd_type == "buy":
            code = pending.get("code", "")
            qty  = pending.get("qty", 0)
            mdata = self.api.get_market_data(code)
            if mdata:
                cur = int(float(mdata.get("stck_prpr", 0)))
                if cur > 0 and qty > 0:
                    amt = cur * qty
                    if self._do_buy(code, cur, amt):
                        _update_state(cmd_result=f"✅ {code} {qty}주 매수 완료")
                    else:
                        _update_state(cmd_result=f"❌ {code} 매수 실패")
                else:
                    _update_state(cmd_result=f"⚠️ 가격/수량 오류")
            else:
                _update_state(cmd_result=f"⚠️ {code} 시세 조회 실패")

        elif cmd_type == "start":
            self.daily_loss_count = 0
            self._is_paused = False
            _update_state(paused=False, daily_loss=0, loss_date=today_str(), cmd_result="✅ 재개 완료")
            print("♻️ [MID] kiki !시작 명령 처리")

    # ============================================================
    # 상태 출력
    # ============================================================
    def _print_status(self, cash: int, score_enter: int, psbl_cash: int):
        paused_str = "⏸️" if self._is_paused else "▶️"
        print(f"{'='*50}")
        print(f"📈 [MID] {paused_str} 기준:{score_enter}점 | 💵 예수금:{cash:,} | 💰 주문가능:{psbl_cash:,}")
        print(f"📊 시장:{self.market_status} | 손절:{self.daily_loss_count}/{MAX_DAILY_LOSS}")
        for code, pos in self.positions.items():
            entry = pos["entry_price"]
            qty   = pos["qty"]
            name  = self._name(code)
            print(f"  💰 {code}({name}) | {entry:,}원 | {qty}주")
        print(f"{'='*50}\n")

    # ============================================================
    # 상태 저장 (키키 !m상태 등 명령어용)
    # ============================================================
    def _save_status(self, cash: int, total_profit: float,
                     score_enter: int, now: str, pos_mkt_cache: dict = None):
        _update_state(last_status={
            "cash":          cash,
            "psbl_cash":     cash,
            "total_profit":  int(total_profit),
            "positions":     len(self.positions),
            "score_enter":   score_enter,
            "last_update":   now,
            "market_status": self.market_status,
            "market_rate":   getattr(self, "market_rate", 0.0),
            "daily_loss":    self.daily_loss_count,
            "code_name_map": self.code_name_map,
            "new_codes":     self.new_codes_list,
            "active_sectors": [],
            "positions_detail": {
                code: {
                    "name":        self.code_name_map.get(code, code),
                    "entry_price": int(pos.get("entry_price", 0)),
                    "current":     int(float(
                        (pos_mkt_cache or {}).get(code, {}).get("stck_prpr", 0)
                        or pos.get("entry_price", 0)
                    )),
                    "rate": round(
                        (float(
                            (pos_mkt_cache or {}).get(code, {}).get("stck_prpr", 0)
                            or pos.get("entry_price", 0)
                        ) - pos.get("entry_price", 0))
                        / max(pos.get("entry_price", 1), 1) * 100, 2
                    ),
                    "qty":     pos.get("qty", 0),
                    "buy_tag": pos.get("buy_tag", "mid_swing"),
                    "buy_date": pos.get("buy_date", ""),
                }
                for code, pos in self.positions.items()
            },
        })

    # ============================================================
    # 메인 루프
    # ============================================================
    def run(self):
        self._notify(
            f"🚀 [영암9 MID-SWING] 중단기봇2 가동\n"
            f"⏰ {now_kst().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"💰 1차:{fmt_won(BUY_1ST_AMT_BASE)} / 최대 {MAX_POSITIONS}종목\n"
            f"📈 익절:+15/+25/+40% | 손절:-10% | 트레일:-8% | 청산:20영업일",
            critical=True,
        )

        # 재시작 시 손절 카운터 자동 초기화
        self.daily_loss_count = 0
        self._is_paused = False
        _update_state(paused=False, daily_loss=0)
        print("♻️ [MID] 재시작 — 손절카운터 초기화")

        # 실계좌 동기화
        if _sync_positions:
            try:
                real = _sync_positions(
                    self.api, "sbot2_trade_history.db",
                    lambda msg: self._notify(msg),
                    bot_type="sbot2",
                )
                if real:
                    self.positions.update(real)
                    print(f"✅ [MID] 실계좌 동기화: {len(real)}종목")
            except Exception as e:
                print(f"⚠️ [MID] 실계좌 동기화 오류: {e}")

        while True:
            try:
                now    = now_kst()
                today  = today_str()
                now_t  = now.strftime("%H%M")

                # 주말/휴장
                if now.weekday() >= 5:
                    print(f"🗓️ [MID] 주말 — 60초 대기")
                    time.sleep(60)
                    continue

                # ★ 20:00 이후 완전 장외 대기
                if now_t > SELL_CHECK_END:
                    print(f"😴 [MID] 장외 대기 (20시 이후)...")
                    time.sleep(300)
                    continue

                # 08:00 이전 대기
                if now_t < SELL_CHECK_START:
                    time.sleep(30)
                    continue

                # 날짜 변경 체크
                self._check_daily_reset(today)

                # 시장 상태 업데이트
                self._update_market_status()

                # 상태 읽기
                st          = _read_state()
                score_enter = st.get("score_enter", BUY_SCORE_ENTER)
                paused      = st.get("paused", False)
                if paused != self._is_paused:
                    self._is_paused = paused

                # ★ 디스코드 명령 처리
                self._handle_pending_command(st)

                # 예수금 + 최대 주문가능금액 (sbot 방식)
                cash      = self.api.get_buyable_cash()
                psbl_cash = self.api.get_psbl_order_cash("005930")
                if psbl_cash <= 0:
                    psbl_cash = cash
                print(f"\n⏰ {now.strftime('%H:%M:%S')} | 💵 예수금: {cash:,} | 💰 주문가능: {psbl_cash:,}")

                # 상태 출력
                self._print_status(cash, score_enter, psbl_cash)

                # 미체결 취소
                self._cancel_pending_orders()

                # 매도 체크
                pos_mkt_cache = {}
                self._check_all_sells(pos_mkt_cache)

                # 매수 (09:10 이후 장중, 일시중단 아닐 때)
                if (REG_MARKET_START <= now_t <= REG_MARKET_END
                        and now_t >= BUY_START_TIME
                        and not self._is_paused
                        and self.market_status != "stop"):

                    avail = MAX_POSITIONS - len(self.positions)
                    if avail > 0 and psbl_cash >= BUY_1ST_AMT_BASE:
                        pool = self._build_pool()
                        print(f"  🔍 [MID] 종목 풀: {len(pool)}개")
                        if pool:
                            self._run_analysis(pool, now_t, score_enter, psbl_cash)
                        else:
                            # ★ kiwoom cond_pool 상태 확인
                            cp = getattr(self.kiwoom, 'cond_pool', [])
                            np = getattr(self.kiwoom, 'new_pool', [])
                            print(f"  ⚠️ [MID] 풀 비어있음 — cond_pool:{len(cp)} new_pool:{len(np)}")
                    else:
                        if avail <= 0:
                            print(f"⛔ [MID] 슬롯 없음 ({len(self.positions)}/{MAX_POSITIONS})")

                # ★ 키키 명령어용 상태 저장 (매 루프 말미)
                try:
                    total_profit = sum(
                        (float(pos_mkt_cache.get(c, {}).get("stck_prpr", 0) or p["entry_price"])
                         - p["entry_price"]) * p.get("qty", 0)
                        for c, p in self.positions.items()
                    )
                    self._save_status(cash, total_profit, score_enter,
                                      now.strftime("%H:%M:%S"), pos_mkt_cache)
                except Exception as _se:
                    print(f"⚠️ [MID] 상태 저장 오류: {_se}")

                time.sleep(LOOP_SLEEP)

            except KeyboardInterrupt:
                print("\n🛑 [MID] 중단기봇2 종료")
                self._notify("🛑 [MID] 중단기봇2 종료")
                break
            except Exception as e:
                print(f"❌ [MID] 루프 오류: {e}")
                import traceback
                traceback.print_exc()
                time.sleep(10)


# ============================================================
# 엔트리포인트
# ============================================================
if __name__ == "__main__":
    bot = SBot2()
    bot.run()