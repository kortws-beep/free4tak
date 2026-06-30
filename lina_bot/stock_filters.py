"""
stock_filters.py — ETF/우선주 등 비정규 종목 제외 공통 필터
─────────────────────────────────────────────────────────────
2026-06-30 신규 생성 배경:
swing_analyzer.py / trend_analyzer.py 각 2곳(텍스트용/dict용)씩 총 4곳에
ETF 키워드 목록이 거의 동일하게 중복 구현되어 있었음. "WON"(우리자산운용
ETF 브랜드)이 빠져 있어 "WON 미국우주항공방산" ETF가 일반 종목처럼 필터를
통과해 추세 탑픽으로 추천되고 실제 매수까지 시도되는 사고가 있었음.

한 곳에서 관리하도록 통합 — 새 ETF 브랜드가 나오면 여기 한 군데만
추가하면 모든 분석 엔진에 즉시 반영됨.

[국내 ETF 운용사 브랜드명] (2026-06 기준, 리브랜딩 이력 포함 — 옛 이름도
실제 거래소에 일부 잔존 가능성 있어 함께 포함)
  삼성자산운용     KODEX
  미래에셋자산운용  TIGER
  KB자산운용       RISE (구 KBSTAR)
  한화자산운용     PLUS (구 ARIRANG)
  한국투자신탁운용  ACE (구 KINDEX)
  NH아문디자산운용  HANARO
  신한자산운용     SOL (구 SMART)
  키움투자자산운용  KIWOOM (구 KOSEF, HEROES)
  하나자산운용     1Q (구 KTOP)
  우리자산운용     WON
  마이다스에셋     마이다스
  타임폴리오자산운용 TIMEFOLIO
  교보악사자산운용  KODEX 아님 — POWER
  유리자산운용     FOCUS
  메리츠자산운용   MASTER
  흥국자산운용     HK
  DB자산운용       UNICORN
  대신자산운용     마이다스 아님 — DAISHIN
"""

ETF_BRAND_KEYWORDS = [
    # 메이저 브랜드 (신규명)
    "KODEX", "TIGER", "RISE", "PLUS", "ACE", "HANARO", "SOL",
    "KIWOOM", "1Q", "WON",
    # 리브랜딩 이전 옛 이름 (거래소에 일부 잔존 가능성 대비)
    "KBSTAR", "ARIRANG", "KINDEX", "SMART", "KOSEF", "HEROES", "KTOP",
    # 중소형 운용사 브랜드
    "TREX", "마이다스", "TIMEFOLIO", "FOCUS", "MASTER", "UNICORN",
    "DAISHIN",
    # 상품 유형 키워드
    "인버스", "레버리지", "ETN", "ETF",
]


def is_etf_or_excluded(stock_name: str) -> bool:
    """
    종목명이 ETF/ETN/우선주 등 정규 분석 대상에서 제외해야 하는
    종목인지 판별. swing_analyzer/trend_analyzer 등에서 공통 사용.
    """
    if any(kw in stock_name for kw in ETF_BRAND_KEYWORDS):
        return True
    if stock_name.endswith("우") or stock_name.endswith("우B"):
        return True
    return False
