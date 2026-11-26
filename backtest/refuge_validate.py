"""
refuge_validate.py — 보험용(피난처) 섹터 검증: AI 분야 vs 나머지 분야 (2026-11-26, 대장)
================================================================
[이 파일이 하는 일 — 비개발자용 설명]
대장 구상(11-25~26): 인버스 대신 'AI주 들 때 반대급부 1종목(헤지)'. 국면 35% 미만이면 daybot·sbot 각각 봇 1 + 헤지 1.
"어차피 그날그날 바뀌니 섹터별 데이터 검증이 먼저", "AI가 묻은 분야 vs 나머지, 애매한 건 빼고, 보안·코인·금융 넣기".
  · AI 그룹: 관심그룹 반도체 7 + S7 대형주 + 전력 2 + 신재생_ESS + 통신 + 로봇 3
  · 나머지: 조선 2 · 방산 · 철강 · 바이오 3 · 화장품 · 미용의료기기 + 보안·코인·금융(테마 DB 종목)
  · 제외(애매): 원전 2 · 우주 · 2차전지 2
  · 분야 하루 등락 = 그 분야 종목 단순 평균(일봉 kr_stock_daily_data, 2년) — 시가 갭·종가·장중
  · 국면 = 전 종목 중 20일선 위 비율(일봉으로 2년 다시 계산, market_regime과 같은 정의)
질문
  ① AI 그룹이 크게 빠진 날(-3%↓) 나머지 분야 중 어디가 버텼나 · 늘 같은 분야인가(날마다 바뀌나)
  ② 그다음 날(반등일 포함) 그 분야들은 어떻게 되나
  ③ '헤지 1종목' 고르는 규칙별 성적 — 아침에 알 수 있는 정보만 씀(미래 보기 금지):
       고정(앞 1년에 제일 잘 버틴 분야를 뒤 1년에 고정) / 어제 강한 비AI 분야 / 미국 짝꿍 ⭐ 비AI 분야 / 비AI 전체 바구니
     → 'AI 1 + 헤지 1' 50:50 vs 'AI만'을 국면 35% 미만 날에 비교(수익·최대 낙폭·급락일·반등일)
실행: venv/bin/python backtest/refuge_validate.py
"""
import bisect
import re
import json
import os
import sqlite3
import statistics as st
import sys

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in ("intelligence", "core"):
    sys.path.insert(0, os.path.join(_BASE, _d))
import kr_heatmap  # noqa: E402
import us_pairs  # noqa: E402

KR_DB = os.path.join(_BASE, "lina_bot", "kr_theme_finance.db")
AI = ["반도체_HBM패키징", "반도체_기판PCB", "반도체_전공정장비", "반도체_소재부품", "반도체_후공정테스트", "반도체_MLCC",
      "반도체_유리기판", "S7 대형주", "전력_변압기", "전력_전선케이블", "신재생_ESS", "통신",
      "로봇_휴머노이드", "로봇_피지컬AI자동화", "로봇_주관심"]
REST = ["조선", "조선_기자재엔진", "방산", "철강", "바이오_신약", "바이오_대형비만", "바이오_탈모", "화장품", "미용의료기기"]
THEMES = {"보안": ("보안", "사이버 보안"), "코인": ("가상화폐", "블록체인"), "금융": ("증권", "은행")}
EXTRA_PAIRS = {"보안": ["CRWD", "PANW"], "코인": ["COIN", "MSTR"], "금융": ["JPM", "GS"]}
CRASH = -3.0


def load_groups() -> dict:
    with open(kr_heatmap.GROUP_CACHE, encoding="utf-8") as f:
        raw = {k: [tuple(x) for x in v] for k, v in json.load(f)["groups"].items()}
    g = {k: [n for _, n in v] for k, v in kr_heatmap.with_s7(raw).items()}
    out = {k: g[k] for k in AI + REST if k in g}
    c = sqlite3.connect(f"file:{_th_db()}?mode=ro", uri=True)
    for name, themes in THEMES.items():
        q = ",".join("?" * len(themes))
        # 테마 DB 종목명은 '이름KOSDAQ 테마' 꼴 — 시장 표시부터 잘라냄
        out[name] = sorted({re.sub(r"(KOSPI|KOSDAQ|KONEX).*$", "", r[0]).strip() for r in
                            c.execute(f"SELECT DISTINCT stock_name FROM kr_theme_stocks WHERE theme_name IN ({q})", themes)})
    c.close()
    return out


def _th_db():
    import youtube_stock_monitor as y
    return y.THEME_DB


