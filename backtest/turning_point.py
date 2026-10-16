"""
turning_point.py — 과거 하락 구간의 전환점(바닥) 분석 (2026-10-17, 하락장 끝 연구 1번)
================================================================
[이 파일이 하는 일 — 비개발자용 설명]

대장: "6일간 코스피 -24%, 오늘 반등 2% — 관망. 7월 전저점(5,260)을 테스트하느냐 말아 올리느냐는
예측이 힘든 영역." → 예측 대신 과거 하락 구간들이 바닥에서 어떻게 움직였는지 숫자로 본다.
지수 일봉(market_index, intelligence/market_flow.py로 수집) + 종목 일봉 DB(20일 신저가 비율·20일선 위 비율).

하락 구간 = 코스피가 직전 20거래일 고점 대비 -8% 이상 빠졌다가, 그 저점에서 +8% 이상 되돌린 구간
(되돌림이 아직 안 나왔으면 '진행 중').
구간마다:
  · 고점 → 저점: 날짜·하락폭·걸린 거래일
  · 구간 안 신저가 비율 최고일(최대 투매일)과 그날 → 저점까지 거래일 (투매일이 곧 바닥이었나?)
  · 최대 투매일 뒤 첫 양봉(지수 상승 마감)일 — 그 뒤에 더 낮은 저점이 나왔나(첫 반등 뒤 재하락 여부)
  · 저점 뒤 5·10·20거래일 수익률, 저점 뒤 10일 안 저점 재시험(저점 +2% 안까지 다시 내려왔나)
  · 저점 날 20일선 위 비율
마지막에 지금 구간 상태(진행 중이면 고점 대비 하락폭, 지금까지 저점, 최대 투매일).
실행:  python backtest/turning_point.py [--drop 8] [--rebound 8] [--idx KOSPI|KOSDAQ]
"""
import os
import sys
import sqlite3

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "backtest"))
import swing_backtest as sb      # noqa: E402
import rebound_backtest as rb    # noqa: E402
import bottom_signal as bs       # noqa: E402

LOG_DB = os.path.join(BASE, "lina_bot", "three_month_leader_log.db")
LOOKBACK = 20


def load_index(idx: str = "KOSPI", db: str = LOG_DB) -> list:
    """[(date, close)] 오래된→최신."""
    conn = sqlite3.connect(db)
    try:
        return conn.execute("SELECT date, close FROM market_index WHERE idx=? AND close IS NOT NULL ORDER BY date",
                            (idx,)).fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


def episodes(series: list, drop: float = 8.0, rebound: float = 8.0) -> list:
    """[{peak_i, trough_i, end_i or None}] — 20일 고점 대비 -drop% 진입 → 저점 대비 +rebound% 되돌림에서 끝."""
    out, i, n = [], LOOKBACK, len(series)
    closes = [c for _, c in series]
    last_end = 0
    while i < n:
        hi_i = max(range(max(i - LOOKBACK, last_end), i), key=lambda k: closes[k])
        # 직전 구간이 끝난 뒤 생긴 고점에서만 새 구간 시작 — 되돌림 뒤 재시험이 새 구간으로 잡히지 않게
        if closes[i] <= closes[hi_i] * (1 - drop / 100):
            trough = i
            j = i
            end = None
            while j < n:
                if closes[j] < closes[trough]:
                    trough = j
                if closes[j] >= closes[trough] * (1 + rebound / 100):
                    end = j
                    break
                j += 1
            out.append({"peak_i": hi_i, "trough_i": trough, "end_i": end})
            if end is None:
                break
            last_end = end
            i = end + 1
        else:
            i += 1
    return out


def fwd(closes: list, i: int, k: int):
    return (closes[i + k] / closes[i] - 1) * 100 if i + k < len(closes) else None


