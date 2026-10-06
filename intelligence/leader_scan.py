"""
leader_scan.py — "주도주검색식3" 키움 조건식의 파이썬 구현 (관찰 전용)
================================================================
[이 파일이 하는 일 — 비개발자용 설명]

키움 HTS의 "주도주검색식3"을 키움 없이 한투 API로 계산한다(2026-10-06).

키움 원본 조건식: A and B and C and ((D and E) or F)
  A 시가총액 500억 ~ 110조
  B 거래대금 순위 상위 200
  C [10분봉] 거래대금 50억 이상 (지금 만들어지는 10분봉)
  D 전일종가 대비 고가 +5% 이상
  E 전일종가 대비 현재가 +3% 이상
  F 전일종가 대비 현재가 -5% 이하

계산 순서 (한투 호출을 아끼려고):
  1. 후보 풀 = 일봉 DB의 전 거래일 거래대금 상위 400 + 한투 거래대금순위/
     거래증가율순위(각 30) — 오늘 상위 200은 거의 이 안에 있다.
  2. 복수시세 API(한 번에 30종목)로 풀 전체 현재가·거래대금 → 오늘 거래대금
     순으로 줄 세워 상위 200 = B. 여기서 (D and E) or F도 바로 판정.
  3. 남은 종목만 1분봉을 받아 지금 10분봉 거래대금(C) 계산.
  4. 최종 통과 종목만 시가총액(A) 확인.
"""
import os
import sqlite3
import datetime

import three_month_leader as tml   # 일봉 DB 경로·종목코드 매핑 재사용

KST = datetime.timezone(datetime.timedelta(hours=9))

A_MIN_CAP_EOK   = 500            # 억원
A_MAX_CAP_EOK   = 1_100_000      # 110조 = 1,100,000억
B_RANK          = 200
C_MIN_VALUE     = 5_000_000_000  # 50억 (키움 단위: 분봉 천원 → 5,000,000)
D_HIGH_PCT      = 5.0
E_CLOSE_PCT     = 3.0
F_DROP_PCT      = -5.0
BAR_MIN         = 10
POOL_FROM_DB    = 400
# ★ 2026-10-06: 순위 API가 ETF/ETN도 주는데 키움 조건검색은 ETF 제외로 쓰고
#   있어서(실측: KODEX 코스닥150레버리지가 통과로 나옴) 이름으로 거른다.
ETF_KEYWORDS = ("KODEX", "TIGER", "KBSTAR", "RISE", "ARIRANG", "HANARO", "KOSEF", "TREX",
                "SOL ", "ACE ", "PLUS ", "KIWOOM", "TIMEFOLIO", "WON ", "1Q ", "BNK ",
                "레버리지", "인버스", "ETN", "선물")


def _is_etf(name: str) -> bool:
    return any(k in (name or "") for k in ETF_KEYWORDS)


def build_pool(api, db_path: str = tml.THEME_DB, today: str = None) -> dict:
    """{code: name} — 전 거래일 거래대금 상위 + 한투 순위 API 보완."""
    today = today or datetime.datetime.now(KST).strftime("%Y-%m-%d")
    conn = sqlite3.connect(db_path, timeout=10)
    conn.execute("PRAGMA query_only = ON")
    try:
        name_code = tml._name_code_map(conn)
        latest = conn.execute("SELECT MAX(date) FROM kr_stock_daily_data WHERE date < ?",
                              (today,)).fetchone()[0]
        rows = conn.execute(
            "SELECT stock_name FROM kr_stock_daily_data WHERE date = ? "
            "ORDER BY COALESCE(trade_value, close_price * volume) DESC LIMIT ?",
            (latest, POOL_FROM_DB)).fetchall() if latest else []
    finally:
        conn.close()
    pool = {name_code[n]: n for (n,) in rows if n in name_code}
    for blng in ("3", "1"):
        for code, name in api.get_value_rank(blng):
            if len(code) == 6 and code not in pool and not _is_etf(name):
                pool[code] = name
    return pool


def bar_value(bars: list, now_hhmm: str) -> float:
    """지금 만들어지는 10분봉(09:00, 09:10 … 기준)의 거래대금(원).
    누적거래대금이 있으면 (최신 누적 − 봉 시작 직전 누적), 없으면 분봉 가격×거래량 합."""
    h, m = int(now_hhmm[:2]), int(now_hhmm[2:4])
    start = f"{h:02d}{m - m % BAR_MIN:02d}00"
    cur  = [b for b in bars if b["time"] >= start]
    if not cur:
        return 0.0
    before = [b for b in bars if b["time"] < start]
    if cur[0]["acml_value"] and before and before[0]["acml_value"]:
        return cur[0]["acml_value"] - before[0]["acml_value"]
    return sum(b["price"] * b["volume"] for b in cur)


