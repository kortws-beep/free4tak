"""
market_safety_stop.py — 시장 쏠림 안전장치 (매수시작 전 판단, 위험시 봇 자체 정지)
================================================================
[배경]
2026-08-21, S7(삼성전자/SK하이닉스 등 대형주) 몇 종목만 오르고 나머지
종목은 대부분 빠지는 쏠림장이 발생 — sbot/sbo2의 분산된 보유종목들이
지수와 무관하게 손절권으로 몰림. 사용자가 직접 판단해 두 봇을 완전히
정지시켰는데("완전히 정지.. 매도체크만 하게 하지 말고 봇 자체를
정지"), 사람이 못 볼 때를 대비해 같은 판단을 자동으로 해달라고 요청.

[판단 기준]
market_concentration.py가 계산하는 breadth_ratio(시장폭 — 전체 테마 중
상승 비율)가 낮으면(<50%) 소수 종목만 오르는 쏠림장으로 간주.
concentration_gap(대형주-코스피 갭)은 좋은 날/나쁜 날 구분이 breadth_
ratio보다 덜 명확해서(실측 비교 결과) 판단 기준에서 제외 — breadth_
ratio 단독 사용.

[타이밍]
breadth_ratio는 직전 5분(BREADTH_WINDOW_MIN) 데이터로 계산되는데, 장
시작(09:00) 직후엔 데이터가 거의 없어 0.0으로 찍혀 신뢰할 수 없음.
그래서 이 스크립트는 09:19에 실행하도록 cron 등록(sbot/sbo2 BUY_START_
TIME도 09:20으로 함께 늦춰서, 이 판단이 끝난 뒤에만 매수가 시작되게 함).

[실행 방법]
  python3 intelligence/market_safety_stop.py

  # cron 등록 (평일 09:19, 매수시작 1분 전)
  19 9 * * 1-5 cd /home/free4tak/k-bot/stock_bot && \\
      /home/free4tak/k-bot/stock_bot/venv/bin/python3 intelligence/market_safety_stop.py \\
      >> /home/free4tak/k-bot/stock_bot/logs/market_safety_stop.log 2>&1

[정지 방법]
`sudo systemctl stop yeongam9-sbot`/`yeongam9-sbo2` — sudoers에
NOPASSWD 등록 필요(2026-08-21 사용자가 직접 등록 완료). 매도체크까지
포함해 완전 정지(사용자 명시적 요구 — "매도체크만 하게 하지 말고 봇
자체를 정지시켜야해").

[재개]
자동 재개 없음 — 사용자가 직접 상황 보고 재시작.
================================================================
"""
import os
import sys
import subprocess
import datetime

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

from market_concentration import compute_snapshot, save_snapshot, init_db

BREADTH_DANGER_THRESHOLD = 50.0   # 시장폭 이 값 미만이면 위험 (2026-08-21 실측 비교로 결정)
TARGET_SERVICES = ["yeongam9-sbot", "yeongam9-sbo2"]


def _notify(msg: str):
    try:
        from notifier import Notifier
        Notifier(name="시장안전장치").send(msg, critical=True)
    except Exception as e:
        print(f"⚠️ 알림 전송 오류: {e}")


def _stop_service(service: str) -> bool:
    try:
        res = subprocess.run(
            ["sudo", "systemctl", "stop", service],
            capture_output=True, text=True, timeout=15,
        )
        if res.returncode == 0:
            print(f"✅ {service} 정지 완료")
            return True
        print(f"❌ {service} 정지 실패: {res.stderr.strip()}")
        return False
    except Exception as e:
        print(f"❌ {service} 정지 예외: {e}")
        return False


def main():
    init_db()
    snapshot = compute_snapshot()
    save_snapshot(snapshot)

    breadth = snapshot.get("breadth_ratio", 0.0)
    gap     = snapshot.get("concentration_gap", 0.0)
    kospi   = snapshot.get("kospi_rate", 0.0)
    now     = datetime.datetime.now().strftime("%H:%M")

    print(f"[{now}] 시장폭:{breadth:.1f}% | 쏠림갭:{gap:+.2f}%p | 코스피:{kospi:+.2f}%")

    # breadth_ratio==0.0은 "데이터 없음"과 "진짜 0%" 둘 다 가능한데,
    # 이 시각(09:19)이면 데이터가 없을 리 없어 사실상 후자로 봐도 되지만
    # 안전하게 0.0은 "판단불가"로 취급해 정지시키지 않음(과잉반응 방지).
    if breadth <= 0.0:
        print("⚠️ 시장폭 데이터 없음 — 판단 보류, 정지하지 않음")
        return

    if breadth >= BREADTH_DANGER_THRESHOLD:
        print(f"✅ 시장폭 {breadth:.1f}% ≥ {BREADTH_DANGER_THRESHOLD}% — 정상, 매수 진행")
        return

    print(f"🚨 시장폭 {breadth:.1f}% < {BREADTH_DANGER_THRESHOLD}% — 쏠림 위험 판정, 봇 정지")
    stopped = []
    for svc in TARGET_SERVICES:
        if _stop_service(svc):
            stopped.append(svc)

    _notify(
        f"🚨 [시장안전장치] 쏠림 위험 감지 — sbot/sbo2 자동 정지\n"
        f"시장폭: {breadth:.1f}% (기준 {BREADTH_DANGER_THRESHOLD}% 미만)\n"
        f"쏠림갭: {gap:+.2f}%p | 코스피: {kospi:+.2f}%\n"
        f"정지됨: {', '.join(stopped) if stopped else '없음(정지 실패, 확인 필요)'}\n"
        f"수동으로 상황 판단 후 재시작해줘 — 자동 재개 없음."
    )


if __name__ == "__main__":
    main()
