#!/bin/bash
# weekly_review.sh — 토요일 주간 리뷰 자동화 (nbot + sbot 통합)
# 사용: cd /home/free4tak/k-bot/stock_bot/backtest && bash weekly_review.sh
set +e

VENV="/home/free4tak/k-bot/stock_bot/venv/bin/python3"
DIR="/home/free4tak/k-bot/stock_bot/backtest"
export K_BOT_ROOT="/home/free4tak/k-bot/stock_bot"
TODAY=$(date +%Y-%m-%d)
NBOT_START="2025-08-01"
SBOT_START="2024-06-01"

echo ""
echo "╔══════════════════════════════════════════════════╗"
echo "║       영암9 주간 백테스트 리뷰                   ║"
echo "║       $(date '+%Y년 %m월 %d일 %H:%M')            ║"
echo "╚══════════════════════════════════════════════════╝"
echo ""

cd $DIR

# ── Step 1: OHLCV 최신화 ──────────────────────────────────
echo "📡 [1/4] OHLCV 데이터 최신화..."
$VENV fetch_history_fdr.py \
    --start 2024-05-08 \
    --end $TODAY \
    --top 50 \
    2>&1 | grep -E "완료|실패|오류|OHLCV"

# ── Step 2: 수급 최신화 ───────────────────────────────────
echo ""
echo "📡 [2/4] 수급 데이터 최신화..."
$VENV fetch_investor.py \
    2>&1 | grep -E "완료|실패|누적|기간"

# ── Step 3: nbot 백테스트 ────────────────────────────────
echo ""
echo "🚀 [3/4] 단타봇(nbot) 백테스트 실행 중..."
$VENV run_backtest.py \
    --compare \
    --start $NBOT_START \
    --end $TODAY \
    --max-codes 50 \
    2>&1 | grep -E "시나리오|수익률|승률|MDD|샤프|PF|거래|─|═|저장"

sleep 2
NBOT_LATEST=$(ls -t results/result_*.json 2>/dev/null | grep -v sbot | head -1)
echo "📋 nbot 결과: ${NBOT_LATEST:-없음}"
# ── Step 4: sbot 백테스트 ────────────────────────────────
echo ""
echo "🚀 [4/4] 스윙봇(sbot) 백테스트 실행 중..."
$VENV run_sbot_backtest.py \
    --compare \
    --start $SBOT_START \
    --end $TODAY \
    --max-codes 50 \
    2>&1 | grep -E "시나리오|수익률|승률|MDD|샤프|PF|거래|─|═|저장|판단"

sleep 2
SBOT_LATEST=$(ls -t results/sbot_result_*.json 2>/dev/null | head -1)

# ── HTML 통합 리포트 생성 ────────────────────────────────
echo ""
echo "📊 통합 HTML 리포트 생성..."

if [ -z "$NBOT_LATEST" ] && [ -z "$SBOT_LATEST" ]; then
    echo "❌ 결과 파일 없음"
    exit 1
fi

$VENV generate_combined_report.py \
    --nbot  "${NBOT_LATEST:-none}" \
    --sbot  "${SBOT_LATEST:-none}" \
    --date  "$TODAY"

# 기존 nbot 단독 리포트도 유지
if [ -n "$NBOT_LATEST" ]; then
    $VENV generate_report.py "$NBOT_LATEST" "$TODAY" 2>/dev/null || true
fi

echo ""
echo "╔══════════════════════════════════════════════════╗"
echo "║  ✅ 완료! 브라우저에서 열기:                     ║"
echo "║  xdg-open results/weekly_report_$TODAY.html ║"
echo "╚══════════════════════════════════════════════════╝"
echo ""

# 자동으로 브라우저 열기 시도
REPORT="$DIR/results/weekly_report_$TODAY.html"
if [ -f "$REPORT" ]; then
    xdg-open "$REPORT" 2>/dev/null || \
    firefox  "$REPORT" 2>/dev/null || \
    echo "📂 브라우저에서 직접 열기: $REPORT"
fi
