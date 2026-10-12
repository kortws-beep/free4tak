"""
unified_risk.py — 영암9 통합 리스크 매니저
================================================================
[역할]
  - 세 봇(daybot/sbot/cbot) 합산 손실 한도 관리
  - 리스크 레벨 자동 산출 (normal / warning / danger)
  - 전봇 긴급중단 플래그 관리
  - 각 봇 루프에서 주기적으로 호출

[리스크 레벨]
  normal  : 손실 < 186,200원 (한도 70%)
  warning : 손실 186,200원 ~ 266,000원 (70~100%)
  danger  : 손실 >= 266,000원 OR 수동 긴급중단

[사용법]
  # 각 봇 루프 (30초~1분마다)
  from unified_risk import check_risk, is_paused

  state = check_risk()
  if is_paused():
      print("🚨 전봇 긴급중단 중")
      continue

  # 대시보드/키키에서 긴급중단
  from unified_risk import pause_all, resume_all
  pause_all()    # 전봇 중단
  resume_all()   # 전봇 재개

[실행 (독립 프로세스로도 가능)]
  python3 unified_risk.py          # 30초 주기 모니터링
  python3 unified_risk.py status   # 현재 상태 출력
================================================================
"""
import os
import sys
import time
import datetime

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in ["core", "intelligence", "interface", "bots", ""]:
    _p = os.path.join(_BASE, _d)
    if _p not in sys.path:
        sys.path.insert(0, _p)

from master_db import (
    update_risk, get_risk_state,
    set_pause_all, set_daily_loss_limit, resumed_today,
    MASTER_DB,
)

# ============================================================
# 상수
# ============================================================
DAILY_LOSS_LIMIT = 266_000   # 380만 × 7%
WARN_RATIO       = 0.70      # 경고 임계 70%
CHECK_INTERVAL   = 30        # 체크 주기 (초)


# ============================================================
# 핵심 API
# ============================================================
def ensure_limit(db_path: str = MASTER_DB) -> int:
    """DB 한도가 테이블 기본값(5만)이면 DAILY_LOSS_LIMIT로 맞춤(대시보드에서 바꾼 값은 유지).
    ★ 2026-10-12 — 자동중단을 처음 연결하면서 확인: 아무도 설정 안 해 5만이었음(하루 데이봇
    손실만으로도 전봇 중단될 수준)."""
    st = get_risk_state(db_path)
    limit = st.get("daily_loss_limit") or 0
    if not limit or limit <= 50_000:
        set_daily_loss_limit(DAILY_LOSS_LIMIT, db_path)
        return DAILY_LOSS_LIMIT
    return int(limit)


def check_risk(db_path: str = MASTER_DB) -> dict:
    """
    리스크 상태 갱신 + 반환. 위험하면 알림, 한도 초과면 전봇 자동 중단.
    ★ 2026-10-12 — 이 함수를 아무도 안 불러 자동중단이 한 번도 작동한 적 없었음 →
    리나가 1분마다 호출(lina_bot.risk_watch_loop).
    """
    state = update_risk(db_path)

    # 위험 감지 시 디스코드 알림
    if state.get("risk_level") in ("warning", "danger"):
        _notify_risk(state, db_path)

    return state


def is_paused() -> bool:
    """전봇 긴급중단 여부 확인. 각 봇 루프 시작부에서 호출."""
    try:
        state = get_risk_state()
        return state.get("paused_all", False)
    except Exception:
        return False


def pause_all(reason: str = "수동") -> bool:
    """전봇 긴급중단 ON"""
    ok = set_pause_all(True)
    _notify(f"🚨 전봇 긴급중단 ON [{reason}]", critical=True)
    return ok


def resume_all() -> bool:
    """전봇 긴급중단 해제"""
    ok = set_pause_all(False)
    _notify("✅ 전봇 긴급중단 해제", critical=True)
    return ok


def get_status() -> dict:
    """현재 리스크 상태 조회 (읽기 전용)"""
    state = get_risk_state()
    if not state:
        return {}

    limit = state.get("daily_loss_limit", DAILY_LOSS_LIMIT)
    loss  = state.get("total_loss_krw", 0)
    ratio = loss / limit if limit > 0 else 0

    return {
        **state,
        "loss_ratio":   round(ratio * 100, 1),   # 한도 대비 %
        "loss_remain":  int(limit - loss),         # 남은 여유
        "warn_line":    int(limit * WARN_RATIO),   # 경고선
    }


# ============================================================
# 알림 (디스코드)
# ============================================================
_last_notify_ts  = 0.0
_last_risk_level = "normal"

def _notify(msg: str, critical: bool = False):
    """디스코드 알림(키키 채널 — 봇 관리 알림). 실패하면 print.
    ★ 2026-10-12 — 예전엔 Notifier(웹훅주소)+asyncio.run(send)로 불러 매번 예외 → 조용히 print만
    했음(Notifier는 name/route를 받는 동기 send)."""
    print(f"[unified_risk] {msg}")
    try:
        from notifier import Notifier
        Notifier(name="risk").send(msg, critical=critical)
    except Exception as e:
        print(f"[unified_risk] 알림 실패: {e}")


