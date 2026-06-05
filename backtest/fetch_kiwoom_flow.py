"""
fetch_kiwoom_flow.py — 키움 ka10045로 외국인/기관 일별 수급 수집
================================================================
[이 파일이 하는 일]
키움 API의 ka10045(종목별기관매매추이) TR로 종목별 일별 외국인+기관
순매수 데이터를 수집합니다.

[핵심 장점]
- 외국인 + 기관 데이터를 한 번에 받음 (ka10008 + ka10009 → 1회 호출)
- 기간 지정 가능 (strt_dt ~ end_dt) → 1년치 한 번에 수집 가능
- 키움 토큰은 사용자의 kiwoom_api.py에서 자동 사용

[사용]
  python3 fetch_kiwoom_flow.py --start 2024-05-08 --end 2026-05-08
  python3 fetch_kiwoom_flow.py --codes 005930,000660
"""
import os
import sys
import time
import json
import argparse
import sqlite3
import datetime
from typing import Optional

import requests
from dotenv import load_dotenv


# 프로젝트 루트 경로 자동 탐색
_here = os.path.dirname(os.path.abspath(__file__))
_candidates = [
    os.environ.get("K_BOT_ROOT"),
    _here,
    os.path.abspath(os.path.join(_here, "..")),
]
PROJECT_ROOT = None
for _root in _candidates:
    if _root and os.path.exists(os.path.join(_root, "kiwoom_api.py")):
        if _root not in sys.path:
            sys.path.insert(0, _root)
        PROJECT_ROOT = _root
        break

# ★ .env 로드 — 부모 디렉토리(stock_bot)에 있음
if PROJECT_ROOT:
    env_path = os.path.join(PROJECT_ROOT, ".env")
    if os.path.exists(env_path):
        load_dotenv(env_path)
        print(f"📂 .env 로드: {env_path}")
