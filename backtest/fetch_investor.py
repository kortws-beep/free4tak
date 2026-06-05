"""
fetch_investor.py — KIS API 기반 일별 수급 누적 수집기
================================================================
[이 파일이 하는 일]
KIS API의 inquire-investor TR(FHKST01010900)을 호출해서 종목별
일별 외국인/기관/개인 순매수 데이터를 수집합니다.

[KIS API의 한계 + 우리의 해결책]
- API는 "현재로부터 ~30일치"만 반환 (과거 특정 시점 조회 불가)
- 해결: 매일 1번씩 실행 → DB에 INSERT OR IGNORE 누적
- 30일 후엔 60일치, 90일 후엔 ~120일치 (중복 제외 누적)

[권장 운영 방법]
crontab에 등록 (매일 17:00, 장 마감 후):
  0 17 * * 1-5 cd /home/free4tak/k-bot/stock_bot/backtest && \
              /home/free4tak/k-bot/stock_bot/venv/bin/python3 fetch_investor.py

[사용]
  # KOSPI 50종목 일별 수급 받아서 누적
  python3 fetch_investor.py

  # 특정 종목만
  python3 fetch_investor.py --codes 005930,000660

[데이터 흐름]
  KIS API (FHKST01010900)
    → output 배열 (당일~30일 전 일별)
    → daily_flow 테이블에 INSERT OR IGNORE
    → 백테스트 시 feature_builder가 이 데이터를 자동으로 사용
"""
import os
import sys
import time
import argparse
import sqlite3
import datetime
from typing import Optional


# 프로젝트 루트 경로 자동 탐색 (kis_api 임포트용)
_here = os.path.dirname(os.path.abspath(__file__))
_candidates = [
    os.environ.get("K_BOT_ROOT"),
    _here,
    os.path.abspath(os.path.join(_here, "..")),
    os.path.join(os.path.abspath(os.path.join(_here, "..")), "core"),
]
for _root in _candidates:
    if _root and os.path.exists(os.path.join(_root, "kis_api.py")):
        if _root not in sys.path:
            sys.path.insert(0, _root)
        PROJECT_ROOT = _root
        break
else:
    raise ImportError("kis_api.py를 찾을 수 없음")


