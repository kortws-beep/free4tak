"""
hedge_sector_review.py — 하락장 헤지 후보 검증 (SCENARIOS 7-2, 2026-10-20)
================================================================
[이 파일이 하는 일 — 비개발자용 설명]
대장(10-19): "장기 하락 때 대체할 수 있는 헤지 섹터도 필요." 하락장엔 sbot·daybot이 쉬기만 하는데,
그동안 돈을 어디 두면 좋았을지 과거로 확인한다.
  하락 구간 = 코스피가 20거래일 고점 대비 -8% 빠진 날(그날 '알아챔' — 실시간으로도 알 수 있는 날) ~
              저점에서 +8% 되돌린 날(끝). turning_point.py와 같은 정의, 2010년~지금(FinanceDataReader).
  후보마다 ① 알아챈 날 종가에 사서 끝난 날 종가에 판 수익(실제로 할 수 있는 것)
           ② 고점→저점 수익(사후 판단 — '덜 빠짐'인지 '오름'인지 보는 참고용)
  판정: 구간을 시간순 앞/뒤 절반으로 나눠 ① 평균이 둘 다 + 인 것만 ✅ (다른 검증들과 같은 원칙).
  ⚠️ '덜 빠짐 ≠ 오름' — 코스피보다 덜 빠졌어도 마이너스면 헤지가 아니라 '덜 잃는 곳'.
  ③ 봇 '하락장 쉬기' 날 흉내: 국면(20일선 위 종목 비율) 기록은 07-14부터라 짧음 → 대신 '전날 코스피가 20일선보다
     4% 이상 아래'를 쉬는 날로 봄(07-14~10-20 실제 국면<35%와 65일 중 59일 일치, 쉬는 날 18일 전부 잡음).
     쉬는 날마다 후보를 하루 들고 있었으면(전날 종가→그날 종가) 쌓인 수익. 쉬는 날 수로 앞/뒤 반씩 — 둘 다 + 면 ✅.
실행: python backtest/hedge_sector_review.py [--refresh]
"""
import os
import sys
import pickle
import datetime

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(BASE, "backtest", "data", "hedge_prices.pkl")
START = "2010-01-01"
LOOKBACK, DROP, REBOUND = 20, 8.0, 8.0

CANDIDATES = {
    "KS11": "코스피 지수(비교용)",
    "069500": "KODEX 200(비교용)",
    "114800": "KODEX 인버스",
    "252670": "KODEX 200선물인버스2X",
    "261240": "KODEX 미국달러선물",
    "132030": "KODEX 골드선물(H)",
    "411060": "ACE KRX금현물",
    "153130": "KODEX 단기채권(현금 대신)",
    "114260": "KODEX 국고채3년",
    "148070": "KIWOOM 국고채10년",
    "305080": "TIGER 미국채10년선물",
    "360750": "TIGER 미국S&P500",
    "133690": "TIGER 미국나스닥100",
    "279530": "KODEX 고배당주",
    "091170": "KODEX 은행",
    "033780": "KT&G",
    "017670": "SK텔레콤",
    "030200": "KT",
    "015760": "한국전력",
}


def episodes(closes: list, drop: float = DROP, rebound: float = REBOUND, lookback: int = LOOKBACK) -> list:
    """[{peak, detect, trough, end}] 인덱스. detect = 20일 고점 대비 -drop% 처음 닿은 날, end = 저점 대비 +rebound%
    (끝 안 났으면 None). 직전 구간이 끝난 뒤의 고점에서만 새 구간 시작(turning_point.episodes와 같은 규칙)."""
    out, i, n, last_end = [], lookback, len(closes), 0
    while i < n:
        hi = max(range(max(i - lookback, last_end), i), key=lambda k: closes[k])
        if closes[i] <= closes[hi] * (1 - drop / 100):
            trough, end, j = i, None, i
            while j < n:
                if closes[j] < closes[trough]:
                    trough = j
                if closes[j] >= closes[trough] * (1 + rebound / 100):
                    end = j
                    break
                j += 1
            out.append({"peak": hi, "detect": i, "trough": trough, "end": end})
            if end is None:
                break
            last_end, i = end, end + 1
        else:
            i += 1
    return out


