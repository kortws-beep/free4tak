"""
sector_weather.py — 자금시장 날씨판 3단계: 관심그룹(분야)별 날씨 (2026-11-17, 대장 "데이터가 보여주는 분야에 진입")
================================================================
[이 파일이 하는 일 — 비개발자용 설명]
대장: "우리 관심그룹을 공격·보통·방어·쉼으로 구분해서 그날 공격 시점을 변화시키는 것. 반도체·로봇에 국한하지 말고 데이터가 보여주는
분야에 진입." → 리나가 3분마다 쌓는 한투 분야별 관심그룹 기록(three_month_leader_log.db sector_obs — 그룹 순위·평균 등락·오른 종목 비율·
거래대금·대장주)으로, 매일 아침 '어제까지' 모습만 보고 분야마다 날씨를 정한다(그날 것은 안 씀 — 미래 보기 금지).
항목(+1/0/-1, 이유 같이):
  · 3일 흐름   — 최근 3일 평균 등락 +1%↑ / -1%↓
  · 순위       — 최근 3일 평균 순위가 상위 1/4 / 하위 1/4
  · 오른 종목 비율 — 어제 60%↑ / 20%↓ (그룹 전체가 같이 움직였나)
  · 돈 쏠림    — 어제 거래대금이 5일 평균의 1.3배↑이면서 올랐으면 +1, 그러면서 빠졌으면 -1
  · 순위 상승  — 3일 전보다 순위가 5계단↑ 오르면 +1(새로 뜨는 분야), 5계단↓ 내리면 -1
태세: 점수 ≥2 🟢공격 / ≥0 🟡보통 / ≥-2 🟠방어 / 그 아래 🔴쉼.
지금은 판단·기록·보여주기까지(키키 아침 메시지·대시보드). 봇 진입 분야 연결은 검증(validate) 쌓인 뒤 대장 OK로.
실행: venv/bin/python intelligence/sector_weather.py [날짜]     (그날 아침 분야 날씨)
      venv/bin/python intelligence/sector_weather.py --validate (아침 날씨 → 그날 그 분야가 시장보다 강했나)
"""
import datetime
import os
import sqlite3
import sys

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEADER_DB = os.path.join(_BASE, "lina_bot", "three_month_leader_log.db")
MW_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "money_weather.db")
NOT_SECTOR = {"isee5", "sub", "new", "끼", "v알림", "신규", "신규추천", "서브", "고정"}   # 분야가 아닌 관심그룹(알림·보관용)
STATES = [(2, "🟢 공격"), (0, "🟡 보통"), (-2, "🟠 방어"), (-99, "🔴 쉼")]


def state_of(score: int) -> str:
    return next(lab for th, lab in STATES if score >= th)


def daily_close(conn, before: str, days: int = 6) -> dict:
    """before 이전 최근 days 거래일, 그룹별 그날 마지막 기록 → {date: {grp: (rank, avg_chg, up_ratio, value, leader)}}."""
    dates = [d for (d,) in conn.execute("SELECT DISTINCT date FROM sector_obs WHERE date < ? ORDER BY date DESC LIMIT ?",
                                        (before, days))][::-1]
    out = {}
    for d in dates:
        t = conn.execute("SELECT MAX(time) FROM sector_obs WHERE date=?", (d,)).fetchone()[0]
        rows = conn.execute("SELECT grp, rank, avg_chg, up_ratio, value, leader FROM sector_obs WHERE date=? AND time=?",
                            (d, t)).fetchall()
        out[d] = {g: (r, c or 0, u or 0, v or 0, ld) for g, r, c, u, v, ld in rows
                  if (g or "").strip().lower() not in NOT_SECTOR}
    return out


