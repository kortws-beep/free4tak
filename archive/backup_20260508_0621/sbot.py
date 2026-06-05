"""
sbot.py — 영암9 스윙봇 메인
========================================================
[실행]
  python3 sbot.py

[모듈 구조]
  sbot.py          ← 메인 루프 (이 파일)
  kis_api.py       ← 한투 API (공통)
  kiwoom_api.py    ← 키움 API (공통)
  notifier.py      ← 디스코드 알림 (공통)
  sbot_strategy.py ← 스윙 전략
  sbot_analyzer.py ← 스윙 AI 분석
  sbot_db.py       ← 스윙 매매이력 DB

[전략]
  대상: 시총 1조~20조 중대형주
  보유기간: 3~5일 스윙
  1차 익절 +8% → 30% 매도
  2차 익절 +15% → 40% 매도
  트레일링 스탑 -7% / 손절 -7%
  new 그룹 가점 +7점

[변경 이력]
  2026-04-29 최초 생성
  2026-05-01 new 관심그룹 연동
  2026-05-04 단타 조건검색식 제외 (SKIP_COND_KEYWORDS)
             09:20 이전 매수 금지 (BUY_START_TIME)
  2026-05-04 모듈 분리 리팩토링
"""

import os
import time
import json
import asyncio
import datetime
from dotenv import load_dotenv

from kis_api       import KisAPI
from kiwoom_api    import KiwoomAPI
from notifier      import Notifier
from sbot_strategy import SwingStrategy
from sbot_analyzer import SwingAnalyzer
from sbot_db       import SwingDB

load_dotenv()


# ============================================================
# 상수
# ============================================================
MAX_POSITIONS   = 3
BUY_1ST_AMT     = 2000000
BUY_SCORE_MIN   = 45
BUY_SCORE_ENTER = 60
LOOP_SLEEP      = 60
POOL_SIZE       = 100

REG_MARKET_START = "0900"
REG_MARKET_END   = "1530"
BUY_START_TIME   = "0920"
SLEEP_INTERVAL   = 60

# 스윙에서 제외할 단타용 조건검색식 키워드
SKIP_COND_KEYWORDS = ["단타", "장개장", "직후", "시가이탈", "오전중저가", "090930", "당일고가"]

# 약세장 방어
MARKET_WEAK_THRESH = -1.5
MARKET_STOP_THRESH = -2.5
MAX_DAILY_LOSS     = 2

# 종목 기준
MKT_CAP_MIN = 10000
MKT_CAP_MAX = 200000
MIN_PRICE   = 5000
MAX_PRICE   = 2000000

BOT_STATE_FILE = "sbot_state.json"


# ============================================================
# 상태 파일 헬퍼
# ============================================================
def _read_state() -> dict:
    try:
        if os.path.exists(BOT_STATE_FILE):
            with open(BOT_STATE_FILE, "r") as f:
                return json.load(f)
    except Exception:
        pass
    return {"paused": False, "score_enter": BUY_SCORE_ENTER,
            "pending_cmd": None, "cmd_result": None}

def _write_state(state: dict):
    try:
        with open(BOT_STATE_FILE, "w") as f:
            json.dump(state, f, ensure_ascii=False)
    except Exception as e:
        print(f"⚠️ 상태 파일 저장 오류: {e}")

def _update_state(**kwargs):
    state = _read_state()
    state.update(kwargs)
    _write_state(state)

def _write_cmd_result(result: str):
    _update_state(cmd_result=result, pending_cmd=None)

def _write_status(status: dict):
    state = _read_state()
    state["last_status"] = status
    state["last_update"] = datetime.datetime.now().strftime("%H:%M:%S")
    _write_state(state)


