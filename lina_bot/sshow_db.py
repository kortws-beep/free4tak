"""
sshow_db.py — 생쇼 공략주 DB 저장 및 조회
─────────────────────────────────────────────────────────────
매일 14:30 수집된 생쇼 공략주를 DB에 누적 저장
5영업일 (약 20종목) 풀로 tele_swing_analyzer에 제공

[테이블 구조]
  sshow_picks : 생쇼 공략주 이력
    - date       : 수집일 (YYYY-MM-DD)
    - stock_name : 종목명
    - buy_price  : 매수가 (파싱 성공시)
    - stop_price : 손절가 (파싱 성공시)
    - tgt_price  : 목표가 (파싱 성공시)
    - raw_text   : 원문
    - created_at : 저장시각
    - result        : 결과 판정 (2026-06-30 추가)
                       'pending'=미판정, 'hit'=목표가도달,
                       'stop'=손절가터치, 'hold'=기간만료(보합)
    - result_date   : 결과 판정일
    - result_price  : 판정 시점 종가
    - price_valid   : 가격 정합성 (1=정상, 0=비정상 — stop>=buy 등)

[적중률 통계]
  get_sshow_stats() — 최근 N일 result='hit'/'stop'/'hold' 집계
  check_and_update_results() — 5영업일 지난 pending 건을 자동 판정
  (매일 1회, kiki_briefing 등에서 호출 권장)
"""

import os
import re
import sqlite3
import datetime

BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
DB_PATH     = os.path.join(BASE_DIR, "sshow_picks.db")
KEEP_DAYS   = 25   # ★ 2026-06-30: lina_bot 자동알림을 제거하고 토요일
                    #   백테스터(run_sshow_backtest.py) 단독 실행으로 전환.
                    #   21일 체크인으로 모든 건이 확정되니, 그보다 살짝
                    #   여유 있는 25일을 보관 기준으로 — DB가 무한정
                    #   쌓이지 않게 확정된 건은 주기적으로 정리.
RESULT_CHECK_DAYS = 21   # 최종 판정까지의 역일(달력일) 수
CHECKIN_DAYS = [7, 14, 21]  # ★ 2026-06-30: 7/14/21 역일(달력일)마다 경과 알림
                             #   (7,14일=중간 경과, 21일=최종 확정)
                             #   거래일(영업일) 대신 역일 기준으로 변경 —
                             #   주말 끼는 것까지 합쳐 "3주" 개념을 그대로 유지


# ══════════════════════════════════════════════════════════════
# DB 초기화
# ══════════════════════════════════════════════════════════════

