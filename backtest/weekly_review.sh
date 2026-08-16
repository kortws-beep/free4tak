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
echo "🚀 [1/4] 스윙봇(sbot) 백테스트 실행 중..."
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
echo "🚀 [2/4] 리나 스윙봇(sbo2) 백테스트 실행 중..."
cd $LINA_BACKTEST_DIR
$VENV lina_backtest.py \
    --compare \
    --start $SBO2_START \
    --end $TODAY \
    2>&1 | grep -E "시나리오|수익률|승률|MDD|PF|거래|─|═|저장|판단|최종"

sleep 2
SBO2_LATEST=$(ls -t results/lina_backtest_*.json 2>/dev/null | head -1)
echo "📋 sbo2 결과: ${SBO2_LATEST:-없음}"

# ── Step 3: sbo2 실거래 신호 사후검증 ────────────────────
#   ★ Step 2는 가상 시뮬레이션(swing_analyzer 자체 재현, 4슬롯/ATR
#     트레일링 미반영)이라 실거래와 차이가 있을 수 있음. 이 단계는
#     실제 운영 중 쌓인 sbo2_candidates(VCP/추세/텔레 추천 로그)를
#     실거래와 동일한 ATR×2.0/3.0 로직으로 사후검증한다. (2026-06-27 추가)
echo ""
echo "🚀 [3/4] sbo2 실거래 신호(VCP/추세/텔레) 사후검증 중..."
cd $BACKTEST_DIR
$VENV run_sbo2_signal_check.py --hold-days 25 \
    2>&1 | grep -E "분석 대상|시뮬레이션 완료|슬롯별|점수구간별|총|목표1도달|평균|저장|⚠️"

sleep 1
SIGNAL_CHECK_LATEST=$(ls -t results/sbo2_signal_check_*.json 2>/dev/null | head -1)
echo "📋 sbo2 신호검증 결과: ${SIGNAL_CHECK_LATEST:-없음}"

# ── Step 4: 통합 리포트 ──────────────────────────────────
echo ""
echo "📊 [4/4] 통합 HTML 리포트 생성..."
cd $BACKTEST_DIR

$VENV generate_combined_report.py \
    --sbot  "${SBOT_LATEST:-none}" \
    --sbo2  "${SBO2_LATEST:-none}" \
    --signal-check "${SIGNAL_CHECK_LATEST:-none}" \
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
