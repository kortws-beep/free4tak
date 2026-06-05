# 영암9 cbot.py & kiki.py 재구성 (v2 추가본)

## 📌 이번 세션의 작업 — 운영본 기반 전면 재구성

이전에 만들어진 임시 cbot/kiki는 **폐기**하고, 운영 중인 실제 파일을
기반으로 **검증된 로직은 그대로 유지**하면서 다른 봇에서 적용한
개선사항을 동일하게 입혔습니다.

---

## 🪙 cbot.py v2.3 — 코인봇 (1,405줄)

### 베이스: 운영 v2.2 (1,174줄)
운영 코인봇의 **검증된 핵심 로직은 변경 없이** 유지:
- 30만원 소액 시드 설정 (1차 10만, 2차 5만)
- 종목 풀 자동 확장 (KRW 마켓 거래대금 상위 20개 + 고정 4개)
- 4시간봉 + 공포탐욕 + BTC 시장상태 + 야간 손절 + 알트 동시 1개 제한
- 1차 +5%/30% / 2차 +10%/40% / 트레일링 -5%
- 직전봉 -5% 급락 즉시 손절
- 업비트 JWT 인증 / 5분 캐시 / 알트 동시보유 제한

### ⚠️ 단 하나 정정한 운영 상수
`DAILY_LOSS_LIMIT = -60_000` → `-45_000` 으로 수정.

이유: 파일 docstring에는 "일일 손실 한도: -4.5만원"으로 명시되어
있었지만, 실제 코드 상수는 `-60_000`으로 어긋나 있었습니다.
docstring을 따른 수정이며, **만약 -60,000원이 의도적이었다면 수정 후
바꿔주세요** (`cbot.py` 130번째 줄):
```python
DAILY_LOSS_LIMIT = -45_000   # 또는 -60_000으로 환원
```

### ✨ 추가된 v2.3 개선 (다른 봇과 동일 적용)

| 개선 항목 | 설명 |
|---|---|
| **본절 보호** | 1차 익절 후 -2% 손절선 자동 적용 (이익 수확분 보호) |
| **effective_entry 보정** | 분할 익절 후 잔량의 평단가 보정 |
| **동적 AI 임계치** | 최근 승률 기반 매수기준 자동 조정 (40%↓ +5점 / 60%↑ -3점) |
| **AI 캐시 가격 무효화** | 가격 5% 변동 시 캐시 재계산 |
| **WAL 모드 SQLite** | 동시 접근 안정성 + busy_timeout |
| **DB 인덱스 추가** | `idx_cbot_market`, `idx_cbot_sell` |
| **부분 매도 DB** | `_save_sell_history(sold_qty=...)` 분할 행 추가 |
| **atomic 상태파일** | common_utils 사용 (JSON 깨짐 방지) |
| **알림 재시도** | critical=True면 5회 재시도 (rate limit 대응) |
| **today 변수 순서** | 루프 맨 앞에서 정의 (NameError 방지) |
| **loss_date 체크** | 일자 다를 때만 daily_pnl 초기화 |
| **peak_tracker 즉시 초기화** | 매수 직후 effective_entry 포함 즉시 등록 |

---

## 🤖 kiki.py v2 — AI 비서 (1,999줄)

### 베이스: 운영본 (1,668줄)
운영 키키의 **모든 명령어/검색/HTS 동기화/브리핑 로직 유지**:
- 키키 캐릭터 (꼬리 둘 여우정령) 페르소나 그대로
- Tavily → 네이버/구글 폴백 검색 체인 그대로
- 키움 WebSocket HTS 관심그룹 동기화 (업종_*/테마/new) 그대로
- 모닝(08:00) / 저녁(20:00) 자동 브리핑 그대로
- 평일 09:00 / 11:00 / 14:00 HTS 자동 동기화 그대로
- 자연어 → CMD 변환 + 도구 호출 루프 그대로

### ✨ 추가된 v2 개선

