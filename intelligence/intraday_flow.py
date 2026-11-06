"""
intraday_flow.py — 종목별 외국인·기관 장중 잠정(가집계) 수급 모으기 (2026-11-05, 대장)
================================================================
[이 파일이 하는 일 — 비개발자용 설명]
11-04 대장: 외국인이 오전에 던지고 오후에 되사는 날(삼성전자 오전 -717억 → 오후 +625억)의 '되사는 종목'을 따라가는
'외국인 귀환 엔진'을 검토하려면 시간대별 수급이 필요함. 장중 잠정치는 지나면 사라지니 지금부터 매일 쌓는다(매매 안 함).
  ★ 키움 ka10064(장중투자자별매매차트) — 대장이 보는 [1051] 화면과 같은 거래소 잠정(금액·수량, 09:19·09:51·11:00·13:09·14:14쯤 5회),
     대장 "키움 게 더 정확" → 주 데이터. 테이블 kflow.
  한투 HHPTJ04160200(종목별 외인기관 추정가집계) — 증권사 직원이 장중 입력한 누계(주), 비교용(테이블 flow).
  ★ 키움 ka10040(당일 주요 거래원) — 외국계 창구 추정 매도·매수 합 + 상위 5 창구, 장중 6번 스냅샷(테이블 broker) — 잠정과 어느 쪽이 빠른지 비교용.
  입력 시각 외국인 09:30·11:20·13:20·14:30 / 기관 10:00·11:20·13:20·14:30 (bsop_hour_gb 1~5, 5가 마지막).
  장 끝난 뒤에도 그날 5회분을 다 줘서 하루 한 번(리나 15:45)만 모으면 됨.
  대상: 최근 20일 평균 거래대금 100억↑ 종목 + NEW·SUB 관심그룹.
  저장: intelligence/intraday_flow.db 테이블 flow(date, code, round, frgn, orgn, total) — 같은 날 다시 돌려도 덮어씀.
        테이블 day(그날 시가·고가·저가·종가·등락·거래대금 + 다음날 시가·고가 — 다음 거래일 수집 때 채움)
실행(수동): venv/bin/python intelligence/intraday_flow.py [YYYY-MM-DD]
"""
import os
import sys
import time
import sqlite3
import datetime

_here = os.path.dirname(os.path.abspath(__file__))
_base = os.path.dirname(_here)
for _d in ["core", "intelligence", "interface", "bots", ""]:
    _p = os.path.join(_base, _d)
    if _p not in sys.path:
        sys.path.insert(0, _p)

DB_PATH = os.path.join(_here, "intraday_flow.db")
MIN_VALUE_EOK = 100
PAUSE = 0.06


def universe(daily_db: str = None) -> dict:
    """{코드: 이름} 최근 20거래일 평균 거래대금 MIN_VALUE_EOK억↑."""
    import dip_leader
    c = sqlite3.connect(daily_db or dip_leader.DAILY_DB, timeout=10)
    try:
        dates = [d for (d,) in c.execute("SELECT DISTINCT date FROM kr_stock_daily_data ORDER BY date DESC LIMIT 20")]
        if not dates:
            return {}
        codes = {n: k for k, n in c.execute("SELECT code, name FROM kr_stock_master")}
        q = (f"SELECT stock_name, AVG(trade_value) FROM kr_stock_daily_data WHERE date IN ({','.join('?' * len(dates))}) "
             "GROUP BY stock_name HAVING AVG(trade_value) >= ?")
        return {codes[n]: n for n, _ in c.execute(q, (*dates, MIN_VALUE_EOK * 1e8)) if n in codes}
    finally:
        c.close()


def parse(output2: list) -> list:
    """API output2 → [(회차, 외국인, 기관, 합)] 주 단위."""
    out = []
    for r in output2 or []:
        try:
            out.append((int(r["bsop_hour_gb"]), int(r["frgn_fake_ntby_qty"]), int(r["orgn_fake_ntby_qty"]),
                        int(r["sum_fake_ntby_qty"])))
        except (KeyError, ValueError, TypeError):
            continue
    return sorted(out)


def fetch(api, code: str) -> list:
    import requests
    h = {"authorization": f"Bearer {api.token}", "appkey": api.appkey, "appsecret": api.secret,
         "tr_id": "HHPTJ04160200", "custtype": "P"}
    try:
        r = requests.get(f"{api.base_url}/uapi/domestic-stock/v1/quotations/investor-trend-estimate",
                         headers=h, params={"MKSC_SHRN_ISCD": code}, timeout=10).json()
    except Exception:
        return []
    return parse(r.get("output2")) if r.get("rt_cd") == "0" else []


