"""
fetch_flow_krx.py — KRX 데이터포털 로그인 세션 기반 수급 수집
================================================================
[이 파일이 하는 일]
KRX 데이터포털(data.krx.co.kr) 로그인 세션 쿠키를 이용해서
투자자별 거래실적(개별종목) 데이터를 수집합니다.

[사용 전 준비]
1. data.krx.co.kr 브라우저 로그인
2. F12 → Network → getJsonData.cmd 요청의 Cookie 값 복사
3. .env 파일에 저장:
   KRX_COOKIE=__smVisitorID=xxx; JSESSIONID=xxx; mdc.client_session=true

[사용법]
  # .env에 KRX_COOKIE 설정 후 실행
  python3 fetch_flow_krx.py

  # 기간 지정
  python3 fetch_flow_krx.py --start 2024-05-01 --end 2026-05-18

  # 특정 종목만
  python3 fetch_flow_krx.py --codes 005930,000660

  # 쿠키 직접 입력
  python3 fetch_flow_krx.py --cookie "__smVisitorID=xxx; JSESSIONID=xxx; mdc.client_session=true"

[KRX API 정보]
  URL: https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd
  bld: dbms/MDC/STAT/standard/MDCSTAT02301
  반환: 외국인/기관/개인 순매수 거래량

[주의]
  - 세션 쿠키는 수 시간 후 만료 → 만료 시 재로그인 후 쿠키 갱신
  - KRX 서버 부하 방지: 종목 간 1초 대기
  - 하루 최대 조회 건수 제한 있을 수 있음 (로그인 계정 정책)
================================================================
"""
import os
import sys
import time
import json
import argparse
import sqlite3
import datetime
from datetime import datetime as dt, timedelta
import requests

# .env 로드
_here = os.path.dirname(os.path.abspath(__file__))
for _ep in [
    os.path.join(_here, ".env"),
    os.path.join(_here, "..", ".env"),
]:
    if os.path.exists(_ep):
        from dotenv import load_dotenv
        load_dotenv(_ep, override=True)
        break


# ============================================================
# KRX API
# ============================================================
KRX_URL = "https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd"

HEADERS_BASE = {
    "Accept":           "application/json, text/javascript, */*; q=0.01",
    "Accept-Encoding":  "gzip, deflate, br, zstd",
    "Accept-Language":  "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "Connection":       "keep-alive",
    "Content-Type":     "application/x-www-form-urlencoded; charset=UTF-8",
    "Host":             "data.krx.co.kr",
    "Origin":           "https://data.krx.co.kr",
    "Referer":          "https://data.krx.co.kr/contents/MDC/MDI/mdiLoader/index.cmd?menuId=MDC0201020103",
    "Sec-Fetch-Dest":   "empty",
    "Sec-Fetch-Mode":   "cors",
    "Sec-Fetch-Site":   "same-origin",
    "User-Agent":       "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:149.0) Gecko/20100101 Firefox/149.0",
    "X-Requested-With": "XMLHttpRequest",
}

# 종목코드 → KRX isuCd(표준코드) 변환 캐시
_isu_cache: dict = {}


def load_all_isu_codes(cookie: str) -> dict:
    """
    KRX 전체 종목 표준코드를 한 번에 조회해서 캐시에 저장.
    반환: {short_code: full_code}
    ex) {"005930": "KR7005930003", "005935": "KR7005931001", ...}
    """
    global _isu_cache
    if _isu_cache:
        return _isu_cache

    try:
        headers = {**HEADERS_BASE, "Cookie": cookie}
        res = requests.post(
            KRX_URL,
            headers=headers,
            data={
                "bld":           "dbms/comm/finder/finder_stkisu",
                "locale":        "ko_KR",
                "market":        "ALL",
                "searchText":    "",
                "pageFirstCall": "Y",
            },
            timeout=15,
        )
        items = res.json().get("block1", [])
        for item in items:
            short = item.get("short_code", "")
            full  = item.get("full_code", "")
            if short and full:
                _isu_cache[short] = full
        print(f"   ✅ KRX 표준코드 로드: {len(_isu_cache)}종목")
    except Exception as e:
        print(f"   ⚠️ 표준코드 로드 실패: {e}")

    return _isu_cache


def get_isu_cd(code: str, cookie: str) -> str:
    """종목코드(6자리) → KRX 표준코드(KR7XXXXXXXXX) 변환."""
    if not _isu_cache:
        load_all_isu_codes(cookie)
    return _isu_cache.get(code, f"KR7{code}003")  # 폴백


