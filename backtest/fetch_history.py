"""
fetch_history.py — 백테스트용 과거 데이터 수집
================================================================
[역할]
- pykrx로 KOSPI/KOSDAQ 종목별 OHLCV + 외국인/기관 순매수 수집
- SQLite (backtest_data.db)에 저장
- 결측/거래정지/액면분할 보정

[왜 pykrx?]
- KIS API: 1초 20건 제한 + 인증 필요 + 외국인/기관 별도 조회
- pykrx: KRX 공시 데이터 직접 스크레이핑 (인증 X, 한 번에 수십 종목)
- 5년치 350종목 → 약 5~10분 (KIS는 30분~1시간)

[사용]
  python3 fetch_history.py --start 2021-01-01 --end 2026-05-01 --top 200
  python3 fetch_history.py --codes 005930,000660,035720
"""
import os
import time
import argparse
import sqlite3
import datetime
from typing import Optional


def _import_pykrx():
    """pykrx는 데이터 수집 시에만 필요. 지연 임포트로 init_db만 쓰는 경우 부담 없게."""
    try:
        from pykrx import stock
        return stock
    except ImportError:
        print("❌ pykrx 미설치 — 'pip install pykrx --break-system-packages'")
        raise


# ============================================================
# DB 스키마
# ============================================================
SCHEMA = """
CREATE TABLE IF NOT EXISTS daily_ohlcv (
    code     TEXT NOT NULL,        -- 종목코드 (6자리)
    date     TEXT NOT NULL,        -- YYYY-MM-DD
    open     REAL,
    high     REAL,
    low      REAL,
    close    REAL,
    volume   INTEGER,              -- 거래량 (주)
    value    INTEGER,              -- 거래대금 (원)
    change   REAL,                 -- 등락률 (%)
    PRIMARY KEY (code, date)
);

CREATE TABLE IF NOT EXISTS daily_flow (
    code         TEXT NOT NULL,
    date         TEXT NOT NULL,
    foreign_qty  INTEGER,          -- 외국인 순매수 수량 (음수=순매도)
    orgn_qty     INTEGER,          -- 기관 순매수 수량
    prsn_qty     INTEGER,          -- 개인 순매수 수량
    PRIMARY KEY (code, date)
);

CREATE TABLE IF NOT EXISTS market_meta (
    code         TEXT PRIMARY KEY,
    name         TEXT,
    market       TEXT,             -- 'KOSPI' / 'KOSDAQ'
    listed_date  TEXT,
    last_updated TEXT
);

CREATE INDEX IF NOT EXISTS idx_ohlcv_date ON daily_ohlcv(date);
CREATE INDEX IF NOT EXISTS idx_flow_date  ON daily_flow(date);
"""


def init_db(db_path: str) -> sqlite3.Connection:
    """DB 초기화 + WAL 모드 (멀티봇 시스템과 일관)"""
    conn = sqlite3.connect(db_path, timeout=30)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


# ============================================================
# 종목 리스트 가져오기
# ============================================================
def get_top_codes(top_n: int = 200, market: str = "ALL") -> list:
    """
    시총 상위 N개 코드 반환.
    market: 'KOSPI' / 'KOSDAQ' / 'ALL'

    [Survivorship Bias 주의]
    이건 '오늘' 시점 시총 상위라서 과거 상폐된 종목은 빠짐.
    완벽한 백테스트엔 부족하지만 실용적으로 OK.
    """
    stock = _import_pykrx()
    today = datetime.datetime.now().strftime("%Y%m%d")

    codes = []
    if market in ("KOSPI", "ALL"):
        codes += stock.get_market_ticker_list(today, market="KOSPI")
    if market in ("KOSDAQ", "ALL"):
        codes += stock.get_market_ticker_list(today, market="KOSDAQ")

    # 시총순 정렬
    cap_df = stock.get_market_cap_by_ticker(today)
    cap_df = cap_df[cap_df.index.isin(codes)]
    cap_df = cap_df.sort_values("시가총액", ascending=False)
    return cap_df.index[:top_n].tolist()


