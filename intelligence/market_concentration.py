"""
market_concentration.py — 시장 쏠림 지수 (관찰 전용, Phase 1)
================================================================
[하는 일]
장중 30분마다 1회 실행되어 "지금 시장이 얼마나 대형주에 쏠려있는지"를
숫자로 계산해 저장한다. sbot/sbo2 스코어링에는 아직 연결하지 않고
관찰만 한다 (lina_bot.py의 종합 브리핑이 이 데이터를 읽어 코멘트 생성).

[계산 항목]
1. 시가총액 쏠림 — MEGA_CAP_CODES(삼성전자/SK하이닉스 등) 평균 등락률
   vs 코스피 등락률의 차이 (concentration_gap). 클수록 대형주 쏠림.
   ★ KIS API에 시가총액/지수기여도 API가 없어 하드코딩 워치리스트의
   등락률로 근사한다 — 정밀한 가중치 계산이 아니라 방향성 신호용.
2. 시장 폭(breadth) — sector_monitor.db의 최신 sector_flow 스냅샷에서
   전체 테마의 rising_num/total_num 합산 비율. 낮을수록 소수 종목만 오름.
3. 주도주/섹터 교체 — sector_monitor.py의 detect_baton_touch() 재사용
   (급가속/이탈 테마 감지, 이미 구현된 로직을 그대로 가져다 씀).

[실행 방법]
  # 수동 실행 (장중)
  python3 market_concentration.py

  # cron 등록 (평일 09:00~15:30, 30분마다)
  */30 9-15 * * 1-5 cd /home/free4tak/k-bot/stock_bot && \\
      /home/free4tak/k-bot/stock_bot/venv/bin/python3 intelligence/market_concentration.py \\
      >> /home/free4tak/k-bot/stock_bot/logs/market_concentration.log 2>&1

[다음 단계 (이번 범위 아님)]
  - 이 스냅샷을 sbot(AI프롬프트+점수보너스)/sbo2(룰기반 점수식)에 연결
  - cbot(코인) 대응
================================================================
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
import json
import sqlite3
import datetime

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
DB_PATH        = os.path.join(_here, "market_concentration.db")
SECTOR_DB_PATH = os.path.join(_here, "sector_monitor.db")
MARKET_START   = "0900"
MARKET_END     = "1530"

# ★ 시가총액 쏠림 근사용 워치리스트 — 편집하기 쉬운 상수.
#   코스피 시총 상위 종목 위주. 필요시 추가/조정.
MEGA_CAP_CODES = {
    "005930": "삼성전자",
    "000660": "SK하이닉스",
    "373220": "LG에너지솔루션",
    "207940": "삼성바이오로직스",
    "005380": "현대차",
}

# 시장 폭 breadth 계산 시 "최신 스냅샷"으로 볼 시간창 (분)
BREADTH_WINDOW_MIN = 5


# ============================================================
# DB 초기화
# ============================================================
def init_db():
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS concentration_snapshot (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            ts                 TEXT NOT NULL,
            kospi_rate         REAL DEFAULT 0,
            mega_avg_rate      REAL DEFAULT 0,
            mega_detail        TEXT DEFAULT '',
            concentration_gap  REAL DEFAULT 0,
            breadth_ratio      REAL DEFAULT 0,
            rotation_flag      TEXT DEFAULT '',
            created_at         TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS market_context_summary (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            date         TEXT NOT NULL,
            summary_text TEXT NOT NULL,
            raw_snapshot TEXT DEFAULT '',
            created_at   TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()


# ============================================================
# 종합 브리핑 이력 (lina_bot.py의 daily_market_context_report에서 사용)
# ============================================================
def save_market_summary(date: str, summary_text: str, raw_snapshot: dict):
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        INSERT INTO market_context_summary (date, summary_text, raw_snapshot)
        VALUES (?,?,?)
    """, (date, summary_text, json.dumps(raw_snapshot, ensure_ascii=False)))
    conn.commit()
    conn.close()


def get_recent_summaries(days: int = 3) -> list:
    """최근 N일치 종합 브리핑 이력 — 추세 비교용."""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.execute("PRAGMA query_only=ON")
        since = (datetime.datetime.now() - datetime.timedelta(days=days)).strftime("%Y-%m-%d")
        rows = conn.execute("""
            SELECT date, summary_text FROM market_context_summary
            WHERE date >= ? ORDER BY date ASC
        """, (since,)).fetchall()
        conn.close()
        return [{"date": r[0], "summary_text": r[1]} for r in rows]
    except Exception as e:
        print(f"⚠️ 브리핑 이력 조회 오류: {e}")
        return []


