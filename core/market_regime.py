"""
market_regime.py — 시장 국면(20일선 위 종목 비율) (2026-10-08)
================================================================
[이 파일이 하는 일 — 비개발자용 설명]

스윙 백테스트(backtest/swing_backtest.py, 1년 2321신호) 결과: 그날 전 종목 중 20일선 위에
있는 종목 비율이 50% 미만이면 추격매수가 단타·스윙 모두 본전~손실, 50% 이상이면 이익
(스윙 PF 1.39~1.49, NEW 그룹 스윙 2.7~3.6). 6월 이후 손실 대부분이 50% 아래 장.
  → 대장 결정: 1) 매일 계산해 리나 아침 브리핑에 한 줄  2) 50% 미만이면 데이봇 매수금 절반

계산: 리나 일봉 DB(lina_bot/kr_theme_finance.db) 전 종목의 마지막 거래일 종가 vs 직전 20일
평균. 일봉 수집은 15:40 이후에만 그날 봉을 넣으므로, 장중·아침엔 "전 거래일 종가 기준".
기록: 리나 로그 DB(three_month_leader_log.db) market_regime 표 — 날짜별 한 줄, 다시 계산 안 함.
실행:  python core/market_regime.py          (최신 계산·기록 + 최근 10일)
       python core/market_regime.py 60       (최근 60거래일 다시 계산해 채움)
"""
import os
import sqlite3

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
THEME_DB = os.path.join(_BASE, "lina_bot", "kr_theme_finance.db")
LOG_DB = os.path.join(_BASE, "lina_bot", "three_month_leader_log.db")
MA = 20
STRONG_PCT = 50.0          # 이 이상이면 추격 유리
MIN_STOCKS = 50            # 계산에 쓴 종목이 이보다 적으면(수집 덜 된 날) 기록 안 함


def compute(theme_db: str = THEME_DB, dates: list = None, ma: int = MA) -> dict:
    """{날짜: (비율%, 종목수)} — dates 생략 시 마지막 거래일 하나."""
    conn = sqlite3.connect(theme_db)
    try:
        all_dates = [d for (d,) in conn.execute("SELECT DISTINCT date FROM kr_stock_daily_data ORDER BY date")]
        if not all_dates:
            return {}
        want = dates or all_dates[-1:]
        lo = all_dates[max(0, all_dates.index(min(want)) - ma)] if min(want) in all_dates else all_dates[0]
        rows = conn.execute("SELECT stock_name, date, close_price FROM kr_stock_daily_data "
                            "WHERE date >= ? AND date <= ? ORDER BY date", (lo, max(want))).fetchall()
    finally:
        conn.close()
    series = {}
    for name, d, c in rows:
        if c:
            series.setdefault(name, []).append((d, c))
    up, tot = {}, {}
    for s in series.values():
        for i in range(ma, len(s)):
            d = s[i][0]
            if d not in want:
                continue
            tot[d] = tot.get(d, 0) + 1
            if s[i][1] > sum(c for _, c in s[i - ma:i]) / ma:
                up[d] = up.get(d, 0) + 1
    return {d: (up.get(d, 0) / n * 100, n) for d, n in tot.items() if n >= MIN_STOCKS}


def _table(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS market_regime (date TEXT PRIMARY KEY, breadth REAL, n INTEGER)")


def save(res: dict, log_db: str = LOG_DB) -> None:
    conn = sqlite3.connect(log_db, timeout=10)
    try:
        _table(conn)
        conn.executemany("INSERT OR REPLACE INTO market_regime VALUES (?,?,?)",
                         [(d, round(p, 2), n) for d, (p, n) in res.items()])
        conn.commit()
    finally:
        conn.close()


def history(n: int = 10, log_db: str = LOG_DB) -> list:
    """[(date, breadth)] 최신 n개, 오래된→최신."""
    try:
        conn = sqlite3.connect(log_db, timeout=10)
        try:
            rows = conn.execute("SELECT date, breadth FROM market_regime ORDER BY date DESC LIMIT ?",
                                (n,)).fetchall()
        finally:
            conn.close()
        return rows[::-1]
    except sqlite3.OperationalError:
        return []


def latest(theme_db: str = THEME_DB, log_db: str = LOG_DB):
    """(date, 비율%) — 기록에 최신 거래일이 없으면 계산해 기록. 실패하면 None."""
    try:
        conn = sqlite3.connect(theme_db)
        try:
            last = conn.execute("SELECT MAX(date) FROM kr_stock_daily_data").fetchone()[0]
        finally:
            conn.close()
        h = history(1, log_db)
        if h and h[-1][0] == last:
            return h[-1]
        res = compute(theme_db)
        if res:
            save(res, log_db)
            d = max(res)
            return d, res[d][0]
        return h[-1] if h else None
    except Exception as e:
        print(f"⚠️ 시장 국면 계산 실패: {e}")
        return None


def is_strong(breadth) -> bool:
    return breadth is not None and breadth >= STRONG_PCT


def format_line(theme_db: str = THEME_DB, log_db: str = LOG_DB) -> str:
    """브리핑용 한 줄."""
    cur = latest(theme_db, log_db)
    if not cur:
        return "📏 시장 국면: 계산 불가(일봉 DB 확인 필요)"
    d, p = cur
    trend = " → ".join(f"{b:.0f}" for _, b in history(5, log_db))
    mood = ("추격 유리 — 데이봇 정상 매수" if is_strong(p)
            else "추격 불리 — 데이봇 매수금 절반, 스윙·추격은 보수적으로")
    return f"📏 시장 국면: 20일선 위 종목 {p:.0f}% ({d[5:]} 종가, 최근 5일 {trend}) — {mood}"


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1].isdigit():
        conn = sqlite3.connect(THEME_DB)
        try:
            ds = [d for (d,) in conn.execute("SELECT DISTINCT date FROM kr_stock_daily_data "
                                             "ORDER BY date DESC LIMIT ?", (int(sys.argv[1]),))]
        finally:
            conn.close()
        res = compute(THEME_DB, sorted(ds))
        save(res)
        print(f"✅ {len(res)}거래일 계산·기록")
    print(format_line())
    for d, b in history(10):
        print(f"   {d} {b:5.1f}% {'█' * int(b // 5)}")
