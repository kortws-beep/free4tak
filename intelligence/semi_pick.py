"""
semi_pick.py — 급락장 반도체 소부장 '아웃퍼폼 후보' 선별 (2026-11-26, 대장 — 김태성 전략)
================================================================
[이 파일이 하는 일 — 비개발자용 설명]
김태성 제안(대장 전달): "지수 급락 구간엔 안전자산보다 아웃퍼폼 가능 종목 선별이 중요. 반도체 소부장 중
손실률 -50% 넘는 종목만 추가매수 검토, YOY 영업이익 증가폭 큰 기업으로 압축·교체. -10~-30% 손실 종목은 지수 반등 시
자연 회복 가능해 추가매수 불필요." → 리나 `!반도체`로 고무공처럼 줄 세움(매매는 대장 판단).
  · 대상: 한투 관심그룹 중 '반도체_'로 시작하는 분야 종목(대장 정리 소부장). 삼성전자·하이닉스 등 S7 대형주는 뺌
  · 낙폭: 최근 약 6개월(126거래일) 최고 종가 대비 지금 가격(장중이면 실시간) — '손실률'을 고점에서 산 사람 기준으로 봄
      💎 -50%↓ 추가매수 검토  /  👀 -30~-50% 지켜보기  /  🌱 -10~-30% 지수 반등 때 자연 회복(추가매수 불필요)
  · 이익: 네이버 재무(하루 한 번 저장) — 올해 연간 영업이익 컨센서스 vs 작년 실적(YoY %·증가폭 억), 최근 확정 분기 YoY
  · 순위: 💎 안에서 올해 영업이익 증가폭(억) 큰 순 → 적자 지속·감익은 '교체 후보'로 따로 표시
  · 내 보유(리나 등록 종목 중 반도체 분야): 평단 대비 손실률로 같은 기준 판정
실행: venv/bin/python intelligence/semi_pick.py
"""
import datetime
import json
import os
import sqlite3
import sys
import time

import requests

_HERE = os.path.dirname(os.path.abspath(__file__))
_BASE = os.path.dirname(_HERE)
for _d in ("core", "intelligence"):
    if os.path.join(_BASE, _d) not in sys.path:
        sys.path.insert(0, os.path.join(_BASE, _d))

DAILY_DB = os.path.join(_BASE, "lina_bot", "kr_theme_finance.db")
FIN_CACHE = os.path.join(_HERE, "semi_pick_fin.json")
LINA_WATCH = os.path.join(_BASE, "lina_bot", "manual_watch_state.json")
UA = {"User-Agent": "Mozilla/5.0"}
FIN_URL = "https://m.stock.naver.com/api/stock/{}/finance/{}"
LOOKBACK = 126
BUY_DD, WATCH_DD, NATURAL_DD = -50.0, -30.0, -10.0


def _num(v):
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


def bucket(dd) -> str:
    if dd is None:
        return ""
    if dd <= BUY_DD:
        return "💎"
    if dd <= WATCH_DD:
        return "👀"
    if dd <= NATURAL_DD:
        return "🌱"
    return "·"


def parse_fin(annual: dict, quarter: dict) -> dict:
    """네이버 재무 → {op_this(올해 컨센서스·억), op_last(작년 실적), op_yoy%, op_diff, q_label, q_yoy%, cons}."""
    def table(d):
        info = (d or {}).get("financeInfo") or {}
        keys = [(t["key"], t.get("isConsensus") == "Y") for t in info.get("trTitleList") or []]
        op = next((r["columns"] for r in info.get("rowList") or [] if r.get("title") == "영업이익"), {})
        return keys, {k: _num((op.get(k) or {}).get("value")) for k, _ in keys}
    out = {}
    ak, av = table(annual)
    act = [k for k, c in ak if not c and av.get(k) is not None]
    con = [k for k, c in ak if c and av.get(k) is not None]
    if act:
        last = act[-1]
        this = con[0] if con else None
        out["op_last"], out["y_last"] = av[last], last[:4]
        if this:
            out["op_this"], out["y_this"], out["cons"] = av[this], this[:4], True
            out["op_diff"] = av[this] - av[last]
            out["op_yoy"] = (av[this] / av[last] - 1) * 100 if av[last] and av[last] > 0 else None
    qk, qv = table(quarter)
    qa = [k for k, c in qk if not c and qv.get(k) is not None]
    if qa:
        q = qa[-1]
        prev = f"{int(q[:4]) - 1}{q[4:]}"
        if qv.get(prev) is not None:
            out["q_label"] = f"{q[2:4]}.{q[4:]}"
            out["q_op"], out["q_prev"] = qv[q], qv[prev]
            out["q_yoy"] = (qv[q] / qv[prev] - 1) * 100 if qv[prev] and qv[prev] > 0 else None
    return out


