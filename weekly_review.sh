#!/bin/bash
# weekly_review.sh — 토요일 주간 리뷰 자동화 (sbot + sbo2 통합)
# 사용: cd /home/free4tak/k-bot/stock_bot && bash weekly_review.sh
set +e

VENV="/home/free4tak/k-bot/stock_bot/venv/bin/python3"
STOCK_BOT="/home/free4tak/k-bot/stock_bot"
BACKTEST_DIR="$STOCK_BOT/backtest"
LINA_BACKTEST_DIR="$STOCK_BOT/lina_bot/backtest"
TODAY=$(date +%Y-%m-%d)
SBOT_START="2024-06-01"
SBO2_START="2025-12-01"

echo ""
echo "╔══════════════════════════════════════════════════╗"
echo "║       영암9 주간 백테스트 리뷰                   ║"
echo "║       $(date '+%Y년 %m월 %d일 %H:%M')            ║"
echo "╚══════════════════════════════════════════════════╝"
echo ""

# ── Step 1: sbot 백테스트 ────────────────────────────────
echo "🚀 [1/3] 스윙봇(sbot) 백테스트 실행 중..."
cd $BACKTEST_DIR
$VENV run_sbot_backtest.py \
    --compare \
    --start $SBOT_START \
    --end $TODAY \
    --max-codes 50 \
    2>&1 | grep -E "시나리오|수익률|승률|MDD|PF|거래|─|═|저장|판단"

sleep 2
SBOT_LATEST=$(ls -t results/sbot_result_*.json 2>/dev/null | head -1)
echo "📋 sbot 결과: ${SBOT_LATEST:-없음}"

# ── Step 2: sbo2(lina) 백테스트 ─────────────────────────
echo ""
echo "🚀 [2/3] 리나 스윙봇(sbo2) 백테스트 실행 중..."
cd $LINA_BACKTEST_DIR
$VENV lina_backtest.py \
    --compare \
    --start $SBO2_START \
    --end $TODAY \
    2>&1 | grep -E "시나리오|수익률|승률|MDD|PF|거래|─|═|저장|판단|최종"

sleep 2
SBO2_LATEST=$(ls -t $LINA_BACKTEST_DIR/results/lina_backtest_*.json 2>/dev/null | head -1)
echo "📋 sbo2 결과: ${SBO2_LATEST:-없음}"

# ── Step 3: 통합 리포트 ──────────────────────────────────
echo ""
echo "📊 [3/3] 통합 HTML 리포트 생성..."
cd $BACKTEST_DIR

$VENV generate_combined_report.py \
    --sbot  "${SBOT_LATEST:-none}" \
    --sbo2  "${SBO2_LATEST:-none}" \
    --date  "$TODAY"

echo ""
echo "╔══════════════════════════════════════════════════╗"
echo "║  ✅ 완료! 브라우저에서 열기:                     ║"
echo "║  xdg-open results/weekly_report_$TODAY.html ║"
echo "╚══════════════════════════════════════════════════╝"
echo ""

# 자동으로 브라우저 열기 시도
REPORT="$BACKTEST_DIR/results/weekly_report_$TODAY.html"
if [ -f "$REPORT" ]; then
    xdg-open "$REPORT" 2>/dev/null || \
    firefox  "$REPORT" 2>/dev/null || \
    echo "📂 브라우저에서 직접 열기: $REPORT"
fi
