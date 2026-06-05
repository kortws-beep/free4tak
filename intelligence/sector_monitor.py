"""
sector_monitor.py — 섹터 순환매 + 체결 가속도 데이터 수집
================================================================
[Phase 1: 데이터 수집 전용 — 10일간 수집 후 분석]

[하는 일]
1. 매 1분마다 상위 10개 테마의 거래대금 합산 기록
2. 매 30초마다 테마 내 주요 종목 체결강도 기록
3. SQLite DB에 누적 저장
4. 10일 후 패턴 분석 → nbot/sbot 통합 여부 결정

[실행 방법]
  # 수동 실행 (장중)
  python3 sector_monitor.py

  # cron 등록 (평일 09:00~15:30)
  0 9 * * 1-5 cd /home/free4tak/k-bot/stock_bot && \
      /home/free4tak/k-bot/stock_bot/venv/bin/python3 sector_monitor.py \
      >> /tmp/sector_monitor.log 2>&1

[수집 데이터]
  sector_flow:    테마별 1분 거래대금 + 등락률 + 상승종목수
  stock_momentum: 종목별 30초 체결강도 + 거래량비율 + 등락률
"""
import sys as _sys
import os as _os
_BASE = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
for _d in ["core", "intelligence", "interface", "bots", ""]:
    _p = _os.path.join(_BASE, _d)
    if _p not in _sys.path:
        _sys.path.insert(0, _p)

import os
import sys
import time
import sqlite3
import datetime
import json

_here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _here)

from dotenv import load_dotenv
for _ep in [os.path.join(_here, ".env"), os.path.join(_here, "..", ".env")]:
    if os.path.exists(_ep):
        load_dotenv(_ep)
        break

# ============================================================
# 설정
# ============================================================
DB_PATH      = os.path.join(_here, "sector_monitor.db")
TOP_THEMES   = 10    # 상위 테마 수
TOP_STOCKS   = 3     # 테마당 주요 종목 수
FLOW_INTERVAL   = 60   # 거래대금 수집 주기 (초)
MOMENTUM_INTERVAL = 30  # 체결강도 수집 주기 (초)
MARKET_START = "0900"
MARKET_END   = "1530"


# ============================================================
# DB 초기화
# ============================================================
def init_db(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=30)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript("""
        -- 테마별 1분 거래대금 흐름
        CREATE TABLE IF NOT EXISTS sector_flow (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ts          TEXT NOT NULL,          -- YYYY-MM-DD HH:MM
            theme_cd    TEXT NOT NULL,
            theme_nm    TEXT NOT NULL,
            flu_rt      REAL DEFAULT 0,         -- 테마 등락률
            rising_num  INTEGER DEFAULT 0,       -- 상승 종목수
            total_num   INTEGER DEFAULT 0,       -- 전체 종목수
            trde_amt    REAL DEFAULT 0,          -- 테마 총 거래대금 (억원)
            flow_rate   REAL DEFAULT 0           -- 전분 대비 변동률 (%)
        );
        CREATE INDEX IF NOT EXISTS idx_sf_ts ON sector_flow(ts);
        CREATE INDEX IF NOT EXISTS idx_sf_theme ON sector_flow(theme_cd, ts);

        -- 종목별 30초 체결강도
        CREATE TABLE IF NOT EXISTS stock_momentum (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ts          TEXT NOT NULL,          -- YYYY-MM-DD HH:MM:SS
            code        TEXT NOT NULL,
            theme_cd    TEXT NOT NULL,
            theme_nm    TEXT NOT NULL,
            change_rate REAL DEFAULT 0,         -- 등락률
            vol_ratio   REAL DEFAULT 0,         -- 거래량비율
            trde_amt    REAL DEFAULT 0,         -- 거래대금 (억원)
            cntg_str    REAL DEFAULT 0,         -- 체결강도 (추정)
            accel       REAL DEFAULT 0          -- 가속도 (전회 대비)
        );
        CREATE INDEX IF NOT EXISTS idx_sm_ts ON stock_momentum(ts);
        CREATE INDEX IF NOT EXISTS idx_sm_code ON stock_momentum(code, ts);
    """)
    conn.commit()
    return conn


