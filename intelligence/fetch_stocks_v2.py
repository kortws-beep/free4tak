"""
fetch_stocks_v2.py — 백테스트용 종목 리스트 + 일봉 수집
================================================================
한투 거래량순위(volume-rank) + 일봉 API

사용법:
  python3 fetch_stocks_v2.py          # 종목 수집 + 일봉 수집
  python3 fetch_stocks_v2.py --list   # 종목만 확인
  python3 fetch_stocks_v2.py --ohlcv  # 일봉만 수집
"""
import os, sys, time, sqlite3, argparse, datetime, requests
from dotenv import load_dotenv
load_dotenv()

DATA_DB    = "backtest_data.db"
START_DATE = "2024-01-01"
END_DATE   = datetime.date.today().strftime("%Y-%m-%d")
MAX_STOCKS = 200
SLEEP_SEC  = 0.12
BASE_URL   = "https://openapi.koreainvestment.com:9443"


# ============================================================
# DB 초기화
# ============================================================
def init_db():
    conn = sqlite3.connect(DATA_DB, timeout=15)
    conn.execute("PRAGMA journal_mode=WAL")
    # ★ 기존 테이블 컬럼 불일치 시 재생성
    cur = conn.execute("PRAGMA table_info(stock_list)").fetchall()
    if cur and len(cur) != 3:
        conn.execute("DROP TABLE IF EXISTS stock_list")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS stock_list (
            code TEXT PRIMARY KEY, name TEXT, market TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS daily_ohlcv (
            code TEXT NOT NULL, date TEXT NOT NULL,
            open INTEGER DEFAULT 0, high INTEGER DEFAULT 0,
            low  INTEGER DEFAULT 0, close INTEGER DEFAULT 0,
            volume INTEGER DEFAULT 0, value REAL DEFAULT 0,
            change_rate REAL DEFAULT 0,
            PRIMARY KEY (code, date)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_code ON daily_ohlcv(code)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_date ON daily_ohlcv(date)")
    conn.commit(); conn.close()
    print(f"✅ DB 초기화 ({DATA_DB})")


# ============================================================
# 토큰
# ============================================================
_token = ""
_token_issued_at = 0

def get_token() -> str:
    global _token, _token_issued_at
    # ★ 20분마다 재발급 (한투 토큰 유효기간 대비 안전하게)
    if _token and (time.time() - _token_issued_at) < 1200:
        return _token
    res = requests.post(
        f"{BASE_URL}/oauth2/tokenP",
        json={"grant_type": "client_credentials",
              "appkey": os.getenv("KIS_APPKEY",""),
              "appsecret": os.getenv("KIS_SECRET","")},
        timeout=10
    ).json()
    _token = res.get("access_token", "")
    if _token:
        _token_issued_at = time.time()
    print(f"{'✅' if _token else '❌'} 한투 토큰 {'발급' if _token else '실패'}")
    return _token

def hdrs(tr_id: str) -> dict:
    return {
        "authorization": f"Bearer {get_token()}",
        "appkey":        os.getenv("KIS_APPKEY",""),
        "appsecret":     os.getenv("KIS_SECRET",""),
        "tr_id":         tr_id,
        "custtype":      "P",   # ★ 개인 필수
    }


# ============================================================
# 1. 거래량 순위로 종목 수집 (코스피 + 코스닥 각 30개)
# ============================================================
def fetch_volume_rank() -> list:
    """
    한투 거래량순위 API (FHPST01710000)
    ★ 수정: URL /quotations/volume-rank
    """
    codes = []
    url   = f"{BASE_URL}/uapi/domestic-stock/v1/quotations/volume-rank"

    for mrkt, label in [("J", "코스피"), ("NX", "코스닥(NXT)")]:
        try:
            params = {
                "FID_COND_MRKT_DIV_CODE":  mrkt,
                "FID_COND_SCR_DIV_CODE":   "20171",
                "FID_INPUT_ISCD":          "0000",
                "FID_DIV_CLS_CODE":        "0",
                "FID_BLNG_CLS_CODE":       "0",
                "FID_TRGT_CLS_CODE":       "111111111",
                "FID_TRGT_EXLS_CLS_CODE":  "000000",   # ★ 6자리
                "FID_INPUT_PRICE_1":       "2000",     # 2000원 이상
                "FID_INPUT_PRICE_2":       "",
                "FID_VOL_CNT":             "",
                "FID_INPUT_DATE_1":        "0",
            }
            r    = requests.get(url, headers=hdrs("FHPST01710000"),
                               params=params, timeout=10)
            data = r.json()
            rt   = data.get("rt_cd")
            msg  = data.get("msg1","")[:40]
            out  = data.get("output", [])
            print(f"  [{label}] rt={rt} | {msg} | {len(out)}개")

            for item in out:
                code = item.get("mksc_shrn_iscd","").strip()
                name = item.get("hts_kor_isnm","").strip()
                if code and code.isdigit():
                    codes.append((code, name, mrkt))
            time.sleep(0.2)

        except Exception as e:
            print(f"  ⚠️ [{label}] 오류: {e}")

    return codes


# ============================================================
# 2. 주요 종목 하드코딩 (거래량 API 보완용)
#    코스피200 + 코스닥150 핵심 종목
# ============================================================
CORE_STOCKS = [
    # 코스피 대형주
    ("005930","삼성전자","J"),("000660","SK하이닉스","J"),
    ("035420","NAVER","J"),("005380","현대차","J"),
    ("000270","기아","J"),("051910","LG화학","J"),
    ("006400","삼성SDI","J"),("028050","삼성엔지니어링","J"),
    ("012450","한화에어로스페이스","J"),("047810","한국항공우주","J"),
    ("064350","현대로템","J"),("034020","두산에너빌리티","J"),
    ("009830","한화솔루션","J"),("034220","LG디스플레이","J"),
    ("011200","HMM","J"),("035720","카카오","J"),
    ("015760","한국전력","J"),("028670","팬오션","J"),
    ("047050","포스코인터내셔널","J"),("003670","포스코퓨처엠","J"),
    ("055550","신한지주","J"),("105560","KB금융","J"),
    ("138040","메리츠금융지주","J"),("316140","우리금융지주","J"),
    ("024110","기업은행","J"),("323410","카카오뱅크","J"),
    ("030200","KT","J"),("032640","LG유플러스","J"),
    ("003490","대한항공","J"),("036460","한국가스공사","J"),
    ("010140","삼성중공업","J"),("137310","에스디바이오센서","J"),
    ("207940","삼성바이오로직스","J"),("068270","셀트리온","J"),
    ("000100","유한양행","J"),("326030","SK바이오팜","J"),
    ("011070","LG이노텍","J"),("066570","LG전자","J"),
    ("096770","SK이노베이션","J"),("009150","삼성전기","J"),
    ("018260","삼성에스디에스","J"),("010950","S-Oil","J"),
    ("017670","SK텔레콤","J"),("030000","제일기획","J"),
    ("086790","하나금융지주","J"),("039490","키움증권","J"),
    ("251270","넷마블","J"),("036570","엔씨소프트","J"),
    ("259960","크래프톤","J"),("112610","씨에스윈드","J"),
    # 코스닥 대형주/주도주 (★ 대폭 추가)
    ("247540","에코프로비엠","NX"),("086520","에코프로","NX"),
    ("196170","알테오젠","NX"),("091990","셀트리온헬스케어","NX"),
    ("041510","에스엠","NX"),("035900","JYP Ent.","NX"),
    ("122870","와이지엔터테인먼트","NX"),("018290","레이","NX"),
    ("039030","이오테크닉스","NX"),("060310","3S","NX"),
    ("357780","솔브레인","NX"),("108320","LX세미콘","NX"),
    ("240810","원익IPS","NX"),("036810","에프에스티","NX"),
    ("403870","HPSP","NX"),("214150","클래시스","NX"),
    ("009420","한올바이오파마","NX"),("145020","휴젤","NX"),
    ("041960","블리자드코리아","NX"),("263720","디앤씨미디어","NX"),
    ("058470","리노공업","NX"),("054040","한국컴퓨터","NX"),
    ("031430","신성통상","NX"),("095660","네오위즈","NX"),
    ("293490","카카오게임즈","NX"),("112040","위메이드","NX"),
    # ★ 단타봇 실제 거래 종목 (코스닥)
    ("090710","휴림로봇","NX"),("076610","해성옵틱스","NX"),
    ("012860","모베이스전자","NX"),("264850","이랜시스","NX"),
    ("086960","MDS테크","NX"),("223250","드림씨아이에스","NX"),
    ("203650","드림시큐리티","NX"),("332570","PS일렉트로닉스","NX"),
    ("125490","한라캐스트","NX"),("319400","현대무벡스","NX"),
    ("229000","젠큐릭스","NX"),("253840","수젠텍","NX"),
    ("137310","에스디바이오센서","NX"),("078150","HB테크놀러지","NX"),
    # ★ 코스닥 주도 섹터
    ("014930","성문전자","NX"),("066900","디피씨","NX"),
    ("950140","잉글우드랩","NX"),("044180","KBI메탈","NX"),
    ("041920","메디아나","NX"),("048830","메디오젠","NX"),
    ("065130","탑엔지니어링","NX"),("104480","티케이케미칼","NX"),
    ("950130","엑세스바이오","NX"),("950160","코오롱티슈진","NX"),
    ("053800","안랩","NX"),("079940","가비아","NX"),
    ("030520","한글과컴퓨터","NX"),("060250","NHN KCP","NX"),
    ("035720","카카오","NX"),("035600","KG이니시스","NX"),
    ("140860","파크시스템스","NX"),("039440","에스티아이","NX"),
    ("079550","LIG넥스원","NX"),("237690","에스티팜","NX"),
    ("298040","효성중공업","NX"),("298000","효성티앤씨","NX"),
    ("096530","씨젠","NX"),("206650","엔케이맥스","NX"),
]


# ============================================================
# 3. 종목 리스트 통합
# ============================================================
def fetch_stock_list() -> list:
    print("\n📋 종목 리스트 수집...")

    # 1) 거래량 순위 (최신 장중 종목)
    print("\n[거래량 순위]")
    vol_stocks = fetch_volume_rank()

    # 2) 핵심 종목 합치기
    seen  = {c[0] for c in vol_stocks}
    extra = [(c, n, m) for c, n, m in CORE_STOCKS if c not in seen]
    all_stocks = vol_stocks + extra

    # 중복 제거
    final, final_codes = [], set()
    for code, name, mrkt in all_stocks:
        if code not in final_codes:
            final.append((code, name, mrkt))
            final_codes.add(code)

    # DB 저장
    conn = sqlite3.connect(DATA_DB)
    for code, name, mrkt in final:
        conn.execute("INSERT OR REPLACE INTO stock_list VALUES (?,?,?)",
                    (code, name, mrkt))
    conn.commit(); conn.close()

    print(f"\n✅ 총 {len(final)}개 종목 수집")
    print(f"   거래량순위: {len(vol_stocks)}개 + 핵심종목: {len(extra)}개")
    return final[:MAX_STOCKS]


# ============================================================
# 4. 일봉 수집
# ============================================================
def _fetch_ohlcv_chunk(code: str, chunk_start: str, chunk_end: str, conn) -> int:
    """한 구간(최대 100일) 일봉 수집"""
    try:
        r = requests.get(
            f"{BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
            headers=hdrs("FHKST03010100"),
            params={
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD":         code,
                "FID_INPUT_DATE_1":       chunk_start.replace("-",""),
                "FID_INPUT_DATE_2":       chunk_end.replace("-",""),
                "FID_PERIOD_DIV_CODE":    "D",
                "FID_ORG_ADJ_PRC":        "0",
            },
            timeout=10
        ).json()
        candles = r.get("output2", [])
        if not candles: return 0

        count = 0
        for c in candles:
            dt  = c.get("stck_bsop_date","")
            cls = int(c.get("stck_clpr", 0) or 0)
            if not dt or len(dt) != 8 or cls <= 0: continue
            date = f"{dt[:4]}-{dt[4:6]}-{dt[6:]}"
            conn.execute("""
                INSERT OR REPLACE INTO daily_ohlcv
                    (code,date,open,high,low,close,volume,value,change_rate)
                VALUES (?,?,?,?,?,?,?,?,?)
            """, (
                code, date,
                int(c.get("stck_oprc",0) or 0),
                int(c.get("stck_hgpr",0) or 0),
                int(c.get("stck_lwpr",0) or 0),
                cls,
                int(c.get("acml_vol", 0) or 0),
                round(float(c.get("acml_tr_pbmn",0) or 0)/1e8, 2),
                round(float(c.get("prdy_ctrt",   0) or 0), 2),
            ))
            count += 1
        conn.commit()
        return count
    except Exception as e:
        return 0


def fetch_ohlcv(code: str, name: str, start: str, end: str) -> int:
    """
    ★ 구간 분할 수집 — 한투 API는 1회 최대 100일
    3개월(약 65거래일)씩 나눠서 전체 기간 수집
    """
    try:
        from datetime import date, timedelta
        s_date = date.fromisoformat(start)
        e_date = date.fromisoformat(end)

        conn   = sqlite3.connect(DATA_DB, timeout=15)
        total  = 0
        chunk  = timedelta(days=90)  # 3개월씩

        cur = s_date
        while cur <= e_date:
            c_end   = min(cur + chunk, e_date)
            c_start = cur.isoformat()
            c_end_s = c_end.isoformat()

            # 이미 수집된 구간 스킵
            exists = conn.execute(
                "SELECT COUNT(*) FROM daily_ohlcv WHERE code=? AND date BETWEEN ? AND ?",
                (code, c_start, c_end_s)
            ).fetchone()[0]

            if exists >= 50:  # 50일 이상 있으면 스킵
                cur = c_end + timedelta(days=1)
                continue

            n = _fetch_ohlcv_chunk(code, c_start, c_end_s, conn)
            total += n
            time.sleep(SLEEP_SEC)
            cur = c_end + timedelta(days=1)

        conn.close()
        return total
    except Exception as e:
        print(f"  ⚠️ {code} 오류: {e}")
        return 0


def fetch_all_ohlcv(stocks: list, start: str, end: str):
    print(f"\n📥 일봉 수집: {start} ~ {end} | {len(stocks)}개 종목")
    conn  = sqlite3.connect(DATA_DB)
    total = 0

    for i, (code, name, _) in enumerate(stocks):
        cnt = conn.execute(
            "SELECT COUNT(*) FROM daily_ohlcv WHERE code=? AND date>=?",
            (code, start)
        ).fetchone()[0]
        if cnt >= 450:  # ★ 2년치(약 500거래일) 이상이면 스킵
            print(f"  ⏭️ [{i+1}/{len(stocks)}] {code}({name}) 스킵({cnt}일)")
            continue

        n = fetch_ohlcv(code, name, start, end)
        total += n
        status = "✅" if n > 0 else "⚠️"
        print(f"  {status} [{i+1}/{len(stocks)}] {code}({name}) {n}일")
        time.sleep(SLEEP_SEC)

    conn.close()

    conn  = sqlite3.connect(DATA_DB)
    rows  = conn.execute(
        "SELECT COUNT(*) FROM daily_ohlcv WHERE date>=?", (start,)
    ).fetchone()[0]
    cds   = conn.execute(
        "SELECT COUNT(DISTINCT code) FROM daily_ohlcv WHERE date>=?", (start,)
    ).fetchone()[0]
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
        print("\n📋 수집 종목 (앞 30개):")
        for code, name, mrkt in stocks[:30]:
            label = "코스피" if mrkt == "J" else "코스닥"
            print(f"  {code} {name:<20} [{label}]")
        print(f"\n  ... 총 {len(stocks)}개")
        return

    if args.ohlcv:
        conn   = sqlite3.connect(DATA_DB)
        stocks = [(r[0],r[1],r[2]) for r in
                  conn.execute("SELECT code,name,market FROM stock_list").fetchall()]
        conn.close()
        if not stocks:
            print("❌ 종목 없음 — 먼저 실행해주세요")
            return
        fetch_all_ohlcv(stocks, args.start, args.end)
        return

    # 기본: 종목 수집 + 일봉 수집
    stocks = fetch_stock_list()
    if stocks:
        fetch_all_ohlcv(stocks, args.start, args.end)

if __name__ == "__main__":
    main()
