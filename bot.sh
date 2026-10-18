#!/bin/bash
# ============================================================
# bot.sh — 영암9 봇 통합 관리 스크립트
# ============================================================
# 사용법:
#   ./bot.sh start [봇]   # 봇 이름 없거나 all이면 전체, 있으면 그 봇만
#   ./bot.sh stop [봇]
#   ./bot.sh restart [봇]
#   ./bot.sh status       # 전체 상태
#   ./bot.sh log daybot   # 단타봇 실시간 로그
#   ./bot.sh log all      # 전체 실시간 로그
#   모르는 봇 이름(오타 등)은 아무것도 안 하고 에러 — 예전엔 전체로 처리돼 "stop Cbot"이 8개 다 멈췄음(10-18 수정)
# ============================================================

# ★ 2026-09-29: sbot×sbo2 통합(대장 결정)으로 sbo2 의도적 정지 — 전체
#   start/restart/enable에 끼어있으면 되살아나버리므로 목록에서 제외.
#   개별 "./bot.sh {start|stop|restart} sbo2"는 여전히 동작(서비스 자체는
#   롤백 대비 살아있음), status/log/today도 인자로 sbo2 주면 그대로 조회 가능.
# ★ 2026-09-29 밤: daybot(단타봇) 신설 — sbo2가 쓰던 계좌 재사용, 키움
#   스크리닝+KIS 실행 하이브리드. 배포 전 sbo2 완전정지(disable) 필수.
# ★ 2026-10-07 — youtube-live(intelligence/youtube_live_monitor.py, 지속실행
#   서비스) 추가. pkill -f "${bot}.py" 좀비청소 단계는 스크립트 파일명이
#   youtube_live_monitor.py라 "youtube-live.py"와 안 맞아 매칭 안 되지만,
#   그건 수동실행 잔재 프로세스 청소용이라 무해(systemctl 쪽은 이름이
#   정확히 yeongam9-youtube-live라 정상 동작).
BOTS="sbot cbot daybot kiki sector dashboard lina youtube-live"
KNOWN="$BOTS sbo2"     # 개별 지정만 되는 서비스(전체 목록엔 없음)

# start/stop/restart 대상: 이름 없음·all → 전체($BOTS), 아는 이름 → 그 봇만, 그 외 → 에러로 종료
pick_targets() {
  if [ -z "$1" ] || [ "$1" = "all" ]; then
    TARGETS="$BOTS"; SCOPE="전체"
  elif [[ " $KNOWN " == *" $1 "* ]]; then
    TARGETS="$1"; SCOPE="$1"
  else
    echo "❌ 모르는 봇 이름: '$1' — 아무것도 안 했어요. 가능: $KNOWN (또는 all)"
    exit 1
  fi
}

case "$1" in

  start)
    pick_targets "$2"
    echo "🚀 영암9 ${SCOPE} 시작..."
    [ "$SCOPE" = "전체" ] && sudo systemctl daemon-reload   # 비밀번호 필요 — 개별 시작 땐 생략
    for bot in $TARGETS; do
      if [ "$(systemctl is-active yeongam9-${bot} 2>/dev/null)" = "active" ]; then
        echo "  ✔️  yeongam9-${bot} 이미 실행 중"
        continue
      fi
      # ★ 수동 실행된 좀비 프로세스 정리 — 서비스가 꺼져 있을 때만(돌고 있는 봇을 죽이지 않게)
      pkill -f "${bot}.py" 2>/dev/null && echo "  🧹 ${bot} 좀비 프로세스 정리" && sleep 1
      sudo systemctl start yeongam9-${bot}
      sleep 1
      if [ "$(systemctl is-active yeongam9-${bot} 2>/dev/null)" = "active" ]; then
        echo "  ▶️  yeongam9-${bot} 시작"
      else
        echo "  ❌ yeongam9-${bot} 시작 실패 (journalctl -u yeongam9-${bot} -n 20)"
      fi
    done
    echo "✅ 완료"
    ;;

  stop)
    pick_targets "$2"
    echo "🛑 영암9 ${SCOPE} 정지..."
    for bot in $TARGETS; do
      sudo systemctl stop yeongam9-${bot}
      echo "  ⏹  yeongam9-${bot} 정지"
    done
    echo "✅ 완료"
    ;;

  restart)
    pick_targets "$2"
    if [ "$SCOPE" = "전체" ]; then
      echo "🔄 영암9 전체 재시작..."
      "$0" stop all
      sleep 3
      "$0" start all
    else
      echo "🔄 $2 재시작..."
      sudo systemctl restart yeongam9-$2
      sleep 2
      if [ "$(systemctl is-active yeongam9-$2 2>/dev/null)" = "active" ]; then
        echo "  ✅ $2 재시작 완료"
      else
        echo "  ❌ $2 재시작 실패 (journalctl -u yeongam9-$2 -n 20)"
      fi
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
      journalctl -u yeongam9-${bot} -f --output=cat
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

  watchdog)
    case "$2" in
      start)
        echo "🐕 watchdog 시작..."
        sudo systemctl daemon-reload
        sudo systemctl start yeongam9-watchdog-sbo2.timer
        sudo systemctl enable yeongam9-watchdog-sbo2.timer
        sudo systemctl start yeongam9-watchdog-sbot.timer
        sudo systemctl enable yeongam9-watchdog-sbot.timer
        sudo systemctl start yeongam9-watchdog-daybot.timer
        sudo systemctl enable yeongam9-watchdog-daybot.timer
        sudo systemctl start yeongam9-watchdog-cbot.timer
        sudo systemctl enable yeongam9-watchdog-cbot.timer
        echo "  ✅ sbo2 + sbot + daybot + cbot watchdog 시작 (30초 간격, 5분 무응답 시 재시작)"
        ;;
      stop)
        echo "🐕 watchdog 정지..."
        sudo systemctl stop yeongam9-watchdog-sbo2.timer
        sudo systemctl stop yeongam9-watchdog-sbot.timer
        sudo systemctl stop yeongam9-watchdog-daybot.timer
        sudo systemctl stop yeongam9-watchdog-cbot.timer
        echo "  ⏹  sbo2 + sbot + daybot + cbot watchdog 정지"
        ;;
      status)
        echo "🐕 watchdog 상태"
        systemctl status yeongam9-watchdog-sbo2.timer --no-pager
        systemctl status yeongam9-watchdog-sbot.timer --no-pager
        systemctl status yeongam9-watchdog-daybot.timer --no-pager
        systemctl status yeongam9-watchdog-cbot.timer --no-pager
        ;;
      *)
        echo "사용법: $0 watchdog {start|stop|status}"
        ;;
    esac
    ;;
  *)
    echo "사용법: $0 {start|stop|restart [bot]|status|log [bot]|today [bot]|enable|disable|git [msg]|push}"
    echo ""
    echo "  start [bot]   — 봇 시작 (이름 없거나 all이면 전체)"
    echo "  stop [bot]    — 봇 정지 (이름 없거나 all이면 전체)"
    echo "  restart [bot] — 봇 재시작 (이름 없거나 all이면 전체)"
    echo "                  봇: $KNOWN"
    echo "  status        — 봇별 실행 상태"
    echo "  log [bot]     — 실시간 로그 (all/sbot/sbo2/cbot/daybot/kiki/lina)"
    echo "  today [bot]   — 오늘 로그"
    echo "  enable        — 부팅 자동시작 등록"
    echo "  disable       — 부팅 자동시작 해제"
    ;;
esac

