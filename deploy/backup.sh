#!/bin/bash
# ============================================================
# deploy/backup.sh — 영암9 전체 백업 (새 PC 복구용)
# ============================================================
# 사용법:
#   deploy/backup.sh [백업폴더] [--with-logs]
#     백업폴더 기본값: ~/backups   (USB면 /media/free4tak/<USB이름>/bk_bot 등)
#     --with-logs : logs/ 도 포함 (500MB+, 기본은 제외)
#
# 담기는 것 (tar.gz 한 파일):
#   - ~/k-bot 전체 (stock_bot 코드 + .env + credentials/token + state json
#     + 세션파일 + OpenClaw + config) — venv/__pycache__/worktree 제외
#   - 모든 SQLite DB 는 sqlite3 backup API로 "일관된 사본"을 따로 떠서 넣음
#     (봇이 돌고 있어도 WAL 내용까지 안전하게 포함 — 원본 .db-wal 을 빼던
#      예전 backup_bot.sh 방식은 최근 쓰기가 빠질 수 있었음)
#   - 시스템 설정 스냅샷: crontab, /etc/systemd/system/yeongam9-*,
#     pip freeze, 수동설치 apt 목록, 공인IP
#
# ★ .env·토큰이 들어있음 → 이 파일은 비밀번호처럼 다룰 것 (클라우드 공개 업로드 금지)
# 복구: deploy/RESTORE.md 참고
# ============================================================
set -euo pipefail

KBOT="$HOME/k-bot"
BOT="$KBOT/stock_bot"
DEST="$HOME/backups"
WITH_LOGS=0
for a in "$@"; do
  case "$a" in
    --with-logs) WITH_LOGS=1 ;;
    *) DEST="$a" ;;
  esac
done

DATE=$(date +%Y-%m-%d_%H%M)
NAME="yeongam9_full_$DATE"
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
mkdir -p "$DEST" "$WORK/$NAME/system" "$WORK/$NAME/db_snapshots"

echo "📦 영암9 전체 백업 시작 → $DEST/$NAME.tar.gz"

# 1) 시스템 설정 스냅샷
crontab -l > "$WORK/$NAME/system/crontab.txt" 2>/dev/null || true
mkdir -p "$WORK/$NAME/system/systemd"
cp /etc/systemd/system/yeongam9-* "$WORK/$NAME/system/systemd/" 2>/dev/null || true
"$BOT/venv/bin/pip" freeze > "$WORK/$NAME/system/pip_freeze.txt" 2>/dev/null || true
apt-mark showmanual > "$WORK/$NAME/system/apt_manual.txt" 2>/dev/null || true
"$BOT/venv/bin/python3" --version > "$WORK/$NAME/system/python_version.txt" 2>&1 || true
curl -s --max-time 5 https://api.ipify.org > "$WORK/$NAME/system/public_ip.txt" 2>/dev/null || true
hostname > "$WORK/$NAME/system/hostname.txt"

# 2) SQLite 일관 사본 (실행 중에도 안전)
echo "🗄️  DB 스냅샷..."
python3 - "$KBOT" "$WORK/$NAME/db_snapshots" <<'PY'
import os, sqlite3, sys
root, out = sys.argv[1], sys.argv[2]
skip = ("/venv/", "/.claude/worktrees/", "/__pycache__/")
n = 0
for d, _, files in os.walk(root):
    if any(s in d + "/" for s in skip):
        continue
    for f in files:
        if not f.endswith(".db"):
            continue
        src = os.path.join(d, f)
        if os.path.getsize(src) == 0:
            continue
        rel = os.path.relpath(src, root)
        dst = os.path.join(out, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        try:
            s = sqlite3.connect(f"file:{src}?mode=ro", uri=True, timeout=30)
            t = sqlite3.connect(dst)
            s.backup(t)
            t.close(); s.close()
            n += 1
        except Exception as e:
            print(f"  ⚠️ {rel}: {e} (원본 파일은 tar에 그대로 들어감)")
print(f"  ✅ DB {n}개 스냅샷")
PY

# 3) k-bot 전체 tar (DB 원본도 들어가지만, 복구 땐 db_snapshots 쪽으로 덮어씀)
EXCL=(--exclude="stock_bot/venv" --exclude="*/myenv" --exclude="__pycache__"
      --exclude="*.pyc" --exclude="stock_bot/.claude/worktrees"
      --exclude="*.db-wal" --exclude="*.db-shm" --exclude="*.db-journal")
[ "$WITH_LOGS" = "0" ] && EXCL+=(--exclude="stock_bot/logs/*.log" --exclude="stock_bot/logs/*/*.log")

echo "🗜️  압축 중 (몇 분 걸릴 수 있음)..."
# tar 종료코드 1 = "읽는 중 파일이 바뀜" 경고(봇 실행 중이면 정상) — DB는 위 스냅샷이 정본
rc=0
tar --warning=no-file-changed --warning=no-file-removed "${EXCL[@]}" \
    -czf "$DEST/$NAME.tar.gz" -C "$HOME" "k-bot" -C "$WORK" "$NAME" || rc=$?
[ "$rc" -gt 1 ] && { echo "❌ tar 실패 (코드 $rc)"; exit "$rc"; }

SIZE=$(du -sh "$DEST/$NAME.tar.gz" | cut -f1)
sha256sum "$DEST/$NAME.tar.gz" > "$DEST/$NAME.tar.gz.sha256"
chmod 600 "$DEST/$NAME.tar.gz"
echo "✅ 백업 완료: $DEST/$NAME.tar.gz ($SIZE)"

# 4) 오래된 백업 정리 (최근 5개만 유지)
ls -1t "$DEST"/yeongam9_full_*.tar.gz 2>/dev/null | tail -n +6 | while read -r old; do
  rm -f "$old" "$old.sha256"; echo "🗑️  오래된 백업 삭제: $(basename "$old")"
done
