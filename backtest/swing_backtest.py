"""
swing_backtest.py — 관심그룹 스윙 백테스트 (2026-10-08, 대장 매매 방식 검증)
================================================================
[이 파일이 하는 일 — 비개발자용 설명]

대장 방식: "신호만 보고 사는 게 아니라, 당장 수익이 안 나도 며칠 들고 가도 되는 종목
위주로 산다 — 단타가 될 수도 스윙이 될 수도 있고, 손절은 최소한." 후보는 주도주·단타
검색식·한투 NEW 그룹·키움 X-ray를 같이 보고(순위 없음), 이슈·유튜브·미국장 전일 상승과
맞는지로 고른다.
질문: "고른 범위(관심그룹) + 들고 가는 매매(넓은 손절·며칠 보유)"가 숫자로도 통하나?

방법 (리나 일봉 DB kr_theme_finance.db, 약 200거래일):
  신호(주도주 근사): 그날 +5% 이상 오르고 거래대금 상위 30위 안(ETF·스팩·우선주 제외)
  → 그날 종가에 매수(+0.1%) → 다음 날부터 일봉으로 매도 규칙 적용
  매도 규칙: 단타형(-3.5%·+2.5% 트레일·3일) vs 스윙형(넓은 손절/손절 없음·+5% 뒤 트레일·10~20일)
  나눠 보기: 한투 NEW 그룹 / 그 밖 관심그룹 / 관심그룹 밖, 그리고 맥락(유튜브 언급·미국 연결 강세)
  "들고 있으면 돌아오나": 손절 없이 들고 갔을 때 -5%·-10%까지 밀린 뒤 본전 회복 비율
가정·한계:
  · 일봉 안 순서를 모르므로 손절·트레일은 시가 갭 먼저, 그다음 저가로 판정(불리하게)
  · ★ 관심그룹은 "지금" 구성 — 대장이 최근 뜬 종목으로 NEW를 채웠다면 과거 성적이 부풀려짐
    (뒤늦은 선택 편향). 관심그룹 안/밖 차이는 이 점을 감안해서 볼 것.
  · 실제 검색식 신호(리나 기록)는 10-06부터라 아직 스윙 결과가 안 익음 — 쌓이면 같은 규칙 적용
실행:  python backtest/swing_backtest.py [--days 250] [--no-api]
       관심그룹은 처음 한 번 한투에서 읽어 backtest/data/watch_groups.json에 저장(이후 재사용,
       새로 읽으려면 --refresh-groups)
"""
import os
import sys
import json
import sqlite3
import datetime
from dataclasses import dataclass

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "intelligence"))
import three_month_leader as tml  # noqa: E402

GROUPS_JSON = os.path.join(BASE, "backtest", "data", "watch_groups.json")
COST, SLIP = 0.002, 0.001
CHG_MIN = 5.0          # 신호: 그날 등락률
VALUE_TOP = 30         # 신호: 그날 거래대금 순위
NEW_GROUP_NAMES = ("new", "신규추천", "신규", "new추천")     # sector_watch와 같은 기준


@dataclass(frozen=True)
class SwingRule:
    name: str
    stop: float = None          # 손절(%) — None이면 없음
    tp: float = 5.0             # 이만큼 오르면 트레일링 시작
    trail: float = 4.0          # 고점 대비 이만큼 밀리면 매도
    floor: float = 1.0          # 트레일링 매도 최소 보장 수익
    max_days: int = 10          # 이 거래일 지나면 종가 정리


RULES = [
    SwingRule("단타형(현행 근사)", stop=-3.5, tp=2.5, trail=1.5, max_days=3),
    SwingRule("스윙 손절-7 10일", stop=-7.0),
    SwingRule("스윙 손절-10 10일", stop=-10.0),
    SwingRule("스윙 손절없음 10일", stop=None),
    SwingRule("스윙 손절-10 20일 트레일5", stop=-10.0, tp=8.0, trail=5.0, max_days=20),
]


# ── 시뮬레이션 (순수 함수) ─────────────────────────────────
def simulate(entry_close: float, after: list, rule: SwingRule) -> dict:
    """after = 매수 다음 날부터 [(date, o, h, l, c)]. → {ret, days, reason, mae}"""
    entry = entry_close * (1 + SLIP)
    stop_px = entry * (1 + rule.stop / 100) if rule.stop is not None else None
    peak, mae = None, 0.0

    def out(px, i, reason):
        return {"ret": px * (1 - SLIP) / entry - 1 - COST, "days": i + 1, "reason": reason, "mae": mae}
    for i, (d, o, h, l, c) in enumerate(after):
        if peak is None:
            if stop_px and l <= stop_px:
                mae = min(mae, (l / entry - 1) * 100)
                return out(min(o, stop_px), i, "손절")
            mae = min(mae, (l / entry - 1) * 100)
            if h >= entry * (1 + rule.tp / 100):
                peak = h
        else:
            ts = max(peak * (1 - rule.trail / 100), entry * (1 + rule.floor / 100))
            if l <= ts:
                return out(min(o, ts), i, "트레일링")
            peak = max(peak, h)
        if i + 1 >= rule.max_days:
            return out(c, i, "기한")
    if not after:
        return {}
    return dict(out(after[-1][4], len(after) - 1, "보유중"))


