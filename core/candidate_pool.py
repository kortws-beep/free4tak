"""
candidate_pool.py — 모멘텀/추세/완화/유튜브 후보 소스 (sbo2.py에서 이식)
================================================================
[유래]
2026-09-29, sbot×sbo2 통합 작업의 일부로 lina_bot/sbo2.py에 있던
get_candidates()와 그 헬퍼 함수들을 순수 함수 형태로 이식한 모듈.
원본 설계 의도(모멘텀/추세/완화/유튜브 4개 소스, 각 소스의 스코어링/
게이트 로직)는 그대로 유지하고, Sbo2 클래스 메서드였던 부분(self.positions/
self.candidates 참조)만 파라미터로 받는 순수 함수로 바꿨다.

[사용처]
bots/sbot.py가 이 모듈의 get_candidates()/refresh_*()를 호출해 자체
소스(키움조건검색/KIS new그룹/S7)와 합쳐 하나의 통합 후보풀을 구성한다.

[의존성]
swing_master.py/trend_analyzer.py는 lina_bot/에 그대로 남아있다
(lina_bot.py 스케줄러가 계속 씀) — 이 모듈이 lina_bot을 sys.path에
추가해서 재사용한다(intelligence/youtube_stock_monitor.py가 이미
쓰고 있는 검증된 패턴과 동일).
================================================================
"""
import os
import re
import sys
import time
import sqlite3

_HERE      = os.path.dirname(os.path.abspath(__file__))
_STOCK_BOT = os.path.dirname(_HERE)
for _d in ["core", "intelligence", "interface", "bots", "lina_bot", ""]:
    _p = os.path.join(_STOCK_BOT, _d)
    if _p not in sys.path:
        sys.path.insert(0, _p)

from common_utils import today_str

LINA_BOT_DIR = os.path.join(_STOCK_BOT, "lina_bot")
THEME_DB     = os.path.join(LINA_BOT_DIR, "kr_theme_finance.db")
MOMENTUM_DB  = os.path.join(_STOCK_BOT, "intelligence", "ai_momentum_picks.db")
YOUTUBE_DB   = os.path.join(_STOCK_BOT, "intelligence", "youtube_picks.db")


# ============================================================
# 슬롯 상수 (lina_bot/sbo2.py에서 그대로 이식 — SLOT_INTER/SLOT_TELE/
# SLOT_WATCHLIST/SLOT_POOL은 이미 sbo2에서도 죽은 레거시 슬롯이라
# 포팅하지 않음)
# ============================================================
SLOT_MOMENTUM = "momentum"
SLOT_TREND    = "trend"
SLOT_LIGHT    = "light"
SLOT_YOUTUBE  = "youtube"

SLOT_LABEL = {
    SLOT_MOMENTUM: "모멘텀",
    SLOT_TREND:    "추세",
    SLOT_LIGHT:    "완화",
    SLOT_YOUTUBE:  "유튜브",
}

CANDIDATE_CAP_PER_SLOT = 3       # 슬롯당 후보 상위 N개만 유지

# 유튜브 관심종목 필터 — sbo2.py 2026-09-25 기준값 그대로
YT_MENTION_DAYS      = 21
YT_MENTION_MIN_COUNT = 2
YT_MAX_RISE_PCT      = 0.15
WATCHLIST_REFRESH_SEC = 600  # 10분 캐시

_YT_WATCHLIST_CACHE = {"ts": 0.0, "names": set()}


