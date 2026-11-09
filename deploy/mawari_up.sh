#!/bin/bash
# ============================================================
# deploy/mawari_up.sh — 마와리 가디언 노드(docker) 띄우기/복구
# ============================================================
# 사용법:  sudo deploy/mawari_up.sh
#
# - 노드 신원(버너 지갑 키 등)은 docker 볼륨 mawari-data 안에 있음.
#   이게 바뀌면 "다른 노드"가 되므로, 볼륨이 비어 있으면 아래 순서로 채움:
#     1) 예전 서버 디스크  /mnt/old/var/lib/docker/volumes/mawari-data/_data
#     2) 백업 사본        ~/k-bot/mawari_data_backup  (백업 tar 에 들어감)
# - 다 띄운 뒤 볼륨 내용을 ~/k-bot/mawari_data_backup 으로 복사 →
#   deploy/backup.sh 가 매주 같이 백업함 (지갑 키 포함 — 백업 파일 취급 주의)
# - 위임자 지갑 주소는 .env 의 MAWARI_DELEGATOR_ALLOWLIST
# - 2026-11-09 예전 컨테이너 설정 그대로: 이미지 0.2.0, restart unless-stopped
# ============================================================
set -euo pipefail

[ "$(id -u)" = "0" ] || { echo "❌ sudo로 실행하세요: sudo $0"; exit 1; }

U=free4tak
BOT=/home/$U/k-bot/stock_bot
SAVE=/home/$U/k-bot/mawari_data_backup
OLD=/mnt/old/var/lib/docker/volumes/mawari-data/_data
IMAGE="us-east4-docker.pkg.dev/mawari-chain-prod/mawari-client/guardian-node:0.2.0"
NAME=mawari-node
VOL=mawari-data

DELEG=$(grep -E '^MAWARI_DELEGATOR_ALLOWLIST=' "$BOT/.env" | tail -1 | cut -d= -f2-)
[ -n "$DELEG" ] || { echo "❌ .env 에 MAWARI_DELEGATOR_ALLOWLIST 없음"; exit 1; }

command -v docker >/dev/null || { echo "❌ docker 없음: sudo apt install -y docker.io"; exit 1; }
systemctl enable --now docker >/dev/null

docker volume inspect $VOL >/dev/null 2>&1 || docker volume create $VOL >/dev/null
VDIR=$(docker volume inspect -f '{{.Mountpoint}}' $VOL)

if [ -z "$(ls -A "$VDIR" 2>/dev/null)" ]; then
  if [ -d "$OLD" ] && [ -n "$(ls -A "$OLD")" ]; then
    echo "📥 예전 서버 디스크에서 노드 데이터 복원"; cp -a "$OLD/." "$VDIR/"
  elif [ -d "$SAVE" ] && [ -n "$(ls -A "$SAVE")" ]; then
    echo "📥 백업 사본에서 노드 데이터 복원"; cp -a "$SAVE/." "$VDIR/"
    chown -R --reference="$VDIR" "$VDIR" 2>/dev/null || true
  else
    echo "⚠️ 예전 노드 데이터 없음 — 새 노드로 시작됩니다 (신원 바뀜). 계속하려면 Enter, 중단은 Ctrl+C"
    read -r _
  fi
else
  echo "ℹ️ 볼륨에 이미 데이터 있음 — 그대로 사용"
fi

if docker ps -a --format '{{.Names}}' | grep -qx $NAME; then
  echo "🔁 기존 컨테이너 교체"; docker rm -f $NAME >/dev/null
fi

docker pull "$IMAGE"
docker run -d --name $NAME --restart unless-stopped \
  -v $VOL:/home/node/.mawari-node \
  -e DELEGATOR_ALLOWLIST="$DELEG" \
  "$IMAGE" >/dev/null

# 주간 백업에 들어가도록 사본 (사용자 소유)
rm -rf "$SAVE"; mkdir -p "$SAVE"; cp -a "$VDIR/." "$SAVE/"; chown -R $U: "$SAVE"; chmod 700 "$SAVE"

# 로그창(open_bot_logs.sh)용: 이 명령 하나만 비밀번호 없이
SUD=/etc/sudoers.d/mawari-logs
if [ ! -f $SUD ]; then
  echo "$U ALL=(ALL) NOPASSWD: /usr/bin/docker logs -f $NAME" > $SUD.tmp
  chmod 0440 $SUD.tmp && visudo -cf $SUD.tmp >/dev/null && mv $SUD.tmp $SUD && echo "🔓 로그창용 sudoers 등록"
fi

sleep 5
docker ps --filter name=$NAME --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}'
echo "--- 최근 로그"; docker logs --tail 15 $NAME 2>&1
echo "✅ 완료 — 실시간 로그: sudo docker logs -f $NAME"
