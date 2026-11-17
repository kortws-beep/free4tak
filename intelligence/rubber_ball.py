"""
rubber_ball.py — 급락장 '고무공' 후보 순위 (2026-11-17, 대장 "적용하세" — SCENARIOS 7-7 비상 매뉴얼)
================================================================
[이 파일이 하는 일 — 비개발자용 설명]
급락장(지수 2거래일↑ 하락 & 고점 -5%↓)에 음봉 엔진은 '무너진 장(-15%↓)'이라 쉬고, 날씨판은 쉼인데 실제로 돈이 된 건 고무공(급락 뒤
강하게 튀는 종목 — 11-16 티엘비 +5%·디아이). 11-11 확인 기준 그대로 후보를 줄 세운다(매매는 대장이 — 리나 `!고무공`):
  · 대상: 한투 분야별 관심그룹(알림·보관용 그룹 제외)
  · 3개월 주도 유지: 어제 종가가 약 3개월(63거래일) 전보다 높음
  · 1개월 낙폭: 어제 종가가 최근 21거래일 고가 대비 몇 % 아래 — 11-11 확인: -35%↓ 반등 +18.4% · -25~-35% +11.0% · -15~-25% +7.6%,
    3개월 플러스 × 1개월 -25%↓ 9종목 평균 +24.6%
  · 되돌림 성격: 최근 약 6개월 '5일 고점 -15%↓ 급락' 때마다 그 뒤 5거래일 안 최고 반등(평균·+10%↑ 비율) — 11-11 거친 측정과 같은 방식
  · 재료 표시: 오늘 [특징주] 뉴스·리나 테마 순위 대장에 나왔으면 🔥 (대장 보충 "급반등은 부각 종목+급락+재료가 버무려질 때")
  · 오늘 시세: 지금 등락·당일 저가 대비 반등(장중이면)
순위: 1개월 낙폭 구간(-25%↓ 먼저) → 되돌림 점수. 점수 단독 매수 X — 대장 판단 재료.
실행: venv/bin/python intelligence/rubber_ball.py
"""
import datetime
import os
import sqlite3
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_BASE = os.path.dirname(_HERE)
for _d in ("core", "intelligence"):
    if os.path.join(_BASE, _d) not in sys.path:
        sys.path.insert(0, os.path.join(_BASE, _d))
DAILY_DB = os.path.join(_BASE, "lina_bot", "kr_theme_finance.db")
LEADER_DB = os.path.join(_BASE, "lina_bot", "three_month_leader_log.db")
FEATURE_DB = os.path.join(_HERE, "feature_news.db")
NOT_SECTOR = {"isee5", "sub", "new", "끼", "v알림", "신규", "신규추천", "서브", "고정"}


def _history(names: set, today: str, days: int = 190) -> dict:
    c = sqlite3.connect(f"file:{DAILY_DB}?mode=ro", uri=True, timeout=10)
    try:
        start = (datetime.date.fromisoformat(today) - datetime.timedelta(days=days * 1.5)).isoformat()
        rows = c.execute("SELECT stock_name, date, close_price, high_price FROM kr_stock_daily_data WHERE date >= ? AND date < ? "
                         "ORDER BY date", (start, today)).fetchall()
    finally:
        c.close()
    out = {}
    for n, d, cl, hi in rows:
        if n in names and cl:
            out.setdefault(n, []).append((d, float(cl), float(hi or cl)))
    return out


def rebound_char(series: list, lookback: int = 126) -> tuple:
    """최근 lookback거래일 안 '5일 고점 -15%↓' 날마다 그 뒤 5거래일 최고 종가 반등 → (평균 %, +10%↑ 비율 %, 횟수)."""
    s = series[-(lookback + 10):]
    outs, last_i = [], -99
    for i in range(5, len(s) - 5):
        hi5 = max(x[2] for x in s[i - 5:i + 1])
        if s[i][1] <= hi5 * 0.85 and i - last_i >= 5:        # 같은 급락을 여러 번 세지 않게 5일 간격
            last_i = i
            outs.append((max(x[1] for x in s[i + 1:i + 6]) / s[i][1] - 1) * 100)
    if not outs:
        return None, None, 0
    return sum(outs) / len(outs), sum(o >= 10 for o in outs) / len(outs) * 100, len(outs)


