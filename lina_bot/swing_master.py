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
DB_PATH_SECTOR   = os.path.join(BASE_DIR, "..", "intelligence", "sector_monitor.db")

TOP_N_DEFAULT    = 5


# ── 임포트 (같은 폴더) ────────────────────────────────────────
from swing_analyzer import get_swing_picks
from trend_analyzer import get_trend_picks


# ══════════════════════════════════════════════════════════════
# 촉매 확인 (1번 엔진) - DB 연동 동적 스캔 버전
# ══════════════════════════════════════════════════════════════

def _get_catalyst_stocks() -> set:
    """
    us_kr_mapping.db에서 미장 티커를 동적으로 불러와 급등(+3% 이상) 스캔
    → 한국 수혜 종목명 set 반환
    + 텔레그램 최근 50건 언급 종목 추가
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

    # ── 3. 대장님 전용 왓치리스트 강제 주입 ──────────────────────
    from watchlist_manager import get_watchlist_stocks
    hot_kr |= get_watchlist_stocks(priority=1)

    # ── 4. sector_monitor 실시간 급등 테마 연동 ───────────────
    if os.path.exists(DB_PATH_SECTOR):
        try:
            sec_conn   = sqlite3.connect(DB_PATH_SECTOR, timeout=5)
            sec_cursor = sec_conn.cursor()

            # 오늘 + 최근 2시간 내 급등 테마 (등락률 +5% 이상)
            cutoff = (datetime.datetime.now() -
                      datetime.timedelta(hours=2)).strftime("%Y-%m-%d %H:%M")
            sec_cursor.execute("""
                SELECT DISTINCT theme_nm, MAX(flu_rt) as max_flu
                FROM sector_flow
                WHERE ts >= ? AND flu_rt >= 5.0
                GROUP BY theme_nm
                ORDER BY max_flu DESC
                LIMIT 10
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
    swing_report = get_swing_picks(top_n=20)   # 넉넉하게 20개
    swing_names  = _extract_names_from_report(swing_report)

    print("⚙️  [마스터] 3번 상승추세 엔진 실행 중...")
    trend_report = get_trend_picks(top_n=20)
    trend_names  = _extract_names_from_report(trend_report)

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
    swing_top2 = sorted(swing_names)[:2]
    if swing_top2:
        report += f"   🔻 VCP 탑픽   : {' / '.join(swing_top2)}\n"

    trend_top2 = sorted(trend_names)[:2]
    if trend_top2:
        report += f"   📈 추세 탑픽   : {' / '.join(trend_top2)}\n"

    hot_overlap = (swing_names | trend_names) & catalyst_set - s_grade - a_grade
    if hot_overlap:
        report += f"   🔥 촉매 관심주 : {' / '.join(sorted(hot_overlap)[:3])}\n"

    report += "   📌 `!스윙` / `!추세` 로 상세 데이터 확인 가능\n"

    return report


if __name__ == "__main__":
    print(get_master_report(top_n=5))
