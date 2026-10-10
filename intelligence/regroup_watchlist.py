"""
regroup_watchlist.py — 관심그룹 재편 도우미: 주가가 같이 움직이는 종목끼리 묶기 (2026-10-10)
================================================================
[이 파일이 하는 일 — 비개발자용 설명]

대장: "관심그룹 재편하려니 너무 많고 얽혀 있다 — 주가로 분석하면 안 될까?"
한투 관심그룹 전 종목의 최근 일봉 수익률로 "같이 오르내리는 정도(상관계수)"를 재고,
비슷하게 움직이는 종목끼리 분야별 최대 12개 안팎으로 다시 묶어 제안한다.
  · 묶음 이름: 묶음 안 종목들이 가장 많이 속한 테마(kr_theme_stocks) + 지금 관심그룹 이름
  · 종목마다: 지금 속한 관심그룹, 최근 20일 수익률, 거래대금 순위(묶음 안 대장 후보)
  · 정리 대상: 여러 그룹에 중복된 종목 / 지금 그룹과 따로 노는 종목 / 일봉 없는 종목
제안일 뿐 HTS 관심그룹은 대장이 직접 고친다(같은 결과를 CSV로도 저장).
실행:  python intelligence/regroup_watchlist.py [--days 120] [--max 12] [--min-corr 0.35]
       python intelligence/regroup_watchlist.py --group 반도체   (그 그룹만 잘게, 기본 8개씩)
       관심그룹은 backtest/data/watch_groups.json(스윙 백테스트가 저장)을 쓰고, 없거나
       --refresh면 한투에서 다시 읽는다.
"""
import os
import re
import sys
import csv
import json
import math
import sqlite3

import three_month_leader as tml

GROUPS_JSON = os.path.join(tml._BASE, "backtest", "data", "watch_groups.json")
OUT_CSV = os.path.join(tml._BASE, "intelligence", "regroup_suggestion.csv")
DAYS = 120          # 상관 계산 기간(거래일)
MIN_DAYS = 60       # 이보다 일봉이 적으면 계산에서 뺌(신규 상장 등)
MAX_SIZE = 12       # 묶음 최대 종목 수 — 대장 "분야별 10개 정도"
MIN_CORR = 0.35     # 묶음 사이 평균 상관이 이보다 낮으면 더 안 합침
NEW_GROUP_NAMES = ("new", "신규추천", "신규", "new추천")


# ── 계산 (순수 함수) ───────────────────────────────────────
def returns(closes: list) -> dict:
    """[(date, close)] → {date: 일간 수익률}."""
    out = {}
    for (d0, c0), (d1, c1) in zip(closes, closes[1:]):
        if c0 and c1:
            out[d1] = c1 / c0 - 1
    return out


def corr(a: dict, b: dict, min_n: int = 30):
    """두 수익률 시계열의 상관계수(겹치는 날만). 겹치는 날이 적으면 None."""
    ds = [d for d in a if d in b]
    n = len(ds)
    if n < min_n:
        return None
    xa = [a[d] for d in ds]
    xb = [b[d] for d in ds]
    ma, mb = sum(xa) / n, sum(xb) / n
    va = sum((x - ma) ** 2 for x in xa)
    vb = sum((x - mb) ** 2 for x in xb)
    if va <= 0 or vb <= 0:
        return None
    return sum((x - ma) * (y - mb) for x, y in zip(xa, xb)) / math.sqrt(va * vb)


def cluster(codes: list, sim: dict, max_size: int = MAX_SIZE, min_corr: float = MIN_CORR) -> list:
    """평균연결 군집: 평균 상관이 가장 높은 두 묶음부터 합침(합친 크기 ≤ max_size, 평균 ≥ min_corr).
    sim[(a, b)] (a < b) = 상관. → [[코드…]] 큰 묶음부터."""
    groups = {i: [c] for i, c in enumerate(codes)}

    def s(a, b):
        return sim.get((a, b) if a < b else (b, a))
    # 묶음 쌍 평균 상관 (합계, 개수)
    link = {}
    ids = list(groups)
    for x in range(len(ids)):
        for y in range(x + 1, len(ids)):
            v = s(codes[ids[x]], codes[ids[y]])
            if v is not None:
                link[(ids[x], ids[y])] = (v, 1)
    while True:
        best, pair = None, None
        for (i, j), (tot, n) in link.items():
            if len(groups[i]) + len(groups[j]) > max_size:
                continue
            avg = tot / n
            if avg >= min_corr and (best is None or avg > best):
                best, pair = avg, (i, j)
        if pair is None:
            break
        i, j = pair
        groups[i] += groups.pop(j)
        new = {}
        for (a, b), (tot, n) in link.items():
            if j in (a, b) or i in (a, b):
                continue
            new[(a, b)] = (tot, n)
        for k in groups:
            if k == i:
                continue
            tot = n = 0
            for a in groups[i]:
                for b in groups[k]:
                    v = s(a, b)
                    if v is not None:
                        tot += v; n += 1
            if n:
                new[(min(i, k), max(i, k))] = (tot, n)
        link = new
    return sorted(groups.values(), key=len, reverse=True)


