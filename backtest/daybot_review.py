"""
daybot_review.py — daybot 실매매 점검 (2026-10-08, cbot 점검과 같은 순서)
================================================================
[이 파일이 하는 일 — 비개발자용 설명]

대장 방침: "안정성이 어느 정도 확보되면 그때 1종목당 매수금을 늘린다."
그 "안정성"을 숫자로 보려고 daybot_trade_history.db(실제 매매)를 뜯어본다.
  1. 전체 성적 — 승률·평균 익/손·손익비·PF·누적(수수료·세금 약 0.2% 차감 추정)
  2. 어디서 벌고 어디서 잃나 — 매도 사유별 / 후보 출처(tier)별 / 매수 시각대별 /
     매수 때 등락률 구간별 / 보유일별
  3. 손절이 -3.5%에서 끊겼나 — 갭하락·체결 미끄러짐으로 더 깊게 잃은 건
  4. 하루 손익 — 최악의 날, 연속 손실, "일손실 한도가 있었다면" 걸렸을 날
     (daybot엔 지금 일손실 한도가 없음 — cbot은 -15만)
  5. 증액 준비 점검표 — 최근 거래 기준 승률·PF·최악의 날
  6. (--after) 손절·기한청산 뒤 그날 30분/1시간/종가 — 바닥에서 판 건지
     (한투 분봉 조회라 서버에서만, 건당 2회 호출)
실행:  python backtest/daybot_review.py [최근 N일, 기본 30] [--after]
"""
import os
import sys
import json
import sqlite3
import datetime

COST = 0.002                 # 수수료+거래세 대략(왕복, 매수금 대비)
STOP_PCT = -3.5
LIMITS = (-100_000, -150_000)
READY = {"n": 30, "win": 50.0, "pf": 1.3, "worst_day": -150_000}   # 증액 점검 기준(제안값)


def _find(name: str) -> str:
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for d in (base, os.path.join(base, "bots"), os.getcwd()):
        p = os.path.join(d, name)
        if os.path.exists(p):
            return p
    return os.path.join(base, name)


def _dt(s: str) -> datetime.datetime:
    return datetime.datetime.fromisoformat(str(s).replace("T", " ")[:19])


def load_trades(db: str, days: int) -> list:
    since = (datetime.datetime.now() - datetime.timedelta(days=days)).isoformat()
    conn = sqlite3.connect(db)
    try:
        rows = conn.execute("""SELECT code, stock_name, buy_price, buy_time, sell_price, sell_time, qty,
                                      profit_rate, sell_reason, buy_tag, hold_days
                               FROM trades WHERE sell_price IS NOT NULL AND sell_time >= ?
                               ORDER BY sell_time""", (since,)).fetchall()
        cands = conn.execute("""SELECT ts, code, change_rate, ask_bid_ratio FROM candidate_log
                                WHERE bought=1 AND ts >= ?""", (since,)).fetchall()
    except sqlite3.OperationalError:
        cands = []
    finally:
        conn.close()
    by_code = {}
    for ts, code, chg, abr in cands:
        by_code.setdefault(code, []).append((_dt(ts), chg, abr))
    out = []
    for code, name, bp, bt, sp, st, qty, rate, reason, tag, hd in rows:
        if not bp or not sp or not qty:
            continue
        b, s = _dt(bt), _dt(st)
        gross = (sp - bp) * qty
        t = {"code": code, "name": name or code, "buy": b, "sell": s, "bp": bp, "sp": sp, "qty": qty,
             "amt": bp * qty, "rate": (sp / bp - 1) * 100, "krw": gross - bp * qty * COST,
             "reason": reason or "", "tag": tag or "", "hold": hd or 0, "chg": None, "abr": None}
        near = [c for c in by_code.get(code, []) if abs((c[0] - b).total_seconds()) <= 600]
        if near:
            _, t["chg"], t["abr"] = min(near, key=lambda c: abs((c[0] - b).total_seconds()))
        out.append(t)
    return out


def reason_group(r: str) -> str:
    for key, label in (("트레일링", "트레일링"), ("손절", "손절"), ("보유기한", "보유기한"),
                       ("수동", "수동"), ("익절", "익절")):
        if key in r:
            return label
    return "기타"


def time_bucket(b: datetime.datetime) -> str:
    hm = b.strftime("%H%M")
    for end, label in (("0900", "프리장"), ("0940", "09:00~09:40"), ("1100", "09:40~11:00"),
                       ("1300", "11:00~13:00"), ("1530", "13:00~15:30")):
        if hm < end:
            return label
    return "애프터"


