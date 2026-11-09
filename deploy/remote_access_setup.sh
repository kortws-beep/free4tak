#!/bin/bash
# ============================================================
# deploy/remote_access_setup.sh — 원격접속 복구 (Tailscale + SSH + 방화벽)
# ============================================================
# 사용법:  sudo deploy/remote_access_setup.sh
#
# 순서가 중요: 방화벽(ufw)을 먼저 잠근 뒤에 원격 화면(3389)을 켬.
#   이 서버는 공인IP 직결 — 열린 포트는 곧장 인터넷에 노출됨.
#
# 1) ufw: 기본 차단 · 3389(원격화면)는 Tailscale 대역만 · 22(SSH)·5055는 tailscale0만
# 2) Tailscale 설치 — 예전 디스크 상태파일이 있으면 그대로 옮겨서
#    예전과 같은 기기·같은 IP(100.81.218.118)로 붙음 (윈도우 쪽 설정 그대로)
# 3) openssh-server 설치
# 원격 화면(GNOME 원격 데스크톱) 켜기는 사용자 권한이라 누리가 따로 함.
# ============================================================
set -euo pipefail
[ "$(id -u)" = "0" ] || { echo "❌ sudo로 실행하세요: sudo $0"; exit 1; }

echo "━━━ [1] 방화벽(ufw) ━━━"
apt-get install -y ufw >/dev/null
ufw default deny incoming
ufw default allow outgoing
ufw allow from 100.64.0.0/10 to any port 3389 proto tcp comment 'RDP Tailscale only'
ufw allow in on tailscale0 to any port 22 proto tcp comment 'SSH Tailscale only'
ufw allow in on tailscale0 to any port 5055 proto tcp comment 'smartfarm Tailscale only'
ufw --force enable
ufw status verbose

echo "━━━ [2] Tailscale ━━━"
if ! command -v tailscale >/dev/null; then
  curl -fsSL https://tailscale.com/install.sh | sh
fi
OLD=/mnt/old/var/lib/tailscale/tailscaled.state
if [ -f "$OLD" ] && [ ! -s /var/lib/tailscale/tailscaled.state.restored ]; then
  echo "📥 예전 서버의 Tailscale 신원 복원 (같은 IP 유지)"
  systemctl stop tailscaled
  mkdir -p /var/lib/tailscale
  cp -a "$OLD" /var/lib/tailscale/tailscaled.state
  date > /var/lib/tailscale/tailscaled.state.restored
fi
systemctl enable --now tailscaled
sleep 3
if ! tailscale status >/dev/null 2>&1; then
  echo "🔑 아래 주소로 로그인하세요 (예전 계정 그대로)"
  tailscale up
fi
echo "Tailscale IP: $(tailscale ip -4)"

echo "━━━ [3] SSH 서버 ━━━"
apt-get install -y openssh-server >/dev/null
systemctl enable --now ssh
echo "✅ 완료 — 이제 누리에게 '원격화면 켜줘' 하면 됨"