def fetch_one(code: str, isu_cd: str,
              start_yyyymmdd: str, end_yyyymmdd: str,
              cookie: str) -> list:
    """
    KRX 투자자별 거래실적(개별종목) 조회.
    반환: [{date, foreign_qty, orgn_qty, prsn_qty}, ...]
    """
    headers = {**HEADERS_BASE, "Cookie": cookie}

    data = {
        "bld":         "dbms/MDC/STAT/standard/MDCSTAT02302",  # 일별 조회
        "locale":      "ko_KR",
        "inqTpCd":     "2",    # 2=일별
        "trdVolVal":   "1",    # 1=거래량(수량)
        "askBid":      "3",    # 3=순매수
        "isuCd":       isu_cd,
        "isuCd2":      code,
        "strtDd":      start_yyyymmdd,
        "endDd":       end_yyyymmdd,
        "share":       "1",
        "money":       "1",
        "csvxls_isNo": "false",
    }

    try:
        res = requests.post(KRX_URL, headers=headers, data=data, timeout=15)
        if res.status_code != 200:
            return []

        body = res.json()
        rows_raw = body.get("output") or []
        if not rows_raw:
            return []

        def safe_int(v):
            try:
                return int(str(v).replace(",", "").strip() or "0")
            except:
                return 0

        rows = []
        for item in rows_raw:
            # 날짜: "2026/01/08" → "2026-01-08"
            date_raw = str(item.get("TRD_DD", "")).replace("/", "").replace("-", "").strip()
            if len(date_raw) != 8:
                continue
            date_str = f"{date_raw[:4]}-{date_raw[4:6]}-{date_raw[6:]}"

            # TRDVAL1=외국인, TRDVAL2=기관, TRDVAL3=개인 (vol=1, askBid=3 기준)
            foreign_qty = safe_int(item.get("TRDVAL1", 0))
            orgn_qty    = safe_int(item.get("TRDVAL2", 0))
            prsn_qty    = safe_int(item.get("TRDVAL3", 0))

            rows.append({
                "date":        date_str,
                "foreign_qty": foreign_qty,
                "orgn_qty":    orgn_qty,
                "prsn_qty":    prsn_qty,
            })

        return rows

    except Exception as e:
        return []


# ============================================================
# DB 헬퍼 (fetch_investor.py와 동일 스키마)
# ============================================================
def get_db_path(override: str = "") -> str:
    if override:
        return override
    return os.path.join(_here, "data", "backtest_data.db")


