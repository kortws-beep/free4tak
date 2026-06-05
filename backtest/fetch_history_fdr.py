"""
fetch_history_fdr.py — FinanceDataReader 기반 데이터 수집 (pykrx 백업)
================================================================
[배경]
2026년 2월 이후 pykrx가 KRX API 차단으로 동작 불안정.
FinanceDataReader는 네이버 금융 백엔드를 사용해 OHLCV 안정.
다만 외국인/기관 수급은 KRX 의존이라 받을 수 없음 → 일단 0으로 채움.

[차이점 vs fetch_history.py]
- OHLCV: ✅ 가능 (네이버 백엔드)
- 외국인/기관 순매수: ❌ 채워지지 않음 (수급 가산점은 0)
- 종목 리스트 자동 선정: 미리 정의된 KOSPI 50 사용

[사용]
  python3 fetch_history_fdr.py --start 2024-05-08 --end 2025-05-08
  python3 fetch_history_fdr.py --codes 005930,000660,035720
"""
import os
import sys
import time
import argparse
import sqlite3
import datetime
from typing import Optional


# ============================================================
# DB 초기화 (fetch_history와 동일 스키마 — 호환성 유지)
# ============================================================
SCHEMA = """
CREATE TABLE IF NOT EXISTS daily_ohlcv (
    code     TEXT NOT NULL,
    date     TEXT NOT NULL,
    open     REAL,
    high     REAL,
    low      REAL,
    close    REAL,
    volume   INTEGER,
    value    INTEGER,
    change   REAL,
    PRIMARY KEY (code, date)
);

CREATE TABLE IF NOT EXISTS daily_flow (
    code         TEXT NOT NULL,
    date         TEXT NOT NULL,
    foreign_qty  INTEGER,
    orgn_qty     INTEGER,
    prsn_qty     INTEGER,
    PRIMARY KEY (code, date)
);

CREATE TABLE IF NOT EXISTS market_meta (
    code         TEXT PRIMARY KEY,
    name         TEXT,
    market       TEXT,
    listed_date  TEXT,
    last_updated TEXT
);

CREATE INDEX IF NOT EXISTS idx_ohlcv_date ON daily_ohlcv(date);
"""


def init_db(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=30)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


# ============================================================
# 정적 KOSPI 50 리스트 (시총 상위, 2026년 5월 기준 추정)
# ★ KRX API 차단 시 fallback으로 사용
# ★ 운영 중에는 사용자가 직접 --codes로 지정하는 게 가장 안전
# ============================================================
KOSPI_TOP50_FALLBACK = [
    "005930",  # 삼성전자
    "000660",  # SK하이닉스
    "373220",  # LG에너지솔루션
    "207940",  # 삼성바이오로직스
    "005380",  # 현대차
    "005935",  # 삼성전자우
    "000270",  # 기아
    "012330",  # 현대모비스
    "035420",  # NAVER
    "035720",  # 카카오
    "068270",  # 셀트리온
    "105560",  # KB금융
    "055550",  # 신한지주
    "051910",  # LG화학
    "006400",  # 삼성SDI
    "028260",  # 삼성물산
    "066570",  # LG전자
    "003670",  # 포스코퓨처엠
    "096770",  # SK이노베이션
    "017670",  # SK텔레콤
    "030200",  # KT
    "086790",  # 하나금융지주
    "316140",  # 우리금융지주
    "024110",  # 기업은행
    "138930",  # BNK금융지주
    "032830",  # 삼성생명
    "088350",  # 한화생명
    "009150",  # 삼성전기
    "010130",  # 고려아연
    "011200",  # HMM
    "267260",  # HD현대일렉트릭
    "329180",  # HD현대중공업
    "010140",  # 삼성중공업
    "042660",  # 한화오션
    "323410",  # 카카오뱅크
    "377300",  # 카카오페이
    "035250",  # 강원랜드
    "047810",  # 한국항공우주
    "003550",  # LG
    "034730",  # SK
    "015760",  # 한국전력
    "036570",  # 엔씨소프트
    "251270",  # 넷마블
    "079550",  # LIG넥스원
    "009540",  # HD한국조선해양
    "402340",  # SK스퀘어
    "000810",  # 삼성화재
    "001040",  # CJ
    "010950",  # S-Oil
    "032640",  # LG유플러스
]


# ============================================================
# FDR 임포트 (지연)
# ============================================================
def _import_fdr():
    try:
        import FinanceDataReader as fdr
        return fdr
    except ImportError:
        print("❌ FinanceDataReader 미설치")
        print("   설치: pip install finance-datareader")
        sys.exit(1)


# ============================================================
# 종목 리스트 가져오기
# ============================================================
def get_codes(top_n: int = 50, market: str = "KOSPI") -> list:
    """
    1차: FDR의 StockListing 시도
    2차: 실패 시 정적 KOSPI 50 리스트 fallback
    """
    fdr = _import_fdr()
    try:
        df = fdr.StockListing(market)
        # 시총 컬럼이 있으면 정렬
        if "Marcap" in df.columns:
            df = df.sort_values("Marcap", ascending=False)
        codes = df["Code"].tolist()[:top_n] if "Code" in df.columns else \
                df.iloc[:, 0].tolist()[:top_n]
        if codes and len(codes) > 0:
            print(f"✅ FDR로 {market} {len(codes)}종목 조회됨")
            return codes
    except Exception as e:
        print(f"⚠️ FDR StockListing 실패: {e}")

    # Fallback
    print(f"⚠️ KRX 차단 영향 — 정적 KOSPI 50 리스트 사용")
    return KOSPI_TOP50_FALLBACK[:top_n]


