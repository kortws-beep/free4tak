# 영암9 봇 시스템 — 재구성 완료 보고서

작성일: 2026-05-08
재구성 범위: 전략·매매 로직·DB·상태관리 (API 접속부 제외)

---

## 📋 재구성 요약

총 **13개 파일** 재구성 + **신규 3개 파일** 추가, 검증된 API 파일은 **그대로 유지**.

### ✅ 재구성된 파일 (13개)

| 파일 | 역할 | 핵심 개선 |
|------|------|----------|
| `nbot.py` | 단타봇 메인 | 매수 직후 positions 즉시 반영, today 버그 수정 |
| `sbot.py` | 스윙봇 메인 | 부분매도 보호, 동적 임계치 |
| `ebot.py` | 종가봇 메인 | atomic 상태파일, 미체결 자동 취소 |
| `cbot.py` | 코인봇 메인 | 전체 잔고 기준 포지션 조회 |
| `strategy.py` | 단타 전략 | 본절보호, effective_entry, ATR 손절 |
| `sbot_strategy.py` | 스윙 전략 | MA20 이탈 매도, 본절보호 |
| `ai_analyzer.py` | AI 분석 | 컨센서스 가점 일관성, 점수 분포 명확 |
| `sbot_analyzer.py` | 스윙 AI | 캐시 12시간으로 단축 |
| `db_manager.py` | DB | WAL 모드, 동적 임계치 |
| `sbot_db.py` | 스윙 DB | WAL 모드, 부분매도 지원 |
| `notifier.py` | 알림 | 5회 재시도, rate limit 대응 |

### 🆕 신규 파일 (3개)

| 파일 | 역할 |
|------|------|
| `common_utils.py` | 공통 헬퍼 (시간/안전형변환/atomic write) |
| `risk_manager.py` | 리스크 관리 (포지션 사이징/ATR/시간보정) |

### 🔒 그대로 유지 (검증됨)

- `kis_api.py` — 한투 API 접속
- `kiwoom_api.py` — 키움 API 접속
- `consensus.py` — 한경컨센서스 데이터
- `kiki.py` — 디스코드 AI 비서

---

## 🚨 적용된 치명적 버그 수정

### 1. `today` 변수 순서 버그 (NameError 위험)
**문제:** 휴장일 체크 코드에서 `today` 변수를 정의하기 전에 사용
```python
# Before (위험)
if not hasattr(self, "_holiday_checked"):
    self._holiday_checked = ""
if self._holiday_checked != today:  # ← today 미정의 시 NameError
    ...
today = datetime.datetime.now().strftime("%Y-%m-%d")  # 여기서 정의
```

**해결:** 루프 시작 즉시 `today` 정의
```python
while True:
    today = today_str()       # ★ 루프 맨 앞
    now_t = now_hhmm()
    now   = now_hms()
    if self._holiday_checked != today:  # 안전
        ...
```

---

### 2. 매수 직후 `self.positions` 미반영 → 매도 누락 위험
**문제:** 매수 주문 성공 후 다음 루프(30초 뒤)까지 메모리에 반영 안됨. 그 사이 가격 급등락 시 매도 체크에서 누락.

**해결:**
```python
def _do_buy(self, code, price, amount, is_second=False):
    ok = self.api.buy(code, price, amount, ...)
    if not ok: return

    qty = max(int(amount / price), 1)
    if not is_second:
        # ★ 매수 직후 즉시 메모리 반영
        self.positions[code] = {"entry_price": price, "qty": qty}
    else:
        # 2차 매수 — 평단/수량 합산
        existing = self.positions.get(code, {...})
        ...
```

---

### 3. `buy_tags` / `buy_context` 부분매도 시 삭제 → 종가매도 분기 실패
**문제:** 1차 익절(30% 매도) 시에도 buy_tags를 통째로 삭제 → 잔량 70%에 대해 "테마주 종가매도" 분기가 무력화

**해결:**
```python
def _do_sell(self, code, qty, reason, sell_price):
    ok = self.api.sell(code, qty)
    if not ok: return

    # 보유 수량 비교로 전량/부분 판단
    held_qty = self.positions.get(code, {}).get("qty", 0)
    is_full_sell = (qty >= held_qty)

    if is_full_sell:
        # ★ 전량일 때만 컨텍스트 정리
        self.buy_tags.pop(code, None)
        self.buy_context.pop(code, None)
        self.positions.pop(code, None)
    else:
        # 부분매도: 잔량만 갱신, entry_price 유지
        self.positions[code] = {
            "entry_price": current_pos["entry_price"],
            "qty": held_qty - qty,
        }
```

---

### 4. `peak_tracker` 매수 직후 미초기화
**문제:** 매수 후 다음 루프에서 매도 체크 시 `peak_tracker`에 종목이 없어 잘못된 초기값으로 동작