def chg_bucket(c):
    if c is None:
        return "기록없음"
    for hi, label in ((3, "~3%"), (8, "3~8%"), (15, "8~15%")):
        if c < hi:
            return label
    return "15%~"


def stats(ts: list) -> dict:
    if not ts:
        return {"n": 0}
    w = [t for t in ts if t["krw"] > 0]
    l = [t for t in ts if t["krw"] <= 0]
    gw, gl = sum(t["krw"] for t in w), -sum(t["krw"] for t in l)
    return {"n": len(ts), "win": len(w) / len(ts) * 100,
            "aw": sum(t["rate"] for t in w) / len(w) if w else 0.0,
            "al": sum(t["rate"] for t in l) / len(l) if l else 0.0,
            "pf": gw / gl if gl > 0 else float("inf"), "krw": gw - gl}


def _fmt(s: dict) -> str:
    if not s["n"]:
        return "0건"
    pf = f"{s['pf']:.2f}" if s["pf"] != float("inf") else "∞"
    return (f"{s['n']:>3}건 승률 {s['win']:>3.0f}% | 평균익 {s['aw']:+.2f}% 평균손 {s['al']:+.2f}% | "
            f"PF {pf:>4} | {s['krw']:>+10,.0f}원")


def daily(ts: list) -> list:
    days = {}
    for t in ts:
        days.setdefault(t["sell"].date(), []).append(t)
    out = []
    for d, xs in sorted(days.items()):
        cum, low, hit = 0.0, 0.0, {}
        for t in sorted(xs, key=lambda x: x["sell"]):
            cum += t["krw"]; low = min(low, cum)
            for lim in LIMITS:
                if cum <= lim and lim not in hit:
                    hit[lim] = t["sell"]
        out.append({"date": d, "krw": cum, "low": low, "n": len(xs), "hit": hit})
    return out


def max_streak(ts: list) -> int:
    best = cur = 0
    for t in ts:
        cur = cur + 1 if t["krw"] <= 0 else 0
        best = max(best, cur)
    return best


def group_lines(ts: list, key, order=None) -> list:
    g = {}
    for t in ts:
        g.setdefault(key(t), []).append(t)
    keys = order if order else sorted(g, key=lambda k: -len(g[k]))
    return [f"   {k:<12} {_fmt(stats(g[k]))}" for k in keys if k in g]


def after_sell(api, t: dict) -> dict:
    """그날 매도 뒤 30분/1시간/종가와 2시간 안 최고(매도가 대비 %). 정규장 분봉 기준."""
    d, s = t["sell"].strftime("%Y%m%d"), t["sell"]
    if s.strftime("%H%M") >= "1530" or s.strftime("%H%M") < "0900":
        return {}
    end2h = min(s + datetime.timedelta(hours=2), s.replace(hour=15, minute=30, second=0))
    bars = api.get_minute_bars_by_date(t["code"], d, end2h.strftime("%H%M%S"))
    close_bars = api.get_minute_bars_by_date(t["code"], d, "153000")
    seq = sorted((b["time"], b["price"], b["high"]) for b in bars if b["time"] > s.strftime("%H%M%S"))

    def at(mins):
        target = (s + datetime.timedelta(minutes=mins)).strftime("%H%M%S")
        later = [p for tm, p, _ in seq if tm >= target]
        return (later[0] / t["sp"] - 1) * 100 if later else None
    res = {"30분": at(30), "1시간": at(60),
           "2시간최고": (max(h for _, _, h in seq) / t["sp"] - 1) * 100 if seq else None}
    if close_bars:
        res["종가"] = (max(close_bars, key=lambda b: b["time"])["price"] / t["sp"] - 1) * 100
    return res


