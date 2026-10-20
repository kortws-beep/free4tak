"""
discord_feed.py — 키키·리나·산야 메시지 한 화면 모아보기 (대시보드 /feed, 2026-10-20 대장 요청)
================================================================
[이 파일이 하는 일 — 비개발자용 설명]
대장: "키키·리나·산야 디스코드 메시지가 따로따로 올라오니, 한 대시보드에 시간순으로 모아서
       시스템/종목/시황/기타를 색과 아이콘으로 구분해 보여줘."
→ 디스코드 채널 기록을 봇 토큰으로 직접 읽음(봇들 코드는 안 건드림 — 누가 어떤 경로로 보냈든 다 잡힘).
   키키 = 키키 DM 채널(DISCORD_CHANNEL_ID), 리나 = 리나 보고 채널, 산야 = 리나 이름의 검색식·참고 채널.
   메시지마다 글자(키워드)로 분류:
     ⚙️ 시스템(검정) — 재시작·정지·오류·점검·연결 끊김/복구 …
     💹 종목(파랑)   — 매수·매도·손절·익절·등록·해제·탈출감시 …
     🌐 시황(초록)   — 코스피·수급·국면·비트 24시간·펀딩·브리핑·성적표 …
     💬 기타(회색)
   봇이 보낸 것만(대장이 친 명령은 뺌). 20초 캐시, 채널당 최근 100건.
접속: http://100.81.218.118:5000/feed
"""
import os
import re
import time
import datetime

import requests

API = "https://discord.com/api/v10"
LINA_CHANNEL = "1508487747508240525"   # lina_bot.REPORT_CHANNEL_ID
CACHE_SEC = 20
KST = datetime.timezone(datetime.timedelta(hours=9))
_cache = {"t": 0.0, "items": []}

BOTS = {"kiki": "키키", "lina": "리나", "sanya": "산야"}
CATS = {"system": ("⚙️", "시스템"), "stock": ("💹", "종목"), "market": ("🌐", "시황"), "etc": ("💬", "기타")}

# 위에서부터 먼저 걸리는 쪽으로 분류(시스템 → 시황 고유어 → 종목 → 시황 일반 → 기타)
_RULES = [
    ("system", r"\[영암9|한도 초과|WebSocket|웹소켓|watchdog|heartbeat|재시작|토큰|인증|오류|에러|크래시|안전장치"),
    # 장 판단 알림 — '점검'·'매수 재개/쉬기' 같은 말이 들어가도 시황
    ("market", r"시장 점검|투매 점검|비트 24시간|펀딩|국면|시황|반대매매|금투협|성적표|시장상태|쏠림|분야별 강도|"
               r"브리핑|뉴스"),
    ("system", r"가동|봇 시작|정지|중단|실패|예외|점검|끊|복구|서비스|세션|재연결|초기화|타임아웃|timeout"),
    ("stock", r"매수|매도|손절|익절|보호선|트레일링|등록|해제|탈출감시|급락감지|평단|체결|주문|종목|"
              r"KRW-[A-Z0-9]+|\(\d{6}\)"),
    ("market", r"수급|외국인|기관|프로그램|코스피|코스닥|나스닥|S&P|환율|미국장|선물|탐욕|공포|"
               r"섹터|테마|주도주|MBN|유튜브|모멘텀|검색식|리포트|전략|경제|금리|FOMC|CPI"),
]
_RULES = [(c, re.compile(p, re.I)) for c, p in _RULES]


def classify(text: str) -> str:
    for cat, rx in _RULES:
        if rx.search(text or ""):
            return cat
    return "etc"


def clean(text: str) -> str:
    """디스코드 꾸밈 글자(**굵게**, `코드`, <@멘션>) 걷어내기."""
    t = re.sub(r"```[a-z]*\n?|```", "", text or "")
    t = re.sub(r"\*\*|__|`|~~", "", t)
    t = re.sub(r"<[@#][!&]?\d+>", "", t)
    return t.strip()


