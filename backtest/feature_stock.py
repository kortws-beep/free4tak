"""
feature_stock.py — 횡보장 엔진 후보 ② '개별 특징주' 분봉 검증 (2026-11-05, 대장 "횡보장은 개별 특징주 장세 — 필히 들어가야")
================================================================
[이 파일이 하는 일 — 비개발자용 설명]
유튜브 추천 기록은 20일 지나면 지워져 9월치가 없고, 뉴스 '[특징주]' 기사도 9월 38건뿐이라
'가격으로 본 특징주'로 검증: 그날 자기 테마(들)는 조용한데(테마 평균 +2% 미만) 혼자 크게 오른 종목 = 개별 재료.
제미나이 가설: 아침에 시가를 높게 띄운(갭 +5%↑) 재료주는 밀리고, 오후(13시 이후)에 뜬금없이 터진 종목은 다음 날까지 간다.
  날짜: 2026-07-01 ~ 10-31, 국면(전날 20일선 위 비율)으로 하락<35 / 횡보35~50 / 상승50+
  후보: 일봉 고가 +7%↑·거래대금 300억↑(sideways_pullback과 같은 분봉 캐시 minute_vol.db 재사용) & 테마 평균(종가) +2% 미만
  유형·진입:
    오전 갭형    — 시가 갭 +5%↑ → 09:05 매수
    오후 급등형  — 13:00엔 +3% 미만이었는데 14:50에 +7%↑ → 14:50 매수
  매도:
    당일정리     — +3% 익절 · -3.5% 손절 · 15:15 정리(오전 갭형만 의미)
    다음날시가   — 다음 거래일 시가에 정리
    다음날+3     — 다음 날 +3% 익절 · -3.5% 손절 · 다음 날 15:15 정리
  수수료·세금 0.2%, 매수 미끄러짐 0.1%. 표마다 건수·승률·평균·앞/뒤 절반.
실행: venv/bin/python backtest/feature_stock.py
"""
import os
import sys
import sqlite3
import collections

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(BASE, "core"), os.path.join(BASE, "backtest"), os.path.join(BASE, "intelligence")]
import market_regime                    # noqa: E402
import dip_leader                       # noqa: E402
import sideways_pullback as SP          # noqa: E402

COST, SLIP = 0.002, 0.001
THEME_QUIET = 2.0


def daily_chg(date: str) -> dict:
    """{이름: 등락%} 그날 종가 기준."""
    c = sqlite3.connect(dip_leader.DAILY_DB)
    prev = c.execute("SELECT MAX(date) FROM kr_stock_daily_data WHERE date < ?", (date,)).fetchone()[0]
    p = dict(c.execute("SELECT stock_name, close_price FROM kr_stock_daily_data WHERE date=?", (prev,)))
    out = {n: (cl / p[n] - 1) * 100 for n, cl in c.execute("SELECT stock_name, close_price FROM kr_stock_daily_data WHERE date=?", (date,))
           if p.get(n)}
    c.close()
    return out


def price_at(bars, t):
    xs = [b for b in bars if b[0] <= t]
    return xs[-1][1] if xs else None


def sim_rest_of_day(bars, t, entry, end="151500"):
    for tm, p, h, l, v in bars:
        if tm <= t:
            continue
        if tm >= end:
            return (p / entry - 1) * 100
        if l <= entry * 0.965:
            return (min(h, entry * 0.965) / entry - 1) * 100
        if h >= entry * 1.03:
            return 3.0
    return (bars[-1][1] / entry - 1) * 100 if bars else 0.0


def main():
    from dotenv import load_dotenv
    load_dotenv(os.path.join(BASE, ".env"))
    from kis_api import KisAPI
    import theme_open_obs
    store = SP.VolStore(KisAPI())
    themes = theme_open_obs.load_themes()
    code_themes = collections.defaultdict(list)
    for th, ms in themes.items():
        if len(ms) >= 5:
            for c, n in ms:
                code_themes[c].append((th, [n2 for _, n2 in ms]))
    c = sqlite3.connect(dip_leader.DAILY_DB)
    ds = [d for (d,) in c.execute("SELECT DISTINCT date FROM kr_stock_daily_data WHERE date >= '2026-05-01' ORDER BY date")]
    c.close()
    br = {d: v[0] for d, v in market_regime.compute(dates=ds).items()}
    bd = sorted(br)
    cand = SP.candidates()
    res = collections.defaultdict(list)
    for date in sorted(cand):
        prior = [d for d in bd if d < date]
        if not prior:
            continue
        g = SP.bucket(br[prior[-1]])
        chg = daily_chg(date)
        nxt = store.days_from  # 다음 거래일 분봉
        for code, name, prev in cand[date]:
            ths = code_themes.get(code)
            if not ths:
                continue
            avg = max(sum(chg.get(n, 0) for n in ns if n != name) / max(1, len(ns) - 1) for _, ns in ths)
            if avg >= THEME_QUIET:
                continue                                   # 테마가 같이 오른 날 = 개별 재료 아님
            bars = store.day(code, date)
            if not bars:
                continue
            o = bars[0][1]
            kinds = []
            if o >= prev * 1.05:
                kinds.append(("오전갭형", "090500"))
            p13, p1450 = price_at(bars, "130000"), price_at(bars, "145000")
            if p13 and p1450 and p13 < prev * 1.03 and p1450 >= prev * 1.07:
                kinds.append(("오후급등형", "145000"))
            if not kinds:
                continue
            days = nxt(code, date, 2)
            nb = days[1][1] if len(days) > 1 and days[0][0] == date else None
            for kind, t in kinds:
                px = price_at(bars, t)
                if not px:
                    continue
                entry = px * (1 + SLIP)
                if kind == "오전갭형":
                    res[(g, kind, "당일정리")].append(sim_rest_of_day(bars, t, entry) - COST * 100)
                if nb:
                    res[(g, kind, "다음날시가")].append((nb[0][1] / entry - 1) * 100 - COST * 100)
                    r = None                                   # 산 날은 그대로 들고 감(손절 안 봄) → 다음 날 규칙
                    for tm, p, h, l, v in nb:
                        if tm >= "151500":
                            r = (p / entry - 1) * 100
                            break
                        if l <= entry * 0.965:
                            r = (min(h, entry * 0.965) / entry - 1) * 100
                            break
                        if h >= entry * 1.03:
                            r = 3.0
                            break
                    if r is None:
                        r = (nb[-1][1] / entry - 1) * 100
                    res[(g, kind, "다음날+3")].append(r - COST * 100)
        print(f"{date} {g} 후보 {len(cand[date])}", flush=True)
    for g in ("횡보35~50", "상승50+", "하락<35"):
        print(f"\n[{g}]")
        for kind in ("오전갭형", "오후급등형"):
            for rule in ("당일정리", "다음날시가", "다음날+3"):
                xs = res.get((g, kind, rule), [])
                if xs:
                    print(SP.line(f"{kind} {rule}", xs))


if __name__ == "__main__":
    sys.exit(main())
