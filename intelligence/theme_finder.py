"""
theme_finder.py — 주달 테마로 새 관심그룹 종목 찾기 (2026-10-10)
================================================================
[이 파일이 하는 일 — 비개발자용 설명]

대장: "현대차 로봇부품, 바이오_탈모 그룹을 더 찾아줘." 리나 DB의 주달 테마(kr_theme_stocks)에서
키워드가 들어간 테마의 종목을 모아, 고르기 쉽게 보여준다.
  · 어떤 테마에서 걸렸는지, 지금 어느 관심그룹에 있는지
  · 최근 20일 수익률, 20일 평균 거래대금(큰 순 정렬 — 대장 후보가 위로)
  · 그 종목들끼리 같이 움직이는 정도(평균 상관) — 낮으면 이름만 같은 테마
  · 마지막 줄에 HTS 입력용 종목코드
실행:  python intelligence/theme_finder.py 탈모
       python intelligence/theme_finder.py 로봇 --and 현대     (로봇 테마이면서 현대 테마에도 있는 종목)
       python intelligence/theme_finder.py --list 로봇          (키워드 들어간 테마 이름만)
       python intelligence/theme_finder.py 로봇 --ref 현대무벡스 현대위아
           (로봇 테마 종목을 기준 종목들과 같이 움직이는 순으로 — 테마 교집합이 없을 때.
            주달 '현대자동차그룹'은 계열사뿐이라 로봇 부품 협력사는 주가로 찾아야 함, 2026-10-10)
"""
import os
import re
import sys
import json
import sqlite3

import three_month_leader as tml
import regroup_watchlist as rw

DAYS = 60


def load_theme_rows(db: str = tml.THEME_DB) -> list:
    """[(code, name, theme)] — kr_theme_stocks의 '종목명KOSPI 005930' 형식을 나눔."""
    out = []
    conn = sqlite3.connect(db)
    try:
        for raw, theme in conn.execute("SELECT stock_name, theme_name FROM kr_theme_stocks"):
            raw = (raw or "").strip()
            m = re.search(r"([0-9A-Z]{6})$", raw)
            if m and theme:
                name = re.sub(r"\s*KOS(?:PI|DAQ)\s*[0-9A-Z]{6}$", "", raw).strip()
                out.append((m.group(1), name, theme))
    finally:
        conn.close()
    return out


def find(rows: list, keywords: list, and_keywords: list = None) -> dict:
    """키워드가 이름에 든 테마의 종목 {code: {"name", "themes": [...]}}. and_keywords가 있으면
    그 키워드 테마에도 속한 종목만."""
    def hit(theme, kws):
        return any(k.lower() in theme.lower() for k in kws)
    by_code = {}
    for code, name, theme in rows:
        by_code.setdefault(code, {"name": name, "themes": set()})["themes"].add(theme)
    out = {}
    for code, v in by_code.items():
        main = sorted(t for t in v["themes"] if hit(t, keywords))
        if not main:
            continue
        if and_keywords and not any(hit(t, and_keywords) for t in v["themes"]):
            continue
        out[code] = {"name": v["name"], "themes": main,
                     "also": sorted(t for t in v["themes"] if and_keywords and hit(t, and_keywords))}
    return out


def current_groups() -> dict:
    """{code: [관심그룹]} — 재편 도구가 저장한 관심그룹(없으면 빈 값)."""
    if not os.path.exists(rw.GROUPS_JSON):
        return {}
    with open(rw.GROUPS_JSON, encoding="utf-8") as f:
        groups = json.load(f)
    out = {}
    for g, stocks in groups.items():
        for c, _n in stocks:
            out.setdefault(c, []).append(g)
    return out


