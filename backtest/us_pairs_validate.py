"""
us_pairs_validate.py — 미국 짝꿍 밤 등락 → 다음 날 우리 분야(관심그룹)가 정말 그쪽으로 움직였나 (2026-11-25, 대장)
================================================================
[이 파일이 하는 일 — 비개발자용 설명]
intelligence/us_pairs.py의 ⭐(짝꿍 평균 +2%↑)/🔻(-2%↓)가 쓸모 있는지 2년치로 확인.
  · 우리 분야 등락: 지금 관심그룹 구성(섹터맵과 같음, S7 묶음 포함)으로 과거 일봉(kr_theme_finance.db kr_stock_daily_data)을
    분야별 단순 평균 — 시가 갭(전일 종가→시가), 종가(전일 종가→종가), 장중(시가→종가)
  · 시장 대비: 같은 날 관심그룹 전체 종목 평균을 빼서 '그 분야만의 힘'을 봄(미국이 다 빠진 날 다 빠지는 건 당연하니까)
  · 미국 날짜 d의 등락은 그다음 한국 거래일에 붙임(미국장은 한국 아침 6시에 끝남)
한계: 관심그룹 구성이 '지금' 기준(과거엔 없던 종목도 들어감) · 단순 평균 · 2년.
실행: venv/bin/python backtest/us_pairs_validate.py
"""
import bisect
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


def load_groups() -> dict:
    with open(kr_heatmap.GROUP_CACHE, encoding="utf-8") as f:
        g = {k: [tuple(x) for x in v] for k, v in json.load(f)["groups"].items()}
    g = kr_heatmap.with_s7(g)
    return {k: v for k, v in g.items() if k in us_pairs.PAIRS}


def kr_daily(names: set) -> dict:
    """{name: [(date, open, close)]} 오래된→최신."""
    c = sqlite3.connect(f"file:{KR_DB}?mode=ro", uri=True)
    out = {}
    q = ",".join("?" * len(names))
    for n, d, o, cl in c.execute(f"SELECT stock_name, date, open_price, close_price FROM kr_stock_daily_data "
                                 f"WHERE stock_name IN ({q}) ORDER BY date", list(names)):
        if o and cl:
            out.setdefault(n, []).append((d, float(o), float(cl)))
    c.close()
    return out


def stock_moves(rows) -> dict:
    """{date: (갭%, 종가%, 장중%)} — 전 거래일 종가 기준."""
    out = {}
    for (d0, _o0, c0), (d1, o1, c1) in zip(rows, rows[1:]):
        if c0 and o1:
            out[d1] = ((o1 / c0 - 1) * 100, (c1 / c0 - 1) * 100, (c1 / o1 - 1) * 100)
    return out


def us_moves() -> dict:
    """{ticker: {미국 날짜: %}}"""
    out = {}
    for t in us_pairs.tickers():
        rows = us_pairs.daily(t, rng="2y")
        out[t] = {d1: (p1 / p0 - 1) * 100 for (_, p0), (d1, p1) in zip(rows, rows[1:]) if p0}
    return out


def main():
    groups = load_groups()
    names = {n for v in groups.values() for _, n in v}
    kd = {n: stock_moves(r) for n, r in kr_daily(names).items()}
    print(f"관심그룹 종목 {len(names)}개 중 일봉 있음 {len(kd)}개")
    dates = sorted({d for m in kd.values() for d in m})
    market = {}
    for d in dates:
        v = [m[d] for m in kd.values() if d in m]
        market[d] = tuple(st.mean(x[i] for x in v) for i in range(3))
    usm = us_moves()
    us_dates = sorted({d for m in usm.values() for d in m})

    rows = []   # (분야, 한국 날짜, 짝꿍 평균, 분야 갭/종가/장중, 시장 대비 갭/종가/장중)
    for g, members in groups.items():
        for d in dates:
            i = bisect.bisect_left(us_dates, d) - 1          # d보다 앞선 마지막 미국 날짜
            if i < 0:
                continue
            ud = us_dates[i]
            pr = [usm[t][ud] for t in us_pairs.PAIRS[g] if ud in usm.get(t, {})]
            v = [kd[n][d] for _, n in members if n in kd and d in kd[n]]
            if not pr or len(v) < 2:
                continue
            gm = tuple(st.mean(x[k] for x in v) for k in range(3))
            rows.append((g, d, st.mean(pr), gm, tuple(gm[k] - market[d][k] for k in range(3))))
    print(f"분야×날짜 {len(rows)}건 · {dates[0]}~{dates[-1]}\n")

    def show(label, sel):
        if not sel:
            print(f"{label:28s} n=0")
            return
        m = lambda k, rel: st.mean((r[4] if rel else r[3])[k] for r in sel)
        win = sum(r[4][1] > 0 for r in sel) / len(sel) * 100
        print(f"{label:28s} n={len(sel):5d} | 분야 갭 {m(0,0):+5.2f} 종가 {m(1,0):+5.2f} 장중 {m(2,0):+5.2f} | "
              f"시장 대비 갭 {m(0,1):+5.2f} 종가 {m(1,1):+5.2f} 장중 {m(2,1):+5.2f} · 시장보다 강했던 날 {win:3.0f}%")

    half = dates[len(dates) // 2]
    for tag, sub in (("전체", rows), (f"앞반(~{half})", [r for r in rows if r[1] < half]),
                     (f"뒷반({half}~)", [r for r in rows if r[1] >= half])):
        print(f"=== {tag} ===")
        show("⭐ 짝꿍 +2%↑", [r for r in sub if r[2] >= 2])
        show("   짝꿍 +1~2%", [r for r in sub if 1 <= r[2] < 2])
        show("   짝꿍 -1~+1%", [r for r in sub if -1 < r[2] < 1])
        show("   짝꿍 -2~-1%", [r for r in sub if -2 < r[2] <= -1])
        show("🔻 짝꿍 -2%↓", [r for r in sub if r[2] <= -2])
        xs = [r[2] for r in sub]
        for k, nm in ((0, "갭"), (1, "종가"), (2, "장중")):
            ys = [r[4][k] for r in sub]
            print(f"   상관계수(짝꿍 ↔ 시장 대비 {nm}) {st.correlation(xs, ys):+.3f}")
        print()

    print("=== 분야별(전체 기간): ⭐날 시장 대비 종가 / 🔻날 시장 대비 종가 · 상관(갭) ===")
    for g in sorted(groups):
        s = [r for r in rows if r[0] == g]
        up = [r[4][1] for r in s if r[2] >= 2]
        dn = [r[4][1] for r in s if r[2] <= -2]
        cg = st.correlation([r[2] for r in s], [r[4][0] for r in s]) if len(s) > 10 else 0
        print(f"{g:16s} ⭐{len(up):3d}일 {st.mean(up) if up else 0:+5.2f} · 🔻{len(dn):3d}일 {st.mean(dn) if dn else 0:+5.2f} · 상관(갭) {cg:+.2f}")


if __name__ == "__main__":
    main()