def init_flow_table(db_path: str):
    conn = sqlite3.connect(db_path, timeout=30)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS daily_flow (
            code        TEXT NOT NULL,
            date        TEXT NOT NULL,
            foreign_qty INTEGER,
            orgn_qty    INTEGER,
            prsn_qty    INTEGER,
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
    if not rows:
        return 0, 0
    conn = sqlite3.connect(db_path, timeout=30)
    conn.execute("PRAGMA journal_mode = WAL")

    before = conn.execute(
        "SELECT COUNT(*) FROM daily_flow WHERE code=?", (code,)
    ).fetchone()[0]

    conn.executemany(
        "INSERT OR IGNORE INTO daily_flow "
        "(code, date, foreign_qty, orgn_qty, prsn_qty) VALUES (?,?,?,?,?)",
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


def get_codes_and_range(db_path: str) -> tuple:
    conn = sqlite3.connect(db_path, timeout=10)
    codes = [r[0] for r in conn.execute(
        "SELECT DISTINCT code FROM daily_ohlcv ORDER BY code").fetchall()]
    row = conn.execute(
        "SELECT MIN(date), MAX(date) FROM daily_ohlcv").fetchone()
    conn.close()
    return codes, row[0], row[1]


# ============================================================
# 메인
# ============================================================
def main():
    parser = argparse.ArgumentParser(
        description="KRX 로그인 세션으로 과거 수급 데이터 수집")
    parser.add_argument("--start",  default="",
                        help="수집 시작일 YYYY-MM-DD (기본: ohlcv 최소일)")
    parser.add_argument("--end",    default="",
                        help="수집 종료일 YYYY-MM-DD (기본: 오늘)")
    parser.add_argument("--codes",  default="",
                        help="쉼표구분 종목코드 (기본: DB의 모든 종목)")
    parser.add_argument("--max-codes", type=int, default=0,
                        help="최대 종목 수 (0=전체)")
    parser.add_argument("--cookie", default="",
                        help="KRX 세션 쿠키 (미입력 시 .env의 KRX_COOKIE 사용)")
    parser.add_argument("--db",     default="",
                        help="DB 경로 (기본: data/backtest_data.db)")
    parser.add_argument("--sleep",  type=float, default=1.0,
                        help="종목 간 대기 초 (기본 1.0)")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--test",   action="store_true",
                        help="삼성전자 1건만 테스트 후 종료")
    args = parser.parse_args()

    # 쿠키 결정
    cookie = args.cookie or os.getenv("KRX_COOKIE", "")
    if not cookie:
        print("❌ KRX 쿠키 없음")
        print("   방법1: python3 fetch_flow_krx.py --cookie '쿠키값'")
        print("   방법2: .env에 KRX_COOKIE=쿠키값 저장")
        sys.exit(1)

    db_path   = get_db_path(args.db)
    today_str = datetime.datetime.now().strftime("%Y-%m-%d")

    # 테스트 모드
    if args.test:
        print("🧪 테스트 모드 — 삼성전자 3일치")
        isu_cd = get_isu_cd("005930", cookie)
        print(f"   isuCd: {isu_cd}")
        rows = fetch_one("005930", isu_cd, "20260101", "20260110", cookie)
        if rows:
            print(f"   ✅ {len(rows)}건 수신")
            for r in rows[:5]:
                print(f"      {r}")
        else:
            print("   ❌ 데이터 없음 — 쿠키 만료 또는 API 파라미터 오류")
            print("   응답 구조 확인이 필요합니다. --verbose 옵션으로 재시도하세요.")
        return

    if not os.path.exists(db_path):
        print(f"❌ DB 없음: {db_path}")
        sys.exit(1)

    # 종목 + 날짜 범위
    all_codes, ohlcv_min, ohlcv_max = get_codes_and_range(db_path)

    if args.codes:
        codes = [c.strip() for c in args.codes.split(",") if c.strip()]
    else:
        codes = all_codes

    if args.max_codes and args.max_codes > 0:
        codes = codes[:args.max_codes]

    start_fmt = (args.start or ohlcv_min).replace("-", "")
    end_fmt   = (args.end   or today_str).replace("-", "")

    print(f"📋 대상 종목: {len(codes)}개")
    print(f"📅 수집 기간: {start_fmt[:4]}-{start_fmt[4:6]}-{start_fmt[6:]} "
          f"~ {end_fmt[:4]}-{end_fmt[4:6]}-{end_fmt[6:]}")
    print(f"🗄️  DB: {db_path}")

    init_flow_table(db_path)

    # ★ 표준코드 캐시 미리 로드 (루프 전)
    load_all_isu_codes(cookie)

    total_added = 0
    total_fail  = 0
    t0          = time.time()

    for i, code in enumerate(codes, 1):
        try:
            isu_cd = get_isu_cd(code, cookie)

            # ★ 180일씩 분할 요청 (KRX API 기간 제한 대응)
            rows = []
            s = dt.strptime(start_fmt, "%Y%m%d")
            e = dt.strptime(end_fmt,   "%Y%m%d")
            while s <= e:
                chunk_end = min(s + timedelta(days=179), e)
                chunk_rows = fetch_one(code, isu_cd,
                                       s.strftime("%Y%m%d"),
                                       chunk_end.strftime("%Y%m%d"), cookie)
                if chunk_rows:
                    rows.extend(chunk_rows)
                s = chunk_end + timedelta(days=1)

            if not rows:
                print(f"  ⚠️  {code}: 데이터 없음 ({i}/{len(codes)})")
                total_fail += 1
            else:
                added, total = save_flow(db_path, code, rows, today_str)
                total_added += added
                if args.verbose or added > 0:
                    print(f"  ✅ {code}: {len(rows)}건 수신 / "
                          f"신규 {added}건 / 누적 {total}건 ({i}/{len(codes)})")
                elif i % 10 == 0:
                    elapsed = time.time() - t0
                    eta = elapsed / i * (len(codes) - i)
                    print(f"  진행 {i}/{len(codes)} | "
                          f"신규 {total_added}건 | "
                          f"경과 {elapsed:.0f}s | ETA {eta:.0f}s")

        except Exception as e:
            print(f"  ❌ {code}: {e}")
            total_fail += 1

        time.sleep(args.sleep)

    # 결과 요약
    elapsed = time.time() - t0
    conn = sqlite3.connect(db_path, timeout=10)
    total_rows = conn.execute("SELECT COUNT(*) FROM daily_flow").fetchone()[0]
    date_range = conn.execute(
        "SELECT MIN(date), MAX(date) FROM daily_flow").fetchone()
    conn.close()

    print(f"\n{'='*55}")
    print(f"✅ 완료 — 소요 {elapsed:.0f}초")
    print(f"   종목: {len(codes)}개 / 실패: {total_fail}개")
    print(f"   신규 추가: {total_added:,}건")
    print(f"   daily_flow 누적: {total_rows:,}건")
    if date_range[0]:
        print(f"   기간: {date_range[0]} ~ {date_range[1]}")
    print(f"{'='*55}")
    print(f"\n다음 단계:")
    print(f"  K_BOT_ROOT=~/k-bot/stock_bot python3 run_backtest_attribution.py \\")
    print(f"    --buy-score-min 70 --max-positions 3 \\")
    print(f"    --start 2025-08-01 --end 2026-05-01")


if __name__ == "__main__":
    main()