# ============================================================
# DB 스키마 (fetch_history와 호환 — daily_flow 테이블 그대로)
# ============================================================
def init_db(db_path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
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

        -- 수집 메타 (어느 날 누가 받았는지 추적)
        CREATE TABLE IF NOT EXISTS flow_collect_log (
            collect_date TEXT NOT NULL,
            code         TEXT NOT NULL,
            rows_added   INTEGER,
            rows_total   INTEGER,
            PRIMARY KEY (collect_date, code)
        );
    """)
    conn.commit()
    return conn


# ============================================================
# KIS API에서 종목 1개의 30일치 일별 수급 받기
# ============================================================
def fetch_one(api, code: str) -> list:
    """
    inquire-investor 호출 → 일별 데이터 리스트로 반환
    반환: [{date, foreign_qty, orgn_qty, prsn_qty}, ...] 최대 ~30건

    [핵심] 기존 get_investor_trend는 5일 누적만 반환하므로,
    여기서 raw output을 직접 파싱한다.
    """
    import requests

    api.refresh_token_if_needed()

    url = f"{api.base_url}/uapi/domestic-stock/v1/quotations/inquire-investor"
    headers = {
        "Content-Type":  "application/json",
        "authorization": f"Bearer {api.token}",
        "appKey":  api.appkey,
        "appSecret": api.secret,
        "tr_id": "FHKST01010900",
    }
    params = {"fid_cond_mrkt_div_code": "J", "fid_input_iscd": code}

    def safe_int(v):
        try: return int(str(v).replace(",", "") or 0)
        except: return 0

    try:
        res = requests.get(url, headers=headers, params=params, timeout=10).json()
        items = res.get("output", [])
        if not items:
            return []

        rows = []
        for item in items:
            # KIS API 응답 예시:
            # stck_bsop_date: "20260507"
            # frgn_ntby_qty: 외국인 순매수 수량
            # orgn_ntby_qty: 기관 순매수 수량
            # prsn_ntby_qty: 개인 순매수 수량
            date_str = item.get("stck_bsop_date", "")
            if not date_str or len(date_str) != 8:
                continue
            # YYYYMMDD → YYYY-MM-DD
            date_fmt = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"
            rows.append({
                "date":        date_fmt,
                "foreign_qty": safe_int(item.get("frgn_ntby_qty", 0)),
                "orgn_qty":    safe_int(item.get("orgn_ntby_qty", 0)),
                "prsn_qty":    safe_int(item.get("prsn_ntby_qty", 0)),
            })
        return rows
    except Exception as e:
        print(f"   ⚠️ {code}: {e}")
        return []


# ============================================================
# DB에 누적 저장 (INSERT OR IGNORE — 중복 자동 제외)
# ============================================================
def save_to_db(conn: sqlite3.Connection, code: str,
                rows: list, today_str: str) -> tuple:
    """
    반환: (새로 추가된 건수, DB 내 종목 총건수)
    """
    if not rows:
        return 0, 0

    # 기존 종목 건수
    before = conn.execute(
        "SELECT COUNT(*) FROM daily_flow WHERE code = ?",
        (code,)
    ).fetchone()[0]

    # INSERT OR IGNORE (date, code) 복합키 충돌 시 무시
    conn.executemany(
        "INSERT OR IGNORE INTO daily_flow "
        "(code, date, foreign_qty, orgn_qty, prsn_qty) "
        "VALUES (?, ?, ?, ?, ?)",
        [(code, r["date"], r["foreign_qty"],
          r["orgn_qty"], r["prsn_qty"]) for r in rows]
    )

    # 추가 후 종목 건수
    after = conn.execute(
        "SELECT COUNT(*) FROM daily_flow WHERE code = ?",
        (code,)
    ).fetchone()[0]

    added = after - before

    # 수집 로그
    conn.execute(
        "INSERT OR REPLACE INTO flow_collect_log "
        "(collect_date, code, rows_added, rows_total) "
        "VALUES (?, ?, ?, ?)",
        (today_str, code, added, after)
    )
    conn.commit()
    return added, after


# ============================================================
# 종목 리스트 — backtest_data.db의 daily_ohlcv에서 자동 추출
# ============================================================
def get_codes_from_db(db_path: str) -> list:
    """이미 OHLCV가 수집된 종목들만 대상 (백테스트 일관성)"""
    if not os.path.exists(db_path):
        return []
    conn = sqlite3.connect(db_path, timeout=10)
    rows = conn.execute(
        "SELECT DISTINCT code FROM daily_ohlcv ORDER BY code"
    ).fetchall()
    conn.close()
    return [r[0] for r in rows]


# ============================================================
# 메인
# ============================================================
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--codes", default="",
                        help="쉼표구분 종목코드 (비우면 DB의 모든 종목)")
    parser.add_argument("--db",
                        default=os.path.join(
                            os.path.dirname(__file__),
                            "data", "backtest_data.db"))
    parser.add_argument("--sleep", type=float, default=0.3,
                        help="API 호출 간격 (KIS 1초 20건 제한)")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    # KIS API 임포트 (지연)
    try:
        from kis_api import KisAPI
    except ImportError as e:
        print(f"❌ kis_api 임포트 실패: {e}")
        sys.exit(1)

    # .env 명시적 로드 (backtest/ 하위에서 실행 시)
    from dotenv import load_dotenv
    for _ep in [os.path.join(os.path.dirname(__file__), '..', '.env'),
                os.path.join(os.path.dirname(__file__), '.env')]:
        if os.path.exists(_ep):
            load_dotenv(_ep, override=True)
            break
    print(f"🔑 KIS API 토큰 발급 중...")
    api = KisAPI()
    if not api.token:
        print("❌ 토큰 발급 실패 — .env의 KIS_APPKEY/KIS_SECRET 확인")
        sys.exit(1)

    # 종목 결정
    if args.codes:
        codes = [c.strip() for c in args.codes.split(",") if c.strip()]
    else:
        codes = get_codes_from_db(args.db)
    if not codes:
        print("❌ 종목 없음 — fetch_history_fdr.py 먼저 실행하세요")
        sys.exit(1)
    print(f"📋 대상 종목 {len(codes)}개")

    # DB 준비
    conn = init_db(args.db)
    today_str = datetime.datetime.now().strftime("%Y-%m-%d")

    # 수집
    print(f"📡 수급 데이터 수집 시작 (오늘 = {today_str})")
    t0 = time.time()
    total_added = 0
    fail_count  = 0
    for i, code in enumerate(codes, 1):
        try:
            rows = fetch_one(api, code)
            added, total = save_to_db(conn, code, rows, today_str)
            total_added += added
            if args.verbose or added > 0:
                print(f"   {code}: 받음 {len(rows)}건 / "
                      f"신규 {added}건 / 누적 {total}건")
            if not rows:
                fail_count += 1
        except Exception as e:
            print(f"❌ {code} 오류: {e}")
            fail_count += 1
        if i % 10 == 0:
            elapsed = time.time() - t0
            print(f"   진행 {i}/{len(codes)} | 경과 {elapsed:.0f}s")
        time.sleep(args.sleep)

    elapsed = time.time() - t0

    # 결과 요약
    cur = conn.execute("SELECT COUNT(*) FROM daily_flow")
    total_rows = cur.fetchone()[0]
    cur = conn.execute(
        "SELECT MIN(date), MAX(date) FROM daily_flow")
    date_min, date_max = cur.fetchone()

    print(f"\n✅ 완료 — {len(codes)}종목 / 실패 {fail_count} / 신규 {total_added}건"
          f" / 소요 {elapsed:.0f}초")
    print(f"📊 daily_flow 누적 — 총 {total_rows:,}건")
    if date_min:
        print(f"📅 기간 — {date_min} ~ {date_max}")

    # 종목별 누적 건수 분포
    cur = conn.execute("""
        SELECT
          CASE WHEN cnt >= 90 THEN '90+'
               WHEN cnt >= 60 THEN '60-89'
               WHEN cnt >= 30 THEN '30-59'
               WHEN cnt >= 10 THEN '10-29'
               ELSE '<10'
          END AS bucket,
          COUNT(*) AS n
        FROM (
          SELECT code, COUNT(*) AS cnt FROM daily_flow GROUP BY code
        )
        GROUP BY bucket
        ORDER BY bucket DESC
    """)
    print(f"\n📊 종목별 누적 일수 분포:")
    for row in cur.fetchall():
        print(f"   {row[0]:>6}일: {row[1]}종목")

    conn.close()


if __name__ == "__main__":
    main()