def recovery(entry_close: float, after: list, dip: float, horizon: int = 10):
    """손절 없이 들고 갔을 때: horizon일 안에 -dip% 이상 밀렸나, 밀렸다면 그 뒤 본전(매수가) 회복했나.
    → None(안 밀림) / True(회복) / False(못 함)"""
    entry = entry_close * (1 + SLIP) * (1 + COST)
    dipped = False
    for d, o, h, l, c in after[:horizon]:
        if not dipped and l <= entry * (1 - dip / 100):
            dipped = True
            continue
        if dipped and h >= entry:
            return True
    return False if dipped else None


# ── 데이터 ────────────────────────────────────────────────
def load_daily(db: str, days: int) -> dict:
    """{종목명: [(date, o, h, l, c, value)]} 최근 days거래일."""
    conn = sqlite3.connect(db)
    try:
        dates = [d for (d,) in conn.execute("SELECT DISTINCT date FROM kr_stock_daily_data "
                                            "ORDER BY date DESC LIMIT ?", (days,))]
        if not dates:
            return {}
        rows = conn.execute("SELECT stock_name, date, open_price, high_price, low_price, close_price, volume, "
                            "trade_value FROM kr_stock_daily_data WHERE date >= ? ORDER BY date",
                            (min(dates),)).fetchall()
    finally:
        conn.close()
    out = {}
    for name, d, o, h, l, c, v, tv in rows:
        if c and h and l and o:
            out.setdefault(name, []).append((d, o, h, l, c, tv or c * (v or 0)))
    return out


def make_signals(daily: dict, name_code: dict, chg_min: float = CHG_MIN, top: int = VALUE_TOP,
                 exclude=None) -> list:
    """그날 +chg_min% 이상 & 거래대금 상위 top위 → [{date, name, code, i, chg, rank}]."""
    by_date = {}
    for name, rows in daily.items():
        code = name_code.get(name, "")
        if exclude and exclude(code, name):
            continue
        for i in range(1, len(rows)):
            prev_c, (d, o, h, l, c, val) = rows[i - 1][4], rows[i]
            chg = (c / prev_c - 1) * 100 if prev_c else 0
            by_date.setdefault(d, []).append((val, name, code, i, chg))
    sig = []
    for d, xs in by_date.items():
        xs.sort(key=lambda x: -x[0])
        for rank, (val, name, code, i, chg) in enumerate(xs[:top], 1):
            if chg >= chg_min:
                sig.append({"date": d, "name": name, "code": code, "i": i, "chg": chg, "rank": rank})
    return sorted(sig, key=lambda s: (s["date"], s["rank"]))


def load_groups(refresh: bool, api_ok: bool) -> dict:
    if not refresh and os.path.exists(GROUPS_JSON):
        with open(GROUPS_JSON, encoding="utf-8") as f:
            return {g: [tuple(x) for x in v] for g, v in json.load(f).items()}
    if not api_ok:
        return {}
    from dotenv import load_dotenv
    for env in (os.path.join(BASE, ".env"), os.path.join(BASE, "lina_bot", ".env")):
        load_dotenv(env)
    sys.path.insert(0, os.path.join(BASE, "core"))
    from kis_api import KisAPI
    import sector_watch as sw
    groups = sw.load_groups(KisAPI(), sw.hts_id())
    os.makedirs(os.path.dirname(GROUPS_JSON), exist_ok=True)
    with open(GROUPS_JSON, "w", encoding="utf-8") as f:
        json.dump(groups, f, ensure_ascii=False)
    return groups


def universe(code: str, groups: dict) -> str:
    gs = [g for g, stocks in groups.items() if any(c == code for c, _ in stocks)]
    if any(g.strip().lower() in NEW_GROUP_NAMES for g in gs):
        return "NEW그룹"
    return "관심그룹(NEW외)" if gs else "관심그룹 밖"


# ── 집계 ──────────────────────────────────────────────────
def stats(rs: list) -> dict:
    rets = [r["ret"] for r in rs]
    if not rets:
        return {"n": 0}
    w = [x for x in rets if x > 0]
    l = [x for x in rets if x <= 0]
    return {"n": len(rets), "win": len(w) / len(rets) * 100, "avg": sum(rets) / len(rets) * 100,
            "pf": sum(w) / -sum(l) if l and sum(l) < 0 else float("inf"),
            "days": sum(r["days"] for r in rs) / len(rs), "worst": min(rets) * 100}


def fmt(s: dict) -> str:
    if not s["n"]:
        return "     0건"
    pf = f"{s['pf']:.2f}" if s["pf"] != float("inf") else "∞"
    return (f"{s['n']:>5}건 승률 {s['win']:>3.0f}% 평균 {s['avg']:+.2f}% PF {pf:>4} "
            f"보유 {s['days']:.1f}일 최악 {s['worst']:+.0f}%")


