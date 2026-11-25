#!/bin/bash
# ============================================================
# deploy/usb_copy.sh — 외장 SSD(라벨 SSD0)에 최신 전체 백업 복사 (2026-11-25, 대장)
# ============================================================
# 토요일 백업(deploy/backup.sh) 끝에 자동 실행 → 같은 백업이 세 군데:
#   ① /mnt/old(내장 옛 NVMe) ② 구글 드라이브(암호화) ③ 외장 SSD ← 이것
# 사용법: deploy/usb_copy.sh <백업폴더>   (그 폴더의 가장 최근 yeongam9_full_*.tar.gz를 복사)
#   · SSD는 라벨(SSD0)로 찾음 — 꽂혀서 마운트돼 있지 않으면 경고만 남기고 넘어감(백업 전체는 실패 처리 안 함)
#   · 복사 뒤 sha256으로 확인, SSD엔 최근 KEEP개만 유지
#   · ★ .env·토큰 들어있는 파일 — SSD는 서버에 꽂아 두는 용도, 들고 다니면 분실 주의
# ============================================================
set -uo pipefail
SRC="${1:-$HOME/backups}"
LABEL="${USB_BACKUP_LABEL:-SSD0}"
KEEP="${USB_BACKUP_KEEP:-6}"

MNT=$(findmnt -rn -S "LABEL=$LABEL" -o TARGET 2>/dev/null | head -1)
if [ -z "$MNT" ]; then
  echo "⚠️  외장 SSD($LABEL)가 마운트돼 있지 않음 — 외장 복사 건너뜀"
  exit 0
fi
DEST="$MNT/bk_bot"
mkdir -p "$DEST" || { echo "❌ 외장 SSD에 폴더를 못 만듦: $DEST"; exit 1; }

LATEST=$(ls -1t "$SRC"/yeongam9_full_*.tar.gz 2>/dev/null | head -1)
[ -z "$LATEST" ] && { echo "⚠️  복사할 백업이 없음: $SRC"; exit 0; }
NAME=$(basename "$LATEST")

if [ -f "$DEST/$NAME" ] && [ -f "$DEST/$NAME.sha256" ] && (cd "$DEST" && sha256sum -c --quiet "$NAME.sha256" 2>/dev/null); then
  echo "✅ 외장 SSD에 이미 있음(검증 통과): $NAME"
else
  echo "💾 외장 SSD로 복사: $NAME → $DEST"
  cp "$LATEST" "$DEST/$NAME.part" && mv "$DEST/$NAME.part" "$DEST/$NAME" || { echo "❌ 복사 실패"; exit 1; }
  [ -f "$LATEST.sha256" ] && cp "$LATEST.sha256" "$DEST/" || (cd "$DEST" && sha256sum "$NAME" > "$NAME.sha256")
  sync
  if (cd "$DEST" && sha256sum -c --quiet "$NAME.sha256"); then
    echo "✅ 외장 SSD 복사·검증 완료 ($(du -sh "$DEST/$NAME" | cut -f1))"
  else
    echo "❌ 외장 SSD 검증 실패 — 파일 손상 가능: $DEST/$NAME"; exit 1
  fi
fi

ls -1t "$DEST"/yeongam9_full_*.tar.gz 2>/dev/null | tail -n +$((KEEP + 1)) | while read -r old; do
  rm -f "$old" "$old.sha256"; echo "🗑️  외장 SSD 오래된 백업 삭제: $(basename "$old")"
done
echo "   외장 SSD 남은 공간: $(df -h "$MNT" | tail -1 | awk '{print $4}')"
