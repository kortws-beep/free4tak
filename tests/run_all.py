"""
전체 테스트 실행기 — 저장소 루트에서:  python tests/run_all.py

- tests/test_*.py 를 파일마다 별도 프로세스로 실행(가짜 모듈이 서로 섞이지 않게)
- pyflakes가 설치돼 있으면 운영 코드의 "정의되지 않은 이름"(NameError 예비군)도 검사
  (설치: pip install pyflakes — 없으면 이 단계만 건너뜀)
- 하나라도 실패하면 종료코드 1
"""
import glob
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
LINT_DIRS = ["bots", "core", "interface", "intelligence", "lina_bot"]


def run_tests() -> bool:
    ok = True
    for path in sorted(glob.glob(os.path.join(HERE, "test_*.py"))):
        name = os.path.basename(path)
        # 파일을 직접 실행(tests/가 import 경로에 잡혀 _helpers를 찾음, 각 파일은 unittest.main())
        res = subprocess.run([sys.executable, path], cwd=REPO, capture_output=True, text=True)
        passed = res.returncode == 0
        ok &= passed
        print(f"{'✅' if passed else '❌'} {name}")
        if not passed:
            print((res.stdout + res.stderr)[-3000:])
    return ok


def run_pyflakes() -> bool:
    try:
        import pyflakes  # noqa: F401
    except ImportError:
        print("ℹ️ pyflakes 미설치 — 정적검사 건너뜀 (pip install pyflakes)")
        return True
    files = []
    for d in LINT_DIRS:
        files += glob.glob(os.path.join(REPO, d, "*.py"))
    res = subprocess.run([sys.executable, "-m", "pyflakes", *files],
                         capture_output=True, text=True)
    bad = [l for l in res.stdout.splitlines() if "undefined name" in l]
    if bad:
        print("❌ pyflakes: 정의되지 않은 이름")
        print("\n".join(bad))
        return False
    print("✅ pyflakes: 정의되지 않은 이름 없음")
    return True


if __name__ == "__main__":
    ok = run_tests()
    ok = run_pyflakes() and ok
    print("\n🎉 전부 통과" if ok else "\n💥 실패 있음")
    sys.exit(0 if ok else 1)
