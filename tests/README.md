# tests — 실거래 봇 시나리오 테스트

```bash
python tests/run_all.py      # 저장소 루트에서. 전부 통과하면 "🎉 전부 통과"
pip install pyflakes          # (선택) 정의되지 않은 이름 검사까지 같이 돌림
```

- 실제 증권사/업비트/디스코드에 **접속하지 않는다**(가짜 API로 재현). 장중에 돌려도 안전.
- DB·상태파일은 임시 폴더에 만들고 지운다(운영 DB를 건드리지 않음).
- 커밋/배포 전에 한 번 돌리는 용도. 실패하면 그 테스트가 지키는 원칙이 깨진 것.

| 파일 | 지키는 것 |
|---|---|
| test_daybot_order_flow | 주문 접수≠체결: 미체결·부분체결 재감시, 매도거부 백오프, 수동 일부매도 |
| test_sbot_sell_and_db | 손절·트레일링 실패 시 상태 유지, 물타기 DB 합산, 애프터장 매도가 |
| test_cbot_positions | 수동매도 감지 키 정리, 전량매도 시 포지션 제거, 잔고실패 시 복구 |
| test_kiki_routing | 명령 권한, !분석오늘/!매도 라우팅, 텔레그램 재시작 차단 |
| test_master_risk | 전봇 긴급중단(오늘만 유효), daybot 손실 집계 |
| test_collect_daily_data | 실제 거래일 저장, 거래대금, 휴장일 가짜행 정리, backfill |
| test_three_month_leader | 3개월수급 후보선정(B·E)과 장중 조건(F·G·H·I·A) |

새 기능/버그수정을 할 때 그 시나리오를 여기에 하나씩 추가하면, 다음 수정이 그걸 깨뜨리는지 바로 알 수 있다.
