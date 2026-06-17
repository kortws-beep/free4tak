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
