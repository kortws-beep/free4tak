import sqlite3
import pandas as pd

class EodStrategy:
    def __init__(self, db_path="backteste/eod_backtest.db"):
        self.db_path = db_path

    def get_backtest_score(self, code):
        """
        해당 종목이 과거 종가매매 타점에서 어떠했는지 검증
        (현재는 데이터 수집 초기이므로 기본 점수 반환, 
        데이터가 쌓일수록 과거 승률에 따라 점수 부여)
        """
        try:
            conn = sqlite3.connect(self.db_path)
            # 해당 종목의 과거 종가 진입 이력 조회
            query = f"SELECT * FROM eod_targets WHERE code = '{code}'"
            df = pd.read_sql(query, conn)
            conn.close()
            
            if len(df) == 0:
                return {"total": 50, "msg": "신규 포착 종목"} # 데이터 없을 때 기본점수
                
            # 데이터가 쌓이면 여기서 실제 수익 확률 계산 로직 작동
            return {"total": 70, "msg": "과거 우수 타점"}
        except:
            return {"total": 0, "msg": "분석 오류"}