| 개선 항목 | 설명 |
|---|---|
| **종가봇(ebot) 연동** | `!e상태` / `!e성과` / `!e정지` / `!e시작` 신규 |
| **모든 봇 합산 성과** | `!성과` 명령에 단타+스윙+종가+코인 합계 |
| **저녁 브리핑 합산** | 4개 봇의 오늘 실현손익 모두 표시 |
| **atomic 상태파일** | common_utils 사용 (멀티봇 동시 쓰기 안전) |
| **WAL 호환 DB 읽기** | `_ro_connect()` — query_only 모드 |
| **!c매도 동적 코인** | FIXED_COINS 외 보유 코인도 매도 가능 (cbot status 동적 조회) |
| **on_ready 동적 봇 표시** | 실행 중인 봇만 환영 메시지에 표시 |
| **on_message 안정화** | 명령 실행 try/except로 봇 다운 방지 |
| **!시작 명령 강화** | `loss_date` 함께 갱신 → 카운터 초기화 안정 |
| **단타봇 손절카운터** | `!상태`에 당일 손절횟수 표시 |
| **상태 폴링 헬퍼** | `wait_cmd_result()` 별도 함수로 분리 |
| **모델 환경변수** | `ANTHROPIC_MODEL` 지원 |

### 검증된 부분 — 무손실 보존
- `LOCATIONS`, `PTY_CODE`, `SEARCH_SHORTCUTS` 사전 그대로
- `_get_weather_kma()` 기상청 API 호출 로직 그대로
- `_web_search_global()`, `_web_search_korea()` 그대로
- `interpret()` 시스템 프롬프트 + 도구 호출 흐름 그대로
- 키움 토큰/WebSocket 관심그룹 조회 그대로
- HTS 동기화 → 단타/스윙 봇 상태 갱신 로직 그대로

---

## 🚀 배포 가이드

### 1) 백업
```bash
cd /home/free4tak/k-bot/stock_bot
mkdir -p backup_20260508
cp *.py backup_20260508/
cp *.json backup_20260508/ 2>/dev/null
```

### 2) 모든 봇 정지
```bash
bash stop.sh
# 또는 개별 정지
pkill -f "python.*nbot.py"
pkill -f "python.*sbot.py"
pkill -f "python.*ebot.py"
pkill -f "python.*cbot.py"
pkill -f "python.*kiki.py"
```

### 3) 14개 파일 모두 교체
```bash
# /mnt/user-data/outputs/yeongam9/ 안의 모든 파일을 운영 폴더에 복사
cp /path/to/outputs/yeongam9/*.py /home/free4tak/k-bot/stock_bot/
```

**교체 대상 (14개):**
- `common_utils.py` (신규)
- `notifier.py` (신규)
- `risk_manager.py` (신규)
- `db_manager.py` (수정)
- `ai_analyzer.py` (수정)
- `strategy.py` (수정)
- `nbot.py` (수정)
- `sbot_strategy.py` (수정)
- `sbot_analyzer.py` (수정)
- `sbot_db.py` (수정)
- `sbot.py` (수정)
- `ebot.py` (수정)
- `cbot.py` (수정 ★)
- `kiki.py` (수정 ★)

**건드리지 않는 파일 (유지):**
- `kis_api.py` (KIS API 인증 — 검증된 운영본)
- `kiwoom_api.py` (키움 API 인증 — 검증된 운영본)
- `consensus.py` (네이버/다음 컨센서스 — 검증된 운영본)
- `*.db` 파일 (매매이력)
- `.env` 파일
- `start.sh` / `stop.sh`

### 4) 로그 폴더 생성
```bash
cd /home/free4tak/k-bot/stock_bot
mkdir -p logs
```
(notifier가 알림 실패 시 `logs/notify_failed.log`에 백업)

### 5) 환경변수 확인
```bash
# .env 파일에 필수 키 있는지 확인
grep -E "UPBIT_ACCESS_KEY|UPBIT_SECRET_KEY|ANTHROPIC_API_KEY|DISCORD_BOT_TOKEN|DISCORD_CHANNEL_ID" .env
grep -E "KIWOOM_APPKEY|KIWOOM_SECRETKEY|TAVILY_API_KEY|KMA_API_KEY" .env
```

### 6) 단독 테스트 권장 (각 30분)
```bash
# 단타봇 단독
python3 nbot.py
# Ctrl+C 종료 후 디스코드 알림 확인

# 스윙봇 단독
python3 sbot.py

# 종가봇 단독
python3 ebot.py

# 코인봇 단독 ★ DAILY_LOSS_LIMIT 동작 확인
python3 cbot.py

# 키키 단독 — !도움말, !c상태, !e상태 등 명령어 확인
python3 kiki.py
```

### 7) 통합 시작
```bash
bash start.sh
```

---

## 🔍 cbot v2.3에서 특히 확인해야 할 점

1. **DAILY_LOSS_LIMIT 값**
   docstring 따라 `-45_000`으로 변경했습니다. 의도한 값이 아니면
   `cbot.py` 130번째 줄에서 수정하세요.

