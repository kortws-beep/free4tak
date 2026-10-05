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

가짜 거르기(키움 조건식 밖, daybot _check_spike_quality와 같은 기준):
  B 구간(60봉) 최대 거래대금일 = 스파이크. 조건식은 거래대금만 보므로
  "대금만 터지고 밀린" 설거지를 따로 거른다.
  기준1 스파이크일 종가가 당일 고저폭의 65% 이상 위치 (윗꼬리 길면 탈락)
  기준3 스파이크일 전일 대비 상승률 7% 이상
  기준2 현재가 > 스파이크 직전 5일 평균 종가 × 1.05 (되돌아왔으면 탈락) — 장중 판단
  고가/저가·직전 데이터가 없으면 통과(보조 필터가 본 조건을 막지 않게).

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

# ── 가짜 거르기 (daybot SPIKE_* 와 동일 값) ──
SPIKE_LOOKBACK      = 60      # DB index 0~59 (오늘 제외 직전 60봉)
SPIKE_CLOSE_POS_MIN = 0.65    # 기준1
SPIKE_MIN_RETURN    = 7.0     # 기준3 (%)
SPIKE_RETRACE_MAX   = 1.05    # 기준2

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


def _spike_quality(rows, vals):
    """B 구간 최대 거래대금일의 모양 검사(기준1·3) + 기준2용 직전 5일 평균.
    rows: 최신→과거 (date, close, volume, trade_value, high, low).
    반환: (실패사유 or None, 스파이크 index, 직전5일 평균종가 or None)"""
    window = vals[:SPIKE_LOOKBACK]
    idx = max(range(len(window)), key=lambda i: window[i][0])
    _, close, _, _, high, low = rows[idx]
    if high and low and high > low and close:
        pos = (close - low) / (high - low)
        if pos < SPIKE_CLOSE_POS_MIN:
            return f"가짜:종가위치{pos:.0%}", idx, None
    if idx + 1 < len(rows):
        prev = rows[idx + 1][1]
        if prev and close:
            ret = (close - prev) / prev * 100
            if ret < SPIKE_MIN_RETURN:
                return f"가짜:상승률{ret:.1f}%", idx, None
    pre = [r[1] for r in rows[idx + 1: idx + 6] if r[1]]
    base = sum(pre) / len(pre) if len(pre) >= 3 else None
    return None, idx, base


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
        # 넉넉한 달력 구간을 한 번에 읽고, 종목별로 "자기 기록의 최근 119행"을 쓴다.
        # ★ 2026-10-06: 처음엔 전 종목 합친 DISTINCT 날짜 119개로 창을 잡았는데,
        #   재수집 안 된 일부 종목에 남은 공휴일 가짜행 날짜까지 거래일로 세는 바람에
        #   창이 실제 115거래일로 좁아져 정상 종목이 전부 "기록부족"이 됐음.
        by_name: dict = {}
        dates = []
        if latest:
            since = (datetime.date.fromisoformat(latest)
                     - datetime.timedelta(days=NEED_ROWS * 2 + 30)).isoformat()
            for name, d, c, v, tv, hi, lo in conn.execute("""
                SELECT stock_name, date, close_price, volume, trade_value, high_price, low_price
                FROM kr_stock_daily_data WHERE date >= ? AND date < ?
                ORDER BY stock_name, date DESC
            """, (since, today)):
                by_name.setdefault(name, []).append((d, c, v, tv, hi, lo))
            dates = [since, latest]
        items, scanned = [], 0
        # ★ 후보 0개일 때 원인을 바로 알 수 있게 탈락 사유를 센다
        skip = {"일봉없음": 0, "기록부족(119일 미만)": 0, "최신일 누락": 0, "E탈락": 0, "B탈락": 0,
                "가짜(설거지)": 0}
        fakes = []   # [(종목명, 사유)] — 키움 결과와 대조용
        for name, code in name_code.items():
            rows = by_name.get(name, [])[:NEED_ROWS]
            if not rows:
                skip["일봉없음"] += 1; continue
            if len(rows) < NEED_ROWS:
                skip["기록부족(119일 미만)"] += 1; continue
            if rows[0][0] != latest:
                skip["최신일 누락"] += 1; continue
            scanned += 1
            vals = [_value(c, v, tv) for _, c, v, tv, _, _ in rows]

            # E: DB index 59~118 전부 0 ~ 300억 (60회 이상)
            e_vals = vals[E_OFFSET - 1:E_OFFSET - 1 + E_WINDOW]
            if sum(1 for val, _ in e_vals if 0 <= val <= E_MAX_VALUE) < E_MIN_COUNT:
                skip["E탈락"] += 1; continue
            # B: DB index 0~58 중 2,000억 이상 1회 이상(오늘분은 장중에 추가 판단)
            b_vals  = vals[:B_LOOKBACK - 1]
            b_hits  = [(rows[i][0], val) for i, (val, _) in enumerate(b_vals) if val >= B_MIN_VALUE]
            if not b_hits:
                skip["B탈락"] += 1; continue
            fake, s_idx, pre_base = _spike_quality(rows, vals)
            if fake:
                skip["가짜(설거지)"] += 1
                fakes.append((name, f"{fake} (스파이크 {rows[s_idx][0]})")); continue
            closes = [r[1] for r in rows]
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
                "pre_spike_base": pre_base,                # 기준2: 현재가가 이 ×1.05 이하면 탈락
            })
        return {"date": today, "latest_db_date": latest, "scanned": scanned, "items": items,
                "skip": skip, "fakes": fakes, "window_from": dates[0] if dates else None}
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
        base = it.get("pre_spike_base")
        if base and price <= base * SPIKE_RETRACE_MAX:  fails.append(f"가짜:되돌림(스파이크전 {base:,.0f})")

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
    print(f"   조회 구간: {u['window_from']} ~ {u['latest_db_date']} (종목별 최근 {NEED_ROWS}거래일 사용)")
    print("   탈락 사유: " + ", ".join(f"{k} {v}" for k, v in u["skip"].items()))
    for it in u["items"]:
        base = it.get("pre_spike_base")
        print(f"  {it['name']}({it['code']}) 스파이크 {it['b_spike_date']} "
              f"{it['b_spike_value']/1e8:,.0f}억{' (근사)' if it['approx_value'] else ''}"
              + (f" | 되돌림선 {base * SPIKE_RETRACE_MAX:,.0f}원" if base else ""))
    for name, why in u.get("fakes", []):
        print(f"  ✂️ {name} — {why}")