def to_item(bot: str, m: dict):
    """디스코드 메시지 1건 → 화면 항목. 봇이 아닌(대장) 메시지·빈 메시지는 None."""
    if not (m.get("author") or {}).get("bot"):
        return None
    text = m.get("content") or ""
    for e in m.get("embeds") or []:
        text += "\n" + "\n".join(x for x in (e.get("title"), e.get("description")) if x)
    text = clean(text)
    if not text:
        return None
    ts = datetime.datetime.fromisoformat(m["timestamp"].replace("Z", "+00:00")).astimezone(KST)
    return {"id": m["id"], "bot": bot, "bot_name": BOTS[bot], "cat": classify(text), "text": text,
            "ts": ts.strftime("%Y-%m-%d %H:%M:%S"), "date": ts.strftime("%m-%d"), "time": ts.strftime("%H:%M")}


def merge(per_bot: dict) -> list:
    """{bot: [메시지,…]} → 시간 역순(최신 위) 목록."""
    items = [it for bot, ms in per_bot.items() for it in (to_item(bot, m) for m in ms) if it]
    return sorted(items, key=lambda x: (x["ts"], x["id"]), reverse=True)


def _channels() -> dict:
    kiki, lina = os.getenv("DISCORD_BOT_TOKEN", ""), os.getenv("DISCORD_BOT_TOKEN_N", "")
    return {"kiki": (os.getenv("DISCORD_CHANNEL_ID", ""), kiki),
            "lina": (LINA_CHANNEL, lina),
            "sanya": (os.getenv("LINA_SCAN_CHANNEL_ID", ""), lina or kiki)}


def _fetch(channel: str, token: str, limit: int = 100) -> list:
    if not (channel and token):
        return []
    try:
        r = requests.get(f"{API}/channels/{channel}/messages", params={"limit": limit},
                         headers={"Authorization": f"Bot {token}"}, timeout=10)
        return r.json() if r.ok else []
    except Exception as e:
        print(f"⚠️ [모아보기] {channel} 읽기 실패: {e}")
        return []


def build() -> dict:
    if time.time() - _cache["t"] >= CACHE_SEC:
        per = {bot: _fetch(ch, tok) for bot, (ch, tok) in _channels().items()}
        if any(per.values()):
            _cache.update(t=time.time(), items=merge(per))
    return {"items": _cache["items"], "at": datetime.datetime.now(KST).strftime("%H:%M:%S")}


