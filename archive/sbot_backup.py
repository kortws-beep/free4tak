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
import os
import time
import json
import asyncio
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
from sbot_strategy import SwingStrategy
from sbot_analyzer import SwingAnalyzer
from sbot_db       import SwingDB
from risk_manager  import RiskManager

load_dotenv()


# ============================================================
# 상수 (튜닝 포인트)
# ============================================================
MAX_POSITIONS    = 3              # 최대 보유 종목
BUY_1ST_AMT_BASE = 2_000_000      # 1차 매수 기본 금액 (점수 따라 ±)
BUY_SCORE_MIN    = 45             # 후보 최소 점수
BUY_SCORE_ENTER  = 60             # 매수 진입 기준점
LOOP_SLEEP       = 60
POOL_SIZE        = 100

REG_MARKET_START = "0900"
REG_MARKET_END   = "1530"
BUY_START_TIME   = "0920"         # 09:20 이후만 매수
SLEEP_INTERVAL   = 60

# 키움 조건검색식에서 단타용 키워드는 제외 (스윙엔 부적합)
SKIP_COND_KEYWORDS = ["단타", "장개장", "직후", "시가이탈", "오전중저가", "090930", "당일고가"]

# 약세장 방어
MARKET_WEAK_THRESH = -1.5
MARKET_STOP_THRESH = -2.5
MAX_DAILY_LOSS     = 2

# 종목 기준
MKT_CAP_MIN = 10000     # 1조원 (스윙은 대형주)
MKT_CAP_MAX = 200000    # 20조원
MIN_PRICE   = 5000
MAX_PRICE   = 2_000_000

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
        ok = self.api.buy(code, price, amount, self.code_name_map)
        if not ok:
            return

        ctx = self.buy_context.get(code, {})
        qty = max(int(amount / price), 1) if price > 0 else 0

        # ★ 매수 직후 메모리 반영
        if not is_second:
            self.positions[code] = {"entry_price": price, "qty": qty}
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

        # ★ 핵심: 전량 매도일 때만 컨텍스트 정리
        if is_full_sell:
            self.buy_context.pop(code, None)
            self.positions.pop(code, None)
        else:
            # 부분 매도: 잔량만 갱신 (entry_price 유지)
            self.positions[code] = {
                "entry_price": current_pos.get("entry_price", sell_price),
                "qty":         held_qty - qty,
            }

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
        basic = self.api.get_market_data(code)
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
            print(f"   🧠 {code} | 룰:{rule_score}→AI:{score}점 | {reason}")
            data["ai_reason"] = reason
            self.score_cache[code] = (score, data)

        # 3) AI 분석 안 한 종목은 룰 점수 + new 가점만
        for code, rule_score, data in rest:
            score, bonus = self.strategy.apply_new_bonus(code, rule_score, self.new_codes_list)
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

        # 6) 매수 실행
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

            tag = " 🆕new" if is_new else ""
            print(f"🚀 [SWING] 매수 {code} | {score}점 | {fmt_won(buy_amount)}{tag}"
                  + (f" | ATR{atr_rate*100:.1f}%" if atr_rate else ""))

            self.buy_context[code] = {
                "ai_score":   score,
                "ai_reason":  data.get("ai_reason", ""),
                "stock_name": data.get("stock_name", ""),
            }
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
    def _check_all_sells(self, pos_mkt_cache: dict):
        """모든 보유 종목 매도 체크"""
        for code, pos in list(self.positions.items()):
            mdata = pos_mkt_cache.get(code) or self.api.get_market_data(code)
            if not mdata:
                continue
            tech     = self._tech_cache.get(code, ({}, 0))
            ma20     = tech[0].get("ma20", 0) if isinstance(tech, tuple) else 0
            atr_rate = self._get_atr_rate(code)

            self.strategy.check_sell(
                code, pos, mdata, self.market_status,
                self.peak_tracker, self._is_paused,
                lambda c, p, a: self._do_buy(c, p, a, is_second=True),
                lambda c, q, r, sp: self._do_sell(c, q, r, sp),
                self._do_loss,
                ma20=ma20, atr_rate=atr_rate,
            )

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
                cash           = self.api.get_buyable_cash()
                self.positions = self.api.get_current_positions()
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
                print(f"📈 총손익: {int(total_profit):,}원")

                # ── 시장 상태 (5분마다) ────────────────────
                if time.time() - self._last_market_check > 300:
                    self._update_market_status()
                    self._last_market_check = time.time()

                # ── 시장 stop ─────────────────────────────
                if self.market_status == "stop":
                    print(f"🚨 [SWING] 시장 중단 모드 | 코스피:{self.market_rate:+.2f}%")
                    self._check_all_sells(pos_mkt_cache)
                    time.sleep(LOOP_SLEEP); continue

                # ── 일시중단 ──────────────────────────────
                if self._is_paused:
                    print("⏸️ [SWING] 일시중단 — 매도 체크만")
                    self._check_all_sells(pos_mkt_cache)
                    self._save_status(cash, total_profit, score_enter, now)
                    time.sleep(LOOP_SLEEP); continue

                # ── 종목 풀 ───────────────────────────────
                codes = self._get_pool()
                if not codes:
                    print("⚠️ 종목 풀 없음")
                    time.sleep(LOOP_SLEEP); continue

                # ── 분석 + 매수 ───────────────────────────
                self._run_analysis(codes, now_t, score_enter, psbl_cash)

                # ── 매도 체크 ─────────────────────────────
                self._check_all_sells(pos_mkt_cache)

                # ── 상태 저장 ─────────────────────────────
                self._save_status(cash, total_profit, score_enter, now)

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
                     score_enter: int, now: str):
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
        })


# ============================================================
# 진입점
# ============================================================
if __name__ == "__main__":
    SBot().run()