# ============================================================
# 종목코드 조회 (lina_bot/sbo2.py get_stock_code/get_stock_name 이식)
# ============================================================
def get_stock_name(code: str) -> str:
    """코드 → 한글 종목명 조회 (kr_theme_stocks → kr_stock_master 순서)."""
    try:
        conn = sqlite3.connect(THEME_DB, timeout=5)
        row  = conn.execute("""
            SELECT stock_name FROM kr_theme_stocks
            WHERE stock_name LIKE ? LIMIT 1
        """, (f"%{code}%",)).fetchone()
        if row:
            conn.close()
            return re.sub(r'(KOSPI|KOSDAQ).*|\d{6}', '', row[0]).strip()
        # ★ 2026-10-06 — 테마DB(주달 크롤링, 4개월 전 스냅샷)에 없는
        #   신규상장/비테마 종목은 한투 공식 종목마스터(kr_stock_master,
        #   update_stock_master.py)에서 재조회(대장 지적 — 케이엔알시스템
        #   199430 사례로 발견).
        row = conn.execute("""
            SELECT name FROM kr_stock_master WHERE code = ? LIMIT 1
        """, (code,)).fetchone()
        conn.close()
        if row:
            return row[0]
    except Exception:
        pass
    return code


def get_stock_code(name: str) -> str:
    """kr_theme_finance.db 에서 종목명으로 코드 조회
    (kr_theme_stocks → kr_stock_master 순서)."""
    conn = None
    try:
        conn = sqlite3.connect(THEME_DB, timeout=5)
        # name 바로 뒤에 시장구분자가 붙는 행만 매칭(접두어 충돌 방지 —
        # "삼성전자" 조회시 "삼성전자우"까지 같이 걸리는 사고 방지)
        row = conn.execute("""
            SELECT DISTINCT stock_name FROM kr_theme_stocks
            WHERE stock_name LIKE ? OR stock_name LIKE ?
            LIMIT 1
        """, (f"{name}KOSPI %", f"{name}KOSDAQ %")).fetchone()
        if row:
            m = re.search(r'(\d{6})', row[0])
            if m:
                return m.group(1)
        row = conn.execute("""
            SELECT stock_name FROM kr_stock_daily_data
            WHERE stock_name = ?
            LIMIT 1
        """, (name,)).fetchone()
        if row:
            m = re.search(r'(\d{6})', row[0])
            if m:
                return m.group(1)
        # ★ 2026-10-06 — 위 두 소스(테마DB)에 없으면 한투 공식
        #   종목마스터에서 정확히 일치하는 이름으로 재조회.
        row = conn.execute("""
            SELECT code FROM kr_stock_master WHERE name = ? LIMIT 1
        """, (name,)).fetchone()
        if row:
            return row[0]
    except Exception as e:
        print(f"⚠️ [candidate_pool] 코드 조회 오류 {name}: {e}")
    finally:
        if conn is not None:
            conn.close()
    return ""


# ============================================================
# 완화트랙 최소 차트건전성 게이트
# ============================================================
def check_light_chart_health(stock_name: str, conn: sqlite3.Connection, api=None) -> dict:
    """
    "차트가 완전히 망가지지 않았다" 수준만 가볍게 확인. 세 패턴 중
    하나만 만족하면 통과: (A) 하락전환(저점 2~7일전+3%반등) (B) 박스권
    상단돌파임박(15일변동폭≤15%+상단근접) (C) 거래량서지(200일선위+
    52주고점-20%이내+거래량300%+양봉+짧은윗꼬리).
    + 60일선 대비 15% 이상 못 빠져있어야 함(완전 붕괴 배제).
    """
    rows = conn.execute("""
        SELECT close_price, volume FROM kr_stock_daily_data
        WHERE stock_name = ? ORDER BY date DESC LIMIT 260
    """, (stock_name,)).fetchall()
    closes  = [r[0] for r in rows if r[0] and r[0] > 0]
    volumes = [r[1] for r in rows if r[1] and r[1] > 0]
    if len(closes) < 30:
        return {}

    curr = closes[0]
    ma60 = sum(closes[:60]) / len(closes[:60]) if len(closes) >= 30 else 0
    if ma60 > 0 and curr < ma60 * 0.85:
        return {}

    window = closes[0:15]
    lo, hi = min(window), max(window)

    pattern = None
    idx_lo = window.index(lo)
    if 2 <= idx_lo <= 7 and lo > 0 and curr >= lo * 1.03:
        pattern = "하락전환"
    elif lo > 0 and (hi - lo) / lo <= 0.15 and curr >= hi * 0.97:
        pattern = "박스돌파임박"

    if not pattern and api and len(closes) >= 200 and len(volumes) >= 20:
        ma200 = sum(closes[:200]) / 200
        week52_high = max(closes[:252]) if len(closes) >= 252 else max(closes)
        if curr > ma200 and curr >= week52_high * 0.8:
            try:
                code = get_stock_code(stock_name)
                mdata = api.get_market_data(code) if code else None
                if mdata:
                    acml_vol = float(mdata.get("acml_vol", 0) or 0)
                    avg_vol20 = sum(volumes[:20]) / 20
                    day_open = float(mdata.get("stck_oprc", 0) or 0)
                    day_high = float(mdata.get("stck_hgpr", 0) or 0)
                    is_bullish   = day_open > 0 and curr > day_open
                    no_long_wick = day_high <= 0 or (day_high - curr) / curr <= 0.03
                    if (avg_vol20 > 0 and acml_vol >= avg_vol20 * 3.0
                            and is_bullish and no_long_wick):
                        pattern = "거래량서지"
            except Exception as e:
                print(f"⚠️ [candidate_pool] {stock_name} 거래량서지 조회 오류: {e}")

    if not pattern:
        return {}

    return {
        "pattern": pattern,
        "curr_price": curr,
        "stop_price": round(curr * 0.93, 0),
        "tgt_price":  round(curr * 1.12, 0),
    }