# ============================================================
# 종목 1개 OHLCV 수집
# ============================================================
def fetch_one(conn: sqlite3.Connection, code: str,
              start: str, end: str,
              fdr=None, verbose: bool = False) -> dict:
    """
    code: 6자리 (e.g. '005930')
    start/end: 'YYYY-MM-DD'
    """
    if fdr is None:
        fdr = _import_fdr()

    stat = {"code": code, "ohlcv_rows": 0, "flow_rows": 0, "errors": []}

    # ── OHLCV ──────────────────────────────────────
    try:
        df = fdr.DataReader(code, start, end)
        if df is not None and not df.empty:
            rows = []
            # 컬럼 정규화: FDR은 보통 [Open, High, Low, Close, Volume, Change]
            df = df.rename(columns={
                "Open":   "open",
                "High":   "high",
                "Low":    "low",
                "Close":  "close",
                "Volume": "volume",
                "Change": "change",
            })
            for dt, row in df.iterrows():
                close_v = float(row.get("close", 0))
                vol_v   = int(row.get("volume", 0))
                rows.append((
                    code,
                    dt.strftime("%Y-%m-%d"),
                    float(row.get("open",  close_v)),
                    float(row.get("high",  close_v)),
                    float(row.get("low",   close_v)),
                    close_v,
                    vol_v,
                    int(close_v * vol_v),       # 거래대금 = 종가 × 거래량 (근사)
                    float(row.get("change", 0)) * 100,  # FDR은 비율(0.01=1%)
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

    # ── 외국인/기관: FDR로는 불가 → 0으로 채워서 일관성 유지 ──
    # (백테스트 시 strategy.py의 수급 가산점만 0이 됨)
    # 만약 KIS API로 받을 수 있다면 별도 모듈 추가 권장

    conn.commit()

    if verbose:
        print(f"   {code}: ohlcv={stat['ohlcv_rows']}건"
              + (f" ⚠️ {stat['errors']}" if stat['errors'] else ""))
    return stat


# ============================================================
# 메인
# ============================================================
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2024-01-01")
    parser.add_argument("--end",
                        default=datetime.datetime.now().strftime("%Y-%m-%d"))
    parser.add_argument("--top", type=int, default=50)
    parser.add_argument("--codes", default="",
                        help="특정 종목만 (쉼표구분), 지정 시 --top 무시")
    parser.add_argument("--market", default="KOSPI",
                        choices=["KOSPI", "KOSDAQ", "KRX"])
    parser.add_argument("--db",
                        default=os.path.join(
                            os.path.dirname(__file__), "data", "backtest_data.db"))
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--sleep", type=float, default=0.2)
    args = parser.parse_args()

    os.makedirs(os.path.dirname(args.db), exist_ok=True)
    conn = init_db(args.db)
    fdr  = _import_fdr()

    if args.codes:
        codes = [c.strip() for c in args.codes.split(",") if c.strip()]
    else:
        codes = get_codes(args.top, args.market)
    print(f"📋 대상 종목 {len(codes)}개")

    print(f"📡 데이터 수집 시작: {args.start} ~ {args.end}")
    print(f"   ⚠️ FDR 모드: 외국인/기관 수급은 0으로 채워짐 (KRX 차단)")

    t0 = time.time()
    fail_count = 0
    for i, code in enumerate(codes, 1):
        try:
            stat = fetch_one(conn, code, args.start, args.end,
                             fdr=fdr, verbose=args.verbose)
            if not stat["ohlcv_rows"]:
                fail_count += 1
        except Exception as e:
            print(f"❌ {code} 실패: {e}")
            fail_count += 1
        if i % 10 == 0:
            elapsed = time.time() - t0
            eta = elapsed / i * (len(codes) - i)
            print(f"   진행 {i}/{len(codes)} | "
                  f"경과 {elapsed:.0f}s | 잔여 {eta:.0f}s")
        time.sleep(args.sleep)

    elapsed = time.time() - t0
    print(f"\n✅ 완료 — {len(codes)}종목 / 실패 {fail_count}종목 "
          f"/ 소요 {elapsed:.0f}초")

    cur = conn.execute("SELECT COUNT(*) FROM daily_ohlcv")
    print(f"📊 OHLCV 총 {cur.fetchone()[0]:,}건")

    # 샘플 출력
    sample = conn.execute(
        "SELECT code, date, close, volume FROM daily_ohlcv "
        "ORDER BY date DESC LIMIT 5").fetchall()
    print("📋 샘플 (최근 5건):")
    for s in sample:
        print(f"   {s[0]} {s[1]} 종가={s[2]:>8,.0f} 거래량={s[3]:>12,}")

    conn.close()


if __name__ == "__main__":
    main()
