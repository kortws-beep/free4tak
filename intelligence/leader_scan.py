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
    # ★ 2026-10-06: 처음엔 전체시장 거래금액/거래증가율 순위(각 30)만 보탰는데,
    #   키움엔 뜬 에스투더블유(+17.9%, 거래대금 ~580억)가 풀에서 빠졌음 —
    #   전날 조용하다 오늘 터진 종목. 시장별(코스피/코스닥)로 나눠 받고
    #   상승률 순위도 더해 오늘 새로 뜬 종목을 넓게 잡는다(호출 8회).
    extra = []
    for market in ("0001", "1001"):
        for blng in ("3", "1", "0"):
            extra += api.get_value_rank(blng, market)
        extra += api.get_rise_rank(market)
    # ★ 2026-10-07: ETF도 풀에는 넣는다(결과에선 계속 제외). 키움 "거래대금 순위
    #   상위 200"이 ETF를 포함해 줄 세우는지 확인하려고 ETF 포함 순위(rank_all)를
    #   함께 기록 — 첫날 대조에서 파이썬만 통과가 많아(하위 순위 종목 위주) 의심.
    for code, name in extra:
        if len(code) == 6 and code not in pool:
            pool[code] = name
    return pool


# ★ 2026-10-07: 대조 첫날 파이썬만 오래 통과한 종목(제이앤티씨 순위 19~22위·10분봉
#   최대 211억 등)은 순위·10분봉으론 설명이 안 됨 → 키움 조건식 '대상'에서 투자주의/
#   경고/위험·단기과열 같은 종목을 빼고 있을 가능성. 한투 현재가의 상태값을 같이 기록.
_STAT = {"51": "관리", "52": "투자위험", "53": "투자경고", "54": "투자주의", "58": "거래정지",
         "59": "단기과열"}
_WARN = {"01": "투자주의", "02": "투자경고", "03": "투자위험"}


def market_status(md: dict) -> str:
    """한투 현재가(inquire-price) 응답의 시장경고·종목상태를 짧은 글로. 정상이면 ""."""
    tags = []
    s = str(md.get("iscd_stat_cls_code") or "")
    if s in _STAT:
        tags.append(_STAT[s])
    w = _WARN.get(str(md.get("mrkt_warn_cls_code") or ""))
    if w and w not in tags:
        tags.append(w)
    if str(md.get("short_over_yn") or "").upper() == "Y" and "단기과열" not in tags:
        tags.append("단기과열")
    if str(md.get("invt_caful_yn") or "").upper() == "Y":
        tags.append("투자유의")
    return ",".join(tags)


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
    priced = sorted(((c, q) for c, q in quotes.items() if q["price"] > 0), key=lambda x: -x[1]["value"])
    rank_all = {c: i for i, (c, _) in enumerate(priced, 1)}          # ETF 포함 순위(진단용)
    ranked = [(c, q) for c, q in priced if not _is_etf(pool.get(c) or q["name"])][:B_RANK]
    results = []
    for rank, (code, q) in enumerate(ranked, 1):
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
        cap, status = None, ""
        if not fails:
            md = api.get_market_data(code) or {}
            try:
                cap = float(md.get("hts_avls") or 0)   # 억원
            except (TypeError, ValueError):
                cap = 0.0
            if cap and not (A_MIN_CAP_EOK <= cap <= A_MAX_CAP_EOK):
                fails.append(f"A시총{cap:,.0f}억")
            status = market_status(md)
        results.append({
            "name": pool.get(code) or q["name"], "code": code, "rank": rank,
            "rank_all": rank_all.get(code), "status": status,
            "price": q["price"], "chg": q["chg"], "high_pct": high_pct,
            "value": q["value"], "bar_value": bv, "cap_eok": cap,
            "path": "급등(D·E)" if de else "급락(F)",
            "passed": not fails, "fails": fails,
        })
    results.sort(key=lambda r: (not r["passed"], r["rank"]))
    return {"time": now.strftime("%H:%M"), "pool": len(pool), "priced": len(quotes),
            "ranked": len(ranked), "results": results}


def format_hit(r: dict) -> str:
    st = f" ⚠️{r['status']}" if r.get("status") else ""
    return (f"📌 **{r['name']}**({r['code']}) {r['price']:,.0f}원 {r['chg']:+.2f}% "
            f"[{r['path']}]{st}\n"
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
        try:
            conn.execute("ALTER TABLE leader_obs ADD COLUMN rank_all INTEGER")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE leader_obs ADD COLUMN status TEXT")
        except sqlite3.OperationalError:
            pass
        conn.executemany("INSERT INTO leader_obs (date, time, code, name, rank, price, chg, high_pct, "
                         "value, bar_value, path, passed, fails, rank_all, status) "
                         "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", [
            (now.strftime("%Y-%m-%d"), now.strftime("%H:%M"), r["code"], r["name"], r["rank"],
             r["price"], r["chg"], r["high_pct"], r["value"], r["bar_value"], r["path"],
             int(r["passed"]), ", ".join(r["fails"]), r.get("rank_all"), r.get("status", ""))
            for r in rows])
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
        print("코스닥 거래금액순위 5개:", api.get_value_rank("3", "1001")[:5])
        print("코스닥 상승률순위 5개:", api.get_rise_rank("1001")[:5])
        sys.exit(0)
    pool = build_pool(api)
    out = scan(api, pool)
    print(f"{out['time']} 풀 {out['pool']}종목 → 시세 {out['priced']} → 상위 {out['ranked']} "
          f"→ 급등/급락 {len(out['results'])}개, 통과 {sum(r['passed'] for r in out['results'])}개")
    for r in out["results"][:20]:   # 통과가 앞, 근접 후보는 순위순 20개까지
        print(("✅ " if r["passed"] else "   ") + format_hit(r).replace("**", "")
              + ("" if r["passed"] else f"\n   ✗ {', '.join(r['fails'])}"))