2. **본절 보호 동작 확인**
   1차 익절(+5%) 후 -2% 도달 시 잔량 즉시 청산되는지 로그 확인.

3. **부분 매도 DB 행**
   기존: 같은 행 `qty` 줄임
   v2.3: **새 행으로 부분매도분 기록 + 잔량은 기존 행 갱신**

   `cbot_trade_history.db`의 trade_history 행이 늘어나도 정상.

---

## 🔍 kiki v2에서 특히 확인해야 할 점

1. **종가봇 명령 동작**
   `ebot_state.json`이 생성되어야 `!e상태` 정상 작동. ebot 미실행
   시에는 `cmd_all_status`/`!성과`에서 자연스럽게 빠짐.

2. **!c매도 동적 코인**
   기존: `KRW-BTC/ETH/XRP/SOL`만 가능
   v2: `cbot_state.json`의 `positions_detail` + `coin_pool`에서
   동적 조회 → 보유 중인 코인이면 자동 인식

3. **!성과 합산 표시**
   4개 봇 중 오늘 실현 매매가 있는 봇만 표시. 없으면 "오늘 실현 매매 없음".

---

## 📋 14개 모듈 한눈에 보기

| 파일 | 역할 | 줄수 | 변경유형 |
|---|---|---|---|
| `common_utils.py` | KST/safe_*/atomic write | 179 | 신규 |
| `notifier.py` | 디스코드 알림 + 재시도 + 백업 | 132 | 신규 |
| `risk_manager.py` | RiskManager 클래스 | 188 | 신규 |
| `db_manager.py` | 단타 DB + WAL | 397 | 수정 |
| `ai_analyzer.py` | AI 분석 + 컨센서스 | 197 | 수정 |
| `strategy.py` | 단타 전략 + 본절보호 | 415 | 수정 |
| `nbot.py` | 단타봇 메인 | 1,273 | 수정 |
| `sbot_strategy.py` | 스윙 전략 (MA20+본절) | 289 | 수정 |
| `sbot_analyzer.py` | 스윙 AI (12h 캐시) | 203 | 수정 |
| `sbot_db.py` | 스윙 DB | 154 | 수정 |
| `sbot.py` | 스윙봇 메인 | 894 | 수정 |
| `ebot.py` | 종가봇 메인 | 765 | 수정 |
| **`cbot.py`** | 코인봇 v2.3 ★ | 1,405 | **운영본 기반 재구성** |
| **`kiki.py`** | AI 비서 v2 ★ | 1,999 | **운영본 기반 재구성** |

**총 14개 파일 / 8,690줄** 모두 syntax 검증 완료 (`python3 -m ast.parse`).

---

## 💡 자주 묻는 질문

### Q. 운영 중 실수로 본절 보호가 너무 빨리 발동하면 어떻게 막나요?
A. `cbot.py` 라인 142
```python
STOP_LOSS_AFTER_1ST = -0.02   # -3% 등으로 완화 가능
```
또는 본절 보호 자체를 끄려면 stage 분기 무력화:
```python
def _get_stop_loss(self, stage: int = 0) -> float:
    # if stage >= 1:
    #     return STOP_LOSS_AFTER_1ST  # 주석 처리
    if self.market_status == "stop":  return STOP_LOSS_STOP
    ...
```

### Q. 동적 AI 임계치가 마음에 안 들면?
A. `cbot.py`의 `_get_dynamic_ai_threshold()` 함수에서 그냥
`return AI_SCORE_MIN_BASE` 한 줄로 고정시킬 수 있어요.

### Q. kiki에서 ebot 명령이 작동 안 해요
A. `ebot.py`가 한 번이라도 실행되어야 `ebot_state.json`이 생성됩니다.
ebot 실행 후 다시 시도하세요.

### Q. !성과에 단타봇만 나와요
A. 정상입니다. 오늘 실현 매매가 없는 봇은 표시 안 됨. 모두 평가 손익만
있고 매도 안 한 상태면 "오늘 실현 매매 없음"으로 표시됩니다.

---

## 🛠 롤백 방법

문제가 생기면 백업본으로 즉시 복구:
```bash
cd /home/free4tak/k-bot/stock_bot
bash stop.sh
cp backup_20260508/*.py .
bash start.sh
```

---

작업 완료 일자: **2026-05-08**
세션: cbot.py + kiki.py 운영본 베이스 전면 재구성