load_dotenv()  # 현재 폴더에도 있으면 추가 로드
# ============================================================
# DB 초기화 (fetch_history와 호환)
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
    """)
    conn.commit()
    return conn


# ============================================================
# 키움 ka10045 호출
# ============================================================
def fetch_kiwoom_flow(token: str, code: str,
                       start: str, end: str) -> list:
    """
    ka10045 호출 — 종목별 외국인/기관 일별 순매매수량.

    Parameters:
      token: 키움 API 토큰
      code:  종목코드 (6자리)
      start: 시작일 'YYYYMMDD'
      end:   종료일 'YYYYMMDD'

    Returns:
      [{date, foreign_qty, orgn_qty, close, volume}, ...]
    """
    url = "https://api.kiwoom.com/api/dostk/frgnistt"

    headers = {
        "Content-Type":  "application/json;charset=UTF-8",
        "authorization": f"Bearer {token}",
        "cont-yn":       "N",
        "next-key":      "",
        "api-id":        "ka10045",
    }
    body = {
        "stk_cd":            code,
        "strt_dt":           start,
        "end_dt":            end,
        "orgn_prsm_unp_tp":  "1",   # 1=매수단가
        "for_prsm_unp_tp":   "1",
    }

    def safe_int(v):
        try:
            s = str(v).replace(",", "").replace("+", "").strip()
            return int(s) if s and s != "-" else 0
        except:
            return 0

    rows = []
    cont_yn = "N"
    next_key = ""
    pages = 0
    max_pages = 10  # 안전장치 (1페이지 = ~600일 추정)

    while True:
        pages += 1
        if pages > max_pages:
            break

        headers["cont-yn"]  = cont_yn
        headers["next-key"] = next_key

        try:
            res = requests.post(url, headers=headers,
                                data=json.dumps(body), timeout=15)
            data = res.json()
        except Exception as e:
            print(f"   ⚠️ {code} 요청 실패: {e}")
            break

        # 응답 구조: stk_orgn_trde_trnsn (LIST)
        items = data.get("stk_orgn_trde_trnsn", [])
        for item in items:
            date_str = item.get("dt", "")
            if not date_str or len(date_str) != 8:
                continue
            date_fmt = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"
            rows.append({
                "date":        date_fmt,
                "foreign_qty": safe_int(item.get("for_daly_nettrde_qty", 0)),
                "orgn_qty":    safe_int(item.get("orgn_daly_nettrde_qty", 0)),
                "close":       safe_int(item.get("close_pric", 0)),
                "volume":      safe_int(item.get("trde_qty", 0)),
            })

        # 연속조회 체크 (응답 헤더는 res.headers에 있음)
        cont_yn  = res.headers.get("cont-yn",  "N")
        next_key = res.headers.get("next-key", "")
        if cont_yn != "Y" or not next_key:
            break

    return rows


# ============================================================
# DB 누적 저장
# ============================================================
def save_to_db(conn: sqlite3.Connection, code: str, rows: list) -> int:
    if not rows:
        return 0
    before = conn.execute(
        "SELECT COUNT(*) FROM daily_flow WHERE code = ?", (code,)
    ).fetchone()[0]
    conn.executemany(
        "INSERT OR IGNORE INTO daily_flow "
        "(code, date, foreign_qty, orgn_qty, prsn_qty) "
        "VALUES (?, ?, ?, ?, 0)",
        [(code, r["date"], r["foreign_qty"], r["orgn_qty"]) for r in rows]
    )
    after = conn.execute(
        "SELECT COUNT(*) FROM daily_flow WHERE code = ?", (code,)
    ).fetchone()[0]
    conn.commit()
    return after - before


# ============================================================
# 종목 리스트
# ============================================================
def get_codes_from_db(db_path: str) -> list:
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
    parser.add_argument("--start", default="2024-05-08")
    parser.add_argument("--end",
                        default=datetime.datetime.now().strftime("%Y-%m-%d"))
    parser.add_argument("--codes", default="")
    parser.add_argument("--db",
                        default=os.path.join(
                            os.path.dirname(__file__),
                            "data", "backtest_data.db"))
    parser.add_argument("--sleep", type=float, default=0.3)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    # 키움 API 임포트 (토큰 받기 위해)
    try:
        from kiwoom_api import KiwoomAPI
    except ImportError as e:
        print(f"❌ kiwoom_api 임포트 실패: {e}")
        sys.exit(1)

    print(f"🔑 키움 토큰 발급 중...")
    kiwoom = KiwoomAPI()
    token = kiwoom.get_token()
    if not token:
        print("❌ 키움 토큰 발급 실패 — KIWOOM_APPKEY/KIWOOM_SECRETKEY 확인")
        sys.exit(1)
    print(f"✅ 토큰 OK")

    # 종목 리스트
    if args.codes:
        codes = [c.strip() for c in args.codes.split(",") if c.strip()]
    else:
        codes = get_codes_from_db(args.db)
    if not codes:
        print("❌ 종목 없음 — fetch_history_fdr.py 먼저 실행")
        sys.exit(1)
    print(f"📋 대상 종목 {len(codes)}개")

    # 날짜 변환 (YYYY-MM-DD → YYYYMMDD)
    start_kw = args.start.replace("-", "")
    end_kw   = args.end.replace("-", "")

    conn = init_db(args.db)
    print(f"📡 수급 수집 시작: {args.start} ~ {args.end}")

    t0 = time.time()
    total_added = 0
    fail_count = 0
    for i, code in enumerate(codes, 1):
        try:
            rows = fetch_kiwoom_flow(token, code, start_kw, end_kw)
            added = save_to_db(conn, code, rows)
            total_added += added
            if args.verbose or rows:
                print(f"   {code}: 받음 {len(rows)}건 / 신규 {added}건")
            if not rows:
                fail_count += 1
        except Exception as e:
            print(f"❌ {code} 오류: {e}")
            fail_count += 1
        if i % 10 == 0:
            print(f"   진행 {i}/{len(codes)} | "
                  f"경과 {time.time()-t0:.0f}s")
        time.sleep(args.sleep)

    print(f"\n✅ 완료 — {len(codes)}종목 / 실패 {fail_count} / "
          f"신규 {total_added}건 / {time.time()-t0:.0f}초")

    cur = conn.execute("SELECT COUNT(*) FROM daily_flow")
    total = cur.fetchone()[0]
    cur = conn.execute("SELECT MIN(date), MAX(date) FROM daily_flow")
    dmin, dmax = cur.fetchone()
    print(f"📊 daily_flow 누적: {total:,}건 ({dmin} ~ {dmax})")
    conn.close()


if __name__ == "__main__":
    main()