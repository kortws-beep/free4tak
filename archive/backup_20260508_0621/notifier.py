"""
notifier.py — 디스코드 알림
"""
import os
import requests


class Notifier:

    def __init__(self):
        self.webhook   = os.getenv("DISCORD_WEBHOOK_URL")
        self.bot_token = os.getenv("DISCORD_BOT_TOKEN")
        self.channel   = os.getenv("DISCORD_CHANNEL_ID")

    def send(self, msg: str):
        print(msg)
        if self.webhook:
            try:
                requests.post(self.webhook, json={"content": msg}, timeout=3)
            except Exception as e:
                print(f"⚠️ 웹훅 전송 실패: {e}")
        if self.bot_token and self.channel:
            try:
                requests.post(
                    f"https://discord.com/api/v10/channels/{self.channel}/messages",
                    headers={"Authorization": f"Bot {self.bot_token}"},
                    json={"content": msg}, timeout=3
                )
            except Exception as e:
                print(f"⚠️ 봇 채널 전송 실패: {e}")
