#!/bin/bash
# ============================================================
# deploy/offsite_upload.sh — 백업을 구글 드라이브에 '암호화해서' 한 벌 더 (2026-11-14, 대장)
# ============================================================
# 왜: 기본 백업(/mnt/old)은 같은 PC 안 디스크 → 화재·도난·전원사고·랜섬웨어면 원본과 같이 사라짐(3-2-1 원칙의 '다른 장소' 없음).
# 어떻게: rclone 'crypt' 원격(이름 gcrypt)으로 올림 → 서버에서 잠근 뒤 올라가므로 구글엔 열 수 없는 덩어리만 있음
#        (백업 안에 .env 키·계좌·토큰이 있어서 평문 업로드는 절대 금지).
# 사용법:  deploy/offsite_upload.sh [백업폴더]      (기본 /mnt/old/home/free4tak/bk_bot — backup.sh가 끝에 자동 호출)
#   · 그 폴더의 가장 최근 yeongam9_full_*.tar.gz(+.sha256)를 gcrypt:yeongam9 에 올리고
#   · 클라우드엔 최근 KEEP(4)개만 남김
#   · gcrypt 원격이 아직 없으면(설정 전) 조용히 건너뜀 — 백업 자체는 실패로 안 봄
# 처음 설정(대장이 원격 화면 터미널에서 1회): deploy/RESTORE.md "클라우드 이중 백업" 참고
# 복구:  rclone copy gcrypt:yeongam9/<파일명> ~/  → sha256sum -c 로 확인 → RESTORE.md 순서
# ============================================================
set -uo pipefail

SRC="${1:-/mnt/old/home/free4tak/bk_bot}"
REMOTE="gcrypt:yeongam9"
KEEP=4
RCLONE="$(command -v rclone || echo "$HOME/.local/bin/rclone")"

if [ ! -x "$RCLONE" ] || ! "$RCLONE" listremotes 2>/dev/null | grep -qx "gcrypt:"; then
  echo "☁️  클라우드 백업 건너뜀 — rclone 또는 gcrypt 원격 없음(설정 전)"
  exit 0
fi

LATEST=$(ls -1t "$SRC"/yeongam9_full_*.tar.gz 2>/dev/null | head -1)
[ -z "$LATEST" ] && { echo "☁️  올릴 백업 없음: $SRC"; exit 0; }
NAME=$(basename "$LATEST")

echo "☁️  구글 드라이브(암호화)로 올리는 중: $NAME"
if "$RCLONE" copy "$LATEST" "$REMOTE" --retries 3 --low-level-retries 10 \
   && "$RCLONE" copy "$LATEST.sha256" "$REMOTE" --retries 3; then
  # 올라간 크기 대조(같은 파일인지)
  L=$(stat -c %s "$LATEST")
  R=$("$RCLONE" size "$REMOTE/$NAME" --json 2>/dev/null | python3 -c "import json,sys; print(json.load(sys.stdin)['bytes'])" 2>/dev/null)
  if [ "$L" = "$R" ]; then
    echo "✅ 클라우드 백업 완료: $NAME ($((L / 1024 / 1024))MB, 크기 일치)"
  else
    echo "⚠️ 클라우드 백업 크기 불일치(로컬 $L / 클라우드 ${R:-?}) — 다음 주 다시 올라감"
  fi
else
  echo "❌ 클라우드 업로드 실패 — 네트워크·구글 로그인 만료 확인(rclone config reconnect gdrive:)"
  exit 0
fi

# 클라우드엔 최근 KEEP개만
"$RCLONE" lsf "$REMOTE" --include "yeongam9_full_*.tar.gz" 2>/dev/null | sort -r | tail -n +$((KEEP + 1)) | while read -r old; do
  "$RCLONE" deletefile "$REMOTE/$old" && "$RCLONE" deletefile "$REMOTE/$old.sha256" 2>/dev/null
  echo "🗑️  클라우드 오래된 백업 삭제: $old"
done
