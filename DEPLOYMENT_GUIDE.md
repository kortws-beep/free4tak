# 🚀 영암9 운영 배포 가이드 (최종)

**작업일자:** 2026-05-08
**검증 완료:** 18개 모듈 import OK / 봇 클래스 4개 OK / ATR 활성화 / kiki+ebot 통합 OK

---

## ✅ 검증 완료 사항

```
✅ 18개 모듈 모두 import OK
✅ 4개 봇 클래스 정의 OK (NBot, SBot, EBot, CBot)
✅ ATR용 get_daily_ohlc 존재 OK
✅ calc_atr_rate 작동 OK
✅ common_utils 헬퍼 호환 OK
✅ kiki에 ebot 통합 OK (nbot/sbot/ebot/cbot 4종)
✅ consensus 시그니처 일치
✅ KisAPI 모든 메서드 호환 (★ get_daily_ohlc 추가)
✅ KiwoomAPI 100% 호환
✅ Strategy/SwingStrategy/RiskManager/AIAnalyzer/DBManager 100% 호환
```

---

## 📦 교체 대상 파일 (15개)

운영 폴더 `/home/free4tak/k-bot/stock_bot/`에 복사:

### 신규 파일 (3개)
- `common_utils.py` — atomic 상태파일 / KST 시간
- `notifier.py` — 디스코드 알림 + 재시도
- `risk_manager.py` — RiskManager 클래스
- `sbot_db.py` — 스윙봇 전용 DB (sbot.py 분리에 필요)

### 수정된 파일 (11개)
- `db_manager.py` — WAL 모드 + 동적 임계치
- `ai_analyzer.py` — 컨센서스 가점 통합
- `strategy.py` — 단타 전략 + 본절 보호
- `nbot.py` — 단타봇 (모든 개선 적용)
- `sbot_strategy.py` — 스윙 전략
- `sbot_analyzer.py` — 스윙 AI (12h 캐시)
- `sbot.py` — 스윙봇
- `ebot.py` — 종가봇
- `cbot.py` — 코인봇 v2.3 ★
- `kiki.py` — AI 비서 v2 ★
- `kis_api.py` — ★ get_daily_ohlc 메서드 추가됨

### 운영 폴더에 그대로 두는 파일
- `kiwoom_api.py` (변경 없음)
- `consensus.py` (변경 없음)
- `sports_crawler.py` (변경 없음)
- `start.sh`, `stop.sh`, `status.sh`
- `*.db`, `.env`, `*.json`

### 삭제 가능한 파일
- `patch_cbot.py` — 사용 끝 (cbot.py에 패치 통합됨)

---

## 🔑 환경변수 체크 (.env)

운영 직전 반드시 확인:

### 필수
```bash
KIS_APPKEY=...           # 한국투자증권
KIS_SECRET=...
KIS_CANO=...
KIS_ACNT_PRDT_CD=...
KIS_HTS_ID=...           # 한투 관심그룹용

UPBIT_ACCESS_KEY=...     # 업비트
UPBIT_SECRET_KEY=...

ANTHROPIC_API_KEY=...    # Claude AI

DISCORD_BOT_TOKEN=...    # 디스코드
DISCORD_CHANNEL_ID=...
```

### 선택 (kiki 비서 풀 기능용)
```bash
KIWOOM_APPKEY=...        # HTS 관심그룹 동기화
KIWOOM_SECRETKEY=...

KMA_API_KEY=...          # 기상청 날씨
TAVILY_API_KEY=...       # 검색 (Tavily)
NAVER_CLIENT_ID=...      # 네이버 검색 폴백
NAVER_CLIENT_SECRET=...

ANTHROPIC_MODEL=claude-haiku-4-5-20251001  # 모델 변경 가능 (선택)
```

확인 명령:
```bash
cd /home/free4tak/k-bot/stock_bot
grep -E "KIS_APPKEY|UPBIT_ACCESS|ANTHROPIC_API|DISCORD_BOT" .env
```

---

