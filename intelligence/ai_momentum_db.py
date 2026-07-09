"""
ai_momentum_db.py — AI 모멘텀 스캐너 픽 저장 및 사후검증
─────────────────────────────────────────────────────────────
하루 2회(아침/오후) 로컬 AI가 텔레그램/뉴스/쏠림지수/섹터모니터 등을
종합해서 뽑은 종목 2개씩을 저장하고, 생쇼(sshow_db.py)와 동일한
7/14일 역일 체크인 방식으로 사후검증한다.

★ 관찰 전용 — sbot/sbo2 스코어링에는 연결하지 않는다. 생쇼처럼 일정
기간 데이터가 쌓인 뒤 적중률을 보고 연결 여부를 판단한다.

[테이블 구조]
  momentum_picks
    - date/session   : 수집일 + 'am'|'pm'
    - stock_name/code: AI가 지목한 종목명/코드
    - reasoning      : AI가 댄 모멘텀 근거
    - buy/stop/tgt   : ATR 기반 매수가/손절가/목표가 (호출자가 계산해서 전달)
    - consensus_*    : 컨센서스 보강 정보 (선택)
    - result         : 'pending'|'hit'|'stop'|'hold'
    - last_checkin   : 마지막 체크인 일수 (0/7/14)
"""

import os
import sqlite3
import datetime

_BASE    = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ai_momentum_picks.db")
FINANCE_DB_PATH = os.path.join(_BASE, "lina_bot", "kr_theme_finance.db")

KEEP_DAYS         = 18   # sshow_db.py와 동일 — 14일 체크인 + 여유
RESULT_CHECK_DAYS = 14   # 최종 판정까지의 역일(달력일) 수
CHECKIN_DAYS      = [7, 14]


# ══════════════════════════════════════════════════════════════
# DB 초기화
# ══════════════════════════════════════════════════════════════

def init_db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS momentum_picks (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            date             TEXT    NOT NULL,
            session          TEXT    NOT NULL,   -- 'am' | 'pm'
            stock_name       TEXT    NOT NULL,
            code             TEXT    DEFAULT '',
            reasoning        TEXT    DEFAULT '',
            buy_price        REAL    DEFAULT 0,
            stop_price       REAL    DEFAULT 0,
            tgt_price        REAL    DEFAULT 0,
            consensus_bonus  INTEGER DEFAULT 0,
            consensus_reason TEXT    DEFAULT '',
            result           TEXT    DEFAULT 'pending',
            result_date      TEXT    DEFAULT '',
            result_price     REAL    DEFAULT 0,
            last_checkin     INTEGER DEFAULT 0,
            created_at       TEXT    DEFAULT (datetime('now','localtime')),
            UNIQUE(date, session, stock_name)
        )
    """)
    conn.commit()
    conn.close()


# ══════════════════════════════════════════════════════════════
# 저장
# ══════════════════════════════════════════════════════════════

def save_picks(date: str, session: str, picks: list) -> int:
    """
    picks: [{"stock_name":..., "code":..., "reasoning":...,
             "buy_price":..., "stop_price":..., "tgt_price":...,
             "consensus_bonus":0, "consensus_reason":""}, ...]
    반환: 저장된 건수
    """
    init_db()
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")

    saved = 0
    for p in picks:
        name = p.get("stock_name", "").strip()
        if not name or len(name) < 2:
            continue
        try:
            conn.execute("""
                INSERT OR IGNORE INTO momentum_picks
                    (date, session, stock_name, code, reasoning,
                     buy_price, stop_price, tgt_price,
                     consensus_bonus, consensus_reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                date, session, name, p.get("code", ""), p.get("reasoning", "")[:300],
                p.get("buy_price", 0), p.get("stop_price", 0), p.get("tgt_price", 0),
                p.get("consensus_bonus", 0), p.get("consensus_reason", "")[:200],
            ))
            if conn.execute("SELECT changes()").fetchone()[0] > 0:
                saved += 1
                print(f"   💾 모멘텀픽 저장[{session}]: {name} "
                      f"매수:{p.get('buy_price', 0):,.0f} "
                      f"손절:{p.get('stop_price', 0):,.0f} "
                      f"목표:{p.get('tgt_price', 0):,.0f}")
        except Exception as e:
            print(f"⚠️ 모멘텀픽 저장 오류 {name}: {e}")

    conn.commit()
    cleanup_old_picks(conn)
    conn.close()

    if saved:
        print(f"✅ 모멘텀픽 DB 저장: {saved}건 ({date} {session})")
    return saved


