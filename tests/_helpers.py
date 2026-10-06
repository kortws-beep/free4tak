"""
테스트 공용 도우미 — 가짜 모듈(stub) 등록, 경로 설정, 임시 작업폴더.

★ 각 test_*.py는 run_all.py가 "파일마다 별도 프로세스"로 실행한다.
  stub이 sys.modules에 등록되면 같은 프로세스의 다른 테스트에도 섞이기
  때문(예: daybot 테스트의 가짜 KisAPI가 수집기 테스트로 새는 것 방지).
"""
import os
import sys
import types
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ★ 2026-10-06: 테스트는 실제 .env를 절대 읽지 않는다. kiki_cmd/kiki_briefing/
#   kiki_data/collect_daily_data가 import 시 load_dotenv(..., override=True)를
#   해서, 대장이 .env에 넣은 실제 값(KIKI_ALLOWED_USER_IDS 등)이 테스트가 미리
#   넣어 둔 os.environ 값을 덮어썼음(test_kiki_routing 2건 실패). 이 파일은
#   모든 테스트가 맨 먼저 import하므로 여기서 dotenv를 아무것도 안 하는 가짜로
#   바꿔 둔다 — 테스트에 필요한 환경값은 각 테스트가 os.environ에 직접 넣는다.
_dotenv = types.ModuleType("dotenv")
_dotenv.load_dotenv = lambda *a, **k: False
_dotenv.find_dotenv = lambda *a, **k: ""
_dotenv.dotenv_values = lambda *a, **k: {}
sys.modules["dotenv"] = _dotenv
for _d in ["core", "bots", "interface", "intelligence", "lina_bot", ""]:
    _p = os.path.join(REPO, _d)
    if _p not in sys.path:
        sys.path.insert(0, _p)


def stub(name: str, **attrs):
    """sys.modules[name]에 빈 모듈을 넣고 attrs를 붙인다(이미 있으면 덮어씀)."""
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    sys.modules[name] = mod
    return mod


def use_temp_cwd():
    """상대경로 DB/상태파일(sbot_trade_history.db 등)이 저장소를 더럽히지 않도록
    임시 폴더로 이동. TemporaryDirectory 객체를 반환(참조 유지 필요)."""
    tmp = tempfile.TemporaryDirectory(prefix="yeongam9_test_")
    os.chdir(tmp.name)
    return tmp
