import anthropic
import os
from dotenv import load_dotenv

load_dotenv()
client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

def ask_claude_strategy(stock_name, technical_summary, market_context):
    """
    Claude 3.5 Sonnet에게 모멘텀 트레이더의 시각으로 분석 요청
    """
    prompt = f"""
    당신은 '마크 미너비니'와 '윌리엄 오닐'의 전략을 완벽히 구사하는 수석 전략가입니다.
    현재 시장 상황과 {stock_name}의 데이터를 분석하여 최적의 판단을 내리세요.

    [시장 상황]
    {market_context}

    [종목 데이터 (기술적 지표)]
    {technical_summary}

    [판단 필수 원칙]
    1. VCP(변동성 수축) 패턴이 완성 단계인가?
    2. 200일 이평선 및 주요 지지선이 무너지지 않았는가?
    3. 시장(코스피/코스닥)의 급락세가 종목의 모멘텀을 압도하고 있지는 않은가?

    [보고 형식]
    - 판단: [적극매수/매수/관망/매도]
    - 확신도: 0~100%
    - 핵심 이유 (3줄 요약):
    - 리스크 요인:
    - 최종 한줄평:
    """

    try:
        message = client.messages.create(
            model="claude-3-5-sonnet-20240620",
            max_tokens=800,
            messages=[{"role": "user", "content": prompt}]
        )
        return message.content[0].text
    except Exception as e:
        return f"AI 분석 실패: {str(e)}"