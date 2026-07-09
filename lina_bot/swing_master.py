"""
swing_master.py
─────────────────────────────────────────────────────────────
대장 전용 S/A/B 등급 통합 마스터 리포트

3개 엔진 교집합:
 1번 — 촉매 확인  (미장 급등 섹터 OR 텔레그램 핫 키워드)
 2번 — VCP 스윙  (횡보 수렴 + 거래량 마름 + 스마트머니)
 3번 — 상승추세  (HH/HL 파동 + RSI 눌림 + 60일선 우상향)

등급:
 🥇 S급 — 3개 교집합  → 풀베팅 감
 🥈 A급 — 2개 교집합  → 절반 베팅 감
 🥉 B급 — 1개만       → 관망 / 소량

호출:
    from swing_master import get_master_report
    report = get_master_report(top_n=5)
"""

import sqlite3
import os
import re

import datetime
import yfinance as yf

BASE_DIR         = os.path.dirname(os.path.abspath(__file__))
DB_PATH          = os.path.join(BASE_DIR, "kr_theme_finance.db")
DB_PATH_MAPPING  = os.path.join(BASE_DIR, "us_kr_mapping.db")
DB_PATH_TELEGRAM = os.path.join(BASE_DIR, "intelligence", "telegram_events.db")
# sector_monitor DB — 여러 경로 중 존재하는 것 사용
_sector_candidates = [
    os.path.join(BASE_DIR, "..", "intelligence", "sector_monitor.db"),
    os.path.join(BASE_DIR, "intelligence", "sector_monitor.db"),
    os.path.join(BASE_DIR, "..", "data", "sector_monitor.db"),
    "/home/free4tak/k-bot/stock_bot/intelligence/sector_monitor.db",
]
DB_PATH_SECTOR = next((p for p in _sector_candidates if os.path.exists(p)),
                      _sector_candidates[0])

TOP_N_DEFAULT    = 5


# ── 임포트 (같은 폴더) ────────────────────────────────────────
from swing_analyzer import get_swing_picks
from trend_analyzer import get_trend_picks


# ══════════════════════════════════════════════════════════════
# 촉매 확인 (1번 엔진) - DB 연동 동적 스캔 버전
# ══════════════════════════════════════════════════════════════

CATALYST_CACHE_FILE = os.path.join(BASE_DIR, "catalyst_cache.json")
CATALYST_CACHE_TTL_SEC = 1800   # 30분 — 이 안에는 캐시 재사용

def _get_catalyst_stocks() -> set:
    """
    us_kr_mapping.db에서 미장 티커를 동적으로 불러와 급등(+3% 이상) 스캔
    → 한국 수혜 종목명 set 반환
    + 텔레그램 최근 50건 언급 종목 추가

    ★ 2026-06-29 캐시 추가: swing_master.get_master_report()(07:20 브리핑)와
    sbo2.get_candidates()(후보 갱신)가 각자 독립적으로 이 함수를 호출해서
    yfinance API를 미장 티커 수만큼(약 45개) 매번 중복 조회하던 문제가 있었음.
    - 두 호출 사이 시간차로 결과가 달라질 수 있어 사용자가 브리핑에서 본
      "촉매 종목"과 sbo2가 실제로 쓰는 종목이 다를 수 있었음
    - 원래 설계 의도("브리핑이 계산하면 sbo2가 재사용")가 실제로는
      구현되어 있지 않았음
    파일 캐시(30분 TTL)로 양쪽이 같은 결과를 공유하도록 수정.
    """
    import json as _json
    import time as _time

    try:
        if os.path.exists(CATALYST_CACHE_FILE):
            with open(CATALYST_CACHE_FILE, "r", encoding="utf-8") as f:
                cached = _json.load(f)
            age = _time.time() - cached.get("ts", 0)
            if age < CATALYST_CACHE_TTL_SEC:
                print(f"   ♻️ catalyst_set 캐시 재사용 ({int(age)}초 전, "
                      f"{len(cached.get('stocks', []))}개)")
                return set(cached.get("stocks", []))
    except Exception as e:
        print(f"⚠️ catalyst 캐시 읽기 오류: {e}")

    hot_kr = _get_catalyst_stocks_fresh()

    try:
        with open(CATALYST_CACHE_FILE, "w", encoding="utf-8") as f:
            _json.dump({"ts": _time.time(), "stocks": sorted(hot_kr)}, f, ensure_ascii=False)
    except Exception as e:
        print(f"⚠️ catalyst 캐시 저장 오류: {e}")

    return hot_kr