def init_db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sshow_picks (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            date       TEXT    NOT NULL,
            stock_name TEXT    NOT NULL,
            buy_price  REAL    DEFAULT 0,
            stop_price REAL    DEFAULT 0,
            tgt_price  REAL    DEFAULT 0,
            raw_text   TEXT    DEFAULT '',
            created_at TEXT    DEFAULT (datetime('now','localtime')),
            UNIQUE(date, stock_name)
        )
    """)
    # ★ 2026-06-30 추가 컬럼 — 기존 운영 DB에도 안전하게 적용되도록
    #   ALTER TABLE로 동적 추가 (이미 있으면 무시)
    for col, coltype in [
        ("result",       "TEXT DEFAULT 'pending'"),
        ("result_date",  "TEXT DEFAULT ''"),
        ("result_price", "REAL DEFAULT 0"),
        ("price_valid",  "INTEGER DEFAULT 1"),
        ("last_checkin", "INTEGER DEFAULT 0"),  # 마지막으로 알림 보낸 체크인 일수(0/5/10/15)
        # ★ 2026-06-30: buy_price/stop_price/tgt_price는 이제 ATR 기반
        #   재계산값을 기본 저장하고, mbn 원문 파싱값은 참고용으로 별도
        #   보존 (분할/파싱오류로 어긋나도 원본 비교/디버깅 가능하도록)
        ("raw_buy_price",  "REAL DEFAULT 0"),
        ("raw_stop_price", "REAL DEFAULT 0"),
        ("raw_tgt_price",  "REAL DEFAULT 0"),
        ("price_source",   "TEXT DEFAULT 'raw'"),  # 'atr'=재계산값 사용 / 'raw'=원문 파싱값 그대로
    ]:
        try:
            conn.execute(f"ALTER TABLE sshow_picks ADD COLUMN {col} {coltype}")
        except sqlite3.OperationalError:
            pass  # 이미 존재
    conn.commit()
    conn.close()


# ══════════════════════════════════════════════════════════════
# 파싱 헬퍼
# ══════════════════════════════════════════════════════════════

def _parse_price(text: str) -> float:
    """텍스트에서 숫자(원) 추출"""
    m = re.search(r'[\d,]+', text.replace(' ', ''))
    if m:
        try:
            return float(m.group().replace(',', ''))
        except Exception:
            pass
    return 0.0

def _parse_stock_name(text: str) -> str:
    """생쇼 텍스트에서 종목명 추출"""
    # "📌 종목명" 패턴
    m = re.search(r'📌\s*([가-힣A-Za-z0-9·\-&]+)', text)
    if m:
        return m.group(1).strip()
    # 첫 번째 한글 단어
    m = re.search(r'([가-힣]{2,10}(?:[A-Za-z0-9]*)?)', text)
    if m:
        return m.group(1).strip()
    return ""

def _parse_sshow_block(block: str) -> dict:
    """
    생쇼 텍스트 블록에서 종목명/매수가/손절가 파싱
    새 포맷:
      📌 현대건설기계(267270) [매수: 156,300원 / 목표: 172,000원 / 손절: 130,000원]
      [사유]: ...
    """
    result = {
        "stock_name": "",
        "buy_price":  0.0,
        "stop_price": 0.0,
        "tgt_price":  0.0,
        "raw_text":   block,
    }

    lines = [l.strip() for l in block.split('\n') if l.strip()]

    # 📌 줄과 다음 줄을 합쳐서 파싱 (종목명/코드/가격이 줄바꿈으로 분리될 수 있음)
    for i, line in enumerate(lines):
        if "📌" in line:
            # 다음 줄과 합치기
            combined = line
            if i + 1 < len(lines) and "📌" not in lines[i+1] and "[사유]" not in lines[i+1]:
                combined = line + " " + lines[i+1]

            # 종목명: 📌 다음, 괄호 또는 [ 앞까지
            m = re.search(r'📌\s*([가-힣A-Za-z0-9·\-&\s]+?)(?:\s*[\(\[])', combined)
            if m:
                result["stock_name"] = m.group(1).strip()
            else:
                m = re.search(r'📌\s*([가-힣A-Za-z0-9·\-&]+)', combined)
                if m:
                    result["stock_name"] = m.group(1).strip()

            # 매수가
            m = re.search(r'매수\s*:\s*([\d,]+)원', combined)
            if m:
                result["buy_price"] = float(m.group(1).replace(',', ''))

            # 목표가
            m = re.search(r'목표\s*:\s*([\d,]+)원', combined)
            if m:
                result["tgt_price"] = float(m.group(1).replace(',', ''))

            # 손절가
            m = re.search(r'손절\s*:\s*([\d,]+)원', combined)
            if m:
                result["stop_price"] = float(m.group(1).replace(',', ''))

    return result


# ══════════════════════════════════════════════════════════════
# 저장/조회 함수
# ══════════════════════════════════════════════════════════════

def save_sshow_picks(raw_text: str) -> int:
    """
    생쇼 raw_text 파싱 후 DB 저장
    새 포맷: "📌 종목명(코드) [매수: X원 / 목표: X원 / 손절: X원]\n  [사유]: ..."
    반환: 저장된 건수
    """
    init_db()

    today = datetime.date.today().strftime("%Y-%m-%d")
    saved = 0

    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")

    # 블록 분리 (📌 기준)
    blocks = [b.strip() for b in raw_text.split("\n\n") if b.strip()]

    for block in blocks:
        if "📌" not in block:
            continue

        parsed = _parse_sshow_block(block)
        name = parsed["stock_name"]
        if not name or len(name) < 2:
            continue

        # ★ 2026-06-30 추가: mbn 원문 매수가/목표가/손절가가 액면분할
        #   반영시차나 파싱오류로 실제가격과 5~7배씩 어긋나는 사고가
        #   발견됨(미코 등). kr_theme_finance.db의 실제 종가+ATR 기반으로
        #   재계산한 값을 우선 사용. 재계산 실패(데이터 없음 등) 시에만
        #   원문 파싱값으로 폴백. 원문값은 raw_* 컬럼에 항상 보존.
        raw_buy_p, raw_stop_p, raw_tgt_p = (
            parsed["buy_price"], parsed["stop_price"], parsed["tgt_price"])

        recalced = _recalc_prices_from_finance_db(name, today)
        if recalced:
            buy_p, stop_p, tgt_p = (
                recalced["buy_price"], recalced["stop_price"], recalced["tgt_price"])
            price_source = "atr"
        else:
            buy_p, stop_p, tgt_p = raw_buy_p, raw_stop_p, raw_tgt_p
            price_source = "raw"
            print(f"   ⚠️ {name} ATR 재계산 실패(가격데이터 없음) → 원문 파싱값 사용")

        # ★ 가격 정합성 검증 (손절가 < 매수가 < 목표가가 정상). ATR
        #   재계산값은 식 자체가 항상 정합하므로 사실상 raw 폴백 케이스만
        #   해당 — KT&G/엘앤에프처럼 손절가가 매수가보다 크게 파싱되는
        #   사례가 실제로 발견됨. 단정해서 버리지는 않되, price_valid=0으로
        #   표시해 통계 집계에서 자동 제외되도록 함.
        price_valid = 1
        if buy_p > 0 and stop_p > 0 and tgt_p > 0:
            if not (stop_p < buy_p < tgt_p):
                price_valid = 0
                print(f"   ⚠️ 가격 역전 감지: {name} 매수:{buy_p:,.0f} "
                      f"손절:{stop_p:,.0f} 목표:{tgt_p:,.0f} → 통계 제외 표시")

        # [사유] 추출
        reason = ""
        for line in block.split("\n"):
            if "[사유]" in line:
                reason = re.sub(r"\[사유\]\s*:?\s*", "", line).strip()
                break

        try:
            conn.execute("""
                INSERT OR IGNORE INTO sshow_picks
                    (date, stock_name, buy_price, stop_price, tgt_price,
                     raw_text, price_valid,
                     raw_buy_price, raw_stop_price, raw_tgt_price, price_source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (today, name, buy_p, stop_p, tgt_p,
                  reason[:300], price_valid,
                  raw_buy_p, raw_stop_p, raw_tgt_p, price_source))
            if conn.execute("SELECT changes()").fetchone()[0] > 0:
                saved += 1
                src_tag = "ATR재계산" if price_source == "atr" else "원문"
                print(f"   💾 생쇼 저장[{src_tag}]: {name} 매수:{buy_p:,.0f} "
                      f"손절:{stop_p:,.0f} 목표:{tgt_p:,.0f}")
        except Exception as e:
            print(f"⚠️ 생쇼 저장 오류 {name}: {e}")

    conn.commit()

    # 오래된 데이터 정리
    cleanup_old_picks(conn)
    conn.close()

    if saved > 0:
        print(f"✅ 생쇼 DB 저장: {saved}건 (날짜: {today})")

    return saved


