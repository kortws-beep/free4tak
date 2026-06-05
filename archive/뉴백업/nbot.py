"""
nbot.py — 영암9 단타봇 메인 (전면 재구성판)
================================================================
[이 파일이 하는 일 — 비개발자용 설명]

장중(09:00~15:30)에 종목을 자동으로 사고 파는 단타 자동매매 봇입니다.

▶ 매수 흐름:
  1. 키움 조건검색식으로 후보 150개 수집
  2. 룰 점수 계산 → 상위 10개를 Claude AI에 분석 요청
  3. AI 점수 + 컨센서스 가점 + 업종/테마 가산점
  4. 기준점 이상이면 매수 (포지션 사이즈는 점수에 비례)

▶ 매도 흐름 (매 30초마다 체크):
  1. +5% 도달 → 30% 매도 (1차 익절)
  2. +10% 도달 → 추가 40% 매도 (2차 익절)
  3. +15% 도달 → 잔량 전부 매도 (3차 익절)
  4. 트레일링 스탑 / 손절 / 종가매도

[적용된 주요 개선사항]
[★ 치명적 버그 수정]
1. today 변수 순서 버그 수정 (휴장일 체크 시 NameError 방지)
2. buy_tags/buy_context — 부분 매도 시 삭제 안함 (전량 매도 시만)
3. 매수 직후 self.positions 즉시 업데이트 (체결 사이 매도 누락 방지)
4. peak_tracker — 매수 직후 즉시 초기화

[★ 손실 방어]
5. 동적 매수 임계치 — 최근 승률 따라 자동 조정
6. ATR 기반 손절선 — 변동성 따라 동적
7. 본절 보호 — 1차 익절 후 본전 깨지면 즉시 매도
8. 종가매도 -3%까지 확대 — 갭하락 위험 제거

[★ 수익 극대화]
9. 포지션 사이징 — 점수 높을수록 더 많이 매수
10. 시간대별 점수 보정 — 후장은 페널티
11. 약세장에서도 강세 업종은 매수 허용
12. 강한 종목은 양봉 조건 면제 (눌림목 매수 기회 확보)
================================================================

[실행]
  python3 nbot.py

[모듈 의존성]
  kis_api.py     ← 한투 API (검증됨, 그대로 사용)
  kiwoom_api.py  ← 키움 API (검증됨, 그대로 사용)
  notifier.py    ← 디스코드 알림 (재시도 강화)
  common_utils.py← 공통 헬퍼 (신규)
  db_manager.py  ← DB 매니저 (WAL/캐시 단축)
  strategy.py    ← 전략 (본절보호/트레일링 강화)
  ai_analyzer.py ← AI 분석 (점수 분포 명확)
  risk_manager.py← 리스크 관리 (포지션 사이징)
"""

import os
import time
import json
import asyncio
import datetime
from dotenv import load_dotenv

from common_utils import (
    now_kst, now_hhmm, now_hms, today_str,
    is_weekend, safe_int, safe_float,
    read_state, write_state, update_state,
    fmt_won, fmt_pct,
)
from db_manager   import DBManager
from notifier     import Notifier
from kis_api      import KisAPI
from kiwoom_api   import KiwoomAPI
from ai_analyzer  import AIAnalyzer
from strategy     import Strategy
from risk_manager import RiskManager

load_dotenv()


# ============================================================
# 상수 (튜닝 포인트)
# ============================================================
MAX_POSITIONS    = 5         # 최대 보유 종목 수
LIMIT_PER_STOCK  = 300000    # 종목당 최대 가격 (고가주 회피)
BUY_1ST_AMT_BASE = 200000    # 1차 매수 기본 금액 (점수에 따라 ±)
BUY_2ND_AMT      = 100000    # 2차 매수 (눌림목/추격)
BUY_SCORE_MIN    = 45        # 후보군 최소 점수
BUY_SCORE_ENTER  = 55        # 매수 진입 기준 점수 (동적 조정됨)
LOOP_SLEEP       = 30        # 메인 루프 주기 (초)
POOL_SIZE        = 150       # 분석 풀 크기

# 시간 상수
EOD_SELL_TIME    = "1515"
REG_MARKET_START = "0900"
REG_MARKET_END   = "1530"
BUY_START_TIME   = "0920"
SLEEP_INTERVAL   = 60

# 키움 조건검색식 필터
USE_COND_KEYWORDS = ["단타", "주도주", "장개장", "090930"]

# 시장 방어
MARKET_WEAK_THRESH = -1.5   # 코스피 -1.5% 이하 → 약세장
MARKET_STOP_THRESH = -2.5   # 코스피 -2.5% 이하 → 중단
MAX_DAILY_LOSS     = 2      # 하루 최대 손절 횟수
MAX_SAME_SECTOR    = 2      # 같은 업종 동시 보유 최대

# 업종/테마
SECTOR_CHECK_START = "0920"
SECTOR_TOP_N       = 3
SECTOR_MIN_RATE    = 0.5
SECTOR_CODE_MAP = {
    "005": "반도체",        "009": "전력전선",
    "016": "우주방산통신",  "017": "우주방산통신",
    "021": "제약바이오",    "022": "제약바이오",
    "024": "화장품건강기기","025": "화장품건강기기",
    "027": "2차전지",       "034": "원전신재생전력",
    "036": "조선철강",      "037": "조선철강",
    "042": "로봇자율주행",  "043": "로봇자율주행",
}

# 파일 경로
BOT_STATE_FILE = "bot_state.json"