def theme_assign(codes: list, themes: dict, sim: dict, min_members: int = 3, min_fit: float = 0.2) -> tuple:
    """주달 테마 기준 재편(2026-10-10 대장: "주달 테마로 정리"). 한 종목이 여러 테마에 걸린 게
    얽힘의 원인 → 종목마다 자기 테마들 중 그 테마의 다른 관심종목들과 평균 상관(fit)이
    가장 높은 테마 하나에 배정. 관심종목이 min_members개 미만인 테마는 후보에서 뺌.
    → ({테마: [(code, fit)]}, [어느 테마와도 fit < min_fit인 코드])"""
    members = {}
    for c in codes:
        for t in themes.get(c, []):
            members.setdefault(t, []).append(c)
    members = {t: cs for t, cs in members.items() if len(cs) >= min_members}

    def fit(c, t):
        vs = [sim.get((c, o) if c < o else (o, c)) for o in members[t] if o != c]
        vs = [v for v in vs if v is not None]
        return sum(vs) / len(vs) if vs else None
    out, loose = {}, []
    for c in codes:
        cand = [(f, t) for t in themes.get(c, []) if t in members for f in [fit(c, t)] if f is not None]
        if not cand or max(cand)[0] < min_fit:
            loose.append(c)
            continue
        f, t = max(cand)
        out.setdefault(t, []).append((c, f))
    return out, loose


BOTH_MARGIN = 0.10     # 두 그룹 적합도 차이가 이 안이면 "둘 다 유지"
MOVE_MARGIN = 0.15     # 다른 그룹이 지금 그룹보다 이만큼 더 맞으면 "옮길 후보"


def group_fit(groups: dict, codes: list, sim: dict) -> dict:
    """{code: {그룹: 그 그룹 다른 종목들과 평균 상관}} — NEW 그룹 제외, 비교할 종목 2개 이상만.
    ★ 2026-10-10 대장: 반도체·방산·우주에 같이 걸린 종목은 사람이 분류하기 어려움 → 지금
    어느 그룹과 실제로 같이 움직이는지 숫자로."""
    have = set(codes)
    mem = {g: [c for c, _ in v if c in have] for g, v in groups.items()
           if g.strip().lower() not in NEW_GROUP_NAMES}
    out = {}
    for c in codes:
        fits = {}
        for g, ms in mem.items():
            vs = [sim.get((c, o) if c < o else (o, c)) for o in ms if o != c]
            vs = [v for v in vs if v is not None]
            if len(vs) >= 2:
                fits[g] = sum(vs) / len(vs)
        out[c] = fits
    return out


def placement(fits: dict, current: list) -> tuple:
    """(판정, 주 그룹) — 판정: "둘 다 유지" / "주 그룹만" / "옮길 후보" / ""."""
    cur = [g for g in current if g in fits]
    if not fits:
        return "", None
    best = max(fits, key=fits.get)
    ranked = sorted(fits.values(), reverse=True)
    if len(cur) >= 2:
        top2 = sorted(cur, key=fits.get, reverse=True)[:2]
        if fits[top2[0]] - fits[top2[1]] <= BOTH_MARGIN:
            return "둘 다 유지", top2[0]
        return "주 그룹만", top2[0]
    if cur and best not in cur and fits[best] - fits[cur[0]] >= MOVE_MARGIN:
        return "옮길 후보", best
    return "", (cur[0] if cur else best) if ranked else None


def avg_corr(members: list, sim: dict):
    vs = [sim.get((a, b) if a < b else (b, a)) for x, a in enumerate(members) for b in members[x + 1:]]
    vs = [v for v in vs if v is not None]
    return sum(vs) / len(vs) if vs else None


def theme_sizes(themes: dict) -> dict:
    """{테마: 전체 종목 수} — 작을수록 구체적인 테마(예: 반도체 > HBM)."""
    out = {}
    for ts in themes.values():
        for t in ts:
            out[t] = out.get(t, 0) + 1
    return out


