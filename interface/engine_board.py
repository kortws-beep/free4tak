"""
engine_board.py — 우리 엔진 대항전 (대시보드 /bots 세 번째 탭, 2026-10-24 대장 요청)
================================================================
[이 파일이 하는 일 — 비개발자용 설명]
대장이 본 유튜브 'AI 자동매매 24시간 LIVE' 화면의 "오늘 AI 알고리즘 엔진 대항전"(전략별 성적 줄 세우기)을 우리 식으로.
봇마다 '어떤 소스로 사서 / 어떤 이유로 팔아서' 얼마 벌고 잃었는지를 한 줄씩 줄 세움:
  sbot   — 산 곳(매수 태그): NEW · SUB · S7 · 대장 수동 …
  daybot — 산 곳(후보 순위): 겹침 · 주도주 · 090930 · 단타000 …
  cbot   — 판 이유: 익절 · 트레일링 · 보호선 · 급락감지 · 손절
  리나   — 등록 해제 때 남긴 수익률(수량 기록이 없어 금액은 없음)
기간: 오늘 / 이번 주(월~) / 이번 달. 금액은 매수·매도가 차이 × 수량(수수료 전, cbot은 봇이 기록한 손익).
"""
import os
import sqlite3
import datetime

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DBS = {"sbot": os.path.join(_BASE, "sbot_trade_history.db"), "daybot": os.path.join(_BASE, "daybot_trade_history.db"),
       "cbot": os.path.join(_BASE, "cbot_trade_history.db"),
       "lina": os.path.join(_BASE, "lina_bot", "three_month_leader_log.db")}
BOTS = {"sbot": "🤖 sbot", "daybot": "⚡ daybot", "cbot": "🪙 cbot", "lina": "🌸 리나"}
SBOT_TAGS = {"expert": "NEW 관심그룹", "sub": "SUB 고정석", "s7": "S7 대형주 급락", "trend": "추세(예전 소스)", "수동": "대장 수동"}
DAYBOT_TAGS = [("dip_leader", "🕯️ 음봉 엔진(급락 주도주)"), ("overlap", "겹침(2개 이상)"), ("090930", "090930타점"), ("단타000", "단타000"), ("주도주", "주도주검색식3"),
               ("섹터", "섹터"), ("scout", "스카우트"), ("fallback", "폴백"), ("수동", "대장 수동")]
CBOT_REASONS = [("보호선", "보호선"), ("급락감지", "급락감지"), ("트레일링", "트레일링"), ("익절", "익절"), ("목표", "익절"),
                ("손절", "손절"), ("수동", "수동 매도")]


def period_start(period: str, today: datetime.date) -> str:
    if period == "day":
        d = today
    elif period == "month":
        d = today.replace(day=1)
    else:
        d = today - datetime.timedelta(days=today.weekday())
    return d.isoformat()


def daybot_engine(tag: str) -> str:
    t = tag or ""
    return next((name for key, name in DAYBOT_TAGS if key in t), t or "미분류")


def cbot_engine(reason: str) -> str:
    r = reason or ""
    return next((name for key, name in CBOT_REASONS if key in r), "기타")


def _rows(db: str, sql: str, args: tuple) -> list:
    if not os.path.exists(db):
        return []
    conn = sqlite3.connect(db, timeout=5)
    try:
        return conn.execute(sql, args).fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


def collect(since: str, dbs: dict = None) -> list:
    """[(bot, engine, 손익원 or None, 수익률%)] — since(YYYY-MM-DD) 이후 매도된 것."""
    dbs = dbs or DBS
    out = []
    for tag, b, s, q, rate in _rows(dbs["sbot"], "SELECT buy_tag, buy_price, sell_price, qty, profit_rate FROM trades "
                                                  "WHERE sell_price IS NOT NULL AND sell_time >= ?", (since,)):
        out.append(("sbot", SBOT_TAGS.get(tag or "", tag or "미분류"), (s - b) * (q or 0) if b and s else None, rate))
    for tag, b, s, q, rate in _rows(dbs["daybot"], "SELECT buy_tag, buy_price, sell_price, qty, profit_rate FROM trades "
                                                    "WHERE sell_price IS NOT NULL AND sell_time >= ?", (since,)):
        out.append(("daybot", daybot_engine(tag), (s - b) * (q or 0) if b and s else None, rate))
    for reason, krw, rate in _rows(dbs["cbot"], "SELECT sell_reason, profit_krw, profit_rate FROM trades "
                                                "WHERE sell_time >= ?", (since,)):
        out.append(("cbot", cbot_engine(reason), krw, rate))
    # ★ 10-29 대장 — 눌림목 주머니(ETH·XRP·SOL 20일 기한청산)도 cbot 성적에
    for krw, rate in _rows(dbs["cbot"], "SELECT profit_krw, profit_rate FROM dip_trades WHERE sell_time >= ?", (since,)):
        out.append(("cbot", "🪤 눌림목 주머니", krw, rate))
    for (rate,) in _rows(dbs["lina"], "SELECT profit_rate FROM manual_watch_log WHERE event='deregister' "
                                      "AND profit_rate IS NOT NULL AND ts >= ?", (since,)):
        out.append(("lina", "등록 해제(매도가 넣은 것만·수익률)", None, rate))
    return out


def board(period: str = "week", today: datetime.date = None, dbs: dict = None) -> dict:
    today = today or datetime.date.today()
    since = period_start(period, today)
    acc = {}
    for bot, eng, krw, rate in collect(since, dbs):
        a = acc.setdefault((bot, eng), {"bot": bot, "bot_name": BOTS[bot], "engine": eng, "n": 0, "wins": 0,
                                        "krw": None, "rates": []})
        a["n"] += 1
        if krw is not None:
            a["krw"] = (a["krw"] or 0) + krw
        if rate is not None:
            a["rates"].append(rate)
        if (krw if krw is not None else (rate or 0)) > 0:
            a["wins"] += 1
    rows = []
    for a in acc.values():
        rates = a.pop("rates")
        a["avg"] = sum(rates) / len(rates) if rates else None
        rows.append(a)
    rows.sort(key=lambda r: -(r["krw"] if r["krw"] is not None else -1e15))
    totals = {}
    for r in rows:
        if r["krw"] is not None:
            totals[r["bot"]] = totals.get(r["bot"], 0) + r["krw"]
    return {"period": period, "since": since, "rows": rows, "totals": totals}
