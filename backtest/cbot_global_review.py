"""
cbot_global_review.py — 해외 코인 정보(펀딩비·김치 프리미엄 등)로 cbot 실제 매수 성적 나눠 보기 (2026-10-18)
================================================================
[이 파일이 하는 일 — 비개발자용 설명]
업비트 가격과 '다른 종류' 정보가 cbot 손실 매수를 가려내는지 본다. intelligence/coin_global.py가 쌓은
backtestc/coin_global.db를 읽어, cbot 실제 매수(수동 제외, 다 팔린 것)마다 산 순간의 값을 붙인다.
  · 펀딩비(비트 / 4코인 평균, 8시간 기준 %) — 높을수록 선물 롱 과열
  · 김치 프리미엄(%) · 24시간 변화(%p) · 테더 프리미엄(%) — 한국 쪽 과열/공포
  · (30일치뿐) 비트 미결제약정 24시간 변화(%) · 전체 계정 롱 비중 — 9월 하순 이후 매수만
기준선을 임의로 고르지 않고 값을 3등분(낮음·중간·높음)해 구간별 성적을 본 뒤, "높은 1/3 쉬기"·"낮은 1/3 쉬기"
규칙 효과를 앞·뒤 절반으로 나눠 확인(둘 다 +면 ✅). 주문·저장 없음.
실행:  python backtest/cbot_global_review.py [최근 N일, 기본 120]
"""
import os
import sys
import sqlite3
import datetime
from bisect import bisect_right

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cbot_crash_review import _find   # noqa: E402
from cbot_regime_review import load_trades, stats   # noqa: E402

GDB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backtestc", "coin_global.db")
SYMS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]
H = datetime.timedelta(hours=1)


class Series:
    def __init__(self, rows, lag=datetime.timedelta(0)):
        """rows=[(ts, value)] — lag: 봉이 끝나야 알 수 있는 값이면 봉 길이만큼(시각은 봉 시작)."""
        rows = sorted((datetime.datetime.fromisoformat(t) + lag, v) for t, v in rows if v is not None)
        self.t = [a for a, _ in rows]
        self.v = [b for _, b in rows]

    def at(self, t, back=datetime.timedelta(0), stale=datetime.timedelta(hours=9)):
        q = t - back
        i = bisect_right(self.t, q) - 1
        return None if i < 0 or q - self.t[i] > stale else self.v[i]


def load_series(conn) -> dict:
    q = lambda sql, *a: conn.execute(sql, a).fetchall()
    fund = {s: Series(q("SELECT ts, rate*100 FROM funding WHERE symbol=?", s)) for s in SYMS}
    return {
        "fund": fund,
        "kimp": Series(q("SELECT ts, premium FROM kimp"), H),
        "usdt": Series(q("SELECT ts, usdt_premium FROM kimp"), H),
        "oi": Series(q("SELECT ts, oi FROM oi WHERE symbol='BTCUSDT'"), H),
        "long": Series(q("SELECT ts, long_ratio*100 FROM ls_global WHERE symbol='BTCUSDT'"), H),
    }


def enrich(trades: list, S: dict) -> list:
    day = datetime.timedelta(hours=24)
    for x in trades:
        t = x["buy_time"]
        f = [S["fund"][s].at(t) for s in SYMS]
        x["fund_btc"] = f[0]
        x["fund_avg"] = None if None in f else sum(f) / 4
        x["kimp"] = S["kimp"].at(t, stale=3 * H)
        k0 = S["kimp"].at(t, day, stale=3 * H)
        x["kimp_d"] = None if x["kimp"] is None or k0 is None else x["kimp"] - k0
        x["usdt"] = S["usdt"].at(t, stale=3 * H)
        o, o0 = S["oi"].at(t, stale=3 * H), S["oi"].at(t, day, stale=3 * H)
        x["oi_d"] = None if o is None or o0 is None else (o / o0 - 1) * 100
        x["long"] = S["long"].at(t, stale=3 * H)
    return trades


IND = [("펀딩비 비트(%/8h)", "fund_btc", "{:+.4f}"), ("펀딩비 4코인 평균", "fund_avg", "{:+.4f}"),
       ("김치 프리미엄(%)", "kimp", "{:+.2f}"), ("김프 24시간 변화(%p)", "kimp_d", "{:+.2f}"),
       ("테더 프리미엄(%)", "usdt", "{:+.2f}"), ("비트 미결제약정 24h(%)", "oi_d", "{:+.1f}"),
       ("비트 롱 계정 비중(%)", "long", "{:.1f}")]


def report(trades: list, days: int) -> str:
    ts = sorted(trades, key=lambda x: x["buy_time"])
    half = ts[len(ts) // 2]["buy_time"]
    L = [f"🌍 cbot × 해외 코인 정보 — 최근 {days}일 끝난 매수 {len(trades)}건 · {stats(trades)} · 앞뒤 기준 {half:%m-%d}"]
    for name, k, fmt in IND:
        xs = sorted([x for x in trades if x[k] is not None], key=lambda x: x[k])
        L.append(f"■ {name} — 값 있는 매수 {len(xs)}건")
        if len(xs) < 15:
            L.append("   표본 부족(15건 미만) — 더 쌓인 뒤 보기")
            continue
        cut = [xs[len(xs) // 3][k], xs[2 * len(xs) // 3][k]]
        parts = [("낮은 1/3", [x for x in xs if x[k] < cut[0]]),
                 ("중간", [x for x in xs if cut[0] <= x[k] < cut[1]]),
                 ("높은 1/3", [x for x in xs if x[k] >= cut[1]])]
        L.append(f"   경계 {fmt.format(cut[0])} / {fmt.format(cut[1])}")
        for nm, g in parts:
            L.append(f"   {nm:<6} {stats(g)}")
        for nm, g in (parts[2], parts[0]):
            a = -sum(x["krw"] for x in g if x["buy_time"] < half)
            b = -sum(x["krw"] for x in g if x["buy_time"] >= half)
            only = len({x["buy_time"] >= half for x in g}) < 2
            tag = "➖ 한쪽 기간뿐" if only else "✅" if a > 0 and b > 0 else "❌"
            L.append(f"   → '{nm} 쉬기' 효과 {(a + b) / 1e4:+.1f}만 (앞 {a / 1e4:+.1f} · 뒤 {b / 1e4:+.1f}) {tag}")
    L.append("   ※ 지표 7개 × 규칙 2개 = 14번 찔러보는 셈 — ✅가 1~2개면 우연일 수 있음. 실전 반영 전 다른 기간으로 재확인")
    return "\n".join(L)


if __name__ == "__main__":
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 120
    trades = load_trades(_find("cbot_trade_history.db"), days)
    conn = sqlite3.connect(GDB)
    print(report(enrich(trades, load_series(conn)), days))