# ============================================================
# 종목별 일봉 + 수급 수집
# ============================================================
def fetch_one(conn: sqlite3.Connection, code: str,
              start: str, end: str, verbose: bool = False) -> dict:
    """
    한 종목의 OHLCV + 외국인/기관 순매수 수집 → DB 적재.
    start/end: 'YYYYMMDD' 형식.
    반환: 통계 dict
    """
    stock = _import_pykrx()
    stat = {"code": code, "ohlcv_rows": 0, "flow_rows": 0, "errors": []}

    # ── OHLCV ─────────────────────────────────────
    try:
        df = stock.get_market_ohlcv(start, end, code)
        if df is not None and not df.empty:
            rows = []
            for dt, row in df.iterrows():
                rows.append((
                    code,
                    dt.strftime("%Y-%m-%d"),
                    float(row["시가"]),
                    float(row["고가"]),
                    float(row["저가"]),
                    float(row["종가"]),
                    int(row["거래량"]),
                    int(row.get("거래대금", 0)),
                    float(row.get("등락률", 0)),
                ))
            conn.executemany(
                "INSERT OR REPLACE INTO daily_ohlcv "
                "(code, date, open, high, low, close, volume, value, change) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
            stat["ohlcv_rows"] = len(rows)
    except Exception as e:
        stat["errors"].append(f"ohlcv: {e}")

    # ── 외국인/기관/개인 순매수 ──────────────────────
    try:
        df = stock.get_market_trading_volume_by_date(start, end, code)
        if df is not None and not df.empty:
            rows = []
            for dt, row in df.iterrows():
                rows.append((
                    code,
                    dt.strftime("%Y-%m-%d"),
                    int(row.get("외국인합계", row.get("외국인", 0))),
                    int(row.get("기관합계", row.get("기관", 0))),
                    int(row.get("개인", 0)),
                ))
            conn.executemany(
                "INSERT OR REPLACE INTO daily_flow "
                "(code, date, foreign_qty, orgn_qty, prsn_qty) "
                "VALUES (?, ?, ?, ?, ?)",
                rows,
            )
            stat["flow_rows"] = len(rows)
    except Exception as e:
        stat["errors"].append(f"flow: {e}")

    conn.commit()

    if verbose:
        print(f"   {code}: ohlcv={stat['ohlcv_rows']}건, "
              f"flow={stat['flow_rows']}건"
              + (f" ⚠️ {stat['errors']}" if stat['errors'] else ""))
    return stat


# ============================================================
# 메인
# ============================================================
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2023-01-01",
                        help="시작일 (YYYY-MM-DD)")
    parser.add_argument("--end",
                        default=datetime.datetime.now().strftime("%Y-%m-%d"),
                        help="종료일 (YYYY-MM-DD)")
    parser.add_argument("--top", type=int, default=200,
                        help="시총 상위 N개")
    parser.add_argument("--codes", default="",
                        help="특정 종목만 (쉼표구분), 지정 시 --top 무시")
    parser.add_argument("--market", default="ALL",
                        choices=["KOSPI", "KOSDAQ", "ALL"])
    parser.add_argument("--db",
                        default=os.path.join(
                            os.path.dirname(__file__), "data", "backtest_data.db"))
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--sleep", type=float, default=0.3,
                        help="요청 간격 (초)")
    args = parser.parse_args()

    # DB 디렉토리 보장
    os.makedirs(os.path.dirname(args.db), exist_ok=True)
    conn = init_db(args.db)

    # 종목 리스트
    if args.codes:
        codes = [c.strip() for c in args.codes.split(",") if c.strip()]
    else:
        print(f"📋 시총 상위 {args.top}개 종목 조회 중 ({args.market})...")
        codes = get_top_codes(args.top, args.market)
    print(f"✅ 대상 종목: {len(codes)}개")

    # 날짜 변환 (pykrx는 YYYYMMDD)
    start = args.start.replace("-", "")
    end   = args.end.replace("-", "")

    # 수집
    print(f"📡 데이터 수집 시작: {args.start} ~ {args.end}")
    t0 = time.time()
    fail_count = 0
    for i, code in enumerate(codes, 1):
        try:
            stat = fetch_one(conn, code, start, end, args.verbose)
            if stat["errors"] and not stat["ohlcv_rows"]:
                fail_count += 1
        except Exception as e:
            print(f"❌ {code} 실패: {e}")
            fail_count += 1
        if i % 20 == 0:
            elapsed = time.time() - t0
            eta = elapsed / i * (len(codes) - i)
            print(f"   진행 {i}/{len(codes)} | "
                  f"경과 {elapsed:.0f}s | 예상 잔여 {eta:.0f}s")
        time.sleep(args.sleep)  # KRX rate limit

    elapsed = time.time() - t0
    print(f"\n✅ 완료 — {len(codes)}종목 / 실패 {fail_count}종목 "
          f"/ 소요 {elapsed:.0f}초")

    # 통계
    cur = conn.execute("SELECT COUNT(*) FROM daily_ohlcv")
    print(f"📊 OHLCV 총 {cur.fetchone()[0]:,}건 저장됨")
    cur = conn.execute("SELECT COUNT(*) FROM daily_flow")
    print(f"📊 수급  총 {cur.fetchone()[0]:,}건 저장됨")

    conn.close()


if __name__ == "__main__":
    main()
