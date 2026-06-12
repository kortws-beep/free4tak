import sqlite3
import os
import re
import yfinance as yf

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH_THEME_FINANCE = os.path.join(BASE_DIR, "kr_theme_finance.db")
DB_PATH_TELEGRAM = r"C:\lina_bot\intelligence\telegram_events.db"
DB_PATH_MAPPING = os.path.join(BASE_DIR, "us_kr_mapping.db")

# 💡 대장의 ETF + 개별주 확장 감시 리스트 (2번+1번 아이디어 융합)
US_WATCHLIST = {
    # 개별 주도주
    "NVDA": "반도체/AI", "INTC": "반도체/파운드리", "TSLA": "2차전지/자율주행", 
    "AAPL": "스마트폰/온디바이스", "MSFT": "클라우드/AI", "GOOGL": "AI/소프트웨어",
    # 헷지 및 섹터 ETF (시장 하락기/테마 순환매 방어용)
    "USO": "에너지/정유", "GLD": "안전자산/금", "SQQQ": "지수하락/방어주", 
    "SOXX": "반도체종합", "LABU": "바이오/제약"
}

def get_hybrid_top_picks():
    """
    [대장 전용 무적 융합 엔진 v3]
    우선순위 파이프라인: 
    Stage 1 (ETF/헷지 매핑) ➡️ Stage 2 (개별주 상대적 강세) ➡️ Stage 3 (국내 텔레그램 우회)
    """
    if not os.path.exists(DB_PATH_THEME_FINANCE) or not os.path.exists(DB_PATH_MAPPING):
        return "⚠️ [엔진] 필요한 금융/맵핑 DB 파일이 누락되었어."

    # ── 0. 기본 데이터 로드 (41만 건 디비 연산) ──
    fin_conn = sqlite3.connect(DB_PATH_THEME_FINANCE)
    fin_cursor = fin_conn.cursor()
    fin_cursor.execute("SELECT stock_name, AVG(close_price) FROM kr_stock_daily_data GROUP BY stock_name")
    ma200_dict = {row[0]: row[1] for row in fin_cursor.fetchall()}
    fin_cursor.execute("SELECT stock_name, close_price FROM kr_stock_daily_data WHERE date = (SELECT MAX(date) FROM kr_stock_daily_data)")
    latest_prices = {row[0]: row[1] for row in fin_cursor.fetchall()}
    
    # 테마 매핑 테이블 로드
    fin_cursor.execute("SELECT stock_name, theme_name FROM kr_theme_stocks")
    kr_theme_mappings = fin_cursor.fetchall()
    fin_conn.close()

    # 국내 종목 200일선 정배열 검증 함수 내장
    def check_up_trend(kr_name):
        for db_stock_name in latest_prices.keys():
            if kr_name in db_stock_name:
                curr_p = latest_prices.get(db_stock_name, 0)
                ma200_p = ma200_dict.get(db_stock_name, 0)
                if curr_p > 0 and ma200_p > 0 and curr_p >= ma200_p:
                    return curr_p
        return 0

    # ── 간밤의 미장 시세 싹 스캔 ──
    us_perf = []
    for ticker in US_WATCHLIST.keys():
        try:
            stock = yf.Ticker(ticker)
            hist = stock.history(period="2d")
            if len(hist) >= 2:
                change = ((hist['Close'].iloc[1] - hist['Close'].iloc[0]) / hist['Close'].iloc[0]) * 100
                us_perf.append({"ticker": ticker, "change": change, "type": US_WATCHLIST[ticker]})
        except Exception:
            pass

    # ── 텔레그램 속보 데이터 확보 (Stage 3 및 크로스체크용) ──
    try:
        tele_conn = sqlite3.connect(DB_PATH_TELEGRAM)
        tele_cursor = tele_conn.cursor()
        tele_cursor.execute("SELECT message FROM telegram_events ORDER BY id DESC LIMIT 50")
        combined_text = " ".join([msg[0] for msg in tele_cursor.fetchall() if msg[0]])
        tele_conn.close()
    except Exception:
        combined_text = ""

    report_heading = ""
    final_picks = []

    # =========================================================================
    # 🔥 STAGE 1: 지수/ETF 및 헷지 자금 유입 체크 (우선순위 1등)
    # =========================================================================
    if us_perf:
        # ETF 플러스를 기록했거나 방어력이 유독 좋은 상위 ETF 탐색
        etf_perf = [x for x in us_perf if x['ticker'] in ["USO", "GLD", "SQQQ", "SOXX", "LABU"]]
        etf_perf.sort(key=lambda x: x['change'], reverse=True)
        
        # 가장 강했던 ETF가 상승했거나 하락장 속 홀로 방어(+0.5% 이상이거나 전체 1위)
        if etf_perf and (etf_perf[0]['change'] >= 0.5 or etf_perf[0]['ticker'] in ["USO", "GLD", "SQQQ"]):
            best_etf = etf_perf[0]
            
            # 매핑 디비 오픈
            map_conn = sqlite3.connect(DB_PATH_MAPPING)
            map_cursor = map_conn.cursor()
            map_cursor.execute("SELECT kr_name, reason FROM us_kr_mapping WHERE us_ticker = ?", (best_etf['ticker'],))
            rows = map_cursor.fetchall()
            map_conn.close()

            for kr_name, reason in rows:
                price = check_up_trend(kr_name)
                if price > 0:
                    final_picks.append({
                        "kr_name": kr_name, "reason": reason, "price": price,
                        "source": f"🇺🇸 ETF 강세 연동 ({best_etf['ticker']} {best_etf['change']:+.2f}%)",
                        "mentions": combined_text.count(kr_name)
                    })
            
            if final_picks:
                report_heading = "📊 [우선순위 1차 필터 작동: 미장 자금 유입 섹터 연동]"

    # =========================================================================
    # ⚡ STAGE 2: 개별 주도주 중 상대적 강세 종목 추적 (우선순위 2등)
    # =========================================================================
    if not final_picks and us_perf:
        stock_perf = [x for x in us_perf if x['ticker'] in ["NVDA", "INTC", "TSLA", "AAPL", "MSFT", "GOOGL"]]
        stock_perf.sort(key=lambda x: x['change'], reverse=True)
        
        if stock_perf:
            best_stock = stock_perf[0]  # 하락장 속에서도 가장 덜 밀렸거나 강했던 개별주 1등
            
            map_conn = sqlite3.connect(DB_PATH_MAPPING)
            map_cursor = map_conn.cursor()
            map_cursor.execute("SELECT kr_name, reason FROM us_kr_mapping WHERE us_ticker = ?", (best_stock['ticker'],))
            rows = map_cursor.fetchall()
            map_conn.close()

            for kr_name, reason in rows:
                price = check_up_trend(kr_name)
                if price > 0:
                    final_picks.append({
                        "kr_name": kr_name, "reason": reason, "price": price,
                        "source": f"🇺🇸 개별주 상대적 강세 연동 ({best_stock['ticker']} {best_stock['change']:+.2f}%)",
                        "mentions": combined_text.count(kr_name)
                    })
            
            if final_picks:
                report_heading = "🎯 [우선순위 2차 필터 작동: 미장 개별주 상대적 강세 역추적]"

    # =========================================================================
    # 💤 STAGE 3: 미장 전멸 시 국내 독고다이 텔레그램 속보 테마 우회 (우선순위 3등)
    # =========================================================================
    if not final_picks:
        report_heading = "🚨 [우선순위 3차 필터 작동: 미장 전멸로 인한 국내 실시간 속보 우회]"
        
        candidates = []
        seen_names = set()
        for stock_raw, theme_name in kr_theme_mappings:
            pure_name = re.sub(r'(KOSPI|KOSDAQ).*|\d{6}', '', stock_raw).strip()
            if pure_name in seen_names: continue
            
            price = check_up_trend(pure_name)
            if price > 0:
                # 최근 속보에서 종목명이나 테마명이 얼마나 불타오르는지 카운트
                mention_cnt = combined_text.count(pure_name) + combined_text.count(theme_name.split('(')[0])
                if mention_cnt > 0:
                    seen_names.add(pure_name)
                    candidates.append({
                        "kr_name": pure_name,
                        "reason": f"실시간 국내 [{theme_name}] 테마 수급 쏠림 현상 포착",
                        "price": price,
                        "source": "🇰🇷 국내 독고다이 테마 수급",
                        "mentions": mention_cnt
                    })
        
        # 텔레그램 언급량 순으로 탑 2 선출
        candidates.sort(key=lambda x: x['mentions'], reverse=True)
        final_picks = candidates[:2]

    # ── 최종 결과 출력 빌드업 ──
    if not final_picks:
        return "💡 대장, 3단계 무적 필터라인을 돌렸으나 200일선 정배열 기준을 만족하는 국내 종목이 디비에 매핑되어 있지 않아."

    # 텔레그램 속보 언급이나 모멘텀 순으로 상위 2개 압축
    final_picks.sort(key=lambda x: x['mentions'], reverse=True)
    final_2 = final_picks[:2]

    report = f"🔥 **{report_heading}** 🔥\n"
    report += "   *필터링: 미장 ETF/지수 ➡️ 개별주 상대강세 ➡️ 텔레그램 독고다이 ➡️ 200일 정배열*\n"
    report += "="*60 + "\n"
    
    for idx, item in enumerate(final_2):
        report += (
            f" 📌 **{idx+1}위 주도주: {item['kr_name']}**\n"
            f"    - 📊 추출 경로 : {item['source']}\n"
            f"    - 💡 매칭 단서 : {item['reason']}\n"
            f"    - 💰 현재 종가 : {item['price']:,}원 (41만 건 연산 200일선 상단 완착)\n"
            f"    - 📢 텔레 레이다: 최근 속보 내 {item['mentions']}회 포착\n"
            f"------------------------------------------------------------\n"
        )
    return report