def daily_all():
    """{name: [(date, open, close)]}, 날짜 목록."""
    c = sqlite3.connect(f"file:{KR_DB}?mode=ro", uri=True)
    rows = {}
    for n, d, o, cl in c.execute("SELECT stock_name, date, open_price, close_price FROM kr_stock_daily_data ORDER BY date"):
        if o and cl:
            rows.setdefault(n, []).append((d, float(o), float(cl)))
    c.close()
    dates = sorted({d for v in rows.values() for d, _, _ in v})
    return rows, dates


def regime_series(rows, dates) -> dict:
    """{date: 20일선 위 종목 비율(%)} — 그날 종가 기준."""
    above, total = {}, {}
    for v in rows.values():
        cl = [x[2] for x in v]
        for i in range(20, len(v)):
            d = v[i][0]
            ma = sum(cl[i - 20:i]) / 20
            total[d] = total.get(d, 0) + 1
            above[d] = above.get(d, 0) + (cl[i] > ma)
    return {d: above.get(d, 0) / total[d] * 100 for d in total if total[d] >= 200}


def moves(rows) -> dict:
    out = {}
    for n, v in rows.items():
        m = {}
        for (d0, _, c0), (d1, o1, c1) in zip(v, v[1:]):
            if c0 and o1:
                m[d1] = ((o1 / c0 - 1) * 100, (c1 / c0 - 1) * 100, (c1 / o1 - 1) * 100)
        out[n] = m
    return out


def group_days(groups, mv, dates) -> dict:
    """{group: {date: (갭, 종가, 장중)}} — 종목 2개 이상 있는 날만."""
    out = {}
    for g, names in groups.items():
        gd = {}
        for d in dates:
            v = [mv[n][d] for n in names if n in mv and d in mv[n] and abs(mv[n][d][1]) < 31]
            if len(v) >= 2:
                gd[d] = tuple(st.mean(x[k] for x in v) for k in range(3))
        out[g] = gd
    return out


def us_signal(dates) -> dict:
    """{한국 날짜: {분야: 직전 미국 거래일 짝꿍 평균 등락}}."""
    pairs = {g: t for g, t in us_pairs.PAIRS.items()}
    pairs.update(EXTRA_PAIRS)
    tick = sorted({t for v in pairs.values() for t in v})
    um = {}
    for t in tick:
        r = us_pairs.daily(t, rng="2y")
        um[t] = {d1: (p1 / p0 - 1) * 100 for (_, p0), (d1, p1) in zip(r, r[1:]) if p0}
    ud = sorted({d for m in um.values() for d in m})
    out = {}
    for d in dates:
        i = bisect.bisect_left(ud, d) - 1
        if i < 0:
            continue
        u = ud[i]
        out[d] = {g: st.mean(um[t][u] for t in ts if u in um.get(t, {})) for g, ts in pairs.items()
                  if any(u in um.get(t, {}) for t in ts)}
    return out


def mdd(rets) -> float:
    eq, peak, worst = 1.0, 1.0, 0.0
    for r in rets:
        eq *= 1 + r / 100
        peak = max(peak, eq)
        worst = min(worst, eq / peak - 1)
    return worst * 100