def describe(series: list, ep: dict, lows: dict, breadth: dict) -> dict:
    dates = [d for d, _ in series]
    closes = [c for _, c in series]
    p, t, e = ep["peak_i"], ep["trough_i"], ep["end_i"]
    span = range(p, (e if e is not None else len(series) - 1) + 1)
    cap_i = max(span, key=lambda k: lows.get(dates[k], 0))
    first_up = next((k for k in range(cap_i + 1, len(series)) if closes[k] > closes[k - 1]), None)
    lower_after_up = None
    if first_up is not None:
        lower_after_up = any(closes[k] < closes[first_up - 1] for k in range(first_up + 1, min(len(series), first_up + 11)))
    retest = None
    if t + 1 < len(series):
        nxt = closes[t + 1:t + 11]
        retest = any(c <= closes[t] * 1.02 for c in nxt) if nxt else None
    return {"peak": dates[p], "peak_c": closes[p], "trough": dates[t], "trough_c": closes[t],
            "dd": (closes[t] / closes[p] - 1) * 100, "days": t - p, "end": dates[e] if e is not None else None,
            "cap": dates[cap_i], "cap_nl": lows.get(dates[cap_i], 0), "cap_to_trough": t - cap_i,
            "first_up": dates[first_up] if first_up is not None else None, "lower_after_up": lower_after_up,
            "f5": fwd(closes, t, 5), "f10": fwd(closes, t, 10), "f20": fwd(closes, t, 20), "retest": retest,
            "breadth": breadth.get(dates[t])}


def report(series: list, lows: dict, breadth: dict, drop: float = 8.0, rebound: float = 8.0, idx: str = "KOSPI") -> str:
    if len(series) < LOOKBACK + 5:
        return "지수 일봉이 모자라 — 먼저 `python intelligence/market_flow.py --backfill 300`"
    eps = episodes(series, drop, rebound)
    L = [f"🔻 {idx} 하락 구간 전환점 — {series[0][0]}~{series[-1][0]}, 20일 고점 대비 -{drop:.0f}% 진입 → "
         f"저점 대비 +{rebound:.0f}% 되돌림에서 끝 · {len(eps)}구간"]
    f = lambda v, s="%": f"{v:+.1f}{s}" if v is not None else "-"
    for ep in eps:
        x = describe(series, ep, lows, breadth)
        status = f"끝 {x['end']}" if x["end"] else "⏳ 진행 중"
        L.append(f"■ {x['peak']}({x['peak_c']:,.0f}) → 저점 {x['trough']}({x['trough_c']:,.0f}) {x['dd']:+.1f}% "
                 f"· {x['days']}거래일 · {status}")
        gap = x["cap_to_trough"]
        when = ("저점까지 0거래일 (투매일이 곧 바닥)" if gap == 0 else f"저점까지 {gap}거래일" if gap > 0
                else f"바닥 {-gap}거래일 뒤에 나옴(바닥 뒤 투매)")
        L.append(f"   최대 투매일 {x['cap']} 신저가 {x['cap_nl']:.0f}% → {when}"
                 + f" · 그 뒤 첫 양봉 {x['first_up'] or '-'}"
                 + ("" if x["lower_after_up"] is None else
                    (" → 10일 안에 더 낮은 저점 나옴(재하락)" if x["lower_after_up"] else " → 더 낮은 저점 없음")))
        rt = "예" if x["retest"] else "아니오" if x["retest"] is not None else "-"
        br = f" · 저점 날 20일선 위 {x['breadth']:.0f}%" if x["breadth"] is not None else ""
        L.append(f"   저점 뒤 5일 {f(x['f5'])} · 10일 {f(x['f10'])} · 20일 {f(x['f20'])} · "
                 f"10일 안 저점 재시험(+2% 안) {rt}{br}")
    done = [describe(series, ep, lows, breadth) for ep in eps if ep["end_i"] is not None]
    if done:
        same = sum(1 for x in done if x["cap_to_trough"] == 0)
        retest = sum(1 for x in done if x["retest"])
        L.append(f"■ 요약(끝난 {len(done)}구간): 최대 투매일 = 바닥 {same}번 · 바닥 뒤 10일 안 재시험 {retest}번 · "
                 f"첫 양봉 뒤 재하락 {sum(1 for x in done if x['lower_after_up'])}번")
    return "\n".join(L)


if __name__ == "__main__":
    a = sys.argv[1:]
    drop = float(a[a.index("--drop") + 1]) if "--drop" in a else 8.0
    reb = float(a[a.index("--rebound") + 1]) if "--rebound" in a else 8.0
    idx = a[a.index("--idx") + 1] if "--idx" in a else "KOSPI"
    daily = sb.load_daily(rb.THEME_DB, 330)
    print(report(load_index(idx), bs.new_low_share(daily), sb.market_breadth(daily), drop, reb, idx))
