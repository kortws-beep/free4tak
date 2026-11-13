"""
data_check.py — 수집 데이터 점검표 (2026-11-14, 대장 "생성되는 데이터의 체크리스트")
================================================================
[이 파일이 하는 일 — 비개발자용 설명]
docs/DATA_CATALOG.md(사람이 읽는 데이터 지도)에 있는 '계속 쌓여야 하는' 표들을 하나씩 열어서
마지막 기록 날짜가 너무 오래되지 않았는지 본다. 서버를 새로 깔았거나(복구), 수집기가 조용히 죽었는지 의심될 때 실행.
  ✅ 정상 / ⚠️ 오래됨(허용 일수 넘김 — 수집기 확인) / ❌ 파일·표 없음 / ⏸️ 의도적으로 멈춘 자료(참고만)
날짜는 '오늘'과 비교한 달력 일수(주말·휴일 감안해 허용치를 넉넉히 잡음).
실행: venv/bin/python scripts/data_check.py
"""
import datetime
import os
import sqlite3

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# (파일, 표, 날짜 칸, 허용 일수, 설명) — 허용 일수 None = 의도적으로 멈춘 자료
CATALOG = [
    # ── 매매 기록 ──
    ("master_trades.db", "master_trades", "sell_time", 10, "3봇 통합 매매 기록(성적표·손실한도)"),
    ("sbot_trade_history.db", "trades", "buy_time", 30, "sbot 매매"),
    ("sbot_trade_history.db", "candidate_log", "ts", 5, "sbot 후보(11-14~)"),
    ("daybot_trade_history.db", "trades", "buy_time", 30, "daybot 매매"),
    ("daybot_trade_history.db", "candidate_log", "ts", 5, "daybot 후보"),
    ("cbot_trade_history.db", "trades", "buy_time", 10, "cbot 매매"),
    # ── 리나 관찰·시장 ──
    ("lina_bot/three_month_leader_log.db", "leader_obs", "date", 5, "주도주검색식3 파이썬판 통과/탈락"),
    ("lina_bot/three_month_leader_log.db", "danta_obs", "date", 5, "단타000 파이썬판"),
    ("lina_bot/three_month_leader_log.db", "tml_obs", "date", 5, "3개월수급 당일주도주"),
    ("lina_bot/three_month_leader_log.db", "obs0909", "date", 5, "090930타점"),
    ("lina_bot/three_month_leader_log.db", "sector_obs", "date", 5, "섹터 순위(3분)"),
    ("lina_bot/three_month_leader_log.db", "sector_alerts", "date", 10, "섹터 대장·2등 알림"),
    ("lina_bot/three_month_leader_log.db", "theme_open_obs", "date", 5, "본장 테마 순위"),
    ("lina_bot/three_month_leader_log.db", "theme_pre_top", "date", 5, "프리장 테마 상위"),
    ("lina_bot/three_month_leader_log.db", "kiwoom_cond_log", "date", 5, "키움 조건검색 결과"),
    ("lina_bot/three_month_leader_log.db", "market_regime", "date", 5, "국면(20일선 위 비율)"),
    ("lina_bot/three_month_leader_log.db", "market_index", "date", 5, "지수 일봉"),
    ("lina_bot/three_month_leader_log.db", "market_investor", "date", 5, "투자자별 수급(시장)"),
    ("lina_bot/three_month_leader_log.db", "market_program", "date", 5, "프로그램 매매"),
    ("lina_bot/three_month_leader_log.db", "market_futures", "date", 5, "선물"),
    ("lina_bot/three_month_leader_log.db", "kofia_funds", "date", 7, "예탁금·미수·반대매매"),
    ("lina_bot/three_month_leader_log.db", "kofia_credit", "date", 7, "신용·대차"),
    ("lina_bot/three_month_leader_log.db", "manual_watch_log", "ts", 30, "리나등록 매매 이벤트"),
    ("lina_bot/lina_archive.db", "messages", "ts", 5, "리나가 보낸 메시지 원문(11-14~)"),
    ("lina_bot/lina_archive.db", "quotes_1m", "date", 5, "관심그룹 1분 시세(11-14~)"),
    ("lina_bot/kr_theme_finance.db", "kr_stock_daily_data", "date", 5, "전 종목 일봉+외인·기관(이름 기준)"),
    ("intelligence/intraday_flow.db", "flow", "date", 5, "장중 잠정 수급"),
    ("intelligence/intraday_flow.db", "broker", "date", 5, "거래원(외국계 창구)"),
    ("intelligence/sector_monitor.db", "sector_flow", "ts", 5, "키움 테마 흐름"),
    ("intelligence/sector_monitor.db", "stock_momentum", "ts", 5, "테마 내 종목 모멘텀"),
    ("intelligence/market_concentration.db", "concentration_snapshot", "ts", 5, "대형주 쏠림"),
    ("intelligence/kind_disclosure.db", "disc", "date", 5, "KIND 공시"),
    ("intelligence/feature_news.db", "news", "collected_at", 5, "[특징주] 뉴스"),
    ("intelligence/hk_consensus.db", "reports", "date", 7, "한경 컨센서스 리포트"),
    ("intelligence/news_sentiment.db", "news_sentiment", "date", 5, "뉴스 감성(키워드)"),
    ("intelligence/mtn_morning.db", "mtn_morning", "date", 5, "MTN 아침 요약"),
    ("intelligence/youtube_picks.db", "youtube_picks", "created_at", 5, "유튜브 종목 추출"),
    ("intelligence/youtube_picks.db", "telegram_buzz", "created_at", 10, "방송 텔레그램 코너"),
    ("intelligence/ai_momentum_picks.db", "momentum_picks", "date", 5, "AI 모멘텀 픽"),
    ("backtest/data/backtest_data.db", "daily_ohlcv", "date", 5, "백테스트 일봉(200종목)"),
    ("backtest/data/backtest_data.db", "daily_leading_sectors", "date", 5, "주도 섹터(AI 판정)"),
    ("backtest/data/minute_cache.db", "bars", "date", 5, "한투 1분봉 캐시(신호 종목 16:05 수집 포함)"),
    # ── 코인 ──
    ("coin_price_ticks.db", "price_ticks", "ts", 2, "코인 시세 틱(cbot)"),
    ("backtestc/coin_backtest.db", "daily_ohlcv", "date", 3, "코인 봉"),
    ("backtestc/coin_global.db", "funding", "ts", 3, "펀딩비·김프 등 해외 지표"),
    # ── 의도적으로 멈춘 자료(참고) ──
    ("intelligence/telegram_events.db", "telegram_events", "created_at", None, "텔레그램 채널(10-03 계정 탈취로 수집 중단)"),
    ("lina_bot/sbo2_trades.db", "sbo2_candidates", "scan_date", None, "sbo2 후보(09-28 sbot에 통합되며 종료)"),
    ("lina_bot/kiwoom_pool_history.db", "kiwoom_pool_log", "scan_date", None, "키움 풀 추적(10-08 종료)"),
    ("backtest/data/backtest_data.db", "daily_flow", "date", None, "종목 수급(06-12 종료 — kr_stock_daily_data가 대신)"),
]


