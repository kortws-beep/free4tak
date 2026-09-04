"""
common_utils.py — 공통 유틸리티
================================================================
[이 파일이 하는 일 — 비개발자용 설명]

여러 봇(nbot/sbot/ebot/cbot)이 공통으로 쓰는 도우미 함수 모음입니다.
- 상태 파일 읽기/쓰기 (각 봇의 현재 상태를 JSON 파일로 저장)
- 시간 관련 함수 (한국 시간, 영업일 체크 등)
- 숫자 포맷팅 (원화 표기 등)
- 안전한 형변환 (오류 방지)

기존에는 각 봇 파일마다 같은 코드가 반복되어 있었는데,
이 파일 하나로 모아 유지보수가 쉬워졌습니다.
================================================================
"""

import os
import json
import datetime
from typing import Any, Optional


# ============================================================
# 한국 시간 (KST)
# ============================================================
try:
    import pytz
    _KST = pytz.timezone("Asia/Seoul")
    def now_kst() -> datetime.datetime:
        """현재 한국 시간을 datetime 객체로 반환 (timezone 정보 없는 naive 객체)"""
        return datetime.datetime.now(_KST).replace(tzinfo=None)
except ImportError:
    def now_kst() -> datetime.datetime:
        # pytz가 없으면 UTC + 9시간으로 직접 계산
        return (
            datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
            + datetime.timedelta(hours=9)
        )


def now_hhmm() -> str:
    """현재 시각을 'HHMM' 형식 문자열로 반환 (예: '0935')"""
    return now_kst().strftime("%H%M")


def now_hms() -> str:
    """현재 시각을 'HH:MM:SS' 형식 문자열로 반환"""
    return now_kst().strftime("%H:%M:%S")


def today_str() -> str:
    """오늘 날짜를 'YYYY-MM-DD' 형식 문자열로 반환"""
    return now_kst().strftime("%Y-%m-%d")


def now_full_ts() -> str:
    """완전한 타임스탬프('YYYY-MM-DD HH:MM:SS') 반환 — buy_time/sell_time
    처럼 날짜+시각이 모두 필요한 기록용(sbo2.py에서 이식, 09-04)."""
    return now_kst().strftime("%Y-%m-%d %H:%M:%S")


def is_weekend() -> bool:
    """주말(토/일)이면 True"""
    return now_kst().weekday() >= 5


def is_market_hours() -> bool:
    """정규장 시간(09:00~15:30)이면 True"""
    t = now_hhmm()
    return "0900" <= t <= "1530"


# ============================================================
# 안전한 형변환 (None/이상값 들어와도 죽지 않게)
# ============================================================
def safe_int(value: Any, default: int = 0) -> int:
    """문자열/None/이상값을 안전하게 int로 변환"""
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except (ValueError, TypeError):
        return default


def safe_float(value: Any, default: float = 0.0) -> float:
    """문자열/None/이상값을 안전하게 float로 변환"""
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (ValueError, TypeError):
        return default


# ============================================================
# 상태 파일 관리 (Atomic Write + 파일 락으로 동시쓰기 보호)
# ============================================================
# ★ 2026-06-28 추가: 여러 봇 프로세스(예: sbot 메인 루프와 kiki의
#   디스코드 명령 처리)가 동시에 같은 *_state.json을 update_state()로
#   건드릴 때, read → modify → write 사이에 다른 프로세스가 끼어들면
#   한쪽의 변경사항이 사라지는 lost-update 문제가 있었음
#   (예: !일시중단 명령이 sbot의 상태 갱신에 덮어써져 사라지는 경우).
#   filelock으로 update_state()의 read-modify-write 전체를 보호.
#
#   ★ 주의: read_state()/write_state() 자체는 락을 걸지 않음 — 매번
#   새 FileLock 인스턴스를 만들면 filelock의 재진입(reentrant) 보장이
#   안 통해 update_state() 내부에서 read_state()/write_state()를 호출할
#   때 같은 파일을 두 번 잠그려다 데드락(타임아웃)이 나는 문제가 있었음.
#   그래서 락은 update_state() 한 곳에서만 걸고, 그 안에서는 락 없는
#   내부 헬퍼(_read_state_raw/_write_state_raw)를 직접 사용.
try:
    from filelock import FileLock as _FileLock
    _FILELOCK_AVAILABLE = True
except ImportError:
    _FileLock = None
    _FILELOCK_AVAILABLE = False
    print("⚠️ filelock 미설치 → state 파일 동시쓰기 보호 비활성 "
          "(pip install filelock 권장)")


def _state_lock(state_file: str):
    """
    state_file에 대응하는 락 객체 반환.
    filelock 미설치 시 아무 동작도 하지 않는 더미 컨텍스트매니저 반환
    (기존 동작 그대로 — 락 없이 진행).
    """
    if _FILELOCK_AVAILABLE:
        return _FileLock(state_file + ".lock", timeout=10)
    else:
        from contextlib import nullcontext
        return nullcontext()


