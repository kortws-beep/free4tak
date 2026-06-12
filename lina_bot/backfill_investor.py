import os
import re
import time
import sqlite3
import requests
from datetime import datetime
from dotenv import load_dotenv
from kis_api import KisAPI

# ── 환경변수 & 경로 ────────────────────────────────────────────
load_dotenv(dotenv_path=os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"), override=True)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH  = os.path.join(BASE_DIR, "kr_theme_finance.db")


def backfill_investor_data(api: KisAPI, stock_name: str, code: str) -> int:
    """
    특정 종목의 최근 30영업일 외인/기관 수급 데이터를 가져와 DB를 업데이트합니다.

    수정사항:
     - 파라미터 키 소문자로 수정 (한투 API 대소문자 구분)
     - stock_name 은 parse_stock 으로 정제된 순수 종목명만 사용
       (kr_stock_daily_data.stock_name 과 동일한 형식이어야 UPDATE 매칭됨)
     - frgn_ntby_qty / orgn_ntby_qty 값이 빈 문자열일 때 int() 오류 방지
    """
    url = f"{api.base_url}/uapi/domestic-stock/v1/quotations/inquire-investor"
    headers = {
        "Content-Type": "application/json",
        "authorization": f"Bearer {api.token}",
        "appKey":    api.appkey,
        "appSecret": api.secret,
        "tr_id":     "FHKST01010900",
    }
    # ✅ 수정 1: 파라미터 키 소문자 (kis_api.py 와 동일하게)
    params = {
        "fid_cond_mrkt_div_code": "J",
        "fid_input_iscd":         code,
    }

    def safe_int(v):
        try:
            return int(str(v).replace(",", "").strip() or "0")
        except Exception:
            return 0

    try:
        res    = requests.get(url, headers=headers, params=params, timeout=5).json()
        output = res.get("output", [])

        if not output:
            print(f"  ⚠️  {stock_name} ({code}) 수급 응답 없음 "
                  f"| rt_cd={res.get('rt_cd')} msg={res.get('msg1', '')}")
            return 0

        conn    = sqlite3.connect(DB_PATH)
        cursor  = conn.cursor()
        updated = 0

        for item in output:
            raw_date = item.get("stck_bsop_date", "")
            if not raw_date or len(raw_date) != 8:
                continue

            fmt_date    = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:8]}"
            foreign_net = safe_int(item.get("frgn_ntby_qty", 0))
            inst_net    = safe_int(item.get("orgn_ntby_qty",  0))

            # ✅ 수정 2: stock_name 은 정제된 순수 종목명 (collect_daily_data.py 와 동일)
            #    kr_stock_daily_data 에는 순수 종목명으로 저장되어 있음
            cursor.execute("""
                UPDATE kr_stock_daily_data
                SET foreign_net_buy     = ?,
                    institution_net_buy = ?,
                    updated_at          = CURRENT_TIMESTAMP
                WHERE date = ? AND stock_name = ?
            """, (foreign_net, inst_net, fmt_date, stock_name))

            if cursor.rowcount > 0:
                updated += 1

        conn.commit()
        conn.close()
        return updated

    except Exception as e:
        print(f"  ❌ {stock_name} ({code}) 수급 백필 오류: {e}")
        return 0


# ── 메인 ──────────────────────────────────────────────────────
if __name__ == "__main__":
    appkey = os.getenv("KIS_APPKEY")
    secret = os.getenv("KIS_SECRET")

    print("\n🚀 [과거 수급 데이터 빈칸 채우기 시작]")
    api = KisAPI(appkey=appkey, secret=secret)
    api.refresh_token_if_needed()

    # DB 에서 종목 목록 (raw 이름 그대로)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT stock_name FROM kr_theme_stocks")
    raw_names = [r[0] for r in cursor.fetchall()]
    conn.close()

    if not raw_names:
        print("⚠️  kr_theme_stocks 에 종목이 없어.")
        exit()

    print(f"📋 대상 종목: {len(raw_names)}개\n")
    total_updated = 0
    skipped       = 0

    for i, raw in enumerate(raw_names, 1):
        # ✅ 수정 3: collect_daily_data.py 와 동일한 parse_stock 로직
        m = re.search(r"(\d{6})$", raw.strip())
        if not m:
            skipped += 1
            continue

        code = m.group(1)
        name = re.sub(r"\s*KOS(?:PI|DAQ)\s*\d{6}$", "", raw).strip()

        api.refresh_token_if_needed()
        print(f"  [{i}/{len(raw_names)}] 🔄 {name} ({code}) 수급 채우는 중...")

        count = backfill_investor_data(api, name, code)
        total_updated += count
        print(f"      ✅ {count}일치 업데이트")

        time.sleep(0.4)   # API 호출 제한 방지

    print(f"\n🎉 전체 완료! 총 {total_updated}건 수급 업데이트 | 코드 파싱 실패: {skipped}개")
