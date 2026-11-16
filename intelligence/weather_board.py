"""
weather_board.py — 자금시장 날씨판 2단계: 층별 점수 → 태세(🟢공격·🟡보통·🟠방어·🔴쉼) (2026-11-17, 대장 "키키가 관제탑")
================================================================
[이 파일이 하는 일 — 비개발자용 설명]
money_weather.py가 쌓는 재료로 '오늘 아침 날씨'를 정한다. 아직 **보여 주기만** — 봇은 안 건드림(대장 OK 뒤 4단계에서 연결).
블랙박스 금지: 항목마다 +1/0/-1과 이유를 그대로 적어서 "왜 방어야?"에 바로 답할 수 있게.
  1층 글로벌(미국장 마감 기준, 한국 아침에 이미 확정된 것만) — 반도체 5일·나스닥 1일·VIX 수준·VIX 급등·미국 10년 금리 5일·
       달러 5일·비트코인 5일
  2층 한국 주식 — 국면(20일선 위 비율)·외국인 현물 연속성(대장: 약 4일)·외국인 선물 방향·코스피 20일 고점 대비
  2층 코인 — 공포탐욕지수(cbot 상태)·비트코인 20일선 위/아래
태세: 점수 합으로 🟢공격 / 🟡보통 / 🟠방어 / 🔴쉼. 기준값은 검증(validate)으로 다듬음 — 5년 백테스트는 1층만 가능
(2층 재료는 국면 07-14~·수급 25-08~라 짧음).
실행: venv/bin/python intelligence/weather_board.py            (오늘 날씨판)
      venv/bin/python intelligence/weather_board.py --validate (1층 5년 검증: 태세별 코스피 그날·5일 뒤)
"""
import datetime
import json
import os
import sqlite3
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_BASE = os.path.dirname(_HERE)
MW_DB = os.path.join(_HERE, "money_weather.db")
LEADER_DB = os.path.join(_BASE, "lina_bot", "three_month_leader_log.db")
CBOT_STATE = os.path.join(_BASE, "cbot_state.json")

STATES = [(3, "🟢 공격"), (0, "🟡 보통"), (-3, "🟠 방어"), (-99, "🔴 쉼")]   # 점수 ≥ 기준이면 그 태세(위에서부터)


def state_of(score: int) -> str:
    return next(lab for th, lab in STATES if score >= th)


def _series(conn, sym: str, before: str, n: int) -> list:
    """before(날짜) '이전' 최근 n개 [(date, close)] 오래된→최신."""
    rows = conn.execute("SELECT date, close FROM global_daily WHERE symbol=? AND date < ? ORDER BY date DESC LIMIT ?",
                        (sym, before, n)).fetchall()
    return rows[::-1]


def _ret(s: list, k: int):
    return (s[-1][1] / s[-1 - k][1] - 1) * 100 if len(s) > k and s[-1 - k][1] else None


def layer_global(day: str, conn=None) -> tuple:
    """1층 → (점수, [(항목, 점수, 이유)]). day 아침(한국) 기준 — 그 전날까지 끝난 해외 장만."""
    own = conn is None
    conn = conn or sqlite3.connect(MW_DB, timeout=10)
    try:
        sox, ndx, vix = _series(conn, "^SOX", day, 6), _series(conn, "^IXIC", day, 2), _series(conn, "^VIX", day, 2)
        tnx, dxy, btc = _series(conn, "^TNX", day, 6), _series(conn, "DX-Y.NYB", day, 6), _series(conn, "BTC-USD", day, 6)
    finally:
        if own:
            conn.close()
    items = []

    def add(name, cond_up, cond_down, why):
        sc = 1 if cond_up else (-1 if cond_down else 0)
        items.append((name, sc, why))

    r = _ret(sox, 5)
    if r is not None:
        add("미국 반도체 5일", r > 3, r < -3, f"SOX 5일 {r:+.1f}%")
    r = _ret(ndx, 1)
    if r is not None:
        add("나스닥 전날", r > 1, r < -1, f"나스닥 {r:+.1f}%")
    if vix:
        v = vix[-1][1]
        add("VIX 수준", v < 16, v > 22, f"VIX {v:.1f}")
        r = _ret(vix, 1)
        if r is not None:
            add("VIX 급등", False, r > 10, f"VIX 하루 {r:+.0f}%")
    if len(tnx) > 5:
        d = tnx[-1][1] - tnx[-6][1]
        add("미국 10년 금리 5일", d < -0.15, d > 0.15, f"10년 {tnx[-1][1]:.2f}% (5일 {d:+.2f}%p)")
    r = _ret(dxy, 5)
    if r is not None:
        add("달러 5일", r < -1, r > 1, f"달러 인덱스 5일 {r:+.1f}%")
    r = _ret(btc, 5)
    if r is not None:
        add("비트코인 5일", r > 5, r < -5, f"BTC 5일 {r:+.1f}%")
    return sum(i[1] for i in items), items