def weather(day: str = None, conn=None) -> list:
    """day 아침 분야 날씨 → [(grp, score, state, [(항목, 점수, 이유)], 어제 대장주)] 점수 높은 순."""
    day = day or datetime.date.today().isoformat()
    own = conn is None
    conn = conn or sqlite3.connect(f"file:{LEADER_DB}?mode=ro", uri=True, timeout=10)
    try:
        dc = daily_close(conn, day, 6)
    finally:
        if own:
            conn.close()
    dates = sorted(dc)
    if len(dates) < 4:
        return []
    last, last3 = dates[-1], dates[-3:]
    n = max(len(dc[last]), 1)
    out = []
    for g, (rank, chg, up, val, leader) in dc[last].items():
        items = []
        hist3 = [dc[d][g] for d in last3 if g in dc[d]]
        c3 = sum(x[1] for x in hist3) / len(hist3)
        items.append(("3일 흐름", 1 if c3 > 1 else (-1 if c3 < -1 else 0), f"3일 평균 {c3:+.1f}%"))
        r3 = sum(x[0] for x in hist3) / len(hist3)
        items.append(("순위", 1 if r3 <= n * 0.25 else (-1 if r3 > n * 0.75 else 0), f"3일 평균 {r3:.0f}위/{n}"))
        items.append(("오른 종목 비율", 1 if up >= 60 else (-1 if up <= 20 else 0), f"어제 {up:.0f}%"))
        vals = [dc[d][g][3] for d in dates[:-1] if g in dc[d]]
        if vals and sum(vals):
            x = val / (sum(vals) / len(vals))
            items.append(("돈 쏠림", 1 if (x >= 1.3 and chg > 0) else (-1 if (x >= 1.3 and chg < 0) else 0),
                          f"거래대금 5일 평균의 {x:.1f}배 · 어제 {chg:+.1f}%"))
        before3 = dc[dates[-4]].get(g) if len(dates) >= 4 else None
        if before3:
            mv = before3[0] - rank
            items.append(("순위 상승", 1 if mv >= 5 else (-1 if mv <= -5 else 0), f"3일 전 {before3[0]}위 → 어제 {rank}위"))
        sc = sum(i[1] for i in items)
        out.append((g, sc, state_of(sc), items, leader))
    out.sort(key=lambda x: -x[1])
    return out


def format_weather(rows: list, day: str, top: int = 6) -> str:
    if not rows:
        return "🗺️ 분야 날씨: 기록 부족"
    by = {}
    for g, sc, st, _, ld in rows:
        by.setdefault(st, []).append(f"{g}({sc:+d}{', ' + ld if ld else ''})")
    lines = [f"🗺️ **분야 날씨** {day} 아침 (관심그룹 {len(rows)}개 · 어제까지 기록)"]
    for _, lab in STATES:
        if by.get(lab):
            names = by[lab][:top] if lab != "🔴 쉼" else by[lab][:4]
            more = f" 외 {len(by[lab]) - len(names)}" if len(by[lab]) > len(names) else ""
            lines.append(f"{lab} {len(by[lab])}: " + " · ".join(names) + more)
    return "\n".join(lines)


def save(rows: list, day: str) -> None:
    try:
        c = sqlite3.connect(MW_DB, timeout=10)
        c.execute("CREATE TABLE IF NOT EXISTS sector_board (date TEXT, grp TEXT, score INTEGER, state TEXT, leader TEXT, "
                  "items TEXT, source TEXT, PRIMARY KEY(date, grp))")
        import json
        c.executemany("INSERT OR REPLACE INTO sector_board VALUES (?,?,?,?,?,?,?)",
                      [(day, g, sc, st, ld or "", json.dumps(it, ensure_ascii=False), "sector_obs(리나 3분 기록)")
                       for g, sc, st, it, ld in rows])
        c.commit()
        c.close()
    except Exception as e:
        print(f"⚠️ [분야 날씨] 기록 실패: {e}")


def validate():
    """아침 분야 날씨 → 그날 그 분야 등락 - 전 분야 평균(상대 강도). 앞/뒤 반."""
    conn = sqlite3.connect(f"file:{LEADER_DB}?mode=ro", uri=True, timeout=10)
    dates = [d for (d,) in conn.execute("SELECT DISTINCT date FROM sector_obs ORDER BY date")]
    res = []
    for d in dates[4:]:
        w = weather(d, conn)
        today = daily_close(conn, (datetime.date.fromisoformat(d) + datetime.timedelta(days=1)).isoformat(), 1).get(d, {})
        if not w or not today:
            continue
        avg = sum(v[1] for v in today.values()) / len(today)
        for g, sc, st, _, _ in w:
            if g in today:
                res.append((d, st, today[g][1] - avg, today[g][1]))
    conn.close()
    if not res:
        print("검증할 기록 부족")
        return
    mid = sorted({r[0] for r in res})[len({r[0] for r in res}) // 2]
    print(f"[분야 날씨 검증] {res[0][0]}~{res[-1][0]} · {len({r[0] for r in res})}일 · 관측 {len(res)} (앞/뒤 반 경계 {mid})")
    for _, lab in STATES:
        for part, sel in (("전체", res), ("앞반", [r for r in res if r[0] < mid]), ("뒷반", [r for r in res if r[0] >= mid])):
            g = [r for r in sel if r[1] == lab]
            if g:
                beat = sum(r[2] > 0 for r in g) / len(g) * 100
                print(f"  {lab} {part}: {len(g):>4} · 시장 대비 {sum(r[2] for r in g) / len(g):+.2f}%p (이긴 비율 {beat:.0f}%) · "
                      f"그날 평균 {sum(r[3] for r in g) / len(g):+.2f}%")


if __name__ == "__main__":
    if "--validate" in sys.argv:
        validate()
    else:
        day = next((a for a in sys.argv[1:] if a[:2] == "20"), datetime.date.today().isoformat())
        print(format_weather(weather(day), day))
