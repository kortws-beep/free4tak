"""
money_weather.py — 자금시장 날씨판 1단계: 글로벌 자금 지표·이벤트 달력 저장 (2026-11-17, 대장 "자금시장 자체의 날씨판, 키키가 관제탑")
================================================================
[이 파일이 하는 일 — 비개발자용 설명]
대장 구상: 주가·수급·뉴스·해외 지표(금리·금·코인·유가)·주도 섹터·이벤트(실적·IPO·전쟁)를 한 판에 모아 '지금은 공격/보통/방어/쉼'을
정하고, 그 판으로 주식·코인 봇을 같이 제어. 3층 — ①글로벌 자금 날씨 ②시장별(한국 주식·코인) ③섹터·테마.
②·③ 재료는 이미 쌓이는 중(국면·수급·신용·섹터·뉴스·코인 공포지수 등, docs/DATA_CATALOG.md). 빠져 있던 ①과 이벤트 달력을 여기서 쌓는다.
  · global_daily : 해외 지표 일봉(미국 지수·반도체·VIX·금리·달러·금·유가·구리·비트코인·원달러·닛케이·대만) — 출처 provider 칸
                   (매일 키키 07:10 — 미국장 끝난 뒤 최근 10일을 다시 받아 덮어씀: 장중에 받힌 임시값도 확정값으로 고쳐짐)
  · events       : 시장을 흔드는 일정(실적·IPO·FOMC·금통위·전쟁 등) — 출처 source 칸 필수, 사람이 넣은 것도 출처 적기
아직 판단(점수·태세)은 안 함 — 2단계 '관찰판'에서. 이 파일은 데이터만.
실행: venv/bin/python intelligence/money_weather.py --backfill [년수, 기본 5]   (과거 이력 한 번에)
      venv/bin/python intelligence/money_weather.py                         (최근 10일 갱신)
      venv/bin/python intelligence/money_weather.py --events                 (이벤트 달력 보기)
"""
import datetime
import math
import os
import sqlite3
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(_HERE, "money_weather.db")
MACRO_DB = os.path.join(_HERE, "macro_calendar.db")
PROVIDER = "Yahoo Finance(yfinance)"

# 심볼 → (이름, 층/분야) — ①글로벌 자금 날씨 재료
SYMBOLS = {
    "^IXIC": ("나스닥 종합", "미국 주식"), "^GSPC": ("S&P 500", "미국 주식"), "^SOX": ("필라델피아 반도체", "미국 주식"),
    "^VIX": ("VIX 공포지수", "심리"), "^TNX": ("미국 10년 금리", "금리"), "^IRX": ("미국 3개월 금리", "금리"),
    "DX-Y.NYB": ("달러 인덱스", "환율"), "KRW=X": ("원/달러", "환율"), "GC=F": ("금", "원자재"), "CL=F": ("WTI 유가", "원자재"),
    "HG=F": ("구리", "원자재"), "BTC-USD": ("비트코인(달러)", "코인"), "^N225": ("닛케이", "아시아 주식"),
    "^TWII": ("대만 가권", "아시아 주식"),
    "^KS11": ("코스피", "한국 주식"), "^KQ11": ("코스닥", "한국 주식"),   # ★ 날씨판 판단 검증용(다음 날·5일 뒤 성적)
    # ★ 11-17 대장 "미장 섹터 움직임 추가" — S&P 11개 섹터 ETF(SPDR) + 반도체·방산·원전·로봇 테마 ETF
    "XLK": ("미국 기술", "미국 섹터"), "XLC": ("미국 커뮤니케이션", "미국 섹터"), "XLY": ("미국 경기소비재", "미국 섹터"),
    "XLP": ("미국 필수소비재", "미국 섹터"), "XLV": ("미국 헬스케어", "미국 섹터"), "XLF": ("미국 금융", "미국 섹터"),
    "XLI": ("미국 산업재", "미국 섹터"), "XLE": ("미국 에너지", "미국 섹터"), "XLB": ("미국 기초소재", "미국 섹터"),
    "XLU": ("미국 유틸리티", "미국 섹터"), "XLRE": ("미국 부동산", "미국 섹터"),
    "SMH": ("미국 반도체 ETF", "미국 테마"), "ITA": ("미국 방산 ETF", "미국 테마"), "URA": ("우라늄·원전 ETF", "미국 테마"),
    "BOTZ": ("로봇·AI ETF", "미국 테마"), "LIT": ("리튬·2차전지 ETF", "미국 테마"), "TAN": ("태양광 ETF", "미국 테마"),
}


