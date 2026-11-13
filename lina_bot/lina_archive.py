"""
lina_archive.py — 리나가 받거나 내보내는 것 전부 보관 (2026-11-14, 대장 "리나 데이터에 들어오는 거는 모두 저장되어야 해")
================================================================
[이 파일이 하는 일 — 비개발자용 설명]
점검해 보니 리나가 디스코드로 보내는 브리핑·알림 원문(07:30 브리핑, 08:50 MBN 전략 요약, 끼 알림, 섹터·검색식 알림 등)은
화면으로만 흘러가고 남지 않았고, 끼 알림용으로 매분 받는 관심그룹 시세도 판정만 하고 버리고 있었음.
→ 두 가지를 lina_bot/lina_archive.db 에 쌓는다(매매엔 영향 없음, 실패해도 조용히 넘어감).
★ 대장 "데이터들의 출처는 명확히 구분해야 해" — 줄마다 출처 칸을 둠:
  · messages : 리나가 보낸 메시지 원문 전부
      - source : 그 메시지를 보낸 리나 기능 이름(예: danta_scan_watch=단타000 검색식, sector_watch_loop=섹터 감시,
                 daily_morning_report=07:30 브리핑, daily_strategy_report=08:50 MBN 전략, spike_watch_loop=끼 알림,
                 on_message=대장 명령 응답) — 코드가 자동으로 기록(사람이 붙이는 꼬리표가 아니라 틀릴 일 없음)
      - tag    : 첫 줄의 [대괄호] 꼬리표(예: [단타000], [주도주검색식3]) — 없으면 빈칸
      - channel: 디스코드 채널 이름(또는 DM)
  · quotes_1m: 관심그룹 1분 시세 — grp(어느 한투 관심그룹에서 온 종목인지, 예: isee5) + provider(시세 출처: 한투 현재가 API)
"""
import datetime
import os
import re
import sqlite3

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lina_archive.db")
_TAG = re.compile(r"\[([^\]]{1,40})\]")


def _conn():
    c = sqlite3.connect(DB_PATH, timeout=10)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, source TEXT, tag TEXT, "
              "channel TEXT, title TEXT, text TEXT)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_msg_ts ON messages(ts)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_msg_src ON messages(source, ts)")
    c.execute("CREATE TABLE IF NOT EXISTS quotes_1m (date TEXT, time TEXT, grp TEXT, code TEXT, price REAL, chg REAL, "
              "high REAL, value REAL, volume REAL, provider TEXT, PRIMARY KEY(date, time, grp, code))")
    return c


def save_message(text: str, channel: str = "", source: str = "") -> None:
    try:
        text = str(text or "")
        if not text.strip():
            return
        title = text.strip().splitlines()[0][:120]
        m = _TAG.search(title)
        c = _conn()
        c.execute("INSERT INTO messages (ts, source, tag, channel, title, text) VALUES (?,?,?,?,?,?)",
                  (datetime.datetime.now().isoformat(timespec="seconds"), str(source or ""),
                   m.group(1).strip() if m else "", str(channel or ""), title, text))
        c.commit()
        c.close()
    except Exception as e:
        print(f"⚠️ [리나 보관] 메시지 저장 실패: {e}")


def save_quotes(date: str, hhmm: str, quotes: dict, grp, provider: str = "KIS 현재가(get_multi_price)") -> int:
    """grp: 그룹 이름(str) 또는 {코드: 그룹 이름}(종목마다 다른 관심그룹일 때)."""
    try:
        rows = [(date, hhmm, (grp.get(code, "") if isinstance(grp, dict) else grp), code, float(q.get("price") or 0), float(q.get("chg") or 0), float(q.get("high") or 0),
                 float(q.get("value") or 0), float(q.get("volume") or 0), provider)
                for code, q in (quotes or {}).items() if float(q.get("price") or 0) > 0]
        if not rows:
            return 0
        c = _conn()
        c.executemany("INSERT OR REPLACE INTO quotes_1m VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
        c.commit()
        c.close()
        return len(rows)
    except Exception as e:
        print(f"⚠️ [리나 보관] 1분 시세 저장 실패: {e}")
        return 0
