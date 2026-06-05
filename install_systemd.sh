#!/bin/bash
# ============================================================
# install_systemd.sh — 영암9 systemd 서비스 자동 설치
# ============================================================
# 사용법: sudo bash install_systemd.sh
#
# 설치 후:
#   sudo systemctl start yeongam9-nbot    # 시작
#   sudo systemctl stop yeongam9-nbot     # 정지
#   sudo systemctl status yeongam9-nbot   # 상태
#   journalctl -u yeongam9-nbot -f        # 실시간 로그
#   journalctl -u yeongam9-nbot --since today  # 오늘 로그
# ============================================================

WORK_DIR="/home/free4tak/k-bot/stock_bot"
VENV_DIR="/home/free4tak/venv"
USER="free4tak"
SERVICE_DIR="/etc/systemd/system"

echo "🚀 영암9 systemd 서비스 설치 시작..."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# ============================================================
# 1. nbot (단타봇) — 평일 09:00~15:30
# ============================================================
cat > ${SERVICE_DIR}/yeongam9-nbot.service << EOF
[Unit]
Description=영암9 단타봇 (nbot)
After=network.target
Wants=network-online.target

[Service]
Type=simple
User=${USER}
WorkingDirectory=${WORK_DIR}
Environment=PATH=${VENV_DIR}/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=${VENV_DIR}/bin/python3 -u nbot.py

# ★ 자동 재시작
Restart=on-failure
RestartSec=30s
StartLimitIntervalSec=300
StartLimitBurst=5

# 로그
StandardOutput=append:${WORK_DIR}/logs/nbot.log
StandardError=append:${WORK_DIR}/logs/nbot.log

# 종료 대기 (포지션 정리 시간)
TimeoutStopSec=60

[Install]
WantedBy=multi-user.target
EOF
echo "✅ yeongam9-nbot.service 생성"

# ============================================================
# 2. sbot (스윙봇) — 상시 실행
# ============================================================
cat > ${SERVICE_DIR}/yeongam9-sbot.service << EOF
[Unit]
Description=영암9 스윙봇 (sbot)
After=network.target
Wants=network-online.target

[Service]
Type=simple
User=${USER}
WorkingDirectory=${WORK_DIR}
Environment=PATH=${VENV_DIR}/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=${VENV_DIR}/bin/python3 -u sbot.py

Restart=on-failure
RestartSec=30s
StartLimitIntervalSec=300
StartLimitBurst=5

StandardOutput=append:${WORK_DIR}/logs/sbot.log
StandardError=append:${WORK_DIR}/logs/sbot.log

TimeoutStopSec=60

[Install]
WantedBy=multi-user.target
EOF
echo "✅ yeongam9-sbot.service 생성"

# ============================================================
# 3. ebot (종가봇) — 15:00 이후 자동 대기
# ============================================================
cat > ${SERVICE_DIR}/yeongam9-ebot.service << EOF
[Unit]
Description=영암9 종가봇 (ebot)
After=network.target
Wants=network-online.target

[Service]
Type=simple
User=${USER}
WorkingDirectory=${WORK_DIR}
Environment=PATH=${VENV_DIR}/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=${VENV_DIR}/bin/python3 -u ebot.py

Restart=on-failure
RestartSec=30s
StartLimitIntervalSec=300
StartLimitBurst=5

StandardOutput=append:${WORK_DIR}/logs/ebot.log
StandardError=append:${WORK_DIR}/logs/ebot.log

TimeoutStopSec=60

[Install]
WantedBy=multi-user.target
EOF
echo "✅ yeongam9-ebot.service 생성"

# ============================================================
# 4. cbot (코인봇) — 24시간 상시
# ============================================================
cat > ${SERVICE_DIR}/yeongam9-cbot.service << EOF
[Unit]
Description=영암9 코인봇 (cbot)
After=network.target
Wants=network-online.target