def last_date(path: str, table: str, col: str):
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    try:
        v = c.execute(f"SELECT MAX({col}) FROM {table}").fetchone()[0]
        n = c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        c.close()
    return (str(v)[:10].replace("T", " ") if v else None), n


def main():
    today = datetime.date.today()
    bad = 0
    for path, table, col, allow, desc in CATALOG:
        full = os.path.join(BASE, path)
        if not os.path.exists(full):
            print(f"❌ {path}:{table} — 파일 없음 · {desc}")
            bad += 1
            continue
        try:
            d, n = last_date(full, table, col)
        except Exception as e:
            print(f"❌ {path}:{table} — {e} · {desc}")
            bad += 1
            continue
        if allow is None:
            print(f"⏸️ {path}:{table} 마지막 {d} ({n:,}줄) · {desc}")
            continue
        try:
            age = (today - datetime.date.fromisoformat(d[:10])).days if d else 9999
        except ValueError:
            age = 9999
        ok = age <= allow
        bad += not ok
        print(f"{'✅' if ok else '⚠️'} {path}:{table} 마지막 {d} ({age}일 전, {n:,}줄) · {desc}")
    print(f"\n{'🎉 전부 정상' if not bad else f'⚠️ 확인 필요 {bad}개'} — 지도: docs/DATA_CATALOG.md")


if __name__ == "__main__":
    main()