def financials(codes: list, get=None, today: str = None) -> dict:
    """{code: parse_fin} — 하루 한 번 파일에 저장(종목당 네이버 2번)."""
    today = today or datetime.date.today().isoformat()
    try:
        with open(FIN_CACHE, encoding="utf-8") as f:
            cache = json.load(f)
    except (OSError, ValueError):
        cache = {}
    if cache.get("date") != today:
        cache = {"date": today, "data": {}}
    get = get or (lambda url: requests.get(url, headers=UA, timeout=8).json())
    changed = False
    for c in codes:
        if c in cache["data"]:
            continue
        try:
            cache["data"][c] = parse_fin(get(FIN_URL.format(c, "annual")), get(FIN_URL.format(c, "quarter")))
        except Exception as e:
            print(f"⚠️ [반도체] {c} 재무 조회 실패: {e}")
            cache["data"][c] = {}
        changed = True
        time.sleep(0.15)
    if changed:
        tmp = FIN_CACHE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False)
        os.replace(tmp, FIN_CACHE)
    return cache["data"]


def highs(names: set, days: int = LOOKBACK) -> dict:
    """{종목명: 최근 days 거래일 최고 종가} — 일봉 DB(이름 키)."""
    if not names:
        return {}
    c = sqlite3.connect(f"file:{DAILY_DB}?mode=ro", uri=True, timeout=10)
    try:
        dates = [d for (d,) in c.execute("SELECT DISTINCT date FROM kr_stock_daily_data ORDER BY date DESC LIMIT ?", (days,))]
        if not dates:
            return {}
        q = ",".join("?" * len(names))
        return {n: float(h) for n, h in c.execute(
            f"SELECT stock_name, MAX(close_price) FROM kr_stock_daily_data WHERE date >= ? AND stock_name IN ({q}) "
            f"GROUP BY stock_name", [dates[-1]] + list(names)) if h}
    finally:
        c.close()


def universe(groups: dict) -> dict:
    """{(code, name): [반도체 분야...]} — S7 대형주 제외."""
    import kr_heatmap
    out = {}
    for g, v in groups.items():
        if not g.startswith("반도체_"):
            continue
        for code, n in v:
            if code in kr_heatmap.S7_CODES:
                continue
            out.setdefault((code, n), []).append(g.replace("반도체_", ""))
    return out


def candidates(groups: dict = None, quotes: dict = None, fin: dict = None, hi: dict = None) -> list:
    import kr_heatmap
    groups = groups if groups is not None else kr_heatmap.load_groups()
    uni = universe(groups)
    codes = sorted({c for c, _ in uni})
    quotes = quotes if quotes is not None else kr_heatmap.quotes(codes)
    hi = hi if hi is not None else highs({n for _, n in uni})
    fin = fin if fin is not None else financials(codes)
    rows = []
    for (code, n), gs in uni.items():
        q = quotes.get(code) or {}
        price, h = q.get("price"), hi.get(n)
        if not price or not h:
            continue
        h = max(h, price)
        f = fin.get(code) or {}
        rows.append({"code": code, "name": n, "groups": gs, "price": price, "chg": q.get("chg"),
                     "high": h, "dd": (price / h - 1) * 100, **f})
    rows.sort(key=lambda r: r["dd"])
    return rows


def grow_ok(r) -> bool:
    """올해 영업이익이 작년보다 늘 것(컨센서스) — 흑자전환 포함."""
    return r.get("op_diff") is not None and r["op_diff"] > 0 and (r.get("op_this") or 0) > 0


