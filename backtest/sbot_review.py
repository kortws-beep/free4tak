"""
sbot_review.py — sbot(스윙봇) 실매매 점검 (2026-10-12, 데이봇 점검과 같은 1단계)
================================================================
[이 파일이 하는 일 — 비개발자용 설명]

대장: "개입 없이 돌려도 되나" 점검 5번. sbot_trade_history.db(실제 매매)를 뜯어본다.
  1. 전체 성적 — 승률·평균 익/손·손익비·PF·누적(수수료·세금 약 0.2% 차감 추정)
  2. 어디서 벌고 잃나 — 매도 사유별 / 매수 출처(키움·NEW·모멘텀·추세·유튜브…)별 /
     도달한 익절 단계별 / 보유 기간별 / AI 점수별
  3. 매수 때 시장 국면별(20일선 위 종목 비율) — 약한 장 절반 매수가 sbot에도 맞는지
  4. 하루 손익 — 최악의 날, 일손실 한도 -50만에 걸렸을 날
  5. 손절 뒤 5일·10일 주가(매도가 대비, 리나 일봉) — 손절이 맞았나, 털린 건가
실행:  python backtest/sbot_review.py [최근 N일, 기본 90]
"""
import os
import sys
import sqlite3
import datetime

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "backtest"))
sys.path.insert(0, os.path.join(BASE, "core"))
import daybot_review as dr  # noqa: E402   (집계·표시 함수 재사용)

COST = dr.COST
LIMIT = -500_000
THEME_DB = os.path.join(BASE, "lina_bot", "kr_theme_finance.db")


def load_trades(db: str, days: int) -> list:
    since = (datetime.datetime.now() - datetime.timedelta(days=days)).isoformat()
    conn = sqlite3.connect(db)
    try:
        rows = conn.execute("""SELECT code, stock_name, buy_price, buy_time, sell_price, sell_time, qty,
                                      sell_reason, buy_tag, hold_days, ai_score, stage_reached
                               FROM trades WHERE sell_price IS NOT NULL AND sell_time >= ?
                               ORDER BY sell_time""", (since,)).fetchall()
    except sqlite3.OperationalError:          # 예전 스키마(포렌식 컬럼 없음)
        rows = [r + (0,) for r in conn.execute(
            """SELECT code, stock_name, buy_price, buy_time, sell_price, sell_time, qty,
                      sell_reason, buy_tag, hold_days, ai_score
               FROM trades WHERE sell_price IS NOT NULL AND sell_time >= ? ORDER BY sell_time""",
            (since,)).fetchall()]
    finally:
        conn.close()
    out = []
    for code, name, bp, bt, sp, st, qty, reason, tag, hd, ai, stage in rows:
        if not bp or not sp or not qty:
            continue
        b, s = dr._dt(bt), dr._dt(st)
        out.append({"code": code, "name": name or code, "buy": b, "sell": s, "bp": bp, "sp": sp, "qty": qty,
                    "amt": bp * qty, "rate": (sp / bp - 1) * 100, "krw": (sp - bp) * qty - bp * qty * COST,
                    "reason": reason or "", "tag": tag or "-", "hold": hd or (s.date() - b.date()).days,
                    "ai": ai, "stage": stage or 0})
    return out


def reason_group(r: str) -> str:
    for key, label in (("손절", "손절"), ("급락", "손절"), ("트레일", "트레일링"), ("본절", "본절"),
                       ("익절", "익절"), ("목표", "익절"), ("기한", "보유기한"), ("정체", "보유기한"),
                       ("수동", "수동")):
        if key in r:
            return label
    return "기타"


def hold_bucket(h: int) -> str:
    for hi, label in ((1, "당일"), (3, "1~2일"), (6, "3~5일"), (11, "6~10일")):
        if h < hi:
            return label
    return "11일+"


def ai_bucket(a) -> str:
    if a is None:
        return "-"
    for hi, label in ((60, "~59"), (70, "60~69"), (80, "70~79")):
        if a < hi:
            return label
    return "80+"


def regime_map(dates: list) -> dict:
    """{매수일: 전 거래일 종가 기준 20일선 위 비율} — 매수 당일 아침 기준(장중 매수는 전날 종가로 판단)."""
    try:
        import market_regime as mr
    except Exception:
        return {}
    if not os.path.exists(THEME_DB):
        return {}
    conn = sqlite3.connect(THEME_DB)
    try:
        all_days = [d for (d,) in conn.execute("SELECT DISTINCT date FROM kr_stock_daily_data ORDER BY date")]
    finally:
        conn.close()
    prev = {}
    for d in sorted(set(dates)):
        before = [x for x in all_days if x < d]
        if before:
            prev[d] = before[-1]
    res = mr.compute(THEME_DB, sorted(set(prev.values()))) if prev else {}
    return {d: res[p][0] for d, p in prev.items() if p in res}


def regime_bucket(b) -> str:
    if b is None:
        return "-"
    return "50% 미만(약한 장)" if b < 50 else "50% 이상"


