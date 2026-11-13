# 영암9 데이터 지도 (DATA_CATALOG)

> 2026-11-14 처음 작성(대장 요청: "생성되는 데이터 체크리스트 — 백테스터·서버 재설치·이어받는 사람용").
> **데이터를 새로 쌓거나 멈추면 이 문서와 `scripts/data_check.py`의 CATALOG를 같이 고칠 것.**
> 점검: `venv/bin/python scripts/data_check.py` → 표마다 마지막 기록 날짜 ✅/⚠️/❌.

---

## 0. 원칙 (대장 지정)

1. **들어오는 건 전부 저장한다.** 화면·디스코드로만 흘려보내지 않는다(11-14). 나중에 하락장·횡보장·상승장별 패턴을 보기 위해서다.
2. **출처는 줄마다 구분한다.** 어느 소스·기능·그룹에서 왔는지, 왜 수집했는지를 기록한다. 여러 소스를 한 목록으로 합치더라도 출처 칸은 지우지 않는다(11-14).
3. **같은 종목이 하루에 여러 번 뜨면 그날 최초 1번이 기준이다.** 백테스트는 종목·날짜별 첫 신호 시각과 가격으로 한다. 다음 날 다시 뜨면 그날의 최초 1번을 따로 센다.
4. **dbot 검증은 당일 승부 기준이고, 분봉으로 한다.** 일봉으로는 몇 시에 샀고 손절과 익절 중 무엇이 먼저 닿았는지 알 수 없다.
5. **매수 전날까지의 정보만 특성으로 쓴다.** 산 뒤의 정보가 섞이면 미래를 보고 고른 착시가 생긴다.
6. **테스트는 실제 DB에 쓰지 않는다.** 테스트 중 통합 거래 DB는 `YEONGAM9_MASTER_DB` 임시 경로를 쓴다(11-13 사고 뒤 정함).

**표에 쓰는 기호**

| 기호 | 뜻 |
|---|---|
| 🔒 | 다시 받을 수 없다(그 순간의 관찰 기록). 백업이 생명이다. |
| 🔁 | 나중에 외부에서 다시 받을 수 있다(일봉, 분봉은 약 1년치, 공시 등). |

---

## 1. 매매 기록 (봇이 쓰는 것)

| 파일 · 표 | 내용 | 쓰는 곳 | 출처 칸 | 보존 |
|---|---|---|---|---|
| `master_trades.db` · master_trades | 3봇 통합 매도 기록. 저녁 성적표·합산 손실 한도·대시보드가 이걸 본다. | 각 봇 매도 시 `core/master_db.py` | bot_type, buy_tag, is_partial(분할 매도 여부) | 🔒 |
| `master_trades.db` · master_positions / master_risk | 통합 보유 현황, 일일 손실 한도 상태 | 각 봇, `unified_risk` | bot_type | 🔒 |
| `sbot_trade_history.db` · trades | sbot 매매(5-18~) | `core/sbot_db.py` | buy_tag·grade(슬롯 출처), '수동' = 대장이 직접 산 걸 봇이 넘겨받은 것 | 🔒 |
| `sbot_trade_history.db` · candidate_log | **sbot 후보(11-14~)** | `sbot._run_analysis`·`_get_unified_candidates` | grade(출처 슬롯), mode = scored(풀 전체 점수) · buy(매수 판단) · observe(슬롯이 찼을 때 30분마다 관찰) | 🔒 |
| `daybot_trade_history.db` · trades / candidate_log | daybot 매매(9-30~), 후보(10-06~, 호가·시세 원본 포함) | `core/daybot_db.py` | buy_tag, source_tier, skip_reason | 🔒 |
| `cbot_trade_history.db` · trades / dip_trades | 코인 매매, 눌림목 주머니 | cbot | — | 🔒 |
| `lina_bot/three_month_leader_log.db` · manual_watch_log | 리나등록(대장 수동 종목) 익절·해제 이벤트 | lina_bot | event | 🔒 |
| 상태 파일 `*_state.json` | 봇의 현재 상태(보유·일시정지·명령 IPC) | 각 봇(`common_utils` 잠금) | — | 운영용, 기록 아님 |

---

## 2. 신호·관찰 (리나가 쌓는 것) — 대부분 🔒

모두 `lina_bot/three_month_leader_log.db` 안에 있다.