def _get_catalyst_stocks_fresh() -> set:
    """
    실제 yfinance/텔레그램 스캔 수행 (캐시 미사용 — _get_catalyst_stocks()의
    내부 구현. 강제로 새로 스캔하고 싶을 때는 이 함수를 직접 호출 가능)
    """
    hot_kr = set()

    # ── 1. 미장 동적 스캔 (DB 연동) ──────────────────────────────
    if os.path.exists(DB_PATH_MAPPING):
        map_conn   = sqlite3.connect(DB_PATH_MAPPING)
        map_cursor = map_conn.cursor()

        # DB에서 감시할 미장 티커 목록을 중복 없이 모두 가져오기
        map_cursor.execute("SELECT DISTINCT us_ticker FROM us_kr_mapping")
        watchlist = [row[0] for row in map_cursor.fetchall()]

        print(f"🇺🇸 미장 티커 {len(watchlist)}개 동적 스캔 중...")

        for ticker in watchlist:
            try:
                hist = yf.Ticker(ticker).history(period="2d")
                if len(hist) < 2:
                    continue
                
                # 3% 이상 급등 시에만 촉매로 인정 (대장님 세팅 유지)
                chg = (hist['Close'].iloc[1] - hist['Close'].iloc[0]) / hist['Close'].iloc[0] * 100
                if chg >= 3.0:
                    map_cursor.execute(
                        "SELECT kr_name FROM us_kr_mapping WHERE us_ticker = ?", (ticker,)
                    )
                    for row in map_cursor.fetchall():
                        hot_kr.add(row[0])
            except Exception:
                pass

        map_conn.close()

    # ── 2. 텔레그램 스캔 ─────────────────────────────────────────
    if os.path.exists(DB_PATH_TELEGRAM):
        try:
            tele_conn   = sqlite3.connect(DB_PATH_TELEGRAM)
            tele_cursor = tele_conn.cursor()
            tele_cursor.execute(
                "SELECT message FROM telegram_events ORDER BY id DESC LIMIT 50")
            combined = " ".join(r[0] for r in tele_cursor.fetchall() if r[0])
            tele_conn.close()

            # DB의 전 종목명과 매칭
            fin_conn   = sqlite3.connect(DB_PATH)
            fin_cursor = fin_conn.cursor()
            fin_cursor.execute(
                "SELECT DISTINCT stock_name FROM kr_stock_daily_data")
            for (sname,) in fin_cursor.fetchall():
                pure = re.sub(r'\s*(KOSPI|KOSDAQ)\s*\d{6}$', '', sname).strip()
                if pure and pure in combined:
                    hot_kr.add(pure)
            fin_conn.close()
        except Exception:
            pass

    # ★ 2026-07-07: 고정 왓치리스트 강제 주입 제거 (사용자 결정) —
    #   왓치리스트 151개가 미장/텔레그램/섹터 상황과 무관하게 항상
    #   "촉매 통과"로 잡혀서, 교집합(VCP+추세+촉매)의 촉매 조건이 왓치
    #   종목에겐 사실상 죽은 필터가 되고 있었음(190개 중 151개가 이
    #   소스 하나). "촉매"는 실제 오늘 벌어진 일(미장 급등/텔레그램
    #   언급/섹터 급등)만으로 판단하도록 되돌림.

    # ── 4. sector_monitor 실시간 급등 테마 연동 ───────────────
    if os.path.exists(DB_PATH_SECTOR):
        try:
            sec_conn   = sqlite3.connect(DB_PATH_SECTOR, timeout=5)
            sec_cursor = sec_conn.cursor()

            # 오늘 장중 데이터 전체 사용 (09:00 이후)
            cutoff = datetime.datetime.now().strftime("%Y-%m-%d") + " 09:00"
            sec_cursor.execute("""
                SELECT DISTINCT theme_nm, MAX(flu_rt) as max_flu
                FROM sector_flow
                WHERE ts >= ? AND flu_rt >= 7.0
                GROUP BY theme_nm
                ORDER BY max_flu DESC
                LIMIT 5
            """, (cutoff,))
            hot_themes = sec_cursor.fetchall()
            sec_conn.close()

            if hot_themes:
                print(f"   🔥 sector 급등 테마: {[t[0] for t in hot_themes[:3]]}")

            # 테마명 키워드 매칭 → kr_stock_daily_data 종목 추가
            fin_conn   = sqlite3.connect(DB_PATH)
            fin_cursor = fin_conn.cursor()
            fin_cursor.execute("SELECT DISTINCT stock_name FROM kr_stock_daily_data")
            all_stocks = fin_cursor.fetchall()

            # kr_theme_stocks 테마 매핑
            fin_cursor.execute("SELECT stock_name, theme_name FROM kr_theme_stocks")
            theme_rows = fin_cursor.fetchall()
            fin_conn.close()

            # 종목 → 테마 맵
            stock_theme_map = {}
            for sname, tname in theme_rows:
                pure = re.sub(r'\s*(KOSPI|KOSDAQ)\s*\d{6}$', '', sname).strip()
                stock_theme_map.setdefault(pure, [])
                stock_theme_map[pure].append(tname)

            for sec_theme, flu_rt in hot_themes:
                # 언더바 앞 키워드 추출 (예: "반도체_후공정장비" → "반도체")
                keywords = [k.strip() for k in sec_theme.replace('_', ' ').split()]

                for pure, themes in stock_theme_map.items():
                    for t in themes:
                        if any(kw in t for kw in keywords):
                            hot_kr.add(pure)
                            break

        except Exception as e:
            print(f"⚠️ sector_monitor 연동 오류: {e}")

    return hot_kr


