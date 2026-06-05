"""
fetch_flow_pykrx.py — pykrx 기반 과거 수급 데이터 일괄 수집
================================================================
[이 파일이 하는 일]
KIS API는 최근 30일치만 반환하므로 과거 수급을 소급 수집 불가.
pykrx는 KRX 공식 데이터를 무료로 제공 — 날짜 범위 지정 가능.

수집 항목:
  - 외국인 순매수 수량 (foreign_qty)
  - 기관 순매수 수량  (orgn_qty)
  - 개인 순매수 수량  (prsn_qty)

[설치]
  pip install pykrx

[사용]
  # 기본 — daily_ohlcv의 모든 종목, ohlcv 기간에 맞게 자동 수집
  python3 fetch_flow_pykrx.py

  # 기간 지정
  python3 fetch_flow_pykrx.py --start 2024-05-01 --end 2026-05-18

  # 특정 종목만
  python3 fetch_flow_pykrx.py --codes 005930,000660 --start 2024-05-01

  # 빠른 테스트 (첫 5종목만)
  python3 fetch_flow_pykrx.py --max-codes 5

[기존 daily_flow와 완전 호환]
  INSERT OR IGNORE → 기존 KIS 수집분(최근 36일)과 중복 없이 병합
================================================================
"""
import os
import sys
import time
import argparse
import sqlite3
import datetime

# pykrx 설치 확인
try:
    from pykrx import stock as krx
except ImportError:
    print("❌ pykrx 미설치 — 먼저 실행: pip install pykrx")
    sys.exit(1)


# ============================================================
# DB 헬퍼
# ============================================================
def get_db_path(script_dir: str, override: str = "") -> str:
    if override:
        return override
    return os.path.join(script_dir, "data", "backtest_data.db")


def get_codes_and_range(db_path: str) -> tuple:
    """daily_ohlcv에서 종목 목록 + 날짜 범위 자동 추출"""
    conn = sqlite3.connect(db_path, timeout=10)
    codes = [r[0] for r in conn.execute(
        "SELECT DISTINCT code FROM daily_ohlcv ORDER BY code").fetchall()]
    row = conn.execute(
        "SELECT MIN(date), MAX(date) FROM daily_ohlcv").fetchone()
    conn.close()
    return codes, row[0], row[1]


