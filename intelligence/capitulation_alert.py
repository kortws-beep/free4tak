"""
capitulation_alert.py — 투매일 반등 섹터 알림 (2026-10-14, 시나리오 1번)
================================================================
[이 파일이 하는 일 — 비개발자용 설명]

검증(backtest/capitulation_sector.py, 투매일 15일): 20일 신저가 종목이 40%+인 투매일 종가에 사서
다음 날 아침에 승부 → 다음 날 고가 +5% 도달 45%(평소 26%). 결합 1등 섹터 대표주 +7% 지정가 익절,
다음 날 시가가 매수가 밑이면 시가 정리 → 거래당 +0.90%(평소 -0.22%). 못 닿은 걸 종가까지 들면
이익을 다 까먹음(투매가 하루 더 이어진 날) → 아침 규칙이 핵심.
대장: "사실 오늘 반등이 나오긴 했는데 종가는 음봉 — 아직 하락장이 안 끝났다" → 알림은 매수 지시가
아니라 '조건이 왔다'는 알림. 비중 50%, 판단은 대장.

리나가 하는 일:
  15:40(거래일) — 장 마감 뒤 전 종목(약 2,500) 그날 시세로 '오늘 20일 신저가' 비율 계산(★ 10-14 대장:
                 14:20 대신 장 끝나고 — 정확한 종가로 판단하고, 시간외 종가매매(15:40~16:00)는 종가 그대로
                 체결이라 '바닥날 종가 매수' 검증과 똑같음). 멀티종목 시세(30개씩)로 1분 안쪽.
                 기준 이상이면 관심그룹별 대표주(거래대금 1~3등)의 오후반등(저가 대비 종가)으로 줄 세워 알림.
  다음 날 08:50 — 아침 규칙 다시 알림(시가가 어제 종가 밑이면 시가 정리, 아니면 +7% 지정가).
  !투매 — 언제든 지금 상태 점검(기준 미달이어도 숫자 보여줌).
※ 기준(CAP_NL, 기본 40%)은 전 종목으로 검증한 값 — 그래서 전 종목으로 계산(상위 400은 안 맞았음).
"""
import os
import re
import json
import sqlite3
import statistics
import datetime

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
THEME_DB = os.path.join(_BASE, "lina_bot", "kr_theme_finance.db")
GROUPS_JSON = os.path.join(_BASE, "backtest", "data", "watch_groups.json")
STATE = os.path.join(_BASE, "lina_bot", "cap_alert_state.json")
CAP_NL = float(os.getenv("LINA_CAP_NL", "40"))
# ★ 10-14: 처음엔 상위 400으로 했는데 맞춰보니 투매는 중소형주에서 나와 상위 400 비율이 훨씬 낮음
#   (05-29 전 종목 46.9% vs 상위400 13.2% — 다음 날 대박이던 날을 놓침). 검증과 같게 전 종목(0)으로.
#   조회 부담이 문제면 .env LINA_CAP_UNIVERSE=400 (기준도 같이 다시 맞춰야 함).
UNIVERSE = int(os.getenv("LINA_CAP_UNIVERSE", "0"))
LOOKBACK = 20
LEADERS = 3
TOP_GROUPS = 3
TP_PCT = 7
NEW_GROUP_NAMES = ("new", "신규추천", "신규", "new추천")
_EXCLUDE = ("KODEX", "TIGER", "KBSTAR", "ARIRANG", "HANARO", "KOSEF", "ACE ", "SOL ", "RISE ", "PLUS ",
            "ETN", "스팩", "레버리지", "인버스")


def excluded(name: str) -> bool:
    return any(w in name for w in _EXCLUDE) or name.endswith("우") or name.endswith("우B")


def load_universe(theme_db: str = THEME_DB, top: int = UNIVERSE, today: str = None) -> dict:
    """{종목명: 직전 20거래일 최저가} — 마지막 거래일(오늘 제외) 거래대금 상위 top(0이면 전 종목)."""
    today = today or datetime.date.today().isoformat()
    conn = sqlite3.connect(theme_db)
    try:
        dates = [d for (d,) in conn.execute("SELECT DISTINCT date FROM kr_stock_daily_data WHERE date < ? "
                                            "ORDER BY date DESC LIMIT ?", (today, LOOKBACK))]
        if not dates:
            return {}
        rows = conn.execute("SELECT stock_name, date, low_price, trade_value FROM kr_stock_daily_data "
                            "WHERE date >= ? AND date < ?", (min(dates), today)).fetchall()
    finally:
        conn.close()
    lows, val = {}, {}
    for n, d, lo, tv in rows:
        if excluded(n) or not lo:
            continue
        lows[n] = min(lows.get(n, lo), lo)
        if d == dates[0]:
            val[n] = tv or 0
    names = sorted((n for n in val if n in lows), key=lambda n: -val[n])
    if top:
        names = names[:top]
    return {n: lows[n] for n in names}