def _get_us_market_movers() -> list:
    """
    ★ 2026-07-09 추가 — AI 모멘텀 스캐너(아침 세션)용.
    _get_catalyst_stocks_fresh()의 미장 스캔 루프를 재사용하되, 3% 임계치
    필터링 없이 전체 등락률 + 매핑된 한국 수혜종목을 그대로 반환한다.
    (촉매 계산 로직 자체는 건드리지 않음 — 이 함수는 별도 순수 조회.)

    반환: [(us_ticker, change_pct, [kr_name, ...]), ...] 등락률 내림차순
    """
    movers = []
    if not os.path.exists(DB_PATH_MAPPING):
        return movers

    map_conn   = sqlite3.connect(DB_PATH_MAPPING)
    map_cursor = map_conn.cursor()
    map_cursor.execute("SELECT DISTINCT us_ticker FROM us_kr_mapping")
    watchlist = [row[0] for row in map_cursor.fetchall()]

    for ticker in watchlist:
        try:
            hist = yf.Ticker(ticker).history(period="2d")
            if len(hist) < 2:
                continue
            chg = (hist['Close'].iloc[1] - hist['Close'].iloc[0]) / hist['Close'].iloc[0] * 100
            map_cursor.execute(
                "SELECT kr_name FROM us_kr_mapping WHERE us_ticker = ?", (ticker,)
            )
            kr_names = [row[0] for row in map_cursor.fetchall()]
            movers.append((ticker, round(float(chg), 2), kr_names))
        except Exception:
            pass

    map_conn.close()
    movers.sort(key=lambda x: x[1], reverse=True)
    return movers


# ══════════════════════════════════════════════════════════════
# 종목명 추출 헬퍼
# ══════════════════════════════════════════════════════════════

def _extract_names_from_report(report: str) -> set:
    """
    swing/trend 리포트 텍스트에서 '위: 종목명' 패턴으로 종목명 추출
    """
    names = set()
    for line in report.splitlines():
        m = re.search(r'\*?\*?\d+위:\s*\*?\*?(.+?)\*?\*?\s*\(스코어', line)
        if m:
            names.add(m.group(1).strip())
    return names


# ══════════════════════════════════════════════════════════════
# 메인
# ══════════════════════════════════════════════════════════════

