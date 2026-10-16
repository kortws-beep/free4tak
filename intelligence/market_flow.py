"""
market_flow.py — 지수 일봉 · 시장별 투자자 순매수 수집 (2026-10-17, 하락장 전환점 연구 1단계)
================================================================
[이 파일이 하는 일 — 비개발자용 설명]

대장: "미수 3일·신용 담보부족 반대매매까지 나와야 하락장 마감", 10-16 "외국인이 현물 순매수로
돌아섰고 선물은 팔았다". 전환점을 숫자로 보려면 매일 쌓인 시장 데이터가 필요한데 지금 DB엔
종목 일봉뿐(지수·투자자 흐름 없음). → 네이버 증권 모바일 API(kis_api.get_market_index가 이미
쓰는 경로)로 두 가지를 리나 로그 DB(three_month_leader_log.db)에 쌓는다.
  · market_index  — 코스피·코스닥 일봉(시가·고가·저가·종가·등락률). --backfill로 과거도 채움
  · market_investor — 시장별 개인·외국인·기관 순매수(억원, 최근 며칠치가 API에 나옴 → 매일 모으면 쌓임)
★ 10-17 2단계(probe_sources.py로 형식 확인):
  · market_futures — 한투 코스피200 선물 최근월물(101000): 가격·베이시스·이론가·괴리율·미결제약정(증감)
  · 선물 투자자 — 네이버 FUT integration의 dealTrendInfo → market_investor(market='FUT')
  · kofia_funds  — 금투협 증시자금(STATSCU0100000060BO): 투자자예탁금·파생예수금·RP·미수금·실제 반대매매·비중(%)
  · kofia_credit — 금투협 신용공여 잔고(STATSCU0100000070BO): 신용융자(전체·유가·코스닥)·대주·예탁증권담보융자
  금투협 금액 단위 백만원, 하루 늦게 올라옴.

응답 형식을 이 환경에선 확인 못 해서 필드 이름이 달라도 최대한 읽고, 못 읽으면 응답 앞부분을 출력함
(그걸 보내주면 맞춤).
실행:  python intelligence/market_flow.py              (오늘까지 최신 수집 + 최근 상태 출력)
       python intelligence/market_flow.py --raw        (최근 원문 — 투자자·프로그램·상승하락 필드 확인)
       python intelligence/market_flow.py --backfill 300   (지수 일봉 과거 300거래일 채우기)
"""
import os
import re
import sys
import json
import sqlite3

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_DB = os.path.join(_BASE, "lina_bot", "three_month_leader_log.db")
INDEXES = ("KOSPI", "KOSDAQ")
INVESTOR_MARKETS = ("KOSPI", "KOSDAQ", "FUT")          # FUT = 코스피200 선물(외국인 선물 순매수)
KOFIA_URL = "https://freesis.kofia.or.kr/meta/getMetaDataList.do"
KOFIA_FUNDS, KOFIA_CREDIT = "STATSCU0100000060BO", "STATSCU0100000070BO"
UA = {"User-Agent": "Mozilla/5.0"}