# ============================================================
# 겹침 점수 보정 (텔레그램/한경컨센서스/MBN뉴스/촉매)
# ============================================================
def get_mbn_news_names() -> set:
    """MBN골드 뉴스(service_id=10001)에서 종목명 매칭."""
    names = set()
    try:
        import requests as _req
        from bs4 import BeautifulSoup as _BS

        base_url = "https://www.mbngold.com"
        headers  = {"User-Agent": "Mozilla/5.0", "Referer": f"{base_url}/mg/mypage/login.php"}
        sess = _req.Session()
        sess.post(f"{base_url}/mg/mypage/login_action.php", headers=headers, data={
            "mode": "login", "rURL": f"{base_url}/mg/news/",
            "mID": os.getenv("MBNGOLD_ID", ""), "mPWD": os.getenv("MBNGOLD_PW", ""),
        }, timeout=10)

        list_url = f"{base_url}/mg/news/index.php?news_service_id=10001"
        res  = sess.get(list_url, headers=headers, timeout=10)
        soup = _BS(res.content.decode("utf-8", errors="ignore"), "html.parser")

        titles = []
        for a in soup.find_all("a", href=True):
            if "view.php" in a["href"] and "news_no=MM" in a["href"]:
                t = a.get_text(strip=True)
                if t:
                    titles.append(t)
            if len(titles) >= 15:
                break

        if not titles:
            return names

        conn = sqlite3.connect(THEME_DB, timeout=5)
        stock_names = set()
        for (sname,) in conn.execute("SELECT DISTINCT stock_name FROM kr_stock_daily_data"):
            pure = re.sub(r"\s*(KOSPI|KOSDAQ)\s*\d{6}$", "", sname).strip()
            if len(pure) >= 2:
                stock_names.add(pure)
        conn.close()

        combined = " ".join(titles)
        for name in stock_names:
            if name in combined:
                names.add(name)
        if names:
            print(f"   📰 MBN뉴스 종목 매칭: {len(names)}종목")
    except Exception as e:
        print(f"⚠️ [candidate_pool] MBN뉴스 조회 오류: {e}")
    return names


def calc_overlap_boost(name: str, code: str, curr_price: float,
                        catalyst_names: set, news_names: set) -> tuple:
    """3개 소스(한경컨센서스/MBN뉴스/촉매) 겹침 점수 가산.
    반환: (가산점, 겹친소스 라벨 리스트)"""
    boost = 0
    reasons = []
    if name in catalyst_names:
        boost += 10; reasons.append("촉매")
    if name in news_names:
        boost += 10; reasons.append("MBN뉴스")
    try:
        from consensus import apply_consensus_bonus
        cbonus, creason = apply_consensus_bonus(code, 0, curr_price) if code else (0, "")
        if cbonus > 0:
            boost += cbonus; reasons.append(f"한경컨센서스({creason})")
    except Exception as e:
        print(f"⚠️ [candidate_pool] 한경컨센서스 조회 오류 {name}: {e}")
    return boost, reasons