def _read_state_raw(state_file: str, default: dict) -> dict:
    """락 없이 그냥 읽기 (update_state 내부 전용)"""
    try:
        if os.path.exists(state_file):
            with open(state_file, "r", encoding="utf-8") as f:
                return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"⚠️ 상태 파일 읽기 오류 ({state_file}): {e}")
    return default.copy()


def _write_state_raw(state_file: str, state: dict) -> bool:
    """락 없이 그냥 쓰기 (update_state 내부 전용)"""
    try:
        tmp_file = state_file + ".tmp"
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        os.replace(tmp_file, state_file)
        return True
    except Exception as e:
        print(f"⚠️ 상태 파일 저장 오류 ({state_file}): {e}")
        return False


def read_state(state_file: str, default: dict = None) -> dict:
    """
    상태 파일을 읽어 dict로 반환.
    파일이 없거나 깨졌으면 default를 반환.
    """
    if default is None:
        default = {}
    return _read_state_raw(state_file, default)


def write_state(state_file: str, state: dict) -> bool:
    """
    상태 파일을 안전하게 저장.
    중간에 죽어도 파일이 깨지지 않도록 임시 파일에 먼저 쓴 뒤 교체.
    """
    return _write_state_raw(state_file, state)


def update_state(state_file: str, **kwargs) -> bool:
    """
    상태 파일을 부분 업데이트 (기존 내용 유지하며 일부만 변경).
    ★ read-modify-write 전체를 단일 락으로 보호 — 다른 프로세스가
    이 사이에 같은 파일을 건드려 변경사항이 사라지는 것을 방지.
    """
    try:
        with _state_lock(state_file):
            state = _read_state_raw(state_file, {})
            state.update(kwargs)
            return _write_state_raw(state_file, state)
    except Exception as e:
        # filelock.Timeout 등 — 락 획득 실패 시에도 죽지 않고 False 반환
        print(f"⚠️ 상태 파일 업데이트 오류 ({state_file}): {e}")
        return False


# ============================================================
# 포맷팅 (디스코드 알림용)
# ============================================================
def fmt_won(amount: int) -> str:
    """원화 포맷: 1234567 → '1,234,567원'"""
    return f"{int(amount):,}원"


def fmt_pct(rate: float, decimals: int = 2) -> str:
    """수익률 포맷: 0.0523 → '+5.23%' / -0.012 → '-1.20%'"""
    return f"{rate*100:+.{decimals}f}%"


def fmt_rate_direct(rate: float, decimals: int = 2) -> str:
    """이미 % 단위인 값: 5.23 → '+5.23%'"""
    return f"{rate:+.{decimals}f}%"


# ============================================================
# 가격 호가 단위 (한국 주식 시장 규칙)
# ============================================================
def get_hoga_unit(price: float) -> int:
    """
    주가별 호가 단위 반환.
    예: 5000원짜리는 5원 단위, 100000원짜리는 100원 단위로 주문 가능.
    """
    if   price < 2000:    return 1
    elif price < 5000:    return 5
    elif price < 20000:   return 10
    elif price < 50000:   return 50
    elif price < 200000:  return 100
    elif price < 500000:  return 500
    else:                 return 1000


def round_to_hoga(price: float, direction: str = "up") -> int:
    """
    주가를 호가 단위로 반올림.
    direction: 'up'(올림), 'down'(내림), 'near'(반올림)
    """
    hoga = get_hoga_unit(price)
    if direction == "up":
        return int((price + hoga - 0.001) // hoga) * hoga
    elif direction == "down":
        return int(price // hoga) * hoga
    else:  # near
        return int((price + hoga / 2) // hoga) * hoga


# ============================================================
# Claude API 응답 파싱
# ============================================================
def extract_claude_text(response) -> str:
    """
    Claude API 응답(client.messages.create() 반환값)에서 텍스트만 안전하게
    추출한다.
    ★ 2026-09-03: 여러 파일이 관행적으로 response.content[0].text로 첫
    블록을 텍스트라고 가정하고 있었는데, 소넷5로 모델을 올린 뒤 응답
    content[0]에 ThinkingBlock(추론 블록)이 먼저 오는 경우가 있어
    .text 접근이 AttributeError로 죽는 문제가 발견됨(리나 09:35 쏠림
    브리핑 등이 조용히 실패하고 있었음 — 사용자 지적으로 발견).
    content 리스트에서 실제 text 타입 블록만 골라 이어붙이도록 통일.
    """
    try:
        parts = [b.text for b in response.content if getattr(b, "type", None) == "text"]
        return "".join(parts).strip()
    except Exception:
        return ""