def report(found: dict, prices: dict, cur: dict, title: str, ref: dict = None) -> str:
    """ref: {기준 종목 코드: 수익률} — 있으면 기준과 같이 움직이는 순으로 정렬."""
    info = {}
    rets = {}
    for c in found:
        rows = prices.get(c) or []
        if len(rows) >= 21:
            info[c] = {"r20": (rows[-1][1] / rows[-21][1] - 1) * 100 if rows[-21][1] else None,
                       "val": sum(v for _, _, v in rows[-20:]) / 20}
            rets[c] = rw.returns([(d, cl) for d, cl, _ in rows])
    refc = {}
    for c in found:
        if ref and c in rets:
            vs = [rw.corr(rets[c], r) for rc, r in ref.items() if rc != c]
            vs = [v for v in vs if v is not None]
            refc[c] = sum(vs) / len(vs) if vs else None
    if ref:
        codes = sorted(found, key=lambda c: -(refc.get(c) if refc.get(c) is not None else -9))
    else:
        codes = sorted(found, key=lambda c: -(info.get(c, {}).get("val") or 0))
    have = [c for c in codes if c in rets]
    fit = {}
    for c in have:
        vs = [rw.corr(rets[c], rets[o]) for o in have if o != c]
        vs = [v for v in vs if v is not None]
        fit[c] = sum(vs) / len(vs) if vs else None
    allf = [v for v in fit.values() if v is not None]
    L = [f"🔎 {title} — {len(found)}종목 ({'기준 종목과 같이 움직이는 순' if ref else '20일 거래대금 큰 순'})"
         + (f" · 서로 같이 움직이는 정도 평균 {sum(allf) / len(allf):.2f}" if allf else "")]
    for c in codes:
        i = info.get(c, {})
        r = f"{i['r20']:+.1f}%" if i.get("r20") is not None else "  -  "
        val = f"{i['val'] / 1e8:,.0f}억" if i.get("val") else "-"
        f = f"{fit[c]:.2f}" if fit.get(c) is not None else " - "
        g = "/".join(cur.get(c, [])) or "-"
        rf = (f" · 기준과 {refc[c]:.2f}" if refc.get(c) is not None else " · 기준과  - ") if ref else ""
        L.append(f"   {found[c]['name']:<14}({c}) 20일 {r:>7} · 대금 {val:>6} · 같이 {f}{rf} · 지금 {g}"
                 f" · 테마: {', '.join(found[c]['themes'][:2])}")
    L.append("   ※ '같이' 0.3 미만은 테마 이름만 같고 따로 움직이는 종목 — 그룹에서 빼는 게 나음")
    if ref:
        L.append("   ※ '기준과' = 기준 종목들과 평균 상관. 0.4 이상이면 같이 움직이는 편")
    L.append(f"\n■ HTS 입력용({'기준과 같이 움직이는 순' if ref else '거래대금 큰 순'}): " + " ".join(codes))
    return "\n".join(L)


def main():
    a = sys.argv[1:]
    if not a:
        print(__doc__)
        return
    rows = load_theme_rows()
    if a[0] == "--list":
        kws = a[1:]
        themes = sorted({t for _, _, t in rows if any(k.lower() in t.lower() for k in kws)})
        print(f"'{' '.join(kws)}' 들어간 주달 테마 {len(themes)}개:")
        for t in themes:
            print(f"   {t} ({sum(1 for _, _, x in rows if x == t)}종목)")
        return
    ref_names = []
    if "--ref" in a:
        k = a.index("--ref")
        ref_names, a = a[k + 1:], a[:k]
    and_kw = []
    if "--and" in a:
        k = a.index("--and")
        and_kw, a = a[k + 1:], a[:k]
    found = find(rows, a, and_kw)
    if not found:
        if and_kw and find(rows, a):
            print(f"'{' '.join(a)}' 테마 종목은 있는데, 그중 '{' '.join(and_kw)}' 테마에도 든 종목은 없어 — "
                  f"주가로 찾으려면: {' '.join(a)} --ref <기준 종목명들>")
        else:
            print(f"'{' '.join(a)}' 테마 종목이 없어 — --list {a[0]} 로 테마 이름부터 확인해줘")
        return
    names = {c: v["name"] for c, v in found.items()}
    ref = None
    if ref_names:
        conn = sqlite3.connect(tml.THEME_DB)
        try:
            nc = tml._name_code_map(conn)
        finally:
            conn.close()
        ref_codes = {nc[n]: n for n in ref_names if n in nc}
        missing = [n for n in ref_names if n not in nc]
        if missing:
            print(f"⚠️ 기준 종목 이름을 못 찾음: {', '.join(missing)}")
        names.update(ref_codes)
    prices = rw.load_prices(names, DAYS)
    if ref_names:
        ref = {c: rw.returns([(d, cl) for d, cl, _ in prices[c]]) for c in ref_codes if c in prices}
    title = (f"'{' '.join(a)}' 테마" + (f" ∩ '{' '.join(and_kw)}' 테마" if and_kw else "")
             + (f" · 기준: {', '.join(ref_names)}" if ref_names else ""))
    print(report(found, prices, current_groups(), title, ref))


if __name__ == "__main__":
    main()