def get_master_report(top_n: int = TOP_N_DEFAULT) -> str:

    # ── 3개 엔진 실행 ─────────────────────────────────────────
    print("⚙️  [마스터] 1번 촉매 스캔 중...")
    catalyst_set = _get_catalyst_stocks()

    print("⚙️  [마스터] 2번 VCP 스윙 엔진 실행 중...")
    from swing_analyzer import get_swing_data
    swing_list  = get_swing_data(top_n=20)
    swing_names = {d["name"] for d in swing_list}

    print("⚙️  [마스터] 3번 상승추세 엔진 실행 중...")
    from trend_analyzer import get_trend_data
    trend_list  = get_trend_data(top_n=20)
    trend_names = {d["name"] for d in trend_list}

    print(f"   촉매 종목: {len(catalyst_set)}개")
    print(f"   VCP 통과: {len(swing_names)}개")
    print(f"   추세 통과: {len(trend_names)}개")

    # ── 교집합 계산 & 등급 부여 ───────────────────────────────
    s_grade = swing_names & trend_names & catalyst_set        # 3개
    a_grade = (
        ((swing_names & trend_names)  - catalyst_set) |   # 추세+VCP
        ((swing_names & catalyst_set) - trend_names)  |   # VCP+촉매
        ((trend_names & catalyst_set) - swing_names)       # 추세+촉매
    )
    b_grade = (
        (swing_names | trend_names | catalyst_set)
        - s_grade - a_grade
    )

    # ── 결과 없으면 안내 ──────────────────────────────────────
    total_hits = len(s_grade) + len(a_grade)
    if total_hits == 0:
        return (
            "💡 **[마스터 리포트]** 오늘은 A급 이상 교집합 종목이 없어.\n\n"
            f"   VCP 통과    : {len(swing_names)}개\n"
            f"   추세 통과   : {len(trend_names)}개\n"
            f"   촉매 감지   : {len(catalyst_set)}개\n\n"
            "   → B급 단독 종목은 `!스윙` / `!추세` 로 따로 확인해봐."
        )

    # ── 리포트 빌드 ──────────────────────────────────────────
    report  = "🏆 **[마스터 리포트 — S/A/B 등급 교집합 분석]** 🏆\n"
    report += f"   VCP {len(swing_names)}개 × 추세 {len(trend_names)}개 × 촉매 {len(catalyst_set)}개 교집합\n"
    report += "=" * 60 + "\n"

    # S급
    if s_grade:
        report += f"\n🥇 **S급 — 3개 교집합 [{len(s_grade)}종목] → 풀베팅 감!**\n"
        report += "   촉매 ✅  VCP타점 ✅  상승추세 ✅\n"
        report += "-" * 40 + "\n"
        for name in sorted(s_grade)[:top_n]:
            report += f"   🔥 **{name}**\n"

    # A급
    if a_grade:
        report += f"\n🥈 **A급 — 2개 교집합 [{len(a_grade)}종목] → 절반 베팅 감**\n"
        report += "-" * 40 + "\n"

        # 어떤 2개 조합인지 태그
        for name in sorted(a_grade)[:top_n]:
            tags = []
            if name in catalyst_set: tags.append("촉매✅")
            if name in swing_names:  tags.append("VCP✅")
            if name in trend_names:  tags.append("추세✅")
            missing = []
            if name not in catalyst_set: missing.append("촉매❌")
            if name not in swing_names:  missing.append("VCP❌")
            if name not in trend_names:  missing.append("추세❌")
            report += f"   ⚡ **{name}**  {' '.join(tags)}  {' '.join(missing)}\n"

    # B급 (상위 5개만)
    b_show = sorted(b_grade)[:5]
    if b_show:
        report += f"\n🥉 **B급 — 1개만 [{len(b_grade)}종목] → 관망 권장**\n"
        report += "-" * 40 + "\n"
        for name in b_show:
            tag = "촉매" if name in catalyst_set else ("VCP" if name in swing_names else "추세")
            report += f"   🔸 {name}  ({tag}만 해당)\n"
        if len(b_grade) > 5:
            report += f"   ... 외 {len(b_grade)-5}개\n"

    report += "\n" + "=" * 60 + "\n"
    report += "   💡 S급부터 공략 → A급은 조합 보고 판단 → B급은 관망\n\n"

    # ── 각 엔진 Top2 ─────────────────────────────────────────
    # ★ 2026-06-29 수정: swing_names/trend_names는 점수 정보가 없는 set이라
    #   sorted()를 해도 가나다순일 뿐 점수 상위가 아니었음 (실제로는
    #   "탑픽"이라는 라벨과 다르게 동작하던 버그). swing_list/trend_list는
    #   get_swing_data()/get_trend_data() 단계에서 이미 점수 내림차순으로
    #   정렬되어 있으므로, 그 순서 그대로 앞 2개를 뽑으면 진짜 점수 상위가 됨.
    swing_top2 = [d["name"] for d in swing_list[:2]]
    if swing_top2:
        report += f"   🔻 VCP 탑픽   : {' / '.join(swing_top2)}\n"

    trend_top2 = [d["name"] for d in trend_list[:2]]
    if trend_top2:
        report += f"   📈 추세 탑픽   : {' / '.join(trend_top2)}\n"

    hot_overlap = (swing_names | trend_names) & catalyst_set - s_grade - a_grade
    if hot_overlap:
        report += f"   🔥 촉매 관심주 : {' / '.join(sorted(hot_overlap)[:3])}\n"

    report += "   📌 `!스윙` / `!추세` 로 상세 데이터 확인 가능\n"

    return report


if __name__ == "__main__":
    print(get_master_report(top_n=5))