def init_flow_table(db_path: str):
    """daily_flow 테이블 보장 (없으면 생성)"""
    conn = sqlite3.connect(db_path, timeout=30)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS daily_flow (
            code         TEXT NOT NULL,
            date         TEXT NOT NULL,
            foreign_qty  INTEGER,
            orgn_qty     INTEGER,
            prsn_qty     INTEGER,
            PRIMARY KEY (code, date)
        );
        CREATE INDEX IF NOT EXISTS idx_flow_date ON daily_flow(date);
        CREATE TABLE IF NOT EXISTS flow_collect_log (
            collect_date TEXT NOT NULL,
            code         TEXT NOT NULL,
            rows_added   INTEGER,
            rows_total   INTEGER,
            PRIMARY KEY (collect_date, code)
        );
    """)
    conn.commit()
    conn.close()


def save_flow(db_path: str, code: str, rows: list, today_str: str) -> tuple:
    """INSERT OR IGNORE — 기존 데이터와 중복 없이 병합"""
    if not rows:
        return 0, 0
    conn = sqlite3.connect(db_path, timeout=30)
    conn.execute("PRAGMA journal_mode = WAL")

    before = conn.execute(
        "SELECT COUNT(*) FROM daily_flow WHERE code=?", (code,)
    ).fetchone()[0]

    conn.executemany(
        "INSERT OR IGNORE INTO daily_flow "
        "(code, date, foreign_qty, orgn_qty, prsn_qty) "
        "VALUES (?,?,?,?,?)",
        [(code, r["date"], r["foreign_qty"], r["orgn_qty"], r["prsn_qty"])
         for r in rows]
    )

    after = conn.execute(
        "SELECT COUNT(*) FROM daily_flow WHERE code=?", (code,)
    ).fetchone()[0]
    added = after - before

    conn.execute(
        "INSERT OR REPLACE INTO flow_collect_log "
        "(collect_date, code, rows_added, rows_total) VALUES (?,?,?,?)",
        (today_str, code, added, after)
    )
    conn.commit()
    conn.close()
    return added, after


# ============================================================
# pykrx 수급 수집
# ============================================================
def fetch_flow_pykrx(code: str, start: str, end: str) -> list:
    """
    pykrx로 종목 1개의 기간별 수급 수집.
    start/end: "YYYYMMDD" 형식

    반환: [{date, foreign_qty, orgn_qty, prsn_qty}, ...]
    """
    try:
        # 투자자별 순매수 수량
        # pykrx: get_market_trading_volume_by_investor
        # 컬럼: 기관합계, 기타법인, 개인, 외국인합계, 전체
        df = krx.get_market_net_purchases_of_equities_by_date(
            start, end, code
        )
        if df is None or df.empty:
            return []

        rows = []
        for date_idx, row in df.iterrows():
            date_str = date_idx.strftime("%Y-%m-%d")

            # pykrx 컬럼명 (버전마다 다를 수 있음 — 안전하게 처리)
            cols = list(df.columns)

            # 외국인 순매수 수량
            foreign_qty = 0
            for cname in ["외국인합계", "외국인", "Foreigners"]:
                if cname in cols:
                    foreign_qty = int(row[cname])
                    break

            # 기관 순매수 수량
            orgn_qty = 0
            for cname in ["기관합계", "기관", "Institutions"]:
                if cname in cols:
                    orgn_qty = int(row[cname])
                    break

            # 개인 순매수 수량
            prsn_qty = 0
            for cname in ["개인", "Individual", "Individuals"]:
                if cname in cols:
                    prsn_qty = int(row[cname])
                    break

            rows.append({
                "date":        date_str,
                "foreign_qty": foreign_qty,
                "orgn_qty":    orgn_qty,
                "prsn_qty":    prsn_qty,
            })

        return rows

    except Exception as e:
        return []


def fetch_flow_pykrx_v2(code: str, start: str, end: str) -> list:
    """
    pykrx API 버전 차이 대응 — v2 방식 시도.
    get_market_trading_volume_by_investor 사용.
    """
    try:
        df = krx.get_market_trading_volume_by_investor(
            start, end, code
        )
        if df is None or df.empty:
            return []

        rows = []
        cols = list(df.columns)

        # 순매수 = 매수 - 매도가 따로 없는 경우 직접 계산
        for date_idx, row in df.iterrows():
            date_str = date_idx.strftime("%Y-%m-%d")

            foreign_qty = 0
            orgn_qty = 0
            prsn_qty = 0

            for cname in cols:
                val = int(row[cname]) if not hasattr(row[cname], '__iter__') else 0
                cname_l = cname.lower()
                if "외국" in cname or "foreign" in cname_l:
                    foreign_qty = val
                elif "기관" in cname or "instit" in cname_l:
                    orgn_qty = val
                elif "개인" in cname or "individ" in cname_l:
                    prsn_qty = val

            rows.append({
                "date": date_str,
                "foreign_qty": foreign_qty,
                "orgn_qty": orgn_qty,
                "prsn_qty": prsn_qty,
            })

        return rows
    except Exception:
        return []


def fetch_one_safe(code: str, start_yyyymmdd: str, end_yyyymmdd: str) -> list:
    """v1 → v2 순으로 시도, 둘 다 실패하면 빈 리스트"""
    rows = fetch_flow_pykrx(code, start_yyyymmdd, end_yyyymmdd)
    if not rows:
        rows = fetch_flow_pykrx_v2(code, start_yyyymmdd, end_yyyymmdd)
    return rows


# ============================================================
# 메인
# ============================================================
def main():
    parser = argparse.ArgumentParser(
        description="pykrx로 과거 수급 데이터 일괄 수집 → backtest_data.db")
    parser.add_argument("--start", default="",
                        help="수집 시작일 YYYY-MM-DD (기본: ohlcv 최소일)")
    parser.add_argument("--end",   default="",
                        help="수집 종료일 YYYY-MM-DD (기본: 오늘)")
    parser.add_argument("--codes", default="",
                        help="쉼표구분 종목코드 (기본: DB의 모든 종목)")
    parser.add_argument("--max-codes", type=int, default=0,
                        help="최대 종목 수 (0=전체)")
    parser.add_argument("--db", default="",
                        help="DB 경로 (기본: data/backtest_data.db)")
    parser.add_argument("--sleep", type=float, default=1.0,
                        help="종목 간 대기 초 (KRX 부하 방지, 기본 1.0)")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    script_dir = os.path.dirname(os.path.abspath(__file__))
    db_path    = get_db_path(script_dir, args.db)

    if not os.path.exists(db_path):
        print(f"❌ DB 없음: {db_path}")
        sys.exit(1)

    # 종목 + 날짜 범위 결정
    all_codes, ohlcv_min, ohlcv_max = get_codes_and_range(db_path)

    if args.codes:
        codes = [c.strip() for c in args.codes.split(",") if c.strip()]
    else:
        codes = all_codes

    if args.max_codes and args.max_codes > 0:
        codes = codes[:args.max_codes]

    # 날짜 → YYYYMMDD (pykrx 형식)
    start_str = (args.start or ohlcv_min).replace("-", "")
    end_str   = (args.end   or datetime.datetime.now().strftime("%Y-%m-%d")).replace("-", "")

    print(f"📋 대상 종목: {len(codes)}개")
    print(f"📅 수집 기간: {start_str[:4]}-{start_str[4:6]}-{start_str[6:]} "
          f"~ {end_str[:4]}-{end_str[4:6]}-{end_str[6:]}")
    print(f"🗄️  DB: {db_path}")

    # 테이블 보장
    init_flow_table(db_path)

    today_str     = datetime.datetime.now().strftime("%Y-%m-%d")
    total_added   = 0
    total_fail    = 0
    t0            = time.time()

    for i, code in enumerate(codes, 1):
        try:
            rows = fetch_one_safe(code, start_str, end_str)

            if not rows:
                print(f"  ⚠️  {code}: 데이터 없음 ({i}/{len(codes)})")
                total_fail += 1
            else:
                added, total = save_flow(db_path, code, rows, today_str)
                total_added += added
                status = f"신규 {added}건 / 누적 {total}건"
                if args.verbose or added > 0:
                    print(f"  ✅ {code}: 받음 {len(rows)}건 / {status} ({i}/{len(codes)})")
                else:
                    # 간략 진행상황
                    if i % 10 == 0:
                        elapsed = time.time() - t0
                        eta = elapsed / i * (len(codes) - i)
                        print(f"  진행 {i}/{len(codes)} | "
                              f"신규 {total_added}건 | "
                              f"경과 {elapsed:.0f}s | "
                              f"ETA {eta:.0f}s")

        except Exception as e:
            print(f"  ❌ {code}: {e}")
            total_fail += 1

        time.sleep(args.sleep)

    # ── 결과 요약 ─────────────────────────────────
    elapsed = time.time() - t0
    conn = sqlite3.connect(db_path, timeout=10)
    total_rows = conn.execute("SELECT COUNT(*) FROM daily_flow").fetchone()[0]
    date_range = conn.execute(
        "SELECT MIN(date), MAX(date) FROM daily_flow").fetchone()
    conn.close()

    print(f"\n{'='*55}")
    print(f"✅ 완료 — 소요 {elapsed:.0f}초")
    print(f"   종목: {len(codes)}개 처리 / 실패 {total_fail}개")
    print(f"   신규 추가: {total_added:,}건")
    print(f"   daily_flow 누적: {total_rows:,}건")
    if date_range[0]:
        print(f"   기간: {date_range[0]} ~ {date_range[1]}")
    print(f"{'='*55}")
    print(f"\n다음 단계:")
    print(f"  python3 run_backtest_attribution.py --buy-score-min 70 --max-positions 3")


if __name__ == "__main__":
    main()