# ============================================================
# 계산
# ============================================================
def _calc_breadth_ratio() -> float:
    """sector_monitor.db 최신 스냅샷의 상승종목수/전체종목수 비율.
    조회 실패나 데이터 없음 시 0.0 반환(신호 없음으로 취급)."""
    try:
        conn = sqlite3.connect(SECTOR_DB_PATH, timeout=5)
        conn.execute("PRAGMA query_only=ON")
        cutoff = (datetime.datetime.now()
                  - datetime.timedelta(minutes=BREADTH_WINDOW_MIN)).strftime("%Y-%m-%d %H:%M")
        row = conn.execute("""
            SELECT SUM(rising_num), SUM(total_num)
            FROM sector_flow
            WHERE ts >= ?
        """, (cutoff,)).fetchone()
        conn.close()
        rising, total = row if row else (0, 0)
        if not total:
            return 0.0
        return round((rising or 0) / total * 100, 1)
    except Exception as e:
        print(f"⚠️ breadth 조회 오류: {e}")
        return 0.0


def _calc_rotation_flag() -> str:
    """sector_monitor.py의 detect_baton_touch() 재사용 — 급가속/이탈 테마."""
    try:
        from sector_monitor import detect_baton_touch
        conn = sqlite3.connect(SECTOR_DB_PATH, timeout=5)
        conn.execute("PRAGMA query_only=ON")
        signals = detect_baton_touch(conn)
        conn.close()
        if not signals:
            return ""
        return ", ".join(f"{s['theme_nm']}({s['status']})" for s in signals[:5])
    except Exception as e:
        print(f"⚠️ 바통터치 조회 오류: {e}")
        return ""


def compute_snapshot() -> dict:
    """대형주 쏠림 + 시장폭 + 주도주교체 스냅샷 계산."""
    from kis_api import KisAPI
    api = KisAPI()

    kospi_rate = api.get_market_index().get("kospi", 0.0)

    mega_detail = {}
    for code, name in MEGA_CAP_CODES.items():
        try:
            data = api.get_market_data(code)
            rate = float(data.get("prdy_ctrt", 0)) if data else 0.0
        except Exception as e:
            print(f"⚠️ 대형주 조회 오류 {name}({code}): {e}")
            rate = 0.0
        mega_detail[code] = rate

    rates = list(mega_detail.values())
    mega_avg_rate = round(sum(rates) / len(rates), 2) if rates else 0.0
    concentration_gap = round(mega_avg_rate - kospi_rate, 2)

    breadth_ratio = _calc_breadth_ratio()
    rotation_flag = _calc_rotation_flag()

    return {
        "ts":                datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "kospi_rate":        kospi_rate,
        "mega_avg_rate":     mega_avg_rate,
        "mega_detail":       json.dumps(mega_detail, ensure_ascii=False),
        "concentration_gap": concentration_gap,
        "breadth_ratio":     breadth_ratio,
        "rotation_flag":     rotation_flag,
    }


def save_snapshot(snapshot: dict):
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        INSERT INTO concentration_snapshot
            (ts, kospi_rate, mega_avg_rate, mega_detail,
             concentration_gap, breadth_ratio, rotation_flag)
        VALUES (?,?,?,?,?,?,?)
    """, (
        snapshot["ts"], snapshot["kospi_rate"], snapshot["mega_avg_rate"],
        snapshot["mega_detail"], snapshot["concentration_gap"],
        snapshot["breadth_ratio"], snapshot["rotation_flag"],
    ))
    conn.commit()
    conn.close()


def get_latest_snapshot() -> dict:
    """lina_bot.py 등 다른 모듈에서 최신 스냅샷을 읽어갈 때 쓰는 헬퍼."""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.execute("PRAGMA query_only=ON")
        row = conn.execute("""
            SELECT ts, kospi_rate, mega_avg_rate, mega_detail,
                   concentration_gap, breadth_ratio, rotation_flag
            FROM concentration_snapshot
            ORDER BY id DESC LIMIT 1
        """).fetchone()
        conn.close()
        if not row:
            return {}
        cols = ["ts", "kospi_rate", "mega_avg_rate", "mega_detail",
                "concentration_gap", "breadth_ratio", "rotation_flag"]
        return dict(zip(cols, row))
    except Exception as e:
        print(f"⚠️ 최신 스냅샷 조회 오류: {e}")
        return {}


# ============================================================
# 메인 (1회 실행 — cron이 30분마다 호출)
# ============================================================
def main():
    now_hhmm = datetime.datetime.now().strftime("%H%M")
    if not (MARKET_START <= now_hhmm <= MARKET_END):
        print(f"😴 장외 시간({now_hhmm}) — 건너뜀")
        return

    init_db()
    snapshot = compute_snapshot()
    save_snapshot(snapshot)
    print(
        f"✅ [{snapshot['ts']}] 코스피:{snapshot['kospi_rate']:+.2f}% "
        f"대형주평균:{snapshot['mega_avg_rate']:+.2f}% "
        f"쏠림갭:{snapshot['concentration_gap']:+.2f}%p "
        f"시장폭:{snapshot['breadth_ratio']:.1f}% "
        f"교체신호:{snapshot['rotation_flag'] or '없음'}"
    )


if __name__ == "__main__":
    main()
