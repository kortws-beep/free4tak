"""
three_month_leader.py — "3개월수급 당일주도주" 키움 조건식의 파이썬 구현 (관찰 전용)
================================================================
[이 파일이 하는 일 — 비개발자용 설명]

키움 HTS에 만들어 둔 "3개월수급 당일주도주" 조건식을 키움 없이 계산한다.
(2026-10-06 대장 지정 — 키움 API 불안정 대응 + 백테스트 가능하게)

키움 원본 조건식 (A and B and E and F and G and H and I):
  A 체결강도 100% ~ 1000%
  B [일] 0봉전 60봉 이내 거래대금 2,000억 이상 1회 이상
  E [일] 60봉전부터 60봉 이내 거래대금 0 ~ 300억 60회 이상
  F [일] 거래량비율: 1봉 평균(전일) 대비 0봉 80% 이상
  G [일] 당일 거래대금 20억 이상
  H [일] 전일 종가 대비 등락률 3% ~ 12%
  I [일] 이격도(종가/120일선) 100% ~ 140%

나눠서 계산하는 이유:
  B·E는 "과거 일봉"만 보는 조건이라 장중에 다시 계산할 필요가 없다.
  → build_universe(): 일봉 DB(kr_stock_daily_data)로 B·E를 먼저 걸러 후보를
    수십 개 이하로 줄인다(API 호출 없음).
  → check_candidates(): 장중엔 그 후보만 한투 현재가로 F·G·H·I를 보고,
    전부 통과한 종목만 체결강도(A)를 추가 조회한다.

봉 번호 기준: "0봉 = 오늘(장중)". 일봉 DB에는 어제까지만 있으므로
  DB 행 index k = (k+1)봉전.
  B(0~59봉) = 오늘 거래대금 + DB index 0~58
  E(60~119봉) = DB index 59~118  → DB에 최소 119거래일 필요

거래대금: DB의 trade_value(실제 거래대금, 2026-10-06부터 수집)를 쓰고,
  비어 있는 과거 행은 종가×거래량 근사치로 대신한다(경계값 근처는 키움과
  다를 수 있음 — 수집이 쌓이면 자연히 해소).
"""
import os
import re
import sqlite3
import datetime

_BASE     = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
THEME_DB  = os.path.join(_BASE, "lina_bot", "kr_theme_finance.db")
KST       = datetime.timezone(datetime.timedelta(hours=9))

# ── 키움 조건식 기준값 (키움 거래대금 단위: 일봉 백만원 → 여기선 원) ──
B_LOOKBACK        = 60
B_MIN_VALUE       = 200_000_000_000   # 2,000억
E_OFFSET          = 60
E_WINDOW          = 60
E_MAX_VALUE       = 30_000_000_000    # 300억
E_MIN_COUNT       = 60
F_MIN_VOL_RATIO   = 80.0              # %
G_MIN_VALUE       = 2_000_000_000     # 20억
H_MIN, H_MAX      = 3.0, 12.0         # %
MA_LEN            = 120
I_MIN, I_MAX      = 100.0, 140.0      # %
A_MIN, A_MAX      = 100.0, 1000.0     # %

NEED_ROWS = E_OFFSET + E_WINDOW - 1   # 119 — DB index 0~118


def _value(close, volume, trade_value):
    """(거래대금, 근사여부). trade_value가 없으면 종가×거래량."""
    if trade_value:
        return trade_value, False
    return (close or 0) * (volume or 0), True


def _name_code_map(conn) -> dict:
    """kr_theme_stocks의 '종목명KOSPI 005930' → {종목명: 코드}."""
    out = {}
    for (raw,) in conn.execute("SELECT DISTINCT stock_name FROM kr_theme_stocks"):
        m = re.search(r"([0-9A-Z]{6})$", (raw or "").strip())
        if not m:
            continue
        name = re.sub(r"\s*KOS(?:PI|DAQ)\s*[0-9A-Z]{6}$", "", raw).strip()
        out.setdefault(name, m.group(1))
    return out


