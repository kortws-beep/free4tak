"""
premarket_nxt_collect.py — NXT 프리장(08:00~08:50) 1분봉 과거 수집 (2026-10-26, 프리장 섹터 백테스트용)
================================================================
[이 파일이 하는 일 — 비개발자용 설명]
대장 질문: "오늘의 섹터 대장주~3등주가 본장에서 프리장 가격 대비 몇 %까지 허용해야 상승 유지하나?"
→ 과거 프리장 가격이 필요. 한투 일별 분봉을 NXT(시장코드 NX)로 부르면 2025-11-03부터 08:00~08:50 분봉이 나옴.
  리나 테마 DB 종목 중 지금 NXT에서 거래되는 종목(약 570개) × 거래일마다 1번씩 받아
  backtest/data/nxt_premarket.db에 '프리장 마지막 가격·고가·저가·거래량·대금'을 남김(분봉 자체는 안 남김).
  최근 날짜부터, 이미 받은 (날짜, 종목)은 건너뜀 → 중간에 끊겨도 다시 돌리면 이어서.
실행: venv/bin/python backtest/premarket_nxt_collect.py [시작일=2025-11-03]
"""
import os
import sys
import time
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(BASE, "core"), os.path.join(BASE, "intelligence")]
OUT_DB = os.path.join(BASE, "backtest", "data", "nxt_premarket.db")
DAILY_DB = os.path.join(BASE, "lina_bot", "kr_theme_finance.db")
RATE = 14            # 초당 호출(한투 키당 20 — 밤엔 다른 봇이 이 키를 거의 안 씀)


def summarize(bars: list) -> tuple:
    """프리장 분봉(아무 순서) → (마지막 시각, 마지막 가격, 고가, 저가, 거래량, 대금) / 없으면 None."""
    if not bars:
        return None
    bars = sorted(bars, key=lambda b: b["time"])
    return (bars[-1]["time"], bars[-1]["price"], max(b["high"] for b in bars), min(b["low"] for b in bars),
            sum(b["volume"] for b in bars), sum(b["price"] * b["volume"] for b in bars))


class Limiter:
    def __init__(self, rate):
        self.gap, self.next, self.lock = 1.0 / rate, 0.0, threading.Lock()

    def wait(self):
        with self.lock:
            now = time.time()
            t = max(now, self.next)
            self.next = t + self.gap
        time.sleep(max(0.0, t - now))


def fetch(api, code: str, ymd: str) -> list:
    from kis_api import _get
    url = f"{api.base_url}/uapi/domestic-stock/v1/quotations/inquire-time-dailychartprice"
    headers = {"authorization": f"Bearer {api.token}", "appkey": api.appkey, "appsecret": api.secret,
               "tr_id": "FHKST03010230", "custtype": "P"}
    params = {"FID_COND_MRKT_DIV_CODE": "NX", "FID_INPUT_ISCD": code, "FID_INPUT_HOUR_1": "085000",
              "FID_INPUT_DATE_1": ymd, "FID_PW_DATA_INCU_YN": "N", "FID_FAKE_TICK_INCU_YN": ""}
    res = _get(url, headers=headers, params=params, timeout=10).json()
    if res.get("rt_cd") != "0":
        raise RuntimeError(res.get("msg1"))
    out = []
    for b in res.get("output2") or []:
        t = str(b.get("stck_cntg_hour") or "")
        if b.get("stck_bsop_date") == ymd and "080000" <= t < "085000":
            out.append({"time": t, "price": float(b["stck_prpr"]), "high": float(b["stck_hgpr"]),
                        "low": float(b["stck_lwpr"]), "volume": float(b.get("cntg_vol") or 0)})
    return out


def main():
    from dotenv import load_dotenv
    load_dotenv(os.path.join(BASE, ".env"))
    from kis_api import KisAPI
    import theme_open_obs as T
    start = next((a for a in sys.argv[1:] if "-" in a), "2025-11-03")
    api = KisAPI()
    themes = T.load_themes()
    codes = sorted({c for ms in themes.values() for c, _ in ms})
    nxt = sorted(c for c, q in api.get_multi_price(codes, pause=0.15, market="NX").items() if q.get("price", 0) > 0)
    dc = sqlite3.connect(DAILY_DB)
    days = [d for (d,) in dc.execute("SELECT DISTINCT date FROM kr_stock_daily_data WHERE date>=? ORDER BY date DESC",
                                     (start,))]
    dc.close()
    os.makedirs(os.path.dirname(OUT_DB), exist_ok=True)
    conn = sqlite3.connect(OUT_DB, check_same_thread=False)
    conn.execute("CREATE TABLE IF NOT EXISTS pre (date TEXT, code TEXT, last_time TEXT, price REAL, high REAL, low REAL, "
                 "volume REAL, value REAL, PRIMARY KEY(date, code))")
    conn.execute("CREATE TABLE IF NOT EXISTS nxt_codes (code TEXT PRIMARY KEY)")
    conn.executemany("INSERT OR IGNORE INTO nxt_codes VALUES (?)", [(c,) for c in nxt])
    conn.commit()
    done = set(conn.execute("SELECT date, code FROM pre"))
    lim, lock = Limiter(RATE), threading.Lock()
    print(f"NXT {len(nxt)}종목 × {len(days)}일({days[-1]}~{days[0]}) — 이미 {len(done)}건", flush=True)

    def job(d, c):
        for attempt in range(3):
            lim.wait()
            try:
                s = summarize(fetch(api, c, d.replace("-", "")))
                with lock:
                    conn.execute("INSERT OR REPLACE INTO pre VALUES (?,?,?,?,?,?,?,?)", (d, c, *(s or (None,) * 6)))
                return
            except Exception as e:
                if attempt == 2:
                    print(f"⚠️ {d} {c}: {e}", flush=True)
                time.sleep(1 + attempt)

    with ThreadPoolExecutor(3) as ex:
        for i, d in enumerate(days):
            list(ex.map(lambda c: job(d, c), [c for c in nxt if (d, c) not in done]))
            with lock:
                conn.commit()
            n = conn.execute("SELECT COUNT(*) FROM pre WHERE date=? AND price IS NOT NULL", (d,)).fetchone()[0]
            print(f"{d} 완료 ({i + 1}/{len(days)}) — 프리장 가격 {n}종목", flush=True)
    conn.close()


if __name__ == "__main__":
    main()
