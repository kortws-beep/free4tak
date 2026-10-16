"""
situation_classifier.py — 상황 판별기: 상승장/박스권/하락장/투매 (2026-10-17)
================================================================
[이 파일이 하는 일 — 비개발자용 설명]

SCENARIOS.md ⑤ "상황 판별기 — 상승장/박스권/하락장/투매 구분 → 봇 실적을 상황별로 나눠
시나리오 늘리기". 이번 2주 관찰: 추세형 봇(daybot·sbot)이 하락장·투매에서 손절이 반복됐다는
감을 숫자로 — 그날을 네 상황 중 하나로 분류하고, daybot/sbot이 그날 판 거래를 상황별로
나눠 승률·PF를 비교한다. (cbot/코인 쪽 위험회피는 오퍼스가 별도로 다룸 — 여기선 KR 주식만.)

분류 기준(기존에 이미 쓰는 지표 재사용, 새 지표 안 만듦):
  투매   : 그날 20일 신저가 비율(`bottom_signal.new_low_share`) >= 40%  (capitulation_sector.py와 동일 기준)
  하락장 : 투매가 아니고 20일선 위 종목 비율(`market_regime`) < 35%
  박스권 : 투매가 아니고 35% <= 비율 < 65%
  상승장 : 투매가 아니고 비율 >= 65%
한계: 경계값(35/65)은 지금까지 관측된 국면(15~60%대) 범위 안에서 50% 기준선을 대칭으로 넓힌
  것 — 상승장 표본이 더 쌓이면 재검토 필요. daybot은 이력이 짧아(10-03~) 표본 적을 수 있음.
실행:  python backtest/situation_classifier.py [--days 300]
"""
import os
import sys
import sqlite3

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in ("backtest", "core"):
    sys.path.insert(0, os.path.join(BASE, _d))
import swing_backtest as sb     # noqa: E402
import rebound_backtest as rb   # noqa: E402
import bottom_signal as bs      # noqa: E402
import market_regime as mr      # noqa: E402

CAP_NL = 40.0
DOWN_MAX = 35.0
UP_MIN = 65.0
BOTS = {"daybot": os.path.join(BASE, "daybot_trade_history.db"),
        "sbot": os.path.join(BASE, "sbot_trade_history.db")}


def classify(breadth, new_low: float) -> str:
    """하루 (20일선 위 비율%, 20일 신저가 비율%) → 상승장/박스권/하락장/투매."""
    if new_low is not None and new_low >= CAP_NL:
        return "투매"
    if breadth is None:
        return "모름"
    if breadth < DOWN_MAX:
        return "하락장"
    if breadth < UP_MIN:
        return "박스권"
    return "상승장"


def history(days: int = 300) -> dict:
    """{date: 상황} — 리나 일봉 DB 기준 최근 days거래일."""
    daily = sb.load_daily(rb.THEME_DB, days)
    table = rb.day_table(daily)
    dates = sorted(table)
    breadth = mr.compute(mr.THEME_DB, dates)
    lows = bs.new_low_share(daily)
    return {d: classify(breadth.get(d, (None, 0))[0], lows.get(d)) for d in dates}


def load_sells(db: str, by: str = "sell") -> list:
    """[(date, profit_krw, profit_rate)] — 매도 완료 거래. daybot/sbot 로컬 DB엔 profit_krw가
    없어 (매도가-매수가)*수량으로 계산."""
    if not os.path.exists(db):
        return []
    conn = sqlite3.connect(db)
    try:
        col = "buy_time" if by == "buy" else "sell_time"
        rows = conn.execute(f"SELECT substr({col},1,10), buy_price, sell_price, qty, profit_rate "
                            "FROM trades WHERE sell_time IS NOT NULL").fetchall()
    finally:
        conn.close()
    return [(d, (sp - bp) * qty if bp and sp and qty else 0.0, rate) for d, bp, sp, qty, rate in rows]


def pnl_stats(rows: list) -> dict:
    if not rows:
        return {"n": 0, "win_rate": 0.0, "pf": 0.0, "net": 0.0}
    n = len(rows)
    wins = sum(1 for r in rows if r[1] and r[1] > 0)
    gain = sum(k for _, k, _ in rows if k and k > 0)
    loss = -sum(k for _, k, _ in rows if k and k < 0)
    return {"n": n, "win_rate": wins / n * 100, "pf": (gain / loss) if loss else float("inf"),
            "net": sum(k or 0 for _, k, _ in rows)}


def prev_day_labels(labels: dict) -> dict:
    """{날짜: 전 거래일 상황} — 매수 규칙은 아침에 전 거래일 종가 국면으로 판단하므로(market_regime.latest),
    '산 날' 기준 분류는 그 전날 상황을 붙인다. (★ 10-17 오퍼스: 판 날 기준만으론 매수 쉬기 규칙 근거가 안 됨)"""
    ds = sorted(labels)
    return {d: labels[ds[i - 1]] for i, d in enumerate(ds) if i}


def by_situation(sells: list, labels: dict) -> dict:
    out = {}
    for s in sells:
        lab = labels.get(s[0], "모름")
        out.setdefault(lab, []).append(s)
    return {lab: pnl_stats(rows) for lab, rows in out.items()}


def report(days: int = 300) -> str:
    labels = history(days)
    order = ["상승장", "박스권", "하락장", "투매", "모름"]
    L = [f"🧭 상황 판별기 — 최근 {len(labels)}거래일 분류: "
         + " · ".join(f"{k} {sum(1 for v in labels.values() if v == k)}일" for k in order)]
    for bot, db in BOTS.items():
        for by, labs, title in (("sell", labels, "판 날 상황"), ("buy", prev_day_labels(labels), "산 날 상황(전날 종가 기준)")):
            sells = load_sells(db, by)
            stat = by_situation(sells, labs)
            L.append(f"\n■ {bot} — {title} (매도 {len(sells)}건)")
            for lab in order:
                s = stat.get(lab)
                if not s or not s["n"]:
                    continue
                L.append(f"   {lab:<5} {s['n']:>3}건 · 승률{s['win_rate']:4.0f}% · PF{s['pf']:5.2f} · "
                         f"순손익{s['net']:+,.0f}원")
    return "\n".join(L)


if __name__ == "__main__":
    a = sys.argv[1:]
    d = int(a[a.index("--days") + 1]) if "--days" in a else 300
    print(report(d))