# ============================================================
# 키움/KIS API 래퍼
# ============================================================
def get_kiwoom_themes(kiwoom, top_n: int = 10) -> list:
    """ka90001로 상위 테마 목록 조회"""
    try:
        items = kiwoom.get_theme_top(top_n=top_n)
        return items or []
    except Exception as e:
        print(f"⚠️ 테마 조회 오류: {e}")
        return []


def get_theme_stock_codes(kiwoom, theme_cd: str,
                          code_name_map: dict, top_n: int = 5) -> list:
    """ka90002로 테마 구성 종목 조회"""
    try:
        stocks = kiwoom.get_theme_stocks(theme_cd, code_name_map)
        codes = [s[0] if isinstance(s, (list, tuple)) else s for s in stocks]
        return codes[:top_n]
    except Exception as e:
        print(f"⚠️ 테마 종목 조회 오류({theme_cd}): {e}")
        return []


def get_stock_data(api, code: str) -> dict:
    """KIS API로 종목 시세 조회"""
    try:
        data = api.get_market_data(code)
        if not data:
            return {}
        return {
            "change_rate": float(data.get("prdy_ctrt", 0) or 0),
            "trde_amt":    float(data.get("acml_tr_pbmn", 0) or 0) / 1e8,  # → 억원
            "vol_ratio":   float(data.get("vol_tnrt", 0) or 0),
            # 체결강도 근사: 거래량비율 × 등락률 부호
            "cntg_str":    float(data.get("vol_tnrt", 0) or 0),
        }
    except Exception as e:
        return {}


# ============================================================
# 거래대금 변동률 계산
# ============================================================
def calc_flow_rate(conn: sqlite3.Connection,
                   theme_cd: str, cur_amt: float) -> float:
    """전분 대비 거래대금 변동률"""
    try:
        row = conn.execute("""
            SELECT trde_amt FROM sector_flow
            WHERE theme_cd = ?
            ORDER BY ts DESC LIMIT 1
        """, (theme_cd,)).fetchone()
        if row and row[0] > 0:
            return (cur_amt - row[0]) / row[0] * 100
    except Exception:
        pass
    return 0.0


def calc_accel(conn: sqlite3.Connection,
               code: str, cur_str: float) -> float:
    """전회 대비 체결강도 가속도"""
    try:
        row = conn.execute("""
            SELECT cntg_str FROM stock_momentum
            WHERE code = ?
            ORDER BY ts DESC LIMIT 1
        """, (code,)).fetchone()
        if row:
            return cur_str - row[0]
    except Exception:
        pass
    return 0.0


# ============================================================
# 데이터 저장
# ============================================================
def save_sector_flow(conn: sqlite3.Connection, ts: str,
                     theme_cd: str, theme_nm: str,
                     flu_rt: float, rising_num: int,
                     total_num: int, trde_amt: float,
                     flow_rate: float):
    conn.execute("""
        INSERT INTO sector_flow
        (ts, theme_cd, theme_nm, flu_rt, rising_num, total_num, trde_amt, flow_rate)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (ts, theme_cd, theme_nm, flu_rt, rising_num,
          total_num, trde_amt, flow_rate))
    conn.commit()


def save_stock_momentum(conn: sqlite3.Connection, ts: str,
                        code: str, theme_cd: str, theme_nm: str,
                        change_rate: float, vol_ratio: float,
                        trde_amt: float, cntg_str: float, accel: float):
    conn.execute("""
        INSERT INTO stock_momentum
        (ts, code, theme_cd, theme_nm, change_rate, vol_ratio,
         trde_amt, cntg_str, accel)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (ts, code, theme_cd, theme_nm, change_rate,
          vol_ratio, trde_amt, cntg_str, accel))
    conn.commit()


