"""
youtube_daily_digest.py — 유튜브 라이브 모니터 일일 리포트
================================================================
[하는 일]
youtube_live_monitor.py가 밤새 잡은 (목표가/손절가 있는) 종목 중,
1회성이라 실시간 알림(notify_report, "14일내 2회+"만 통과) 문턱을
못 넘은 것들도 대장이 놓치지 않도록, 매일 06시에 전일 06시~당일 06시
구간 전체 리스트를 한 번에 보여준다.

★ 2026-09-17: 대장 요청 — "실시간 알림은 2회+ 문턱 현상유지하고
대신 06시에 전일 06~당일06시까지의 리스트를 한번 보여주는게 낫겠다."
(밤새 목표가/손절가 있는 진짜 픽 11건이 전부 1회성이라 실시간 알림은
0건이었던 걸 확인한 뒤 나온 절충안)

크론: 매일 06:00 (day_trade_scout처럼 크론 기반, 상시서비스 아님)
================================================================
"""
import os
import re
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

from youtube_stock_monitor import DB_PATH


_PRICE_RE = re.compile(r"(목표가|손절가)\s*[:：]?\s*[\d,]+")


def get_last_24h_picks() -> list:
    """★ 2026-09-16 가격게이트 배포 이전 데이터가 섞여있으면(과거 실행
    잔재) evaluation에 목표가/손절가 숫자가 없는 저품질 건도 같이 나올
    수 있어서, 디제스트에서도 한 번 더 걸러낸다 — 82건 전량 발송으로
    테스트하다 발견(대장이 우려했던 "종목수 폭발"이 알림채널만
    실시간→일일로 바뀌어 그대로 재현될 뻔함)."""
    conn = sqlite3.connect(DB_PATH, timeout=5)
    conn.execute("PRAGMA query_only = ON")
    rows = conn.execute("""
        SELECT stock_name, channel, evaluation, created_at
        FROM youtube_picks
        WHERE created_at >= datetime('now', 'localtime', '-1 day')
        ORDER BY created_at
    """).fetchall()
    conn.close()
    return [
        {"name": r[0], "channel": r[1], "evaluation": r[2], "created_at": r[3]}
        for r in rows if r[2] and _PRICE_RE.search(r[2])
    ]


def main():
    picks = get_last_24h_picks()
    if not picks:
        print("😴 [유튜브 일일리포트] 지난 24시간 캐치 없음")
        return

    lines = []
    for p in picks:
        ev = (p["evaluation"] or "").replace("\n", " ").strip()
        lines.append(f"- {p['name']} ({p['channel']}): {ev}" if ev else f"- {p['name']} ({p['channel']})")

    msg = f"[유튜브 일일리포트] 최근 24시간 캐치 {len(picks)}건\n" + "\n".join(lines)
    try:
        from notifier import Notifier
        Notifier(name="유튜브스카우트").send(msg)
    except Exception as e:
        print(f"⚠️ 알림 전송 오류: {e}")


if __name__ == "__main__":
    main()
