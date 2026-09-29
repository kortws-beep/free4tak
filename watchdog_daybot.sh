#!/bin/bash
# ============================================================
# watchdog_daybot.sh — daybot heartbeat 감시
# 5분(300초) 이상 heartbeat 없으면 자동 재시작
# ============================================================

HB_FILE="/tmp/hb_daybot"
MAX_AGE=300   # 5분
BOT_NAME="yeongam9-daybot"
LOG_TAG="[watchdog-daybot]"

# heartbeat 파일 없으면 daybot이 아직 시작 안 된 것 — 패스
if [ ! -f "$HB_FILE" ]; then
    echo "$LOG_TAG heartbeat 파일 없음 — 대기 중"
    exit 0
fi

# 의도적 정지(inactive)면 건드리지 않음 — sbo2/sbot과 동일한 가드
# (watchdog_sbo2.sh 2026-07-29 수정 패턴 그대로 이식)
if ! systemctl is-active --quiet "$BOT_NAME"; then
    echo "$LOG_TAG 서비스 inactive — 의도적 정지로 판단, 재시작 안 함"
    exit 0
fi

# 파일 최종 수정 시간 기준 경과 시간(초)
NOW=$(date +%s)
FILE_TIME=$(stat -c %Y "$HB_FILE" 2>/dev/null || echo 0)
AGE=$((NOW - FILE_TIME))

if [ "$AGE" -gt "$MAX_AGE" ]; then
    echo "$LOG_TAG ⚠️ heartbeat ${AGE}초 경과 (기준: ${MAX_AGE}초) → daybot 재시작"
    logger -t "watchdog_daybot" "heartbeat ${AGE}초 경과 → 재시작"
    sudo systemctl restart "$BOT_NAME"
    # heartbeat 파일 초기화 (중복 재시작 방지)
    rm -f "$HB_FILE"
    echo "$LOG_TAG 🔄 재시작 완료"
else
    echo "$LOG_TAG ✅ heartbeat 정상 (${AGE}초 전)"
fi
