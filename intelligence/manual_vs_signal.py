"""
manual_vs_signal.py — 대장 수동매매 vs 신호 대조 (4번 프로토타입, 2026-10-08)
================================================================
[이 파일이 하는 일 — 비개발자용 설명]

대장이 리나에 "등록/해제"한 수동매매(manual_watch_log)를 그날 우리가 가진 신호와 맞춰 본다.
  · 대장이 사기 전에 파이썬판 검색식(주도주검색식3/단타000/3개월수급)이 그 종목을 잡았나,
    못 잡았으면 그 무렵 어느 조건에서 떨어졌나(근접 사유)
  · 그 시각 섹터감시 상위 분야의 대장·2등이었나 / 데이봇 후보였나(왜 안 샀나)
  · 대장 관심그룹 어디에 들어 있던 종목인가
  · 같은 종목을 데이봇 규칙(첫 신호에 매수, 현행 손절·트레일링)으로 샀다면
목적: 대장 손에서 나는 수익 중 봇이 못 따라가는 부분 = 검색식·필터에 더할 단서 찾기.

매수 시각: 등록은 분할매수를 끝낸 뒤 하므로(10-03 대장 지정) 실제 매수는 더 이르다.
  그날 1분봉에서 등록 전 평단가를 처음 지난 시각을 "매수 추정"으로 쓴다(한투 분봉).
  분봉을 못 받으면 등록 시각을 쓴다.
실행:  python intelligence/manual_vs_signal.py [최근 N일, 기본 14] [--no-api]
"""
import os
import sys
import sqlite3
import datetime

import three_month_leader as tml

sys.path.insert(0, os.path.join(tml._BASE, "backtest"))
import daybot_replay as rp  # noqa: E402

PY_TABLES = (("leader_obs", "주도주"), ("danta_obs", "단타000"), ("tml_obs", "3개월수급"))
DAYBOT_DB = os.path.join(tml._BASE, "daybot_trade_history.db")


def _q(conn, sql, *a):
    try:
        return conn.execute(sql, a).fetchall()
    except sqlite3.OperationalError:
        return []


def load_manual(conn, days: int) -> list:
    """등록→해제 짝. [{code,name,entry,reg,dereg,sell,rate}] (해제 전이면 dereg=None)."""
    since = (datetime.date.today() - datetime.timedelta(days=days)).isoformat()
    rows = _q(conn, "SELECT ts, event, code, name, entry_price, sell_price, profit_rate, registered_at "
                    "FROM manual_watch_log WHERE ts >= ? ORDER BY ts", since)
    open_, out = {}, []
    for ts, ev, code, name, entry, sell, rate, reg_at in rows:
        if ev == "register":
            if code in open_:                  # 해제 없이 다시 등록 = 추가매수(평단 갱신)
                open_[code]["adds"].append((ts, entry))
                open_[code]["entry"] = entry
                continue
            open_[code] = {"code": code, "name": name or code, "entry": entry, "entry0": entry, "reg": ts,
                           "dereg": None, "sell": None, "rate": None, "adds": []}
            out.append(open_[code])
        elif ev == "deregister":
            t = open_.pop(code, None)
            if t is None:                      # 기록 시작 전에 등록한 종목
                t = {"code": code, "name": name or code, "entry": entry, "entry0": entry,
                     "reg": (reg_at or ts)[:19].replace("T", " "), "dereg": None, "sell": None, "rate": None,
                     "adds": []}
                out.append(t)
            t.update(dereg=ts, sell=sell, rate=rate)
    return out


def estimate_buy_time(bars: list, entry: float, before: str):
    """1분봉 [(time, price, high, low)]에서 before(HHMMSS) 이전 평단가를 처음 지난 시각."""
    if not entry:
        return None
    for t, p, h, l in bars:
        if t > before:
            break
        if l <= entry <= h:
            return t
    return None


def signals(conn, date: str, code: str, before: str) -> dict:
    """그날 파이썬판 검색식: {라벨: {"first": 첫 통과 HHMM 또는 None, "near": before 직전 탈락 사유}}."""
    out = {}
    hm = f"{before[:2]}:{before[2:4]}"
    for table, label in PY_TABLES:
        rows = _q(conn, f"SELECT time, passed, fails FROM {table} WHERE date=? AND code=? ORDER BY time",
                  date, code)
        first = next((t for t, ok, _ in rows if ok), None)
        near = [f for t, ok, f in rows if not ok and t <= hm]
        out[label] = {"first": first, "near": near[-1] if near else None, "seen": bool(rows)}
    return out


def daybot_candidate(date: str, code: str, path: str = DAYBOT_DB):
    if not os.path.exists(path):
        return None
    conn = sqlite3.connect(path)
    try:
        rows = _q(conn, "SELECT ts, bought, skip_reason FROM candidate_log WHERE code=? AND ts LIKE ? "
                        "ORDER BY ts", code, f"{date}%")
    finally:
        conn.close()
    if not rows:
        return None
    bought = [r for r in rows if r[1]]
    return f"매수({bought[0][0][11:16]})" if bought else f"후보 {len(rows)}회·{rows[-1][2] or '-'}"