def name_code_map(theme_db: str = THEME_DB) -> dict:
    out = {}
    conn = sqlite3.connect(theme_db)
    try:
        for (raw,) in conn.execute("SELECT DISTINCT stock_name FROM kr_theme_stocks"):
            m = re.search(r"([0-9A-Z]{6})$", (raw or "").strip())
            if m:
                out.setdefault(re.sub(r"\s*KOS(?:PI|DAQ)\s*[0-9A-Z]{6}$", "", raw).strip(), m.group(1))
        try:
            for code, name in conn.execute("SELECT code, name FROM kr_stock_master"):
                out.setdefault(name, code)
        except sqlite3.OperationalError:
            pass
    finally:
        conn.close()
    return out


def load_groups(path: str = GROUPS_JSON) -> dict:
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return {g: [tuple(x) for x in v] for g, v in json.load(f).items()}


def _one(api, c):
    d = api.get_market_data(c) or {}
    try:
        px = float(d.get("stck_prpr") or 0)
        lo = float(d.get("stck_lwpr") or 0)
        op = float(d.get("stck_oprc") or 0)
        if px > 0 and lo > 0 and op > 0:
            return {"open": op, "high": float(d.get("stck_hgpr") or px), "low": lo, "price": px,
                    "value": float(d.get("acml_tr_pbmn") or 0), "chg": float(d.get("prdy_ctrt") or 0)}
    except (TypeError, ValueError):
        pass
    return None


def snapshot(api, codes, pause: float = 0.1) -> dict:
    """{코드: {"open","high","low","price","value","chg"}} — 한투 현재가. 실패한 종목은 빠짐.
    ★ 10-14: 한 종목씩이면 2,500종목에 10분+ → 기존 복수시세(get_multi_price, 30개씩 — 주도주·단타 스캔이
    쓰는 검증된 함수)로 먼저 받고, 저가·시가를 못 읽은 것만 한 종목씩 보충."""
    import time
    codes = list(dict.fromkeys(codes))
    out = {}
    multi = getattr(api, "get_multi_price", None)
    answered = set()
    if multi:
        got = multi(codes, pause=pause) or {}
        answered = set(got)
        for c, q in got.items():
            if q.get("price", 0) > 0 and q.get("low", 0) > 0 and q.get("open", 0) > 0:
                out[c] = {k: q.get(k, 0) for k in ("open", "high", "low", "price", "value", "chg")}
        # ★ 10-15: 장 전(07:20 !투매)엔 복수시세가 시가·저가 0으로 와서 '못 읽음'으로 보고 2,500종목을
        #   한 종목씩 다시 받느라 10분+ 돌았음(받아도 장 전이라 0) → 응답은 왔는데 값이 0이면 보충 안 함.
        if answered and not out:
            print("⚠️ [투매알림] 복수시세에 오늘 시가·저가가 없음(장 시작 전?) — 보충 조회 안 함")
            return {}
    for c in codes:
        if c in out or c in answered:
            continue
        r = _one(api, c)
        if r:
            out[c] = r
        time.sleep(pause)
    return out


def evaluate(univ: dict, snap: dict, groups: dict, code_of: dict) -> dict:
    """univ {이름: 20일 최저}, snap {코드: 시세}, groups {그룹: [(코드, 이름)]} → 판단 재료."""
    seen = [(n, snap[code_of[n]], lo20) for n, lo20 in univ.items() if code_of.get(n) in snap]
    res = {"n": len(seen), "share": 0.0, "o2l": 0.0, "l2c": 0.0, "chg": 0.0, "groups": []}
    if not seen:
        return res
    res["share"] = sum(s["low"] <= lo20 for _, s, lo20 in seen) / len(seen) * 100
    res["o2l"] = statistics.median((s["low"] / s["open"] - 1) * 100 for _, s, _ in seen)
    res["l2c"] = statistics.median((s["price"] / s["low"] - 1) * 100 for _, s, _ in seen)
    res["chg"] = statistics.median(s["chg"] for _, s, _ in seen)
    gs = []
    for g, members in groups.items():
        if g.strip().lower() in NEW_GROUP_NAMES:
            continue
        ms = [(n, c, snap[c]) for c, n in members if c in snap]
        if len(ms) < 3:
            continue
        lead = sorted(ms, key=lambda x: -x[2]["value"])[:LEADERS]
        aft = statistics.mean((s["price"] / s["low"] - 1) * 100 for _, _, s in lead)
        gs.append({"group": g, "aft": aft, "chg": statistics.mean(s["chg"] for _, _, s in lead),
                   "leaders": [(n, c, (s["price"] / s["low"] - 1) * 100, s["chg"], s["price"]) for n, c, s in lead]})
    res["groups"] = sorted(gs, key=lambda x: -x["aft"])
    return res


