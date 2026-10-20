"""키키·리나·산야 메시지 모아보기 — 분류(시스템/종목/시황/기타), 봇 메시지만, 시간 역순 병합."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "interface"))
import discord_feed as F  # noqa: E402


class Classify(unittest.TestCase):
    def test_categories(self):
        cases = {
            "[CBOT] 🚨 WebSocket 연결이 20회 연속 실패 중이야": "system",
            "[SBOT] 🚀 [영암9 SWING1] 스윙봇 가동": "system",
            "✅ sector 재시작 완료!": "system",
            "[CBOT] 🚀 [영암9 COIN v3] ATR 추세추종 + 단일매수 💰 단일매수:1,000,000원": "system",
            "[CBOT] 🚨 당일 손실 한도 초과! — 매수 멈춤, 4시간 뒤 시장 점검": "system",
            "[CBOT] 💔 [매도] KRW-BTC | 손절(-1.49%)": "stock",
            "[CBOT] 🚀 [1차매수] KRW-WLD | 608,802원 시장:normal | 공포탐욕:55": "stock",
            "✅ 비나텍(126340) 등록 완료 [키움]": "stock",
            "🧪 [주도주검색식3] 📌 SK하이닉스(000660) -5.25%": "stock",
            "[CBOT] 📉 비트 24시간 -1.34% → 신규 매수 쉬기": "market",
            "[CBOT] 🟢 시장 점검: 순간 급락 후 안정으로 판단 — 매수 재개": "market",
            "🌙 봇 성적표 — 10-20(화) 국면 33%": "market",
            "🗺️ 분야별 강도 14:30 (관심그룹 29개)": "market",
            "🦊 키키: !r 에코프로 실행할까요? (네/아니오)": "etc",
        }
        for text, want in cases.items():
            self.assertEqual(F.classify(text), want, text)


class Merge(unittest.TestCase):
    def test_bot_only_sorted_kst_and_clean(self):
        bot = {"bot": True}
        per = {"kiki": [{"id": "1", "author": bot, "content": "**[CBOT]** 💔 [매도] KRW-BTC", "timestamp": "2026-10-20T01:09:34.000+00:00"}],
               "lina": [{"id": "2", "author": {"bot": False}, "content": "등록 삼성전자", "timestamp": "2026-10-20T02:00:00+00:00"},
                        {"id": "3", "author": bot, "content": "", "embeds": [{"title": "📰 아침 뉴스"}], "timestamp": "2026-10-19T22:00:00+00:00"}],
               "sanya": [{"id": "4", "author": bot, "content": "🗺️ 분야별 강도", "timestamp": "2026-10-20T05:30:00+00:00"}]}
        items = F.merge(per)
        self.assertEqual([x["id"] for x in items], ["4", "1", "3"])          # 대장 메시지(2) 빠짐, 최신 위
        self.assertEqual((items[1]["time"], items[1]["text"]), ("10:09", "[CBOT] 💔 [매도] KRW-BTC"))   # KST·굵게 제거
        self.assertEqual((items[2]["date"], items[2]["cat"], items[2]["bot_name"]), ("10-20", "market", "리나"))


if __name__ == "__main__":
    unittest.main()