def after_stop(trades: list) -> list:
    """손절 뒤 5·10거래일 종가(매도가 대비 %) — 리나 일봉(종목명 기준)."""
    if not os.path.exists(THEME_DB):
        return []
    conn = sqlite3.connect(THEME_DB)
    out = []
    try:
        for t in trades:
            if reason_group(t["reason"]) != "손절":
                continue
            rows = conn.execute("SELECT date, close_price FROM kr_stock_daily_data WHERE stock_name=? AND date>? "
                                "ORDER BY date LIMIT 10", (t["name"], t["sell"].date().isoformat())).fetchall()
            r5 = (rows[4][1] / t["sp"] - 1) * 100 if len(rows) >= 5 and rows[4][1] else None
            r10 = (rows[9][1] / t["sp"] - 1) * 100 if len(rows) >= 10 and rows[9][1] else None
            out.append((t, r5, r10))
    finally:
        conn.close()
    return out


def report(ts: list, days: int, regimes: dict = None, stops: list = None) -> str:
    L = [f"📊 sbot 실매매 점검 — 최근 {days}일, 매도완료 {len(ts)}건 (분할매도 포함, 수수료·세금 약 {COST:.1%} 차감)"]
    if not ts:
        return "\n".join(L + ["   매도 기록 없음"])
    s = dr.stats(ts)
    L.append("■ 전체  " + dr._fmt(s))
    if s["al"]:
        L.append(f"   손익비 {abs(s['aw'] / s['al']):.2f} · 최대 연속손실 {dr.max_streak(ts)}건 · "
                 f"평균 매수금 {sum(t['amt'] for t in ts) / len(ts):,.0f}원")
    L.append("■ 매도 사유별");   L += dr.group_lines(ts, lambda t: reason_group(t["reason"]))
    L.append("■ 매수 출처별");   L += dr.group_lines(ts, lambda t: t["tag"])
    L.append("■ 도달 익절단계별"); L += dr.group_lines(ts, lambda t: f"단계{t['stage']}",
                                              [f"단계{i}" for i in range(6)])
    L.append("■ 보유 기간별");   L += dr.group_lines(ts, lambda t: hold_bucket(t["hold"]),
                                            ["당일", "1~2일", "3~5일", "6~10일", "11일+"])
    L.append("■ AI 점수별");     L += dr.group_lines(ts, lambda t: ai_bucket(t["ai"]),
                                            ["~59", "60~69", "70~79", "80+", "-"])
    if regimes:
        L.append("■ 매수 때 시장 국면별 (전 거래일 종가 기준 20일선 위 종목 비율)")
        L += dr.group_lines(ts, lambda t: regime_bucket(regimes.get(t["buy"].date().isoformat())),
                            ["50% 미만(약한 장)", "50% 이상", "-"])
    ds = dr.daily(ts)
    L.append(f"■ 하루 손익 — {len(ds)}일 중 플러스 {sum(d['krw'] > 0 for d in ds)}일 · "
             f"하루 평균 {sum(d['krw'] for d in ds) / len(ds):+,.0f}원")
    for d in sorted(ds, key=lambda x: x["krw"])[:5]:
        L.append(f"   {d['date']} {d['krw']:+11,.0f}원 ({d['n']}건)")
    hit = [d for d in ds if d["low"] <= LIMIT]
    L.append(f"   일손실 한도 {LIMIT:,}원 걸렸을 날: {len(hit)}일"
             + (" — " + ", ".join(f"{d['date']:%m-%d}" for d in hit) if hit else ""))
    months = {}
    for t in ts:
        months.setdefault(t["sell"].strftime("%Y-%m"), []).append(t)
    L.append("■ 월별")
    for m in sorted(months):
        L.append(f"   {m}  {dr._fmt(dr.stats(months[m]))}")
    if stops:
        L.append("■ 손절 뒤 주가 (매도가 대비, 일봉 종가)")
        for k, idx in (("5거래일", 1), ("10거래일", 2)):
            xs = [x[idx] for x in stops if x[idx] is not None]
            if xs:
                L.append(f"   {k} 뒤 평균 {sum(xs) / len(xs):+.2f}% · 매도가 위 {sum(x > 0 for x in xs)}/{len(xs)}건")
        for t, r5, r10 in stops[-12:]:
            f = lambda v: f"{v:+.1f}%" if v is not None else "-"
            L.append(f"   {t['sell']:%m-%d} {t['name']:<12} {t['rate']:+.2f}% → 5일 {f(r5)} · 10일 {f(r10)}")
    return "\n".join(L)


if __name__ == "__main__":
    n = next((int(a) for a in sys.argv[1:] if a.isdigit()), 90)
    db = dr._find("sbot_trade_history.db")
    trades = load_trades(db, n)
    regimes = regime_map([t["buy"].date().isoformat() for t in trades])
    print(report(trades, n, regimes, after_stop(trades)))