def scan(api, pool: dict, now: datetime.datetime = None) -> dict:
    """반환: {"time", "pool", "priced", "ranked": B 통과 수,
              "results": [{"name","code","rank","price","chg","high_pct","value",
                           "bar_value","cap_eok","path","passed","fails"}]}
    results에는 B와 (D·E 또는 F)를 통과한 종목만 담긴다(근접 후보 = C·A 탈락)."""
    now = now or datetime.datetime.now(KST)
    quotes = api.get_multi_price(list(pool))
    ranked = sorted(((c, q) for c, q in quotes.items() if q["price"] > 0),
                    key=lambda x: -x[1]["value"])[:B_RANK]
    results = []
    for rank, (code, q) in enumerate(ranked, 1):
        if _is_etf(pool.get(code) or q["name"]):
            continue
        prev = q["prev_close"] or (q["price"] / (1 + q["chg"] / 100) if q["chg"] > -100 else 0)
        high_pct = (q["high"] / prev - 1) * 100 if prev else 0.0
        de = high_pct >= D_HIGH_PCT and q["chg"] >= E_CLOSE_PCT
        f  = q["chg"] <= F_DROP_PCT
        if not (de or f):
            continue
        fails = []
        bv = bar_value(api.get_minute_bars(code, now.strftime("%H%M%S")), now.strftime("%H%M"))
        if bv < C_MIN_VALUE:
            fails.append(f"C10분봉{bv / 1e8:.0f}억")
        cap = None
        if not fails:
            md = api.get_market_data(code) or {}
            try:
                cap = float(md.get("hts_avls") or 0)   # 억원
            except (TypeError, ValueError):
                cap = 0.0
            if cap and not (A_MIN_CAP_EOK <= cap <= A_MAX_CAP_EOK):
                fails.append(f"A시총{cap:,.0f}억")
        results.append({
            "name": pool.get(code) or q["name"], "code": code, "rank": rank,
            "price": q["price"], "chg": q["chg"], "high_pct": high_pct,
            "value": q["value"], "bar_value": bv, "cap_eok": cap,
            "path": "급등(D·E)" if de else "급락(F)",
            "passed": not fails, "fails": fails,
        })
    results.sort(key=lambda r: (not r["passed"], r["rank"]))
    return {"time": now.strftime("%H:%M"), "pool": len(pool), "priced": len(quotes),
            "ranked": len(ranked), "results": results}


def format_hit(r: dict) -> str:
    return (f"📌 **{r['name']}**({r['code']}) {r['price']:,.0f}원 {r['chg']:+.2f}% "
            f"[{r['path']}]\n"
            f"   거래대금 {r['value'] / 1e8:,.0f}억(풀 내 {r['rank']}위) | 10분봉 "
            f"{r['bar_value'] / 1e8:,.0f}억 | 고가 {r['high_pct']:+.1f}%")


def log_scan(out: dict, now: datetime.datetime = None, db_path: str = tml.LOG_DB) -> int:
    """백테스트·키움 대조용 기록 (three_month_leader_log.db의 leader_obs 테이블)."""
    rows = out.get("results") or []
    if not rows:
        return 0
    now = now or datetime.datetime.now(KST)
    conn = sqlite3.connect(db_path, timeout=10)
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS leader_obs (
            date TEXT, time TEXT, code TEXT, name TEXT, rank INTEGER, price REAL,
            chg REAL, high_pct REAL, value REAL, bar_value REAL, path TEXT,
            passed INTEGER, fails TEXT)""")
        conn.executemany("INSERT INTO leader_obs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", [
            (now.strftime("%Y-%m-%d"), now.strftime("%H:%M"), r["code"], r["name"], r["rank"],
             r["price"], r["chg"], r["high_pct"], r["value"], r["bar_value"], r["path"],
             int(r["passed"]), ", ".join(r["fails"])) for r in rows])
        conn.commit()
        return len(rows)
    finally:
        conn.close()


if __name__ == "__main__":
    # python leader_scan.py        — 지금 시점 스캔 1회
    # python leader_scan.py probe  — 복수시세/분봉 API 응답 필드 확인(처음 1회)
    import sys
    import json
    sys.path.insert(0, os.path.join(tml._BASE, "core"))
    from dotenv import load_dotenv
    # 리나 안에선 이미 로드돼 있음 — 단독 실행용 (리나와 같은 위치들)
    for _env in (os.path.join(tml._BASE, ".env"), os.path.join(tml._BASE, "lina_bot", ".env")):
        load_dotenv(_env)
    from kis_api import KisAPI
    api = KisAPI()
    if len(sys.argv) > 1 and sys.argv[1] == "probe":
        print("복수시세:", json.dumps(api.get_multi_price(["005930", "000660"]), ensure_ascii=False))
        print("분봉 3개:", json.dumps(api.get_minute_bars("005930")[:3], ensure_ascii=False))
        print("거래금액순위 5개:", api.get_value_rank("3")[:5])
        sys.exit(0)
    pool = build_pool(api)
    out = scan(api, pool)
    print(f"{out['time']} 풀 {out['pool']}종목 → 시세 {out['priced']} → 상위 {out['ranked']} "
          f"→ 급등/급락 {len(out['results'])}개, 통과 {sum(r['passed'] for r in out['results'])}개")
    for r in out["results"][:20]:   # 통과가 앞, 근접 후보는 순위순 20개까지
        print(("✅ " if r["passed"] else "   ") + format_hit(r).replace("**", "")
              + ("" if r["passed"] else f"\n   ✗ {', '.join(r['fails'])}"))