def main():
    groups = load_groups()
    rows, dates = daily_all()
    print(f"일봉 {len(rows)}종목 · {dates[0]}~{dates[-1]} ({len(dates)}일)")
    print("분야 종목 수:", {g: len(v) for g, v in groups.items()})
    mv = moves(rows)
    reg = regime_series(rows, dates)
    gd = group_days(groups, mv, dates)
    rest = [g for g in groups if g not in AI]
    ai_day = {d: st.mean(gd[g][d][1] for g in AI if d in gd.get(g, {})) for d in dates
              if sum(1 for g in AI if d in gd.get(g, {})) >= 8}
    ai_gap = {d: st.mean(gd[g][d][0] for g in AI if d in gd.get(g, {})) for d in ai_day}
    days = sorted(ai_day)
    prev = {d: days[i - 1] for i, d in enumerate(days) if i}
    half = days[len(days) // 2]

    # ① AI 급락일 — 비AI 분야 성적(같은 날 종가·갭, AI 대비)
    crash = [d for d in days if ai_day[d] <= CRASH]
    print(f"\n=== ① AI 그룹 -3%↓ 급락일 {len(crash)}일 (평균 AI {st.mean(ai_day[d] for d in crash):+.2f}%) ===")
    print(f"{'분야':14s} {'n':>3s} {'종가':>7s} {'AI대비':>7s} {'갭':>6s} {'장중':>6s} {'플러스%':>6s} {'비AI 1등 횟수':>8s}")
    top_cnt = {g: 0 for g in rest}
    for d in crash:
        cand = [(gd[g][d][1], g) for g in rest if d in gd[g]]
        if cand:
            top_cnt[max(cand)[1]] += 1
    stat = []
    for g in rest:
        v = [gd[g][d] for d in crash if d in gd[g]]
        if not v:
            continue
        cl = st.mean(x[1] for x in v)
        stat.append((cl, g, len(v), st.mean(x[0] for x in v), st.mean(x[2] for x in v), sum(x[1] > 0 for x in v) / len(v) * 100))
    for cl, g, n, gp, intra, pos in sorted(stat, reverse=True):
        print(f"{g:14s} {n:3d} {cl:+7.2f} {cl - st.mean(ai_day[d] for d in crash):+7.2f} {gp:+6.2f} {intra:+6.2f} {pos:6.0f} {top_cnt[g]:8d}")

    # ② 급락 다음 날
    nxt = [days[days.index(d) + 1] for d in crash if days.index(d) + 1 < len(days)]
    print(f"\n=== ② 급락 다음 날 {len(nxt)}일 (AI 평균 {st.mean(ai_day[d] for d in nxt):+.2f}%) ===")
    for cl, g in sorted(((st.mean(gd[g][d][1] for d in nxt if d in gd[g]), g) for g in rest if any(d in gd[g] for d in nxt)), reverse=True):
        print(f"   {g:14s} {cl:+6.2f}")

    # ③ 헤지 고르기 규칙 — 국면 35% 미만(전날 종가 국면) 날
    us = us_signal(days)
    first = [d for d in crash if d < half]
    fixed = max(rest, key=lambda g: st.mean([gd[g][d][1] for d in first if d in gd[g]] or [-99]))
    print(f"\n(고정 규칙: 앞 1년 급락일 성적 1등 = {fixed}, 뒤 1년에 고정)")

    def pick(rule, d):
        p = prev.get(d)
        cands = [g for g in rest if d in gd[g]]
        if not cands or not p:
            return None
        if rule == "고정":
            return fixed if fixed in cands else None
        if rule == "어제강한":
            c = [(gd[g][p][1], g) for g in cands if p in gd[g]]
            return max(c)[1] if c else None
        if rule == "미국⭐":
            s = us.get(d) or {}
            c = [(s[g], g) for g in cands if g in s]
            return max(c)[1] if c else None
        if rule == "어제약한":
            c = [(gd[g][p][1], g) for g in cands if p in gd[g]]
            return min(c)[1] if c else None
        return None

    def hedge_ret(rule, d):
        if rule == "비AI바구니":
            v = [gd[g][d][1] for g in rest if d in gd[g]]
            return st.mean(v) if v else None
        g = pick(rule, d)
        return gd[g][d][1] if g else None

    rules = ["고정", "어제강한", "어제약한", "미국⭐", "비AI바구니"]
    for label, sel in (("국면 35% 미만(전날 기준)", lambda d: prev.get(d) in reg and reg[prev[d]] < 35),
                       ("국면 35% 이상", lambda d: prev.get(d) in reg and reg[prev[d]] >= 35)):
        for tag, rng in (("전체", lambda d: True), ("뒤 1년(고정 규칙 공정 비교)", lambda d: d >= half)):
            ds = [d for d in days if sel(d) and rng(d)]
            if not ds:
                continue
            ai_r = [ai_day[d] for d in ds]
            print(f"\n=== ③ {label} · {tag} {len(ds)}일 — 'AI만' vs 'AI 50 + 헤지 50' (고정 분야={fixed}) ===")
            print(f"{'규칙':10s} {'일평균':>7s} {'누적':>8s} {'최대낙폭':>8s} {'AI급락일':>8s} {'AI급등일':>8s} {'헤지 분야 바뀐 횟수':>10s}")
            def show(name, rets, cdays, udays, changes=""):
                eq = 1.0
                for r in rets:
                    eq *= 1 + r / 100
                print(f"{name:10s} {st.mean(rets):+7.3f} {(eq - 1) * 100:+8.1f} {mdd(rets):+8.1f} "
                      f"{st.mean(cdays) if cdays else 0:+8.2f} {st.mean(udays) if udays else 0:+8.2f} {changes:>10}")
            up = [d for d in ds if ai_day[d] >= 3]
            cr = [d for d in ds if ai_day[d] <= CRASH]
            show("AI만", ai_r, [ai_day[d] for d in cr], [ai_day[d] for d in up])
            for rule in rules:
                rets, cds, uds, picks = [], [], [], []
                for d in ds:
                    h = hedge_ret(rule, d)
                    r = ai_day[d] if h is None else (ai_day[d] + h) / 2
                    rets.append(r)
                    if d in cr:
                        cds.append(r)
                    if d in up:
                        uds.append(r)
                    picks.append(pick(rule, d) if rule != "비AI바구니" else "바구니")
                ch = sum(1 for a, b in zip(picks, picks[1:]) if a != b)
                show(rule, rets, cds, uds, str(ch))


if __name__ == "__main__":
    main()