def cleanup_old_picks(conn: sqlite3.Connection = None) -> int:
    """
    KEEP_DAYS(25일) 지난 추천 삭제. conn이 주어지면 그 커넥션 재사용,
    없으면 새로 열고 닫음. 백테스터(run_sshow_backtest.py)에서도
    독립적으로 호출 가능하도록 분리.
    반환: 삭제된 행 수
    """
    own_conn = conn is None
    if own_conn:
        init_db()
        conn = sqlite3.connect(DB_PATH, timeout=10)

    cutoff = (datetime.date.today() -
              datetime.timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
    deleted = conn.execute("DELETE FROM sshow_picks WHERE date < ?", (cutoff,)).rowcount
    conn.commit()

    if own_conn:
        conn.close()
    if deleted:
        print(f"🗑️ 생쇼 오래된 데이터 정리: {deleted}건 ({KEEP_DAYS}일 초과)")
    return deleted


# ══════════════════════════════════════════════════════════════
# 결과 자동 판정 (2026-06-30 추가)
# ══════════════════════════════════════════════════════════════

def _get_finance_db_path() -> str:
    """kr_theme_finance.db 경로 (BASE_DIR 기준, 같은 lina_bot 폴더)"""
    return os.path.join(BASE_DIR, "kr_theme_finance.db")


def _get_price_history(stock_name: str, since_date: str) -> list:
    """
    종목명으로 since_date 이후의 (date, close_price) 목록을 날짜순 반환.
    ★ exact match만 사용 — LIKE 매칭은 '삼성전자'가 '삼성전자우'까지
    잘못 잡는 문제가 있어 반드시 정확히 일치하는 stock_name만 조회.
    """
    finance_db = _get_finance_db_path()
    if not os.path.exists(finance_db):
        return []
    try:
        conn = sqlite3.connect(finance_db, timeout=5)
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
# ATR 기반 가격 재계산 (2026-06-30 추가)
# ══════════════════════════════════════════════════════════════
# 배경: mbn 원문에서 파싱한 buy_price/stop_price/tgt_price가 실제 가격과
# 5~7배씩 차이나는 사고가 발견됨 (미코 106,500원 vs 실제 20,950원 등).
# 원인은 액면분할/병합 반영 시차 또는 원문 파싱 오류로 추정되나, mbn
# 원문 형식이 항상 일정하지 않아 파싱만으로는 근본 해결이 어려움.
# 대신 우리가 이미 보유한 kr_theme_finance.db의 실제 종가(매수가 기준)와
# ATR 기반 손절/목표 계산식(sbot_strategy.calc_atr_levels와 동일 로직)을
# 사용해 가격을 재산출 — 데이터 오염에 흔들리지 않는 일관된 기준 확보.

ATR_STOP_MULT_SSHOW   = 2.0
ATR_TARGET_MULT_SSHOW = 3.0


def _get_ohlc(stock_name: str, end_date: str, days: int = 15) -> list:
    """end_date 이전(포함) days일치 (date, high, low, close)를 날짜 오름차순 반환"""
    finance_db = _get_finance_db_path()
    if not os.path.exists(finance_db):
        return []
    try:
        conn = sqlite3.connect(finance_db, timeout=5)
        rows = conn.execute("""
            SELECT date, high_price, low_price, close_price
            FROM kr_stock_daily_data
            WHERE stock_name = ? AND date <= ?
            ORDER BY date DESC LIMIT ?
        """, (stock_name, end_date, days)).fetchall()
        conn.close()
        return list(reversed(rows))  # 오름차순으로 반환
    except Exception as e:
        print(f"⚠️ OHLC 조회 오류 {stock_name}: {e}")
        return []


def _calc_atr_rate(ohlc: list, period: int = 14) -> float:
    """
    True Range 기반 ATR을 종가 대비 비율로 반환.
    ohlc: [(date, high, low, close), ...] 날짜 오름차순, 최소 2개 이상 필요.
    risk_manager.calc_atr_rate와 동일한 계산 방식.
    """
    if len(ohlc) < 2:
        return 0.0
    trs = []
    for i in range(1, len(ohlc)):
        _, high, low, close = ohlc[i]
        _, _, _, prev_close = ohlc[i - 1]
        if not (high and low and close and prev_close):
            continue
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)
    if not trs:
        return 0.0
    trs = trs[-period:]  # 최근 period개만 사용
    atr = sum(trs) / len(trs)
    last_close = ohlc[-1][3]
    return (atr / last_close) if last_close > 0 else 0.0