def business_days(d0: str, d1: str) -> int:
    a, b = datetime.date.fromisoformat(d0), datetime.date.fromisoformat(d1)
    return sum(1 for i in range((b - a).days) if (a + datetime.timedelta(days=i + 1)).weekday() < 5)


def analyze(trades: list, conn, store=None, groups: dict = None, price_fn=None) -> list:
    """price_fn(code) → 현재가: 아직 들고 있는 종목 평가손익용(대장은 스윙도 함 — 2026-10-08)."""
    member = {}
    for g, stocks in (groups or {}).items():
        for c, _n in stocks:
            member.setdefault(c, []).append(g)
    for t in trades:
        date, reg_hms = t["reg"][:10], t["reg"][11:19].replace(":", "")
        bars = store.day(t["code"], date) if store else []
        t["buy_t"] = estimate_buy_time(bars, t["entry0"], reg_hms) or reg_hms   # 첫 등록 평단 기준
        # 매도가 없이 해제했으면 해제 시각 직전 1분봉 시세로 수익률 추정(대장은 판 뒤에 해제)
        t["rate_est"] = False
        if t["dereg"] and t["rate"] is None and store and t["entry"]:
            ddate, dhms = t["dereg"][:10], t["dereg"][11:19].replace(":", "")
            dbars = bars if ddate == date else store.day(t["code"], ddate)
            px = [p for tm, p, _h, _l in dbars if tm <= dhms]
            if px:
                t["rate"] = (px[-1] / t["entry"] - 1) * 100
                t["rate_est"] = True
        t["unreal"], t["held_days"] = None, business_days(date, (t["dereg"] or str(datetime.date.today()))[:10])
        if not t["dereg"] and price_fn and t["entry"]:
            try:
                cur = price_fn(t["code"])
                if cur:
                    t["unreal"] = (cur / t["entry"] - 1) * 100
            except Exception:
                pass
        t["buy_est"] = t["buy_t"] != reg_hms
        t["sig"] = signals(conn, date, t["code"], t["buy_t"])
        firsts = [(v["first"], k) for k, v in t["sig"].items() if v["first"]]
        before = [x for x in firsts if x[0].replace(":", "") <= t["buy_t"][:4]]
        t["sig_before"] = min(before)[1] if before else None
        t["lead_min"] = (rp._mins(t["buy_t"]) - rp._mins(min(before)[0].replace(":", ""))) if before else None
        probe = [{"date": date, "time": t["buy_t"], "code": t["code"], "name": t["name"]}]
        rp.sector_tags(probe, tml.LOG_DB)
        t["sector"] = probe[0]["sector"]
        t["daybot"] = daybot_candidate(date, t["code"])
        t["groups"] = member.get(t["code"], [])
        t["bot"] = None
        if store and firsts:
            ft, label = min(firsts)
            table = {lab: tb for tb, lab in PY_TABLES}[label]
            row = _q(conn, f"SELECT price FROM {table} WHERE date=? AND code=? AND time=? AND passed=1",
                     date, t["code"], ft)
            if row and row[0][0]:
                days = store.days_from(t["code"], date, rp.CURRENT.hold_days + 2)
                r = rp.simulate(days, ft.replace(":", "") + "00", row[0][0], rp.CURRENT) if days else {}
                t["bot"] = r or None
    return trades


def _avg(xs):
    xs = [x for x in xs if x is not None]
    return (sum(xs) / len(xs), len(xs)) if xs else (None, 0)