HTML = """<!DOCTYPE html>
<html lang="ko"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="apple-mobile-web-app-capable" content="yes"><meta name="mobile-web-app-capable" content="yes">
<meta name="theme-color" content="#0f1117">
<title>봇 메시지 모아보기</title>
<style>
 :root{--bg:#0f1117;--card:#1a1d27;--bd:#2a2d3a;--tx:#e2e8f0;--sub:#94a3b8;
  --system:#0b0d12;--system-tx:#f8fafc;--stock:#3b82f6;--market:#22c55e;--etc:#6b7280}
 *{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--tx);font-family:-apple-system,'Noto Sans KR',sans-serif;padding:12px 16px 40px}
 h1{font-size:18px;margin:4px 0 2px}.sub{color:var(--sub);font-size:12px}a{color:var(--sub)}
 .bar{display:flex;gap:6px;flex-wrap:wrap;margin:10px 0 4px}
 .chip{padding:6px 10px;border-radius:16px;border:1px solid var(--bd);background:var(--card);color:var(--sub);font-size:13px}
 .chip.on{color:var(--tx);border-color:var(--tx)}
 .day{color:var(--sub);font-size:12px;margin:14px 0 4px;border-bottom:1px solid var(--bd);padding-bottom:2px}
 .msg{background:var(--card);border:1px solid var(--bd);border-left:5px solid var(--etc);border-radius:10px;padding:8px 10px;margin:6px 0}
 .msg.system{border-left-color:var(--system);box-shadow:inset 3px 0 0 #475569}.msg.stock{border-left-color:var(--stock)}
 .msg.market{border-left-color:var(--market)}.msg.new{animation:hl 3s ease-out}
 @keyframes hl{from{background:#2b3245}to{background:var(--card)}}
 .hd{display:flex;justify-content:space-between;font-size:12px;color:var(--sub);margin-bottom:3px}
 .who{font-weight:700}.who.kiki{color:#f472b6}.who.lina{color:#a78bfa}.who.sanya{color:#fbbf24}
 .tag{font-size:11px;padding:0 6px;border-radius:8px;margin-left:6px;color:#fff}
 .tag.system{background:var(--system);color:var(--system-tx);border:1px solid #475569}.tag.stock{background:var(--stock)}
 .tag.market{background:var(--market)}.tag.etc{background:var(--etc)}
 .tx{font-size:14px;white-space:pre-wrap;word-break:break-word;line-height:1.45}
 .tx.cut{max-height:4.4em;overflow:hidden}.more{color:var(--sub);font-size:12px;margin-top:2px}
</style></head><body>
<h1>🗂️ 봇 메시지 모아보기</h1><div class="sub"><span id="at">불러오는 중…</span> · <a href="lina">📋 리나 등록종목</a></div>
<div class="bar" id="bots"></div><div class="bar" id="cats"></div>
<div id="list"></div>
<script>
const BOTS={all:'전체',kiki:'🤖 키키',lina:'🌸 리나',sanya:'🔎 산야'};
const CATS={all:'전체',system:'⚙️ 시스템',stock:'💹 종목',market:'🌐 시황',etc:'💬 기타'};
let fb='all',fc='all',items=[],seen=null,open=new Set();
try{fb=localStorage.getItem('feed_bot')||'all';fc=localStorage.getItem('feed_cat')||'all'}catch(e){}
const esc=s=>s.replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
function chips(){
 document.getElementById('bots').innerHTML=Object.entries(BOTS).map(([k,v])=>`<button class="chip ${fb==k?'on':''}" onclick="setF('b','${k}')">${v}</button>`).join('');
 document.getElementById('cats').innerHTML=Object.entries(CATS).map(([k,v])=>`<button class="chip ${fc==k?'on':''}" onclick="setF('c','${k}')">${v}</button>`).join('');
}
function setF(t,k){if(t=='b')fb=k;else fc=k;try{localStorage.setItem('feed_bot',fb);localStorage.setItem('feed_cat',fc)}catch(e){}chips();render();}
function tog(id){open.has(id)?open.delete(id):open.add(id);render();}
function render(){
 let day='',h='';
 for(const x of items){
  if((fb!='all'&&x.bot!=fb)||(fc!='all'&&x.cat!=fc))continue;
  if(x.date!=day){day=x.date;h+=`<div class="day">${day}</div>`;}
  const long=x.text.split('\\n').length>3||x.text.length>180,cut=long&&!open.has(x.id);
  h+=`<div class="msg ${x.cat} ${seen&&!seen.has(x.id)?'new':''}" onclick="tog('${x.id}')">
   <div class="hd"><span><span class="who ${x.bot}">${x.bot_name}</span><span class="tag ${x.cat}">${CATS[x.cat]}</span></span><span>${x.time}</span></div>
   <div class="tx ${cut?'cut':''}">${esc(x.text)}</div>${cut?'<div class="more">▼ 눌러서 전체 보기</div>':''}</div>`;
 }
 document.getElementById('list').innerHTML=h||'<div class="msg">해당하는 메시지가 없어요</div>';
}
async function load(){
 try{
  const d=await (await fetch('api/feed')).json();
  items=d.items;render();seen=new Set(items.map(x=>x.id));   // 다음 갱신 때 새로 온 것만 잠깐 반짝
  document.getElementById('at').textContent=d.at+' 기준 · 20초마다 갱신 · '+items.length+'건';
 }catch(e){document.getElementById('at').textContent='⚠️ 불러오기 실패 — 다시 시도 중';}
}
chips();load();setInterval(load,20000);
</script></body></html>"""
