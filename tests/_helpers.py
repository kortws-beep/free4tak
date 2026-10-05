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