# ============================================================
# 유튜브 관심종목
# ============================================================
def get_youtube_watchlist_names() -> set:
    """유튜브 라이브 모니터(intelligence/youtube_picks.db)가 잡은 추천종목 중
    최근 YT_MENTION_DAYS일 내 YT_MENTION_MIN_COUNT회 이상 언급된 종목만,
    최초 언급일 종가 대비 현재 종가가 YT_MAX_RISE_PCT 이상 오른 건
    제외하고 반환."""
    now = time.time()
    if now - _YT_WATCHLIST_CACHE["ts"] < WATCHLIST_REFRESH_SEC:
        return _YT_WATCHLIST_CACHE["names"]

    names = set()
    try:
        conn = sqlite3.connect(YOUTUBE_DB, timeout=5)
        conn.execute("PRAGMA query_only = ON")
        rows = conn.execute("""
            SELECT stock_name, pick_date FROM youtube_picks
            WHERE pick_date >= date('now', 'localtime', ? || ' days')
            ORDER BY pick_date
        """, (f"-{YT_MENTION_DAYS - 1}",)).fetchall()
        conn.close()

        mentions = {}
        for name, pdate in rows:
            mentions.setdefault(name, []).append(pdate)
        qualifying = {name: dates[0] for name, dates in mentions.items()
                      if len(dates) >= YT_MENTION_MIN_COUNT}

        if qualifying:
            fconn = sqlite3.connect(THEME_DB, timeout=5)
            for name, first_date in qualifying.items():
                base_row = fconn.execute(
                    "SELECT close_price FROM kr_stock_daily_data WHERE stock_name=? AND date=?",
                    (name, first_date)).fetchone()
                latest_row = fconn.execute(
                    "SELECT close_price FROM kr_stock_daily_data WHERE stock_name=? ORDER BY date DESC LIMIT 1",
                    (name,)).fetchone()
                if not base_row or not base_row[0] or not latest_row or not latest_row[0]:
                    continue
                base_price, latest_price = base_row[0], latest_row[0]
                if latest_price >= base_price * (1 + YT_MAX_RISE_PCT):
                    print(f"   ⏭️ [candidate_pool-유튜브] {name} 제외 — 추천일({first_date}) 종가 대비 "
                          f"{(latest_price/base_price-1)*100:+.1f}%")
                    continue
                names.add(name)
            fconn.close()
        if names:
            print(f"   📺 유튜브 관심종목: {len(names)}종목")
    except Exception as e:
        print(f"⚠️ [candidate_pool] 유튜브 관심종목 조회 오류: {e}")

    _YT_WATCHLIST_CACHE["names"] = names
    _YT_WATCHLIST_CACHE["ts"]    = now
    return names


