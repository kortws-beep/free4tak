"""
dip_situations_prep.py — 음봉 엔진 '상황별' 검증용 데이터 준비 (2026-10-30, 대장 "바닥권 하락 등 여러 상황 대입")
================================================================
[이 파일이 하는 일 — 비개발자용 설명]
dip_leader_minute.py의 117 하락일 거래마다 그날의 상황 꼬리표를 붙여 backtest/data/dip_situations.json에 저장.
  국면: 전날 전 종목 20일선 위 비율(market_regime.compute로 과거 재계산) · 그 전날 대비 오름/내림(반등 조짐)
  지수(ETF 대용 코스피·코스닥 중 그날 더 빠진 쪽): 전날까지 연속 하락일수 · 전날 종가의 20일 고점 대비 낙폭 · 60일선 위/아래
  거래: 진입 방식(반등+0.5% 현행 / 14:30) × 대상(전체 / NEW·SUB) × 매도 규칙 몇 가지 결과
분석은 dip_situations_review.py(주말)에서. 실행: venv/bin/python backtest/dip_situations_prep.py
"""
import os
import sys
import json
import datetime

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(BASE, "core"), os.path.join(BASE, "backtest")]
import dip_leader                        # noqa: E402
import market_regime                     # noqa: E402
import dip_leader_minute as M            # noqa: E402

OUT = os.path.join(BASE, "backtest", "data", "dip_situations.json")
EXITS = {   # 이름 → (익절%, 손절%(0=없음), 손절 시작일, 보유일)
    "현행 +3/-3.5": (3, 3.5, 0, 4),
    "+5/-3.5": (5, 3.5, 0, 4),
    "+5/-7": (5, 7, 0, 4),
    "+5/손절없음": (5, 0, 0, 4),
    "+3/손절없음": (3, 0, 0, 4),
}


def etf_rows(api, code: str, since: str = "2025-05-01") -> list:
    """[(date, close)] 오래된→최신."""
    out, end = {}, None
    for _ in range(6):
        rows = api.get_daily_ohlc(code, days=100, end_date=end)
        if not rows:
            break
        for r in rows:
            out[r["date"]] = r["close"]
        oldest = min(r["date"] for r in rows)
        if oldest <= since:
            break
        end = (datetime.date.fromisoformat(oldest) - datetime.timedelta(days=1)).strftime("%Y%m%d")
    return sorted(out.items())


def index_tags(rows: list) -> dict:
    """{date: {연속하락, 20일고점낙폭%, 60일선위}} — 모두 '전날 종가까지' 기준(그날 장중엔 알 수 있는 값)."""
    out = {}
    for i in range(61, len(rows)):
        closes = [c for _, c in rows[:i]]            # 전날까지
        streak = 0
        for j in range(len(closes) - 1, 0, -1):
            if closes[j] < closes[j - 1]:
                streak += 1
            else:
                break
        hi20 = max(closes[-20:])
        out[rows[i][0]] = {"streak": streak, "dd20": (closes[-1] / hi20 - 1) * 100,
                           "above60": closes[-1] > sum(closes[-60:]) / 60}
    return out


def sim(store, code, date, t, price, tp, sl, start, hold):
    entry = price * (1 + M.SLIP)
    ds = store.days_from(code, date, hold)
    for i, (d, bars) in enumerate(ds):
        for tm, p, h, l in bars:
            if i == 0 and tm <= t:
                continue
            if sl and i >= start and l <= entry * (1 - sl / 100):
                if h < entry * (1 - sl / 100):
                    return (h / entry - 1) * 100 - M.COST * 100
                return -sl - M.COST * 100
            if h >= entry * (1 + tp / 100):
                return tp - M.COST * 100
    return (ds[-1][1][-1][1] / entry - 1) * 100 - M.COST * 100 if ds else 0.0


def main():
    from dotenv import load_dotenv
    load_dotenv(os.path.join(BASE, ".env"))
    from kis_api import KisAPI
    api = KisAPI()
    store = M.MinuteStore(api)
    daily = {k: M.etf_daily(api, c) for k, c in M.ETF.items()}
    days = sorted(d for d in set(daily["kospi"]) | set(daily["kosdaq"]) if d >= M.SINCE and any(
        d in daily[k] and daily[k][d][1] <= daily[k][d][0] * (1 - dip_leader.INDEX_DROP / 100) for k in M.ETF))
    idx = {k: index_tags(etf_rows(api, c)) for k, c in M.ETF.items()}
    import sqlite3
    conn = sqlite3.connect(dip_leader.DAILY_DB)
    all_dates = [d for (d,) in conn.execute("SELECT DISTINCT date FROM kr_stock_daily_data WHERE date >= '2025-09-01' ORDER BY date")]
    conn.close()
    br = {d: v[0] for d, v in market_regime.compute(dates=all_dates).items()}
    bd = sorted(br)
    print(f"국면 재계산 {len(br)}일 ({bd[0]}~{bd[-1]}) · 하락일 {len(days)}")
    watch = dip_leader.watch_codes(api, os.getenv("KIS_HTS_ID2", os.getenv("KIS_HTS_ID", "")))
    rec = []
    for date in days:
        prior = [d for d in bd if d < date]
        if len(prior) < 2:
            continue
        b1, b2 = br[prior[-1]], br[prior[-2]]
        drop = {k: (daily[k][date][1] / daily[k][date][0] - 1) * 100 for k in M.ETF if date in daily[k]}
        key = min(drop, key=drop.get)                  # 그날 더 빠진 지수
        tag = {"date": date, "breadth": b1, "breadth_up": b1 > b2, "index": key, "index_low": drop[key],
               **idx[key].get(date, {})}
        prev = {k: daily[k][date][0] for k in M.ETF if date in daily[k]}
        trig = M.trigger_times(M.index_path(store, date, prev))
        uni, dd = dip_leader.universe(date), M.daily_rows(date)
        for name in ("반등+0.5%(현행)", "14:30 고정"):
            if name not in trig:
                continue
            for flt, allow in (("전체", None), ("NEW·SUB", watch)):
                p = M.pick_at(store, date, trig[name], uni, dd, allow)
                if not p:
                    continue
                res = {e: sim(store, p[0], date, trig[name], p[2], *v) for e, v in EXITS.items()}
                rec.append({**tag, "entry": name, "filter": flt, "code": p[0], "name": p[1], "time": trig[name],
                            "price": p[2], "chg": p[3], "bounce": p[4], "ret": res})
        print(f"  {date} 국면 {b1:.0f}% · {key} 연속하락 {tag.get('streak')}일 · 20일고점 {tag.get('dd20', 0):+.1f}%")
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False, indent=1)
    print(f"저장 {len(rec)}건 → {OUT}")


if __name__ == "__main__":
    sys.exit(main())