# ============================================================
# 메인 수집 루프
# ============================================================
def collect_once(api, kiwoom, conn: sqlite3.Connection,
                 code_name_map: dict, theme_cache: dict):
    """한 번의 수집 사이클"""
    now = datetime.datetime.now()
    ts_min = now.strftime("%Y-%m-%d %H:%M")
    ts_sec = now.strftime("%Y-%m-%d %H:%M:%S")

    # 1) 테마 목록 조회 (1분마다)
    themes = get_kiwoom_themes(kiwoom, top_n=TOP_THEMES)
    if not themes:
        print(f"⚠️ [{ts_min}] 테마 조회 실패")
        return

    print(f"\n📊 [{ts_min}] 테마 수집 ({len(themes)}개)")

    for item in themes:
        theme_cd  = item.get("thema_grp_cd", "")
        theme_nm  = item.get("thema_nm", "")
        flu_rt    = float(item.get("flu_rt", 0) or 0)
        rising    = int(item.get("rising_stk_num", 0) or 0)
        total     = int(item.get("stk_num", 0) or 0)

        if not theme_cd:
            continue

        # 2) 테마 구성 종목 조회 (캐시 — 매시간 갱신)
        cache_key = f"{now.strftime('%Y-%m-%d %H')}_{theme_cd}"
        if cache_key not in theme_cache:
            codes = get_theme_stock_codes(
                kiwoom, theme_cd, code_name_map, TOP_STOCKS)
            theme_cache[cache_key] = codes
            time.sleep(0.2)
        else:
            codes = theme_cache[cache_key]

        # 3) 종목별 시세 → 테마 거래대금 합산
        total_amt = 0.0
        for code in codes:
            stock = get_stock_data(api, code)
            if not stock:
                continue

            # 체결강도 가속도
            accel = calc_accel(conn, code, stock["cntg_str"])

            save_stock_momentum(
                conn, ts_sec, code, theme_cd, theme_nm,
                stock["change_rate"], stock["vol_ratio"],
                stock["trde_amt"], stock["cntg_str"], accel
            )
            total_amt += stock["trde_amt"]
            time.sleep(0.1)

        # 4) 거래대금 변동률 계산
        flow_rate = calc_flow_rate(conn, theme_cd, total_amt)

        # 5) 섹터 플로우 저장
        save_sector_flow(
            conn, ts_min, theme_cd, theme_nm,
            flu_rt, rising, total, total_amt, flow_rate
        )

        # 바톤터치 감지 로그
        rising_ratio = rising / total if total > 0 else 0
        flow_emoji = "🔥" if flow_rate > 20 else ("📉" if flow_rate < -20 else "➡️")
        print(f"  {flow_emoji} {theme_nm}: 등락{flu_rt:+.2f}% | "
              f"상승{rising}/{total} | "
              f"거래대금{total_amt:.1f}억 | "
              f"변동{flow_rate:+.1f}%")