def main():
    args = sys.argv[1:]
    days = int(args[args.index("--days") + 1]) if "--days" in args else 250
    api_ok = "--no-api" not in args
    daily = load_daily(tml.THEME_DB, days)
    conn = sqlite3.connect(tml.THEME_DB)
    try:
        name_code = tml._name_code_map(conn)
    finally:
        conn.close()
    try:
        import leader_scan as ls
        exclude = ls.excluded_by_name
    except Exception:
        exclude = None
    groups = load_groups("--refresh-groups" in args, api_ok)
    sig = make_signals(daily, name_code, exclude=exclude)
    for s in sig:
        s["uni"] = universe(s["code"], groups) if groups else "-"
    span = f"{sig[0]['date']} ~ {sig[-1]['date']}" if sig else "-"
    print(f"📈 스윙 백테스트 — 신호 {len(sig)}개 ({span}) · 그날 +{CHG_MIN:g}%↑ & 거래대금 {VALUE_TOP}위 안, 종가 매수")
    if groups:
        cnt = {}
        for s in sig:
            cnt[s["uni"]] = cnt.get(s["uni"], 0) + 1
        print("   " + " · ".join(f"{k} {v}개" for k, v in sorted(cnt.items())) +
              f"  (관심그룹 {len(groups)}개, 지금 구성 기준)")
    else:
        print("   ⚠️ 관심그룹 못 읽음 — 범위 비교 생략(--no-api 빼고 실행하면 한투에서 읽어 저장)")

    # 맥락: 유튜브 언급(3일 안) · 미국 연결 강세
    try:
        import manual_vs_signal as mv
        for s in sig:
            s["yt"] = bool(mv.youtube_mentions(s["name"], f"{s['date']} 15:30:00"))
        if api_ok:
            links = {s["name"]: mv.us_links(s["name"]) for s in sig}
            tickers = [tk for v in links.values() for tk, _ in v]
            ch = mv.fetch_us_changes(tickers, days + 30) if tickers else {}
            for s in sig:
                ctx = mv.us_context(links[s["name"]], s["date"], ch)
                s["us"] = bool(ctx) and ctx[0][1] >= mv.US_STRONG_PCT
    except Exception as e:
        print(f"⚠️ 맥락(유튜브·미국) 계산 생략: {e}")

    results = {}
    for rule in RULES:
        rs = []
        for s in sig:
            rows = daily[s["name"]]
            r = simulate(rows[s["i"]][4], [x[:5] for x in rows[s["i"] + 1:]], rule)
            if r and r["reason"] != "보유중":
                rs.append(dict(s, **r))
        results[rule.name] = rs

    unis = ["NEW그룹", "관심그룹(NEW외)", "관심그룹 밖"] if groups else []
    print("\n■ 매도 규칙 × 범위 (끝난 거래만)")
    for rule in RULES:
        rs = results[rule.name]
        print(f"  [{rule.name}]")
        print(f"   {'전체':<14}{fmt(stats(rs))}")
        for u in unis:
            print(f"   {u:<14}{fmt(stats([r for r in rs if r['uni'] == u]))}")

    print("\n■ 맥락별 (스윙 손절-10 10일 / 단타형)")
    for label, f in (("유튜브 언급", lambda r: r.get("yt")), ("미국 연결 강세", lambda r: r.get("us")),
                     ("둘 다", lambda r: r.get("yt") and r.get("us")),
                     ("맥락 없음", lambda r: not r.get("yt") and not r.get("us"))):
        a = stats([r for r in results["스윙 손절-10 10일"] if f(r)])
        b = stats([r for r in results["단타형(현행 근사)"] if f(r)])
        print(f"   {label:<10} 스윙 {fmt(a)}")
        print(f"   {'':<10} 단타 {fmt(b)}")

    print("\n■ 들고 있으면 돌아오나 — 손절 없이 10거래일, 밀린 뒤 본전 회복 비율")
    for dip in (5, 10):
        for u in ["전체"] + unis:
            pick = [s for s in sig if u == "전체" or s["uni"] == u]
            res = [recovery(daily[s["name"]][s["i"]][4], [x[:5] for x in daily[s["name"]][s["i"] + 1:]], dip)
                   for s in pick if len(daily[s["name"]]) > s["i"] + 10]
            dipped = [x for x in res if x is not None]
            if res:
                rec = sum(1 for x in dipped if x)
                print(f"   -{dip}% {u:<14} 밀린 비율 {len(dipped) / len(res) * 100:>3.0f}% "
                      f"({len(dipped)}/{len(res)}) → 그중 10일 안 본전 회복 "
                      f"{(rec / len(dipped) * 100) if dipped else 0:>3.0f}%")
    print("\n※ 관심그룹은 지금 구성 기준이라 과거 성적이 부풀려졌을 수 있음(뒤늦은 선택 편향). "
          "종가 매수·일봉 근사.")


if __name__ == "__main__":
    sys.exit(main())