| 표 | 내용 | 주기 | 출처 칸 |
|---|---|---|---|
| leader_obs | 주도주검색식3 파이썬판. 통과와 탈락 모두, 탈락 사유(fails)까지 기록 | 장중 | path, passed, fails |
| danta_obs | 단타000 파이썬판 | 장중 | passed, fails |
| tml_obs | 3개월수급 당일주도주 | 09:00~12:00, 3분마다 | passed, others_ok |
| obs0909 | 090930 타점 | 09:00~09:40 | passed, fails |
| kiwoom_cond_log | 키움 조건검색 실제 결과 | 장중 | tag(검색식 이름) |
| sector_obs / sector_alerts | 섹터 순위(3분마다), 대장·2등 알림 | 장중 | grp, role |
| theme_open_obs / theme_pre_top | 본장 테마 순위, 프리장 테마 상위 종목 | 08:30~15:15 | rank |
| market_regime | 국면(20일선 위 종목 비율). 35% 미만이면 하락장 | 매일 | n(계산에 쓴 종목 수) |
| market_index / investor / program / futures / updown / raw | 지수·투자자별 수급·프로그램·선물·등락 종목 수 | 18:30 | idx, market |
| kofia_funds / kofia_credit | 예탁금·미수·반대매매·신용·대차 | 매일 | — |

**리나 보관함 `lina_bot/lina_archive.db` (11-14~)**

| 표 | 내용 | 출처 칸 |
|---|---|---|
| messages | 리나가 보낸 메시지 원문 전부: 브리핑, MBN 전략, 끼 알림, 검색식·섹터 알림, 명령 응답 | **source**(보낸 리나 기능 이름, 코드가 자동 기록), tag(첫 줄 [꼬리표]), channel |
| quotes_1m | 관심그룹 1분 시세(가격·등락률·고가·누적 거래대금·거래량). 장중 매분. | **grp**(한투 관심그룹 이름: isee5·끼·v알림), provider |

**신호 종목 분봉 `backtest/data/minute_cache.db` (11-14~)**

| 표 · 뷰 | 내용 |
|---|---|
| signal_targets | 리나 16:05 수집 대상 종목과 **"왜 받았나"**: 검색식 이름 또는 `daybot후보:<소스>`·`daybot매수:<소스>`, 신호 날짜·시각. 출처마다 그날 첫 시각을 남긴다. 신호 날 포함 3거래일 동안 따라간다. |
| signal_first_of_day(뷰) | 출처와 상관없이 종목·날짜별 **최초 신호** 시각과 그 출처. 원칙 3에 따른 백테스트 기준이다. |

---

## 3. 뉴스·방송·공시

| 파일 · 표 | 내용 | 쓰는 곳 / 언제 | 보존 |
|---|---|---|---|
| `intelligence/kind_disclosure.db` · disc / detail | 거래소 KIND 공시(수주·실적·자사주·증자), 자사주 상세 | 리나 30분마다(08:00~18:30) | 🔁(KIND) |
| `intelligence/feature_news.db` · news | [특징주] 뉴스(시각 포함) | 리나 30분마다 | 🔒(검색 노출이 바뀜) |
| `intelligence/hk_consensus.db` · reports | 한경 컨센서스 리포트(목표가·의견) | 리나 30분마다 | 🔁 |
| `intelligence/news_sentiment.db` + 루트 `news_YYYYMMDD.csv` | 키워드 뉴스·감성 분석 | cron 07:50 `news_crawler.py` | 🔒 |
| `intelligence/mtn_morning.db` + `logs/mtn_morning/*.txt` | MTN 아침 방송 받아쓰기(원문)와 요약, 이벤트 섹터 | cron 05:54 `mtn_morning_brief.py`(Whisper GPU) | 🔒 |
| `intelligence/youtube_picks.db` · youtube_picks / telegram_buzz + `logs/youtube_live/` | 유튜브 라이브 종목 추출과 받아쓰기 원문(60일 보관), 방송 텔레그램 코너 | `youtube-live` 서비스 | 🔒. 출처 칸은 channel, video_id |
| `intelligence/ai_momentum_picks.db` | AI 모멘텀 스캐너 픽(08:55·오후)과 7·14일 판정 | lina | 🔒 |
| `intelligence/expert_picks.db` | mbngold 전문가 픽 | cron 14:30 | 🔒 |
| `intelligence/macro_calendar.db` | FOMC·금통위 일정(48건) | 수동 | 🔁 |

---

## 4. 시세·장중 흐름

