"""
tele_swing_analyzer.py
─────────────────────────────────────────────────────────────
텔레그램 언급 종목 기반 정통 스윙 분석 (점수제)

[설계 원칙]
- 텔레그램 언급 종목을 메인 풀로 사용
- 정통 스윙 트레이딩 원칙 기반 점수 산출
- 60점 이상 상위 2종목 선정 → sbo2 보조 슬롯

[점수 구성 100점]
 주봉 추세 (정배열)          +20점
 200일선 위                  +15점
 MACD 골든크로스             +15점
 RSI 30~55 눌림목            +15점
 피보나치 38.2~61.8% 구간    +15점
 거래량 수반 여부             +10점
 텔레그램 언급 빈도/점수      +10점
"""

import os
import re
import sqlite3
import json
import datetime

BASE_DIR         = os.path.dirname(os.path.abspath(__file__))
DB_PATH          = os.path.join(BASE_DIR, "kr_theme_finance.db")
DB_PATH_TELEGRAM = os.path.join(BASE_DIR, "intelligence", "telegram_events.db")

# ── 튜닝 파라미터 ─────────────────────────────────────
MIN_SCORE        = 50     # 최소 통과 점수
MIN_TRADING_VALUE_EOK = 50    # 최소 거래대금(억원) — 잡주 방지
TOP_N            = 2      # 최종 선정 종목 수
TELE_HOURS       = 360    # 텔레그램 수집 시간 범위 (15일 = 360시간)
RSI_PERIOD       = 14
MACD_FAST        = 12
MACD_SLOW        = 26
MACD_SIGNAL      = 9
WEEKLY_MA        = 5      # 주봉 5주선 (일봉 25일 근사)
ATR_PERIOD       = 14
ATR_STOP_MULT    = 1.5
ATR_TARGET_MULT  = 3.0


# ══════════════════════════════════════════════════════════════
# 헬퍼 함수
# ══════════════════════════════════════════════════════════════

def _ma(prices: list, n: int) -> float:
    if len(prices) < n:
        return 0.0
    return sum(prices[:n]) / n

def _ema(prices: list, n: int) -> list:
    """지수이동평균 리스트 반환 (최신→과거 순 입력, 결과도 최신→과거)"""
    if len(prices) < n:
        return []
    rev = list(reversed(prices))  # 과거→최신 순으로 변환
    k = 2 / (n + 1)
    ema_vals = [sum(rev[:n]) / n]
    for p in rev[n:]:
        ema_vals.append(p * k + ema_vals[-1] * (1 - k))
    # 다시 최신→과거 순으로
    return list(reversed(ema_vals[:len(prices) - n + 1]))

def _rsi(prices: list, n: int = 14) -> float:
    if len(prices) < n + 1:
        return 50.0
    gains  = [max(prices[i] - prices[i+1], 0) for i in range(n)]
    losses = [max(prices[i+1] - prices[i], 0) for i in range(n)]
    ag = sum(gains) / n
    al = sum(losses) / n
    if al == 0:
        return 100.0
    return round(100 - (100 / (1 + ag / al)), 1)

def _atr(prices: list, n: int = 14) -> float:
    if len(prices) < n + 1:
        return 0.0
    return sum(abs(prices[i] - prices[i+1]) for i in range(n)) / n

def _macd(prices: list) -> tuple:
    """(macd_line, signal_line) 반환 — 최신값"""
    fast_ema = _ema(prices, MACD_FAST)
    slow_ema = _ema(prices, MACD_SLOW)
    if not fast_ema or not slow_ema:
        return 0.0, 0.0
    min_len = min(len(fast_ema), len(slow_ema))
    macd_line = [fast_ema[i] - slow_ema[i] for i in range(min_len)]
    signal    = _ema(macd_line, MACD_SIGNAL)
    if not signal:
        return 0.0, 0.0
    return macd_line[0], signal[0]

def _fibonacci_level(high: float, low: float, curr: float) -> float:
    """현재가의 피보나치 되돌림 위치 (0~1)"""
    rng = high - low
    if rng == 0:
        return 0.5
    return (curr - low) / rng


