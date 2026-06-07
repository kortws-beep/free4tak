import os
import requests
from dotenv import load_dotenv

# .env 파일에서 웹훅 주소 불러오기
load_dotenv()
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL") # .env에 적으신 변수명과 맞춰주세요!

def send_discord_alert(title, message, msg_type="info"):
    """
    디스코드로 예쁜 임베드 메시지를 전송합니다.
    msg_type: "buy"(초록색), "sell"(빨간색), "alert"(노란색/경고), "info"(파란색)
    """
    if not DISCORD_WEBHOOK_URL:
        print("⚠️ 디스코드 웹훅 URL이 설정되지 않아 알림을 보낼 수 없습니다.")
        return

    # 메시지 타입에 따른 색상 설정 (Hex Color)
    colors = {
        "buy": 0x00FF00,   # 초록색 (매수)
        "sell": 0xFF0000,  # 빨간색 (매도/손절)
        "alert": 0xFFA500, # 주황색 (폭락장/경고)
        "info": 0x3498DB   # 파란색 (일반 정보)
    }
    color = colors.get(msg_type, 0x3498DB)

    data = {
        "embeds": [
            {
                "title": title,
                "description": message,
                "color": color
            }
        ]
    }

    try:
        response = requests.post(DISCORD_WEBHOOK_URL, json=data)
        response.raise_for_status()
    except Exception as e:
        print(f"❌ 디스코드 전송 실패: {e}")

# ---- 테스트용 코드 (직접 실행해볼 때만 작동) ----
if __name__ == "__main__":
    print("디스코드 알림 테스트를 시작합니다...")
    send_discord_alert("🚀 K-Bot 시스템 가동", "디스코드 연동 테스트가 성공적으로 완료되었습니다!", "info")
    send_discord_alert("🟢 신규 매수 포착", "종목: 삼성전자\n사유: 반도체 주도 섹터 확인 및 VCP 돌파", "buy")
    send_discord_alert("🚨 시장 경고", "코스피 -3% 폭락! 신규 매수를 전면 차단합니다.", "alert")