def holdings_view(rows: list, watch_path: str = LINA_WATCH) -> list:
    """리나 등록 종목 중 반도체 분야 → [(이름, 계좌, 평단, 지금가, 손실률, 판정, 이익 OK)]."""
    try:
        with open(watch_path, encoding="utf-8") as f:
            w = json.load(f)
    except (OSError, ValueError):
        return []
    by = {r["code"]: r for r in rows}
    out = []
    for key, v in w.items():
        code = key.split("@")[0]
        r = by.get(code)
        ep = v.get("entry_price")
        if not r or not ep:
            continue
        loss = (r["price"] / ep - 1) * 100
        acct = {"kiwoom": "K", "daybot": "D", "swing": "S"}.get(v.get("account") or "kiwoom", "K")
        out.append((r["name"], acct, ep, r["price"], loss, bucket(loss), grow_ok(r)))
    return sorted(out, key=lambda x: x[4])


def _op(r) -> str:
    if r.get("op_this") is None:
        return "이익 자료 없음"
    yoy = f"{r['op_yoy']:+.0f}%" if r.get("op_yoy") is not None else ("흑자전환" if (r.get("op_last") or 0) <= 0 < r["op_this"] else "-")
    q = f" · {r['q_label']}분기 {r['q_yoy']:+.0f}%" if r.get("q_yoy") is not None else ""
    return f"{r.get('y_this', '올해')}E 영업익 {r['op_this']:,.0f}억({yoy}, {r['op_diff']:+,.0f}억){q}"


def format_rows(rows: list, hold: list = None, top: int = 8) -> str:
    now = datetime.datetime.now().strftime("%H:%M")
    buy = [r for r in rows if r["dd"] <= BUY_DD]
    good = sorted([r for r in buy if grow_ok(r)], key=lambda r: -(r.get("op_diff") or 0))
    swap = [r for r in buy if not grow_ok(r)]
    watch = sorted([r for r in rows if BUY_DD < r["dd"] <= WATCH_DD and grow_ok(r)], key=lambda r: -(r.get("op_diff") or 0))
    L = [f"🔬 **반도체 소부장 선별** {now} (김태성 전략 · 관심그룹 반도체 분야 {len(rows)}종목 · 6개월 고점 대비 · 매매는 대장 판단)"]
    L.append(f"💎 **-50%↓ & 올해 영업이익 증가** — 추가매수 검토 ({len(good)}종목, 증가폭 큰 순)")
    L += [f"   {i}. **{r['name']}**({r['code']}) [{'·'.join(r['groups'][:2])}] 고점 대비 {r['dd']:.0f}% · {_op(r)}"
          + (f" · 오늘 {r['chg']:+.1f}%" if r.get("chg") is not None else "") for i, r in enumerate(good[:top], 1)] or ["   (없음)"]
    if swap:
        L.append(f"🔁 -50%↓인데 이익 감소·적자 — 교체 후보: " + ", ".join(f"{r['name']}({r['dd']:.0f}%)" for r in swap[:6]))
    if watch:
        L.append(f"👀 -30~-50% & 이익 증가 — 지켜보기: " + ", ".join(f"{r['name']}({r['dd']:.0f}%)" for r in watch[:6]))
    if hold:
        L.append("📒 **내 보유(리나 등록) 반도체**")
        for n, a, ep, p, loss, b, ok in hold:
            act = ("추가매수 검토" if ok else "교체 검토(이익 감소)") if b == "💎" else \
                  ("지켜보기" if b == "👀" else ("지수 반등 때 자연 회복 — 추가매수 불필요" if b == "🌱" else "보유"))
            L.append(f"   {b} {n}({a}) 평단 {ep:,.0f} → {p:,.0f} ({loss:+.1f}%) · {act}")
    L.append("   (-10~-30%는 지수 반등 때 자연 회복 가능 — 추가매수 불필요 · 이익은 네이버 컨센서스 기준, 하루 한 번 갱신)")
    return "\n".join(L)


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv(os.path.join(_BASE, ".env"))
    rows = candidates()
    print(format_rows(rows, holdings_view(rows)))
