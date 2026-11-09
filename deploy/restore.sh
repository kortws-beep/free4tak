#!/bin/bash
# ============================================================
# deploy/restore.sh — 새 PC에 영암9 복구 (Ubuntu, 사용자 free4tak)
# ============================================================
# 사용법:
#   1) 백업 파일이 있으면:  bash restore.sh /경로/yeongam9_full_XXXX.tar.gz
#      (이 스크립트는 백업 안 k-bot/stock_bot/deploy/restore.sh 에도 들어있음 —
#       tar -xzf 백업.tar.gz k-bot/stock_bot/deploy/restore.sh 로 꺼내 실행)
#   2) 이미 ~/k-bot 이 복원돼 있으면: ~/k-bot/stock_bot/deploy/restore.sh
#
# 하는 일 (단계마다 실패하면 멈춤):
#   [1] apt 시스템 패키지 (ffmpeg·java·python venv 등)
#   [2] 백업 풀기 + DB 일관사본으로 덮어쓰기   (백업 파일 준 경우만)
#   [3] venv 새로 만들고 requirements.txt 설치
#   [4] systemd 유닛 설치 + 부팅 자동시작 + watchdog
#   [5] crontab 설치 (기존 crontab 있으면 묻고 덮어씀)
#   [6] 점검 (누락 모듈·테스트)
# 봇 시작은 마지막에 직접: ./bot.sh start  (그 전에 RESTORE.md의 "수동 확인" 보기)
# ============================================================
set -euo pipefail

KBOT="$HOME/k-bot"
BOT="$KBOT/stock_bot"
ARCHIVE="${1:-}"

step() { echo; echo "━━━ $* ━━━"; }

step "[1] 시스템 패키지 (sudo 비밀번호 필요)"
sudo apt-get update
sudo apt-get install -y python3 python3-venv python3-pip python3-dev \
  build-essential git curl sqlite3 ffmpeg default-jre

if [ -n "$ARCHIVE" ]; then
  step "[2] 백업 풀기: $ARCHIVE"
  [ -f "$ARCHIVE.sha256" ] && (cd "$(dirname "$ARCHIVE")" && sha256sum -c "$(basename "$ARCHIVE").sha256")
  if [ -d "$KBOT" ]; then
    mv "$KBOT" "$KBOT.before_restore_$(date +%s)"
    echo "  기존 ~/k-bot 은 옆으로 치워둠"
  fi
  TMP=$(mktemp -d)
  tar -xzf "$ARCHIVE" -C "$TMP"
  mv "$TMP/k-bot" "$KBOT"
  SNAP=$(ls -d "$TMP"/yeongam9_full_* 2>/dev/null | head -1)
  if [ -n "$SNAP" ] && [ -d "$SNAP/db_snapshots" ]; then
    echo "  DB 일관사본으로 덮어쓰기..."
    (cd "$SNAP/db_snapshots" && find . -name '*.db' -print0) | while IFS= read -r -d '' rel; do
      rm -f "$KBOT/$rel-wal" "$KBOT/$rel-shm"
      cp "$SNAP/db_snapshots/$rel" "$KBOT/$rel"
    done
    mkdir -p "$KBOT/stock_bot/deploy/last_restore_system"
    cp -r "$SNAP/system/." "$KBOT/stock_bot/deploy/last_restore_system/"
    echo "  예전 PC 설정(crontab·pip·apt·공인IP)은 deploy/last_restore_system/ 에 둠"
  fi
  rm -rf "$TMP"
else
  step "[2] 백업 파일 없음 — 지금 있는 ~/k-bot 그대로 사용"
fi
[ -d "$BOT" ] || { echo "❌ $BOT 없음"; exit 1; }
cd "$BOT"
mkdir -p logs

step "[3] venv + 파이썬 패키지"
rm -rf venv
python3 -m venv venv
venv/bin/pip install --upgrade pip -q
venv/bin/pip install -r requirements.txt

step "[4] systemd 유닛 + 자동시작 + watchdog"
sudo cp deploy/systemd/yeongam9-* /etc/systemd/system/
sudo systemctl daemon-reload
./bot.sh enable
./bot.sh watchdog start

step "[5] crontab"
if crontab -l >/dev/null 2>&1; then
  read -r -p "  기존 crontab이 있어요. deploy/crontab.txt 로 덮어쓸까요? [y/N] " yn
  [[ "$yn" =~ ^[Yy]$ ]] && crontab deploy/crontab.txt && echo "  ✅ 설치"
else
  crontab deploy/crontab.txt && echo "  ✅ 설치"
fi

step "[6] 점검"
venv/bin/python3 tests/run_all.py | tail -3 || echo "⚠️ 테스트 실패 — 위 로그 확인"
for b in ffmpeg java; do command -v $b >/dev/null && echo "  ✅ $b" || echo "  ❌ $b 없음"; done
echo "  공인 IP: $(curl -s --max-time 5 https://api.ipify.org)  ← 업비트 Open API 허용IP에 등록돼 있어야 함"

echo
echo "🎉 복구 준비 끝. deploy/RESTORE.md 의 '수동 확인'을 본 뒤:  ./bot.sh start"
