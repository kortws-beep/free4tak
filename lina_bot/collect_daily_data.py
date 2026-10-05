"""
collect_daily_data.py
---------------------
KisAPI를 재사용해 kr_stock_daily_data 테이블에
일별 주가(종가/거래량) + 외인/기관 수급을 수집합니다.

실행:
    python collect_daily_data.py
"""

import os
import re
import time
import sqlite3
import sys
from datetime import datetime, timedelta
from dotenv import load_dotenv, find_dotenv  # 💡 find_dotenv 추가

# ── 환경변수 & 경로 (대장님 전용 stock_bot .env 자동 연동) ──────
load_dotenv(find_dotenv(), override=True)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH  = os.path.join(BASE_DIR, "kr_theme_finance.db")
_PROJECT_ROOT = os.path.dirname(BASE_DIR)
for _p in (os.path.join(_PROJECT_ROOT, "core"), os.path.join(_PROJECT_ROOT, "interface"), os.path.join(_PROJECT_ROOT, "bots"), _PROJECT_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ── 💡 한투 API 임포트 수정 (신형 클래스명 반영) ──────────────────
# sbot2.py와 동일하게 kis_api 파일에서 KoreaInvestmentAPI를 가져옵니다.
try:
    from kis_api import KoreaInvestmentAPI as KisAPI
except ImportError:
    # 혹시 모를 구형 명칭 백업용 방어 코드
    from kis_api import KisAPI


# ── 종목명 파싱 ────────────────────────────────────────────────
def parse_stock(raw_name: str) -> tuple[str, str] | tuple[None, None]:
    """
    'エ이블씨엔씨KOSPI 078520' → ('에이블씨엔씨', '078520')
    파싱 실패 시 (None, None)
    """
    m = re.search(r"(\d{6})$", raw_name.strip())
    if not m:
        print(f"  ⚠️  코드 파싱 실패: '{raw_name}' → 건너뜀")
        return None, None
    code      = m.group(1)
    pure_name = re.sub(r"\s*KOS(?:PI|DAQ)\s*\d{6}$", "", raw_name).strip()
    return pure_name, code


# ── 스키마 보강 (기존 DB에도 안전하게 적용) ─────────────────────
def ensure_ohlc_columns():
    """
    ★ kr_stock_daily_data에 시가/고가/저가 컬럼 추가 (2026-06-27)
    기존에는 종가만 저장해서, ATR 등 변동성 계산이 종가 기반
    근사치로만 가능했음. KIS API(get_daily_ohlc)는 이미 open/high/low를
    주고 있었는데 활용을 안 하고 있던 것 — 이제부터 다 저장한다.
    """
    conn = sqlite3.connect(DB_PATH)
    # ★ 2026-10-06: trade_value(실제 거래대금, 원) 추가 — 그동안 거래대금이 필요한
    #   곳(3개월수급 2000억/300억 판정, daybot 매집필터 등)은 종가×거래량 근사치를 썼음.
    for col in ("open_price", "high_price", "low_price", "trade_value"):
        try:
            conn.execute(f"ALTER TABLE kr_stock_daily_data ADD COLUMN {col} INTEGER")
        except sqlite3.OperationalError:
            pass  # 이미 컬럼이 있으면 무시 (재실행 시 정상)
    conn.commit()
    conn.close()


# ── DB upsert ──────────────────────────────────────────────────
def upsert_daily_data(rows: list[dict]) -> int:
    """rows: [{"date","stock_name","open","high","low","close","volume","trade_value","foreign_net","inst_net"}, ...]"""
    if not rows:
        return 0
    conn   = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.executemany(
        """
        INSERT INTO kr_stock_daily_data
            (date, stock_name, open_price, high_price, low_price, close_price,
             volume, trade_value, foreign_net_buy, institution_net_buy)
        VALUES (:date, :stock_name, :open, :high, :low, :close,
                :volume, :trade_value, :foreign_net, :inst_net)
        ON CONFLICT(date, stock_name) DO UPDATE SET
            open_price          = excluded.open_price,
            high_price          = excluded.high_price,
            low_price           = excluded.low_price,
            close_price         = excluded.close_price,
            volume              = excluded.volume,
            trade_value         = excluded.trade_value,
            foreign_net_buy     = excluded.foreign_net_buy,
            institution_net_buy = excluded.institution_net_buy,
            updated_at          = CURRENT_TIMESTAMP
        """,
        rows,
    )
    saved = cursor.rowcount
    conn.commit()
    conn.close()
    return len(rows)   # executemany rowcount는 DB별로 다르므로 len 사용


# ── 핵심 수집 함수 ─────────────────────────────────────────────
def collect_stock(api: KisAPI, stock_name: str, code: str, days: int,
                  end_date: str = None) -> int:
    """
    단일 종목 수집.
    - 주가/거래량 : get_daily_ohlc(days)
    - 수급        : get_investor_trend() → 전일 기준 1건만 제공되므로
                    오늘 날짜로 1건 저장 (배치 수집 시 매일 실행 권장)
    반환: 저장된 행 수
    """
    # ── 1. 일봉 OHLC (종가/거래량) ──────────────────────────────
    ohlc = api.get_daily_ohlc(code, days=days, end_date=end_date)
    if not ohlc:
        # 디버그: 원본 응답 확인
        import requests as _req, datetime as _dt
        _url = f"{api.base_url}/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"
        _headers = {"Content-Type": "application/json",
                    "authorization": f"Bearer {api.token}",
                    "appKey": api.appkey, "appSecret": api.secret,
                    "tr_id": "FHKST03010100"}
        _end   = _dt.datetime.now().strftime("%Y%m%d")
        _start = (_dt.datetime.now() - _dt.timedelta(days=90)).strftime("%Y%m%d")
        _params = {"fid_cond_mrkt_div_code": "J", "fid_input_iscd": code,
                   "fid_input_date_1": _start, "fid_input_date_2": _end,
                   "fid_period_div_code": "D", "fid_org_adj_prc": "0"}
        try:
            _res = _req.get(_url, headers=_headers, params=_params, timeout=5).json()
            print(f"      🔍 API 응답: rt_cd={_res.get('rt_cd')} msg={_res.get('msg1')} output2길이={len(_res.get('output2') or [])}")
        except Exception as _e:
            print(f"      🔍 API 호출 자체 실패: {_e}")
        return 0

    # ── 2. 수급 (5일 누적 + 전일 단일) ─────────────────────────
    inv_cache = {}
    inv = api.get_investor_trend(code, inv_cache)

    # 수급은 전일 마감 기준 1건 → "오늘 이전의 가장 최근 거래일"에 매핑.
    # ★ 2026-10-06: 기존엔 달력상 어제에 붙여서, 월요일 수집이면 금요일 수급이
    #   일요일 날짜로 가 버려졌음(일요일엔 일봉 행이 없음).
    today_str  = datetime.today().strftime("%Y-%m-%d")
    prev_dates = [c.get("date") for c in ohlc if c.get("date") and c["date"] < today_str]
    inv_map: dict[str, dict] = {}
    if inv and prev_dates:
        inv_map[prev_dates[0]] = {
            "foreign_net": inv.get("foreign_today", 0),
            "inst_net":    inv.get("orgn_today",    0),
        }

    # ── 3. 날짜 = API가 준 실제 거래일 ───────────────────────────
    # ★ 2026-10-06: 기존엔 "오늘부터 평일을 거꾸로 센 날짜"를 붙여서, 08시대
    #   수집(최신 봉=전 거래일)이면 어제 데이터가 오늘 날짜로 저장됐고, 평일
    #   공휴일이 끼면 그만큼 더 밀렸음. 이제 stck_bsop_date를 그대로 쓴다.
    rows = []
    for candle in ohlc:
        date_str = candle.get("date")
        if not date_str:
            continue
        inv_day  = inv_map.get(date_str, {})

        rows.append({
            "date":        date_str,
            "stock_name":  stock_name,
            "open":        candle.get("open", 0),
            "high":        candle.get("high", 0),
            "low":         candle.get("low", 0),
            "close":       candle["close"],
            "volume":      candle["volume"],
            "trade_value": candle.get("trade_value") or None,
            "foreign_net": inv_day.get("foreign_net"),
            "inst_net":    inv_day.get("inst_net"),
        })

    # ★ 2026-10-06: 이번에 받은 구간 안에서 실제 거래일이 아닌 날짜 행 정리 —
    #   예전 날짜계산 방식이 평일 공휴일 등에 만들어 둔 엉뚱한 행.
    if rows:
        dates = [r["date"] for r in rows]
        conn = sqlite3.connect(DB_PATH)
        conn.execute(
            f"DELETE FROM kr_stock_daily_data WHERE stock_name=? AND date BETWEEN ? AND ? "
            f"AND date NOT IN ({','.join('?' * len(dates))})",
            [stock_name, min(dates), max(dates), *dates],
        )
        # 최신 봉 이후~오늘 전 사이의 행도 가짜(휴장일에 옛 코드가 전 거래일 데이터를
        # 그날 날짜로 저장한 것 — 2026-10-05 대체공휴일 실사례). 최신구간 수집일 때만.
        if not end_date:
            conn.execute(
                "DELETE FROM kr_stock_daily_data WHERE stock_name=? AND date > ? AND date < ?",
                (stock_name, max(dates), today_str),
            )
        conn.commit(); conn.close()
    return upsert_daily_data(rows)


# ── 전체 수집 ──────────────────────────────────────────────────
def collect_all(days: int = 30, delay: float = 0.5, end_date: str = None) -> None:
    """
    kr_theme_stocks의 모든 종목을 수집합니다.

    Args:
        days  : 수집할 일봉 수 (기본 30일)
        delay : 종목 간 API 호출 대기 시간(초)
    """
    appkey = os.getenv("KIS_APPKEY")
    secret = os.getenv("KIS_SECRET")
    if not appkey or not secret:
        print("❌ .env에 KIS_APPKEY / KIS_SECRET 이 없습니다.")
        return

    print(f"\n🚀 [수급 수집] DB: {DB_PATH}")
    ensure_ohlc_columns()  # ★ open/high/low_price 컬럼 보장 (2026-06-27)
    api = KisAPI(appkey=appkey, secret=secret)

    conn   = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT stock_name FROM kr_theme_stocks")
    raw_names = [r[0] for r in cursor.fetchall()]
    conn.close()

    if not raw_names:
        print("⚠️  kr_theme_stocks에 종목이 없습니다.")
        return

    print(f"📋 수집 대상: {len(raw_names)}개 종목 | 최근 {days}일")
    print(f"🔑 토큰 상태: {'✅ 정상' if api.token else '❌ 빈 토큰 — 발급 실패'} ({api.token[:20] + '...' if api.token else 'EMPTY'})\n")
    if not api.token:
        print("토큰 발급 실패. KIS_APPKEY / KIS_SECRET 값을 확인하세요.")
        return
    total = 0

    for raw in raw_names:
        name, code = parse_stock(raw)
        if not code:
            continue

        api.refresh_token_if_needed()
        print(f"  📈 {name} ({code}) 수집 중...")

        try:
            saved = collect_stock(api, name, code, days, end_date=end_date)
            total += saved
            print(f"      ✅ {saved}일치 저장")
        except Exception as e:
            print(f"      ❌ 오류: {e}")

        time.sleep(delay)

    print(f"\n🎉 수집 완료! 총 {total}건 저장됨")


# ── 단일 종목 테스트 ───────────────────────────────────────────
def collect_one(raw_name: str, days: int = 30) -> None:
    """예: collect_one('삼성SDI KOSPI 006400')"""
    appkey = os.getenv("KIS_APPKEY")
    secret = os.getenv("KIS_SECRET")
    ensure_ohlc_columns()  # ★ open/high/low_price 컬럼 보장 (2026-06-27)
    api    = KisAPI(appkey=appkey, secret=secret)

    name, code = parse_stock(raw_name)
    if not code:
        return

    print(f"📈 단일 수집: {name} ({code}) | 최근 {days}일")
    saved = collect_stock(api, name, code, days)
    print(f"✅ {saved}일치 저장 완료")


# ── 실행 ───────────────────────────────────────────────────────
if __name__ == "__main__":
    # ★ 2026-10-06: `python collect_daily_data.py 100` 처럼 일수를 주면 그만큼
    #   다시 받아 덮어씀 — 날짜계산 버그로 밀려 저장된 과거 데이터 1회 교정용.
    # `python collect_daily_data.py backfill` — 실제 거래대금이 있는 가장 오래된
    #   날짜 이전 100봉을 추가 수집(API 1회 최대 100봉이라 3개월수급 E조건의
    #   119거래일을 채우려면 필요). 여러 번 돌리면 그만큼 더 과거로 내려간다.
    if len(sys.argv) > 1 and sys.argv[1] == "backfill":
        _c = sqlite3.connect(DB_PATH)
        _oldest = _c.execute("SELECT MIN(date) FROM kr_stock_daily_data "
                             "WHERE trade_value IS NOT NULL").fetchone()[0]
        _c.close()
        if not _oldest:
            print("❌ trade_value가 있는 행이 없음 — 먼저 `python collect_daily_data.py 100` 실행")
            sys.exit(1)
        _end = (datetime.strptime(_oldest, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y%m%d")
        print(f"⏪ 과거 구간 추가수집: {_end} 이전 100봉")
        collect_all(days=100, delay=0.3, end_date=_end)
    else:
        _days = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 1
        collect_all(days=_days, delay=0.3)

    # 단일 테스트:
    # collect_one("삼성SDI KOSPI 006400", days=10)