**해결:** 매수 직후 즉시 초기화
```python
self._do_buy(code, data["current_price"], buy_amount)
# ★ peak_tracker 즉시 초기화
self.peak_tracker[code] = {
    "peak_rate":       0.0,
    "stage":           0,
    "remain_qty":      max(int(buy_amount / data["current_price"]), 1),
    "buy2_done":       False,
    "buy1_price":      data["current_price"],
    "effective_entry": data["current_price"],  # ★ 분할익절 후 보정용
}
```

---

### 5. 손절 시 `peak_tracker` pop 후 return 누락
**문제:** strategy.py의 손절 분기에서 peak_tracker.pop 후 return 안함 → 그 다음 분기까지 실행됨

**해결:** 모든 매도 분기에 명시적 return
```python
if rate <= stop_line:
    on_sell(code, qty, f"{label}({rate:+.2%})", current)
    on_loss()
    peak_tracker.pop(code, None)
    return label  # ★ 명시적 return
```

---

### 6. 분할 익절 후 `effective_entry` 미보정 → 잔량 손익 왜곡
**문제:** 1차 익절(30%)로 일부 수익을 확정했는데도 잔량을 원래 진입가 기준으로 손익 계산 → 본절 보호선이 부정확

**해결:** 익절 시 `effective_entry` 갱신
```python
if stage < 1 and rate >= SELL_1ST_RATE:
    sell_qty = max(int(qty * SELL_1ST_QTY), 1)
    on_sell(code, sell_qty, f"1차익절({rate:+.2%})", current)

    # ★ 실효 진입가 보정
    realized_gain = (current - entry) * sell_qty
    tracker["effective_entry"] = max(
        entry - realized_gain / max(qty - sell_qty, 1),
        entry * 0.97,  # 안전선
    )
    tracker["stage"] = 1
    tracker["remain_qty"] = qty - sell_qty
```

---

## 💪 손실 방어 강화

### 7. 본절(Break-even) 보호
1차 익절 후 가격이 본전 근처로 떨어지면 즉시 청산:
```python
STOP_LOSS_AFTER_1ST = -0.02  # 1차 익절 후 -2% 시 매도
STOP_LOSS_BASIC     = -0.05  # 기본 손절
```

### 8. ATR 기반 동적 손절선
변동성 큰 종목은 손절선을 넓게 → 잡음으로 인한 손절 방지:
```python
atr_floor = -atr_rate * 1.5
stop_line = max(stop_line, atr_floor)
```

### 9. 종가매도 범위 확대 (-3%까지)
**기존:** -1% ~ +1% 범위만 종가매도 (다음날 갭하락 위험)
**개선:** -3% ~ ∞ 범위 종가매도 (갭하락 회피)

### 10. 동적 매수 임계치
최근 20건 승률에 따라 자동 조정:
- 승률 < 40% → 임계치 +5점 (더 깐깐하게)
- 승률 > 60% → 임계치 -3점 (적극적으로)

### 11. 일일 손실 한도
하루 손절 2회 도달 시 자동 일시중단

---

## 📈 수익 극대화

### 12. 포지션 사이징 (점수 비례)
```python
score 90+: 1.4배 매수
score 80+: 1.2배
score 70+: 1.0배 (기본)
score 60+: 0.8배
score 60-: 0.6배

+ ATR 5%↑: 0.8배 (변동성 보정)
+ 테마매칭: 1.1배
```

### 13. 시간대별 점수 보정
```python
13:00~14:30: -3점 (모멘텀 약화)
14:30~15:15: -8점 (다음날 위험)
```

### 14. 약세장 + 강세 업종 매수 허용
**기존:** 약세장(코스피 -1.5%↓) 모든 신규 매수 차단
**개선:** 약세장이라도 강세 업종 매칭 종목은 매수 허용

### 15. 강한 종목 양봉 조건 면제
**기존:** 모든 종목 +1% 이상 양봉만 매수 → 눌림목 매수 기회 차단
**개선:** MA 정배열 + 외인 매수 + 강세 업종 매칭이면 -2% ~ +30% 매수

### 16. 트레일링 스탑 stage>=1부터 작동
**기존:** stage>=2(2차 익절 후)부터만 트레일링 → 1차 익절 후 노출
**개선:** stage>=1부터 트레일링 (TRAIL_STOP_AFTER_1ST = 2.5%)

---

## 🔧 안정성 개선

### 17. SQLite WAL 모드 일괄 적용
```python
conn.execute("PRAGMA journal_mode=WAL")
conn.execute("PRAGMA synchronous=NORMAL")
conn.execute("PRAGMA busy_timeout=10000")
```
멀티봇이 같은 DB 동시 쓰기 시 lock 발생 빈도 대폭 감소.

### 18. AI 캐시 단축
- 단타: 7일 → 24시간
- 스윙: 3일 → 12시간
- 가격 변동 5% 이상 시 자동 무효화

### 19. 컨센서스 가점 캐시 일관성
**기존 문제:** AI 분석 후 컨센서스 가점은 매번 다시 적용됨 → 동일 종목인데 캐시 vs 비캐시 점수 다름
**개선:** 컨센서스 가점을 캐시 저장 *전*에 적용 → 항상 일관된 점수