def build_universe(db_path: str = THEME_DB, today: str = None) -> dict:
    """B·E(과거 일봉 조건)를 통과한 종목 = 오늘 장중에 볼 후보.
    반환: {"date", "latest_db_date", "scanned", "items": [ {...}, ... ]}"""
    today = today or datetime.datetime.now(KST).strftime("%Y-%m-%d")
    conn  = sqlite3.connect(db_path, timeout=10)
    conn.execute("PRAGMA query_only = ON")
    try:
        name_code = _name_code_map(conn)
        # 어제까지 중 가장 최근 거래일 — 이 날짜 행이 없는 종목은(수집 누락/
        # 거래정지) 봉 번호가 어긋나므로 제외
        latest = conn.execute(
            "SELECT MAX(date) FROM kr_stock_daily_data WHERE date < ?", (today,)
        ).fetchone()[0]
        # 종목마다 쿼리하면 (stock_name 인덱스가 없어) 테이블을 종목 수만큼 훑음 —
        # 필요한 기간(최근 119거래일)을 한 번에 읽어 파이썬에서 묶는다.
        dates = [r[0] for r in conn.execute(
            "SELECT DISTINCT date FROM kr_stock_daily_data WHERE date < ? "
            "ORDER BY date DESC LIMIT ?", (today, NEED_ROWS))]
        by_name: dict = {}
        if dates:
            for name, d, c, v, tv in conn.execute("""
                SELECT stock_name, date, close_price, volume, trade_value
                FROM kr_stock_daily_data WHERE date BETWEEN ? AND ?
                ORDER BY stock_name, date DESC
            """, (dates[-1], dates[0])):
                by_name.setdefault(name, []).append((d, c, v, tv))
        items, scanned = [], 0
        for name, code in name_code.items():
            rows = by_name.get(name, [])[:NEED_ROWS]
            if len(rows) < NEED_ROWS or rows[0][0] != latest:
                continue
            scanned += 1
            vals = [_value(c, v, tv) for _, c, v, tv in rows]

            # E: DB index 59~118 전부 0 ~ 300억 (60회 이상)
            e_vals = vals[E_OFFSET - 1:E_OFFSET - 1 + E_WINDOW]
            if sum(1 for val, _ in e_vals if 0 <= val <= E_MAX_VALUE) < E_MIN_COUNT:
                continue
            # B: DB index 0~58 중 2,000억 이상 1회 이상(오늘분은 장중에 추가 판단)
            b_vals  = vals[:B_LOOKBACK - 1]
            b_hits  = [(rows[i][0], val) for i, (val, _) in enumerate(b_vals) if val >= B_MIN_VALUE]
            if not b_hits:
                continue
            closes = [c for _, c, _, _ in rows]
            items.append({
                "name":         name,
                "code":         code,
                "prev_close":   rows[0][1],
                "prev_volume":  rows[0][2],
                # I: 120일선 = (오늘 현재가 + 직전 119일 종가) / 120 — 오늘가는 장중에 더함
                "ma_sum_119":   sum(closes[:MA_LEN - 1]),
                "b_spike_date": max(b_hits, key=lambda x: x[1])[0],
                "b_spike_value": max(v for _, v in b_hits),
                "approx_value": any(a for _, a in vals),   # 근사 거래대금이 섞였는지
            })
        return {"date": today, "latest_db_date": latest, "scanned": scanned, "items": items}
    finally:
        conn.close()


def _f(d: dict, key: str) -> float:
    try:
        return float(d.get(key, 0) or 0)
    except (TypeError, ValueError):
        return 0.0


def check_candidates(api, universe: dict, with_strength: bool = True) -> list:
    """후보마다 F·G·H·I(+B 오늘분)를 한투 현재가로 확인, 다 통과하면 A(체결강도) 조회.
    반환: [{"name","code","price","chg","value","vol_ratio","disparity",
            "strength","passed": bool, "fails": [실패한 조건]}, ...]"""
    results = []
    for it in universe.get("items", []):
        md = api.get_market_data(it["code"]) or {}
        price = _f(md, "stck_prpr")
        if price <= 0:
            continue
        chg       = _f(md, "prdy_ctrt")
        value     = _f(md, "acml_tr_pbmn")
        volume    = _f(md, "acml_vol")
        vol_ratio = volume / it["prev_volume"] * 100 if it.get("prev_volume") else 0.0
        ma120     = (it["ma_sum_119"] + price) / MA_LEN
        disparity = price / ma120 * 100 if ma120 > 0 else 0.0

        fails = []
        if not (H_MIN <= chg <= H_MAX):                 fails.append(f"H등락률{chg:+.1f}%")
        if value < G_MIN_VALUE:                         fails.append(f"G거래대금{value/1e8:.0f}억")
        if vol_ratio < F_MIN_VOL_RATIO:                 fails.append(f"F거래량{vol_ratio:.0f}%")
        if not (I_MIN <= disparity <= I_MAX):           fails.append(f"I이격도{disparity:.0f}%")

        strength = None
        if not fails and with_strength:
            strength = api.get_execution_strength(it["code"])
            if strength is None:
                fails.append("A체결강도 조회실패")
            elif not (A_MIN <= strength <= A_MAX):
                fails.append(f"A체결강도{strength:.0f}%")

        results.append({
            "name": it["name"], "code": it["code"], "price": price, "chg": chg,
            "value": value, "vol_ratio": vol_ratio, "disparity": disparity,
            "strength": strength, "passed": not fails, "fails": fails,
            "b_spike_date": it.get("b_spike_date"), "b_spike_value": it.get("b_spike_value"),
            "approx_value": it.get("approx_value", False),
        })
    results.sort(key=lambda r: (not r["passed"], len(r["fails"]), -r["chg"]))
    return results


def format_hit(r: dict) -> str:
    st = f"{r['strength']:.0f}%" if r.get("strength") is not None else "-"
    approx = " (과거 거래대금 일부 근사치)" if r.get("approx_value") else ""
    return (f"📌 **{r['name']}**({r['code']}) {r['price']:,.0f}원 {r['chg']:+.2f}%\n"
            f"   거래대금 {r['value']/1e8:,.0f}억 | 거래량 전일비 {r['vol_ratio']:.0f}% | "
            f"이격도 {r['disparity']:.0f}% | 체결강도 {st}\n"
            f"   스파이크 {r.get('b_spike_date')} {r.get('b_spike_value', 0)/1e8:,.0f}억{approx}")


if __name__ == "__main__":
    # 장 밖에서도 후보(B·E 통과) 목록은 확인 가능: python three_month_leader.py
    u = build_universe()
    print(f"기준 {u['date']} | DB 최신 {u['latest_db_date']} | 검사 {u['scanned']}종목 → 후보 {len(u['items'])}개")
    for it in u["items"]:
        print(f"  {it['name']}({it['code']}) 스파이크 {it['b_spike_date']} "
              f"{it['b_spike_value']/1e8:,.0f}억{' (근사)' if it['approx_value'] else ''}")