## 🚀 배포 절차 (10분 소요)

### 1️⃣ 백업 (필수!)
```bash
cd /home/free4tak/k-bot/stock_bot
mkdir -p backup_20260508
cp *.py backup_20260508/
cp *.json backup_20260508/ 2>/dev/null
echo "✅ 백업 완료: $(ls backup_20260508 | wc -l)개 파일"
```

### 2️⃣ 모든 봇 정지
```bash
bash stop.sh

# 확실히 정지됐는지 확인
ps aux | grep -E "python.*(nbot|sbot|ebot|cbot|kiki)" | grep -v grep
# (출력 없어야 정지됨)
```

### 3️⃣ 로그 폴더 생성
```bash
mkdir -p logs
```

### 4️⃣ 15개 파일 복사
```bash
# /mnt/user-data/outputs/yeongam9/ 안의 파일들을 운영 폴더에 복사
# (REFACTOR_NOTES*.md 와 DEPLOYMENT_GUIDE.md는 제외하고 .py 파일만)

cd /home/free4tak/k-bot/stock_bot
cp /path/to/outputs/yeongam9/*.py .

# 복사 확인
ls -la *.py | wc -l
# (총 19개 정도 — 작업본 15개 + kiwoom_api/consensus/sports_crawler/patch_cbot)
```

### 5️⃣ 임포트 검증 (실제 실행 전 필수)
```bash
cd /home/free4tak/k-bot/stock_bot
python3 -c "
import sys; sys.path.insert(0, '.')
from nbot import NBot
from sbot import SBot
from ebot import EBot
from cbot import CBot
from kiki import bot
from kis_api import KisAPI
assert hasattr(KisAPI, 'get_daily_ohlc'), 'kis_api.py 업데이트 안 됨!'
print('✅ 모든 모듈 임포트 성공')
print('✅ get_daily_ohlc 존재 확인')
"
```

### 6️⃣ 단독 실행 테스트 (시간 여유 시)
```bash
# 코인봇 단독 (주식장 아니어도 24시간 가능)
python3 cbot.py
# 1~2분 후 Ctrl+C로 정지, 디스코드 알림 확인
```

### 7️⃣ 통합 시작
```bash
bash start.sh
```

### 8️⃣ 디스코드에서 동작 확인
```
!도움말        — 명령어 목록 (종가봇 섹션 포함되어야)
!전체상태      — 4개 봇 모두 표시
!c상태         — 코인봇 v2.3 표시
```

---

## ⚠️ 트러블슈팅

### 문제: `ModuleNotFoundError: common_utils`
→ `common_utils.py` 복사 누락. 다시 복사.

### 문제: `AttributeError: 'KisAPI' object has no attribute 'get_daily_ohlc'`
→ `kis_api.py` 업데이트 안 됨. **이번에 수정한 파일** 다시 복사.
   ATR 기능만 미작동이고 봇은 안 죽음. (hasattr 가드)

### 문제: `cbot이 즉시 일시중단됨`
→ `cbot_state.json`의 `paused: true`가 남아 있음.
```bash
python3 -c "
import json
with open('cbot_state.json') as f: s=json.load(f)
s['paused']=False; s['daily_loss']=0
with open('cbot_state.json','w') as f: json.dump(s,f,ensure_ascii=False,indent=2)
print('✅ cbot 재개 상태로 변경')
"
```

### 문제: kiki에서 `!c매도 BTC` 응답 없음
→ cbot.py가 실행 중이어야 함. `ps aux | grep cbot.py`로 확인.

### 문제: 매수가 안 됨
→ 로그에서 다음 확인:
- "🌅 9시 동시호가 단계 — 매수 미허용" → 정상 (9:00~9:01)
- "AI점수 부족" → 동적 임계치 작동 중 (정상)
- "포지션 FULL" → 보유 종목 가득 참
- "당일 손절 N회" → 손절 한도 초과
- "장 휴장" → 휴일

---

## 🎯 운영 첫날 모니터링 포인트