def save(date: str, code: str, rows: list, db: str = DB_PATH):
    c = sqlite3.connect(db, timeout=10)
    c.execute("CREATE TABLE IF NOT EXISTS flow (date TEXT, code TEXT, round INTEGER, frgn INTEGER, orgn INTEGER, "
              "total INTEGER, PRIMARY KEY(date, code, round))")
    c.executemany("INSERT OR REPLACE INTO flow VALUES (?,?,?,?,?,?)", [(date, code, *r) for r in rows])
    c.commit()
    c.close()


def kiwoom_rows(kw, code: str) -> list:
    """★ 11-05 대장 "키움 게 더 정확(수많은 사람이 검증)" — 키움 ka10064(장중투자자별매매차트, [1051] 화면과 같은 거래소 잠정).
    → [(시각, 외국인금액, 기관금액, 연기금금액, 투신금액, 외국인수량, 기관수량)] 금액 백만원·수량 주, 그 시각까지 누계."""
    out = {}
    for tp in ("1", "2"):                      # 1=금액(백만원) 2=수량(주)
        try:
            r = kw._post("chart", "ka10064", {"mrkt_tp": "000", "amt_qty_tp": tp, "trde_tp": "0", "stk_cd": code})
        except Exception:
            return []
        for x in r.get("opmr_invsr_trde_chart") or []:
            tm = x.get("tm", "")
            if not tm or tm == "090000":
                continue
            try:
                d = out.setdefault(tm, {})
                if tp == "1":
                    d.update(fa=int(x["frgnr_invsr"]), oa=int(x["orgn"]), pa=int(x["penfnd_etc"]), ia=int(x["invtrt"]))
                else:
                    d.update(fq=int(x["frgnr_invsr"]), oq=int(x["orgn"]))
            except (KeyError, ValueError, TypeError):
                continue
        time.sleep(0.25)
    return [(tm, d.get("fa"), d.get("oa"), d.get("pa"), d.get("ia"), d.get("fq"), d.get("oq")) for tm, d in sorted(out.items())]


def save_kiwoom(date: str, code: str, rows: list, db: str = DB_PATH):
    c = sqlite3.connect(db, timeout=10)
    c.execute("CREATE TABLE IF NOT EXISTS kflow (date TEXT, code TEXT, tm TEXT, frgn_amt INTEGER, orgn_amt INTEGER, "
              "penfnd_amt INTEGER, invtrt_amt INTEGER, frgn_qty INTEGER, orgn_qty INTEGER, PRIMARY KEY(date, code, tm))")
    c.executemany("INSERT OR REPLACE INTO kflow VALUES (?,?,?,?,?,?,?,?,?)", [(date, code, *r) for r in rows])
    c.commit()
    c.close()


def broker_row(kw, code: str):
    """★ 11-06 대장(11-05 삼성전자: 외국계 창구 매도 208만 주인데 잠정 외국인은 -60억뿐) — 키움 ka10040(당일 주요 거래원).
    → (외국계 추정 매도 합, 매수 합, 매도 상위5 [(창구, 수량)], 매수 상위5) 주 단위 / 실패 None."""
    try:
        r = kw._post("rkinfo", "ka10040", {"stk_cd": code})
    except Exception:
        return None
    if r.get("return_code") not in (0, "0"):
        return None
    n = lambda v: int(str(v or "0").replace("+", "").replace("--", "-") or 0)  # noqa: E731
    top = lambda side: [(r.get(f"{side}_trde_ori_{i}", ""), n(r.get(f"{side}_trde_ori_qty_{i}"))) for i in range(1, 6)  # noqa: E731
                        if r.get(f"{side}_trde_ori_{i}")]
    try:
        return n(r.get("frgn_sel_prsm_sum")), n(r.get("frgn_buy_prsm_sum")), top("sel"), top("buy")
    except ValueError:
        return None


def save_broker(date: str, tm: str, code: str, row, db: str = DB_PATH):
    c = sqlite3.connect(db, timeout=10)
    c.execute("CREATE TABLE IF NOT EXISTS broker (date TEXT, tm TEXT, code TEXT, frgn_sell INTEGER, frgn_buy INTEGER, "
              "top_sell TEXT, top_buy TEXT, PRIMARY KEY(date, tm, code))")
    import json as _j
    c.execute("INSERT OR REPLACE INTO broker VALUES (?,?,?,?,?,?,?)",
              (date, tm, code, row[0], row[1], _j.dumps(row[2], ensure_ascii=False), _j.dumps(row[3], ensure_ascii=False)))
    c.commit()
    c.close()


