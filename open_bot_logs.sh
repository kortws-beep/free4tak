#!/bin/bash
# ============================================================
# open_bot_logs.sh — 재부팅 후 봇 로그 콘솔 자동 오픈
# ============================================================
# GNOME 자동로그인 + autostart로 그래픽 세션 뜨자마자 실행됨.
# 각 봇/서비스 로그를 별도 ptyxis 창으로 하나씩 띄운다.
# ============================================================

cd /home/free4tak/k-bot/stock_bot || exit 1

# systemd 서비스들이 완전히 뜰 시간 확보 (로그파일 생성 등)
sleep 8

ptyxis --new-window -x "bash -c 'tail -F logs/sbot.log'" &
sleep 1
ptyxis --new-window -x "bash -c 'journalctl -u yeongam9-sbo2 -f --output=cat'" &
sleep 1
ptyxis --new-window -x "bash -c 'tail -F logs/cbot.log'" &
sleep 1
ptyxis --new-window -x "bash -c 'tail -F logs/daybot.log'" &
sleep 1
ptyxis --new-window -x "bash -c 'tail -F logs/kiki.log'" &
sleep 1
ptyxis --new-window -x "bash -c 'journalctl -u yeongam9-lina -f --output=cat'" &
sleep 1
ptyxis --new-window -x "bash -c 'tail -F logs/sector_monitor.log'" &
sleep 1
ptyxis --new-window -x "bash -c 'sudo docker logs -f mawari-node'" &