# ============================================================
# 전체 후보 생성 (모멘텀/추세/완화/유튜브 4소스)
# ============================================================
def get_candidates(api=None) -> list:
    """
    4소스 통합 후보 반환:
    - momentum: AI 모멘텀 스캐너 당일 픽 (intelligence/ai_momentum_picks.db)
    - trend   : trend_analyzer.get_trend_data() 전체시장 스캔
    - light   : 촉매종목 중 trend/momentum 미충족 + check_light_chart_health 통과
    - youtube : youtube_picks.db 관심종목(전체 후보가 적을 때만 보조)
    api: KisAPI 인스턴스 (완화트랙 거래량서지 패턴의 실시간 시세 조회용,
         없으면 해당 패턴만 건너뜀)
    """
    from trend_analyzer import get_trend_data
    from swing_master import _get_catalyst_stocks

    catalyst_set = _get_catalyst_stocks()
    trend_data   = get_trend_data(top_n=20)
    news_names   = get_mbn_news_names()

    trend_names = {d["name"] for d in trend_data}
    detail_map  = {}
    for d in trend_data:
        if d["name"] not in detail_map:
            detail_map[d["name"]] = d

    candidates = []

    # ── 모멘텀 ────────────────────────────────────────────
    momentum_names = set()
    try:
        _mconn = sqlite3.connect(MOMENTUM_DB, timeout=5)
        _mrows = _mconn.execute("""
            SELECT stock_name, buy_price, stop_price, tgt_price, theme
            FROM momentum_picks WHERE date = ? ORDER BY id DESC
        """, (today_str(),)).fetchall()
        _mconn.close()
    except Exception as e:
        print(f"⚠️ [candidate_pool] 모멘텀픽 조회 오류: {e}")
        _mrows = []

    momentum_list = []
    for name, buy_price, stop_price, tgt_price, theme in _mrows:
        if name in momentum_names:
            continue
        momentum_names.add(name)
        momentum_list.append({
            "name":     name,
            "grade":    SLOT_MOMENTUM,
            "score":    75,   # 60일 61.3% 적중률 기준 고정값
            "vcp":      False,
            "trend":    name in trend_names,
            "catalyst": name in catalyst_set,
            "curr":     buy_price or 0,
            "stop":     stop_price or 0,
            "tgt":      tgt_price or 0,
            "rr":       round((tgt_price - buy_price) / (buy_price - stop_price), 1)
                        if buy_price and stop_price and buy_price > stop_price else 0,
            "themes":   [theme] if theme else [],
        })
    candidates += momentum_list[:CANDIDATE_CAP_PER_SLOT]

    # ── 추세 ──────────────────────────────────────────────
    trend_only = trend_names - momentum_names
    trend_list = []
    for name in trend_only:
        d = detail_map.get(name, {})
        trend_list.append({
            "name":     name,
            "grade":    SLOT_TREND,
            "score":    d.get("score", 0),
            "vcp":      False,
            "trend":    True,
            "catalyst": name in catalyst_set,
            "curr":     d.get("curr_price", 0),
            "stop":     d.get("stop_price", 0),
            "tgt":      d.get("tgt_price", 0),
            "rr":       d.get("rr_ratio", 0),
            "themes":   d.get("themes", []),
        })
    trend_list.sort(key=lambda x: x["score"], reverse=True)
    candidates += trend_list[:CANDIDATE_CAP_PER_SLOT]

    # ── 완화트랙 ──────────────────────────────────────────
    already_covered = trend_names | momentum_names
    light_pool = catalyst_set - already_covered
    light_list = []
    if light_pool:
        conn = sqlite3.connect(THEME_DB, timeout=5)
        for name in light_pool:
            light = check_light_chart_health(name, conn, api)
            if light:
                light_list.append({
                    "name":     name,
                    "grade":    SLOT_LIGHT,
                    "score":    50,
                    "vcp":      False,
                    "trend":    False,
                    "catalyst": True,
                    "curr":     light["curr_price"],
                    "stop":     light["stop_price"],
                    "tgt":      light["tgt_price"],
                    "rr":       round((light["tgt_price"] - light["curr_price"]) /
                                       (light["curr_price"] - light["stop_price"]), 1)
                                if light["curr_price"] > light["stop_price"] else 0,
                    "themes":   [f"완화조건:{light['pattern']}"],
                })
        conn.close()
    light_list.sort(key=lambda x: x["score"], reverse=True)
    candidates += light_list[:CANDIDATE_CAP_PER_SLOT]

    # ── 유튜브 관심종목 (전체 후보가 적을 때만 보조) ─────────
    if len(candidates) < CANDIDATE_CAP_PER_SLOT * 4:
        youtube_names = get_youtube_watchlist_names() - already_covered - set(light_pool if light_pool else [])
        youtube_list = []
        if youtube_names:
            conn = sqlite3.connect(THEME_DB, timeout=5)
            for name in youtube_names:
                wl = check_light_chart_health(name, conn, api)
                if wl:
                    youtube_list.append({
                        "name":     name,
                        "grade":    SLOT_YOUTUBE,
                        "score":    50,
                        "vcp":      False,
                        "trend":    False,
                        "catalyst": False,
                        "curr":     wl["curr_price"],
                        "stop":     wl["stop_price"],
                        "tgt":      wl["tgt_price"],
                        "rr":       round((wl["tgt_price"] - wl["curr_price"]) /
                                           (wl["curr_price"] - wl["stop_price"]), 1)
                                    if wl["curr_price"] > wl["stop_price"] else 0,
                        "themes":   [f"유튜브관심:{wl['pattern']}"],
                    })
            conn.close()
        youtube_list.sort(key=lambda x: x["score"], reverse=True)
        candidates += youtube_list[:CANDIDATE_CAP_PER_SLOT]

    # ── 겹침 점수 보정 (전체 소스 공통) ──────────────────────
    for c in candidates:
        code = get_stock_code(c["name"])
        boost, reasons = calc_overlap_boost(
            c["name"], code, c["curr"], catalyst_set, news_names)
        if boost:
            c["score"] += boost
            c["themes"] = list(c.get("themes", [])) + [f"겹침:{'/'.join(reasons)}"]

    return candidates