def cleanup_old_picks(conn: sqlite3.Connection = None) -> int:
    own_conn = conn is None
    if own_conn:
        init_db()
        conn = sqlite3.connect(DB_PATH, timeout=10)

    cutoff = (datetime.date.today() -
              datetime.timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
    deleted = conn.execute("DELETE FROM momentum_picks WHERE date < ?", (cutoff,)).rowcount
    conn.commit()

    if own_conn:
        conn.close()
    if deleted:
        print(f"🗑️ 모멘텀픽 오래된 데이터 정리: {deleted}건 ({KEEP_DAYS}일 초과)")
    return deleted


# ══════════════════════════════════════════════════════════════
# 가격 이력 조회 (sshow_db.py와 동일 방식 — exact match만 사용)
# ══════════════════════════════════════════════════════════════

def _get_price_history(stock_name: str, since_date: str) -> list:
    if not os.path.exists(FINANCE_DB_PATH):
        return []
    try:
        conn = sqlite3.connect(FINANCE_DB_PATH, timeout=5)
        rows = conn.execute("""
            SELECT date, close_price FROM kr_stock_daily_data
            WHERE stock_name = ? AND date >= ?
            ORDER BY date ASC
        """, (stock_name, since_date)).fetchall()
        conn.close()
        return rows
    except Exception as e:
        print(f"⚠️ 가격 조회 오류 {stock_name}: {e}")
        return []


# ══════════════════════════════════════════════════════════════
# 결과 자동 판정 (sshow_db.py의 check_and_update_results 포팅)
# ══════════════════════════════════════════════════════════════

def check_and_update_results(force_today: str = None) -> list:
    """
    모멘텀픽 결과를 7/14 역일(달력일) 체크인 시점마다 점검.
    sshow_db.py의 동일 함수와 완전히 같은 판정 로직(조기 hit/stop,
    14일차 hold 마감, last_checkin 기반 중복 알림 방지).
    """
    init_db()
    today = force_today or datetime.date.today().strftime("%Y-%m-%d")

    conn = sqlite3.connect(DB_PATH, timeout=10)
    pending = conn.execute("""
        SELECT id, date, session, stock_name, buy_price, stop_price, tgt_price, last_checkin
        FROM momentum_picks WHERE result = 'pending'
    """).fetchall()

    notifications = []

    for pick_id, pick_date, session, name, buy_p, stop_p, tgt_p, last_checkin in pending:
        if not (buy_p > 0 and stop_p > 0 and tgt_p > 0):
            continue

        history = _get_price_history(name, pick_date)
        if not history:
            continue
        history = [(d, p) for d, p in history if d > pick_date]
        if not history:
            continue

        days_elapsed = (datetime.date.fromisoformat(today) -
                        datetime.date.fromisoformat(pick_date)).days
        latest_date, latest_price = history[-1]

        result, result_date, result_price = None, None, None
        for d, p in history:
            if p >= tgt_p:
                result, result_date, result_price = "hit", d, p
                break
            if p <= stop_p:
                result, result_date, result_price = "stop", d, p
                break

        if result:
            conn.execute("""
                UPDATE momentum_picks
                SET result = ?, result_date = ?, result_price = ?
                WHERE id = ?
            """, (result, result_date, result_price, pick_id))
            pct = (result_price - buy_p) / buy_p * 100
            kind = "hit" if result == "hit" else "stop"
            emoji = "🎯" if kind == "hit" else "🛑"
            label = "목표가 도달" if kind == "hit" else "손절가 터치"
            notifications.append({
                "name": name, "session": session, "stage": days_elapsed, "kind": kind,
                "text": f"{emoji} [{session}] {name} {label}! {result_price:,.0f}원 "
                        f"({pct:+.1f}%, 픽일:{pick_date})",
            })
            print(f"   📊 모멘텀픽 결과판정: {name} ({pick_date}) → {result} "
                  f"({result_price:,.0f}원, {result_date})")
            continue

        reached_stage = None
        for stage in CHECKIN_DAYS:
            if days_elapsed >= stage and last_checkin < stage:
                reached_stage = stage
                break

        if reached_stage is None:
            continue

        pct = (latest_price - buy_p) / buy_p * 100
        dist_to_tgt = (tgt_p - latest_price) / buy_p * 100
        dist_to_stop = (latest_price - stop_p) / buy_p * 100

        if reached_stage == RESULT_CHECK_DAYS:
            conn.execute("""
                UPDATE momentum_picks
                SET result='hold', result_date=?, result_price=?, last_checkin=?
                WHERE id=?
            """, (latest_date, latest_price, reached_stage, pick_id))
            notifications.append({
                "name": name, "session": session, "stage": reached_stage, "kind": "hold",
                "text": f"⏱️ [{session}] {name} {reached_stage}일 경과, 보합 마감 "
                        f"({latest_price:,.0f}원, {pct:+.1f}%, 픽일:{pick_date})",
            })
        else:
            conn.execute("""
                UPDATE momentum_picks SET last_checkin=? WHERE id=?
            """, (reached_stage, pick_id))
            closer = "목표가" if dist_to_tgt < dist_to_stop else "손절가"
            notifications.append({
                "name": name, "session": session, "stage": reached_stage, "kind": "progress",
                "text": f"📍 [{session}] {name} {reached_stage}일 경과 — "
                        f"현재 {latest_price:,.0f}원({pct:+.1f}%), "
                        f"{closer} 쪽에 더 가까움 (픽일:{pick_date})",
            })

    conn.commit()
    conn.close()

    if notifications:
        print(f"✅ 모멘텀픽 체크인 알림 {len(notifications)}건 생성")
    return notifications


def get_pending_with_current_price(force_today: str = None) -> list:
    """
    미결(pending) 픽 전부에 대해 체크인 단계(7/14일) 도달 여부와 무관하게
    항상 "지금 기준" 현재가/수익률을 계산해서 반환 (sshow_db.py의 동일
    함수 포팅 — qwen 픽과 생쇼 픽의 현재 성과를 나란히 비교하기 위함).
    """
    init_db()
    today = force_today or datetime.date.today().strftime("%Y-%m-%d")
    today_d = datetime.date.fromisoformat(today)

    conn = sqlite3.connect(DB_PATH, timeout=10)
    rows = conn.execute("""
        SELECT date, session, stock_name, buy_price, stop_price, tgt_price
        FROM momentum_picks WHERE result='pending'
        ORDER BY date DESC
    """).fetchall()
    conn.close()

    result = []
    for pick_date, session, name, buy_p, stop_p, tgt_p in rows:
        days_elapsed = (today_d - datetime.date.fromisoformat(pick_date)).days

        history = _get_price_history(name, pick_date)
        history = [(d, p) for d, p in history if d >= pick_date]
        current_price = history[-1][1] if history else None
        current_pct = (
            round((current_price - buy_p) / buy_p * 100, 2)
            if (current_price and buy_p > 0) else None
        )

        if days_elapsed >= CHECKIN_DAYS[1]:
            checkin_label = f"{CHECKIN_DAYS[1]}일째(최종)"
        elif days_elapsed >= CHECKIN_DAYS[0]:
            checkin_label = f"{CHECKIN_DAYS[0]}일째 체크"
        else:
            checkin_label = f"D+{days_elapsed} ({CHECKIN_DAYS[0]}일째 전)"

        result.append({
            "date": pick_date, "session": session, "name": name,
            "buy_price": buy_p, "stop_price": stop_p, "tgt_price": tgt_p,
            "days_elapsed": days_elapsed,
            "current_price": current_price, "current_pct": current_pct,
            "checkin_label": checkin_label,
        })
    return result


# ══════════════════════════════════════════════════════════════
# 통계/조회
# ══════════════════════════════════════════════════════════════

def get_momentum_stats(days: int = 30) -> dict:
    """최근 N일간 판정 완료(hit/stop/hold) 적중률 통계 (sshow_db.py와 동일 방식)"""
    if not os.path.exists(DB_PATH):
        return {"total": 0, "hit": 0, "stop": 0, "hold": 0,
                "hit_rate": 0.0, "sample_size_ok": False}

    cutoff = (datetime.date.today() -
              datetime.timedelta(days=days)).strftime("%Y-%m-%d")
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        rows = conn.execute("""
            SELECT result, COUNT(*) FROM momentum_picks
            WHERE date >= ? AND result != 'pending'
            GROUP BY result
        """, (cutoff,)).fetchall()
        conn.close()
    except Exception as e:
        print(f"⚠️ 모멘텀픽 통계 조회 오류: {e}")
        return {"total": 0, "hit": 0, "stop": 0, "hold": 0,
                "hit_rate": 0.0, "sample_size_ok": False}

    counts = {"hit": 0, "stop": 0, "hold": 0}
    for result, cnt in rows:
        if result in counts:
            counts[result] = cnt

    total = sum(counts.values())
    decided = counts["hit"] + counts["stop"]
    hit_rate = (counts["hit"] / decided) if decided > 0 else 0.0

    return {
        "total": total,
        "hit": counts["hit"],
        "stop": counts["stop"],
        "hold": counts["hold"],
        "hit_rate": hit_rate,
        "sample_size_ok": total >= 20,
    }


def get_recent_picks(limit: int = 10) -> list:
    """최근 픽 목록 (!모멘텀 명령어용)"""
    init_db()
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        rows = conn.execute("""
            SELECT date, session, stock_name, reasoning, buy_price,
                   stop_price, tgt_price, result, result_price
            FROM momentum_picks
            ORDER BY date DESC, session DESC, id DESC
            LIMIT ?
        """, (limit,)).fetchall()
        conn.close()
    except Exception as e:
        print(f"⚠️ 모멘텀픽 조회 오류: {e}")
        return []

    return [
        {
            "date": r[0], "session": r[1], "name": r[2], "reasoning": r[3],
            "buy_price": r[4], "stop_price": r[5], "tgt_price": r[6],
            "result": r[7], "result_price": r[8],
        }
        for r in rows
    ]


if __name__ == "__main__":
    init_db()
    print("모멘텀픽 DB 초기화 완료:", DB_PATH)
    print(get_momentum_stats())