def report(ts: list, days: int, after: list = None) -> str:
    L = [f"📊 daybot 실매매 점검 — 최근 {days}일, 매도완료 {len(ts)}건 (수수료·세금 약 {COST:.1%} 차감 추정)"]
    if not ts:
        return "\n".join(L + ["   매도 기록 없음"])
    s = stats(ts)
    L.append("■ 전체  " + _fmt(s))
    if s["al"]:
        L.append(f"   손익비(평균익÷평균손) {abs(s['aw'] / s['al']):.2f} · 최대 연속손실 {max_streak(ts)}건 · "
                 f"평균 매수금 {sum(t['amt'] for t in ts) / len(ts):,.0f}원")
    L.append("■ 매도 사유별");      L += group_lines(ts, lambda t: reason_group(t["reason"]))
    L.append("■ 후보 출처별");      L += group_lines(ts, lambda t: t["tag"] or "-")
    L.append("■ 매수 시각대별");    L += group_lines(ts, lambda t: time_bucket(t["buy"]),
                                                ["프리장", "09:00~09:40", "09:40~11:00", "11:00~13:00",
                                                 "13:00~15:30", "애프터"])
    L.append("■ 매수 때 등락률별"); L += group_lines(ts, lambda t: chg_bucket(t["chg"]),
                                                ["~3%", "3~8%", "8~15%", "15%~", "기록없음"])
    L.append("■ 보유일별");         L += group_lines(ts, lambda t: f"{t['hold']}일",
                                                [f"{i}일" for i in range(10)])
    deep = [t for t in ts if reason_group(t["reason"]) == "손절" and t["rate"] < STOP_PCT - 1.0]
    L.append(f"■ 손절이 {STOP_PCT}%보다 1%p 넘게 깊었던 것 {len(deep)}건 (갭하락·체결 미끄러짐)")
    for t in deep[-10:]:
        L.append(f"   {t['sell']:%m-%d %H:%M} {t['name']:<12} {t['rate']:+.2f}% ({t['krw']:+,.0f}원) "
                 f"보유 {t['hold']}일")
    ds = daily(ts)
    L.append(f"■ 하루 손익 — {len(ds)}일 중 플러스 {sum(d['krw'] > 0 for d in ds)}일 · "
             f"하루 평균 {sum(d['krw'] for d in ds) / len(ds):+,.0f}원")
    for d in sorted(ds, key=lambda x: x["krw"])[:5]:
        L.append(f"   {d['date']} {d['krw']:+10,.0f}원 ({d['n']}건, 장중 최저 {d['low']:+,.0f})")
    for lim in LIMITS:
        hits = [d for d in ds if lim in d["hit"]]
        L.append(f"   일손실 한도 {lim:,}원이 있었다면: {len(hits)}일 걸림"
                 + (" — " + ", ".join(f"{d['date']:%m-%d} {d['hit'][lim]:%H:%M}" for d in hits) if hits else ""))
    recent = ts[-READY["n"]:]
    rs, worst = stats(recent), min((d["low"] for d in daily(recent)), default=0)
    checks = [(f"최근 {len(recent)}건(기준 {READY['n']}건 이상)", len(recent) >= READY["n"]),
              (f"승률 {rs['win']:.0f}% ≥ {READY['win']:.0f}%", rs["win"] >= READY["win"]),
              (f"PF {rs['pf']:.2f} ≥ {READY['pf']}", rs["pf"] >= READY["pf"]),
              (f"하루 최저 {worst:+,.0f} > {READY['worst_day']:,}", worst > READY["worst_day"])]
    L.append("■ 증액 준비 점검(제안 기준) — " + ("✅ 통과" if all(c for _, c in checks) else "⏳ 아직"))
    L += [f"   {'✅' if ok else '❌'} {label}" for label, ok in checks]
    if after:
        L.append("■ 손절·기한청산 뒤 그날 (매도가 대비)")
        rows = [(t, a) for t, a in after if a]
        for k in ("30분", "1시간", "종가", "2시간최고"):
            xs = [a[k] for _, a in rows if a.get(k) is not None]
            if xs:
                L.append(f"   {k:<6} 평균 {sum(xs) / len(xs):+.2f}% · 매도가 위 {sum(x > 0 for x in xs)}/{len(xs)}건")
        for t, a in rows[-15:]:
            f = lambda v: f"{v:+.1f}%" if v is not None else "-"
            L.append(f"   {t['sell']:%m-%d %H:%M} {t['name']:<12} {t['rate']:+.2f}% → 30분 {f(a.get('30분'))} "
                     f"1시간 {f(a.get('1시간'))} 종가 {f(a.get('종가'))} 2시간최고 {f(a.get('2시간최고'))}")
    return "\n".join(L)


if __name__ == "__main__":
    n = next((int(a) for a in sys.argv[1:] if a.isdigit()), 30)
    trades = load_trades(_find("daybot_trade_history.db"), n)
    after = None
    if "--after" in sys.argv:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        sys.path.insert(0, os.path.join(base, "core"))
        from dotenv import load_dotenv
        load_dotenv(os.path.join(base, ".env"))
        from kis_api import KisAPI
        api = KisAPI()
        after = []
        for t in trades:
            if reason_group(t["reason"]) in ("손절", "보유기한"):
                try:
                    after.append((t, after_sell(api, t)))
                except Exception as e:
                    print(f"⚠️ 분봉 조회 실패 {t['code']}: {e}")
    print(report(trades, n, after))
