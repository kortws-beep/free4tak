"""
notifier.py — 디스코드 알림 (재시도 보강판)
================================================================
[이 파일이 하는 일 — 비개발자용 설명]

봇이 매수/매도/오류를 알릴 때 디스코드 채널로 메시지를 보냅니다.
- 웹훅(Webhook): 가장 간단한 알림 방식
- 봇 토큰: 디스코드 봇 자체로 채널에 메시지 전송 (kiki.py가 사용)

[주요 개선 사항]
1. 재시도 로직 — 일시적 네트워크 오류로 알림이 사라지지 않도록 3회 재시도
2. 우선순위 알림 — 매수/매도/손절은 critical=True로 호출 시 5회 재시도
3. 실패한 메시지 로컬 파일에 백업 → 나중에 확인 가능
4. Rate Limit 대응 — 디스코드의 분당 30회 제한 회피
================================================================
"""
import os
import time
import json
import requests
import datetime
from typing import Optional


# 디스코드 메시지 길이 제한
DISCORD_MAX_LEN = 1900

# 실패한 메시지 백업 파일
FAILED_LOG = "logs/notify_failed.log"


class Notifier:
    """디스코드 알림 송신자."""

    def __init__(self, name: str = "bot"):
        self.name      = name  # 어느 봇이 보내는지 (디버깅용)
        self.webhook   = os.getenv("DISCORD_WEBHOOK_URL")
        self.bot_token = os.getenv("DISCORD_BOT_TOKEN")
        self.channel   = os.getenv("DISCORD_CHANNEL_ID")
        self._last_send = 0  # rate limit 회피용 타임스탬프

    def send(self, msg: str, critical: bool = False) -> bool:
        """
        메시지를 디스코드에 보냄.
        critical=True면 매수/매도/손절 등 중요 알림 → 5회 재시도.
        critical=False(기본)는 일반 알림 → 2회 재시도.

        반환: 성공 여부 (True/False)
        """
        # 항상 콘솔에는 찍어둠 (로그 파일에 남도록)
        print(msg)

        # 길이 자르기
        if len(msg) > DISCORD_MAX_LEN:
            msg = msg[:DISCORD_MAX_LEN - 20] + "\n... (잘림)"

        # rate limit 회피 (1초 이상 간격 보장)
        elapsed = time.time() - self._last_send
        if elapsed < 1.0:
            time.sleep(1.0 - elapsed)

        max_retries = 5 if critical else 2
        ok_webhook  = self._send_webhook(msg, max_retries) if self.webhook else True
        ok_bot      = self._send_bot(msg, max_retries) if (self.bot_token and self.channel) else True

        self._last_send = time.time()

        # 둘 다 실패하면 백업
        if not ok_webhook and not ok_bot:
            self._backup_failed(msg)
            return False
        return True

    def _send_webhook(self, msg: str, max_retries: int) -> bool:
        """웹훅으로 전송 (재시도 포함)"""
        for attempt in range(max_retries):
            try:
                res = requests.post(
                    self.webhook,
                    json={"content": msg},
                    timeout=5,
                )
                if res.status_code == 204 or res.status_code == 200:
                    return True
                if res.status_code == 429:
                    # Rate Limited — 디스코드가 알려준 만큼 대기
                    retry_after = res.json().get("retry_after", 1.0)
                    time.sleep(min(retry_after, 5))
                    continue
                # 다른 에러 — 짧게 대기 후 재시도
                time.sleep(0.5 * (attempt + 1))
            except Exception as e:
                if attempt == max_retries - 1:
                    print(f"⚠️ [{self.name}] 웹훅 최종 실패: {e}")
                else:
                    time.sleep(1.0 * (attempt + 1))
        return False

    def _send_bot(self, msg: str, max_retries: int) -> bool:
        """봇 토큰으로 채널에 전송 (재시도 포함)"""
        url     = f"https://discord.com/api/v10/channels/{self.channel}/messages"
        headers = {"Authorization": f"Bot {self.bot_token}"}
        for attempt in range(max_retries):
            try:
                res = requests.post(
                    url, headers=headers,
                    json={"content": msg}, timeout=5,
                )
                if res.status_code == 200:
                    return True
                if res.status_code == 429:
                    retry_after = res.json().get("retry_after", 1.0)
                    time.sleep(min(retry_after, 5))
                    continue
                time.sleep(0.5 * (attempt + 1))
            except Exception as e:
                if attempt == max_retries - 1:
                    print(f"⚠️ [{self.name}] 봇 채널 최종 실패: {e}")
                else:
                    time.sleep(1.0 * (attempt + 1))
        return False

    def _backup_failed(self, msg: str):
        """전송 실패한 메시지를 파일에 백업"""
        try:
            os.makedirs(os.path.dirname(FAILED_LOG), exist_ok=True)
            with open(FAILED_LOG, "a", encoding="utf-8") as f:
                f.write(f"[{datetime.datetime.now().isoformat()}] [{self.name}]\n")
                f.write(msg + "\n")
                f.write("-" * 50 + "\n")
        except Exception:
            pass