def _conn():
    c = sqlite3.connect(DB_PATH, timeout=15)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("CREATE TABLE IF NOT EXISTS global_daily (date TEXT, symbol TEXT, name TEXT, area TEXT, open REAL, high REAL, "
              "low REAL, close REAL, chg REAL, provider TEXT, updated_at TEXT, PRIMARY KEY(date, symbol))")
    c.execute("CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT, time_kst TEXT, kind TEXT, "
              "title TEXT, scope TEXT, impact TEXT, source TEXT, created_at TEXT, UNIQUE(date, kind, title))")
    return c


def _num(v):
    try:
        f = float(v)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


def collect(period: str = "10d") -> int:
    """yfinance로 SYMBOLS 일봉을 받아 global_daily에 덮어씀 → 저장한 줄 수. 심볼 하나 실패해도 나머지는 계속."""
    import yfinance as yf
    now = datetime.datetime.now().isoformat(timespec="seconds")
    rows = []
    for sym, (name, area) in SYMBOLS.items():
        try:
            h = yf.Ticker(sym).history(period=period, auto_adjust=False)
        except Exception as e:
            print(f"⚠️ [자금날씨] {sym} 받기 실패: {e}")
            continue
        prev = None
        for idx, r in h.iterrows():
            close = _num(r.get("Close"))
            if close is None:
                continue
            chg = (close / prev - 1) * 100 if prev else None
            prev = close
            rows.append((idx.date().isoformat(), sym, name, area, _num(r.get("Open")), _num(r.get("High")),
                         _num(r.get("Low")), close, chg, PROVIDER, now))
    if not rows:
        return 0
    c = _conn()
    # 맨 앞 하루는 전일이 없어 chg가 None — 이미 저장된 값이 있으면 그 chg를 지우지 않게 None일 땐 기존 유지
    c.executemany("INSERT INTO global_daily VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(date, symbol) DO UPDATE SET "
                  "open=excluded.open, high=excluded.high, low=excluded.low, close=excluded.close, "
                  "chg=COALESCE(excluded.chg, global_daily.chg), provider=excluded.provider, updated_at=excluded.updated_at",
                  rows)
    c.commit()
    c.close()
    return len(rows)


def add_event(date: str, kind: str, title: str, source: str, time_kst: str = "", scope: str = "", impact: str = "") -> bool:
    """이벤트 하나(같은 날·종류·제목이면 무시). source(출처)는 반드시 — 비면 저장 안 함(대장 '출처는 명확히')."""
    if not (date and kind and title and source):
        print("⚠️ [자금날씨] 이벤트는 날짜·종류·제목·출처가 다 있어야 저장")
        return False
    c = _conn()
    cur = c.execute("INSERT OR IGNORE INTO events (date, time_kst, kind, title, scope, impact, source, created_at) "
                    "VALUES (?,?,?,?,?,?,?,?)", (date, time_kst, kind, title, scope, impact, source,
                                                 datetime.datetime.now().isoformat(timespec="seconds")))
    c.commit()
    c.close()
    return cur.rowcount > 0


def import_macro_calendar() -> int:
    """macro_calendar.db(FOMC·금통위)를 이벤트 달력으로 옮김(원래 출처 그대로)."""
    try:
        m = sqlite3.connect(MACRO_DB, timeout=10)
        rows = m.execute("SELECT date, event_type, description, source FROM macro_events").fetchall()
        m.close()
    except Exception as e:
        print(f"⚠️ [자금날씨] 매크로 일정 읽기 실패: {e}")
        return 0
    return sum(add_event(d, k, desc, src or "macro_calendar.db", scope="금리", impact="높음") for d, k, desc, src in rows)


def upcoming(days: int = 14, today: str = None) -> list:
    today = today or datetime.date.today().isoformat()
    end = (datetime.date.fromisoformat(today) + datetime.timedelta(days=days)).isoformat()
    c = _conn()
    try:
        return c.execute("SELECT date, time_kst, kind, title, scope, impact, source FROM events WHERE date BETWEEN ? AND ? "
                         "ORDER BY date, time_kst", (today, end)).fetchall()
    finally:
        c.close()


def latest(n: int = 1) -> dict:
    """심볼별 최근 n개 (date, close, chg)."""
    c = _conn()
    try:
        out = {}
        for sym in SYMBOLS:
            out[sym] = c.execute("SELECT date, close, chg FROM global_daily WHERE symbol=? ORDER BY date DESC LIMIT ?",
                                 (sym, n)).fetchall()
        return out
    finally:
        c.close()


if __name__ == "__main__":
    if "--backfill" in sys.argv:
        yrs = next((a for a in sys.argv[sys.argv.index("--backfill") + 1:] if a.isdigit()), "5")
        print(f"✅ 과거 {yrs}년 저장: {collect(f'{yrs}y'):,}줄")
        print(f"✅ 매크로 일정 옮김: {import_macro_calendar()}건")
    elif "--events" in sys.argv:
        for r in upcoming(60):
            print(r)
    else:
        print(f"✅ 최근 갱신: {collect():,}줄")