def layer_kr(day: str) -> tuple:
    """2층 한국 주식 → (점수, 항목들). 리나 기록(국면·수급·지수)으로, day 전날까지."""
    items = []
    try:
        c = sqlite3.connect(f"file:{LEADER_DB}?mode=ro", uri=True, timeout=10)
        try:
            b = c.execute("SELECT date, breadth FROM market_regime WHERE date < ? ORDER BY date DESC LIMIT 1", (day,)).fetchone()
            inv = c.execute("SELECT date, foreign_ FROM market_investor WHERE market='KOSPI' AND date < ? ORDER BY date DESC "
                            "LIMIT 8", (day,)).fetchall()
            fut = c.execute("SELECT foreign_ FROM market_investor WHERE market='FUT' AND date < ? ORDER BY date DESC LIMIT 1",
                            (day,)).fetchone()
            idx = c.execute("SELECT date, close FROM market_index WHERE idx='KOSPI' AND date < ? ORDER BY date DESC LIMIT 20",
                            (day,)).fetchall()
        finally:
            c.close()
    except Exception as e:
        return 0, [("한국 주식 재료", 0, f"읽기 실패: {e}")]
    if b:
        items.append(("국면", 1 if b[1] >= 50 else (-1 if b[1] < 35 else 0), f"20일선 위 {b[1]:.0f}% ({b[0][5:]})"))
    if inv:
        streak = 0
        for _, f in inv:
            if (f or 0) > 0:
                streak += 1
            else:
                break
        last = inv[0][1] or 0
        sc = 1 if streak >= 4 else (-1 if last < 0 else 0)
        items.append(("외국인 현물", sc, f"{inv[0][0][5:]} {last:+,.0f}억 · 연속 순매수 {streak}일(대장 기준 약 4일)"))
    if fut and inv:
        f = fut[0] or 0
        items.append(("외국인 선물", 1 if f > 3000 else (-1 if f < -3000 else 0), f"선물 {f:+,.0f}"))
    if len(idx) >= 5:
        hi = max(x[1] for x in idx)
        dd = (idx[0][1] / hi - 1) * 100
        items.append(("코스피 20일 고점 대비", 1 if dd > -3 else (-1 if dd < -8 else 0), f"{dd:+.1f}%"))
    return sum(i[1] for i in items), items


def layer_coin(day: str, conn=None) -> tuple:
    items = []
    try:
        st = json.load(open(CBOT_STATE, encoding="utf-8")).get("last_status") or {}
        fg = st.get("fear_greed")
        if fg is not None:
            items.append(("공포탐욕지수", 1 if fg > 55 else (-1 if fg < 20 else 0), f"{fg} (cbot 현재값)"))
    except Exception:
        pass
    own = conn is None
    conn = conn or sqlite3.connect(MW_DB, timeout=10)
    try:
        btc = _series(conn, "BTC-USD", day, 20)
    finally:
        if own:
            conn.close()
    if len(btc) >= 20:
        ma = sum(x[1] for x in btc) / len(btc)
        items.append(("비트코인 20일선", 1 if btc[-1][1] > ma else -1, f"{btc[-1][1]:,.0f} vs 20일 평균 {ma:,.0f}"))
    return sum(i[1] for i in items), items


def combined(g: int, layer2: int) -> str:
    """★ 11-17 봇 제어용 태세 — 5년 검증을 통과한 건 1층(글로벌)뿐이라 1층 태세가 기준. 2층(한국 주식·코인)은 기록이 짧아
    '나쁠 때 한 단계 더 조심(≤-2면 한 칸 아래)'로만 쓰고 올려 주지는 않음(검증 쌓이면 다시 정함)."""
    labels = [lab for _, lab in STATES]
    i = labels.index(state_of(g))
    if layer2 <= -2:
        i = min(i + 1, len(labels) - 1)
    return labels[i]


