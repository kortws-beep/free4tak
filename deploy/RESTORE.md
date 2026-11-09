# 영암9 새 PC 복구 안내

2026-11-09 새 PC로 옮기면서 겪은 문제를 바탕으로 만든 안내서입니다.

## 백업 (평소에)

```bash
~/k-bot/stock_bot/deploy/backup.sh                     # → ~/backups/yeongam9_full_날짜.tar.gz
~/k-bot/stock_bot/deploy/backup.sh /media/free4tak/USB이름/bk_bot   # USB로 저장
~/k-bot/stock_bot/deploy/backup.sh ~/backups --with-logs           # 로그도 포함
```

- 백업 파일 하나에 `~/k-bot` 전체가 들어갑니다: 코드, `.env`, 토큰, state 파일, DB, OpenClaw, config.
  DB는 봇이 돌고 있어도 깨지지 않게 일관된 사본으로 뜹니다.
- 시스템 설정도 함께 담깁니다: crontab, systemd 유닛, pip/apt 목록, 공인 IP.
- **`.env`와 API 키가 들어 있으니 비밀번호처럼 다루세요.** 같은 디스크에만 두면 백업이 아니니, USB나 다른 PC에 복사해 두세요.

## 복구 (새 PC)

```bash
# 1) 백업 파일을 새 PC 홈으로 복사한 뒤 복구 스크립트만 꺼내서 실행
cd ~ && tar -xzf yeongam9_full_XXXX.tar.gz k-bot/stock_bot/deploy/restore.sh
bash k-bot/stock_bot/deploy/restore.sh ~/yeongam9_full_XXXX.tar.gz
```

`restore.sh`가 하는 일: apt 패키지 설치 → 백업 풀기 → venv와 pip 설치 → systemd 유닛 설치·자동시작·watchdog 등록 → crontab 설치 → 점검.

## 수동 확인 (스크립트가 못 하는 것)

1. **업비트 허용 IP**: 공인 IP가 바뀌면 cbot의 잔고 조회와 매도가 전부 실패합니다
   (`unregistered IP address`). 업비트 → Open API 관리 → 허용 IP에 새 IP를 등록하세요.
2. **키움 REST API 허용 IP**: 마찬가지로 새 IP를 등록해야 합니다. daybot이 키움으로 종목을 스크리닝합니다.
   (한투(KIS)는 IP 등록이 없습니다. IP를 등록해야 하는 곳은 업비트와 키움 두 곳뿐입니다.)
3. **시간대**: `timedatectl`이 `Asia/Seoul`이어야 합니다.
   다르면 `sudo timedatectl set-timezone Asia/Seoul`로 바꾸세요.
4. **놓친 cron**: 복구하는 동안 지나간 작업(예: 20:10 `collect_daily_data.py`)은 수동으로 한 번 실행하세요.
5. **git 설정**: 새 PC에는 `~/.gitconfig`(이름·메일)와 `~/.git-credentials`(push 토큰)가 없어서 커밋이나 push가 실패합니다.
   `git config --global user.name "탁원석"`, `git config --global user.email kortws@gmail.com`을 실행하고,
   push는 처음 할 때 토큰을 입력하세요(`credential.helper store`).
6. **Claude 원격 연결**: 부팅할 때 자동으로 켜려면 crontab에
   `@reboot sleep 30 && /usr/bin/tmux new -d -s claude "cd /home/free4tak/k-bot/stock_bot && /home/free4tak/.local/bin/claude remote-control"`
   한 줄을 넣으세요.
7. 시작: `./bot.sh start`. 상태 확인: `./bot.sh status`.
   youtube-live가 실패하면 `ffmpeg`가 설치됐는지 확인하세요.

## 2026-11-09 복구 때 실제로 터진 것

| 증상 | 원인 | 지금은 |
|---|---|---|
| 리나 무한 재시작 | venv에 yfinance 등 12개 누락 | requirements.txt를 pip freeze로 고정 |
| sbot/daybot `websocket-client 없음` | 위와 같음 | 위와 같음 |
| youtube-live 시작 실패 | ffmpeg(apt), yt-dlp 없음 | restore.sh가 apt로 설치, yt-dlp는 requirements에 포함 |
| cbot 매도 실패 | 업비트 허용 IP 미등록 | 수동 확인 1번 |
| 재부팅하면 봇이 안 켜짐 | 서비스가 전부 disabled | restore.sh가 enable과 watchdog 등록 |
| systemd 경고 `StartLimitIntervalSec` | `[Service]` 섹션에 있었음 | deploy/systemd 사본에서는 `[Unit]`으로 옮김 |

## 참고

- `deploy/systemd/`, `deploy/crontab.txt`는 2026-11-09 현재 PC의 설정입니다.
  유닛이나 cron을 바꾸면 이 사본도 같이 갱신하세요.
  (`backup.sh`는 백업할 때마다 그 시점의 실제 설정을 따로 저장합니다.)
- 패키지를 새로 설치했다면 `venv/bin/pip freeze > requirements.txt`로 목록을 갱신하세요.
- `install_systemd.sh`와 `yeongam9.target`은 예전 구성(nbot/ebot)이라 쓰지 않습니다.
