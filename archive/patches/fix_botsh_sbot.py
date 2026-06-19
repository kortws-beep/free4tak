import sys

path = sys.argv[1] if len(sys.argv) > 1 else "bot.sh"

with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

old = '''  watchdog)
    case "$2" in
      start)
        echo "🐕 watchdog 시작..."
        sudo systemctl daemon-reload
        sudo systemctl start yeongam9-watchdog-sbo2.timer
        sudo systemctl enable yeongam9-watchdog-sbo2.timer
        echo "  ✅ sbo2 watchdog 시작 (30초 간격, 5분 무응답 시 재시작)"
        ;;
      stop)
        echo "🐕 watchdog 정지..."
        sudo systemctl stop yeongam9-watchdog-sbo2.timer
        echo "  ⏹  sbo2 watchdog 정지"
        ;;
      status)
        echo "🐕 watchdog 상태"
        systemctl status yeongam9-watchdog-sbo2.timer --no-pager
        ;;
      *)
        echo "사용법: $0 watchdog {start|stop|status}"
        ;;
    esac
    ;;'''

new = '''  watchdog)
    case "$2" in
      start)
        echo "🐕 watchdog 시작..."
        sudo systemctl daemon-reload
        sudo systemctl start yeongam9-watchdog-sbo2.timer
        sudo systemctl enable yeongam9-watchdog-sbo2.timer
        sudo systemctl start yeongam9-watchdog-sbot.timer
        sudo systemctl enable yeongam9-watchdog-sbot.timer
        echo "  ✅ sbo2 + sbot watchdog 시작 (30초 간격, 5분 무응답 시 재시작)"
        ;;
      stop)
        echo "🐕 watchdog 정지..."
        sudo systemctl stop yeongam9-watchdog-sbo2.timer
        sudo systemctl stop yeongam9-watchdog-sbot.timer
        echo "  ⏹  sbo2 + sbot watchdog 정지"
        ;;
      status)
        echo "🐕 watchdog 상태"
        systemctl status yeongam9-watchdog-sbo2.timer --no-pager
        systemctl status yeongam9-watchdog-sbot.timer --no-pager
        ;;
      *)
        echo "사용법: $0 watchdog {start|stop|status}"
        ;;
    esac
    ;;'''

if old in content:
    content = content.replace(old, new, 1)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)
    print("✅ 수정 완료")
else:
    print("❌ 패턴 미일치")