def ret(px: dict, d0: str, d1: str):
    """종가 기준 d0→d1 수익률(%). 그 날 시세가 없으면(상장 전 등) d0 이후 첫 거래일 사용, 그래도 없으면 None."""
    if not px:
        return None
    days = sorted(px)
    a = next((d for d in days if d >= d0), None)
    b = next((d for d in reversed(days) if d <= d1), None)
    if a is None or b is None or a > d1 or b < d0 or days[0] > d0:      # 상장 전 구간은 제외
        return None
    return (px[b] / px[a] - 1) * 100


def judge(vals: list) -> dict:
    """구간별 수익 목록(시간순) → 평균·승률·앞뒤 절반 평균·✅ 여부."""
    v = [x for x in vals if x is not None]
    if len(v) < 4:
        return {"n": len(v), "ok": False}
    h = len(v) // 2
    f, b = sum(v[:h]) / h, sum(v[h:]) / (len(v) - h)
    return {"n": len(v), "avg": sum(v) / len(v), "win": sum(1 for x in v if x > 0) / len(v) * 100,
            "front": f, "back": b, "ok": f > 0 and b > 0}


PAUSE_GAP = -4.0
ETF_FROM = "2014-07-25"


def pause_days(dates: list, closes: list, gap: float = PAUSE_GAP, ma: int = 20) -> list:
    """[(전날, 그날)] — 전날 종가가 20일선 대비 gap% 이하인 날(봇이 그날 신규매수를 쉬는 날 흉내)."""
    out = []
    for i in range(ma, len(closes)):
        m = sum(closes[i - ma:i]) / ma          # 전날까지 20일 평균
        if (closes[i - 1] / m - 1) * 100 <= gap:
            out.append((dates[i - 1], dates[i]))
    return out


def hold_on(px: dict, days: list) -> tuple:
    """쉬는 날마다 하루씩 들고 있었을 때 (쌓인 수익 %, 쓴 날 수). 시세 없는 날은 건너뜀."""
    g, n = 1.0, 0
    for a, b in days:
        if a in px and b in px:
            g *= px[b] / px[a]
            n += 1
    return (g - 1) * 100, n


def load(refresh: bool = False) -> dict:
    if not refresh and os.path.exists(CACHE):
        with open(CACHE, "rb") as fp:
            data = pickle.load(fp)
        if data.get("_date") == datetime.date.today().isoformat():
            return data
    import FinanceDataReader as fdr
    data = {"_date": datetime.date.today().isoformat()}
    for code in CANDIDATES:
        try:
            px = {}
            for a, b in ((START, "2017-12-31"), ("2018-01-01", None)):   # 한 번에 3,000일까지만 줘서 나눠 받기
                df = fdr.DataReader(code, a, b) if b else fdr.DataReader(code, a)
                px.update({d.strftime("%Y-%m-%d"): float(c) for d, c in df["Close"].items() if c == c and c > 0})
            data[code] = px
        except Exception as e:
            print(f"⚠️ {code} 불러오기 실패: {e}")
            data[code] = {}
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    with open(CACHE, "wb") as fp:
        pickle.dump(data, fp)
    return data


