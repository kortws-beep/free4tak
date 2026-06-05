import time
from kis_api import KISApi
from notifier import DiscordNotifier
from backtesta.ai_analyst import ask_claude_strategy

# 설정
CONDITION_NAME = "마스터_모멘텀_전략" # 한투에 설정된 검색식 이름
INTERVAL = 20 * 60

def run_ai_staff():
    api = KISApi()
    notifier = DiscordNotifier()
    
    notifier.send("✅ **AI 참모 봇 기동** - 20분 간격 전략 분석을 시작합니다.")

    while True:
        try:
            # 1. 한투 검색식 종목 리스트 획득
            codes = api.get_condition_stocks(CONDITION_NAME)
            
            # 시장 상황 요약 (지수 등락 등)
            market_summary = api.get_market_summary() 

            if not codes:
                print(f"[{time.strftime('%H:%M')}] 포착된 종목 없음.")
            else:
                for code in codes[:3]: # 비용/효율상 상위 3개 집중 분석
                    name = api.get_stock_name(code)
                    # 현재가, RSI, 이평선 이격도 등 요약 데이터 생성
                    tech_data = api.prepare_ai_data(code) 
                    
                    # 2. Claude의 전략 보고서 획득
                    report = ask_claude_strategy(name, tech_data, market_summary)
                    
                    # 3. 디스코드 보고
                    notifier.send(f"📊 **[AI 참모 보고] {name}({code})**\n{report}")
            
            time.sleep(INTERVAL)
        except Exception as e:
            print(f"🚨 리포터 오류: {e}")
            time.sleep(60)

if __name__ == "__main__":
    run_ai_staff()