def collect_broker(date: str, tm: str, codes=None, kw=None) -> int:
    """거래원(외국계 창구) 스냅샷 — 리나가 장중 여러 번(잠정 집계 시각 근처)."""
    if codes is None:
        codes = universe()
    if kw is None:
        from kiwoom_api import KiwoomAPI
        kw = KiwoomAPI()
    ok = 0
    for code in codes:
        row = broker_row(kw, code)
        if row:
            save_broker(date, tm, code, row)
            ok += 1
        time.sleep(0.25)
    return ok


def save_prices(date: str, quotes: dict, db: str = DB_PATH):
    """★ 11-05 제미나이 제안 — 그날 성적(시가·고가·저가·종가·등락)을 같이 남기고, 전 거래일 줄엔 '다음날 시가·고가'를 채움
    → 나중에 '잠정 수급 → 당일 종가·다음날 시가/고가' 승률을 바로 볼 수 있게."""
    c = sqlite3.connect(db, timeout=10)
    c.execute("CREATE TABLE IF NOT EXISTS day (date TEXT, code TEXT, prev_close REAL, open REAL, high REAL, low REAL, "
              "close REAL, chg REAL, value REAL, next_open REAL, next_high REAL, PRIMARY KEY(date, code))")
    rows = [(date, k, q.get("prev_close"), q.get("open"), q.get("high"), q.get("low"), q.get("price"), q.get("chg"),
             q.get("value")) for k, q in quotes.items() if q.get("price")]
    c.executemany("INSERT INTO day (date, code, prev_close, open, high, low, close, chg, value) VALUES (?,?,?,?,?,?,?,?,?) "
                  "ON CONFLICT(date, code) DO UPDATE SET prev_close=excluded.prev_close, open=excluded.open, "
                  "high=excluded.high, low=excluded.low, close=excluded.close, chg=excluded.chg, value=excluded.value", rows)
    prev = c.execute("SELECT MAX(date) FROM day WHERE date < ?", (date,)).fetchone()[0]
    if prev:
        c.executemany("UPDATE day SET next_open=?, next_high=? WHERE date=? AND code=?",
                      [(q.get("open"), q.get("high"), prev, k) for k, q in quotes.items() if q.get("price")])
    c.commit()
    c.close()


def collect(api, date: str, codes: dict = None, kw=None) -> tuple:
    """→ (대상 수, 저장 수) — 한투 추정(flow)·키움 잠정(kflow) 둘 다. kw 없으면 키움 API를 만들어 씀(실패하면 한투만)."""
    if codes is None:
        codes = universe()
        try:
            import dip_leader
            for c in dip_leader.watch_codes(api, os.getenv("KIS_HTS_ID2", os.getenv("KIS_HTS_ID", ""))):
                codes.setdefault(c, c)
        except Exception as e:
            print(f"⚠️ [잠정수급] 관심그룹 조회 실패(거래대금 상위만): {e}")
    try:
        save_prices(date, api.get_multi_price(list(codes), pause=0.1))
    except Exception as e:
        print(f"⚠️ [잠정수급] 그날 시세 저장 실패: {e}")
    if kw is None:
        try:
            from kiwoom_api import KiwoomAPI
            kw = KiwoomAPI()
        except Exception as e:
            print(f"⚠️ [잠정수급] 키움 API 준비 실패(한투만): {e}")
    ok = kok = 0
    for code in codes:
        rows = fetch(api, code)
        if rows:
            save(date, code, rows)
            ok += 1
        time.sleep(PAUSE)
        if kw is not None:
            krows = kiwoom_rows(kw, code)
            if krows:
                save_kiwoom(date, code, krows)
                kok += 1
    print(f"   [잠정수급] 한투 {ok} · 키움 {kok} / {len(codes)}종목")
    return len(codes), max(ok, kok)


def main():
    from dotenv import load_dotenv
    load_dotenv(os.path.join(_base, ".env"))
    from kis_api import KisAPI
    date = sys.argv[1] if len(sys.argv) > 1 else datetime.date.today().isoformat()
    n, ok = collect(KisAPI(), date)
    print(f"✅ [잠정수급] {date} {ok}/{n}종목 저장 → {DB_PATH}")


if __name__ == "__main__":
    main()
