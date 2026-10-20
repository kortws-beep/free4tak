"""
sector_rotation_review.py — 반도체가 쉴 때 돈이 어디로 도나 (2026-10-21, 대장 관심그룹 고정안 검증)
================================================================
[이 파일이 하는 일 — 비개발자용 설명]
대장: "전통적으로 반도체가 쉬어가면 2차전지·조선·건설·화장품·금융으로 순환 — 이번엔 2차전지였지만 매번 다르다."
→ new 관심그룹에 '순환 섹터 대표'를 고정으로 앉혀둘지 판단하려고, 과거 반도체가 쉰 구간마다
   대장이 고른 섹터 대표 종목들이 어떻게 움직였는지 줄 세워 본다.
  반도체 쉼 구간 = KODEX 반도체(091160)가 20거래일 고점 대비 -10% 빠진 날(알아챔) ~ 저점에서 +10% 되돌린 날(끝).
  보는 창 두 개: ① 고점→저점(반도체가 빠지는 동안 — 사후 판단, 순환이 실제로 일어나는 때)
                 ② 알아챈 날→끝(실시간으로 할 수 있는 것)
  섹터 수익 = 그 섹터 종목들 평균(상장 전 종목은 빼고). 구간마다 1등 섹터, 섹터별 1등 횟수·평균·코스피보다 나았던 비율.
  ⚠️ 종목은 대장 감으로 고른 '지금' 대표주 — 예전엔 다른 대표주였을 수 있음(생존 편향). 시세는 FDR 2014-07부터.
실행: python backtest/sector_rotation_review.py [--refresh]
"""
import os
import sys
import pickle
import datetime

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "backtest"))
from hedge_sector_review import episodes, ret  # noqa: E402

CACHE = os.path.join(BASE, "backtest", "data", "rotation_prices.pkl")
SEMI, KOSPI = "091160", "KS11"
DROP = REBOUND = 10.0
SECTORS = {
    "2차전지": {"006400": "삼성SDI", "003670": "포스코퓨처엠", "066970": "엘앤에프"},
    "조선": {"082740": "한화엔진", "333430": "일승"},
    "건설": {"000720": "현대건설", "028050": "삼성E&A"},
    "화장품": {"090430": "아모레퍼시픽", "278470": "에이피알", "257720": "실리콘투"},
    "금융": {"105560": "KB금융", "006220": "제주은행"},
}


def sector_ret(data: dict, members: dict, d0: str, d1: str):
    """섹터 평균 수익(%) — 그 구간에 시세 있는 종목만. 하나도 없으면 None."""
    rs = [r for r in (ret(data.get(c, {}), d0, d1) for c in members) if r is not None]
    return sum(rs) / len(rs) if rs else None


def table(data: dict, wins: list) -> dict:
    """wins=[(d0, d1)] → {"rows": [(d0, d1, 반도체, 코스피, {섹터: 수익}, 1등)], "score": {섹터: [1등횟수, 평균, 코스피초과율, n]}}."""
    rows, acc = [], {s: [] for s in SECTORS}
    for d0, d1 in wins:
        sec = {s: sector_ret(data, m, d0, d1) for s, m in SECTORS.items()}
        ks = ret(data[KOSPI], d0, d1)
        have = {s: v for s, v in sec.items() if v is not None}
        top = max(have, key=have.get) if have else None
        rows.append((d0, d1, ret(data[SEMI], d0, d1), ks, sec, top))
        for s, v in have.items():
            acc[s].append((v, ks, s == top))
    score = {}
    for s, xs in acc.items():
        if xs:
            score[s] = [sum(1 for _, _, t in xs if t), sum(v for v, _, _ in xs) / len(xs),
                        sum(1 for v, k, _ in xs if k is not None and v > k) / len(xs) * 100, len(xs)]
    return {"rows": rows, "score": score}


def load(refresh: bool = False) -> dict:
    if not refresh and os.path.exists(CACHE):
        with open(CACHE, "rb") as fp:
            data = pickle.load(fp)
        if data.get("_date") == datetime.date.today().isoformat():
            return data
    import FinanceDataReader as fdr
    data = {"_date": datetime.date.today().isoformat()}
    codes = [SEMI, KOSPI] + [c for m in SECTORS.values() for c in m]
    for code in codes:
        px = {}
        for a, b in (("2010-01-01", "2017-12-31"), ("2018-01-01", None)):
            try:
                df = fdr.DataReader(code, a, b) if b else fdr.DataReader(code, a)
                px.update({d.strftime("%Y-%m-%d"): float(c) for d, c in df["Close"].items() if c == c and c > 0})
            except Exception as e:
                print(f"⚠️ {code} {a}~ 불러오기 실패: {e}")
        data[code] = px
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    with open(CACHE, "wb") as fp:
        pickle.dump(data, fp)
    return data


def _f(v, w=7):
    return f"{v:>+{w}.1f}" if v is not None else f"{'-':>{w}}"


def main():
    data = load("--refresh" in sys.argv)
    semi = sorted(data[SEMI].items())
    dates, closes = [d for d, _ in semi], [c for _, c in semi]
    eps = episodes(closes, DROP, REBOUND)
    print(f"🔄 반도체(KODEX 반도체) 쉼 구간 {len(eps)}개 ({dates[0]}~{dates[-1]}, 20일 고점 -{DROP:g}% → 저점 +{REBOUND:g}%)")
    names = "  ".join(f"{s}({'·'.join(m.values())})" for s, m in SECTORS.items())
    print(f"   섹터 = {names}")
    for title, pick in (("① 반도체가 빠지는 동안(고점→저점, 사후 판단)", lambda e: (e["peak"], e["trough"])),
                        ("② 알아챈 날→끝(실시간으로 할 수 있는 것)", lambda e: (e["detect"], e["end"] if e["end"] is not None else len(dates) - 1))):
        wins = [(dates[a], dates[b]) for a, b in (pick(e) for e in eps)]
        t = table(data, wins)
        print(f"\n{title}")
        print(f"   {'구간':<23}{'반도체':>7}{'코스피':>7}" + "".join(f"{s:>7}" for s in SECTORS) + "   1등")
        for d0, d1, sm, ks, sec, top in t["rows"]:
            print(f"   {d0[2:]}~{d1[2:]}  {_f(sm)}{_f(ks)}" + "".join(_f(sec[s]) for s in SECTORS) + f"   {top or '-'}")
        print("   ── 섹터별: 1등 횟수 · 평균 · 코스피보다 나았던 비율")
        for s, (top_n, avg, beat, n) in sorted(t["score"].items(), key=lambda x: -x[1][0]):
            print(f"   {s:<6} 1등 {top_n:>2}/{n}번 · 평균 {avg:+6.1f}% · 코스피보다 나음 {beat:3.0f}%")


if __name__ == "__main__":
    main()
