"""
lina_archive.py — 리나가 받거나 내보내는 것 전부 보관 (2026-11-14, 대장 "리나 데이터에 들어오는 거는 모두 저장되어야 해")
================================================================
[이 파일이 하는 일 — 비개발자용 설명]
점검해 보니 리나가 디스코드로 보내는 브리핑·알림 원문(07:30 브리핑, 08:50 MBN 전략 요약, 끼 알림, 섹터·검색식 알림 등)은
화면으로만 흘러가고 남지 않았고, 끼 알림용으로 매분 받는 관심그룹 시세도 판정만 하고 버리고 있었음.
→ 두 가지를 lina_bot/lina_archive.db 에 쌓는다(매매엔 영향 없음, 실패해도 조용히 넘어감):
  · messages : 리나가 보낸 메시지 원문 전부(시각·채널·첫 줄 제목) — 나중에 "그날 리나가 뭐라 했나" 그대로 복기
  · quotes_1m: 관심그룹(isee5) 종목 1분 시세(가격·등락률·고가·누적 거래대금·거래량) — 앞으로 쌓이는 분봉 자료
               (daybot 분봉 백테스트용. 과거분은 backtest/data/minute_cache.db를 한투에서 받아 씀)
"""
import datetime
import os
import sqlite3

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lina_archive.db")


def _conn():
    c = sqlite3.connect(DB_PATH, timeout=10)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, channel TEXT, "
              "title TEXT, text TEXT)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_msg_ts ON messages(ts)")
    c.execute("CREATE TABLE IF NOT EXISTS quotes_1m (date TEXT, time TEXT, code TEXT, price REAL, chg REAL, high REAL, "
              "value REAL, volume REAL, PRIMARY KEY(date, time, code))")
    return c


def save_message(text: str, channel: str = "") -> None:
    try:
        text = str(text or "")
        if not text.strip():
            return
        title = text.strip().splitlines()[0][:120]
        c = _conn()
        c.execute("INSERT INTO messages (ts, channel, title, text) VALUES (?,?,?,?)",
                  (datetime.datetime.now().isoformat(timespec="seconds"), str(channel or ""), title, text))
        c.commit()
        c.close()
    except Exception as e:
        print(f"⚠️ [리나 보관] 메시지 저장 실패: {e}")


def save_quotes(date: str, hhmm: str, quotes: dict) -> int:
    try:
        rows = [(date, hhmm, code, float(q.get("price") or 0), float(q.get("chg") or 0), float(q.get("high") or 0),
                 float(q.get("value") or 0), float(q.get("volume") or 0))
                for code, q in (quotes or {}).items() if float(q.get("price") or 0) > 0]
        if not rows:
            return 0
        c = _conn()
        c.executemany("INSERT OR REPLACE INTO quotes_1m VALUES (?,?,?,?,?,?,?,?)", rows)
        c.commit()
        c.close()
        return len(rows)
    except Exception as e:
        print(f"⚠️ [리나 보관] 1분 시세 저장 실패: {e}")
        return 0