def name_cluster(members: list, themes: dict, cur_groups: dict, sizes: dict = None) -> str:
    """묶음 이름: 묶음 절반 이상(최소 2개)이 속한 주달 테마 중 가장 구체적인 것(전체 종목 수가
    가장 적은 것) + 가장 흔한 지금 관심그룹. ★ 2026-10-10 대장: 한투는 반도체를 뭉뚱그려
    구분이 안 됨 → 가장 흔한 테마 대신 가장 구체적인 테마를 이름으로."""
    tc, gc = {}, {}
    for c in members:
        for t in themes.get(c, []):
            tc[t] = tc.get(t, 0) + 1
        for g in cur_groups.get(c, []):
            if g.strip().lower() not in NEW_GROUP_NAMES:
                gc[g] = gc.get(g, 0) + 1
    parts = []
    need = max(2, (len(members) + 1) // 2)
    cover = [t for t, n in tc.items() if n >= need]
    if cover:
        sizes = sizes or {}
        parts.append(min(cover, key=lambda t: (sizes.get(t, 10**6), -tc[t])))
    if gc:
        parts.append("지금 " + max(gc.items(), key=lambda x: x[1])[0])
    return " · ".join(parts) or "이름 없음"


# ── 데이터 ────────────────────────────────────────────────
def load_groups(refresh: bool) -> dict:
    if not refresh and os.path.exists(GROUPS_JSON):
        with open(GROUPS_JSON, encoding="utf-8") as f:
            return {g: [tuple(x) for x in v] for g, v in json.load(f).items()}
    from dotenv import load_dotenv
    for env in (os.path.join(tml._BASE, ".env"), os.path.join(tml._BASE, "lina_bot", ".env")):
        load_dotenv(env)
    sys.path.insert(0, os.path.join(tml._BASE, "core"))
    from kis_api import KisAPI
    import sector_watch as sw
    groups = sw.load_groups(KisAPI(), sw.hts_id())
    os.makedirs(os.path.dirname(GROUPS_JSON), exist_ok=True)
    with open(GROUPS_JSON, "w", encoding="utf-8") as f:
        json.dump(groups, f, ensure_ascii=False)
    return groups


def load_prices(names: dict, days: int, db: str = tml.THEME_DB) -> dict:
    """{code: [(date, close, value)]} 최근 days거래일 — 일봉 DB는 종목명 기준이라 이름으로 찾음."""
    conn = sqlite3.connect(db)
    try:
        ds = [d for (d,) in conn.execute("SELECT DISTINCT date FROM kr_stock_daily_data ORDER BY date DESC LIMIT ?",
                                         (days + 1,))]
        if not ds:
            return {}
        by_name = {}
        for nm, d, c, v, tv in conn.execute(
                "SELECT stock_name, date, close_price, volume, trade_value FROM kr_stock_daily_data "
                "WHERE date >= ? ORDER BY date", (min(ds),)):
            by_name.setdefault(nm, []).append((d, c, tv or (c or 0) * (v or 0)))
    finally:
        conn.close()
    return {code: by_name[nm] for code, nm in names.items() if nm in by_name}


def load_themes(db: str = tml.THEME_DB) -> dict:
    """{code: [테마…]}"""
    out = {}
    conn = sqlite3.connect(db)
    try:
        for raw, theme in conn.execute("SELECT stock_name, theme_name FROM kr_theme_stocks"):
            m = re.search(r"([0-9A-Z]{6})$", (raw or "").strip())
            if m and theme:
                out.setdefault(m.group(1), []).append(theme)
    except sqlite3.OperationalError:
        pass
    finally:
        conn.close()
    return out


# ── 실행 ──────────────────────────────────────────────────
def analyze(groups: dict, prices: dict, themes: dict, max_size: int = MAX_SIZE, min_corr: float = MIN_CORR):
    cur = {}
    names = {}
    for g, stocks in groups.items():
        for c, n in stocks:
            cur.setdefault(c, []).append(g)
            names[c] = n
    rets = {c: returns([(d, cl) for d, cl, _ in rows]) for c, rows in prices.items()
            if len(rows) >= MIN_DAYS}
    codes = sorted(rets)
    sim = {}
    for x, a in enumerate(codes):
        for b in codes[x + 1:]:
            v = corr(rets[a], rets[b])
            if v is not None:
                sim[(a, b)] = v
    clusters = cluster(codes, sim, max_size, min_corr)
    sizes = theme_sizes(themes)
    info = {}
    for c in codes:
        rows = prices[c]
        r20 = (rows[-1][1] / rows[-21][1] - 1) * 100 if len(rows) > 21 and rows[-21][1] else None
        val20 = sum(v for _, _, v in rows[-20:]) / min(20, len(rows))
        info[c] = {"r20": r20, "val20": val20}
    result = []
    for m in clusters:
        m = sorted(m, key=lambda c: -info[c]["val20"])
        result.append({"name": name_cluster(m, themes, cur, sizes), "members": m, "avg": avg_corr(m, sim)})
    # 지금 그룹과 따로 노는 종목: 같은 그룹 다른 종목들과 평균 상관이 낮음
    loners = []
    for g, stocks in groups.items():
        if g.strip().lower() in NEW_GROUP_NAMES:
            continue
        cs = [c for c, _ in stocks if c in rets]
        for c in cs:
            vs = [sim.get((c, o) if c < o else (o, c)) for o in cs if o != c]
            vs = [v for v in vs if v is not None]
            if len(vs) >= 3 and sum(vs) / len(vs) < 0.15:
                loners.append((g, c, sum(vs) / len(vs)))
    dups = {c: gs for c, gs in cur.items()
            if len([g for g in gs if g.strip().lower() not in NEW_GROUP_NAMES]) >= 2}
    missing = [c for c in cur if c not in rets]
    by_theme, loose = theme_assign(codes, themes, sim)
    fits = group_fit(groups, codes, sim)
    return {"fits": fits, "clusters": result, "info": info, "names": names, "cur": cur, "loners": loners,
            "dups": dups, "missing": missing, "n": len(codes), "by_theme": by_theme, "loose": loose,
            "no_theme": [c for c in codes if not themes.get(c)]}


def report(res: dict) -> str:
    nm, info, cur = res["names"], res["info"], res["cur"]
    big = [c for c in res["clusters"] if len(c["members"]) >= 2]
    solo = [c["members"][0] for c in res["clusters"] if len(c["members"]) == 1]
    L = [f"🧩 관심그룹 재편 제안 — 종목 {res['n']}개, 주가 같이 움직이는 묶음 {len(big)}개 "
         f"(최대 {MAX_SIZE}개씩, 평균 상관 {MIN_CORR} 이상만 합침)"]
    for i, c in enumerate(big, 1):
        avg = f"{c['avg']:.2f}" if c["avg"] is not None else "-"
        L.append(f"\n■ 묶음 {i} — {c['name']}  ({len(c['members'])}종목, 평균 상관 {avg})")
        for k, code in enumerate(c["members"]):
            r = info[code]["r20"]
            gs = "/".join(cur.get(code, []))
            lead = "👑" if k == 0 else "  "
            L.append(f"   {lead}{nm.get(code, code)}({code}) 20일 "
                     + (f"{r:+.1f}%" if r is not None else "-") + f" · 지금: {gs}")
    if res.get("by_theme"):
        L.append(f"\n■■ 주달 테마 기준 재편안 — 종목마다 가장 같이 움직이는 주달 테마 하나에 배정 "
                 f"({len(res['by_theme'])}개 테마)")
        for t, ms in sorted(res["by_theme"].items(), key=lambda x: -len(x[1])):
            ms = sorted(ms, key=lambda x: -info[x[0]]["val20"])
            coh = sum(f for _, f in ms) / len(ms)
            L.append(f"   [{t}] {len(ms)}종목 · 결속도 {coh:.2f}")
            L.append("      " + ", ".join(f"{'👑' if k == 0 else ''}{nm.get(c, c)}({f:.2f})"
                                          for k, (c, f) in enumerate(ms)))
        if res.get("loose"):
            L.append(f"   어느 주달 테마와도 같이 안 움직임 {len(res['loose'])}개: "
                     + ", ".join(nm.get(c, c) for c in res["loose"][:40]))
        if res.get("no_theme"):
            L.append(f"   주달 테마 정보 없음 {len(res['no_theme'])}개(신규·수집 전): "
                     + ", ".join(nm.get(c, c) for c in res["no_theme"][:40]))
        L.append("   ※ 괄호 숫자 = 그 테마 다른 관심종목들과 평균 상관(높을수록 같이 움직임)")
    if solo:
        L.append(f"\n■ 혼자 움직이는 종목 {len(solo)}개 (누구와도 상관 {MIN_CORR} 미만 — 개별 재료주이거나 정리 후보)")
        L.append("   " + ", ".join(nm.get(c, c) for c in solo))
    fits = res.get("fits", {})
    if res["dups"]:
        L.append(f"\n■ 여러 그룹에 걸친 종목 {len(res['dups'])}개 — 그룹별 같이 움직이는 정도(평균 상관)")
        for c, gs in sorted(res["dups"].items(), key=lambda x: -len(x[1])):
            f = fits.get(c, {})
            verdict, main = placement(f, gs)
            parts = " / ".join(f"{g} {f[g]:.2f}" if g in f else f"{g} -" for g in gs
                               if g.strip().lower() not in NEW_GROUP_NAMES)
            tail = {"둘 다 유지": "→ 비슷하게 같이 움직임, 둘 다 유지",
                    "주 그룹만": f"→ 주 그룹 {main}"}.get(verdict, "")
            L.append(f"   {nm.get(c, c)}: {parts} {tail}")
    moves = []
    for c, f in fits.items():
        verdict, best = placement(f, [g for g in cur.get(c, []) if g.strip().lower() not in NEW_GROUP_NAMES])
        if verdict == "옮길 후보":
            now = [g for g in cur.get(c, []) if g in f]
            moves.append((nm.get(c, c), now[0], f[now[0]], best, f[best]))
    if moves:
        L.append(f"\n■ 다른 그룹과 더 같이 움직이는 종목 {len(moves)}개 (차이 {MOVE_MARGIN} 이상 — 옮기거나 추가 후보)")
        for n_, g0, f0, g1, f1 in sorted(moves, key=lambda x: x[4] - x[2], reverse=True):
            L.append(f"   {n_}: 지금 {g0} {f0:.2f} → {g1} {f1:.2f}")
    if res["loners"]:
        L.append(f"\n■ 지금 그룹과 따로 노는 종목 {len(res['loners'])}개 (같은 그룹 종목들과 평균 상관 0.15 미만)")
        for g, c, v in sorted(res["loners"]):
            L.append(f"   [{g}] {nm.get(c, c)} 평균 {v:+.2f}")
    if res["missing"]:
        L.append(f"\n■ 일봉 부족·없음 {len(res['missing'])}개 (최근 상장·이름 불일치 — 계산 제외)")
        L.append("   " + ", ".join(nm.get(c, c) for c in res["missing"]))
    L.append("\n※ 👑 = 묶음 안 최근 20일 거래대금 1위(대장 후보). 상관은 최근 일봉 수익률 기준이라 "
             "같은 테마라도 따로 놀면 다른 묶음으로 갈 수 있음.")
    return "\n".join(L)


def save_csv(res: dict, path: str = OUT_CSV) -> None:
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["묶음", "묶음이름", "순서", "종목코드", "종목명", "20일수익률", "지금관심그룹"])
        for i, c in enumerate(res["clusters"], 1):
            label = i if len(c["members"]) >= 2 else "혼자"
            for k, code in enumerate(c["members"], 1):
                r = res["info"][code]["r20"]
                w.writerow([label, c["name"], k, code, res["names"].get(code, code),
                            f"{r:.1f}" if r is not None else "", "/".join(res["cur"].get(code, []))])