# ══════════════════════════════════════════════════════════════
# 텔레그램 언급 종목 추출
# ══════════════════════════════════════════════════════════════

def _get_tele_stocks() -> dict:
    """
    최근 TELE_HOURS 시간 텔레그램 언급 종목 추출
    반환: {종목명: 누적점수}
    ★ 뉴스 피드 채널(AllStockNews, hankyung_fin 등) 제외
      — 뉴스 헤드라인의 종목 언급은 추천이 아님
    """
    result = {}

    # ★ 실제 정보량이 많은 3채널 기준으로 변경 (2026-06-19)
    #   기존 stocknewskorea/stock0/korea_news11은 telegram_events에 데이터 0건 확인됨
    ALLOWED_CHANNELS = (
        "AllStockNews",       # 여의도 주식 속보 — 상한가/공시/특징주
        "FastStockNews",      # 주식급등일보
        "darthacking",        # 실시간 주식 공시
        "-1001208429502",     # FastStockNews 추정 내부 채널ID (username 미매칭분 흡수)
    )

    if not os.path.exists(DB_PATH_TELEGRAM):
        return result

    try:
        cutoff = (datetime.datetime.now() -
                  datetime.timedelta(hours=TELE_HOURS)).strftime("%Y-%m-%d %H:%M:%S")

        conn   = sqlite3.connect(DB_PATH_TELEGRAM, timeout=5)
        cursor = conn.cursor()

        # 최근 메시지 수집 — 허용 채널만
        placeholders = ",".join("?" * len(ALLOWED_CHANNELS))
        cursor.execute(f"""
            SELECT message, score, created_at
            FROM telegram_events
            WHERE created_at >= ?
              AND channel IN ({placeholders})
            ORDER BY id DESC
            LIMIT 200
        """, (cutoff, *ALLOWED_CHANNELS))
        rows = cursor.fetchall()
        conn.close()

        if not rows:
            print("   [텔레스윙] 허용 채널 메시지 없음 (뉴스피드 채널 제외됨)")
            return result

        # kr_stock_daily_data 전 종목명 로드
        fin_conn   = sqlite3.connect(DB_PATH, timeout=5)
        fin_cursor = fin_conn.cursor()
        fin_cursor.execute("SELECT DISTINCT stock_name FROM kr_stock_daily_data")
        all_stocks = fin_cursor.fetchall()
        fin_conn.close()

        # 종목명 정제
        stock_names = {}
        for (sname,) in all_stocks:
            pure = re.sub(r'\s*(KOSPI|KOSDAQ)\s*\d{6}$', '', sname).strip()
            if pure and len(pure) >= 2:
                stock_names[pure] = sname

        # 최근 3일 컷오프
        cutoff_fresh = (datetime.datetime.now() -
                       datetime.timedelta(hours=72)).strftime("%Y-%m-%d %H:%M:%S")

        # 메시지에서 종목명 매칭 (신선도 가중치 적용)
        # ★ 2026-06-29 수정: cutoff_fresh가 정의만 되고 실제로 쓰이지
        #   않아서, "3일 내 언급만 ×2"라는 의도와 다르게 360시간(15일)
        #   이내 모든 메시지에 무조건 ×2가 적용되던 버그. 이제 메시지의
        #   created_at을 직접 비교해서 3일 이내인 것만 ×2, 그 외(4~15일
        #   전)는 가중치 없이(×1) 반영.
        combined = " ".join(r[0] for r in rows if r[0])
        for pure, db_name in stock_names.items():
            if pure in combined:
                fresh_score = sum(
                    (r[1] or 10) * (2 if (len(r) > 2 and r[2] and r[2] >= cutoff_fresh) else 1)
                    for r in rows
                    if r[0] and pure in r[0]
                )
                score = min(fresh_score, 100)
                # 최소 30점 이상만 후보 포함
                if score >= 30:
                    result[pure] = score

    except Exception as e:
        print(f"⚠️ [텔레스윙] 텔레그램 조회 오류: {e}")

    return result


