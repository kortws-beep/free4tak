import os.path
import datetime
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# 구글 캘린더 접근 권한 수준 (읽기 및 쓰기 권한 모두 포함)
SCOPES = ['https://www.googleapis.com/auth/calendar']

def main():
    creds = None
    # 이전에 인증한 기록(token.json)이 있는지 확인합니다.
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)
        
    # 인증 기록이 없거나 만료되었다면 새로 인증을 진행합니다.
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            # 마스터가 넣어둔 credentials.json 파일을 읽어서 인증 창을 띄웁니다.
            flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
            creds = flow.run_local_server(port=0)
            
        # 다음 실행 때 또 로그인하지 않도록 토큰을 token.json 파일로 저장합니다.
        with open('token.json', 'w') as token:
            token.write(creds.to_json())

    try:
        # 구글 캘린더 서비스 객체 생성
        service = build('calendar', 'v3', credentials=creds)

        # 현재 시간 기준 앞으로 다가올 일정 5개 가져오기
        now = datetime.datetime.utcnow().isoformat() + 'Z'  # 'Z'는 UTC 시간을 뜻합니다.
        print('📅 구글 캘린더에서 다가오는 일정을 가져오는 중...')
        
        events_result = service.events().list(
            calendarId='primary', timeMin=now,
            maxResults=5, singleEvents=True,
            orderBy='startTime'
        ).execute()
        
        events = events_result.get('items', [])

        if not events:
            print('🔍 다가오는 일정이 없습니다!')
            return

        print("\n=== 🎉 가져오기 성공! 다가오는 일정 목록 ===")
        for event in events:
            start = event['start'].get('dateTime', event['start'].get('date'))
            print(f"📌 [{start}] {event['summary']}")

    except Exception as e:
        print(f'❌ 에러 발생: {e}')

if __name__ == '__main__':
    main()