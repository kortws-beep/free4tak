# 영암9 시스템 아키텍처 (2026-09-05 기준)

이 문서는 실제 systemd 서비스 목록과 코드 import 관계를 직접 확인해서 작성했습니다.
레포에는 이 외에도 실험/디버그/일회성 스크립트(`*_old.py`, `fix_*.py`, `debug_*.py`,
`verify_*.py`, `reset_*.py`, `sbot2.py`/`sbot2_strategy.py` 등 초기 실험 버전)가 다수
있지만, 아래는 **실제로 systemd로 상시 구동 중인 프로그램**과 그 의존 모듈만 다룹니다.

## 1. 사용 중인 프로그램 (systemd 서비스)

```
stock_bot/
├── bots/cbot.py                      코인(업비트) 자동매매봇 — ATR 추세추종 + AI스코어링
├── bots/sbot.py                      주식 스윙봇 — 자체 스크리닝(미너비니류)+키움 조건검색
├── lina_bot/sbo2.py                  주식 스윙봇(2호기) — swing_master S/A급 후보 + 등급별 슬롯
├── lina_bot/lina_bot.py              디스코드 브리핑봇 "리나" — 시황/뉴스/모멘텀 스케줄러
├── interface/kiki.py                 디스코드 제어봇 "키키" — 명령어로 3개 매매봇 조종
├── interface/dashboard.py            웹 대시보드(Flask, 5000포트) — 손익/포지션/리스크 통합뷰
├── intelligence/sector_monitor.py    섹터 순환매 데이터 수집(1분 주기)
└── intelligence/telegram_monitor.py  텔레그램 뉴스 수집(주식 채널 + coinnesskr 코인채널, 09-04 통합)

(보조, timer로 주기 실행) watchdog_cbot.sh / watchdog_sbo2.sh / watchdog_sbot.sh
    → 각 봇 heartbeat 파일 확인, 응답없으면 systemd 재시작
```

## 2. 공유모듈 (2개 이상 프로그램이 같은 파일을 import — 고치면 전부 영향)

```
core/
├── common_utils.py     날짜/시간 헬퍼, 상태파일 읽기/쓰기(락 보호), API헬스체크, 숫자포맷
│                        → sbot/sbo2/cbot/kiki/lina_bot 등 거의 전체가 사용
├── kis_api.py           한국투자증권 API 래퍼(잔고/시세/매수/매도/차트)
│                        → sbot, sbo2, lina_bot(kiwoom_pool_tracker) 공유
├── kiwoom_api.py        키움 조건검색 API 래퍼(웹소켓)
│                        → sbot, sbo2, lina_bot 공유
├── risk_manager.py      ATR 계산(+캐시), 포지션 사이징(켈리)
│                        → sbot, sbo2 공유 (09-04부터, 이전엔 sbo2가 자체구현)
├── master_db.py         통합 매매이력/포지션 DB(대시보드·교차보유방지용)
│                        → sbot, sbo2, cbot, dashboard 공유
├── account_sync.py      기동 시 실계좌↔DB 정합성 체크
│                        → sbot, cbot 공유 (sbo2는 미사용, 자체 로직으로 매 루프 동기화)
└── unified_risk.py      전봇 합산 손실한도/긴급중단 — dashboard 전용, 매매봇 자체 루프에선 미사용
                          (각 봇은 daily_loss_count 등 자체 손실카운터를 따로 둠)

interface/
├── notifier.py          디스코드 알림(재시도 강화) → sbot, cbot 공유 (sbo2는 자체 웹훅함수 사용)
└── kiki_data.py / kiki_cmd.py / kiki_briefing.py / kiki_monitor.py
                          → kiki.py 자신의 서브모듈(단일 프로그램 내부 분리, 다른 프로그램은 미사용)
```

## 3. 단독모듈 (해당 프로그램만 사용 — 종목선정/전략 등 의도적으로 다른 부분)

```
bots/sbot.py 전용
├── core/sbot_strategy.py    ATR 매도전략(목표1 배수 3.0x 등 sbot 고유 파라미터)
├── core/sbot_analyzer.py    AI 종목 분석
└── core/sbot_db.py          sbot 매매이력 DB

lina_bot/sbo2.py 전용
├── lina_bot/swing_master.py     S/A급 후보 추출(sbo2만의 종목소스)
├── lina_bot/swing_analyzer.py   ATR 손절/목표가(목표1 배수 2.0x 등 sbo2 고유 파라미터)
└── lina_bot/trend_analyzer.py   추세 판정

bots/cbot.py 전용
├── backtestc/strategy_coin.py   코인 AI 스코어링 전략
└── 업비트 API — 별도 파일 없이 cbot.py 안에 직접 구현(공유 안 함)

lina_bot/lina_bot.py 전용
├── lina_bot/kiwoom_pool_tracker.py   키움 조건검색 결과 풀 추적(체크인/승격 판정)
└── lina_bot/collect_daily_data.py 등 데이터 수집 스크립트 다수
```

**의도적으로 다른 핵심 로직(절대 통합하면 안 되는 것)**:
- 종목선정 소스: sbot=자체 스크리닝/키움조건검색, sbo2=swing_master S/A급+VCP/추세/촉매
- 포지션 사이징 공식: sbot=켈리기반, sbo2=등급별 고정비율
- 매수 슬리피지(extra_ticks), 시가총액 최소필터, ATR 목표배수: 각자 실전 경험으로 다르게 튜닝됨

## 4. 단독이지만 동기화된 모듈 (⚠️ 코드는 두 벌, 지금은 행동이 같음 — 향후 재분리 위험지점)

sbot과 sbo2가 **같은 문제를 각자 따로 구현**했다가 09-03~09-05 사이 서로 맞춰놓은 부분입니다.
코드 자체는 여전히 두 파일에 따로 있어서, **한쪽만 고치고 반대쪽을 놓치면 다시 어긋날 수 있습니다.**
이 넷 중 하나를 수정할 일이 생기면 반드시 반대쪽 파일도 같은 문제가 있는지 확인하세요.

| 기능 | sbot 위치 | sbo2 위치 |
|---|---|---|
| 잔고동기화 / 수동매도감지 | `bots/sbot.py` `run()` 내부 (인라인) | `lina_bot/sbo2.py` `_sync_real_positions()` |
| 미체결 주문 취소 | `bots/sbot.py` `run()` 내부 (인라인) | `lina_bot/sbo2.py` `_cancel_stale_orders()` |
| 매도전략(손절/트레일링/목표달성) | `core/sbot_strategy.py` `SwingStrategy.check_sell()` | `lina_bot/sbo2.py` `_check_sell()` (인라인) |
| 매수실행 + 교차보유방지 | `bots/sbot.py` `_do_buy`/`_execute_buys` | `lina_bot/sbo2.py` `_check_buy()` |

맞춰진 공통 동작: 매수직후 90초 동기화보호(BUY_SYNC_GUARD), 잔고 빈응답({}) 오판 방지,
정산지연 재입양 방지, 수동 일부매도 DB기록, 목표1 매도실패시 stage 유지, 홀드(!h/!r) 기능.

cbot도 09-05부터 BUY_SYNC_GUARD와 홀드(!h/!r) 기능을 이식받았지만, 나머지(잔고동기화 등)는
사용자가 cbot에 수동 개입할 일이 거의 없다는 판단하에 현재는 그대로 두었습니다.

---
*세부 발견/수정 이력은 각 커밋 메시지 참고. 이 문서는 구조 스냅샷이며 시점에 따라 달라질 수 있습니다.*