def main():
    data = load("--refresh" in sys.argv)
    ks = sorted(data["KS11"].items())
    dates, closes = [d for d, _ in ks], [c for _, c in ks]
    eps = episodes(closes)
    done = [e for e in eps if e["end"] is not None]
    print(f"🔻 코스피 하락 구간 {len(eps)}개 ({dates[0]}~{dates[-1]}, 20일 고점 -{DROP:g}% 알아챔 → 저점 +{REBOUND:g}% 끝)")
    for e in eps:
        p, d, t = e["peak"], e["detect"], e["trough"]
        en = dates[e["end"]] if e["end"] is not None else "진행 중"
        ksr = ret(data["KS11"], dates[d], dates[e["end"] if e["end"] is not None else -1])
        print(f"   {dates[p]} 고점 → {dates[d]} 알아챔 → {dates[t]} 저점({(closes[t] / closes[p] - 1) * 100:+.1f}%) → {en}"
              f" · 알아챔→끝 코스피 {ksr:+.1f}%")
    print(f"\n① 알아챈 날 사서 끝난 날 판 수익(끝난 {len(done)}개 구간, 앞/뒤 절반 평균 둘 다 + 면 ✅)")
    print(f"   {'후보':<24}{'구간':>4}{'평균':>8}{'승률':>7}{'앞절반':>8}{'뒤절반':>8}{'고점→저점':>10}")
    rows = []
    for code, name in CANDIDATES.items():
        px = data.get(code, {})
        r1 = [ret(px, dates[e["detect"]], dates[e["end"]]) for e in done]
        r2 = [ret(px, dates[e["peak"]], dates[e["trough"]]) for e in done]
        j, j2 = judge(r1), judge(r2)
        rows.append((code, name, j, j2))
    rows.sort(key=lambda x: -(x[2].get("avg") if x[2]["n"] >= 4 else -999))
    for code, name, j, j2 in rows:
        if j["n"] < 4:
            print(f"   {name:<24}{j['n']:>4}  (구간 4개 미만 — 상장 늦음)")
            continue
        print(f"   {name:<24}{j['n']:>4}{j['avg']:>+8.2f}{j['win']:>6.0f}%{j['front']:>+8.2f}{j['back']:>+8.2f}"
              f"{j2.get('avg', 0):>+10.2f}  {'✅' if j['ok'] else ''}")
    # ETF 시세는 FinanceDataReader에 2014-07-25부터만 있음 → 모든 후보가 같은 날로 비교되게 그 뒤만
    pdays = [x for x in pause_days(dates, closes) if x[0] >= ETF_FROM]
    split = pdays[len(pdays) // 2][1]           # 쉬는 날 수로 반 가르기
    print(f"\n③ 쉬는 날(전날 코스피가 20일선 -{-PAUSE_GAP:g}% 이하) {len(pdays)}일 동안 하루씩 들고 있었다면 — "
          f"앞({pdays[0][1][:4]}~{split[:7]})/뒤({split[:7]}~) 둘 다 + 면 ✅")
    print(f"   {'후보':<24}{'쓴날':>6}{'전체':>9}{'앞':>9}{'뒤':>9}{'하루평균':>9}")
    res = []
    for code, name in CANDIDATES.items():
        px = data.get(code, {})
        tot, n = hold_on(px, pdays)
        f, nf = hold_on(px, [x for x in pdays if x[1] < split])
        b, nb = hold_on(px, [x for x in pdays if x[1] >= split])
        res.append((name, n, tot, f if nf >= 20 else None, b if nb >= 20 else None, nb))
    res.sort(key=lambda x: -x[2] / max(x[1], 1))
    for name, n, tot, f, b, nb in res:
        fs = f"{f:>+9.1f}" if f is not None else f"{'-':>9}"
        bs = f"{b:>+9.1f}" if b is not None else f"{'-':>9}"
        ok = "✅" if (f is not None and b is not None and f > 0 and b > 0) else ""
        print(f"   {name:<24}{n:>6}{tot:>+9.1f}{fs}{bs}{tot / max(n, 1):>+9.3f}  {ok}")

    cur = eps[-1] if eps and eps[-1]["end"] is None else None
    if cur:
        d0 = dates[cur["detect"]]
        print(f"\n② 지금 진행 중인 구간({d0} 알아챔~{dates[-1]}) 후보별 수익")
        live = sorted(((ret(data.get(c, {}), d0, dates[-1]), n) for c, n in CANDIDATES.items()), key=lambda x: -(x[0] or -999))
        for r, n in live:
            if r is not None:
                print(f"   {n:<24}{r:+8.2f}%")


if __name__ == "__main__":
    main()
