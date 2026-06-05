"""
nbot.py — 영암9 단타봇 메인
========================================================
[실행]
  python3 nbot.py

[모듈 구조]
  nbot.py        ← 메인 루프 (이 파일)
  kis_api.py     ← 한투 API
  kiwoom_api.py  ← 키움 API
  strategy.py    ← 매수/매도 전략
  ai_analyzer.py ← Claude AI 분석
  db_manager.py  ← DB 저장/조회
  notifier.py    ← 디스코드 알림

[변경 이력]
  2026-04-24 기본 전략 구현
  2026-04-27 paused 버그 수정 / !시작 손절카운터 초기화
  2026-05-01 업종/테마 전략 추가
  2026-05-04 09:20 이전 / 15:15 이후 매수 금지
             USE_COND_KEYWORDS 조건검색식 필터
  2026-05-04 모듈 분리 리팩토링
"""

import os
import time
import json
import asyncio
import datetime
from dotenv import load_dotenv

from db_manager  import DBManager
from notifier    import Notifier
from kis_api     import KisAPI
from kiwoom_api  import KiwoomAPI
from ai_analyzer import AIAnalyzer
from strategy    import Strategy

load_dotenv()


# ============================================================
# 상수
# ============================================================
MAX_POSITIONS    = 5
LIMIT_PER_STOCK  = 300000
BUY_1ST_AMT      = 200000
BUY_2ND_AMT      = 100000
BUY_SCORE_MIN    = 45
BUY_SCORE_ENTER  = 55
LOOP_SLEEP       = 30
POOL_SIZE        = 150

EOD_SELL_TIME    = "1515"
REG_MARKET_START = "0900"
REG_MARKET_END   = "1530"
BUY_START_TIME   = "0920"
SLEEP_INTERVAL   = 60

# 조건검색식 필터 (이름에 포함된 것만 사용)
USE_COND_KEYWORDS = ["단타", "주도주", "장개장", "090930"]

# 약세장 방어
MARKET_WEAK_THRESH = -1.5
MARKET_STOP_THRESH = -2.5
STOP_LOSS_WEAK     = -0.03
MAX_DAILY_LOSS     = 2
MAX_SAME_SECTOR    = 2

# 업종/테마
SECTOR_CHECK_START = "0920"
SECTOR_TOP_N       = 3
SECTOR_MIN_RATE    = 0.5
SECTOR_CODE_MAP = {
    "005": "반도체",       "009": "전력전선",
    "016": "우주방산통신", "017": "우주방산통신",
    "021": "제약바이오",   "022": "제약바이오",
    "024": "화장품건강기기","025": "화장품건강기기",
    "027": "2차전지",      "034": "원전신재생전력",
    "036": "조선철강",     "037": "조선철강",
    "042": "로봇자율주행", "043": "로봇자율주행",
}