def detect_baton_touch(conn: sqlite3.Connection) -> list:
    """최근 데이터로 바톤터치 감지"""
    try:
        # 최근 5분 데이터
        cutoff = (datetime.datetime.now() -
                  datetime.timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M")
        rows = conn.execute("""
            SELECT theme_cd, theme_nm,
                   AVG(flu_rt) as avg_flu,
                   AVG(flow_rate) as avg_flow,
                   MAX(flow_rate) as max_flow
            FROM sector_flow
            WHERE ts >= ?
            GROUP BY theme_cd
            ORDER BY avg_flow DESC
        """, (cutoff,)).fetchall()

        signals = []
        if len(rows) >= 2:
            # 상위 테마 중 급가속 vs 상위 테마 중 감속
            for i, row in enumerate(rows):
                cd, nm, avg_flu, avg_flow, max_flow = row
                if avg_flow > 30:  # 30% 이상 급가속
                    signals.append({
                        "theme_cd": cd,
                        "theme_nm": nm,
                        "status":   "급가속🔥",
                        "flow_rate": avg_flow,
                    })
                elif avg_flow < -20:  # 20% 이상 급감속
                    signals.append({
                        "theme_cd": cd,
                        "theme_nm": nm,
                        "status":   "이탈📉",
                        "flow_rate": avg_flow,
                    })
        return signals
    except Exception as e:
        return []


# ============================================================
# 분석 리포트 (수동 실행용)
# ============================================================
def print_report(conn: sqlite3.Connection, days: int = 1):
    """수집 데이터 요약 리포트"""
    cutoff = (datetime.datetime.now() -
              datetime.timedelta(days=days)).strftime("%Y-%m-%d")

    print(f"\n{'='*60}")
    print(f"📊 섹터 모니터 리포트 (최근 {days}일)")
    print(f"{'='*60}")

    # 테마별 평균 거래대금 변동률
    rows = conn.execute("""
        SELECT theme_nm,
               COUNT(*) as cnt,
               AVG(flu_rt) as avg_flu,
               AVG(flow_rate) as avg_flow,
               MAX(flow_rate) as max_flow,
               MIN(flow_rate) as min_flow
        FROM sector_flow
        WHERE ts >= ?
        GROUP BY theme_cd
        ORDER BY avg_flow DESC
        LIMIT 10
    """, (cutoff,)).fetchall()

    print("\n[테마별 거래대금 흐름]")
    for nm, cnt, avg_flu, avg_flow, max_flow, min_flow in rows:
        print(f"  {nm[:15]:15s} | 등락{avg_flu:+5.2f}% | "
              f"평균변동{avg_flow:+6.1f}% | "
              f"최대{max_flow:+6.1f}% | 최소{min_flow:+6.1f}%")

    # 체결 가속도 상위 종목
    rows2 = conn.execute("""
        SELECT code, theme_nm,
               AVG(accel) as avg_accel,
               MAX(accel) as max_accel,
               AVG(change_rate) as avg_chg
        FROM stock_momentum
        WHERE ts >= ?
        GROUP BY code
        ORDER BY max_accel DESC
        LIMIT 10
    """, (cutoff,)).fetchall()

    print("\n[체결 가속도 상위 종목]")
    for code, nm, avg_accel, max_accel, avg_chg in rows2:
        print(f"  {code} [{nm[:10]:10s}] | "
              f"평균가속{avg_accel:+5.1f} | "
              f"최대가속{max_accel:+5.1f} | "
              f"평균등락{avg_chg:+5.2f}%")

    # DB 현황
    cnt_flow = conn.execute(
        "SELECT COUNT(*) FROM sector_flow WHERE ts >= ?",
        (cutoff,)).fetchone()[0]
    cnt_mom = conn.execute(
        "SELECT COUNT(*) FROM stock_momentum WHERE ts >= ?",
        (cutoff,)).fetchone()[0]
    print(f"\n📈 수집 건수: sector_flow={cnt_flow:,}건 | "
          f"stock_momentum={cnt_mom:,}건")


# ============================================================
# 메인
# ============================================================
def main():
    from kis_api import KisAPI
    from kiwoom_api import KiwoomAPI

    print(f"🚀 섹터 모니터 시작 — {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}")

    api    = KisAPI()
    kiwoom = KiwoomAPI()
    conn   = init_db(DB_PATH)

    code_name_map = {}
    theme_cache   = {}

    last_flow_time = 0

    print(f"📡 수집 시작 (테마:{TOP_THEMES}개, 종목:{TOP_STOCKS}개/테마)")
    print(f"⏰ 장 시간: {MARKET_START} ~ {MARKET_END}")

    try:
        while True:
            now_hhmm = datetime.datetime.now().strftime("%H%M")
            now_wday = datetime.datetime.now().weekday()

            # 주말 스킵
            if now_wday >= 5:
                print("😴 주말 — 대기 중")
                time.sleep(300)
                continue

            # 장외 스킵
            if not (MARKET_START <= now_hhmm <= MARKET_END):
                print(f"😴 장외({now_hhmm}) — 대기 중")
                time.sleep(60)
                continue

            # 1분마다 수집
            if time.time() - last_flow_time >= FLOW_INTERVAL:
                collect_once(api, kiwoom, conn, code_name_map, theme_cache)
                last_flow_time = time.time()

                # 바톤터치 감지
                signals = detect_baton_touch(conn)
                if signals:
                    print("\n🔔 바톤터치 신호:")
                    for s in signals:
                        print(f"  {s['status']} {s['theme_nm']} "
                              f"(변동{s['flow_rate']:+.1f}%)")

            time.sleep(30)

    except KeyboardInterrupt:
        print("\n\n⏹ 수집 중단")
        print_report(conn, days=1)
    finally:
        conn.close()


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "report":
        # 리포트만 출력
        conn = init_db(DB_PATH)
        days = int(sys.argv[2]) if len(sys.argv) > 2 else 1
        print_report(conn, days=days)
        conn.close()
    else:
        main()
