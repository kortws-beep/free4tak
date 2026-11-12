"""
rotate_logs.py — 큰 봇 로그를 봇 멈춤 없이 압축 보관 (2026-11-13, 대장 "cbot.log 다이어트")
================================================================
[이 파일이 하는 일 — 비개발자용 설명]
logs/*.log 중 기준(기본 50MB)보다 큰 것을 logs/archive/<이름>_<날짜>.log.gz 로 압축해 두고,
원래 파일은 0바이트로 비운다(copytruncate). systemd가 append 모드로 쓰고 있어서 봇은 그대로 이어 씀
(비우는 순간 몇 줄이 빠질 수는 있음 — 로그 용도라 허용).
  · 보관본은 로그 이름별로 최근 KEEP개만 남기고 오래된 것부터 지움.
  · 아무도 안 쓰는 옛 로그(30일 넘게 안 바뀐 것)는 통째로 압축하고 원본은 지움.
  · 백업(deploy/backup.sh)은 logs/*.log를 빼고 받으므로, 보관본(.gz)도 기본 백업에선 빠짐(--with-logs면 포함).
실행: venv/bin/python scripts/rotate_logs.py            (실제 정리)
      venv/bin/python scripts/rotate_logs.py --dry-run  (계획만 출력)
      venv/bin/python scripts/rotate_logs.py --min-mb 20
"""
import argparse
import datetime
import glob
import gzip
import os
import shutil
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOGS = os.path.join(BASE, "logs")
ARCH = os.path.join(LOGS, "archive")
KEEP = 6
STALE_DAYS = 30


def _gzip_copy(src: str, dst: str):
    with open(src, "rb") as fi, gzip.open(dst, "wb", compresslevel=6) as fo:
        shutil.copyfileobj(fi, fo, 1024 * 1024)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-mb", type=float, default=50)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    os.makedirs(ARCH, exist_ok=True)
    stamp = datetime.date.today().strftime("%Y%m%d")
    now = time.time()
    for path in sorted(glob.glob(os.path.join(LOGS, "*.log"))):
        size = os.path.getsize(path)
        name = os.path.basename(path)[:-4]
        stale = now - os.path.getmtime(path) > STALE_DAYS * 86400
        if size < a.min_mb * 1e6 and not (stale and size > 1e6):
            continue
        dst = os.path.join(ARCH, f"{name}_{stamp}.log.gz")
        what = "옛 로그 통째 압축(원본 삭제)" if stale else "압축 보관 후 비움"
        print(f"{'(계획) ' if a.dry_run else ''}{name}.log {size / 1e6:,.0f}MB → {os.path.relpath(dst, BASE)} · {what}")
        if a.dry_run:
            continue
        _gzip_copy(path, dst)
        if stale:
            os.remove(path)
        else:
            with open(path, "r+b") as f:
                f.truncate(0)
        print(f"   ✅ 보관본 {os.path.getsize(dst) / 1e6:,.1f}MB")
        olds = sorted(glob.glob(os.path.join(ARCH, f"{name}_*.log.gz")))
        for old in olds[:-KEEP]:
            os.remove(old)
            print(f"   🗑️ 오래된 보관본 삭제: {os.path.basename(old)}")


if __name__ == "__main__":
    main()
