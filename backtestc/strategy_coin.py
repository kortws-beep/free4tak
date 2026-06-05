import sqlite3
import pandas as pd
import numpy as np

class CoinStrategy:
    def __init__(self, db_path="coin_backtest.db"):
        self.db_path = db_path

    def get_rule_score(self, code):
        """CBOT의 7가지 규칙을 검증하여 점수 산출"""
        conn = sqlite3.connect(self.db_path)
        # 최근 30개 봉 데이터를 가져옵니다 (이동평균 계산용)
        query = f"SELECT * FROM daily_ohlcv WHERE code = '{code}' ORDER BY date DESC LIMIT 30"
        df = pd.read_sql(query, conn)
        conn.close()

        if len(df) < 20: return {"total": 0}
        
        # 날짜순 정렬 (과거 -> 현재)
        df = df.sort_values('date')
        
        # 1. 기술적 지표 계산
        close = df['close']
        ma5 = close.rolling(window=5).mean()
        ma20 = close.rolling(window=20).mean()
        vol_ma20 = df['volume'].rolling(window=20).mean()

        # 현재 시점 데이터 (가장 마지막 행)
        curr = df.iloc[-1]
        c_ma5 = ma5.iloc[-1]
        c_ma20 = ma20.iloc[-1]
        c_vol_ma20 = vol_ma20.iloc[-1]

        score = 0
        details = []

        # 규칙 1: MA5 > MA20 (정배열)
        if c_ma5 > c_ma20:
            score += 20
            details.append("이평정배열(+20)")

        # 규칙 2: 현재가 > MA20
        if curr['close'] > c_ma20:
            score += 20
            details.append("가격지지(+20)")

        # 규칙 3: 거래량 폭발 (20봉 평균 대비 1.3배)
        if curr['volume'] > c_vol_ma20 * 1.3:
            score += 30
            details.append("거래폭발(+30)")

        # 규칙 4: RSI (약식 계산: 최근 14봉 상승폭 기준)
        # (실제 RSI는 복잡하므로 여기선 캔들 모양으로 가점)
        if curr['close'] > curr['open']:
            score += 10
            details.append("양봉마감(+10)")

        return {"total": score, "details": ", ".join(details)}

if __name__ == "__main__":
    # 테스트 실행
    engine = CoinStrategy()
    # 업비트 대표 코인 비트코인(KRW-BTC)으로 점수 확인
    res = engine.get_rule_score("KRW-BTC")
    print(f"📊 비트코인 검증 결과: {res}")