def _notify_risk(state: dict, db_path: str = MASTER_DB):
    """리스크 레벨 변화 시만 알림 (중복 방지)"""
    global _last_notify_ts, _last_risk_level

    level = state.get("risk_level", "normal")
    now   = time.time()

    # ★ 2026-10-12 — 레벨이 바뀔 때만 알림(예전엔 같은 레벨이어도 10분마다 반복 → 경고 상태가
    #   하루 종일이면 키키 채널이 도배됨). 날짜가 바뀌면 처음부터.
    key = (state.get("date"), level)
    if key == _last_risk_level:
        return

    _last_notify_ts  = now
    _last_risk_level = key

    loss   = state.get("total_loss_krw", 0)
    limit  = state.get("daily_loss_limit", DAILY_LOSS_LIMIT)
    ratio  = loss / limit * 100 if limit > 0 else 0

    if level == "warning":
        msg = (
            f"⚠️ 리스크 경고\n"
            f"당일 손실: {loss:,.0f}원 / 한도: {limit:,.0f}원 ({ratio:.0f}%)\n"
            f"daybot: {state.get('daybot_loss_krw',0):,.0f}원 | "
            f"sbot: {state.get('sbot_loss_krw',0):,.0f}원 | "
            f"cbot: {state.get('cbot_loss_krw',0):,.0f}원"
        )
        _notify(msg, critical=True)

    elif level == "danger":
        if not state.get("paused_all") and resumed_today(db_path):
            return          # 오늘 대장이 손으로 재개함 — 같은 손실로 다시 멈추지 않음
        if not state.get("paused_all"):
            # 자동 전봇 중단
            set_pause_all(True, db_path)
            msg = (
                f"🚨 리스크 한도 초과 — 전봇 자동 중단!\n"
                f"당일 손실: {loss:,.0f}원 / 한도: {limit:,.0f}원 ({ratio:.0f}%)\n"
                f"재개: 대시보드 또는 키키 !리스크재개"
            )
            _notify(msg, critical=True)


# ============================================================
# 독립 모니터링 루프 (systemd 서비스로도 등록 가능)
# ============================================================
def run_monitor():
    """30초 주기 리스크 모니터링 루프"""
    print(f"🛡️ unified_risk 모니터 시작 (한도: {DAILY_LOSS_LIMIT:,}원)")

    # 한도 DB에 저장
    set_daily_loss_limit(DAILY_LOSS_LIMIT)

    while True:
        try:
            state = check_risk()
            level = state.get("risk_level", "normal")
            loss  = state.get("total_loss_krw", 0)
            profit= state.get("total_profit_krw", 0)
            emoji = {"normal": "🟢", "warning": "🟡", "danger": "🔴"}.get(level, "⚪")
            print(
                f"{emoji} [{datetime.datetime.now().strftime('%H:%M:%S')}] "
                f"손실: {loss:,.0f}원 | 수익: {profit:,.0f}원 | "
                f"레벨: {level}"
                + (" | ⛔ 전봇중단" if state.get("paused_all") else "")
            )
        except Exception as e:
            print(f"⚠️ 리스크 체크 오류: {e}")

        time.sleep(CHECK_INTERVAL)


# ============================================================
# CLI
# ============================================================
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("cmd", nargs="?", default="monitor",
                        choices=["monitor", "status", "pause", "resume", "limit"])
    parser.add_argument("--limit", type=int, help="일일 손실 한도(원)")
    args = parser.parse_args()

    if args.cmd == "status":
        s = get_status()
        print(f"""
📊 통합 리스크 상태
━━━━━━━━━━━━━━━━━━━━━━━
날짜       : {s.get('date')}
손실       : {s.get('total_loss_krw',0):,.0f}원 ({s.get('loss_ratio',0):.1f}%)
수익       : {s.get('total_profit_krw',0):,.0f}원
경고선     : {s.get('warn_line',0):,.0f}원
한도       : {s.get('daily_loss_limit',0):,.0f}원
여유       : {s.get('loss_remain',0):,.0f}원
━━━━━━━━━━━━━━━━━━━━━━━
daybot 손실: {s.get('daybot_loss_krw',0):,.0f}원
sbot 손실  : {s.get('sbot_loss_krw',0):,.0f}원
cbot 손실  : {s.get('cbot_loss_krw',0):,.0f}원
━━━━━━━━━━━━━━━━━━━━━━━
레벨       : {s.get('risk_level','?')}
전봇중단   : {'⛔ 중단중' if s.get('paused_all') else '✅ 정상'}
━━━━━━━━━━━━━━━━━━━━━━━
""")

    elif args.cmd == "pause":
        pause_all("CLI")
        print("🚨 전봇 긴급중단 ON")

    elif args.cmd == "resume":
        resume_all()
        print("✅ 전봇 긴급중단 해제")

    elif args.cmd == "limit":
        if args.limit:
            set_daily_loss_limit(args.limit)
            print(f"✅ 한도 설정: {args.limit:,}원")
        else:
            print("사용법: python3 unified_risk.py limit --limit 266000")

    else:  # monitor
        run_monitor()
