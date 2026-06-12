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
from datetime import datetime, timedelta
from dotenv import load_dotenv
from kis_api import KisAPI

# ── 환경변수 & 경로 ────────────────────────────────────────────
load_dotenv(dotenv_path=os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"), override=True)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH  = os.path.join(BASE_DIR, "kr_theme_finance.db")


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


# ── DB upsert ──────────────────────────────────────────────────
def upsert_daily_data(rows: list[dict]) -> int:
    """rows: [{"date","stock_name","close","volume","foreign_net","inst_net"}, ...]"""
    if not rows:
        return 0
    conn   = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.executemany(
        """
        INSERT INTO kr_stock_daily_data
            (date, stock_name, close_price, volume, foreign_net_buy, institution_net_buy)
        VALUES (:date, :stock_name, :close, :volume, :foreign_net, :inst_net)
        ON CONFLICT(date, stock_name) DO UPDATE SET
            close_price         = excluded.close_price,
            volume              = excluded.volume,
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
def collect_stock(api: KisAPI, stock_name: str, code: str, days: int) -> int:
    """
    단일 종목 수집.
    - 주가/거래량 : get_daily_ohlc(days)
    - 수급        : get_investor_trend() → 전일 기준 1건만 제공되므로
                    오늘 날짜로 1건 저장 (배치 수집 시 매일 실행 권장)
    반환: 저장된 행 수
    """
    # ── 1. 일봉 OHLC (종가/거래량) ──────────────────────────────
    ohlc = api.get_daily_ohlc(code, days=days)
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

    # 수급은 전일 마감 기준 1건 → 어제 날짜로 매핑
    yesterday = (datetime.today() - timedelta(days=1)).strftime("%Y-%m-%d")
    inv_map: dict[str, dict] = {}
    if inv:
        inv_map[yesterday] = {
            "foreign_net": inv.get("foreign_today", 0),
            "inst_net":    inv.get("orgn_today",    0),
        }

    # ── 3. 날짜 계산 (오늘 기준 역산) ───────────────────────────
    # get_daily_ohlc는 최신→과거 순서로 반환
    rows = []
    base = datetime.today()
    skip = 0   # 주말 보정용 오프셋
    for i, candle in enumerate(ohlc):
        # 영업일 기준 역산 (토/일 건너뜀)
        while True:
            d = base - timedelta(days=i + skip)
            if d.weekday() < 5:   # 월~금
                break
            skip += 1

        date_str = d.strftime("%Y-%m-%d")
        inv_day  = inv_map.get(date_str, {})

        rows.append({
            "date":        date_str,
            "stock_name":  stock_name,
            "close":       candle["close"],
            "volume":      candle["volume"],
            "foreign_net": inv_day.get("foreign_net"),
            "inst_net":    inv_day.get("inst_net"),
        })

    return upsert_daily_data(rows)


# ── 전체 수집 ──────────────────────────────────────────────────
def collect_all(days: int = 30, delay: float = 0.5) -> None:
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
            saved = collect_stock(api, name, code, days)
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
    api    = KisAPI(appkey=appkey, secret=secret)

    name, code = parse_stock(raw_name)
    if not code:
        return

    print(f"📈 단일 수집: {name} ({code}) | 최근 {days}일")
    saved = collect_stock(api, name, code, days)
    print(f"✅ {saved}일치 저장 완료")


# ── 실행 ───────────────────────────────────────────────────────
if __name__ == "__main__":
    collect_all(days=1, delay=0.3)

    # 단일 테스트:
    # collect_one("삼성SDI KOSPI 006400", days=10)