# ============================================================
# 캐시된 갱신 (호출부가 상태를 들고 있고, 이 함수들은 그 상태를
# 받아서 새 값을 돌려주는 순수함수 — sbot.py가 self._cand_date/
# self.candidates 등으로 영속화)
# ============================================================
def refresh_full_candidates(candidates: list, cand_date: str, positions: dict, api=None):
    """하루 1회(날짜 바뀔 때만) 4소스 전체 재조회. 보유중인 종목은 제외.
    반환: (새 candidates, 새 cand_date, changed: bool)"""
    today = today_str()
    if cand_date == today:
        return candidates, cand_date, False

    held_codes = set(positions.keys())
    held_names = {p.get("name") for p in positions.values()}

    def _filter(cands):
        result = [c for c in cands
                  if get_stock_code(c["name"]) not in held_codes
                  and c["name"] not in held_names]
        excluded = [c["name"] for c in cands if c not in result]
        if excluded:
            print(f"   ⏭️ 보유중 제외: {', '.join(excluded)}")
        return result

    print("\n🔄 [candidate_pool] 후보 전체 갱신 중...")
    try:
        new_candidates = _filter(get_candidates(api=api))
    except Exception as e:
        print(f"⚠️ [candidate_pool] 후보 갱신 오류: {e}")
        return candidates, cand_date, False

    momentum = sum(1 for c in new_candidates if c["grade"] == SLOT_MOMENTUM)
    trend    = sum(1 for c in new_candidates if c["grade"] == SLOT_TREND)
    light    = sum(1 for c in new_candidates if c["grade"] == SLOT_LIGHT)
    print(f"   모멘텀:{momentum}개 추세:{trend}개 완화:{light}개")
    return new_candidates, today, True