def main():
    global MAX_SIZE, MIN_CORR
    a = sys.argv[1:]
    days = int(a[a.index("--days") + 1]) if "--days" in a else DAYS
    MAX_SIZE = int(a[a.index("--max") + 1]) if "--max" in a else MAX_SIZE
    MIN_CORR = float(a[a.index("--min-corr") + 1]) if "--min-corr" in a else MIN_CORR
    groups = load_groups("--refresh" in a)
    if "--group" in a:                    # 큰 그룹 하나만 세분화(예: --group 반도체)
        kw = a[a.index("--group") + 1]
        groups = {g: v for g, v in groups.items() if kw in g}
        if not groups:
            print(f"'{kw}'가 들어간 관심그룹이 없어")
            return
        if "--max" not in a:
            MAX_SIZE = 8                      # 세분화는 더 잘게
    names = {c: n for stocks in groups.values() for c, n in stocks}
    print(f"관심그룹 {len(groups)}개 · 종목 {len(names)}개 — 일봉 {days}일로 상관 계산 중…")
    res = analyze(groups, load_prices(names, days), load_themes(), MAX_SIZE, MIN_CORR)
    print(report(res))
    save_csv(res)
    print(f"\n💾 CSV 저장: {OUT_CSV} (엑셀로 열어 정리용)")


if __name__ == "__main__":
    main()
