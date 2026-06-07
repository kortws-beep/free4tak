import sys
import os

# 1. 시스템 경로 자동 탐색 설정 (sector_monitor.py와 동일한 방식)
_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in ["core", "intelligence", "interface", "bots", ""]:
    _p = os.path.join(_BASE, _d)
    if _p not in sys.path:
        sys.path.append(_p)

# 2. 이제 에러 없이 불러와집니다
from feature_builder import DataLoader

def test_scanner():
    # 데이터베이스 경로를 절대 경로로 깔끔하게 지정
    db_path = "/home/free4tak/k-bot/stock_bot/data/backtest_data.db"
    
    if not os.path.exists(db_path):
        print(f"❌ DB 파일을 찾을 수 없습니다: {db_path}")
        return

    try:
        loader = DataLoader(db_path)
        all_codes = loader.all_codes()
        print(f"✅ 성공! 총 {len(all_codes)}개의 종목 코드를 불러왔습니다.")
        
        # 첫 번째 종목 로드 테스트
        if all_codes:
            df = loader.load_ohlcv(all_codes[0])
            print(f"✅ 테스트 데이터: {all_codes[0]} — {len(df)}개 봉 로드 완료")
            
    except Exception as e:
        print(f"❌ 에러 발생: {e}")

if __name__ == "__main__":
    test_scanner()