def _num(v):
    """'5,412.30' / '+1,234' / '-56' / 5412.3 → float, 못 읽으면 None."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = re.sub(r"[^\d.\-]", "", str(v).replace("−", "-"))
    try:
        return float(s) if s not in ("", "-", ".") else None
    except ValueError:
        return None


def _date(v) -> str:
    """'2026-10-16' / '20261016' / '2026.10.16' / '2026-10-16T15:30:00+09:00' → '2026-10-16'."""
    d = re.sub(r"\D", "", str(v or ""))[:8]
    return f"{d[:4]}-{d[4:6]}-{d[6:8]}" if len(d) == 8 else ""


def _pick(row: dict, *names):
    for n in names:
        if n in row and row[n] not in (None, ""):
            return row[n]
    return None


def parse_index_rows(data) -> list:
    """네이버 지수 시세 응답 → [(date, open, high, low, close, chg%)]."""
    rows = data if isinstance(data, list) else (data or {}).get("result") or (data or {}).get("items") or []
    out = []
    for r in rows if isinstance(rows, list) else []:
        if not isinstance(r, dict):
            continue
        d = _date(_pick(r, "localTradedAt", "localDate", "bizdate", "date", "tradeDate"))
        c = _num(_pick(r, "closePrice", "close", "closePriceRaw"))
        if not d or c is None:
            continue
        out.append((d, _num(_pick(r, "openPrice", "open")), _num(_pick(r, "highPrice", "high")),
                    _num(_pick(r, "lowPrice", "low")), c, _num(_pick(r, "fluctuationsRatio", "changeRate"))))
    return out


def parse_investor_rows(data) -> list:
    """네이버 지수 통합 응답의 투자자 동향 → [(date, 개인, 외국인, 기관)] 억원."""
    rows = data if isinstance(data, list) else []
    if isinstance(data, dict):
        boxes = [data] + [v for v in data.values() if isinstance(v, dict)]
        for box in boxes:
            for key in ("dealTrendInfos", "dealTrendInfo", "investorTrend", "dealTrends", "investorDealTrends"):
                v = box.get(key)
                if isinstance(v, list):
                    rows = v
                    break
                if isinstance(v, dict):          # ★ 10-17 실제 응답: dealTrendInfo(단수, 하루치 묶음)
                    rows = [v]
                    break
            if rows:
                break
    out = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        d = _date(_pick(r, "bizdate", "localTradedAt", "date"))
        f = _num(_pick(r, "foreignValue", "foreignerValue", "foreign"))
        if not d or f is None:
            continue
        out.append((d, _num(_pick(r, "personalValue", "individualValue", "personal")), f,
                    _num(_pick(r, "institutionalValue", "organValue", "institution"))))
    return out


RAW_KEYS = ("dealTrendInfo", "programTrendInfo", "upDownStockInfo")


def _table(conn):
    # ★ 10-17: 투자자·프로그램·상승하락 종목 수 원문 — 안쪽 필드 이름을 아직 몰라 파싱이 틀려도 데이터는 남게
    conn.execute("CREATE TABLE IF NOT EXISTS market_raw (date TEXT, idx TEXT, key TEXT, json TEXT, "
                 "PRIMARY KEY(date, idx, key))")
    # ★ 10-17 원문 필드 확인 뒤: 프로그램 매매(차익·비차익·합계 억원), 상승·하락 종목 수
    conn.execute("CREATE TABLE IF NOT EXISTS market_program (date TEXT, market TEXT, arb REAL, nonarb REAL, "
                 "total REAL, PRIMARY KEY(date, market))")
    conn.execute("CREATE TABLE IF NOT EXISTS market_updown (date TEXT, market TEXT, upper INTEGER, rise INTEGER, "
                 "lower_ INTEGER, fall INTEGER, steady INTEGER, PRIMARY KEY(date, market))")
    conn.execute("CREATE TABLE IF NOT EXISTS market_futures (date TEXT PRIMARY KEY, name TEXT, price REAL, basis REAL, "
                 "theory REAL, dprt REAL, oi REAL, oi_chg REAL)")
    conn.execute("CREATE TABLE IF NOT EXISTS kofia_funds (date TEXT PRIMARY KEY, deposit REAL, deriv_deposit REAL, "
                 "rp REAL, misu REAL, forced REAL, forced_pct REAL)")
    conn.execute("CREATE TABLE IF NOT EXISTS kofia_credit (date TEXT PRIMARY KEY, credit REAL, credit_kospi REAL, "
                 "credit_kosdaq REAL, short REAL, short_kospi REAL, short_kosdaq REAL, ipo_loan REAL, pledge REAL)")
    conn.execute("CREATE TABLE IF NOT EXISTS market_index (date TEXT, idx TEXT, open REAL, high REAL, low REAL, "
                 "close REAL, chg REAL, PRIMARY KEY(date, idx))")
    conn.execute("CREATE TABLE IF NOT EXISTS market_investor (date TEXT, market TEXT, personal REAL, foreign_ REAL, "
                 "institution REAL, PRIMARY KEY(date, market))")


def save(index_rows: dict, investor_rows: dict, db: str = LOG_DB) -> tuple:
    conn = sqlite3.connect(db, timeout=10)
    try:
        _table(conn)
        n1 = n2 = 0
        for idx, rows in index_rows.items():
            conn.executemany("INSERT OR REPLACE INTO market_index VALUES (?,?,?,?,?,?,?)",
                             [(d, idx, o, h, l, c, ch) for d, o, h, l, c, ch in rows])
            n1 += len(rows)
        for mk, rows in investor_rows.items():
            conn.executemany("INSERT OR REPLACE INTO market_investor VALUES (?,?,?,?,?)",
                             [(d, mk, p, f, i) for d, p, f, i in rows])
            n2 += len(rows)
        conn.commit()
        return n1, n2
    finally:
        conn.close()


def _get_json(url: str):
    import requests
    return requests.get(url, headers=UA, timeout=10).json()


def fetch_index(idx: str, pages: int = 1, page_size: int = 60, get=_get_json) -> list:
    out, seen = [], set()
    for p in range(1, pages + 1):
        try:
            data = get(f"https://m.stock.naver.com/api/index/{idx}/price?pageSize={page_size}&page={p}")
        except Exception as e:
            print(f"⚠️ {idx} 일봉 {p}쪽 조회 실패: {e}")
            break
        rows = [r for r in parse_index_rows(data) if r[0] not in seen]
        if not rows:
            if p == 1:
                print(f"⚠️ {idx} 일봉 응답을 못 읽음 — 앞부분: {json.dumps(data, ensure_ascii=False)[:400]}")
            break
        seen.update(r[0] for r in rows)
        out += rows
    return out


def fetch_investor(idx: str, get=_get_json, raw: list = None) -> list:
    try:
        data = get(f"https://m.stock.naver.com/api/index/{idx}/integration")
    except Exception as e:
        print(f"⚠️ {idx} 투자자 동향 조회 실패: {e}")
        return []
    if raw is not None and isinstance(data, dict):
        for k in RAW_KEYS:
            if data.get(k) is not None:
                raw.append((idx, k, json.dumps(data[k], ensure_ascii=False)))
    rows = parse_investor_rows(data)
    if not rows:
        part = data.get("dealTrendInfo") if isinstance(data, dict) and data.get("dealTrendInfo") else data
        print(f"⚠️ {idx} 투자자 동향을 못 읽음 — dealTrendInfo: {json.dumps(part, ensure_ascii=False)[:600]}")
    return rows


def collect(backfill_days: int = 0, db: str = LOG_DB, get=_get_json, api=None, post=None) -> str:
    pages = max(1, -(-backfill_days // 60)) if backfill_days else 1
    idx_rows = {i: fetch_index(i, pages, get=get) for i in INDEXES}
    raw = []
    inv_rows = {i: fetch_investor(i, get=get, raw=raw) for i in INVESTOR_MARKETS}
    n1, n2 = save(idx_rows, inv_rows, db)
    last = max((r[0] for rows in idx_rows.values() for r in rows), default="")
    if raw and last:
        conn = sqlite3.connect(db, timeout=10)
        try:
            _table(conn)
            conn.executemany("INSERT OR REPLACE INTO market_raw VALUES (?,?,?,?)", [(last, i, k, j) for i, k, j in raw])
            conn.commit()
        finally:
            conn.close()
        rebuild_from_raw(db)
    fut = fetch_futures(api) if api is not None else None
    kd = backfill_days or 20
    funds, credit = fetch_kofia(KOFIA_FUNDS, kd, post), fetch_kofia(KOFIA_CREDIT, kd, post)
    save_extra(fut, funds, credit, last, db)
    return (f"✅ 지수 일봉 {n1}행 · 투자자 동향 {n2}행 · 원문 {len(raw)}건({last}) · 선물 {'1' if fut else '0'}행 · "
            f"금투협 증시자금 {len(funds)}행 · 신용 {len(credit)}행 저장 ({db})")


def recent(n: int = 10, db: str = LOG_DB) -> str:
    conn = sqlite3.connect(db, timeout=10)
    try:
        _table(conn)
        idx = conn.execute("SELECT date, idx, close, chg FROM market_index ORDER BY date DESC LIMIT ?",
                           (n * 2,)).fetchall()
        inv = {(d, m): (p, f, i) for d, m, p, f, i in conn.execute(
            "SELECT date, market, personal, foreign_, institution FROM market_investor")}
        prog = {(d, m): (a, na) for d, m, a, na in conn.execute("SELECT date, market, arb, nonarb FROM market_program")}
        ud = {(d, m): (r, f) for d, m, r, f in conn.execute("SELECT date, market, rise, fall FROM market_updown")}
        fut = {d: (b, dp, oi, oc) for d, b, dp, oi, oc in conn.execute(
            "SELECT date, basis, dprt, oi, oi_chg FROM market_futures")}
        funds = conn.execute("SELECT date, misu, forced, forced_pct, deposit FROM kofia_funds ORDER BY date DESC LIMIT ?",
                             (n,)).fetchall()[::-1]
        credit = {d: c for d, c in conn.execute("SELECT date, credit FROM kofia_credit")}
    finally:
        conn.close()
    L = ["📈 최근 시장 (지수 종가·등락 / 외국인·기관·개인 순매수 억원 / 프로그램 차익·비차익 억원 / 상승:하락 종목 수)"]
    tail = []
    for d, (b, dp, oi, oc) in sorted(fut.items())[-n:]:
        fv = inv.get((d, "FUT"))
        tail.append(f"   {d}  선물 베이시스 {b:+.2f} · 괴리율 {dp:+.2f}% · 미결제 {oi:,.0f}({oc:+,.0f})"
                    + (f" · 선물 외국인 {fv[1]:+,.0f} 기관 {fv[2] or 0:+,.0f} 개인 {fv[0] or 0:+,.0f}" if fv else ""))
    if funds:
        tail.append("   💳 금투협(하루 늦음, 억원) — 미수금 · 실제 반대매매(미수 대비 %) · 신용융자 잔고 · 투자자예탁금")
        for d, mi, fs, fp, dep in funds:
            cr = credit.get(d)
            tail.append(f"   {d}  미수 {mi / 100:,.0f} · 반대매매 {fs / 100:,.0f}({fp:.1f}%)"
                        + (f" · 신용 {cr / 100:,.0f}" if cr else "") + f" · 예탁금 {dep / 100:,.0f}")
    by = {}
    for d, i, c, ch in idx:
        by.setdefault(d, {})[i] = (c, ch)
    for d in sorted(by)[-n:]:
        parts = []
        for i in INDEXES:
            c, ch = by[d].get(i, (None, None))
            v = inv.get((d, i))
            s = f"{i} {c:,.2f}({ch:+.2f}%)" if c is not None and ch is not None else f"{i} -"
            if v:
                s += f" 외 {v[1]:+,.0f} 기 {v[2] or 0:+,.0f} 개 {v[0] or 0:+,.0f}"
            pg = prog.get((d, i))
            if pg:
                s += f" · 프로그램 차익 {pg[0] or 0:+,.0f} 비차익 {pg[1] or 0:+,.0f}"
            u = ud.get((d, i))
            if u:
                s += f" · 상승{u[0]}:하락{u[1]}"
            parts.append(s)
        L.append(f"   {d}  " + " · ".join(parts))
    return "\n".join(L + tail)


def parse_program(d: dict):
    """{"bizdate","indexDifferenceReal"(차익),"indexBiDifferenceReal"(비차익),"indexTotalReal"} → (date, 차익, 비차익, 합계)."""
    if not isinstance(d, dict):
        return None
    t = _num(d.get("indexTotalReal"))
    return (_date(d.get("bizdate")), _num(d.get("indexDifferenceReal")), _num(d.get("indexBiDifferenceReal")), t) \
        if t is not None else None


def parse_updown(d: dict):
    """{"upperCount","riseCount","lowerCount","fallCount","steadyCount"} → (상한, 상승, 하한, 하락, 보합)."""
    if not isinstance(d, dict) or d.get("riseCount") is None:
        return None
    return tuple(int(_num(d.get(k)) or 0) for k in ("upperCount", "riseCount", "lowerCount", "fallCount", "steadyCount"))


def rebuild_from_raw(db: str = LOG_DB) -> int:
    """market_raw 원문 → market_program / market_updown 표(다시 돌려도 같은 결과)."""
    conn = sqlite3.connect(db, timeout=10)
    try:
        _table(conn)
        n = 0
        for d, idx, key, js in conn.execute("SELECT date, idx, key, json FROM market_raw").fetchall():
            v = json.loads(js)
            if key == "programTrendInfo" and parse_program(v):
                pd, a, na, t = parse_program(v)
                conn.execute("INSERT OR REPLACE INTO market_program VALUES (?,?,?,?,?)", (pd or d, idx, a, na, t))
                n += 1
            elif key == "upDownStockInfo" and parse_updown(v):
                conn.execute("INSERT OR REPLACE INTO market_updown VALUES (?,?,?,?,?,?,?)", (d, idx, *parse_updown(v)))
                n += 1
        conn.commit()
        return n
    finally:
        conn.close()


def parse_futures(res: dict):
    """한투 선물 현재가(FHMIF10000000) → (이름, 가격, 베이시스, 이론가, 괴리율, 미결제, 미결제증감) 또는 None."""
    o = (res or {}).get("output1") or {}
    p = _num(o.get("futs_prpr"))
    if not p:
        return None
    return (o.get("hts_kor_isnm", ""), p, _num(o.get("basis")), _num(o.get("hts_thpr")), _num(o.get("dprt")),
            _num(o.get("hts_otst_stpl_qty")), _num(o.get("otst_stpl_qty_icdc")))


def fetch_futures(api, code: str = "101000"):
    """한투 코스피200 선물 최근월물 — 101000이 최근월물로 응답함(10-17 확인: F 202612)."""
    try:
        from kis_api import _get
        url = f"{api.base_url}/uapi/domestic-futureoption/v1/quotations/inquire-price"
        headers = {"authorization": f"Bearer {api.token}", "appkey": api.appkey, "appsecret": api.secret,
                   "tr_id": "FHMIF10000000", "custtype": "P"}
        res = _get(url, headers=headers, params={"FID_COND_MRKT_DIV_CODE": "F", "FID_INPUT_ISCD": code},
                   timeout=10).json()
    except Exception as e:
        print(f"⚠️ 선물 현재가 조회 실패: {e}")
        return None
    r = parse_futures(res)
    if not r:
        print(f"⚠️ 선물 현재가를 못 읽음 — {json.dumps(res, ensure_ascii=False)[:300]}")
    return r


KOFIA_FUNDS_COLS = ("TMPV2", "TMPV3", "TMPV4", "TMPV5", "TMPV6", "TMPV7")     # 예탁금·파생예수금·RP·미수·반대매매·비중
KOFIA_CREDIT_COLS = ("TMPV2", "TMPV3", "TMPV4", "TMPV5", "TMPV6", "TMPV7", "TMPV8", "TMPV9")


def parse_kofia(data, cols) -> list:
    """{"ds1":[{"TMPV1":"20261015","TMPV2":...}]} → [(date, *cols)]."""
    out = []
    for r in (data or {}).get("ds1") or []:
        d = _date(r.get("TMPV1"))
        if d:
            out.append((d, *[_num(r.get(c)) for c in cols]))
    return out


def fetch_kofia(obj: str, days: int = 20, post=None) -> list:
    import datetime
    end = datetime.date.today()
    start = end - datetime.timedelta(days=max(days, 7) * 7 // 5 + 5)      # 거래일 → 달력일 넉넉히
    body = {"dmSearch": {"tmpV40": "1000000", "tmpV41": "1", "tmpV1": "D", "tmpV45": start.strftime("%Y%m%d"),
                         "tmpV46": end.strftime("%Y%m%d"), "OBJ_NM": obj}}
    try:
        if post is None:
            import requests
            data = requests.post(KOFIA_URL, json=body, headers=dict(UA, **{"Content-Type": "application/json"}),
                                 timeout=15).json()
        else:
            data = post(body)
    except Exception as e:
        print(f"⚠️ 금투협 {obj} 조회 실패: {e}")
        return []
    rows = parse_kofia(data, KOFIA_FUNDS_COLS if obj == KOFIA_FUNDS else KOFIA_CREDIT_COLS)
    if not rows:
        print(f"⚠️ 금투협 {obj}를 못 읽음 — {json.dumps(data, ensure_ascii=False)[:300]}")
    return rows


def save_extra(fut, funds: list, credit: list, date: str, db: str = LOG_DB):
    conn = sqlite3.connect(db, timeout=10)
    try:
        _table(conn)
        if fut and date:
            conn.execute("INSERT OR REPLACE INTO market_futures VALUES (?,?,?,?,?,?,?,?)", (date, *fut))
        conn.executemany("INSERT OR REPLACE INTO kofia_funds VALUES (?,?,?,?,?,?,?)", funds)
        conn.executemany("INSERT OR REPLACE INTO kofia_credit VALUES (?,?,?,?,?,?,?,?,?)", credit)
        conn.commit()
    finally:
        conn.close()


def morning_line(db: str = LOG_DB) -> str:
    """★ 10-17 대장 — 07:30 브리핑 한 줄: 강제 물량(미수·반대매매·신용)이 남았나 + 외국인 현물·선물.
    turning_point.py: 반대매매 정점이 바닥 ±1거래일 안 5/8구간. 미수는 사고 3일째 아침 반대매매로 나오므로
    미수 잔고가 늘고 있으면 2~3일 뒤 물량이 남은 것."""
    try:
        conn = sqlite3.connect(db, timeout=10)
        try:
            _table(conn)
            funds = conn.execute("SELECT date, misu, forced, forced_pct FROM kofia_funds "
                                 "WHERE misu IS NOT NULL ORDER BY date DESC LIMIT 10").fetchall()
            credit = conn.execute("SELECT date, credit FROM kofia_credit WHERE credit IS NOT NULL "
                                  "ORDER BY date DESC LIMIT 20").fetchall()
            inv = conn.execute("SELECT date, market, foreign_ FROM market_investor WHERE date=(SELECT MAX(date) "
                               "FROM market_investor)").fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return ""
    if len(funds) < 2:
        return ""
    (d, misu, forced, pct), (_, misu_prev, _, _) = funds[0], funds[1]
    peak = max(funds, key=lambda r: r[2] or 0)
    trend = "↑" if misu > misu_prev else "↓" if misu < misu_prev else "→"
    s = (f"💳 금투협({d[5:]}) 미수 {misu / 100:,.0f}억({trend}) · 반대매매 {forced / 100:,.0f}억({pct:.1f}%, "
         f"최근 정점 {peak[0][5:]} {peak[2] / 100:,.0f}억)")
    if credit:
        hi = max(c for _, c in credit)
        s += f" · 신용 {credit[0][1] / 1e6:,.1f}조(20일 고점 대비 {(credit[0][1] / hi - 1) * 100:+.1f}%)"
    if misu > misu_prev:
        verdict = "미수 증가 중 → 2~3일 뒤 반대매매 물량 남음"
    elif peak[0] != d and forced <= peak[2] * 0.6:
        verdict = "반대매매 정점 지나 꺾임 → 강제 물량 정리 쪽"
    else:
        verdict = "강제 물량 정리 진행 중"
    s += f" → {verdict}"
    fx = {m: f for _, m, f in inv}
    if inv and any(fx.get(m) is not None for m in ("KOSPI", "FUT")):
        parts = [f"{lab} {fx[m]:+,.0f}" for m, lab in (("KOSPI", "코스피 현물"), ("KOSDAQ", "코스닥 현물"), ("FUT", "선물"))
                 if fx.get(m) is not None]
        s += f"\n🌏 외국인({inv[0][0][5:]}) " + " · ".join(parts)
    return s


def show_raw(db: str = LOG_DB) -> str:
    """가장 최근 원문(투자자·프로그램·상승하락) — 필드 이름 확인용."""
    conn = sqlite3.connect(db, timeout=10)
    try:
        _table(conn)
        rows = conn.execute("SELECT date, idx, key, json FROM market_raw WHERE date=(SELECT MAX(date) FROM market_raw)"
                            ).fetchall()
    finally:
        conn.close()
    return "\n".join(f"[{d} {i} {k}] {j[:700]}" for d, i, k, j in rows) or "(원문 없음)"


if __name__ == "__main__":
    a = sys.argv[1:]
    if "--raw" in a:
        print(show_raw())
        sys.exit(0)
    bf = int(a[a.index("--backfill") + 1]) if "--backfill" in a else 0
    api = None
    try:
        sys.path.insert(0, os.path.join(_BASE, "core"))
        from dotenv import load_dotenv
        load_dotenv(os.path.join(_BASE, ".env"))
        from kis_api import KisAPI
        api = KisAPI()
    except Exception as e:
        print(f"⚠️ 한투 연결 실패(선물은 건너뜀): {e}")
    print(collect(bf, api=api))
    print(recent())