# ══════════════════════════════════════════════════════════════
# 생쇼 종목 조회
# ══════════════════════════════════════════════════════════════

def _get_sshow_stocks() -> dict:
    """생쇼 DB에서 최근 5영업일 종목 반환"""
    try:
        from sshow_db import get_sshow_stocks
        return get_sshow_stocks(days=7)
    except Exception as e:
        print(f"⚠️ [텔레스윙] 생쇼 조회 오류: {e}")
        return {}


def _get_sshow_bonus_score() -> int:
    """
    생쇼(전문가4인) 추천 가산점을 최근 30일 적중률 기반으로 산출.
    (★ 2026-06-30 추가 — 기존엔 고정 +8점이었으나, 결과추적 데이터가
    쌓이면서 실제 성과에 따라 가변 적용하도록 변경)
    - 표본 20건 미만(sample_size_ok=False) → 고정 +8 (기존과 동일,
      통계가 신뢰할 만큼 안 쌓였을 때는 보수적으로 유지)
    - 적중률 60% 이상 → +10
    - 적중률 40~60%  → +8 (기존과 동일)
    - 적중률 40% 미만 → +5
    """
    try:
        from sshow_db import get_sshow_stats
        stats = get_sshow_stats(days=30)
    except Exception as e:
        print(f"⚠️ [텔레스윙] 생쇼 통계 조회 오류: {e}")
        return 8  # 통계 조회 실패 시 기존 고정값으로 안전하게 폴백

    if not stats.get("sample_size_ok", False):
        return 8

    hit_rate = stats.get("hit_rate", 0.0)
    if hit_rate >= 0.6:
        return 10
    elif hit_rate >= 0.4:
        return 8
    else:
        return 5


# ══════════════════════════════════════════════════════════════
# 정통 스윙 점수 계산
# ══════════════════════════════════════════════════════════════

