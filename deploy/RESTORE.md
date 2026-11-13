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

- **데이터 점검**: `venv/bin/python scripts/data_check.py` — 수집 표마다 마지막 기록 날짜(✅/⚠️/❌). 무엇이 어디서 쌓이는지는 `docs/DATA_CATALOG.md`.

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
7. **마와리 노드**: `restore.sh`가 `sudo deploy/mawari_up.sh`를 실행합니다. 따로 돌려도 됩니다.
   노드 신원은 docker 볼륨 `mawari-data`에 있고, 백업에는 `k-bot/mawari_data_backup`으로 들어갑니다.
   지갑 주소는 `.env`의 `MAWARI_DELEGATOR_ALLOWLIST`에 있습니다.
8. **원격접속(윈도우 → 이 PC)**: `sudo deploy/remote_access_setup.sh`를 실행하세요. 방화벽을 먼저 잠그고, Tailscale을 예전 IP 그대로 복원하고, SSH를 설치합니다.
   이어서 GNOME 원격 데스크톱을 켭니다(`grdctl rdp enable`, `disable-view-only`). 예전 인증서는 `~/.local/share/gnome-remote-desktop/certs`에 있습니다.
   ⚠️ 자동 로그인이면 열쇠고리가 잠겨 있어서 `grdctl rdp set-credentials`가 저장에 실패합니다.
   먼저 seahorse에서 "로그인" 열쇠고리 비번을 빈칸으로 바꾸세요. mstsc에서 사용자명은 `.\free4tak`입니다.
9. 시작: `./bot.sh start`. 상태 확인: `./bot.sh status`.
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

## 클라우드 이중 백업 (구글 드라이브, 암호화) — 2026-11-14

**왜 필요한가**: `/mnt/old` 백업은 같은 PC 안에 있는 디스크야. 화재·도난·전원사고·랜섬웨어가 나면 원본과 함께 사라져.
그래서 `backup.sh`가 끝날 때 `offsite_upload.sh`가 최신 백업을 **서버에서 암호화한 뒤** 구글 드라이브에 올리고, 클라우드에는 최근 4개만 남겨.
백업 안에 .env(키·계좌·토큰)가 들어 있으니 **암호화하지 않은 채 올리는 건 금지**야.

**처음 설정 (대장이 원격 화면 터미널에서 한 번만)**
1. rclone 설치: `sudo apt install -y rclone`
2. `rclone config`을 실행해서 구글 드라이브 원격을 만들어.
   - `n` → 이름 `gdrive` → 저장소 종류 `drive`
   - client_id와 client_secret은 빈칸으로 둬(엔터).
   - scope는 **`drive.file`**을 골라. rclone이 직접 만든 파일만 만질 수 있는 권한이라, 혹시 열쇠가 새어도 나머지 드라이브는 안전해.
   - 나머지는 기본값으로 두고, auto config는 `y`야. 브라우저가 뜨면 구글 계정으로 로그인하고 허용해.
3. 이어서 같은 `rclone config`에서 암호화 원격을 만들어.
   - `n` → 이름 **`gcrypt`** → 종류 `crypt` → remote에 `gdrive:yeongam9_crypt`
   - filename_encryption은 `standard`, directory_name_encryption은 `true`
   - password는 `y`를 고르고 **직접 정한 비밀번호**를 넣어.
   - password2(salt)도 `y`를 고르고 **두 번째 비밀번호**를 직접 정해.
4. ⚠️ **비밀번호 두 개를 종이에 적어서 서버와 떨어진 곳에 보관해.** 잃어버리면 구글도, 누리도 백업을 열 수 없어.
   이 비밀번호는 `~/.config/rclone/rclone.conf`에 들어 있는데, 이 파일은 백업에 포함되지 않아. 새 PC에서는 종이에 적어 둔 비밀번호로 위 2~3번을 다시 하면 돼.
5. 첫 업로드 시험: `deploy/offsite_upload.sh` → "✅ 클라우드 백업 완료 … 크기 일치"가 나오면 성공이야.
6. 구글 계정 **2단계 인증**을 반드시 켜 둬.

**복구할 때**
`rclone copy gcrypt:yeongam9/<파일명> ~/` → `sha256sum -c <파일명>.sha256`으로 확인 → 이 문서의 "복구" 순서대로 진행해.
목록 보기: `rclone lsf gcrypt:yeongam9`. 구글 웹 화면에서는 파일 이름까지 암호화돼서 알아볼 수 없는 게 정상이야.

## 참고

- `deploy/systemd/`, `deploy/crontab.txt`는 2026-11-09 현재 PC의 설정입니다.
  유닛이나 cron을 바꾸면 이 사본도 같이 갱신하세요.
  (`backup.sh`는 백업할 때마다 그 시점의 실제 설정을 따로 저장합니다.)
- 패키지를 새로 설치했다면 `venv/bin/pip freeze > requirements.txt`로 목록을 갱신하세요.
- `install_systemd.sh`와 `yeongam9.target`은 예전 구성(nbot/ebot)이라 쓰지 않습니다.