def report(trades: list, days: int) -> str:
    L = [f"🧭 대장 수동매매 vs 신호 — 최근 {days}일, {len(trades)}건 (해제 {sum(bool(t['dereg']) for t in trades)}건"
         f" · 그중 매도가 없어 해제 시각 시세로 추정 {sum(bool(t.get('rate_est')) for t in trades)}건)"]
    if not trades:
        return "\n".join(L + ["   리나 등록/해제 기록 없음(manual_watch_log는 10-07부터 쌓임)"])
    n = len(trades)
    has = [t for t in trades if t["sig_before"]]
    L.append(f"■ 사기 전에 파이썬 검색식이 잡았음 {len(has)}/{n} · 섹터 상위 1·2등 "
             f"{sum(bool(t['sector']) for t in trades)}/{n} · 데이봇 후보였음 "
             f"{sum(bool(t['daybot']) for t in trades)}/{n} · 관심그룹 종목 {sum(bool(t['groups']) for t in trades)}/{n}")
    for label in ("주도주", "단타000", "3개월수급"):
        k = sum(1 for t in trades if t["sig"][label]["first"])
        L.append(f"   {label:<6} 그날 통과 {k}/{n}")
    lead, ln = _avg([t["lead_min"] for t in has])
    if lead is not None:
        L.append(f"   신호 → 대장 매수 평균 {lead:.0f}분 뒤 ({ln}건)")
    done = [t for t in trades if t["rate"] is not None]
    hold = [t for t in trades if t.get("unreal") is not None]
    if done or hold:
        a, k = _avg([t["rate"] for t in done])
        u, m = _avg([t["unreal"] for t in hold])
        L.append("   대장 성적 — " + (f"정리 {k}건 평균 {a:+.2f}%" if k else "정리 0건")
                 + (f" · 보유 {m}건 평가 평균 {u:+.2f}%(손절 거의 안 함 — 들고 가는 매매)" if m else ""))
    for title, grp in (("신호 있던 것", has), ("신호 없던 것", [t for t in trades if not t["sig_before"]])):
        a, k = _avg([t["rate"] for t in grp])
        if k:
            w = sum(1 for t in grp if (t["rate"] or 0) > 0)
            L.append(f"   대장 성적 — {title}: {k}건 승률 {w / k * 100:.0f}% 평균 {a:+.2f}%")
    bots = [t for t in trades if t["bot"]]
    if bots:
        a, k = _avg([t["bot"]["ret"] * 100 for t in bots])
        m, _ = _avg([t["rate"] for t in bots])
        L.append(f"   같은 종목 데이봇 규칙(첫 신호 매수)이었다면: {k}건 평균 {a:+.2f}% (대장 {m:+.2f}%)"
                 if m is not None else f"   같은 종목 데이봇 규칙이었다면: {k}건 평균 {a:+.2f}%")
    L.append("■ 건별")
    for t in trades:
        bt = f"{t['buy_t'][:2]}:{t['buy_t'][2:4]}" + ("추정" if t["buy_est"] else "(등록)")
        if t["rate"] is not None:
            res = f"{t['rate']:+.2f}%" + ("(추정)" if t.get("rate_est") else "") + f" 해제 {t['dereg'][11:16]}"
        else:
            res = f"해제 {t['dereg'][11:16]}(시세없음)" if t["dereg"] else (
                f"보유중 {t['unreal']:+.2f}%" if t.get("unreal") is not None else "보유중")
        res += f" · {t.get('held_days', 0)}영업일"
        adds = f" · 추가매수 {len(t['adds'])}회→평단 {t['entry']:,.0f}" if t.get("adds") else ""
        L.append(f"   {t['reg'][5:10]} {t['name']}({t['code']}) {res} | 매수 {bt} @{t['entry0'] or 0:,.0f}{adds}")
        parts = []
        for label, v in t["sig"].items():
            if v["first"]:
                mark = "✅" if t["sig_before"] and v["first"].replace(":", "") <= t["buy_t"][:4] else "⏩뒤"
                why = f"(매수 때 ✗{v['near'][:20]})" if mark == "⏩뒤" and v["near"] else ""
                parts.append(f"{label} {v['first']}{mark}{why}")
            elif v["near"]:
                parts.append(f"{label} ✗({v['near'][:24]})")
            elif not v["seen"]:
                parts.append(f"{label} 풀밖")
        L.append("      검색식: " + (" · ".join(parts) or "기록 없음"))
        extra = []
        if t["sector"]:
            extra.append(t["sector"])
        if t["groups"]:
            extra.append("관심:" + "/".join(t["groups"][:3]))
        if t["daybot"]:
            extra.append("데이봇 " + t["daybot"])
        if t["bot"]:
            extra.append(f"봇규칙이었다면 {t['bot']['ret'] * 100:+.1f}%({t['bot']['reason']})")
        if extra:
            L.append("      " + " · ".join(extra))
    miss = [t for t in trades if not t["sig_before"] and (t["rate"] or 0) > 0]
    if miss:
        L.append("■ 신호 없이 대장이 수익 낸 종목 — 검색식이 놓친 패턴 후보")
        L.append("   " + ", ".join(f"{t['name']}({t['rate']:+.1f}%)" for t in miss))
    return "\n".join(L)


def main():
    n = next((int(a) for a in sys.argv[1:] if a.isdigit()), 14)
    conn = sqlite3.connect(tml.LOG_DB)
    store, groups, price_fn = None, {}, None
    if "--no-api" not in sys.argv:
        from dotenv import load_dotenv
        for env in (os.path.join(tml._BASE, ".env"), os.path.join(tml._BASE, "lina_bot", ".env")):
            load_dotenv(env)
        sys.path.insert(0, os.path.join(tml._BASE, "core"))
        from kis_api import KisAPI
        api = KisAPI()
        store = rp.MinuteStore(api)

        def price_fn(code):
            return float((api.get_market_data(code) or {}).get("stck_prpr") or 0)
        try:
            import sector_watch as sw
            groups = sw.load_groups(api, sw.hts_id())
        except Exception as e:
            print(f"⚠️ 관심그룹 조회 실패(생략): {e}")
    try:
        print(report(analyze(load_manual(conn, n), conn, store, groups, price_fn), n))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