def _recalc_prices_from_finance_db(stock_name: str, pick_date: str) -> dict:
    """
    추천일(pick_date) 기준으로 kr_theme_finance.db의 실제 종가를 매수가로,
    ATR 기반 계산식으로 손절가/목표가를 재산출.
    실패(데이터 없음 등) 시 빈 dict 반환 — 호출부가 원문 파싱값으로 폴백.
    """
    ohlc = _get_ohlc(stock_name, pick_date, days=15)
    if not ohlc:
        return {}
    # pick_date 당일 종가를 매수가로 사용 (없으면 가장 가까운 직전 거래일)
    buy_price = None
    for d, h, l, c in reversed(ohlc):
        if d <= pick_date and c > 0:
            buy_price = c
            break
    if not buy_price:
        return {}

    atr_rate = _calc_atr_rate(ohlc)
    if atr_rate <= 0:
        return {}

    atr_val = buy_price * atr_rate
    stop_price = round(buy_price - atr_val * ATR_STOP_MULT_SSHOW, 0)
    tgt_price  = round(buy_price + atr_val * ATR_TARGET_MULT_SSHOW, 0)

    return {
        "buy_price": round(buy_price, 0),
        "stop_price": stop_price,
        "tgt_price": tgt_price,
        "atr_rate": atr_rate,
    }


