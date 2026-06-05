"""
fetch_stocks.py — 백테스트용 종목 리스트 + 일봉 데이터 수집
================================================================
한투 psearch(조건검색) + 일봉 API로 backtest_data.db 구성

사용법:
  python3 fetch_stocks.py          # 종목 리스트 조회 + 일봉 수집
  python3 fetch_stocks.py --list   # 종목 리스트만 확인
  python3 fetch_stocks.py --ohlcv  # 일봉만 수집 (종목 이미 있을 때)
"""
import os, sys, time, sqlite3, argparse, datetime, requests
from dotenv import load_dotenv
load_dotenv()

# ── 설정 ──────────────────────────────────────────────────
DATA_DB    = "backtest_data.db"
START_DATE = "2024-01-01"
END_DATE   = datetime.date.today().strftime("%Y-%m-%d")
MAX_STOCKS = 300   # 수집할 종목 수
SLEEP_SEC  = 0.12  # API 호출 간격

# 한투 API
BASE_URL = "https://openapi.koreainvestment.com:9443"


# ============================================================
# DB 초기화
# ============================================================
def init_db():
    conn = sqlite3.connect(DATA_DB, timeout=15)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS stock_list (
            code TEXT PRIMARY KEY,
            name TEXT,
            market TEXT,
            sector TEXT DEFAULT ''
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS daily_ohlcv (
            code        TEXT NOT NULL,
            date        TEXT NOT NULL,
            open        INTEGER DEFAULT 0,
            high        INTEGER DEFAULT 0,
            low         INTEGER DEFAULT 0,
            close       INTEGER DEFAULT 0,
            volume      INTEGER DEFAULT 0,
            value       REAL    DEFAULT 0,
            change_rate REAL    DEFAULT 0,
            PRIMARY KEY (code, date)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_code ON daily_ohlcv(code)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_date ON daily_ohlcv(date)")
    conn.commit()
    conn.close()
    print(f"✅ DB 초기화 ({DATA_DB})")


# ============================================================
# 토큰
# ============================================================
_token = ""
def get_token() -> str:
    global _token
    if _token:
        return _token
    appkey = os.getenv("KIS_APPKEY", "")
    secret = os.getenv("KIS_SECRET", "")
    res = requests.post(
        f"{BASE_URL}/oauth2/tokenP",
        json={"grant_type": "client_credentials",
              "appkey": appkey, "appsecret": secret},
        timeout=10
    ).json()
    _token = res.get("access_token", "")
    print(f"{'✅' if _token else '❌'} 한투 토큰")
    return _token

def headers(tr_id: str) -> dict:
    appkey = os.getenv("KIS_APPKEY", "")
    secret = os.getenv("KIS_SECRET", "")
    return {
        "authorization": f"Bearer {get_token()}",
        "appkey": appkey, "appsecret": secret,
        "tr_id": tr_id,
    }


# ============================================================
# 1. 종목 리스트 수집 (3가지 방법 병행)
# ============================================================
def fetch_by_psearch() -> list:
    """한투 psearch 조건검색 → 종목 코드 수집"""
    hts_id = os.getenv("KIS_HTS_ID", "")
    codes  = []

    try:
        # 조건검색 목록
        r = requests.get(
            f"{BASE_URL}/uapi/domestic-stock/v1/quotations/psearch-title",
            headers={**headers("HHKST03900300"), "custtype": "P"},
            params={"user_id": hts_id},
            timeout=5
        ).json()

        items = r.get("output", [])
        print(f"  📋 psearch 검색식: {len(items)}개")
        for item in items:
            print(f"    [{item.get('seq')}] {item.get('title')}")

        # 각 검색식 결과 조회
        for item in items:
            seq = item.get("seq", "")
            title = item.get("title", "")
            try:
                r2 = requests.get(
                    f"{BASE_URL}/uapi/domestic-stock/v1/quotations/psearch-result",
                    headers={**headers("HHKST03900400"), "custtype": "P"},
                    params={"user_id": hts_id, "seq": seq},
                    timeout=5
                ).json()
                out = r2.get("output2", r2.get("output", []))
                for s in out:
                    code = (s.get("mksc_shrn_iscd", "") or
                            s.get("stk_code", "") or
                            s.get("9001", "")).strip()
                    name = (s.get("hts_kor_isnm", "") or
                            s.get("stk_name", "") or
                            s.get("302", "")).strip()
                    if code and code.isdigit() and code not in [c[0] for c in codes]:
                        codes.append((code, name, "psearch"))
                print(f"  ✅ [{seq}]{title}: {len(out)}개")
                time.sleep(0.2)
            except Exception as e:
                print(f"  ⚠️ [{seq}]{title} 오류: {e}")

    except Exception as e:
        print(f"  ⚠️ psearch 오류: {e}")

    return codes


def fetch_by_volume_rank() -> list:
    """한투 거래량 급증 순위 → 종목 수집"""
    codes = []
    # 여러 tr_id 시도
    for tr_id in ["FHPST01710000", "FHKST01710000"]:
        for mrkt in ["J", "Q"]:
            try:
                r = requests.get(
                    f"{BASE_URL}/uapi/domestic-stock/v1/ranking/volume",
                    headers=headers(tr_id),
                    params={
                        "FID_COND_MRKT_DIV_CODE":  mrkt,
                        "FID_COND_SCR_DIV_CODE":   "20171",
                        "FID_INPUT_ISCD":           "0000",
                        "FID_DIV_CLS_CODE":         "0",
                        "FID_BLNG_CLS_CODE":        "0",
                        "FID_TRGT_CLS_CODE":        "111111111",
                        "FID_TRGT_EXLS_CLS_CODE":   "000000",
                        "FID_INPUT_PRICE_1":        "2000",
                        "FID_INPUT_PRICE_2":        "9999999",
                        "FID_VOL_CNT":              "100000",
                        "FID_INPUT_DATE_1":         "",
                    },
                    timeout=5
                )
                if not r.content:
                    continue
                data = r.json()
                if data.get("rt_cd") != "0":
                    continue
                for s in data.get("output", []):
                    code = s.get("mksc_shrn_iscd", "").strip()
                    name = s.get("hts_kor_isnm", "").strip()
                    if code and code.isdigit():
                        codes.append((code, name, "volume"))
                print(f"  ✅ 거래량순위 {mrkt} ({tr_id}): {len(codes)}개")
                time.sleep(0.1)
            except Exception:
                pass
    return codes


def fetch_by_market_cap() -> list:
    """한투 시가총액 상위 → 종목 수집"""
    codes = []
    for mrkt in ["J", "Q"]:
        try:
            r = requests.get(
                f"{BASE_URL}/uapi/domestic-stock/v1/ranking/market-cap",
                headers=headers("FHPST01720000"),
                params={
                    "FID_COND_MRKT_DIV_CODE": mrkt,
                    "FID_COND_SCR_DIV_CODE":  "20172",
                    "FID_INPUT_ISCD":         "0000",
                    "FID_DIV_CLS_CODE":       "0",
                    "FID_BLNG_CLS_CODE":      "0",
                    "FID_TRGT_CLS_CODE":      "111111111",
                    "FID_TRGT_EXLS_CLS_CODE": "000000",
                    "FID_INPUT_PRICE_1":      "2000",
                    "FID_INPUT_PRICE_2":      "9999999",
                    "FID_INPUT_DATE_1":       "",
                },
                timeout=5
            )
            if not r.content:
                continue
            data = r.json()
            if data.get("rt_cd") != "0":
                continue
            for s in data.get("output", []):
                code = s.get("mksc_shrn_iscd", "").strip()
                name = s.get("hts_kor_isnm", "").strip()
                if code and code.isdigit():
                    codes.append((code, name, "mktcap"))
            print(f"  ✅ 시가총액 {mrkt}: {len(codes)}개")
            time.sleep(0.1)
        except Exception as e:
            print(f"  ⚠️ 시가총액 {mrkt}: {e}")
    return codes


def fetch_stock_list() -> list:
    """3가지 방법으로 종목 수집 후 합치기"""
    print("\n📋 종목 리스트 수집...")
    all_codes = {}

    # 1) psearch
    print("\n[1] psearch 조건검색")
    for code, name, src in fetch_by_psearch():
        if code not in all_codes:
            all_codes[code] = (name, src)

    # 2) 거래량 순위
    print("\n[2] 거래량 순위")
    for code, name, src in fetch_by_volume_rank():
        if code not in all_codes:
            all_codes[code] = (name, src)

    # 3) 시가총액 (부족하면)
    if len(all_codes) < 100:
        print("\n[3] 시가총액 상위")
        for code, name, src in fetch_by_market_cap():
            if code not in all_codes:
                all_codes[code] = (name, src)

    # DB 저장
    conn = sqlite3.connect(DATA_DB)
    for code, (name, src) in all_codes.items():
        conn.execute(
            "INSERT OR IGNORE INTO stock_list VALUES (?,?,?,?)",
            (code, name, src, "")
        )
    conn.commit()
    conn.close()

    result = [(code, name, src) for code, (name, src) in all_codes.items()]
    print(f"\n✅ 총 {len(result)}개 종목 수집")
    return result[:MAX_STOCKS]


# ============================================================
# 2. 일봉 데이터 수집
# ============================================================
def fetch_ohlcv(code: str, name: str,
                start: str, end: str) -> int:
    """종목별 일봉 수집"""
    try:
        s = start.replace("-", "")
        e = end.replace("-", "")
        r = requests.get(
            f"{BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
            headers=headers("FHKST03010100"),
            params={
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD":         code,
                "FID_INPUT_DATE_1":       s,
                "FID_INPUT_DATE_2":       e,
                "FID_PERIOD_DIV_CODE":    "D",
                "FID_ORG_ADJ_PRC":        "0",
            },
            timeout=10
        ).json()

        candles = r.get("output2", [])
        if not candles:
            return 0

        conn = sqlite3.connect(DATA_DB, timeout=15)
        count = 0
        for c in candles:
            dt = c.get("stck_bsop_date", "")
            if not dt or len(dt) != 8:
                continue
            date = f"{dt[:4]}-{dt[4:6]}-{dt[6:]}"
            cls  = int(c.get("stck_clpr", 0) or 0)
            if cls <= 0:
                continue
            conn.execute("""
                INSERT OR REPLACE INTO daily_ohlcv
                    (code, date, open, high, low, close, volume, value, change_rate)
                VALUES (?,?,?,?,?,?,?,?,?)
            """, (
                code, date,
                int(c.get("stck_oprc", 0) or 0),
                int(c.get("stck_hgpr", 0) or 0),
                int(c.get("stck_lwpr", 0) or 0),
                cls,
                int(c.get("acml_vol",  0) or 0),
                round(float(c.get("acml_tr_pbmn", 0) or 0) / 1e8, 2),
                round(float(c.get("prdy_ctrt",    0) or 0), 2),
            ))
            count += 1
        conn.commit()
        conn.close()
        return count

    except Exception as e:
        print(f"  ⚠️ {code} 오류: {e}")
        return 0


def fetch_all_ohlcv(stocks: list, start: str, end: str):
    """전체 종목 일봉 수집"""
    print(f"\n📥 일봉 수집: {start} ~ {end} | {len(stocks)}개 종목")
    conn = sqlite3.connect(DATA_DB)
    total = 0

    for i, (code, name, _) in enumerate(stocks):
        # 이미 충분히 수집된 건 스킵
        cnt = conn.execute(
            "SELECT COUNT(*) FROM daily_ohlcv WHERE code=? AND date>=?",
            (code, start)
        ).fetchone()[0]

        if cnt >= 400:
            print(f"  ⏭️ [{i+1}/{len(stocks)}] {code}({name}) 스킵 ({cnt}일)")
            continue

        n = fetch_ohlcv(code, name, start, end)
        total += n
        print(f"  ✅ [{i+1}/{len(stocks)}] {code}({name}) {n}일 저장")
        time.sleep(SLEEP_SEC)

    conn.close()

    # 최종 통계
    conn = sqlite3.connect(DATA_DB)
    rows  = conn.execute("SELECT COUNT(*) FROM daily_ohlcv WHERE date>=?", (start,)).fetchone()[0]
    cds   = conn.execute("SELECT COUNT(DISTINCT code) FROM daily_ohlcv WHERE date>=?", (start,)).fetchone()[0]
    conn.close()
    print(f"\n🎉 수집 완료! 종목:{cds}개 | 총:{rows:,}일")


# ============================================================
# 진입점
# ============================================================
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--list",  action="store_true", help="종목 리스트만 확인")
    parser.add_argument("--ohlcv", action="store_true", help="일봉만 수집")
    parser.add_argument("--start", default=START_DATE)
    parser.add_argument("--end",   default=END_DATE)
    args = parser.parse_args()

    init_db()

    if args.list:
        stocks = fetch_stock_list()
        print("\n📋 수집된 종목:")
        for code, name, src in stocks[:20]:
            print(f"  {code} {name} [{src}]")
        print(f"  ... 총 {len(stocks)}개")
        return

    if args.ohlcv:
        # DB에서 종목 로드
        conn   = sqlite3.connect(DATA_DB)
        stocks = [(r[0], r[1], r[2]) for r in
                  conn.execute("SELECT code, name, market FROM stock_list").fetchall()]
        conn.close()
        if not stocks:
            print("❌ 종목 없음 — 먼저 --list 실행")
            return
        fetch_all_ohlcv(stocks, args.start, args.end)
        return

    # 기본: 종목 수집 + 일봉 수집
    stocks = fetch_stock_list()
    if stocks:
        fetch_all_ohlcv(stocks, args.start, args.end)
    else:
        print("❌ 종목 수집 실패")

if __name__ == "__main__":
    main()