def _calc_swing_score(stock_name: str, tele_score: int, is_sshow: bool = False,
                       sshow_bonus: int = 8) -> dict:
    """
    정통 스윙 트레이딩 원칙 기반 점수 산출
    """
    result = {
        "name":        stock_name,
        "score":       0,
        "tele_score":  tele_score,
        "curr_price":  0,
        "stop_price":  0,
        "tgt_price":   0,
        "rr_ratio":    0,
        "rsi":         0,
        "macd_cross":  False,
        "fib_level":   0,
        "score_detail": {},
    }

    try:
        conn   = sqlite3.connect(DB_PATH, timeout=5)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT date, close_price, volume
            FROM kr_stock_daily_data
            WHERE stock_name LIKE ?
            ORDER BY date DESC
            LIMIT 220
        """, (f"%{stock_name}%",))
        rows = cursor.fetchall()
        conn.close()

        if len(rows) < 30:
            return result

        closes  = [r[1] for r in rows if r[1] and r[1] > 0]
        volumes = [r[2] for r in rows if r[2] and r[2] > 0]

        if not closes:
            return result

        curr    = closes[0]
        score   = 0
        detail  = {}

        # ── 1. 주봉 추세 정배열 (+20점) ──────────────────────
        # 주봉 근사: 25일선(5주) > 125일선(25주)
        ma25  = _ma(closes, 25)
        ma125 = _ma(closes, 125)
        if ma25 > 0 and ma125 > 0 and ma25 > ma125 and curr > ma25:
            score += 20
            detail["주봉정배열"] = "+20"
        elif ma25 > 0 and curr > ma25:
            score += 10
            detail["주봉정배열"] = "+10 (부분)"

        # ── 2. 200일선 위 (+15점) ────────────────────────────
        ma200 = _ma(closes, 200)
        if ma200 > 0 and curr > ma200:
            score += 15
            detail["200일선위"] = "+15"

        # ── 3. MACD 골든크로스 (+15점) ───────────────────────
        macd_line, signal_line = _macd(closes)
        if macd_line > signal_line > 0:
            score += 15
            detail["MACD골든"] = "+15 (강)"
        elif macd_line > signal_line:
            score += 10
            detail["MACD골든"] = "+10 (약)"
        result["macd_cross"] = macd_line > signal_line

        # ── 4. RSI 눌림목 (+15점) ────────────────────────────
        rsi = _rsi(closes)
        result["rsi"] = rsi
        if 30 <= rsi <= 45:
            score += 15
            detail["RSI눌림"] = f"+15 (RSI:{rsi} 최적눌림)"
        elif 45 < rsi <= 55:
            score += 10
            detail["RSI눌림"] = f"+10 (RSI:{rsi} 건전)"
        elif 55 < rsi <= 65:
            score += 5
            detail["RSI눌림"] = f"+5 (RSI:{rsi} 주의)"
        elif rsi < 30:
            score += 5
            detail["RSI눌림"] = f"+5 (RSI:{rsi} 과매도)"
        # 70 초과는 점수 없음 (뒤에서 감점)

        # ── 5. 피보나치 되돌림 구간 (+15점) ──────────────────
        # 최근 60일 고점/저점 기준
        recent = closes[:60] if len(closes) >= 60 else closes
        hi60   = max(recent)
        lo60   = min(recent)
        fib    = _fibonacci_level(hi60, lo60, curr)
        result["fib_level"] = round(fib, 3)

        if 0.382 <= fib <= 0.618:
            score += 15
            detail["피보나치"] = f"+15 ({fib:.1%} 황금구간)"
        elif 0.236 <= fib < 0.382:
            score += 8
            detail["피보나치"] = f"+8 ({fib:.1%})"

        # ── 6. 거래량 수반 (+10점) ────────────────────────────
        if len(volumes) >= 10:
            vol_avg = sum(volumes[1:21]) / min(20, len(volumes)-1)
            vol_now = volumes[0]
            if vol_avg > 0 and vol_now >= vol_avg * 1.5:
                score += 10
                detail["거래량수반"] = "+10 (거래량 급증)"
            elif vol_avg > 0 and vol_now >= vol_avg * 1.0:
                score += 5
                detail["거래량수반"] = "+5 (거래량 보통)"

        # ── 7. 텔레그램 언급 (+10점) ─────────────────────────
        # 원점수 구간별 차등 — 언급 강도 반영
        if tele_score >= 90:
            tele_pts = 10
        elif tele_score >= 70:
            tele_pts = 9
        elif tele_score >= 50:
            tele_pts = 7
        elif tele_score >= 30:
            tele_pts = 4
        else:
            tele_pts = 0
        score += tele_pts
        detail["텔레그램"] = f"+{tele_pts} (원점수:{tele_score})"

        # ── 8. 생쇼 추천 (적중률 기반 가변 가산점) ──────────────
        if is_sshow:
            score += sshow_bonus
            detail["생쇼추천"] = f"+{sshow_bonus} (전문가추천, 30일적중률 기반)"

        # ── ATR 손절/목표 ─────────────────────────────────────
        atr        = _atr(closes)
        stop_price = round(curr - atr * ATR_STOP_MULT, 0)
        tgt_price  = round(curr + atr * ATR_TARGET_MULT, 0)
        stop_pct   = round((curr - stop_price) / curr * 100, 1) if curr > 0 else 0
        tgt_pct    = round((tgt_price - curr) / curr * 100, 1) if curr > 0 else 0
        rr_ratio   = round(tgt_pct / stop_pct, 1) if stop_pct > 0 else 0

        # ── 방어막 ────────────────────────────────────────────
        # 손절가 음수 or 손절폭 20% 초과 → 비정상 ATR → 스킵
        if stop_price <= 0 or stop_pct > 20:
            return result

        # ★ 거래대금 필터 — 최근 5일 평균 거래대금 50억 미달 제외 (잡주 방지)
        if len(volumes) >= 5:
            _vol_avg_recent = sum(volumes[:5]) / 5
            _recent_trading_value = (curr * _vol_avg_recent) / 100_000_000  # 억원
            if _recent_trading_value < MIN_TRADING_VALUE_EOK:
                return result

        # 목표가 최소 +8% 이상
        if tgt_pct < 8.0:
            return result

        # 200일선 위 확인 (데이터 부족시 통과)
        ma200_check = _ma(closes, 200)
        if ma200_check > 0 and curr < ma200_check:
            return result

        # 피보나치 고점권(0.7 이상) 감점
        if fib > 0.7:
            score -= 10
            detail["피보나치고점감점"] = "-10 (고점권 진입 위험)"

        # RSI 과매수(70 이상) 감점
        if rsi > 70:
            score -= 10
            detail["RSI과매수감점"] = f"-10 (RSI:{rsi} 과매수)"

        result.update({
            "score":        max(0, score),
            "curr_price":   curr,
            "stop_price":   stop_price,
            "tgt_price":    tgt_price,
            "stop_pct":     stop_pct,
            "tgt_pct":      tgt_pct,
            "rr_ratio":     rr_ratio,
            "score_detail": detail,
        })

    except Exception as e:
        print(f"⚠️ [텔레스윙] 점수 계산 오류 {stock_name}: {e}")

    return result


# ══════════════════════════════════════════════════════════════
# 메인 함수
# ══════════════════════════════════════════════════════════════

def get_tele_swing_picks(top_n: int = TOP_N, min_score: int = MIN_SCORE) -> list:
    """
    텔레그램 언급 종목 기반 정통 스윙 분석
    반환: dict 리스트 (sbo2 직접 연동용)
    [
        {
            "name":       "한미반도체",
            "score":      75,
            "curr_price": 85000,
            "stop_price": 78000,
            "tgt_price":  98000,
            "rr_ratio":   1.9,
            "rsi":        42.3,
            "macd_cross": True,
            "fib_level":  0.48,
            "tele_score": 45,
            "score_detail": {...},
        }
    ]
    """
    print("\n📡 [텔레스윙] 텔레그램 언급 종목 스캔 중...")

    # 1. 텔레그램 언급 종목 추출
    tele_stocks = _get_tele_stocks()
    if not tele_stocks:
        print("   ⚠️ 텔레그램 언급 종목 없음")
        return []

    print(f"   텔레그램 언급 종목: {len(tele_stocks)}개")

    # 1-2. 생쇼 종목 — 텔레그램 언급 종목에만 가산점 부여
    # ★ 생쇼 단독 종목은 후보 제외 — 텔레그램 언급이 있어야 의미있음
    sshow_stocks = _get_sshow_stocks()
    sshow_names  = set(sshow_stocks.keys()) & set(tele_stocks.keys())  # 교집합만
    print(f"   생쇼 교집합: {len(sshow_names)}개 (텔레그램+생쇼 동시 언급)")

    # ★ 2026-06-30: 생쇼 가산점을 적중률 기반으로 — 종목마다 매번 통계를
    #   다시 조회하면 비효율이므로 루프 시작 전 한 번만 조회
    sshow_bonus = _get_sshow_bonus_score()
    if sshow_names:
        print(f"   생쇼 가산점: +{sshow_bonus} (30일 적중률 기반)")

    # 2. 정통 스윙 점수 계산
    results = []
    for name, tele_score in tele_stocks.items():
        is_sshow = name in sshow_names
        data = _calc_swing_score(name, tele_score, is_sshow=is_sshow,
                                  sshow_bonus=sshow_bonus)
        if data["curr_price"] > 0 and data["rr_ratio"] >= 1.5:
            results.append(data)

    # 3. 점수 필터링 & 정렬
    passed = [r for r in results if r["score"] >= min_score]
    passed.sort(key=lambda x: x["score"], reverse=True)

    top = passed[:top_n]

    if top:
        print(f"   통과: {len(passed)}종목 → 상위 {len(top)}개 선정")
    else:
        print(f"   ⚠️ {min_score}점 이상 종목 없음 (전체 {len(results)}개 분석)")

    return top


def get_tele_swing_report(top_n: int = TOP_N) -> str:
    """
    텔레그램 스윙 리포트 (디스코드 출력용)
    """
    picks = get_tele_swing_picks(top_n=top_n)

    if not picks:
        return (
            f"📡 **[텔레그램 스윙 엔진]**\n"
            f"   현재 {MIN_SCORE}점 이상 후보 없어.\n"
            f"   텔레그램 언급 + 정통 스윙 조건 동시 충족 종목 대기 중!"
        )

    report  = "📡 **[텔레그램 정통 스윙 엔진 — 실시간 모멘텀 × 기술적 분석]** 📡\n"
    report += "=" * 60 + "\n"

    for idx, item in enumerate(picks, 1):
        # ── 1. 기존 상세 정보 문자열 및 MACD 조립 ──
        detail_str = " | ".join(
            f"{k}:{v}" for k, v in item.get("score_detail", {}).items()
        )
        macd_tag = "✅ 골든크로스" if item.get("macd_cross") else "❌ 미형성"

        # ── 🎯 2. [복구] 원래 위에서 정교하게 계산해준 종목별 고유 ATR 타점 사용 ──
        curr_price = item['curr_price']
        
        # 원래 로직이 만들어준 타점의 소수점만 깔끔하게 제거!
        tgt_price = int(item['tgt_price'])
        stop_price = int(item['stop_price'])
        
        # 실제 손익비(R:R) 퍼센트 재계산 (리포트 출력용, 소수점 1자리)
        tgt_pct = round((tgt_price - curr_price) / curr_price * 100, 1)
        stop_pct = round((curr_price - stop_price) / curr_price * 100, 1)

        # ── 3. 지표 태그 생성 ──
        # ★ 2026-06-29 수정: R:R이 "1:2.0 우량"으로 하드코딩되어 있어서
        #   실제 rr_ratio(이미 정확히 계산되어 있던 값)와 무관하게 항상
        #   같은 텍스트가 찍히던 버그. 실제 값을 쓰고 swing_analyzer.py와
        #   동일한 등급 기준(2.5+우량/1.5+보통/그 외 불량)으로 통일.
        rr_ratio = item.get("rr_ratio", 0)
        if rr_ratio >= 2.5:
            rr_tag = "✅ 우량"
        elif rr_ratio >= 1.5:
            rr_tag = "🔸 보통"
        else:
            rr_tag = "❌ 불량"

        rsi_val  = item.get("rsi", 50)
        if rsi_val >= 70:
            rsi_tag = f"{rsi_val} ⚠️ 과매수"
        elif rsi_val >= 55:
            rsi_tag = f"{rsi_val} 주의"
        elif rsi_val >= 30:
            rsi_tag = f"{rsi_val} ✅ 눌림목"
        else:
            rsi_tag = f"{rsi_val} 과매도"

        fib_val = item.get("fib_level", 0) * 100
        if fib_val >= 70:
            fib_tag = f"{fib_val:.1f}% ⚠️ 고점권"
        elif 38.2 <= fib_val <= 61.8:
            fib_tag = f"{fib_val:.1f}% ✅ 황금구간"
        else:
            fib_tag = f"{fib_val:.1f}%"

        # ── 4. 최종 리포트 텍스트 조립 ──
        report += (
            f"\n 📌 **{idx}위: {item['name']}** (총점: {item['score']}점)\n"
            f"    📡 텔레그램  : 원점수 {item.get('tele_score', 0)}점\n"
            f"    💰 현재가    : {curr_price:,}원\n"
            f"    🎯 목표가    : {tgt_price:,}원  (+{tgt_pct}%)\n"
            f"    🛑 손절가    : {stop_price:,}원  (-{stop_pct}%)\n"
            f"    ⚖️  R:R       : 1 : {rr_ratio}  {rr_tag}\n"
            f"    📉 RSI       : {rsi_tag}\n"
            f"    📊 MACD      : {macd_tag}\n"
            f"    🌀 피보나치  : {fib_tag}\n"
            f"    📈 점수 상세 : {detail_str}\n"
            f"------------------------------------------------------------"
        )

    report += f"\n\n   ⚙️ 최소점수 {MIN_SCORE}점 / ATR 변동성 기반 고유 타점 계산\n"
    return report


if __name__ == "__main__":
    print(get_tele_swing_report())