def check_and_update_results(force_today: str = None) -> list:
    """
    생쇼 추천 결과를 7/14/21 역일(달력일) 체크인 시점마다 점검.

    동작:
      1. 모든 'pending' 건에 대해 목표가/손절가 조기 도달 여부를 확인
         → 도달했으면 즉시 result='hit'/'stop'으로 확정 (체크인 일수와 무관)
      2. 아직 미확정인 건 중, 추천일로부터 경과한 역일수가 정확히
         7/14/21에 막 도달한 건은 "체크인 알림" 대상으로 수집:
         - 7일/14일: 중간 경과 (목표가/손절가 중 어느 쪽에 더 가까운지)
         - 21일: 그래도 미확정이면 result='hold'로 최종 확정
      3. 같은 체크인 단계에 대해 중복 알림이 안 가도록 last_checkin 갱신

    price_valid=0(가격 역전 등 비정상)인 건은 판정/알림 대상에서 제외.

    반환: 알림으로 보낼 메시지 dict 리스트
      [{"name":..., "stage": 7|14|21, "kind": "hit"|"stop"|"progress"|"hold",
        "text": "..."}, ...]
    토요일 백테스터(run_sshow_backtest.py)에서 수동 실행 권장.
    """
    init_db()
    today = force_today or datetime.date.today().strftime("%Y-%m-%d")

    conn = sqlite3.connect(DB_PATH, timeout=10)
    pending = conn.execute("""
        SELECT id, date, stock_name, buy_price, stop_price, tgt_price, last_checkin
        FROM sshow_picks
        WHERE result = 'pending' AND price_valid = 1
    """).fetchall()

    notifications = []

    for pick_id, pick_date, name, buy_p, stop_p, tgt_p, last_checkin in pending:
        if not (buy_p > 0 and stop_p > 0 and tgt_p > 0):
            continue  # 가격 파싱 자체가 실패한 건 — 판정 불가, pending 유지

        history = _get_price_history(name, pick_date)
        if not history:
            continue  # 가격 데이터 없음 — 다음 기회에 재시도

        # 추천일 자체(같은 날 종가)는 매수 시점 기준이 아니므로 다음날부터 판정
        history = [(d, p) for d, p in history if d > pick_date]
        if not history:
            continue

        # ★ 2026-06-30: 거래일 수가 아닌 역일(달력일) 수로 경과 판단
        days_elapsed = (datetime.date.fromisoformat(today) -
                        datetime.date.fromisoformat(pick_date)).days
        latest_date, latest_price = history[-1]

        # ── 1. 조기 확정 체크 (목표가/손절가 도달은 언제든 발생 가능) ──
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
                UPDATE sshow_picks
                SET result = ?, result_date = ?, result_price = ?
                WHERE id = ?
            """, (result, result_date, result_price, pick_id))
            pct = (result_price - buy_p) / buy_p * 100
            kind = "hit" if result == "hit" else "stop"
            emoji = "🎯" if kind == "hit" else "🛑"
            label = "목표가 도달" if kind == "hit" else "손절가 터치"
            notifications.append({
                "name": name, "stage": days_elapsed, "kind": kind,
                "text": f"{emoji} {name} {label}! {result_price:,.0f}원 "
                        f"({pct:+.1f}%, 추천일:{pick_date})",
            })
            print(f"   📊 생쇼 결과판정: {name} ({pick_date}) → {result} "
                  f"({result_price:,.0f}원, {result_date})")
            continue  # 조기확정된 건은 체크인 알림 대상에서 제외

        # ── 2. 체크인 단계 도달 여부 (7/14/21 역일) ──
        # last_checkin보다 큰 체크인 단계 중, 이미 도달한 가장 가까운 단계를 찾음
        reached_stage = None
        for stage in CHECKIN_DAYS:
            if days_elapsed >= stage and last_checkin < stage:
                reached_stage = stage
                # 한 번에 여러 단계를 건너뛰지 않도록 가장 작은 새 단계만 사용
                break

        if reached_stage is None:
            continue  # 아직 다음 체크인 시점이 안 됨

        pct = (latest_price - buy_p) / buy_p * 100
        dist_to_tgt = (tgt_p - latest_price) / buy_p * 100
        dist_to_stop = (latest_price - stop_p) / buy_p * 100

        if reached_stage == RESULT_CHECK_DAYS:
            # 최종 체크인(21일) — 미확정이면 hold로 마감
            conn.execute("""
                UPDATE sshow_picks
                SET result='hold', result_date=?, result_price=?, last_checkin=?
                WHERE id=?
            """, (latest_date, latest_price, reached_stage, pick_id))
            notifications.append({
                "name": name, "stage": reached_stage, "kind": "hold",
                "text": f"⏱️ {name} {reached_stage}일 경과, 보합 마감 "
                        f"({latest_price:,.0f}원, {pct:+.1f}%, 추천일:{pick_date})",
            })
        else:
            # 중간 체크인(7일/14일) — 진행상황만 알림, pending 유지
            conn.execute("""
                UPDATE sshow_picks SET last_checkin=? WHERE id=?
            """, (reached_stage, pick_id))
            closer = "목표가" if dist_to_tgt < dist_to_stop else "손절가"
            notifications.append({
                "name": name, "stage": reached_stage, "kind": "progress",
                "text": f"📍 {name} {reached_stage}일 경과 — "
                        f"현재 {latest_price:,.0f}원({pct:+.1f}%), "
                        f"{closer} 쪽에 더 가까움 (추천일:{pick_date})",
            })

    conn.commit()
    conn.close()

    if notifications:
        print(f"✅ 생쇼 체크인 알림 {len(notifications)}건 생성")

    return notifications


def get_sshow_stats(days: int = 30) -> dict:
    """
    최근 N일간 판정 완료(hit/stop/hold)된 추천의 적중률 통계.
    반환: {"total": N, "hit": N, "stop": N, "hold": N, "hit_rate": 0~1,
           "sample_size_ok": bool}
    가격 정합성 비정상(price_valid=0) 건은 집계에서 제외.
    """
    if not os.path.exists(DB_PATH):
        return {"total": 0, "hit": 0, "stop": 0, "hold": 0,
                "hit_rate": 0.0, "sample_size_ok": False}

    cutoff = (datetime.date.today() -
              datetime.timedelta(days=days)).strftime("%Y-%m-%d")
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        rows = conn.execute("""
            SELECT result, COUNT(*) FROM sshow_picks
            WHERE date >= ? AND price_valid = 1 AND result != 'pending'
            GROUP BY result
        """, (cutoff,)).fetchall()
        conn.close()
    except Exception as e:
        print(f"⚠️ 생쇼 통계 조회 오류: {e}")
        return {"total": 0, "hit": 0, "stop": 0, "hold": 0,
                "hit_rate": 0.0, "sample_size_ok": False}

    counts = {"hit": 0, "stop": 0, "hold": 0}
    for result, cnt in rows:
        if result in counts:
            counts[result] = cnt

    total = sum(counts.values())
    # 적중률: hit / (hit + stop)  ('hold'는 무승부로 분모에서 제외 —
    #  목표/손절 어느 쪽도 안 닿은 종목까지 패배로 잡으면 적중률이
    #  부당하게 낮아짐)
    decided = counts["hit"] + counts["stop"]
    hit_rate = (counts["hit"] / decided) if decided > 0 else 0.0

    return {
        "total": total,
        "hit": counts["hit"],
        "stop": counts["stop"],
        "hold": counts["hold"],
        "hit_rate": hit_rate,
        "sample_size_ok": total >= 20,  # 최소 20건은 돼야 신뢰 가능
    }


def migrate_recalc_existing(cutoff_date: str = "2026-06-16") -> dict:
    """
    일회성 마이그레이션 (2026-06-30):
    - cutoff_date 이전 추천은 삭제 (오염 우려 + 통계적으로도 의미 적은
      오래된 데이터)
    - cutoff_date 이후 추천은 ATR 기반 가격으로 재계산해 덮어씀
      (raw_buy_price 등에 원문값은 보존)
    - 재계산 실패(가격데이터 없음)한 건은 그대로 두되 price_valid=0으로
      표시해 통계에서 제외

    반환: {"deleted": N, "recalced": N, "kept_failed": N}
    """
    init_db()
    conn = sqlite3.connect(DB_PATH, timeout=10)

    deleted = conn.execute(
        "DELETE FROM sshow_picks WHERE date < ?", (cutoff_date,)
    ).rowcount
    conn.commit()

    rows = conn.execute("""
        SELECT id, date, stock_name, buy_price, stop_price, tgt_price
        FROM sshow_picks WHERE date >= ?
    """, (cutoff_date,)).fetchall()

    recalced, kept_failed = 0, 0
    for pick_id, pick_date, name, old_buy, old_stop, old_tgt in rows:
        result = _recalc_prices_from_finance_db(name, pick_date)
        if result:
            conn.execute("""
                UPDATE sshow_picks
                SET buy_price=?, stop_price=?, tgt_price=?,
                    raw_buy_price=?, raw_stop_price=?, raw_tgt_price=?,
                    price_source='atr', price_valid=1
                WHERE id=?
            """, (result["buy_price"], result["stop_price"], result["tgt_price"],
                  old_buy, old_stop, old_tgt, pick_id))
            recalced += 1
            print(f"   🔧 재계산: {name}({pick_date}) "
                  f"매수:{old_buy:,.0f}→{result['buy_price']:,.0f}")
        else:
            conn.execute("UPDATE sshow_picks SET price_valid=0 WHERE id=?", (pick_id,))
            kept_failed += 1
            print(f"   ⚠️ 재계산 실패(데이터없음): {name}({pick_date}) → 통계 제외 표시")

    conn.commit()
    conn.close()

    print(f"✅ 마이그레이션 완료: 삭제{deleted} / 재계산{recalced} / 실패{kept_failed}")
    return {"deleted": deleted, "recalced": recalced, "kept_failed": kept_failed}


def get_sshow_stocks(days: int = 5) -> dict:
    """
    최근 N영업일 생쇼 종목 반환
    반환: {종목명: {"buy": 매수가, "stop": 손절가, "tgt": 목표가, "days_ago": N}}
    """
    if not os.path.exists(DB_PATH):
        return {}

    try:
        cutoff = (datetime.date.today() -
                  datetime.timedelta(days=days + 2)).strftime("%Y-%m-%d")

        conn   = sqlite3.connect(DB_PATH, timeout=5)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT stock_name, buy_price, stop_price, tgt_price, date
            FROM sshow_picks
            WHERE date >= ?
            ORDER BY date DESC
        """, (cutoff,))
        rows = cursor.fetchall()
        conn.close()

        result = {}
        today  = datetime.date.today()

        for name, buy, stop, tgt, date_str in rows:
            if name not in result:
                try:
                    d = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
                    days_ago = (today - d).days
                except Exception:
                    days_ago = 99
                result[name] = {
                    "buy":      buy,
                    "stop":     stop,
                    "tgt":      tgt,
                    "days_ago": days_ago,
                }

        return result

    except Exception as e:
        print(f"⚠️ 생쇼 조회 오류: {e}")
        return {}


def get_sshow_summary() -> str:
    """생쇼 DB 현황 요약"""
    if not os.path.exists(DB_PATH):
        return "생쇼 DB 없음"

    try:
        conn   = sqlite3.connect(DB_PATH, timeout=5)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT date, COUNT(*) FROM sshow_picks
            GROUP BY date ORDER BY date DESC LIMIT 7
        """)
        rows = cursor.fetchall()
        conn.close()

        if not rows:
            return "생쇼 DB 비어있음"

        lines = ["📋 [생쇼 DB 현황]"]
        for date, cnt in rows:
            lines.append(f"   {date}: {cnt}종목")
        return "\n".join(lines)

    except Exception as e:
        return f"⚠️ 생쇼 DB 조회 오류: {e}"


if __name__ == "__main__":
    init_db()
    print(get_sshow_summary())
    stocks = get_sshow_stocks(days=5)
    print(f"\n최근 5일 종목: {len(stocks)}개")
    for name, info in list(stocks.items())[:5]:
        print(f"  {name}: 매수{info['buy']:,.0f} 손절{info['stop']:,.0f} "
              f"목표{info['tgt']:,.0f} ({info['days_ago']}일전)")