# ============================================================
# 메인 봇 클래스
# ============================================================
class SBot:

    def __init__(self):
        print("🚀 [영암9 SWING] 스윙봇 가동")

        self.api = KisAPI(
            appkey=os.getenv("KIS_APPKEY2"),
            secret=os.getenv("KIS_SECRET2"),
            cano=os.getenv("KIS_CANO2"),
            acnt=os.getenv("KIS_ACNT_PRDT_CD2"),
        )
        self.kiwoom   = KiwoomAPI()
        self.notifier = Notifier()
        self.strategy = SwingStrategy()
        self.ai       = SwingAnalyzer()
        self.db       = SwingDB()

        self.ai.init_db()
        self.db.init_db()

        self.positions    = {}
        self.score_cache  = {}
        self.buy_context  = {}
        self.peak_tracker = {}
        self.sold_today   = {}
        self._sold_today_date = datetime.datetime.now().strftime("%Y-%m-%d")
        self.code_name_map    = {}
        self._tech_cache      = {}
        self._flow_cache      = {}
        self._is_paused       = False
        self._last_market_check = 0

        self.market_status    = "normal"
        self.market_rate      = 0.0
        self.daily_loss_count = 0
        self.new_codes_list   = []

        if self.kiwoom.enabled:
            print(f"✅ 키움 연동 활성화 | 단타 제외: {SKIP_COND_KEYWORDS}")

    def _notify(self, msg: str):
        self.notifier.send(f"[SWING] {msg}")

    def _name(self, code: str) -> str:
        return self.code_name_map.get(code, code)

    # ============================================================
    # 시장 상태
    # ============================================================
    def _update_market_status(self):
        idx   = self.api.get_market_index()
        kospi = idx["kospi"]
        if kospi == 0.0:
            print(f"⚠️ 시장지수 조회 실패 — 기존 유지: {self.market_status}"); return
        self.market_rate = kospi
        if kospi <= MARKET_STOP_THRESH:   status = "stop"
        elif kospi <= MARKET_WEAK_THRESH: status = "weak"
        else:                             status = "normal"
        if status != self.market_status:
            self._notify(f"시장상태 변경: {self.market_status}→{status} | 코스피:{kospi:+.2f}%")
        self.market_status = status
        print(f"📊 시장: {status} | 코스피:{kospi:+.2f}%")

    # ============================================================
    # new 그룹 종목 조회
    # ============================================================
    def _load_new_codes(self):
        hts_id = os.getenv("KIS_HTS_ID2", os.getenv("KIS_HTS_ID", ""))
        if not hts_id: return
        groups = self.api.get_watchlist_groups(hts_id)
        target = next(
            ((gc, gn) for gc, gn in groups.items()
             if gn.lower() in ("new", "신규추천", "신규", "new추천")),
            None
        )
        if not target:
            print("  ⚠️ 'new' 관심그룹 없음"); return
        grp_code, grp_name = target
        print(f"  🆕 new그룹 발견: [{grp_code}]{grp_name}")
        stocks = self.api.get_watchlist_stocks(grp_code, hts_id, self.code_name_map)
        self.new_codes_list = [c for c, _ in stocks]
        print(f"  🆕 new그룹 종목: {len(self.new_codes_list)}개")

    # ============================================================
    # 종목 풀 조회
    # ============================================================
    def _get_pool(self) -> list:
        if not self.kiwoom.enabled:
            print("⚠️ 키움 없음 — 빈 풀"); return []
        try:
            loop  = asyncio.new_event_loop()
            codes = loop.run_until_complete(
                self.kiwoom.get_condition_codes(
                    use_keywords=None,
                    code_name_map=self.code_name_map,
                )
            )
            loop.close()

            if codes:
                st = _read_state()
                for wc in st.get("watchlist", []):
                    if wc not in codes and wc.isdigit():
                        codes.append(wc)

                try:
                    self._load_new_codes()
                    added = 0
                    for nc in self.new_codes_list:
                        if nc not in codes:
                            codes.append(nc); added += 1
                    if added: print(f"  🆕 new 종목 {added}개 풀 추가")
                    elif self.new_codes_list:
                        print(f"  🆕 new 종목 {len(self.new_codes_list)}개 (이미 포함)")
                except Exception as e:
                    print(f"⚠️ new 그룹 오류: {e}")

                result = codes[:POOL_SIZE]
                print(f"🎯 스윙 종목 풀: {len(result)}개")
                return result
        except Exception as e:
            print(f"⚠️ 키움 오류: {e}")
        return []

    # ============================================================
    # 매수 / 매도
    # ============================================================
    def _do_buy(self, code: str, price: float, amount: int,
                is_second: bool = False):
        ok = self.api.buy(code, price, amount, self.code_name_map)
        if ok:
            ctx = self.buy_context.get(code, {})
            tag = " 🆕new" if code in self.new_codes_list else ""
            self._notify(f"🚀 매수 {code}({self._name(code)}) | {amount:,}원{tag}")
            self.db.save_buy(
                code=code, buy_price=price,
                qty=int(amount / price),
                ai_score=ctx.get("ai_score", 0),
                ai_reason=ctx.get("ai_reason", ""),
                stock_name=self._name(code),
            )
            if not is_second:
                self.sold_today[code] = datetime.datetime.now().strftime("%H:%M:%S")

    def _do_sell(self, code: str, qty: int, reason: str, sell_price: float):
        ok = self.api.sell(code, qty)
        if ok:
            self._notify(f"💰 매도 {code}({self._name(code)}) | {reason}")
            self.db.save_sell(code, sell_price, reason)
            self.sold_today[code] = datetime.datetime.now().strftime("%H:%M:%S")
            self.buy_context.pop(code, None)

    def _do_loss(self):
        self.daily_loss_count += 1
        print(f"📉 당일 손절 누적: {self.daily_loss_count}회")

    # ============================================================
    # 메인 루프
    # ============================================================
    def run(self):
        self._notify(
            f"🚀 [영암9 SWING] 스윙봇 가동\n"
            f"⏰ {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"💰 1차:{BUY_1ST_AMT:,}원 / 최대{MAX_POSITIONS}종목\n"
            f"🎯 익절:+8%/+15% | 손절:-7%\n"
            f"⏳ 매수:{BUY_START_TIME} 이후\n"
            f"⛔ 단타 제외: {SKIP_COND_KEYWORDS}"
        )

        while True:
            try:
                today   = datetime.datetime.now().strftime("%Y-%m-%d")
                now_t   = datetime.datetime.now().strftime("%H%M")
                now     = datetime.datetime.now().strftime("%H:%M:%S")
                weekday = datetime.datetime.now().weekday()

                if weekday >= 5:
                    print(f"😴 [{now}] 주말 — 장 없음")
                    time.sleep(SLEEP_INTERVAL); continue

                # 공휴일 체크 (하루 1회)
                if not hasattr(self, "_holiday_checked"):
                    self._holiday_checked = ""
                if self._holiday_checked != today:
                    self._is_holiday = not self.api.is_market_open()
                    self._holiday_checked = today
                    if self._is_holiday:
                        self._notify(f"🎌 오늘은 휴장일 — 봇 대기")
                if self._is_holiday:
                    print(f"🎌 [{now}] 휴장일 — 대기 중...")
                    time.sleep(300); continue

                is_reg = REG_MARKET_START <= now_t <= REG_MARKET_END
                if not is_reg:
                    print(f"😴 [{now}] 장외 대기...")
                    time.sleep(SLEEP_INTERVAL); continue

                print(f"\n📈 [SWING] 정규장 [{now}]")

                st              = _read_state()
                self._is_paused = st.get("paused", False)

                # 일일 초기화
                today = datetime.datetime.now().strftime("%Y-%m-%d")
                if today != self._sold_today_date:
                    self.sold_today         = {}
                    self._sold_today_date   = today
                    self.daily_loss_count   = 0
                    self.market_status      = "normal"
                    self._tech_cache        = {}
                    self._flow_cache        = {}
                    self.new_codes_list     = []
                    self.api._mkt_cache     = {}
                    print("🔄 일일 초기화 완료")
                else:
                    if not self.sold_today:
                        saved = st.get("sold_today", {})
                        if saved and st.get("sold_today_date") == today:
                            self.sold_today = saved

                score_enter = st.get("score_enter", BUY_SCORE_ENTER)

                if st.get("daily_loss") == 0 and self.daily_loss_count > 0:
                    self.daily_loss_count = 0
                    print("♻️ 손절카운터 초기화")

                # 긴급 매도 명령
                pending = st.get("pending_cmd")
                if pending and pending.get("type") == "sell":
                    sell_code = pending.get("code", "")
                    if sell_code in self.positions:
                        mdata   = self.api.get_market_data(sell_code)
                        s_price = float(mdata.get("stck_prpr", 0)) if mdata else 0
                        self._do_sell(sell_code, self.positions[sell_code]["qty"],
                                      "즉시매도(AI비서명령)", s_price)
                        _write_cmd_result(f"✅ {sell_code} 즉시매도 완료")
                    else:
                        _write_cmd_result(f"⚠️ {sell_code} 보유 중이 아님")

                self.api.refresh_token_if_needed()

                # 계좌 상태
                cash           = self.api.get_buyable_cash()
                self.positions = self.api.get_current_positions()
                print(
                    f"⏰ {now} | 💵 예수금: {cash:,} | "
                    f"포지션: {len(self.positions)}/{MAX_POSITIONS}\n"
                    f"🆕 new 그룹: {len(self.new_codes_list)}종목"
                )

                # 보유종목 현황
                pos_mkt_cache = {}
                total_profit  = 0
                for code, pos in self.positions.items():
                    data = self.api.get_market_data(code)
                    if not data: continue
                    pos_mkt_cache[code] = data
                    cur    = float(data.get("stck_prpr", 0))
                    entry  = pos["entry_price"]
                    qty    = pos["qty"]
                    profit = (cur - entry) * qty
                    rate   = (cur - entry) / entry * 100 if entry > 0 else 0
                    total_profit += profit
                    tag = " 🆕" if code in self.new_codes_list else ""
                    print(f"  💰 {code}({self._name(code)}){tag} | {rate:+.2f}% | {qty}주")
                print(f"📈 총손익: {int(total_profit):,}원")

                # 시장 상태 (10분마다)
                if time.time() - self._last_market_check > 600:
                    self._update_market_status()
                    self._last_market_check = time.time()

                # stop 상태
                if self.market_status == "stop":
                    print("🚨 시장 중단 모드")
                    for code, pos in list(self.positions.items()):
                        mdata = pos_mkt_cache.get(code) or self.api.get_market_data(code)
                        if mdata:
                            self.strategy.check_sell(
                                code, pos, mdata, self.market_status,
                                self.peak_tracker, self._is_paused,
                                lambda c, p, a: self._do_buy(c, p, a, is_second=True),
                                lambda c, q, r, sp: self._do_sell(c, q, r, sp),
                                self._do_loss,
                            )
                    time.sleep(LOOP_SLEEP); continue

                # paused 상태
                if self._is_paused:
                    print("⏸️ [SWING] 일시중단 — 매도 체크만")
                    for code, pos in list(self.positions.items()):
                        mdata = pos_mkt_cache.get(code) or self.api.get_market_data(code)
                        if mdata:
                            self.strategy.check_sell(
                                code, pos, mdata, self.market_status,
                                self.peak_tracker, self._is_paused,
                                lambda c, p, a: self._do_buy(c, p, a, is_second=True),
                                lambda c, q, r, sp: self._do_sell(c, q, r, sp),
                                self._do_loss,
                            )
                    time.sleep(LOOP_SLEEP); continue

                # 종목 풀
                codes = self._get_pool()
                if not codes:
                    print("⚠️ 종목 풀 없음")
                    time.sleep(LOOP_SLEEP); continue

                # 종목 분석
                new_codes    = [c for c in codes if c not in self.score_cache]
                cached_codes = [c for c in codes if c in self.score_cache]
                print(f"\n🔄 분석: 신규 {len(new_codes)}개 | 캐시 {len(cached_codes)}개")

                rule_candidates = []
                for idx, code in enumerate(new_codes):
                    print(f"🔎 분석 {idx+1}/{len(new_codes)}: {code}", end="")
                    basic = self.api.get_market_data(code)
                    if not basic: print(); continue
                    try:
                        data = {
                            "current_price": float(basic.get("stck_prpr",  0) or 0),
                            "change_rate":   float(basic.get("prdy_ctrt",  0) or 0),
                            "trading_value": int(basic.get("acml_tr_pbmn", 0) or 0) // 100_000_000,
                            "volume":        int(basic.get("acml_vol",     0) or 0),
                            "mkt_cap":       int(float(basic.get("hts_avls", 0) or 0)),
                            "stock_name":    basic.get("hts_kor_isnm", ""),
                        }
                        data.update(self.api.get_technical_indicators(code, self._tech_cache))
                        data.update(self.api.get_investor_trend(code, self._flow_cache))

                        is_new = code in self.new_codes_list

                        if data["change_rate"] >= 29.5:     print(" → 상한가"); continue
                        if data["change_rate"] < 3.0:       print(" → 양봉 미달(+3% 미만)"); continue  # ★ 모든 종목 적용
                        if data["current_price"] < MIN_PRICE: print(" → 저가주"); continue
                        if data["current_price"] > MAX_PRICE: print(" → 고가"); continue

                        # ★ 고가 대비 현재가 체크 (이미 꺾인 종목 제외)
                        hg  = data.get("stck_hgpr", 0)
                        cur = data["current_price"]
                        if hg > 0 and (cur - hg) / hg < -0.05:
                            print(f" → 고점 대비 -5% 이상 하락 제외 ({(cur-hg)/hg*100:.1f}%)"); continue
                        mkt_cap = data["mkt_cap"]
                        if not is_new:
                            if mkt_cap < MKT_CAP_MIN: print(f" → 소형주({mkt_cap:,}억)"); continue
                            if mkt_cap > MKT_CAP_MAX: print(f" → 초대형주({mkt_cap:,}억)"); continue
                            if data["trading_value"] < 100: print(" → 거래대금 부족"); continue

                        rule_score = self.strategy.get_rule_score(data)
                        print(f" → 룰:{rule_score}점" + (" 🆕" if is_new else ""))
                        rule_candidates.append((code, rule_score, data))
                    except Exception as e:
                        print(f" → 오류: {e}")

                rule_candidates.sort(key=lambda x: x[1], reverse=True)
                top_ai = rule_candidates[:10]
                rest   = rule_candidates[10:]

                print(f"\n🤖 AI 분석: {len(top_ai)}개")
                for code, rule_score, data in top_ai:
                    ai_result = self.ai.analyze(code, data, self.new_codes_list)
                    score     = ai_result["score"]
                    reason    = ai_result["reason"]
                    score, bonus = self.strategy.apply_new_bonus(code, score, self.new_codes_list)
                    if bonus: reason = f"{reason} | {bonus}"
                    print(f"   🧠 {code} | 룰:{rule_score}→AI:{score}점 | {reason}")
                    data["ai_reason"] = reason
                    self.score_cache[code] = (score, data)

                for code, rule_score, data in rest:
                    score, bonus = self.strategy.apply_new_bonus(code, rule_score, self.new_codes_list)
                    data["ai_reason"] = f"룰점수({rule_score})" + (f" | {bonus}" if bonus else "")
                    self.score_cache[code] = (score, data)

                pool_set = set(codes)
                for c in [c for c in list(self.score_cache) if c not in pool_set]:
                    del self.score_cache[c]

                candidates = [
                    (code, score, data)
                    for code, (score, data) in self.score_cache.items()
                    if score >= BUY_SCORE_MIN
                ]

                def sort_key(x):
                    code, score, d = x
                    return (code in self.new_codes_list,
                            not d.get("ai_reason", "").startswith("룰점수"),
                            score)

                candidates.sort(key=sort_key, reverse=True)
                top10 = candidates[:10]

                print(f"\n🔥 SWING TOP{len(top10)}:")
                for code, score, d in top10:
                    tag = " 🆕" if code in self.new_codes_list else ""
                    ct  = "📦" if code in cached_codes else "🆕"
                    print(f"  {ct} {code}({self._name(code)}){tag} | {score}점 | {d.get('ai_reason','')}")

                # 매수 실행
                # ★ 1차 익절 이후 종목은 슬롯에서 제외 (신규 매수 가능)
                익절중 = sum(
                    1 for c in self.positions
                    if self.peak_tracker.get(c, {}).get("stage", 0) >= 1
                )
                slots = MAX_POSITIONS - len(self.positions) + 익절중
                if 익절중:
                    print(f"  ♻️ 익절진행중 {익절중}종목 슬롯 반환 → 가용슬롯:{slots}")

                if not is_reg:
                    print("🌅 프리장 — 매수 대기")
                elif now_t < BUY_START_TIME:
                    print(f"⏳ {BUY_START_TIME} 이전 — 매수 대기 중")
                elif self.market_status == "weak":
                    print("⚠️ 약세장 — 신규 매수 중단")
                elif self.daily_loss_count >= MAX_DAILY_LOSS:
                    print(f"🛑 손절 {self.daily_loss_count}회 — 매수 정지")
                    st = _read_state()
                    if not st.get("paused"):
                        self._notify(f"🛑 [SWING] 손절 {self.daily_loss_count}회 — 매수 정지")
                        _update_state(paused=True)
                elif slots <= 0:
                    print("📦 포지션 FULL")
                else:
                    for code, score, data in top10:
                        if slots <= 0: break
                        if code in self.positions: continue
                        if data["current_price"] <= 0: continue
                        if score < score_enter: continue
                        if code in self.sold_today: continue

                        tag = " 🆕new" if code in self.new_codes_list else ""
                        print(f"🚀 [SWING] 매수 {code} | {score}점{tag}")
                        self.buy_context[code] = {
                            "ai_score":   score,
                            "ai_reason":  data.get("ai_reason", ""),
                            "stock_name": data.get("stock_name", ""),
                        }
                        self._do_buy(code, data["current_price"], BUY_1ST_AMT)
                        self.peak_tracker[code] = {
                            "peak_rate":  0.0, "stage": 0,
                            "remain_qty": 0,   "buy2_done": False,
                            "buy1_price": data["current_price"],
                        }
                        slots -= 1
                        time.sleep(1)

                # 매도 체크
                for code, pos in list(self.positions.items()):
                    mdata = pos_mkt_cache.get(code) or self.api.get_market_data(code)
                    if mdata:
                        # ★ tech_cache에서 MA20 가져오기
                        tech = self._tech_cache.get(code, ({}, 0))
                        ma20 = tech[0].get("ma20", 0) if isinstance(tech, tuple) else 0
                        self.strategy.check_sell(
                            code, pos, mdata, self.market_status,
                            self.peak_tracker, self._is_paused,
                            lambda c, p, a: self._do_buy(c, p, a, is_second=True),
                            lambda c, q, r, sp: self._do_sell(c, q, r, sp),
                            self._do_loss,
                            ma20=ma20,  # ★ MA20 전달
                        )

                # 상태 저장
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

                time.sleep(LOOP_SLEEP)

            except KeyboardInterrupt:
                self._notify(
                    f"🛑 [SWING] 봇 종료 | "
                    f"{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                )
                break
            except Exception as e:
                print(f"🚨 루프 오류: {e}")
                time.sleep(5)


# ============================================================
# 진입점
# ============================================================
if __name__ == "__main__":
    SBot().run()
