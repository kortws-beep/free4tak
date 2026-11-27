"""
event_swap_check.py — 이벤트로 산 섹터를 '더 큰 이벤트' 섹터로 갈아타면 나은가 (2026-11-28 대장)
================================================================
대장: "파는 건 봇 손절 규칙이 칼같이 있으니 필요 없고, 대신 이벤트로 산 종목을 더 큰 이벤트(젠슨황 같은) 쪽으로
교체할 수 있게 하면 어떨까." → 섹터 ETF 10년으로 시뮬레이션:
  · 기본: 검증 짝 8개(event_noise_check.SET)를 b일 전 매수 → s일 전 매도
  · 큰 이벤트 날 = 들고 있는 동안, 다른 섹터 ETF 하나가 그날 KODEX200보다 +X%p↑ 튀고 내 섹터는 시장보다 못한 날
  · 교체 = 그날 종가에 내 섹터 팔고 가장 많이 튄 섹터 사서 원래 매도일까지(한 번만 교체)
  · 변형: 내 섹터가 산 뒤 손실 중일 때만 교체
산 뒤 정보는 교체 판단에 안 씀(그날 종가까지의 정보로 그날 종가에 교체). 수수료·세금 무시.
실행: venv/bin/python backtest/event_swap_check.py
"""
import bisect
import datetime
import os
import statistics as st
import sys

_H = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [_H, os.path.join(os.path.dirname(_H), "intelligence")]
import event_calendar as E          # noqa: E402
import event_sector_validate as V   # noqa: E402

SET = [("CES", "반도체", 20, 3), ("CES", "IT", 10, 1), ("지스타", "게임", 10, 1), ("광군절", "아모레퍼시픽", 20, 3),
       ("JPM헬스케어", "헬스케어", 20, 3), ("실적시즌", "반도체", 15, 3), ("GTC", "반도체", 20, 3), ("CES", "로봇(22~)", 20, 3)]
ETF_ONLY = [c for c in V.SECTORS if V.SECTORS[c] in {"화장품", "바이오", "헬스케어", "여행레저", "미디어컨텐츠", "K콘텐츠", "게임", "은행",
                                                     "증권", "보험", "반도체", "2차전지", "필수소비재", "경기소비재", "철강", "운송",
                                                     "소프트웨어", "IT", "에너지화학", "로봇(22~)"}]


def main():
    data = V.load()
    bench = dict(data[V.BENCH])
    px = {c: dict(data[c]) for c in data}
    code_of = {v: k for k, v in V.SECTORS.items()}
    today = datetime.date.today()
    evs = E.events(2015, today.year)
    for thr in (3.0, 4.0, 5.0):
        for only_losing in (False, True):
            hold, swap, n_sw, better = [], [], 0, 0
            for ev, sec, b, s in SET:
                code = code_of[sec]
                ds = [d for d, _ in data[code]]
                for d in [x for x in evs[ev] if x < today - datetime.timedelta(days=10)]:
                    i = bisect.bisect_left(ds, d.isoformat())
                    if i - b < 1 or i >= len(ds):
                        continue
                    a, z = ds[i - b], ds[i - s]
                    base = (px[code][z] / px[code][a] - 1) * 100
                    res = base
                    for j in range(i - b + 1, i - s):
                        dj, dp = ds[j], ds[j - 1]
                        if dj not in bench or dp not in bench:
                            continue
                        mk = (bench[dj] / bench[dp] - 1) * 100
                        mine = (px[code][dj] / px[code][dp] - 1) * 100 - mk
                        if mine > 0:
                            continue
                        if only_losing and px[code][dj] >= px[code][a]:
                            continue
                        hot = [(((px[c][dj] / px[c][dp] - 1) * 100 - mk), c) for c in ETF_ONLY
                               if c != code and dj in px[c] and dp in px[c]]
                        hot = [h for h in hot if h[0] >= thr]
                        if not hot:
                            continue
                        _, hc = max(hot)
                        if z not in px[hc]:
                            continue
                        res = ((px[code][dj] / px[code][a]) * (px[hc][z] / px[hc][dj]) - 1) * 100
                        n_sw += 1
                        better += res > base
                        break
                    hold.append(base)
                    swap.append(res)
            print(f"큰 이벤트 = 다른 섹터 하루 +{thr:.0f}%p↑{' · 손실 중일 때만' if only_losing else '':12s} | "
                  f"교체 {n_sw:2d}/{len(hold)}회 · 교체해서 나아진 {better}/{n_sw} | 그냥 보유 평균 {st.mean(hold):+.2f}% · "
                  f"교체 규칙 평균 {st.mean(swap):+.2f}% · 최악 {min(hold):+.1f}→{min(swap):+.1f}%")


if __name__ == "__main__":
    main()
