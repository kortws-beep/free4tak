#!/bin/bash
# ============================================================
# watchdog_sbot.sh — sbot heartbeat 감시
# 5분(300초) 이상 heartbeat 없으면 자동 재시작
# ============================================================

HB_FILE="/tmp/hb_sbot"
MAX_AGE=300   # 5분
BOT_NAME="yeongam9-sbot"
LOG_TAG="[watchdog-sbot]"

if [ ! -f "$HB_FILE" ]; then
    echo "$LOG_TAG heartbeat 파일 없음 — 대기 중"
    exit 0
fi

# ★ 2026-07-29: 사용자가 장이 안 좋아 sbot을 일부러 정지시켰는데,
#   heartbeat 파일 나이만 보고 5분 뒤 무조건 재시작시키던 버그 발견
#   (사용자 지적 — "정지시켰는데 다시 시작시키네"). systemd 서비스가
#   지금 active(=프로세스는 떠있는데 응답 없음, 진짜 hang)일 때만
#   재시작하고, inactive(=사용자가 의도적으로 stop시킴, 또는 크래시는
#   이미 Restart=on-failure가 30초 내로 알아서 복구함)면 건드리지 않는다.
if ! systemctl is-active --quiet "$BOT_NAME"; then
    echo "$LOG_TAG 서비스 inactive — 의도적 정지로 판단, 재시작 안 함"
    exit 0
fi

NOW=$(date +%s)
FILE_TIME=$(stat -c %Y "$HB_FILE" 2>/dev/null || echo 0)
AGE=$((NOW - FILE_TIME))

if [ "$AGE" -gt "$MAX_AGE" ]; then
    echo "$LOG_TAG ⚠️ heartbeat ${AGE}초 경과 (기준: ${MAX_AGE}초) → sbot 재시작"
    logger -t "watchdog_sbot" "heartbeat ${AGE}초 경과 → 재시작"
    sudo systemctl restart "$BOT_NAME"
    rm -f "$HB_FILE"
    echo "$LOG_TAG 🔄 재시작 완료"
else
    echo "$LOG_TAG ✅ heartbeat 정상 (${AGE}초 전)"
fi