[Service]
Type=simple
User=${USER}
WorkingDirectory=${WORK_DIR}
Environment=PATH=${VENV_DIR}/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=${VENV_DIR}/bin/python3 -u cbot.py

Restart=on-failure
RestartSec=30s
StartLimitIntervalSec=300
StartLimitBurst=5

StandardOutput=append:${WORK_DIR}/logs/cbot.log
StandardError=append:${WORK_DIR}/logs/cbot.log

TimeoutStopSec=60

[Install]
WantedBy=multi-user.target
EOF
echo "✅ yeongam9-cbot.service 생성"

# ============================================================
# 5. kiki (AI 비서) — 24시간 상시
# ============================================================
cat > ${SERVICE_DIR}/yeongam9-kiki.service << EOF
[Unit]
Description=영암9 AI 비서 키키 (kiki)
After=network.target
Wants=network-online.target
# nbot보다 늦게 시작 (상태파일 생성 후 읽어야 함)
After=yeongam9-nbot.service

[Service]
Type=simple
User=${USER}
WorkingDirectory=${WORK_DIR}
Environment=PATH=${VENV_DIR}/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=${VENV_DIR}/bin/python3 -u kiki.py

Restart=on-failure
RestartSec=30s
StartLimitIntervalSec=300
StartLimitBurst=5

StandardOutput=append:${WORK_DIR}/logs/kiki.log
StandardError=append:${WORK_DIR}/logs/kiki.log

# 디스코드 연결 끊김 자동 복구 시간
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
EOF
echo "✅ yeongam9-kiki.service 생성"

# ============================================================
# 6. 그룹 서비스 (5개 한번에 관리)
# ============================================================
cat > ${SERVICE_DIR}/yeongam9.target << EOF
[Unit]
Description=영암9 자동매매 봇 전체
Requires=yeongam9-nbot.service yeongam9-sbot.service
Requires=yeongam9-ebot.service yeongam9-cbot.service
Requires=yeongam9-kiki.service
After=yeongam9-nbot.service yeongam9-sbot.service
After=yeongam9-ebot.service yeongam9-cbot.service
After=yeongam9-kiki.service

[Install]
WantedBy=multi-user.target
EOF
echo "✅ yeongam9.target 생성 (그룹 관리용)"

# ============================================================
# 7. logs 폴더 생성
# ============================================================
mkdir -p ${WORK_DIR}/logs
chown ${USER}:${USER} ${WORK_DIR}/logs
echo "✅ logs 폴더 확인"

# ============================================================
# 8. systemd 리로드 + 서비스 활성화
# ============================================================
systemctl daemon-reload
echo "✅ systemd daemon-reload"

# 부팅 시 자동 시작 등록
for svc in nbot sbot ebot cbot kiki; do
    systemctl enable yeongam9-${svc}.service
    echo "✅ yeongam9-${svc} 부팅 자동시작 등록"
done

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "🎉 설치 완료!"
echo ""
echo "📋 사용법:"
echo ""
echo "  [전체 시작/정지]"
echo "  sudo systemctl start yeongam9.target"
echo "  sudo systemctl stop yeongam9.target"
echo ""
echo "  [개별 봇 관리]"
echo "  sudo systemctl start yeongam9-nbot"
echo "  sudo systemctl stop yeongam9-nbot"
echo "  sudo systemctl restart yeongam9-nbot"
echo "  sudo systemctl status yeongam9-nbot"
echo ""
echo "  [로그 확인]"
echo "  journalctl -u yeongam9-nbot -f              # 실시간"
echo "  journalctl -u yeongam9-nbot --since today   # 오늘"
echo "  journalctl -u yeongam9-nbot -n 100          # 최근 100줄"
echo "  journalctl -u yeongam9-nbot --since '1 hour ago'"
echo ""
echo "  [전체 로그 한번에]"
echo "  journalctl -u 'yeongam9-*' -f"
echo ""
echo "  [상태 한눈에]"
echo "  systemctl status 'yeongam9-*'"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