def format_alert(res: dict, nl: float = CAP_NL, forced: bool = False) -> str:
    hit = res["share"] >= nl
    head = ("🚨 **투매일 신호 — 반등 섹터 후보**" if hit
            else "📊 투매 점검 — 기준 미달" if forced else "")
    L = [head, f"   {res['n']}종목 중 오늘 20일 신저가 {res['share']:.0f}% (기준 {nl:.0f}%) · "
               f"시가→저가 {res['o2l']:+.1f}% · 저가→현재 {res['l2c']:+.1f}% · 등락 {res['chg']:+.1f}% (중앙값)"]
    if not hit and not forced:
        return ""
    for x in res["groups"][:TOP_GROUPS]:
        lead = " · ".join(f"{n} 저가대비 {a:+.1f}%(등락 {ch:+.1f}%)" for n, _, a, ch, _ in x["leaders"])
        g = x["group"].replace("_", "\\_")     # ★ 10-16: 디스코드가 _를 기울임으로 먹어 그룹명이 붙어 보였음
        L.append(f"   [{g}] 오후반등 {x['aft']:+.1f}% — {lead}")
    if hit:
        L.append("   📋 시나리오1: 시간외 종가(~16:00, 오늘 종가 그대로) 매수(비중 50%) → 내일 시가가 오늘 종가 밑이면 시가 정리, "
                 f"아니면 +{TP_PCT}% 지정가 · 못 닿으면 그날 정리")
        L.append("   ⚠️ 검증 거래당 +0.9% 수준(투매일 15일), 섹터 고르기 효과는 아직 불확실 — 판단은 대장. "
                 "하락장이 안 끝났으면(종가 음봉 계속) 비중 더 줄이기")
    return "\n".join(L)


def save_picks(res: dict, path: str = STATE, today: str = None):
    picks = [{"group": x["group"], "leaders": [(n, c) for n, c, *_ in x["leaders"]]} for x in res["groups"][:TOP_GROUPS]]
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"date": today or datetime.date.today().isoformat(), "share": res["share"],
                   "picks": picks, "reminded": False}, f, ensure_ascii=False)


def morning_reminder(path: str = STATE, today: str = None) -> str:
    """전날(이전 거래일) 알림이 있으면 아침 규칙 문구 — 한 번만(reminded 표시)."""
    today = today or datetime.date.today().isoformat()
    if not os.path.exists(path):
        return ""
    with open(path, encoding="utf-8") as f:
        st = json.load(f)
    if st.get("reminded") or st.get("date", "") >= today:
        return ""
    st["reminded"] = True
    with open(path, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False)
    names = ", ".join(n for p in st.get("picks", []) for n, _ in p["leaders"])
    return (f"⏰ **어제({st['date'][5:]}) 투매일 알림 종목 — 아침 규칙**\n"
            f"   {names}\n"
            f"   시가가 어제 종가 밑이면 시가에 정리 · 아니면 +{TP_PCT}% 지정가 걸고, 못 닿으면 오늘 안에 정리 "
            "(대장: 09:45 전후까지가 승부)")


def run(api, nl: float = CAP_NL, forced: bool = False) -> str:
    """15:40 자동/!투매 수동 — 알림 문구(기준 미달 자동이면 빈 문자열)."""
    if datetime.datetime.now().strftime("%H%M") < "0900":
        return ("⏰ 장 시작 전이라 오늘 시세(시가·저가)가 없어 — `!투매`는 09:00 이후에.\n"
                "   어제 결과는 서버에서 `python backtest/bottom_signal.py`(마지막 줄 '최근 10거래일')로 봐줘.") if forced else ""
    univ = load_universe()
    groups = load_groups()
    code_of = name_code_map()
    codes = [code_of[n] for n in univ if n in code_of] + [c for ms in groups.values() for c, _ in ms]
    res = evaluate(univ, snapshot(api, codes), groups, code_of)
    if not res["n"]:
        return "⚠️ 오늘 시세를 못 받았어(장 시작 전이거나 한투 조회 실패) — 잠시 뒤 다시 `!투매`" if forced else ""
    msg = format_alert(res, nl, forced)
    if res["share"] >= nl:
        save_picks(res)
    return msg


if __name__ == "__main__":
    import sys
    sys.path.insert(0, os.path.join(_BASE, "core"))
    from dotenv import load_dotenv
    load_dotenv(os.path.join(_BASE, ".env"))
    from kis_api import KisAPI
    print(run(KisAPI(), forced=True))