BOT_STATE_FILE = "bot_state.json"
TRADE_HIST_DB  = "trade_history.db"


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
class NBot:

    def __init__(self):
        print("🚀 [영암9 LIVE] 단타봇 가동")

        self.db       = DBManager()
        self.notify   = Notifier()
        self.api      = KisAPI()
        self.kiwoom   = KiwoomAPI()
        self.ai       = AIAnalyzer(self.db)
        self.strategy = Strategy()

        self.db.init_ai_db()
        self.db.init_trade_db()

        # 상태
        self.positions    = {}
        self.score_cache  = {}
        self.buy_context  = {}
        self.peak_tracker = {}
        self.buy_tags     = {}
        self.sold_today   = {}
        self._sold_today_date = datetime.datetime.now().strftime("%Y-%m-%d")
        self.code_name_map    = {}
        self._tech_cache      = {}
        self._flow_cache      = {}
        self._is_paused       = False

        # 시장 상태
        self.market_status = "normal"
        self.market_rate   = 0.0
        self.daily_loss_count = 0

        # 업종/테마
        self.active_sectors   = []
        self.sector_group_map = {}
        self.theme_codes      = []
        self.new_codes_list   = []
        self.theme_group_map  = {}
        self._sector_check_done_today = set()

        if self.kiwoom.enabled:
            print(f"✅ 키움 연동 활성화 | 조건검색 필터: {USE_COND_KEYWORDS}")
        else:
            print("⚠️ 키움 API 없음 → 한투 폴백")

    # ============================================================
    # 알림 헬퍼
    # ============================================================
    def _notify(self, msg: str):
        self.notify.send(msg)

    def _name(self, code: str) -> str:
        return self.code_name_map.get(code, code)

    # ============================================================
    # 시장 상태 업데이트
    # ============================================================
    def _update_market_status(self):
        idx    = self.api.get_market_index()
        kospi  = idx["kospi"]
        kosdaq = idx["kosdaq"]
        if kospi == 0.0 and kosdaq == 0.0:
            print(f"⚠️ 시장지수 조회 실패 — 기존 유지: {self.market_status}")
            return
        self.market_rate = kospi
        if kospi <= MARKET_STOP_THRESH:   status = "stop";   emoji = "🚨"
        elif kospi <= MARKET_WEAK_THRESH: status = "weak";   emoji = "⚠️"
        else:                             status = "normal";  emoji = "✅"
        if status != self.market_status:
            self._notify(
                f"{emoji} 시장상태 변경: {self.market_status} → {status}\n"
                f"코스피: {kospi:+.2f}% | 코스닥: {kosdaq:+.2f}%"
            )
        self.market_status = status
        print(f"📊 시장: {status} | 코스피:{kospi:+.2f}% | 코스닥:{kosdaq:+.2f}%")

    # ============================================================
    # 업종/테마 관리
    # ============================================================
    def _load_watchlist_groups(self):
        """한투 관심그룹 + 키움 테마 API 로딩"""
        print("  🏢 관심그룹 로딩...")
        sector_map = {}
        theme_list = []
        new_list   = []
        hts_id     = os.getenv("KIS_HTS_ID", "")

        if hts_id:
            groups = self.api.get_watchlist_groups(hts_id)
            for grp_code, grp_name in groups.items():
                is_sector = grp_name.startswith("업종")
                is_theme  = grp_name == "테마" or grp_name.startswith("테마")
                is_new    = grp_name.lower() in ("new", "신규추천", "신규")
                if not (is_sector or is_theme or is_new):
                    continue
                stocks = self.api.get_watchlist_stocks(grp_code, hts_id, self.code_name_map)
                codes  = [c for c, _ in stocks]
                if is_sector:
                    kw = grp_name.split("_", 1)[1] if "_" in grp_name else grp_name
                    sector_map[kw] = codes
                elif is_theme:
                    theme_list.extend(codes)
                elif is_new:
                    new_list.extend(codes)
                time.sleep(0.1)

        # 키움 테마 API
        self.theme_group_map = {}
        top_themes = self.kiwoom.get_theme_top(top_n=5)
        if top_themes:
            for item in top_themes:
                grp_cd = item.get("thema_grp_cd", "")
                grp_nm = item.get("thema_nm", item.get("thema_grp_nm", "테마"))
                if not grp_cd: continue
                stocks = self.kiwoom.get_theme_stocks(grp_cd, self.code_name_map)
                codes  = [c for c, _ in stocks]
                self.theme_group_map[grp_nm] = codes
                for c in codes:
                    if c not in theme_list:
                        theme_list.append(c)
                time.sleep(0.2)

        self.sector_group_map = sector_map
        self.theme_codes      = list(dict.fromkeys(theme_list))
        self.new_codes_list   = list(dict.fromkeys(new_list))

        _update_state(
            kiwoom_themes={k: v[:5] for k, v in self.theme_group_map.items()},
            kiwoom_theme_at=datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        )
        print(
            f"✅ 관심그룹 완료 | 업종:{len(sector_map)}그룹 | "
            f"테마:{len(self.theme_codes)}종목 | new:{len(self.new_codes_list)}종목"
        )

    def _update_active_sectors(self):
        print(f"\n🔍 업종 체크...")
        rates = self.api.get_sector_change_rates(SECTOR_CODE_MAP)
        if rates:
            sorted_rates = sorted(rates.items(), key=lambda x: x[1], reverse=True)
            active_codes = [c for c, r in sorted_rates if r >= SECTOR_MIN_RATE][:SECTOR_TOP_N]
            active_kws   = list(dict.fromkeys([
                SECTOR_CODE_MAP[c] for c in active_codes if c in SECTOR_CODE_MAP
            ]))
            print(f"  📊 강세 업종: {[(c, f'{rates[c]:+.2f}%') for c in active_codes]}")
        else:
            active_kws = []

        # 뉴스 힌트
        try:
            import urllib.parse, re as _re
            client_id     = os.getenv("NAVER_CLIENT_ID", "")
            client_secret = os.getenv("NAVER_CLIENT_SECRET", "")
            if client_id and client_secret:
                query = urllib.parse.quote("오늘 증시 강세 업종 테마주")
                url   = f"https://openapi.naver.com/v1/search/news.json?query={query}&display=10&sort=date"
                import requests as _req
                items = _req.get(url, headers={
                    "X-Naver-Client-Id": client_id,
                    "X-Naver-Client-Secret": client_secret,
                }, timeout=5).json().get("items", [])
                text = " ".join(
                    _re.sub(r"<[^>]+>", "", i.get("title","") + " " + i.get("description",""))
                    for i in items
                )
                for kw in self.sector_group_map:
                    if kw in text and kw not in active_kws:
                        active_kws.append(kw)
                        print(f"  📰 뉴스 힌트: {kw}")
        except Exception:
            pass

        matched = [kw for kw in active_kws if kw in self.sector_group_map]
        prev    = self.active_sectors

        self._load_watchlist_groups()  # 테마 API도 갱신

        self.active_sectors = matched
        _update_state(
            active_sectors=matched,
            sector_updated_at=datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        )

        if set(matched) != set(prev):
            added   = [s for s in matched if s not in prev]
            removed = [s for s in prev   if s not in matched]
            msg = "🎯 강세 업종 변경!\n"
            if added:   msg += f"  ✅ 활성화: {', '.join(added)}\n"
            if removed: msg += f"  ❌ 비활성: {', '.join(removed)}\n"
            msg += f"  현재: {', '.join(matched) or '없음'}"
            self._notify(msg)
        else:
            print(f"  ✅ 활성 업종 유지: {matched or '없음'}")

    def _should_check_sector(self, now_t: str) -> bool:
        if now_t < SECTOR_CHECK_START: return False
        now_dt = datetime.datetime.now()
        if now_dt.minute != int(SECTOR_CHECK_START[2:]): return False
        check_key = now_dt.strftime("%Y-%m-%d %H")
        return check_key not in self._sector_check_done_today

    def _get_sector_theme_codes(self) -> list:
        codes = []
        seen  = set()
        for kw in self.active_sectors:
            for c in self.sector_group_map.get(kw, []):
                if c not in seen: seen.add(c); codes.append(c)
        for c in self.theme_codes:
            if c not in seen: seen.add(c); codes.append(c)
        for c in self.new_codes_list:
            if c not in seen: seen.add(c); codes.append(c)
        return codes

    # ============================================================
    # 종목 풀 조회
    # ============================================================
    def _get_pool(self) -> list:
        if self.kiwoom.enabled:
            try:
                loop  = asyncio.new_event_loop()
                codes = loop.run_until_complete(
                    self.kiwoom.get_condition_codes(
                        use_keywords=USE_COND_KEYWORDS,
                        code_name_map=self.code_name_map,
                    )
                )
                loop.close()
                if codes:
                    st = _read_state()
                    for wc in st.get("watchlist", []):
                        if wc not in codes and wc.isdigit():
                            codes.append(wc)
                    added = 0
                    for sc in self._get_sector_theme_codes():
                        if sc not in codes:
                            codes.append(sc); added += 1
                    if added: print(f"  🎯 업종/테마 {added}개 풀 추가")
                    result = codes[:POOL_SIZE]
                    print(f"🎯 종목 풀 (키움): {len(result)}개")
                    return result
                else:
                    print("⚠️ 키움 조건검색 없음 → 한투 폴백")
            except Exception as e:
                print(f"⚠️ 키움 오류: {e} → 한투 폴백")

        # 한투 폴백 (psearch)
        hts_id = os.getenv("KIS_HTS_ID", "")
        seen   = set()
        codes  = []
        skip   = ["KODEX","TIGER","KBSTAR","ARIRANG","HANARO",
                  "KOSEF","TREX","SOL","ACE","PLUS",
                  "인버스","레버리지","ETN","선물","RISE","TIME"]
        import requests as _req
        for seq in ["0", "1", "2", "3"]:
            url = f"{self.api.base_url}/uapi/domestic-stock/v1/quotations/psearch-result"
            headers = {
                "Content-Type":  "application/json; charset=utf-8",
                "authorization": f"Bearer {self.api.token}",
                "appKey":        self.api.appkey,
                "appSecret":     self.api.secret,
                "tr_id":         "HHKST03900400",
                "custtype":      "P",
            }
            params = {"user_id": hts_id, "seq": seq}
            try:
                res = _req.get(url, headers=headers, params=params).json()
                if res.get("rt_cd") != "0": continue
                for item in res.get("output2", []):
                    code = item.get("code", "").strip()
                    name = item.get("name", "").strip()
                    if not code or code in seen or not code.isdigit(): continue
                    if any(kw in name for kw in skip): continue
                    seen.add(code); codes.append(code)
                    self.code_name_map[code] = name
            except Exception as e:
                print(f"❌ 조건검색 seq={seq} 예외: {e}")

        if len(codes) < 10:
            codes += self.api.get_volume_rank_codes(seen, self.code_name_map)

        st = _read_state()
        for wc in st.get("watchlist", []):
            if wc not in seen and wc.isdigit():
                seen.add(wc); codes.append(wc)
        for sc in self._get_sector_theme_codes():
            if sc not in seen: seen.add(sc); codes.append(sc)

        result = codes[:POOL_SIZE]
        print(f"🎯 종목 풀 (한투): {len(result)}개")
        return result

    # ============================================================
    # 매수 / 매도 실행
    # ============================================================
    def _do_buy(self, code: str, price: float, amount: int,
                is_second: bool = False):
        ok = self.api.buy(code, price, amount, self.code_name_map)
        if ok:
            ctx = self.buy_context.get(code, {})
            self._notify(
                f"🚀 매수 {code}({self._name(code)}) | "
                f"{amount:,}원 | {price:,}원"
            )
            self.db.save_buy_history(
                code=code, buy_price=price,
                qty=int(amount / price),
                ai_score=ctx.get("ai_score", 0),
                ai_reason=ctx.get("ai_reason", ""),
                indicators=ctx.get("indicators", {}),
                stock_name=self._name(code),
                buy_tag=ctx.get("buy_tag", ""),
            )
            if not is_second:
                self.sold_today[code] = datetime.datetime.now().strftime("%H:%M:%S")

    def _do_sell(self, code: str, qty: int, reason: str, sell_price: float):
        ok = self.api.sell(code, qty)
        if ok:
            self._notify(f"💰 매도 {code}({self._name(code)}) | {reason}")
            self.db.save_sell_history(code, sell_price, reason)
            self.sold_today[code] = datetime.datetime.now().strftime("%H:%M:%S")
            self.buy_tags.pop(code, None)
            self.buy_context.pop(code, None)
            st = _read_state()
            st["sold_today"]      = self.sold_today
            st["sold_today_date"] = datetime.datetime.now().strftime("%Y-%m-%d")
            _write_state(st)

    def _do_loss(self):
        self.daily_loss_count += 1
        print(f"📉 당일 손절 누적: {self.daily_loss_count}회")

    # ============================================================
    # 메인 루프
    # ============================================================
    def run(self):
        self._notify(
            f"🚀 [영암9 LIVE] 단타봇 가동\n"
            f"⏰ {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"💰 1차:{BUY_1ST_AMT:,}원 / 최대{MAX_POSITIONS}종목\n"
            f"⏳ 매수:{BUY_START_TIME} ~ {EOD_SELL_TIME}\n"
            f"🔍 조건검색 필터: {USE_COND_KEYWORDS}"
        )
        self._is_paused = False
        self._last_market_check = 0

        if self.kiwoom.enabled:
            try:
                self._load_watchlist_groups()
            except Exception as e:
                print(f"⚠️ 관심그룹 초기 로딩 오류: {e}")

        while True:
            try:
                today   = datetime.datetime.now().strftime("%Y-%m-%d")
                now_t   = datetime.datetime.now().strftime("%H%M")
                now     = datetime.datetime.now().strftime("%H:%M:%S")
                weekday = datetime.datetime.now().weekday()

                if weekday >= 5:
                    day = "토요일" if weekday == 5 else "일요일"
                    print(f"😴 [{now}] {day} — 장 없음")
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
                    print(f"😴 [{now}] 장외 대기 중...")
                    time.sleep(SLEEP_INTERVAL); continue

                print(f"\n📈 정규장 [{now}]")

                # 상태 읽기
                st              = _read_state()
                self._is_paused = st.get("paused", False)

                # 일일 초기화
                today = datetime.datetime.now().strftime("%Y-%m-%d")
                if today != self._sold_today_date:
                    self.sold_today               = {}
                    self._sold_today_date         = today
                    self.daily_loss_count         = 0
                    self.market_status            = "normal"
                    self.active_sectors           = []
                    self._sector_check_done_today = set()
                    self._tech_cache              = {}
                    self._flow_cache              = {}
                    self.buy_tags                 = {}
                    self.api._mkt_cache           = {}
                    _update_state(sold_today={}, sold_today_date=today)
                    print("🔄 일일 초기화 완료")
                    if self.kiwoom.enabled:
                        try: self._load_watchlist_groups()
                        except Exception as e: print(f"⚠️ 관심그룹 재로딩 오류: {e}")
                else:
                    if not self.sold_today:
                        saved = st.get("sold_today", {})
                        if saved and st.get("sold_today_date") == today:
                            self.sold_today = saved
                            if self.sold_today:
                                print(f"♻️ sold_today 복원: {list(self.sold_today.keys())}")

                score_enter = st.get("score_enter", BUY_SCORE_ENTER)

                if st.get("daily_loss") == 0 and self.daily_loss_count > 0:
                    self.daily_loss_count = 0
                    print("♻️ 손절카운터 초기화")

                # 긴급 명령 처리
                pending = st.get("pending_cmd")
                if pending and pending.get("type") == "sell":
                    sell_code = pending.get("code", "")
                    if sell_code in self.positions:
                        mdata   = self.api.get_market_data(sell_code)
                        s_price = float(mdata.get("stck_prpr", 0)) if mdata else 0
                        self._do_sell(sell_code, self.positions[sell_code]["qty"],
                                      "즉시매도(AI비서명령)", s_price)
                        _write_cmd_result(f"✅ {sell_code} 즉시매도 완료 ({s_price:,}원)")
                    else:
                        _write_cmd_result(f"⚠️ {sell_code} 보유 중이 아님")

                elif pending and pending.get("type") == "buy":
                    buy_code = pending.get("code", "")
                    buy_qty  = int(pending.get("qty", 0))
                    if buy_qty <= 0:
                        _write_cmd_result(f"⚠️ 수량 오류")
                    else:
                        mdata = self.api.get_market_data(buy_code)
                        if not mdata:
                            _write_cmd_result(f"⚠️ {buy_code} 시세 조회 실패")
                        else:
                            cur = float(mdata.get("stck_prpr", 0))
                            if cur <= 0:
                                _write_cmd_result(f"⚠️ {buy_code} 현재가 없음")
                            else:
                                self.buy_context[buy_code] = {
                                    "ai_score": 0, "ai_reason": "수동매수",
                                    "indicators": {}, "buy_tag": "",
                                }
                                self._do_buy(buy_code, cur, int(cur * buy_qty * 1.01), is_second=True)
                                if buy_code not in self.peak_tracker:
                                    self.peak_tracker[buy_code] = {
                                        "peak_rate": 0.0, "stage": 0,
                                        "remain_qty": 0, "buy2_done": True,
                                        "buy1_price": cur,
                                    }
                                _write_cmd_result(f"✅ {buy_code} {buy_qty}주 매수 완료")

                # 토큰 갱신 / DB 정리
                self.api.refresh_token_if_needed()
                self.db.clean_ai_db()

                # 계좌 상태
                cash           = self.api.get_buyable_cash()
                self.positions = self.api.get_current_positions()
                psbl_cash      = self.api.get_psbl_order_cash("005930")
                if psbl_cash <= 0: psbl_cash = cash
                print(f"\n⏰ {now} | 💵 예수금: {cash:,} | 💰 주문가능: {psbl_cash:,}")

                # 보유종목 현황
                pos_mkt_cache = {}
                total_profit  = 0
                print("📦 보유종목")
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
                    tag = "🎯" if self.buy_tags.get(code) == "theme_buy" else "  "
                    print(f"  {tag}💰 {code}({self._name(code)}) | {rate:+.2f}% | {qty}주")
                print(f"📈 총손익: {int(total_profit):,}원")
                print(f"🏭 활성 업종: {self.active_sectors or '없음'}")

                # 시장 상태 체크 (5분마다)
                if time.time() - self._last_market_check > 300:
                    self._update_market_status()
                    self._last_market_check = time.time()

                # 업종 체크 (1시간마다)
                if self._should_check_sector(now_t):
                    check_key = datetime.datetime.now().strftime("%Y-%m-%d %H")
                    self._sector_check_done_today.add(check_key)
                    self._update_active_sectors()

                # stop 상태
                if self.market_status == "stop":
                    print(f"🚨 시장 중단 모드 | 코스피:{self.market_rate:+.2f}%")
                    for code, pos in list(self.positions.items()):
                        mdata = pos_mkt_cache.get(code) or self.api.get_market_data(code)
                        if not mdata: continue
                        cur   = float(mdata.get("stck_prpr", 0))
                        entry = pos["entry_price"]
                        if entry > 0 and cur > 0 and (cur - entry)/entry <= STOP_LOSS_WEAK:
                            self._notify(f"🚨 긴급손절(약세장) {code}")
                            self._do_sell(code, pos["qty"], "긴급손절(약세장)", cur)
                            self._do_loss()
                            self.peak_tracker.pop(code, None)
                    time.sleep(LOOP_SLEEP); continue

                # paused 상태
                if self._is_paused:
                    print("⏸️ 일시중단 — 매도 체크만")
                    for code, pos in list(self.positions.items()):
                        mdata = pos_mkt_cache.get(code) or self.api.get_market_data(code)
                        if mdata:
                            self.strategy.check_sell(
                                code, pos, now_t, mdata,
                                self.market_status, self.peak_tracker, self.buy_tags,
                                self._is_paused,
                                lambda c, p, a: self._do_buy(c, p, a),
                                lambda c, q, r, sp: self._do_sell(c, q, r, sp),
                                self._do_loss,
                            )
                    _write_status({
                        "cash": cash, "psbl_cash": psbl_cash,
                        "total_profit": int(total_profit),
                        "positions": len(self.positions),
                        "score_enter": score_enter,
                        "last_update": now,
                        "code_name_map": self.code_name_map,
                        "market_status": self.market_status,
                        "market_rate":   self.market_rate,
                        "daily_loss":    self.daily_loss_count,
                        "active_sectors": self.active_sectors,
                    })
                    time.sleep(LOOP_SLEEP); continue

                # 종목 풀 조회
                codes = self._get_pool()
                if not codes:
                    print("⚠️ 종목 풀 없음, 재시도...")
                    time.sleep(5); continue

                # 종목 분석
                new_codes    = [c for c in codes if c not in self.score_cache]
                cached_codes = [c for c in codes if c in self.score_cache]
                print(f"\n🔄 분석: 신규 {len(new_codes)}개 | 캐시 {len(cached_codes)}개")

                sector_all = set(self._get_sector_theme_codes())

                rule_candidates = []
                for idx, code in enumerate(new_codes):
                    print(f"🔎 룰분석 {idx+1}/{len(new_codes)}: {code}", end="")
                    basic = self.api.get_market_data(code)
                    if not basic: print(); continue
                    try:
                        data = {
                            "current_price": float(basic.get("stck_prpr",  0) or 0),
                            "change_rate":   float(basic.get("prdy_ctrt",  0) or 0),
                            "trading_value": int(basic.get("acml_tr_pbmn", 0) or 0) // 100_000_000,
                            "volume":        int(basic.get("acml_vol",     0) or 0),
                            "volume_ratio":  float(basic.get("vol_inrt",   0) or 0),
                            "vol_tnrt":      float(basic.get("vol_tnrt",   0) or 0),
                            "hts_avls":      int(float(basic.get("hts_avls", 0) or 0)),
                            "stock_name":    basic.get("hts_kor_isnm", ""),
                            "stck_hgpr":     float(basic.get("stck_hgpr", 0) or 0),
                            "stck_sdpr":     float(basic.get("stck_sdpr", 0) or 0),
                            "vol_rate":      float(basic.get("prdy_vrss_vol_rate", 0) or 0),
                        }
                        data.update(self.api.get_technical_indicators(code, self._tech_cache))
                        data.update(self.api.get_investor_trend(code, self._flow_cache))

                        is_sc = code in sector_all
                        data["buy_tag"] = "theme_buy" if is_sc else ""

                        if data["change_rate"] >= 29.5:  print(" → 상한가 제외"); continue
                        if data["change_rate"] > 15:      print(" → 과열 제외"); continue
                        if data["change_rate"] < 3.0:     print(" → 양봉 미달(+3% 미만)"); continue  # ★ 모든 종목 적용
                        if data["volume"] < 30_000:       print(" → 거래량 부족"); continue
                        if data["current_price"] <= 999:  print(" → 동전주 제외"); continue
                        mkt_cap = data["hts_avls"]
                        if mkt_cap < 500:   print(f" → 소형주 제외 ({mkt_cap:,}억)"); continue
                        if mkt_cap > 50000: print(f" → 대형주 제외 ({mkt_cap:,}억)"); continue
                        if data["trading_value"] < 50:    print(" → 거래대금 부족"); continue

                        # ★ 고가 대비 현재가 체크 (이미 꺾인 종목 제외)
                        hg  = data.get("stck_hgpr", 0)
                        cur = data["current_price"]
                        if hg > 0 and (cur - hg) / hg < -0.05:
                            print(f" → 고점 대비 -5% 이상 하락 제외 ({(cur-hg)/hg*100:.1f}%)"); continue

                        if not is_sc:
                            if data["vol_tnrt"]    < 2.0:  print(" → 회전율 부족"); continue
                            _vm = 100 if now_t < "1030" else 50
                            if 0 < data["volume_ratio"] < _vm: print(f" → 거래량증가율 부족"); continue
                            ma5  = data.get("ma5",  0)
                            ma20 = data.get("ma20", 0)
                            if ma5 > 0 and ma20 > 0 and ma5 < ma20: print(" → MA 역배열"); continue
                            hg = data["stck_hgpr"]; sd = data["stck_sdpr"]
                            if hg > 0 and sd > 0 and (hg-sd)/sd*100 < 3.0: print(" → 고가상승 부족"); continue
                            if data["vol_rate"] < 50: print(" → 거래량비율 부족"); continue
                        if data["current_price"] > LIMIT_PER_STOCK: print(" → 고가 제외"); continue

                        rule_score = self.strategy.get_rule_score(data)
                        print(f" → 룰:{rule_score}점" + (" 🎯" if is_sc else ""))
                        rule_candidates.append((code, rule_score, data))
                    except Exception as e:
                        print(f" → 오류: {e}")

                # 상위 10개 AI 분석
                rule_candidates.sort(key=lambda x: x[1], reverse=True)
                top_ai = rule_candidates[:10]
                rest   = rule_candidates[10:]

                print(f"\n🤖 Claude 분석: {len(top_ai)}개")
                for code, rule_score, data in top_ai:
                    ai_result = self.ai.analyze(code, data, self.active_sectors)
                    score     = ai_result["score"]
                    reason    = ai_result["reason"]
                    score, bonus_reason, buy_tag = self.strategy.apply_sector_bonus(
                        code, score, self.active_sectors,
                        self.sector_group_map, self.theme_codes, self.new_codes_list,
                    )
                    if bonus_reason: reason = f"{reason} | {bonus_reason}"
                    if buy_tag:      data["buy_tag"] = buy_tag
                    print(f"   🧠 {code} | 룰:{rule_score}→AI:{score}점 | {reason}")
                    data["ai_reason"] = reason
                    self.score_cache[code] = (score, data)

                for code, rule_score, data in rest:
                    score, bonus_reason, buy_tag = self.strategy.apply_sector_bonus(
                        code, rule_score, self.active_sectors,
                        self.sector_group_map, self.theme_codes, self.new_codes_list,
                    )
                    data["ai_reason"] = f"룰점수({rule_score})" + (f" | {bonus_reason}" if bonus_reason else "")
                    if buy_tag: data["buy_tag"] = buy_tag
                    self.score_cache[code] = (score, data)

                # 캐시 정리
                pool_set = set(codes)
                for c in [c for c in list(self.score_cache) if c not in pool_set]:
                    del self.score_cache[c]

                candidates = [
                    (code, score, data)
                    for code, (score, data) in self.score_cache.items()
                    if score >= BUY_SCORE_MIN
                ]

                def sort_key(x):
                    _, score, d = x
                    return (d.get("buy_tag") == "theme_buy",
                            not d.get("ai_reason", "").startswith("룰점수"),
                            score)

                candidates.sort(key=sort_key, reverse=True)
                top10 = candidates[:10]

                print(f"\n🔥 TOP{len(top10)} (후보 {len(candidates)}개 중):")
                for code, score, d in top10:
                    tag1 = "🎯" if d.get("buy_tag") == "theme_buy" else "  "
                    tag2 = "🤖" if not d.get("ai_reason","").startswith("룰점수") else "📐"
                    ct   = "📦" if code in cached_codes else "🆕"
                    print(f"  {ct}{tag1}{tag2} {code}({self._name(code)}) | {score}점 | {d.get('ai_reason','')}")

                # ── 매수 실행 ─────────────────────────────────
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
                elif now_t >= EOD_SELL_TIME:
                    print("🔔 종가매도 시간 이후 — 매수 금지")
                elif self.market_status == "weak":
                    print("⚠️ 약세장 — 신규 매수 중단")
                elif self.daily_loss_count >= MAX_DAILY_LOSS:
                    print(f"🛑 당일 손절 {self.daily_loss_count}회 — 매수 정지")
                    st = _read_state()
                    if not st.get("paused"):
                        self._notify(f"🛑 손절 {self.daily_loss_count}회 도달\n!시작 으로 재개")
                        _update_state(paused=True, daily_loss=self.daily_loss_count)
                elif slots <= 0:
                    print("📦 포지션 FULL")
                else:
                    for code, score, data in top10:
                        if slots <= 0: break
                        if code in self.positions: continue
                        if data["current_price"] <= 0: continue
                        if score < score_enter: continue
                        if code in self.sold_today:
                            print(f"🚫 재매수 금지 {code}"); continue

                        buy_tag = data.get("buy_tag", "")

                        # 업종 쏠림 방지
                        sector_of = next(
                            (kw for kw, cds in self.sector_group_map.items() if code in cds),
                            None
                        )
                        if sector_of:
                            same_cnt = sum(
                                1 for hc in self.positions
                                if hc in self.sector_group_map.get(sector_of, [])
                            )
                            if same_cnt >= MAX_SAME_SECTOR:
                                print(f"⚠️ 업종 쏠림 방지 {code} [{sector_of}]"); continue

                        print(f"🚀 매수 {code} | {score}점 | {BUY_1ST_AMT:,}원" +
                              (f" | 🎯{buy_tag}" if buy_tag else ""))
                        self.buy_context[code] = {
                            "ai_score":  score,
                            "ai_reason": data.get("ai_reason", ""),
                            "indicators": data,
                            "stock_name": data.get("stock_name", ""),
                            "buy_tag":   buy_tag,
                        }
                        self._do_buy(code, data["current_price"], BUY_1ST_AMT)
                        self.buy_tags[code] = buy_tag
                        self.peak_tracker[code] = {
                            "peak_rate":  0.0, "stage": 0,
                            "remain_qty": 0,   "buy2_done": False,
                            "buy1_price": data["current_price"],
                        }
                        slots -= 1
                        time.sleep(1)

                # ── 매도 체크 ─────────────────────────────────
                for code, pos in list(self.positions.items()):
                    mdata = pos_mkt_cache.get(code) or self.api.get_market_data(code)
                    if mdata:
                        # ★ tech_cache에서 MA10 가져오기
                        tech = self._tech_cache.get(code, ({}, 0))
                        ma10 = tech[0].get("ma10", 0) if isinstance(tech, tuple) else 0
                        self.strategy.check_sell(
                            code, pos, now_t, mdata,
                            self.market_status, self.peak_tracker, self.buy_tags,
                            self._is_paused,
                            lambda c, p, a: self._do_buy(c, p, a),
                            lambda c, q, r, sp: self._do_sell(c, q, r, sp),
                            self._do_loss,
                            ma10=ma10,  # ★ MA10 전달
                        )

                # ── 상태 저장 ─────────────────────────────────
                pos_detail = {}
                for _code, _pos in self.positions.items():
                    _mdata = pos_mkt_cache.get(_code)
                    _cur   = float(_mdata.get("stck_prpr", 0)) if _mdata else 0
                    _entry = _pos["entry_price"]
                    _qty   = _pos["qty"]
                    _rate  = (_cur - _entry) / _entry * 100 if _entry > 0 else 0
                    pos_detail[_code] = {
                        "name":        self._name(_code),
                        "current":     int(_cur),
                        "entry_price": int(_entry),
                        "qty":         _qty,
                        "rate":        round(_rate, 2),
                        "buy_tag":     self.buy_tags.get(_code, ""),
                    }

                _write_status({
                    "cash":             cash,
                    "psbl_cash":        psbl_cash,
                    "total_profit":     int(total_profit),
                    "positions":        len(self.positions),
                    "positions_detail": pos_detail,
                    "score_enter":      score_enter,
                    "last_update":      now,
                    "code_name_map":    self.code_name_map,
                    "market_status":    self.market_status,
                    "market_rate":      self.market_rate,
                    "daily_loss":       self.daily_loss_count,
                    "active_sectors":   self.active_sectors,
                })

                time.sleep(LOOP_SLEEP)

            except KeyboardInterrupt:
                perf = self.db.get_recent_performance(limit=20)
                msg  = f"🛑 [영암9] 봇 종료\n⏰ {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                if perf:
                    msg += (
                        f"📊 최근 {perf['total']}건 | "
                        f"승률:{perf['win_rate']}% | 평균:{perf['avg_profit']:+.2f}%"
                    )
                self._notify(msg)
                break
            except Exception as e:
                print(f"🚨 루프 오류: {e}")
                time.sleep(5)


# ============================================================
# 진입점
# ============================================================
if __name__ == "__main__":
    NBot().run()
