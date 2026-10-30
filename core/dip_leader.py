"""
dip_leader.py — 데이봇 '음봉 엔진': 지수 급락일 중기 주도주 급락 매수 (2026-10-30, 대장 결정)
================================================================
[이 파일이 하는 일 — 비개발자용 설명]
대장: "하락장에서는 종합주가 추이를 보면서 유망했던 종목이 많이 빠졌으면 -이더라도 하나 정도는 매수해서 기다려야 —
       오늘이 아니라 내일이라도 회복. 단타는 음봉 영역에서도 들어갈 자리가 보이면 들어가야."
검증(backtest/dip_leader_review.py, 일봉 2024-11~2026-10): 지수(코스피·코스닥 중 하나) -2%↑ 하락일에
  60일 +30%↑ 오른 주도주 & 20일 평균 거래대금 100억↑ 종목이 오늘 -5%↓ & 5일 고점 대비 -10%↓ → 종가 매수
  1,308건 다음날 +3% 닿음 61%, 다음날 종가 +1.00%(앞 +0.85·뒤 +1.50). 최근 5일 단기 테마주는 오히려 손실(-0.69%).
대장 결정(10-30): 하락장 1슬롯과 공유(비어 있을 때만), +3% 닿으면 전량 매도·안 되면 데이봇 기존 규칙(손절 -3.5%·트레일링).
★ 10-30 12:10 대장: "14:30 매수는 종가배팅" → 장중 진입: "종합주가를 계속 체크하잖아 — 당일 최저가 대비 0.5% 반등 시 대상 종목 중
  당일 최저가 대비 가장 많이 오른 놈". (장중 진입은 아직 분봉 검증 전 — 실전으로 확인)
여기는 계산만(주문은 daybot.py):
  universe(): 어제까지 일봉으로 '중기 주도주' 목록 {코드: {name, value_eok, r60, hi5}} — 하루 한 번
  index_status(): 네이버 지수(코스피·코스닥) 지금 등락·당일 저가 대비 반등
  index_ready(): 둘 중 하나라도 -INDEX_DROP% 이하이면서 그 지수가 저가 대비 +INDEX_REBOUND% 이상
  pick(): 지금 시세로 조건(오늘 -DAY_DROP%↓ & 5일 고점(오늘 고가 포함) -FROM_HIGH%↓) 통과 → 당일 저가 대비 반등 큰 순
"""
import os
import sqlite3

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DAILY_DB = os.path.join(_BASE, "lina_bot", "kr_theme_finance.db")
INDEX_DROP, DAY_DROP, FROM_HIGH = 2.0, 5.0, 10.0
INDEX_REBOUND = 0.5            # 지수가 당일 저가 대비 +0.5% 반등하면 진입(대장)
R60_MIN, VALUE_MIN_EOK = 30.0, 100.0
START = "1000"                 # 장 초반 변동이 잦아든 뒤부터 매수마감(15:20)까지(종가배팅 아님)
TP_PCT = 3.0
TAG = "dip_leader"


def universe(today: str, db: str = DAILY_DB) -> dict:
    """today(YYYY-MM-DD) 전까지 일봉으로 중기 주도주 → {코드: {"name","value_eok","r60","hi5"}}."""
    conn = sqlite3.connect(db, timeout=10)
    try:
        dates = [d for (d,) in conn.execute("SELECT DISTINCT date FROM kr_stock_daily_data WHERE date < ? "
                                            "ORDER BY date DESC LIMIT 61", (today,))]
        if len(dates) < 61:
            return {}
        d0, d60, last5, last20 = dates[0], dates[60], set(dates[:5]), set(dates[:20])
        codes = {n: c for c, n in conn.execute("SELECT code, name FROM kr_stock_master")}
        rows = conn.execute("SELECT stock_name, date, close_price, high_price, trade_value FROM kr_stock_daily_data "
                            "WHERE date >= ? AND date < ?", (d60, today)).fetchall()
    finally:
        conn.close()
    per = {}
    for n, d, c, h, v in rows:
        per.setdefault(n, {})[d] = (c or 0, h or 0, v or 0)
    out = {}
    for n, s in per.items():
        code = codes.get(n)
        if not code or d0 not in s or d60 not in s or not s[d60][0]:
            continue
        r60 = (s[d0][0] / s[d60][0] - 1) * 100
        vals = [s[d][2] for d in last20 if d in s and s[d][2]]
        value = sum(vals) / len(vals) / 1e8 if vals else 0
        if r60 >= R60_MIN and value >= VALUE_MIN_EOK:
            out[code] = {"name": n, "value_eok": value, "r60": r60,
                         "hi5": max(s[d][1] for d in last5 if d in s)}
    return out


def _num(v) -> float:
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return 0.0


def index_status(get=None) -> dict:
    """네이버 지수 → {"kospi": (등락%, 저가 대비 반등%), "kosdaq": (…)} · 실패한 지수는 빠짐."""
    import requests
    get = get or (lambda u: requests.get(u, headers={"User-Agent": "Mozilla/5.0"}, timeout=5).json())
    out = {}
    for sym, key in (("KOSPI", "kospi"), ("KOSDAQ", "kosdaq")):
        try:
            info = {i.get("code"): i.get("value") for i in get(f"https://m.stock.naver.com/api/index/{sym}/integration")
                    .get("totalInfos", [])}
            cur = _num(get(f"https://m.stock.naver.com/api/index/{sym}/basic").get("closePrice"))
            prev, low = _num(info.get("lastClosePrice")), _num(info.get("lowPrice"))
            if cur > 0 and prev > 0 and low > 0:
                out[key] = ((cur / prev - 1) * 100, (cur / low - 1) * 100)
        except Exception:
            continue
    return out


def index_ready(st: dict) -> bool:
    """-INDEX_DROP%↓ 빠진 지수가 당일 저가 대비 +INDEX_REBOUND% 이상 반등했나."""
    return any(chg <= -INDEX_DROP and reb >= INDEX_REBOUND for chg, reb in st.values())


def index_down(idx: dict) -> bool:
    """idx = {"kospi": %, "kosdaq": %}(get_market_index) — 조회 실패(0.0)는 하락 아님."""
    return min(idx.get("kospi", 0.0) or 0.0, idx.get("kosdaq", 0.0) or 0.0) <= -INDEX_DROP


def pick(uni: dict, quotes: dict, exclude: set = frozenset()) -> list:
    """→ [(코드, 이름, 현재가, 등락%, 5일고점 대비%, 당일저가 대비 반등%)] 반등 큰 순(대장: '최저가 대비 가장 많이 오른 놈')."""
    out = []
    for code, u in uni.items():
        q = quotes.get(code)
        if code in exclude or not q or q.get("price", 0) <= 0:
            continue
        price, chg = q["price"], q.get("chg", 0.0)
        hi = max(u["hi5"], q.get("high", 0) or 0)
        low = q.get("low", 0) or 0
        if chg <= -DAY_DROP and hi > 0 and price <= hi * (1 - FROM_HIGH / 100):
            bounce = (price / low - 1) * 100 if low > 0 else 0.0
            out.append((bounce, u["value_eok"], code, u["name"], price, chg, (price / hi - 1) * 100))
    out.sort(reverse=True)
    return [(c, n, p, ch, fh, b) for b, _, c, n, p, ch, fh in out]