def board(day: str = None) -> dict:
    day = day or datetime.date.today().isoformat()
    g, gi = layer_global(day)
    k, ki = layer_kr(day)
    c, ci = layer_coin(day)
    return {"date": day, "global": (g, state_of(g), gi), "kr": (k, state_of(k), ki), "coin": (c, state_of(c), ci),
            "stock_total": (g + k, combined(g, k)), "coin_total": (g + c, combined(g, c))}


def format_board(b: dict) -> str:
    def lines(items):
        return "\n".join(f"   {'＋' if s > 0 else ('－' if s < 0 else '·')} {n}: {why}" for n, s, why in items)
    return (f"🌦️ **자금시장 날씨판** {b['date']} (관찰 중 — 봇 제어 안 함)\n"
            f"1층 글로벌 {b['global'][1]} ({b['global'][0]:+d})\n{lines(b['global'][2])}\n"
            f"2층 한국 주식 {b['kr'][1]} ({b['kr'][0]:+d})\n{lines(b['kr'][2])}\n"
            f"2층 코인 {b['coin'][1]} ({b['coin'][0]:+d})\n{lines(b['coin'][2])}\n"
            f"→ 주식 봇 태세 {b['stock_total'][1]} · 코인 봇 태세 {b['coin_total'][1]} "
            f"(1층 태세가 기준, 2층이 -2 이하면 한 칸 더 조심)")


def save_board(b: dict) -> None:
    """매일 날씨판 기록(4단계 검증용) — money_weather.db board_daily. 실패해도 조용히."""
    try:
        c = sqlite3.connect(MW_DB, timeout=10)
        c.execute("CREATE TABLE IF NOT EXISTS board_daily (date TEXT, layer TEXT, score INTEGER, state TEXT, items TEXT, "
                  "created_at TEXT, PRIMARY KEY(date, layer))")
        now = datetime.datetime.now().isoformat(timespec="seconds")
        for layer in ("global", "kr", "coin"):
            sc, st, items = b[layer]
            c.execute("INSERT OR REPLACE INTO board_daily VALUES (?,?,?,?,?,?)",
                      (b["date"], layer, sc, st, json.dumps(items, ensure_ascii=False), now))
        for layer in ("stock_total", "coin_total"):
            sc, st = b[layer]
            c.execute("INSERT OR REPLACE INTO board_daily VALUES (?,?,?,?,?,?)", (b["date"], layer, sc, st, "", now))
        c.commit()
        c.close()
    except Exception as e:
        print(f"⚠️ [날씨판] 기록 실패: {e}")


def validate(sym: str = "^KS11"):
    """1층 점수(그날 아침 기준)별 코스피 그날 등락·5거래일 뒤 등락 — 5년, 앞/뒤 반."""
    conn = sqlite3.connect(MW_DB, timeout=10)
    ks = conn.execute("SELECT date, close FROM global_daily WHERE symbol=? ORDER BY date", (sym,)).fetchall()
    rows = []
    for i in range(1, len(ks) - 5):
        d = ks[i][0]
        sc, _ = layer_global(d, conn)
        rows.append((d, sc, (ks[i][1] / ks[i - 1][1] - 1) * 100, (ks[i + 5][1] / ks[i - 1][1] - 1) * 100))
    conn.close()
    mid = rows[len(rows) // 2][0]
    print(f"[1층 글로벌 점수 → {sym} 검증] {rows[0][0]}~{rows[-1][0]} {len(rows)}일 (앞/뒤 반 경계 {mid})")
    for _, lab in STATES:
        for part, sel in (("전체", rows), ("앞반", [r for r in rows if r[0] < mid]), ("뒷반", [r for r in rows if r[0] >= mid])):
            g = [r for r in sel if state_of(r[1]) == lab]
            if not g:
                continue
            up = sum(r[2] > 0 for r in g) / len(g) * 100
            print(f"  {lab} {part}: {len(g):>4}일 · 그날 평균 {sum(r[2] for r in g) / len(g):+.2f}% (오른 날 {up:.0f}%) · "
                  f"5일 뒤 평균 {sum(r[3] for r in g) / len(g):+.2f}%")


if __name__ == "__main__":
    if "--validate" in sys.argv:
        validate("^KS11")
        validate("^KQ11")
    else:
        print(format_board(board()))