def refresh_momentum_candidates(candidates: list, positions: dict):
    """모멘텀은 AI 모멘텀 스캐너가 하루 중(08:55/14:35)에 생성되므로,
    하루1회 전체갱신과 별도로 매루프 독립 재조회(로컬 DB만, API호출 없음
    — 비용 저렴). 반환: (새 candidates, changed: bool)"""
    if not candidates:
        return candidates, False
    held_codes = set(positions.keys())
    held_names = {p.get("name") for p in positions.values()}

    try:
        _mconn = sqlite3.connect(MOMENTUM_DB, timeout=5)
        _mrows = _mconn.execute("""
            SELECT stock_name, buy_price, stop_price, tgt_price, theme
            FROM momentum_picks WHERE date = ? ORDER BY id DESC
        """, (today_str(),)).fetchall()
        _mconn.close()
    except Exception as e:
        print(f"⚠️ [candidate_pool] 모멘텀픽 재조회 오류: {e}")
        return candidates, False

    momentum_names = set()
    momentum_list = []
    for name, buy_price, stop_price, tgt_price, theme in _mrows:
        if name in momentum_names:
            continue
        if get_stock_code(name) in held_codes or name in held_names:
            continue
        momentum_names.add(name)
        momentum_list.append({
            "name": name, "grade": SLOT_MOMENTUM, "score": 75,
            "vcp": False, "trend": False, "catalyst": False,
            "curr": buy_price or 0, "stop": stop_price or 0, "tgt": tgt_price or 0,
            "rr": round((tgt_price - buy_price) / (buy_price - stop_price), 1)
                if buy_price and stop_price and buy_price > stop_price else 0,
            "themes": [theme] if theme else [],
        })

    momentum_list = momentum_list[:CANDIDATE_CAP_PER_SLOT]
    old_names = {c["name"] for c in candidates if c["grade"] == SLOT_MOMENTUM}
    new_names = {c["name"] for c in momentum_list}
    if new_names == old_names:
        return candidates, False

    new_candidates = [c for c in candidates if c["grade"] != SLOT_MOMENTUM] + momentum_list
    print(f"   🔄 [candidate_pool] 모멘텀 후보 갱신: {len(new_names)}개 ({', '.join(new_names) or '없음'})")
    return new_candidates, True


def refresh_youtube_candidates(candidates: list, positions: dict, max_positions: int, api=None):
    """유튜브 관심종목은 DB 조회 2개뿐인 가벼운 소스라 매루프 독립 갱신.
    게이트: 실제 보유 슬롯이 꽉 찼으면 스킵(풀 후보수가 아니라 실제
    보유수 기준 — MA40/시총/거래량으로 걸러질 수 있어 풀 개수는 근사치
    부적절). 반환: (새 candidates, changed: bool)"""
    if not candidates:
        return candidates, False
    held_codes = set(positions.keys())
    held_names = {p.get("name") for p in positions.values()}
    non_youtube = [c for c in candidates if c["grade"] != SLOT_YOUTUBE]

    if len(positions) >= max_positions:
        if len(non_youtube) != len(candidates):
            return non_youtube, True
        return candidates, False

    already_covered = {c["name"] for c in non_youtube
                        if c["grade"] in (SLOT_TREND, SLOT_MOMENTUM, SLOT_LIGHT)}
    youtube_names = get_youtube_watchlist_names() - already_covered
    youtube_names = {n for n in youtube_names
                      if get_stock_code(n) not in held_codes and n not in held_names}

    youtube_list = []
    if youtube_names:
        conn = sqlite3.connect(THEME_DB, timeout=5)
        for name in youtube_names:
            wl = check_light_chart_health(name, conn, api)
            if wl:
                youtube_list.append({
                    "name": name, "grade": SLOT_YOUTUBE, "score": 50,
                    "vcp": False, "trend": False, "catalyst": False,
                    "curr": wl["curr_price"], "stop": wl["stop_price"], "tgt": wl["tgt_price"],
                    "rr": round((wl["tgt_price"] - wl["curr_price"]) /
                                (wl["curr_price"] - wl["stop_price"]), 1)
                        if wl["curr_price"] > wl["stop_price"] else 0,
                    "themes": [f"유튜브관심:{wl['pattern']}"],
                })
        conn.close()
    youtube_list.sort(key=lambda x: x["score"], reverse=True)
    youtube_list = youtube_list[:CANDIDATE_CAP_PER_SLOT]

    old_names = {c["name"] for c in candidates if c["grade"] == SLOT_YOUTUBE}
    new_names = {c["name"] for c in youtube_list}
    if new_names == old_names:
        return candidates, False

    new_candidates = non_youtube + youtube_list
    print(f"   🔄 [candidate_pool] 유튜브 관심종목 후보 갱신: {len(new_names)}개 ({', '.join(new_names) or '없음'})")
    return new_candidates, True