### 09:00 ~ 09:10 (오픈 1분 후 시작)
- [ ] 단타봇 첫 매수 알림 디스코드에 도착하는지
- [ ] AI 점수가 화면에 출력되는지
- [ ] 컨센서스 가점이 작동하는지 ("📋 컨센서스" 로그)

### 09:30 ~ 10:00
- [ ] 매도 체크가 5분 간격으로 작동하는지
- [ ] 본절 보호: 1차 익절 후 -2%에서 잘 청산되는지
- [ ] ATR 기반 손절선이 종목별로 다른지 (`logs/`에서 확인)

### 13:00 ~ 14:00
- [ ] 13:00 종가봇 자동 시작
- [ ] 14:00 HTS 관심그룹 자동 동기화

### 15:20 ~ 15:30
- [ ] 종가봇 일괄 매도 (15:25)
- [ ] 단타봇 장마감 매도 (15:18~)

### 20:00
- [ ] 저녁 브리핑 (4개 봇 합산 손익 표시)

---

## 📊 cbot v2.3에서 특히 주시할 것

### DAILY_LOSS_LIMIT = -45,000원
docstring 따라 -60,000 → -45,000으로 변경됨.
**의도와 다르면 즉시 변경:**
```python
# cbot.py 라인 130
DAILY_LOSS_LIMIT = -45_000   # 또는 -60_000
```

### 본절 보호 (-2%)
1차 익절 후 -2%에서 청산. 너무 빨리 발동하면 라인 142에서 -3%로 완화.

### 동적 AI 임계치
첫날은 데이터 부족 → 기본 55점 사용.
거래 10건 이상 누적 후 동적 조정 시작.

---

## 🔄 롤백 (문제 발생 시)

```bash
cd /home/free4tak/k-bot/stock_bot
bash stop.sh
cp backup_20260508/*.py .
cp backup_20260508/*.json . 2>/dev/null
bash start.sh
```

---

## 📞 빠른 연락 명령

문제 발생 즉시 디스코드에서:
```
!전체상태       — 모든 봇 현황 한눈에
!성과          — 오늘 모든 봇 합산 실현손익
!정지          — 단타봇 일시중단
!s정지         — 스윙봇 일시중단
!e정지         — 종가봇 일시중단
!c정지         — 코인봇 일시중단
```

비상시 매도:
```
!매도 005930    — 단타봇 즉시 매도 (코드)
!매도 삼성전자   — 종목명으로도 매도 가능
!c매도 BTC      — 코인봇 즉시 매도
```

---

## ✨ 이 버전의 핵심 개선 (한눈에)

### 손실 방어 강화
1. **본절 보호** (단타/스윙/코인) — 1차 익절 후 -2% 자동 청산
2. **effective_entry 보정** — 분할 익절 후 잔량 평단가 정확
3. **ATR 동적 손절** — 종목별 변동성 따라 손절선 조정
4. **동적 AI 임계치** — 최근 승률 따라 매수 점수 자동 조정

### 정확성 강화
5. **AI 캐시 가격 변동 감지** — 5%↑ 변동 시 캐시 무효
6. **컨센서스 가점** — 한경 리포트 통합
7. **WAL 모드 SQLite** — 동시 접근 안정

### 안정성 강화
8. **atomic 상태파일** — JSON 깨짐 방지
9. **알림 5회 재시도** — Discord rate limit 대응
10. **notify_failed.log 백업** — 알림 실패 시 로그
11. **today 변수 순서** — NameError 방지
12. **peak_tracker 즉시 초기화** — 매수 직후 매도 누락 방지

### 통합 비서
13. **kiki에 ebot 연동** — `!e상태` `!e성과` `!e정지` `!e시작`
14. **!성과 4봇 합산** — 단타+스윙+종가+코인 통합
15. **!c매도 동적 코인** — 보유 모든 코인 매도 가능

---

**🎉 이상 검증 완료. 안전 운영 가능합니다.**

**Good luck with the live operation! 🚀**
