#!/bin/bash
# ============================================================
# bot.sh — 영암9 봇 통합 관리 스크립트
# ============================================================
# 사용법:
#   ./bot.sh start        # 전체 시작
#   ./bot.sh stop         # 전체 정지
#   ./bot.sh restart      # 전체 재시작
#   ./bot.sh status       # 전체 상태
#   ./bot.sh log nbot     # 단타봇 실시간 로그
#   ./bot.sh log all      # 전체 실시간 로그
# ============================================================

BOTS="sbot sbot2 cbot kiki sector dashboard telegram"

case "$1" in

  start)
    echo "🚀 영암9 전체 시작..."
    sudo systemctl daemon-reload
    # ★ 수동 실행된 좀비 프로세스 정리
    for bot in $BOTS; do
      pkill -f "${bot}.py" 2>/dev/null && echo "  🧹 ${bot} 좀비 프로세스 정리"
    done
    sleep 1
    for bot in $BOTS; do
      sudo systemctl start yeongam9-${bot}
      echo "  ▶️  yeongam9-${bot} 시작"
      sleep 1
    done
    echo "✅ 완료"
    ;;

  stop)
    if [ -n "$2" ] && echo "$BOTS" | grep -qw "$2"; then
      echo "🛑 $2 정지..."
      sudo systemctl stop yeongam9-$2
      echo "  ⏹  yeongam9-$2 정지"
    else
      echo "🛑 영암9 전체 정지..."
      for bot in $BOTS; do
        sudo systemctl stop yeongam9-${bot}
        echo "  ⏹  yeongam9-${bot} 정지"
      done
    fi
    echo "✅ 완료"
    ;;

  restart)
    if [ -n "$2" ] && echo "$BOTS" | grep -qw "$2"; then
      echo "🔄 $2 재시작..."
      sudo systemctl restart yeongam9-$2
      sleep 2
      status=$(systemctl is-active yeongam9-$2 2>/dev/null)
      if [ "$status" = "active" ]; then
        echo "  ✅ $2 재시작 완료"
      else
        echo "  ❌ $2 재시작 실패"
      fi
    else
      echo "🔄 영암9 전체 재시작..."
      $0 stop
      sleep 3
      $0 start
    fi
    ;;

  status)
    echo "📊 영암9 봇 상태"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    for bot in $BOTS; do
      status=$(systemctl is-active yeongam9-${bot} 2>/dev/null)
      pid=$(systemctl show yeongam9-${bot} --property=MainPID --value 2>/dev/null)
      if [ "$status" = "active" ]; then
        echo "  ✅ ${bot} (PID: ${pid})"
      elif [ "$status" = "failed" ]; then
        echo "  ❌ ${bot} — 실패! (journalctl -u yeongam9-${bot} -n 20)"
      else
        echo "  ⏹  ${bot} — 정지됨"
      fi
    done
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    ;;

  log)
    bot="${2:-all}"
    if [ "$bot" = "all" ]; then
      echo "📋 전체 봇 실시간 로그 (Ctrl+C로 종료)"
      journalctl -u 'yeongam9-*' -f --output=short-monotonic
    else
      echo "📋 yeongam9-${bot} 실시간 로그 (Ctrl+C로 종료)"
      journalctl -u yeongam9-${bot} -f
    fi
    ;;

  today)
    bot="${2:-all}"
    if [ "$bot" = "all" ]; then
      journalctl -u 'yeongam9-*' --since today
    else
      journalctl -u yeongam9-${bot} --since today
    fi
    ;;

  enable)
    echo "🔧 부팅 자동시작 등록..."
    for bot in $BOTS; do
      sudo systemctl enable yeongam9-${bot}
      echo "  ✅ yeongam9-${bot} 등록"
    done
    ;;

  disable)
    echo "🔧 부팅 자동시작 해제..."
    for bot in $BOTS; do
      sudo systemctl disable yeongam9-${bot}
      echo "  ✅ yeongam9-${bot} 해제"
    done
    ;;

  git|push)
    cd ~/k-bot/stock_bot
    MSG="${2:-$(date '+%Y-%m-%d %H:%M') 업데이트}"
    echo "📦 GitHub 업로드: $MSG"
    git add .
    git commit -m "$MSG" 2>/dev/null || echo "변경사항 없음"
    git push
    echo "✅ 완료"
    ;;
  *)
    echo "사용법: $0 {start|stop|restart|status|log [bot]|today [bot]|enable|disable|git [msg]|push}"
    echo ""
    echo "  start         — 전체 봇 시작"
    echo "  stop          — 전체 봇 정지"
    echo "  restart       — 전체 봇 재시작"
    echo "  status        — 봇별 실행 상태"
    echo "  log [bot]     — 실시간 로그 (all/sbot/sbot2/cbot/kiki)"
    echo "  today [bot]   — 오늘 로그"
    echo "  enable        — 부팅 자동시작 등록"
    echo "  disable       — 부팅 자동시작 해제"
    ;;
esac