| 파일 · 표 | 내용 | 쓰는 곳 / 언제 | 보존 |
|---|---|---|---|
| `lina_bot/kr_theme_finance.db` · kr_stock_daily_data | 전 종목 일봉과 외인·기관 순매수. **키가 종목 이름**이다. | cron 20:10 `collect_daily_data.py`, 20:30 `backfill_investor.py` | 🔁 |
| `lina_bot/kr_theme_finance.db` · kr_stock_master / kr_theme_stocks | 종목 코드↔이름, 테마 구성 | 매주 월 07:50 `update_stock_master.py` | 🔁 |
| `backtest/data/backtest_data.db` | 백테스트 일봉 200종목(2024~), 시장 메타, **주도 섹터(AI 판정)** | cron 20:40 `fetch_history_fdr.py`, 16:30 `build_sector_db.py` | 일봉 🔁, 섹터 판정은 재생성 가능 |
| `backtest/data/feature_ohlcv.db` | 특성 분석용 일봉 캐시(네이버) | `buy_feature_analysis.py`가 필요할 때 받음 | 🔁 |
| `backtest/data/minute_cache.db` · bars | 한투 1분봉(가격·고가·저가). 신호 종목은 16:05 자동, 나머지는 백테스트하면서 받음 | `daybot_replay.MinuteStore` | 🔁 약 1년치만 받을 수 있음. 오래된 건 🔒 |
| `backtest/data/minute_vol.db` | 1분봉에 거래량 포함 | 백테스트(`feature_stock`·`disclosure_stock`) 실행 때 | 🔁 |
| `backtest/data/nxt_premarket.db` | NXT 프리장 시세 | `premarket_nxt_collect.py`(수동 실행, 10-29까지) | 🔒 |
| `intelligence/intraday_flow.db` | 장중 잠정 수급(라운드별), 거래원(외국계 창구) 스냅샷 | 리나 15:45, 거래원 09:30~15:50 | 🔒 |
| `intelligence/sector_monitor.db` | 키움 테마 흐름, 테마 안 종목 모멘텀(분 단위) | `yeongam9-sector` 서비스 | 🔒 |
| `intelligence/market_concentration.db` | 대형주 쏠림 스냅샷, 시장 요약 | cron 09~15시 30분마다 | 🔒 |

---

## 5. 코인

| 파일 | 내용 | 언제 | 보존 |
|---|---|---|---|
| `coin_price_ticks.db` | 업비트 시세 틱(8-18~). 급락 감지 백테스트용 | cbot 상시 | 🔒 |
| `backtestc/coin_backtest.db`, `coin_daily.db` | 코인 봉 | cron 09:05 `update_coin_data.py` | 🔁 |
| `backtestc/coin_global.db` | 펀딩비·미결제약정·롱숏·김프 | cron 09:10 `coin_global.py` | 펀딩비 🔁, 나머지 🔒 |
| `intelligence/coin_telegram_events.db` | 코인 텔레그램(중단됨) | — | ⏸️ |

---

## 6. 멈춘 자료와 정리 대상 (지우지 말고 참고만)

**⏸️ 의도적으로 멈춘 자료**

| 파일 | 마지막 기록 | 멈춘 이유 |
|---|---|---|
| `intelligence/telegram_events.db` | 10-03 | 텔레그램 계정 탈취 뒤 코드를 제거함 |
| `lina_bot/sbo2_trades.db` | 09-28 | sbot에 통합됨. 스윙 후보 검증에 계속 쓴다. |
| `lina_bot/kiwoom_pool_history.db` | 10-08 | 키움 풀 추적 종료 |
| `backtest/data/backtest_data.db` · daily_flow | 06-12 | 종목 수급은 kr_stock_daily_data가 대신함 |
| 루트 `sector_monitor.db` | 5월 | 옛 경로. 지금은 intelligence/ 아래 파일을 쓴다. |
| 루트 `backtest_data.db` | 5월 | 옛 경로. 지금은 backtest/data/ 아래 파일을 쓴다. |
| `trade_history.db` | 4~6월 | nbot 시절 기록 |

**빈 파일·옛 경로 찌꺼기**(내용 없음): `ai_cache.db`, `trades.db`, `finance.db`, `kr_theme_finance.db`(루트), `sbot2_trade_history.db`, `sbo2_trade_history.db`, `data/*.db`, `bots/sbot_trade_history.db`(6-17 테스트 4줄), `lina_bot/C:*telegram_events.db`(윈도우 시절 경로), `lina_bot/backtest/`, `lina_bot/telegram_events.db`, `intelligence/coin_price_ticks.db`. 언젠가 정리하되, 지우기 전에 대장 확인을 받는다.

---

## 7. 서버 재설치·이어받을 때

1. 복구는 `deploy/RESTORE.md` 순서대로 한다. 백업은 `deploy/backup.sh`로 매주 토 03:17에 `/mnt/old/home/free4tak/bk_bot`에 쌓인다. 모든 .db(봇이 돌고 있어도 안전한 스냅샷)와 `logs/` 하위 원문(txt)이 들어 있다. `*.log`는 `--with-logs`를 줄 때만 포함된다.
2. 복구한 뒤 `scripts/data_check.py`를 실행한다. ⚠️가 뜬 표는 그 표를 쓰는 cron(`deploy/crontab.txt`)이나 서비스가 살아 있는지 확인한다.
3. 🔒 자료는 다시 받을 수 없다. 백업 날짜와 복구 날짜 사이는 구멍으로 남는다. 11-09 서버 이전 때 수급 하루가 비었고, `market_flow.fill_investor_gaps`로 메웠다.
4. 비밀 정보(.env의 키·계좌·토큰)는 이 문서에 쓰지 않는다. 외부 AI 리뷰 묶음(`scripts/make_review_packs.py`)에도 들어가지 않는다.