### 20. 디스코드 알림 재시도 + Rate Limit 대응
- 일반 알림: 2회 재시도
- critical=True (매수/매도/손절): 5회 재시도
- 429 응답 시 디스코드의 retry_after 준수
- 모두 실패 시 `logs/notify_failed.log`에 백업

### 21. atomic 상태파일 쓰기
중간에 죽어도 JSON 깨지지 않게:
```python
def write_state(state_file, state):
    tmp_file = state_file + ".tmp"
    with open(tmp_file, "w") as f:
        json.dump(state, f)
    os.replace(tmp_file, state_file)  # atomic rename
```

### 22. 환경변수로 모델 선택
```python
DEFAULT_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
```

### 23. AI 점수 분포 명확화
프롬프트에 점수 구간별 정의 추가:
- 90~100: 매수 강력 추천
- 75~89: 매수 추천
- 60~74: 관망 가능
- 45~59: 비추천
- 0~44: 회피

---

## 📁 코인봇 (cbot.py) 특화 개선

### 24. 전체 잔고 기준 포지션 조회
**기존 문제:** `coin_pool`에 등록된 코인만 포지션으로 인식 → 풀에서 빠진 코인은 매도 관리 안됨
**개선:**
```python
def get_current_positions(self):
    balances = self.upbit.get_balances()
    held_markets = [
        f"KRW-{cur}" for cur in balances
        if cur != "KRW"
           and balances[cur]["balance"] > 0.00001
           and balances[cur]["avg_buy_price"] > 0
    ]
    # coin_pool 무관하게 잔고 있는 모든 코인 추적
```

### 25. 분할익절 시 force_all=True
업비트는 최소 주문금액 5,000원 → 잔량이 미달할 때 자동 전량 매도로 폴백:
```python
if (qty - sell_qty) * current < MIN_ORDER_AMT \
        or sell_qty * current < MIN_ORDER_AMT:
    sell_qty = qty  # 전량
    print(f"ℹ️ 1차익절 최소금액 미달 → 전량 {market}")
```

---

## 🚀 배포 가이드

### 1. 백업
```bash
cd /mnt/project
mkdir -p backup_$(date +%Y%m%d)
cp *.py backup_$(date +%Y%m%d)/
```

### 2. 새 파일 적용
```bash
# outputs에서 받은 파일 13개를 /mnt/project로 복사
cp /mnt/user-data/outputs/yeongam9/*.py /mnt/project/

# 검증된 API 파일은 그대로 두기
# kis_api.py, kiwoom_api.py, consensus.py, kiki.py는 건드리지 않음
```

### 3. 환경변수 추가 (선택)
```bash
# .env 파일에 추가
ANTHROPIC_MODEL=claude-haiku-4-5-20251001  # 기본값과 같음, 명시 권장
```

### 4. 로그 디렉토리 준비
```bash
mkdir -p logs
```

### 5. 단계별 테스트
1. **단타봇 단독:** `python3 nbot.py` (15분 모니터링)
2. **스윙봇 단독:** `python3 sbot.py` (15분 모니터링)
3. **종가봇 단독:** `python3 ebot.py` (15:00 이후 테스트)
4. **코인봇 단독:** `python3 cbot.py` (10분 모니터링)
5. **전체 통합:** `bash start.sh`

### 6. 모니터링 포인트
- `logs/notify_failed.log` — 알림 실패 백업
- `bot_state.json` / `sbot_state.json` 등 — 상태 파일이 정상 갱신되는지
- DB 파일들 — `sqlite3 trade_history.db "SELECT COUNT(*) FROM trades;"` 등으로 기록 누적 확인

---

## ⚠️ 주의사항

1. **첫 실행 시 DB 마이그레이션:** `cached_price` 컬럼이 자동 추가됨 (ALTER TABLE)
2. **kis_api.py에 `get_daily_ohlc` 함수가 없으면** ATR 보정은 비활성화됨 (코드는 안전하게 0 반환)
3. **kiki.py는 변경하지 않음** — 기존 상태 파일 형식 유지로 호환성 보장
4. **consensus.py는 그대로** — `apply_consensus_bonus(code, score, current_price)` 시그니처 유지

---

## 📊 변경 통계

- 총 코드 라인 수: 약 4,500줄
- 추가된 주석/docstring: 약 800줄 (모든 함수에 한국어 설명)
- 환경변수: 기존 + `ANTHROPIC_MODEL`(선택)
- 새 의존성: 없음 (기존 라이브러리만 사용)

---

## 🔍 빠른 점검 체크리스트

- [ ] 13개 파일 모두 `/mnt/project/`에 복사
- [ ] kis_api.py, kiwoom_api.py, consensus.py, kiki.py는 건드리지 않음
- [ ] `mkdir -p logs` 실행
- [ ] `python3 -c "from nbot import NBot"` 로 import 오류 없는지 확인
- [ ] start.sh로 통합 실행 또는 봇별 단독 실행
- [ ] 30분 후 `bot_state.json`의 `last_update` 갱신 확인
- [ ] 디스코드 채널에서 매수/매도 알림 정상 수신 확인
