"""
youtube_daily_digest.py — 유튜브 라이브 모니터 일일 리포트 + DB 정리
================================================================
[하는 일]
매일 06시에 최근 24시간 동안 나온 종목 중 "14일 내 2회 이상 언급"
문턱을 넘긴 것들을 한 번에 정리해서 보여준다. 실시간 알림
(notify_report)과 완전히 같은 문턱/포맷을 쓴다 — 원래는 목표가/
손절가만 있으면 다 보여줬는데(2026-09-17), 그마저도 하루 81건까지
나와서 "너무 많다"는 지적을 받고 실시간과 동일한 2회+ 기준으로 통일.

★ 2026-09-19: 리포트 전에 20일 지난 픽을 DB에서 삭제(cleanup_old_picks)
— 대장 "픽된 후 매수안되고 20일지나면 제외시키자. 안그럼 계속
쌓이기만 할거야." 실제 매매판단(sbo2 관심종목)은 이미 5일 창만
보므로 이 정리는 순수 DB 하우스키핑.

크론: 매일 06:00 (day_trade_scout처럼 크론 기반, 상시서비스 아님)
================================================================
"""
import os
import sys
import sqlite3

_here = os.path.dirname(os.path.abspath(__file__))
_base = os.path.dirname(_here)
for _d in ["core", "intelligence", "interface", "bots", ""]:
    _p = os.path.join(_base, _d)
    if _p not in sys.path:
        sys.path.insert(0, _p)

from dotenv import load_dotenv
for _ep in [os.path.join(_here, ".env"), os.path.join(_base, ".env")]:
    if os.path.exists(_ep):
        load_dotenv(_ep)
        break

from youtube_stock_monitor import DB_PATH, build_2plus_report_lines, cleanup_old_picks


def get_last_24h_names() -> set:
    conn = sqlite3.connect(DB_PATH, timeout=5)
    conn.execute("PRAGMA query_only = ON")
    rows = conn.execute("""
        SELECT DISTINCT stock_name FROM youtube_picks
        WHERE created_at >= datetime('now', 'localtime', '-1 day')
    """).fetchall()
    conn.close()
    return {r[0] for r in rows}


def main():
    deleted = cleanup_old_picks()
    if deleted:
        print(f"🧹 20일 지난 픽 {deleted}건 정리")

    names = get_last_24h_names()
    if not names:
        print("😴 [유튜브 일일리포트] 지난 24시간 캐치 없음")
        return

    report_lines = build_2plus_report_lines(names)
    if not report_lines:
        print("😴 [유튜브 일일리포트] 2회+ 언급 종목 없음")
        return

    msg = f"[유튜브 일일리포트] 14일내 2회+ 언급 종목 {len(report_lines)}건\n" + ", ".join(report_lines)
    try:
        from notifier import Notifier
        Notifier(name="유튜브스카우트").send(msg)
    except Exception as e:
        print(f"⚠️ 알림 전송 오류: {e}")


if __name__ == "__main__":
    main()