def _materials(today: str) -> set:
    names = set()
    try:
        c = sqlite3.connect(f"file:{FEATURE_DB}?mode=ro", uri=True, timeout=5)
        names |= {n for (n,) in c.execute("SELECT DISTINCT name FROM news WHERE pub_ts >= ?", (today,)) if n}
        c.close()
    except Exception:
        pass
    try:
        c = sqlite3.connect(f"file:{LEADER_DB}?mode=ro", uri=True, timeout=5)
        names |= {n for (n,) in c.execute("SELECT DISTINCT leader FROM theme_open_obs WHERE date=?", (today,)) if n}
        names |= {n for (n,) in c.execute("SELECT DISTINCT name FROM theme_pre_top WHERE date=?", (today,)) if n}
        c.close()
    except Exception:
        pass
    return names


def candidates(today: str = None, api=None, live: bool = True) -> list:
    import sector_watch as sw
    today = today or datetime.date.today().isoformat()
    if api is None:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(_BASE, ".env"))
        from kis_api import KisAPI
        api = KisAPI()
    groups = {g: v for g, v in sw.load_groups(api, sw.hts_id()).items() if g.strip().lower() not in NOT_SECTOR}
    member = {}
    for g, v in groups.items():
        for code, n in v:
            member.setdefault((code, n), []).append(g)
    hist = _history({n for _, n in member}, today)
    mats = _materials(today)
    rows = []
    for (code, n), gs in member.items():
        s = hist.get(n)
        if not s or len(s) < 64:
            continue
        last = s[-1][1]
        r3m = (last / s[-64][1] - 1) * 100
        dd1m = (last / max(x[2] for x in s[-21:]) - 1) * 100
        if r3m <= 0 or dd1m > -15:
            continue
        avg, hit, n_ep = rebound_char(s)
        rows.append({"code": code, "name": n, "groups": gs, "r3m": r3m, "dd1m": dd1m, "rb_avg": avg, "rb_hit": hit,
                     "rb_n": n_ep, "mat": n in mats})
    if live and rows:
        try:
            q = api.get_multi_price([r["code"] for r in rows], pause=0.1) or {}
            for r in rows:
                x = q.get(r["code"]) or {}
                r["chg"] = float(x.get("chg") or 0) if x else None
                lo = float(x.get("low") or 0) if x else 0
                r["bounce"] = (float(x.get("price") or 0) / lo - 1) * 100 if lo else None
        except Exception as e:
            print(f"⚠️ [고무공] 시세 조회 실패: {e}")

    def tier(r):
        return 0 if r["dd1m"] <= -25 else 1
    rows.sort(key=lambda r: (tier(r), -(r["rb_avg"] or 0), r["dd1m"]))
    return rows


def format_rows(rows: list, top: int = 10) -> str:
    if not rows:
        return "🏀 고무공 후보 없음(3개월 주도 유지 & 1개월 -15%↓ 종목이 관심그룹에 없음)"
    now = datetime.datetime.now().strftime("%H:%M")
    lines = [f"🏀 **고무공 후보** {now} (관심그룹 · 3개월 주도 유지 & 1개월 고점 -15%↓ · 되돌림 성격 순 — 매매는 대장 판단)"]
    for i, r in enumerate(rows[:top], 1):
        rb = f"과거 급락 뒤 5일 최고 +{r['rb_avg']:.0f}%(+10%↑ {r['rb_hit']:.0f}%, {r['rb_n']}회)" if r["rb_avg"] is not None else "과거 급락 이력 없음"
        today = (f" · 오늘 {r['chg']:+.1f}%" if r.get("chg") is not None else "") + \
                (f"(저가 대비 +{r['bounce']:.1f}%)" if r.get("bounce") else "")
        lines.append(f"{i}. {'🔥' if r['mat'] else ''}**{r['name']}**({r['code']}) [{'·'.join(r['groups'][:2])}] "
                     f"1개월 {r['dd1m']:.0f}% · 3개월 {r['r3m']:+.0f}% · {rb}{today}")
    lines.append("   (11-11 확인: 3개월 플러스 × 1개월 -25%↓ 평균 +24.6% 반등 · 🔥=오늘 특징주/테마 대장 재료 · 사려면 `단타 종목 금액`)")
    return "\n".join(lines)


if __name__ == "__main__":
    print(format_rows(candidates()))
