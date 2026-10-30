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
★ 10-31 새벽 대장 결정(분봉 검증 backtest/dip_leader_minute.py·dip_situations_review.py, 117 하락일):
  장중 반등 진입(현행)은 1년 -0.86%/건 — 시각 고정 10:00~14:00도 전부 마이너스, 14:30만 앞뒤 절반 모두 플러스.
  상황: 지수 '첫 하락날'(전날까지 연속하락 0일)과 '20일 고점 -15%↓ 무너진 장'은 일관 마이너스(7월 -16→-31% 사례) → 그날은 안 삼.
  → 켜지는 날: 지수(그날 더 빠진 코스피·코스닥) 장중 -2%↓ & 전날까지 연속하락 ≥1일 & 전날 종가가 20일 고점 -15% 이내.
  → 14:30에 1종목. 매도: +5%에 50% 익절 → 나머지 고점 -5% 트레일링(본전 보호), 그 전엔 손절 -10%, 산 날 포함 5거래일째 장 끝 정리.
    (대장: "단타는 짧은 수익·회전율, 일주일까지", "50만 들어가면 5만은 줄 생각", "본전 보호하고 도박하자")
  D일 44건(14:30·전체): +5% 전량 +0.63%/건 → 50%+트레일 +0.94%/건(앞 +1.43·뒤 +0.44), 최악 -10.6%.
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
INDEX_REBOUND = 0.5            # (10-30 장중 진입용 — 10-31 14:30 진입으로 바꾸며 안 씀, index_ready 참고용)
R60_MIN, VALUE_MIN_EOK = 30.0, 100.0
START = "1430"                 # ★ 10-31 14:30 진입(장중 반등·시각 고정 진입은 분봉 검증 마이너스)
MIN_STREAK, MAX_DD20 = 1, 15.0  # ★ 10-31 지수 전날까지 연속하락 ≥1일 & 전날 종가 20일 고점 대비 -15% 이내
INDEX_ETF = {"kospi": "069500", "kosdaq": "229200"}   # 지수 일봉 대용(KODEX 200·코스닥150)
TP_RATIO, TRAIL_PCT, STOP_PCT, HOLD_DAYS = 0.5, 5.0, 10.0, 5
# ★ 10-30 12:20 대장: "대상 종목 중 우리 NEW나 SUB에 중복되는 놈만 — 알멕은 3일 연속 패대기(그런 놈은 안 됨). NEW·SUB는 심혈을
#   기울여 모은 종목, 그냥 들고 가도 회복될 확률이 높은 녀석들" → 한투 관심그룹 NEW·SUB와 겹치는 종목만(sbot과 같은 그룹 이름)
WATCH_GROUP_NAMES = ("new", "신규추천", "신규", "new추천", "sub", "서브", "고정")
TP_PCT = 5.0
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


def watch_codes(api, hts_id: str) -> set:
    """한투 관심그룹 NEW·SUB 종목 코드(대장이 고른 종목). 조회 실패면 빈 set(→ 음봉 엔진 안 삼)."""
    if not hts_id:
        return set()
    out = set()
    for gc, gn in (api.get_watchlist_groups(hts_id) or {}).items():
        if (gn or "").lower() in WATCH_GROUP_NAMES:
            out |= {c for c, _ in (api.get_watchlist_stocks(gc, hts_id) or [])}
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


def low_change(st: dict) -> dict:
    """index_status 결과 → {지수: 당일 저가의 전일 대비 %}."""
    return {k: ((1 + chg / 100) / (1 + reb / 100) - 1) * 100 for k, (chg, reb) in st.items()}


def index_hit(st: dict) -> bool:
    """★ 10-31 오늘 장중에 -INDEX_DROP%까지 빠진 지수가 있나(지금 반등했어도 됨)."""
    return any(v <= -INDEX_DROP for v in low_change(st).values())


def trend_tags(closes: list) -> dict:
    """전날까지 종가(오래된→최신) → {"streak": 연속하락일수, "dd20": 20일 고점 대비 %}."""
    streak = 0
    for i in range(len(closes) - 1, 0, -1):
        if closes[i] < closes[i - 1]:
            streak += 1
        else:
            break
    hi = max(closes[-20:]) if closes else 0
    return {"streak": streak, "dd20": (closes[-1] / hi - 1) * 100 if hi else 0.0}


def situation(api, st: dict, today: str):
    """★ 10-31 그날 더 빠진 지수(장중 저가 기준)의 전날까지 추세 → (켤까?, 설명). 조회 실패면 (False, 사유) — 안 삼."""
    lows = low_change(st)
    if not lows:
        return False, "지수 조회 실패"
    key = min(lows, key=lows.get)
    try:
        rows = api.get_daily_ohlc(INDEX_ETF[key], days=30) or []
    except Exception as e:
        return False, f"{key} 일봉 조회 실패({e})"
    closes = [r["close"] for r in sorted(rows, key=lambda r: r["date"]) if r.get("date") and r["date"] < today and r.get("close")]
    if len(closes) < 21:
        return False, f"{key} 일봉 부족({len(closes)})"
    t = trend_tags(closes)
    ok = t["streak"] >= MIN_STREAK and t["dd20"] > -MAX_DD20
    why = (f"{key} 장중저가 {lows[key]:+.1f}% · 전날까지 연속하락 {t['streak']}일 · 20일고점 {t['dd20']:+.1f}%"
           + ("" if ok else (" — 첫 하락날이라 안 삼" if t["streak"] < MIN_STREAK else f" — -{MAX_DD20:g}%↓ 무너진 장이라 안 삼(스윙 몫)")))
    return ok, why


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