# ============================================================
# 상태 파일 헬퍼 (common_utils 래핑)
# ============================================================
def _read_state() -> dict:
    return read_state(BOT_STATE_FILE, default={
        "paused":       False,
        "score_enter":  BUY_SCORE_ENTER,
        "pending_cmd":  None,
        "cmd_result":   None,
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
class NBot:
    """단타봇 본체."""

    def __init__(self):
        print("🚀 [영암9 LIVE] 단타봇 가동")

        # 모듈 초기화
        self.db        = DBManager()
        self.notify    = Notifier(name="nbot")
        self.api       = KisAPI()
        self.kiwoom    = KiwoomAPI()
        self.ai        = AIAnalyzer(self.db)
        self.strategy  = Strategy()
        self.risk      = RiskManager(
            base_buy_amt         = BUY_1ST_AMT_BASE,
            max_daily_loss_count = MAX_DAILY_LOSS,
        )

        self.db.init_ai_db()
        self.db.init_trade_db()

        # ── 거래 상태 ─────────────────────────────────────
        self.positions    = {}       # {code: {entry_price, qty}}
        self.score_cache     = {}  # 분析 결과 캐시 {code: (score, data, ts)}
        self._low_score_skip = {}  # ★ 점수 미달 재분析 금지 {code: ts}
        self.buy_context  = {}       # 매수 시 컨텍스트 (DB 저장용)
        self.peak_tracker = {}       # ★ 종목별 고점/단계 추적
        self.buy_tags     = {}       # 매수 태그 (theme_buy 등)
        self.sold_today   = {}       # 오늘 매도한 종목 (재매수 금지)
        self.code_name_map = {}
        self.atr_cache    = {}       # ★ ATR 캐시 (변동성)

        # ── 메모리 캐시 ─────────────────────────────────
        self._tech_cache = {}
        self._flow_cache = {}

        # ── 일일 상태 ─────────────────────────────────────
        self._sold_today_date = today_str()
        self._holiday_checked = ""
        self._is_holiday      = False
        self._is_paused       = False

        # ── 시장 상태 ─────────────────────────────────────
        self.market_status     = "normal"
        self.market_rate       = 0.0
        self.daily_loss_count  = 0
        self.daily_loss_amount = 0
        self._last_market_check = 0

        # ── 업종/테마 ─────────────────────────────────────
        self.active_sectors           = []
        self.sector_group_map         = {}
        self.theme_codes              = []
        self.new_codes_list           = []
        self.cond_codes               = set()  # ★ 조건검색식 출처 종목
        self.theme_group_map          = {}
        self._sector_check_done_today = set()

        if self.kiwoom.enabled:
            print(f"✅ 키움 연동 | 조건검색 필터: {USE_COND_KEYWORDS}")
        else:
            print("⚠️ 키움 API 없음 → 한투 폴백")

    # ============================================================
    # 알림 헬퍼
    # ============================================================
    def _notify(self, msg: str, critical: bool = False):
        """디스코드 알림 (critical=True면 5회 재시도)"""
        self.notify.send(msg, critical=critical)

    def _name(self, code: str) -> str:
        return self.code_name_map.get(code, code)

    # ============================================================
    # 시장 상태 업데이트
    # ============================================================
    def _update_market_status(self):
        """코스피/코스닥 등락률을 보고 normal/weak/stop 판정"""
        idx    = self.api.get_market_index()
        kospi  = idx.get("kospi", 0.0)
        kosdaq = idx.get("kosdaq", 0.0)

        if kospi == 0.0 and kosdaq == 0.0:
            print(f"⚠️ 시장지수 조회 실패 — 기존 유지: {self.market_status}")
            return

        self.market_rate = kospi

        if kospi <= MARKET_STOP_THRESH:
            status, emoji = "stop", "🚨"
        elif kospi <= MARKET_WEAK_THRESH:
            status, emoji = "weak", "⚠️"
        else:
            status, emoji = "normal", "✅"

        if status != self.market_status:
            self._notify(
                f"{emoji} 시장상태 변경: {self.market_status} → {status}\n"
                f"코스피: {kospi:+.2f}% | 코스닥: {kosdaq:+.2f}%",
                critical=(status == "stop"),
            )

        self.market_status = status
        print(f"📊 시장: {status} | 코스피:{kospi:+.2f}% | 코스닥:{kosdaq:+.2f}%")

    # ============================================================
    # 업종/테마 관심그룹 로딩 (한투 + 키움 테마 API)
    # ============================================================
    def _load_watchlist_groups(self):
        """한투 관심그룹(업종_*/테마/new) + 키움 테마 API"""
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
        try:
            top_themes = self.kiwoom.get_theme_top(top_n=5)
            if top_themes:
                for item in top_themes:
                    grp_cd = item.get("thema_grp_cd", "")
                    grp_nm = item.get("thema_nm", item.get("thema_grp_nm", "테마"))
                    if not grp_cd:
                        continue
                    stocks = self.kiwoom.get_theme_stocks(grp_cd, self.code_name_map)
                    codes  = [c for c, _ in stocks]
                    self.theme_group_map[grp_nm] = codes
                    for c in codes:
                        if c not in theme_list:
                            theme_list.append(c)
                    time.sleep(0.2)
        except Exception as e:
            print(f"⚠️ 키움 테마 API 오류: {e}")

        self.sector_group_map = sector_map
        self.theme_codes      = list(dict.fromkeys(theme_list))
        self.new_codes_list   = list(dict.fromkeys(new_list))

        _update_state(
            kiwoom_themes={k: v[:5] for k, v in self.theme_group_map.items()},
            kiwoom_theme_at=now_kst().strftime("%Y-%m-%d %H:%M"),
        )
        print(
            f"✅ 관심그룹 | 업종:{len(sector_map)}그룹 | "
            f"테마:{len(self.theme_codes)}종목 | new:{len(self.new_codes_list)}종목"
        )

    def _update_active_sectors(self):
        """오늘 강세 업종 갱신 (1시간마다)"""
        print(f"\n🔍 업종 체크...")
        rates = self.api.get_sector_change_rates(SECTOR_CODE_MAP)
        active_kws = []
        if rates:
            sorted_rates = sorted(rates.items(), key=lambda x: x[1], reverse=True)
            active_codes = [c for c, r in sorted_rates if r >= SECTOR_MIN_RATE][:SECTOR_TOP_N]
            active_kws = list(dict.fromkeys([
                SECTOR_CODE_MAP[c] for c in active_codes if c in SECTOR_CODE_MAP
            ]))
            print(f"  📊 강세 업종: {[(c, f'{rates[c]:+.2f}%') for c in active_codes]}")

        # 뉴스 힌트 (네이버 뉴스 API)
        try:
            import urllib.parse, re as _re, requests as _req
            client_id     = os.getenv("NAVER_CLIENT_ID", "")
            client_secret = os.getenv("NAVER_CLIENT_SECRET", "")
            if client_id and client_secret:
                query = urllib.parse.quote("오늘 증시 강세 업종 테마주")
                url   = f"https://openapi.naver.com/v1/search/news.json?query={query}&display=10&sort=date"
                items = _req.get(url, headers={
                    "X-Naver-Client-Id":     client_id,
                    "X-Naver-Client-Secret": client_secret,
                }, timeout=5).json().get("items", [])
                text = " ".join(
                    _re.sub(r"<[^>]+>", "", i.get("title", "") + " " + i.get("description", ""))
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

        self._load_watchlist_groups()  # 갱신
        self.active_sectors = matched

        _update_state(
            active_sectors=matched,
            sector_updated_at=now_kst().strftime("%Y-%m-%d %H:%M"),
        )

        if set(matched) != set(prev):
            added   = [s for s in matched if s not in prev]
            removed = [s for s in prev   if s not in matched]
            msg = "🎯 강세 업종 변경!\n"
            if added:   msg += f"  ✅ 활성: {', '.join(added)}\n"
            if removed: msg += f"  ❌ 비활성: {', '.join(removed)}\n"
            msg += f"  현재: {', '.join(matched) or '없음'}"
            self._notify(msg)
        else:
            print(f"  ✅ 활성 업종 유지: {matched or '없음'}")

    def _should_check_sector(self, now_t: str) -> bool:
        if now_t < SECTOR_CHECK_START:
            return False
        now_dt = now_kst()
        if now_dt.minute != int(SECTOR_CHECK_START[2:]):
            return False
        check_key = now_dt.strftime("%Y-%m-%d %H")
        return check_key not in self._sector_check_done_today

    def _get_sector_theme_codes(self) -> list:
        """활성 업종+테마+신규추천 종목 합본 (중복 제거)"""
        codes, seen = [], set()
        for kw in self.active_sectors:
            for c in self.sector_group_map.get(kw, []):
                if c not in seen:
                    seen.add(c); codes.append(c)
        for c in self.theme_codes + self.new_codes_list:
            if c not in seen:
                seen.add(c); codes.append(c)
        return codes

    # ============================================================
    # 종목 풀 조회 (키움 조건검색식 → 한투 폴백)
    # ============================================================
    def _get_pool(self) -> list:
        """매매할 후보 종목 리스트 반환."""
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
                    # ★ 조건검색 출처 종목 저장 (가점용)
                    self.cond_codes = set(codes)
                    st = _read_state()
                    # 관심종목 추가
                    for wc in st.get("watchlist", []):
                        if wc not in codes and wc.isdigit():
                            codes.append(wc)
                    # 업종/테마 종목 추가
                    added = 0
                    for sc in self._get_sector_theme_codes():
                        if sc not in codes:
                            codes.append(sc); added += 1
                    if added:
                        print(f"  🎯 업종/테마 {added}개 풀 추가")
                    result = codes[:POOL_SIZE]
                    print(f"🎯 종목 풀 (키움): {len(result)}개 | 조건검색:{len(self.cond_codes)}개")
                    return result
                else:
                    print("⚠️ 키움 조건검색 없음 → 한투 폴백")
            except Exception as e:
                print(f"⚠️ 키움 오류: {e} → 한투 폴백")

        # 한투 폴백 (psearch)
        return self._get_pool_kis_fallback()

    def _get_pool_kis_fallback(self) -> list:
        """한투 사용자 등록 조건검색 + 거래량 순위 폴백"""
        import requests as _req
        hts_id = os.getenv("KIS_HTS_ID", "")
        seen, codes = set(), []
        skip = ["KODEX","TIGER","KBSTAR","ARIRANG","HANARO",
                "KOSEF","TREX","SOL","ACE","PLUS",
                "인버스","레버리지","ETN","선물","RISE","TIME"]

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
                res = _req.get(url, headers=headers, params=params, timeout=5).json()
                if res.get("rt_cd") != "0":
                    continue
                for item in res.get("output2", []):
                    code = item.get("code", "").strip()
                    name = item.get("name", "").strip()
                    if not code or code in seen or not code.isdigit():
                        continue
                    if any(kw in name for kw in skip):
                        continue
                    seen.add(code); codes.append(code)
                    self.code_name_map[code] = name
            except Exception as e:
                print(f"❌ 조건검색 seq={seq} 예외: {e}")

        if len(codes) < 10:
            codes += self.api.get_volume_rank_codes(seen, self.code_name_map)

        # 관심종목 + 업종/테마 추가
        st = _read_state()
        for wc in st.get("watchlist", []):
            if wc not in seen and wc.isdigit():
                seen.add(wc); codes.append(wc)
        for sc in self._get_sector_theme_codes():
            if sc not in seen:
                seen.add(sc); codes.append(sc)

        result = codes[:POOL_SIZE]
        print(f"🎯 종목 풀 (한투): {len(result)}개")
        return result

    # ============================================================
    # 매수 / 매도 실행
    # ============================================================
    def _do_buy(self, code: str, price: float, amount: int,
                is_second: bool = False):
        """
        매수 주문 실행.
        ★ 개선: 매수 성공 시 self.positions를 즉시 업데이트해
                   다음 매도 체크에서 누락 방지.
        """
        ok = self.api.buy(code, price, amount, self.code_name_map)
        if not ok:
            return

        ctx = self.buy_context.get(code, {})
        qty = max(int(amount / price), 1) if price > 0 else 0

        # ★ 매수 직후 메모리에 즉시 반영 (다음 루프까지 기다리지 않음)
        if not is_second:
            self.positions[code] = {
                "entry_price": price,
                "qty":         qty,
            }
        else:
            # 2차 매수면 평단/수량 합산 (정확치는 다음 루프에서 API 갱신됨)
            existing = self.positions.get(code, {"entry_price": price, "qty": 0})
            old_qty  = existing["qty"]
            old_avg  = existing["entry_price"]
            new_qty  = old_qty + qty
            if new_qty > 0:
                new_avg  = (old_avg * old_qty + price * qty) / new_qty
                self.positions[code] = {"entry_price": new_avg, "qty": new_qty}

        self._notify(
            f"🚀 매수 {code}({self._name(code)}) | "
            f"{fmt_won(amount)} | {price:,.0f}원 | {qty}주",
            critical=True,
        )

        # DB 저장
        self.db.save_buy_history(
            code=code, buy_price=price, qty=qty,
            ai_score  = ctx.get("ai_score", 0),
            ai_reason = ctx.get("ai_reason", ""),
            indicators= ctx.get("indicators", {}),
            stock_name= self._name(code),
            buy_tag   = ctx.get("buy_tag", ""),
        )

        # 1차 매수일 때만 sold_today에 등록 (재매수 금지용)
        if not is_second:
            self.sold_today[code] = now_hms()

    def _do_sell(self, code: str, qty: int, reason: str, sell_price: float):
        """
        매도 주문 실행.
        ★ 개선: 부분 매도 시 buy_tags / buy_context를 절대 삭제하지 않음.
                  전량 매도일 때만 정리.
        """
        if qty <= 0:
            return

        ok = self.api.sell(code, qty)
        if not ok:
            return

        # 보유 수량 비교로 전량/부분 매도 판단
        current_pos = self.positions.get(code, {})
        held_qty    = current_pos.get("qty", 0)
        is_full_sell = (qty >= held_qty)

        is_loss = "손절" in reason or "본절" in reason
        emoji   = "💔" if is_loss else "💰"
        self._notify(
            f"{emoji} 매도 {code}({self._name(code)}) | {reason} | {qty}주",
            critical=True,
        )

        # DB 저장
        self.db.save_sell_history(code, sell_price, reason,
                                  sold_qty=0 if is_full_sell else qty)

        # ★ 핵심: 전량 매도일 때만 컨텍스트 정리
        if is_full_sell:
            self.buy_tags.pop(code, None)
            self.buy_context.pop(code, None)
            self.positions.pop(code, None)
        else:
            # 부분 매도: 잔량만 갱신 (entry_price는 유지)
            self.positions[code] = {
                "entry_price": current_pos.get("entry_price", sell_price),
                "qty":         held_qty - qty,
            }

        # sold_today는 항상 갱신 (재매수 금지)
        self.sold_today[code] = now_hms()

        # 상태 파일에도 sold_today 저장
        st = _read_state()
        st["sold_today"]      = self.sold_today
        st["sold_today_date"] = today_str()
        write_state(BOT_STATE_FILE, st)

    def _do_loss(self):
        """손절 카운터 +1 → 일일 한도 도달 시 자동 일시중단"""
        self.daily_loss_count += 1
        print(f"📉 당일 손절 누적: {self.daily_loss_count}회")
        _update_state(
            daily_loss=self.daily_loss_count,
            loss_date=today_str(),
        )


    # ============================================================
    # ATR 계산 (변동성 기반 손절선 조정)
    # ============================================================
    def _get_atr_rate(self, code: str) -> float:
        """
        종목의 변동성(ATR / 현재가) 반환.
        변동성 큰 종목은 손절선을 넓게 → 잡음으로 인한 손절 방지.
        """
        if code in self.atr_cache:
            cached_rate, ts = self.atr_cache[code]
            if time.time() - ts < 1800:  # 30분 캐시
                return cached_rate

        try:
            # KIS API로 일봉 데이터 가져와 ATR 계산
            ohlc = self.api.get_daily_ohlc(code, days=20) if hasattr(self.api, 'get_daily_ohlc') else []
            if not ohlc:
                # get_daily_ohlc가 없으면 0 반환 (전략에서 ATR 미적용)
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
        """날짜가 바뀌면 모든 일일 상태 초기화"""
        self.sold_today               = {}
        self._sold_today_date         = today
        self.daily_loss_count         = 0
        self.daily_loss_amount        = 0
        self.market_status            = "normal"
        self.active_sectors           = []
        self._sector_check_done_today = set()
        self._tech_cache              = {}
        self._flow_cache              = {}
        self.buy_tags                 = {}
        self.atr_cache                = {}
        self.api._mkt_cache           = {}

        _update_state(
            sold_today={},
            sold_today_date=today,
            daily_loss=0,
            loss_date=today,
        )
        print("🔄 일일 초기화 완료")

        # 관심그룹 재로딩
        if self.kiwoom.enabled:
            try:
                self._load_watchlist_groups()
            except Exception as e:
                print(f"⚠️ 관심그룹 재로딩 오류: {e}")

    # ============================================================
    # 긴급 명령 처리 (디스코드 → kiki.py → state file)
    # ============================================================
    def _handle_pending_command(self, st: dict):
        """디스코드 봇이 남긴 매수/매도 명령 처리"""
        pending = st.get("pending_cmd")
        if not pending:
            return

        cmd_type = pending.get("type")

        # ── 즉시 매도 ─────────────────────────────────────
        if cmd_type == "sell":
            sell_code = pending.get("code", "")
            if sell_code in self.positions:
                # ★ 시세 조회 3회 재시도 (실패 시 포지션 진입가 사용)
                s_price = 0
                for attempt in range(3):
                    mdata   = self.api.get_market_data(sell_code)
                    s_price = safe_float(mdata.get("stck_prpr", 0)) if mdata else 0
                    if s_price > 0:
                        break
                    import time as _t; _t.sleep(1)

                # 시세 조회 실패 시 포지션 진입가 사용 (0 저장 방지)
                if s_price <= 0:
                    s_price = self.positions[sell_code].get("entry_price", 0)
                    print(f"⚠️ {sell_code} 시세 조회 실패 → 진입가({s_price:,}원)로 대체")

                self._do_sell(
                    sell_code,
                    self.positions[sell_code]["qty"],
                    "즉시매도(AI비서)",
                    s_price,
                )
                _write_cmd_result(f"✅ {sell_code} 즉시매도 완료 ({s_price:,.0f}원)")
            else:
                _write_cmd_result(f"⚠️ {sell_code} 보유 중이 아님")

        # ── 수동 매수 ─────────────────────────────────────
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
                "ai_score":   0,
                "ai_reason":  "수동매수",
                "indicators": {},
                "buy_tag":    "",
            }
            self._do_buy(buy_code, cur, int(cur * buy_qty * 1.01), is_second=False)

            # peak_tracker 즉시 초기화
            if buy_code not in self.peak_tracker:
                self.peak_tracker[buy_code] = {
                    "peak_rate":       0.0,
                    "stage":           0,
                    "remain_qty":      buy_qty,
                    "buy2_done":       True,  # 수동 매수는 2차 안함
                    "buy1_price":      cur,
                    "effective_entry": cur,
                }
            _write_cmd_result(f"✅ {buy_code} {buy_qty}주 매수 완료")

    # ============================================================
    # 한 종목 분석 (룰 + 필터)
    # ============================================================
    def _analyze_one_code(self, code: str, sector_all: set,
                         now_t: str) -> tuple:
        """
        한 종목을 분석해 (data, rule_score) 반환.
        부적격 종목은 (None, 0) 반환.
        """
        basic = self.api.get_market_data(code)
        if not basic:
            return None, 0

        try:
            data = {
                "current_price": safe_float(basic.get("stck_prpr",  0)),
                "change_rate":   safe_float(basic.get("prdy_ctrt",  0)),
                "trading_value": safe_int(basic.get("acml_tr_pbmn", 0)) // 100_000_000,
                "volume":        safe_int(basic.get("acml_vol",     0)),
                "volume_ratio":  safe_float(basic.get("vol_inrt",   0)),
                "vol_tnrt":      safe_float(basic.get("vol_tnrt",   0)),
                "hts_avls":      safe_int(basic.get("hts_avls",     0)),
                "stock_name":    basic.get("hts_kor_isnm", ""),
                "stck_hgpr":     safe_float(basic.get("stck_hgpr",  0)),
                "stck_sdpr":     safe_float(basic.get("stck_sdpr",  0)),
                "vol_rate":      safe_float(basic.get("prdy_vrss_vol_rate", 0)),
            }
            data.update(self.api.get_technical_indicators(code, self._tech_cache))
            data.update(self.api.get_investor_trend(code, self._flow_cache))

            # 업종/테마 매칭 여부
            is_sc = code in sector_all
            data["buy_tag"] = "theme_buy" if is_sc else ""

            # ── 매수 필터 (전략 모듈에 위임) ─────────────────
            passes, reason = self.strategy.passes_buy_filter(data, is_sector_match=is_sc)
            if not passes:
                print(f" → {reason}"); return None, 0

            # 거래량/가격/시총 필터
            if data["volume"] < 30_000:
                print(" → 거래량 부족"); return None, 0
            if data["current_price"] <= 999:
                print(" → 동전주 제외"); return None, 0
            mkt_cap = data["hts_avls"]
            if mkt_cap < 500:
                print(f" → 소형주 제외 ({mkt_cap:,}억)"); return None, 0
            if mkt_cap > 50000:
                print(f" → 대형주 제외 ({mkt_cap:,}억)"); return None, 0
            if data["trading_value"] < 50:
                print(" → 거래대금 부족"); return None, 0

            # 고점 대비 -5% 이상 하락 제외 (이미 꺾인 종목)
            hg  = data["stck_hgpr"]
            cur = data["current_price"]
            if hg > 0 and (cur - hg) / hg < -0.05:
                print(f" → 고점 대비 -5% 이상 하락"); return None, 0

            # 일반 종목 추가 필터 (업종/테마는 면제)
            if not is_sc:
                if data["vol_tnrt"] < 2.0:
                    print(" → 회전율 부족"); return None, 0
                _vm = 100 if now_t < "1030" else 50
                if 0 < data["volume_ratio"] < _vm:
                    print(f" → 거래량증가율 부족"); return None, 0
                ma5  = data.get("ma5",  0)
                ma20 = data.get("ma20", 0)
                if ma5 > 0 and ma20 > 0 and ma5 < ma20:
                    print(" → MA 역배열"); return None, 0
                sd = data["stck_sdpr"]
                if hg > 0 and sd > 0 and (hg - sd) / sd * 100 < 3.0:
                    print(" → 고가상승 부족"); return None, 0
                if data["vol_rate"] < 50:
                    print(" → 거래량비율 부족"); return None, 0

            if data["current_price"] > LIMIT_PER_STOCK:
                print(" → 고가 제외"); return None, 0
            
            # 🚨 [백테스트용 데이터 통역기] 🚨
            if "current_price" not in data:
                data["current_price"] = data.get("close", 0)
                data["change_rate"]   = data.get("change", 0)
                data["trading_value"] = data.get("value", 0) // 100_000_000
                # nbot의 단타 조건에 맞게 시총을 작게(예: 5000억) 세팅
                data["mkt_cap"]       = 5000 
                data["foreign_5d"]    = 10000 
                data["institution_5d"]= 10000

            rule_score = self.strategy.get_rule_score(data)
            print(f" → 룰:{rule_score}점" + (" 🎯" if is_sc else ""))
            return data, rule_score
        except Exception as e:
            print(f" → 오류: {e}")
            return None, 0

    # ============================================================
    # 메인 루프
    # ============================================================
    def run(self):
        """봇 실행. 무한 루프로 30초마다 매매 체크."""
        self._notify(
            f"🚀 [영암9 LIVE] 단타봇 가동\n"
            f"⏰ {now_kst().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"💰 1차:{fmt_won(BUY_1ST_AMT_BASE)} / 최대 {MAX_POSITIONS}종목\n"
            f"⏳ 매수: {BUY_START_TIME} ~ {EOD_SELL_TIME}\n"
            f"🔍 조건검색 필터: {USE_COND_KEYWORDS}",
            critical=True,
        )
        self._is_paused = False
        self._last_market_check = 0

        # 초기 관심그룹 로딩
        if self.kiwoom.enabled:
            try:
                self._load_watchlist_groups()
            except Exception as e:
                print(f"⚠️ 관심그룹 초기 로딩 오류: {e}")

        while True:
            try:
                # ★ 핵심: today를 루프 맨 앞에서 정의 (휴장일 체크 NameError 방지)
                today   = today_str()
                now_t   = now_hhmm()
                now     = now_hms()

                # ── 주말 ─────────────────────────────────────
                if is_weekend():
                    print(f"😴 [{now}] 주말 — 장 없음")
                    time.sleep(SLEEP_INTERVAL)
                    continue

                # ── 휴장일 체크 (하루 1회) ──────────────────────
                if self._holiday_checked != today:
                    self._is_holiday      = not self.api.is_market_open()
                    self._holiday_checked = today
                    if self._is_holiday:
                        self._notify("🎌 오늘은 휴장일 — 봇 대기")

                if self._is_holiday:
                    print(f"🎌 [{now}] 휴장일 — 대기 중...")
                    time.sleep(300)
                    continue

                # ── 정규장 시간 체크 ────────────────────────────
                is_reg = REG_MARKET_START <= now_t <= REG_MARKET_END
                if not is_reg:
                    print(f"😴 [{now}] 장외 대기 중...")
                    time.sleep(SLEEP_INTERVAL)
                    continue

                print(f"\n📈 정규장 [{now}]")

                # ── 상태 읽기 ─────────────────────────────────
                st              = _read_state()
                self._is_paused = st.get("paused", False)

                # ── 일일 초기화 ─────────────────────────────
                if today != self._sold_today_date:
                    self._daily_reset(today)
                else:
                    # 동일 날짜인데 sold_today가 비어있으면 상태 파일에서 복원
                    if not self.sold_today:
                        saved = st.get("sold_today", {})
                        if saved and st.get("sold_today_date") == today:
                            self.sold_today = saved
                            if self.sold_today:
                                print(f"♻️ sold_today 복원: {list(self.sold_today.keys())}")

                # ── 동적 매수 임계치 ─────────────────────────
                base_score   = st.get("score_enter", BUY_SCORE_ENTER)
                score_enter  = self.db.get_dynamic_score_threshold(base_threshold=base_score)

                # 손절 카운터 초기화 (디스코드에서 !시작 명령으로 daily_loss=0 보낼 때)
                if (st.get("daily_loss") == 0 and self.daily_loss_count > 0
                        and st.get("loss_date") != today):
                    self.daily_loss_count = 0
                    print("♻️ 손절카운터 초기화")

                # ── 디스코드 명령 처리 ───────────────────────
                self._handle_pending_command(st)

                # ── 토큰 갱신 / DB 정리 ──────────────────────
                self.api.refresh_token_if_needed()
                self.db.clean_ai_db()

                # ── 계좌 상태 ────────────────────────────────
                cash           = self.api.get_buyable_cash()
                self.positions = self.api.get_current_positions()
                psbl_cash      = self.api.get_psbl_order_cash("005930")
                if psbl_cash <= 0:
                    psbl_cash = cash
                print(f"\n⏰ {now} | 💵 예수금: {cash:,} | 💰 주문가능: {psbl_cash:,}")

                # ── 보유종목 현황 ─────────────────────────────
                pos_mkt_cache = {}
                total_profit  = 0
                print("📦 보유종목")
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
                    tag = "🎯" if self.buy_tags.get(code) == "theme_buy" else "  "
                    print(f"  {tag}💰 {code}({self._name(code)}) | {rate:+.2f}% | {qty}주")
                print(f"📈 총손익: {int(total_profit):,}원")
                print(f"🏭 활성 업종: {self.active_sectors or '없음'}")

                # ── 시장 상태 (5분마다) ──────────────────────
                if time.time() - self._last_market_check > 300:
                    self._update_market_status()
                    self._last_market_check = time.time()

                # ── 업종 체크 (매시 정각) ─────────────────────
                if self._should_check_sector(now_t):
                    check_key = now_kst().strftime("%Y-%m-%d %H")
                    self._sector_check_done_today.add(check_key)
                    self._update_active_sectors()

                # ── 시장 stop 모드 ─────────────────────────────
                if self.market_status == "stop":
                    print(f"🚨 시장 중단 모드 | 코스피:{self.market_rate:+.2f}%")
                    for code, pos in list(self.positions.items()):
                        mdata = pos_mkt_cache.get(code) or self.api.get_market_data(code)
                        if not mdata:
                            continue
                        cur   = safe_float(mdata.get("stck_prpr", 0))
                        entry = pos["entry_price"]
                        if entry > 0 and cur > 0 and (cur - entry)/entry <= -0.03:
                            self._notify(f"🚨 긴급손절(약세장) {code}", critical=True)
                            self._do_sell(code, pos["qty"], "긴급손절(약세장)", cur)
                            self._do_loss()
                            self.peak_tracker.pop(code, None)
                    time.sleep(LOOP_SLEEP)
                    continue

                # ── 일시중단 모드 ──────────────────────────────
                if self._is_paused:
                    print("⏸️ 일시중단 — 매도 체크만")
                    self._check_sells_only(pos_mkt_cache, now_t)
                    self._save_status(cash, psbl_cash, total_profit, score_enter,
                                     pos_mkt_cache, now)
                    time.sleep(LOOP_SLEEP)
                    continue

                # ── 종목 풀 ─────────────────────────────────
                codes = self._get_pool()
                if not codes:
                    print("⚠️ 종목 풀 없음, 재시도...")
                    time.sleep(5)
                    continue

                # ── 종목 분석 ───────────────────────────────
                self._run_analysis(codes, now_t, score_enter, psbl_cash,
                                  pos_mkt_cache)

                # ── 매도 체크 ───────────────────────────────
                self._check_all_sells(pos_mkt_cache, now_t)

                # ── 상태 저장 ───────────────────────────────
                self._save_status(cash, psbl_cash, total_profit, score_enter,
                                 pos_mkt_cache, now)

                time.sleep(LOOP_SLEEP)

            except KeyboardInterrupt:
                perf = self.db.get_recent_performance(limit=20)
                msg  = (f"🛑 [영암9] 봇 종료\n"
                        f"⏰ {now_kst().strftime('%Y-%m-%d %H:%M:%S')}\n")
                if perf:
                    msg += (f"📊 최근 {perf['total']}건 | "
                            f"승률:{perf['win_rate']}% | 평균:{perf['avg_profit']:+.2f}%")
                self._notify(msg, critical=True)
                break
            except Exception as e:
                print(f"🚨 루프 오류: {e}")
                import traceback; traceback.print_exc()
                time.sleep(5)

    # ============================================================
    # 분석 + 매수 실행
    # ============================================================
    def _run_analysis(self, codes: list, now_t: str, score_enter: int,
                      psbl_cash: int, pos_mkt_cache: dict):
        """후보 종목 분석 → 상위 종목 매수"""

        # ★ 점수 미달 종목 재분析 금지 (30분 유지)
        now_ts = time.time()
        self._low_score_skip = {
            c: ts for c, ts in self._low_score_skip.items()
            if now_ts - ts < 1800  # 30분
        }
        # ★ score_cache 30분 유지 (풀 진입/이탈 무관)
        self.score_cache = {
            c: v for c, v in self.score_cache.items()
            if len(v) < 3 or now_ts - v[2] < 1800
        }
        skip_set     = set(self._low_score_skip.keys())
        new_codes    = [c for c in codes
                        if c not in self.score_cache and c not in skip_set]
        cached_codes = [c for c in codes if c in self.score_cache]
        print(f"\n🔄 분석: 신규 {len(new_codes)}개 | 캐시 {len(cached_codes)}개")

        sector_all = set(self._get_sector_theme_codes())

        # 1) 룰 점수 계산
        rule_candidates = []
        for idx, code in enumerate(new_codes):
            print(f"🔎 룰분석 {idx+1}/{len(new_codes)}: {code}", end="")
            data, rule_score = self._analyze_one_code(code, sector_all, now_t)
            if data is not None:
                rule_candidates.append((code, rule_score, data))

        # 2) 상위 10개 AI 분석 + 가산점
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
                cond_codes=self.cond_codes,  # ★ 조건검색 가점
            )
            if bonus_reason:
                reason = f"{reason} | {bonus_reason}"
            if buy_tag:
                data["buy_tag"] = buy_tag
            print(f"   🧠 {code} | 룰:{rule_score}→AI:{score}점 | {reason}")
            data["ai_reason"] = reason
            self.score_cache[code] = (score, data, time.time())
            if score < BUY_SCORE_MIN:
                self._low_score_skip[code] = time.time()

        # 3) AI 분석 안 한 종목은 룰 점수 + 가산점만
        for code, rule_score, data in rest:
            score, bonus_reason, buy_tag = self.strategy.apply_sector_bonus(
                code, rule_score, self.active_sectors,
                self.sector_group_map, self.theme_codes, self.new_codes_list,
                cond_codes=self.cond_codes,  # ★ 조건검색 가점
            )
            data["ai_reason"] = (f"룰점수({rule_score})"
                                + (f" | {bonus_reason}" if bonus_reason else ""))
            if buy_tag:
                data["buy_tag"] = buy_tag
            self.score_cache[code] = (score, data, time.time())
            if score < BUY_SCORE_MIN:
                self._low_score_skip[code] = time.time()

        # 4) 캐시 정리 — ★ 30분 유지 (풀에서 빠져도 유지, 타임스탬프로 만료)
        # 이미 위에서 30분 만료 처리했으므로 여기선 별도 삭제 안함

        # 5) 시간대 보정 적용된 매수 후보
        candidates = []
        for code, cache_val in self.score_cache.items():
            score, data = cache_val[0], cache_val[1]
            if score < BUY_SCORE_MIN:
                continue
            adjusted_score = score + self.risk.time_score_modifier(now_t)
            candidates.append((code, adjusted_score, data))

        def sort_key(x):
            _, score, d = x
            return (
                d.get("buy_tag") == "theme_buy",
                not d.get("ai_reason", "").startswith("룰점수"),
                score,
            )

        candidates.sort(key=sort_key, reverse=True)
        top10 = candidates[:10]

        cached_codes_set = set(cached_codes)
        print(f"\n🔥 TOP{len(top10)} (후보 {len(candidates)}개 중):")
        for code, score, d in top10:
            tag1 = "🎯" if d.get("buy_tag") == "theme_buy" else "  "
            tag2 = "🤖" if not d.get("ai_reason", "").startswith("룰점수") else "📐"
            ct   = "📦" if code in cached_codes_set else "🆕"
            print(f"  {ct}{tag1}{tag2} {code}({self._name(code)}) | "
                  f"{score}점 | {d.get('ai_reason', '')}")

        # 6) 매수 조건 체크 + 실행
        self._execute_buys(top10, now_t, score_enter, psbl_cash)

    def _execute_buys(self, top10: list, now_t: str,
                      score_enter: int, psbl_cash: int):
        """매수 가능한 종목들 실제 주문"""

        # 슬롯 계산: 1차 익절 후 종목은 매수 슬롯에서 제외
        익절중 = sum(
            1 for c in self.positions
            if self.peak_tracker.get(c, {}).get("stage", 0) >= 1
        )
        slots = MAX_POSITIONS - len(self.positions) + 익절중
        if 익절중:
            print(f"  ♻️ 익절진행중 {익절중}종목 슬롯 반환 → 가용:{slots}")

        # 매수 가능 시간/시장 상태 체크
        if now_t < BUY_START_TIME:
            print(f"⏳ {BUY_START_TIME} 이전 — 매수 대기 중")
            return
        if now_t >= EOD_SELL_TIME:
            print("🔔 종가매도 시간 이후 — 매수 금지")
            return

        # 일일 손실 한도 체크
        should_stop, stop_reason = self.risk.should_stop_trading(
            self.daily_loss_count,
        )
        if should_stop:
            print(f"🛑 {stop_reason} — 매수 정지")
            st = _read_state()
            if not st.get("paused"):
                self._notify(f"🛑 {stop_reason}\n!시작 으로 재개", critical=True)
                _update_state(paused=True)
            return

        if slots <= 0:
            print("📦 포지션 FULL")
            return

        # 매수 실행
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
                print(f"🚫 재매수 금지 {code}")
                continue

            # 시장 상태 체크 (★ 약세장에서도 강세업종은 허용)
            is_sector_match = (data.get("buy_tag") == "theme_buy")
            allow, reason = self.risk.allow_buy_in_market(
                self.market_status, is_sector_match=is_sector_match,
            )
            if not allow:
                print(f"⚠️ {reason} {code}")
                continue
            if reason:  # 약세장이지만 허용된 경우
                print(f"✅ {reason} {code}")

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
                    print(f"⚠️ 업종 쏠림 방지 {code} [{sector_of}]")
                    continue

            buy_tag = data.get("buy_tag", "")

            # ★ 포지션 사이징: 점수에 비례한 매수 금액
            atr_rate = self._get_atr_rate(code)
            buy_amount = self.risk.calc_buy_amount(
                score=score, atr_rate=atr_rate,
                is_theme=is_sector_match, psbl_cash=psbl_cash,
                code=code,  # ★ 켈리 공식 — 종목별 성과 반영
            )

            print(f"🚀 매수 {code} | {score}점 | {fmt_won(buy_amount)}"
                  f"{f' | 🎯{buy_tag}' if buy_tag else ''}"
                  f"{f' | ATR{atr_rate*100:.1f}%' if atr_rate else ''}")

            # 컨텍스트 저장 (DB 기록용)
            self.buy_context[code] = {
                "ai_score":   score,
                "ai_reason":  data.get("ai_reason", ""),
                "indicators": data,
                "stock_name": data.get("stock_name", ""),
                "buy_tag":    buy_tag,
            }

            # 매수 실행
            self._do_buy(code, data["current_price"], buy_amount)
            self.buy_tags[code] = buy_tag

            # ★ peak_tracker 즉시 초기화 (매수 직후 매도 체크에서 NPE 방지)
            self.peak_tracker[code] = {
                "peak_rate":       0.0,
                "stage":           0,
                "remain_qty":      max(int(buy_amount / data["current_price"]), 1),
                "buy2_done":       False,
                "buy1_price":      data["current_price"],
                "effective_entry": data["current_price"],
            }
            slots -= 1
            time.sleep(1)  # API rate limit 회피

    # ============================================================
    # 매도 체크
    # ============================================================
    def _check_all_sells(self, pos_mkt_cache: dict, now_t: str):
        """모든 보유 종목 매도 체크"""
        for code, pos in list(self.positions.items()):
            mdata = pos_mkt_cache.get(code) or self.api.get_market_data(code)
            if not mdata:
                continue

            tech     = self._tech_cache.get(code, ({}, 0))
            ma10     = tech[0].get("ma10", 0) if isinstance(tech, tuple) else 0
            atr_rate = self._get_atr_rate(code)

            self.strategy.check_sell(
                code, pos, now_t, mdata,
                self.market_status, self.peak_tracker, self.buy_tags,
                self._is_paused,
                lambda c, p, a: self._do_buy(c, p, a, is_second=True),
                lambda c, q, r, sp: self._do_sell(c, q, r, sp),
                self._do_loss,
                ma10=ma10, atr_rate=atr_rate,
            )

    def _check_sells_only(self, pos_mkt_cache: dict, now_t: str):
        """일시중단 시 매도만 체크"""
        self._check_all_sells(pos_mkt_cache, now_t)

    # ============================================================
    # 상태 저장
    # ============================================================
    def _save_status(self, cash: int, psbl_cash: int, total_profit: float,
                     score_enter: int, pos_mkt_cache: dict, now: str):
        """현재 상태를 JSON 파일에 저장 (kiki.py가 읽음)"""
        pos_detail = {}
        for code, pos in self.positions.items():
            mdata = pos_mkt_cache.get(code)
            cur   = safe_float(mdata.get("stck_prpr", 0)) if mdata else 0
            entry = pos["entry_price"]
            qty   = pos["qty"]
            rate  = (cur - entry) / entry * 100 if entry > 0 else 0
            pos_detail[code] = {
                "name":        self._name(code),
                "current":     int(cur),
                "entry_price": int(entry),
                "qty":         qty,
                "rate":        round(rate, 2),
                "buy_tag":     self.buy_tags.get(code, ""),
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


# ============================================================
# 진입점
# ============================================================
if __name__ == "__main__":
    NBot().run()
