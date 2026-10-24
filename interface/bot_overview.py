"""
bot_overview.py — 우리 봇 기능 지도 + 변경 이력 (대시보드 /bots, 2026-10-22 대장 요청)
================================================================
[이 파일이 하는 일 — 비개발자용 설명]
대장: "봇들에 어떤 기능이 붙어 있는지 정리 — 그래야 활용도 하고 추가할 것도 안다. 이후 변동 이력도.
       기존 두 화면(/lina, /feed)과 왔다 갔다 할 수 있게."
→ 기능 지도(INVENTORY)는 이 파일에 직접 적음 — 기능이 바뀌면 같이 고침(★ 날짜가 최근 7일이면 🆕).
   변경 이력은 docs/CHANGELOG_KO.md(odt 변경이력과 같은 한글 문장)를 읽고, 개발 기록(git)은 접어서 같이 보여줌.
접속: http://100.81.218.118:5000/bots
"""
import os
import re
import datetime
import subprocess

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHANGELOG = os.path.join(_BASE, "docs", "CHANGELOG_KO.md")
NEW_DAYS = 7
# 대상 → (색, 아이콘)
TARGETS = {"sbot": ("#3b82f6", "🤖"), "daybot": ("#f59e0b", "⚡"), "cbot": ("#a855f7", "🪙"), "리나": ("#ec4899", "🌸"),
           "키키": ("#14b8a6", "🦊"), "대시보드": ("#64748b", "📱"), "서버": ("#475569", "🔐"), "분석": ("#22c55e", "🔬")}

# (텍스트, 들어간 날짜 또는 "") — 날짜가 최근 7일이면 🆕
INVENTORY = [
    {"id": "sbot", "name": "sbot — 스윙봇", "emoji": "🤖", "sub": "한투 · 시드 1,000만 · 6슬롯 × 165만", "groups": [
        ("🎯 종목 고르기", [
            ("후보 풀: 한투 관심그룹 NEW + SUB(순환 섹터 대표 고정석, 중복은 한 번)", "2026-10-21"),
            ("S7 슬롯: 대형주 8종목이 10일 고점 대비 -10%면 급락 매수", ""),
            ("AI 점수(하이쿠) + NEW 가산점 → 85점 이상만 매수 · 09:20부터", ""),
            ("정규장 덜 체결분은 저녁 보충 매수", ""),
        ]),
        ("🛡️ 매수 막는 장치 (매도·손절은 항상 돔)", [
            ("하락장 쉬기: 전날 국면(20일선 위 종목 비율) 35% 미만", "2026-10-12"),
            ("전날 투매일(리나 15:40 신저가 40%+) 다음 날 쉬기", ""),
            ("장중 하락 감지: 10:00~14:30 코스피200·코스닥150 시가 대비 -1.5%면 그날 쉬기", "2026-10-19"),
            ("약한 장(국면 50% 미만) 매수금 절반", ""),
            ("일 손실 -50만 · 세 봇 합산 -70만이면 전봇 중단(리나 감시)", ""),
            ("재매수 금지: 손실·본전으로 판 종목만(익절 종목은 허용)", ""),
            ("(꺼짐) 코스피 -2% 약세·-4.5% 중단 장 상태 판단 — .env로 다시 켤 수 있음", "2026-10-22"),
        ]),
        ("💰 매도", [
            ("손절 ATR×2(최대 -10%) · 물타기 안 함", ""),
            ("+8% 또는 목표1에 30% 익절 → 나머지 고점 -5% 트레일링", "2026-10-12"),
            ("!h 홀드 / !r 해제 · 대장이 직접 산 종목도 받아서 sbot 규칙으로", ""),
            ("탈출감시(3분, 알림만): 예전 보유 4종목 반등 +5%·꺾임 -4%·추가 하락", "2026-10-19"),
        ]),
    ]},
    {"id": "daybot", "name": "daybot — 단타봇", "emoji": "⚡", "sub": "한투 · 시드 310만 · 3슬롯(+여유 1) × 100만", "groups": [
        ("🎯 종목 고르기 (09:00~09:40 신규매수)", [
            ("주도주검색식3 · 단타000 (리나 파이썬판, 1~3분)", ""),
            ("090930타점 시가이탈 오전중저가이탈 (키움 2분 조회 · 파이썬판은 맞추는 중이라 소스에서 뺌)", "2026-10-22"),
            ("순위: 겹침 1순위 → 주도주 단독 → 섹터 → 단타000·090930 단독", ""),
            ("등락률 09:40 전 +3~8%(090930은 +1~8%) · 호가 매도잔량 3배 이상", "2026-10-22"),
        ]),
        ("🛡️ 매수 막는 장치", [
            ("하락장 쉬기(국면 35% 미만)", "2026-10-19"),
            ("약한 장 절반 · 일 손실 -15만 · 재매수 금지", ""),
        ]),
        ("💰 매도", [
            ("손절 -3.5% · +5%에 30% 익절 → 트레일링(고점 -1.5%, +4.5% 넘으면 -2%), 최소 +1%", "2026-10-12"),
            ("최대 3영업일 · 애프터장 매도(코드 06) · 대장 수동매도 감지·기록", ""),
        ]),
    ]},
    {"id": "cbot", "name": "cbot — 코인봇", "emoji": "🪙", "sub": "업비트 · 시드 300만 · 3코인 × 100만 + 눌림목 주머니", "groups": [
        ("🎯 종목 고르기", [
            ("필터 통과 전체 풀 + 고정 4개(BTC·ETH·XRP·SOL) · AI 점수 55+(최근 승률 따라 조정)", ""),
            ("위험 기준 금액: 손절 1번에 약 5만 · 최저 50만(마지막 빈 슬롯은 남은 돈 전부)", "2026-10-19"),
        ]),
        ("🛡️ 매수 막는 장치", [
            ("비트 24시간 -1% 미만이면 쉬기", "2026-10-16"),
            ("바이낸스 BTC 펀딩비 0.003%/8h 미만이면 쉬기", "2026-10-18"),
            ("일 손실 -15만 → 멈춤, 4시간마다 시장 점검(업비트 경보·원화마켓·뉴스·AI)", ""),
            ("손실·본전 매도 코인 5시간 재매수 금지", ""),
        ]),
        ("💰 매도", [
            ("손절 ATR×2 · 급락감지(10분 창 고점 대비 ATR×1, 야간 ×1.4)", ""),
            ("보호선: +2.5% 찍은 뒤 +0.3%로 내려오면 전량 매도(10-18 +3% → 10-24 +2.5%)", "2026-10-24"),
            ("+10% 조기 30% 익절 → 목표·트레일링", ""),
        ]),
        ("🪤 눌림목 주머니 (BTC·ETH·XRP·SOL 각 25만)", [
            ("7일 고가 -10% & 200일선 위면 매수 → +8% 익절 · 손절 없음 · 최대 20일", ""),
            ("매수선 3% 안에 들어온 슬롯만 cbot 현금에서 떼어둠", "2026-10-18"),
        ]),
    ]},
    {"id": "lina", "name": "리나 — 대장 수동매매 도우미", "emoji": "🌸", "sub": "키움 · 한투(데이봇 계좌) · 산야 채널", "groups": [
        ("📝 등록하고 자동 익절 (실제 매도)", [
            ("등록 종목 평단 N주 K|D → +3% 추적·고점 -4% 매도 / +5% 30% → 나머지 고점 -3%", ""),
            ("단타 종목 N주|N만 → 데이봇 시드로 매수(현재가+1호가) → +2% 30% → 트레일링(최소 +1%)", "2026-10-21"),
            ("국면 35% 넘으면 단타 종목 정리 알림(시드 반납)", "2026-10-21"),
            ("해제 종목 [매도가] K|D · 등록현황", ""),
        ]),
        ("🔔 장중 알림", [
            ("단타000(1분) · 주도주검색식3(3분) · 090930타점(1분, 키움과 맞출 때까지 기록만·알림 꺼짐)", "2026-10-22"),
            ("한투 관심그룹 섹터 감시 🚩(3분) · 09:35 시장 쏠림 브리핑", ""),
        ]),
        ("📊 장 마감 뒤", [
            ("15:40 투매 점검(신저가 40%+ → 반등 섹터 후보, 다음 날 08:50 아침 규칙)", ""),
            ("15:40 바닥 재시험일(투매 뒤 재시험 + 신저가 20% 미만 → 종가 매수 후보 5개)", "2026-10-21"),
            ("16:00 봇 성적표 · AI 모멘텀 체크인 · 18:30 지수·투자자·선물·금투협(반대매매) 수집", ""),
        ]),
        ("☀️ 아침", [
            ("07:00 뉴스 · 07:20 스윙 마스터 리포트 · 07:30 융합 브리핑 · 08:50 MBN 요약 · 08:55 AI 모멘텀", ""),
            ("명령: !국면 !투매 !쏠림 !섹터 !주도주 !단타 !추세 !성적표 !성과 !전체성과", ""),
        ]),
    ]},
    {"id": "kiki", "name": "키키 — 봇 관리", "emoji": "🦊", "sub": "키키 채널", "groups": [
        ("🛠️ 관리", [
            ("봇마다 !s / !daybot / !c + 상태·시작·정지·재시작·매도 · !전체상태", ""),
            ("!리스크중단/재개 · !점수기준 · !h/!r · !분석오늘 · !브리핑 · !git", ""),
            ("업비트 점검 공지 감시(사전·1시간 전·시작/종료)", "2026-10-19"),
        ]),
    ]},
    {"id": "infra", "name": "대시보드 · 데이터", "emoji": "📱", "sub": "Tailscale 전용", "groups": [
        ("📱 화면", [
            ("/ 봇 합산 손익·포지션·긴급 전봇 중단", ""),
            ("/lina 리나 등록종목 + 매도선 + 전량 매도·해제(PIN)", "2026-10-20"),
            ("/feed 키키·리나·산야 메시지 모아보기", "2026-10-20"),
            ("/bots 이 화면 — 기능 지도 + 변경 이력", "2026-10-22"),
        ]),
        ("🗄️ 쌓는 데이터", [
            ("일봉 20:10 · 투자자 20:30 · FDR 시세/지수 20:40 · 섹터 DB 16:30 · 쏠림 지수 30분", ""),
            ("09:15/30/45 주도 테마 기록(daybot 재검증용)", "2026-10-19"),
            ("코인: 업비트 봉 09:05 · 펀딩비·미결제·김프 09:10 · 30초 시세", "2026-10-18"),
            ("유튜브 06:00 · MBN 추천주 14:30 · 뉴스 07:50", ""),
        ]),
        ("💤 꺼둔 것", [
            ("09:19 시장 쏠림 안전장치(서비스 정지 방식)", "2026-10-20"),
            ("단타 리서치 스카우트", ""),
        ]),
    ]},
]


def _is_new(d: str, today: datetime.date) -> bool:
    try:
        return bool(d) and (today - datetime.date.fromisoformat(d)).days < NEW_DAYS
    except ValueError:
        return False


def inventory(today: datetime.date = None) -> list:
    today = today or datetime.date.today()
    out = []
    for b in INVENTORY:
        groups = [{"title": t, "items": [{"text": x, "since": d, "new": _is_new(d, today)} for x, d in items]}
                  for t, items in b["groups"]]
        out.append(dict(b, groups=groups, new=sum(i["new"] for g in groups for i in g["items"])))
    return out


_LINE = re.compile(r"^-\s*(\d{4}-\d{2}-\d{2})\s*\|\s*([^|]+?)\s*\|\s*(.+)$")


def changelog(path: str = CHANGELOG) -> list:
    """[{"date","target","text","color","icon"}] 최신 위."""
    out = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                m = _LINE.match(line.strip())
                if m:
                    color, icon = TARGETS.get(m.group(2), ("#64748b", "•"))
                    out.append({"date": m.group(1), "target": m.group(2), "text": m.group(3), "color": color, "icon": icon})
    except OSError:
        pass
    return sorted(out, key=lambda x: x["date"], reverse=True)


def git_log(days: int = 14, limit: int = 80) -> list:
    """개발 기록(git) [{"when","msg"}] — 실패하면 []."""
    try:
        r = subprocess.run(["git", "-C", _BASE, "log", f"--since={days}.days", f"-n{limit}", "--date=format:%m-%d %H:%M",
                            "--pretty=%ad\t%s"], capture_output=True, text=True, timeout=5)
        return [{"when": w, "msg": m} for w, m in (ln.split("\t", 1) for ln in r.stdout.splitlines() if "\t" in ln)]
    except Exception:
        return []


def build() -> dict:
    return {"bots": inventory(), "changes": changelog(), "git": git_log(), "targets": list(TARGETS)}


NAV = """<nav class="nav"><a href="lina" data-p="lina">📋 리나</a><a href="feed" data-p="feed">🗂️ 메시지</a><a href="bots" data-p="bots">🤖 봇 기능</a></nav>
<style>.nav{display:flex;gap:6px;margin:0 0 10px;position:sticky;top:0;background:var(--bg);padding:8px 0;z-index:5}
.nav a{flex:1;text-align:center;padding:8px 4px;border-radius:10px;border:1px solid var(--bd,#2a2d3a);color:var(--sub,#94a3b8);
text-decoration:none;font-size:13px;background:var(--card,#1a1d27)}.nav a.on{color:var(--tx,#e2e8f0);border-color:var(--tx,#e2e8f0);font-weight:700}</style>
<script>document.querySelectorAll('.nav a').forEach(a=>{if(location.pathname.endsWith('/'+a.dataset.p))a.classList.add('on')})</script>"""

HTML = """<!DOCTYPE html>
<html lang="ko"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="apple-mobile-web-app-capable" content="yes"><meta name="mobile-web-app-capable" content="yes">
<meta name="theme-color" content="#0f1117">
<title>봇 기능 지도</title>
<style>
 :root{--bg:#0f1117;--card:#1a1d27;--bd:#2a2d3a;--tx:#e2e8f0;--sub:#94a3b8}
 *{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--tx);font-family:-apple-system,'Noto Sans KR',sans-serif;padding:4px 16px 40px}
 h1{font-size:18px;margin:4px 0 2px}.sub{color:var(--sub);font-size:12px}
 .tabs{display:flex;gap:6px;margin:10px 0}.tabs button{flex:1;padding:8px;border-radius:10px;border:1px solid var(--bd);background:var(--card);color:var(--sub);font-size:14px}
 .tabs button.on{color:var(--tx);border-color:var(--tx);font-weight:700}
 .chips{display:flex;gap:6px;flex-wrap:wrap;margin:6px 0}.chip{padding:5px 10px;border-radius:14px;border:1px solid var(--bd);background:var(--card);color:var(--sub);font-size:12px}
 .chip.on{color:var(--tx);border-color:var(--tx)}
 .bot{background:var(--card);border:1px solid var(--bd);border-radius:12px;margin:8px 0;overflow:hidden}
 .bh{display:flex;justify-content:space-between;align-items:center;padding:12px;cursor:pointer}
 .bn{font-size:16px;font-weight:700}.bs{font-size:12px;color:var(--sub);margin-top:2px}
 .badge{font-size:11px;background:#7c3aed;color:#fff;border-radius:8px;padding:1px 6px;margin-left:6px}
 .bb{padding:0 12px 10px;display:none}.bot.open .bb{display:block}
 .gt{font-size:13px;color:var(--sub);margin:10px 0 4px;font-weight:700}
 .it{font-size:14px;line-height:1.45;padding:3px 0 3px 10px;border-left:2px solid var(--bd);margin:3px 0}
 .it.new{border-left-color:#7c3aed}.nw{font-size:11px;color:#c4b5fd;margin-left:4px}
 .day{color:var(--sub);font-size:12px;margin:14px 0 4px;border-bottom:1px solid var(--bd);padding-bottom:2px}
 .ch{background:var(--card);border:1px solid var(--bd);border-left:5px solid #64748b;border-radius:10px;padding:8px 10px;margin:6px 0;font-size:14px;line-height:1.45}
 .tg{font-size:11px;color:#fff;border-radius:8px;padding:0 6px;margin-right:6px}
 details{margin-top:14px}summary{color:var(--sub);font-size:13px;cursor:pointer}
 .git{font-size:12px;color:var(--sub);padding:3px 0;border-bottom:1px dashed var(--bd)}.git b{color:var(--tx);font-weight:400}
</style></head><body>
""" + NAV + """
<h1>🤖 봇 기능 지도</h1><div class="sub" id="at">불러오는 중…</div>
<div class="tabs"><button id="t1" class="on" onclick="tab(1)">🗺️ 기능</button><button id="t2" onclick="tab(2)">📜 변경 이력</button></div>
<div id="p1"></div>
<div id="p2" style="display:none"><div class="chips" id="cf"></div><div id="cl"></div>
 <details><summary>🛠️ 개발 기록(git, 최근 14일)</summary><div id="gl"></div></details></div>
<script>
let D=null,fc='전체',openSet=new Set();
try{openSet=new Set(JSON.parse(localStorage.getItem('bots_open')||'[]'));fc=localStorage.getItem('bots_fc')||'전체'}catch(e){}
const esc=s=>s.replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
function tab(n){[1,2].forEach(i=>{document.getElementById('p'+i).style.display=i==n?'':'none';document.getElementById('t'+i).classList.toggle('on',i==n)});try{localStorage.setItem('bots_tab',n)}catch(e){}}
function tog(id){openSet.has(id)?openSet.delete(id):openSet.add(id);try{localStorage.setItem('bots_open',JSON.stringify([...openSet]))}catch(e){};render()}
function setF(t){fc=t;try{localStorage.setItem('bots_fc',t)}catch(e){};render()}
function render(){
 document.getElementById('p1').innerHTML=D.bots.map(b=>`<div class="bot ${openSet.has(b.id)?'open':''}">
  <div class="bh" onclick="tog('${b.id}')"><div><div class="bn">${b.emoji} ${esc(b.name)}${b.new?`<span class="badge">🆕 ${b.new}</span>`:''}</div><div class="bs">${esc(b.sub)}</div></div><div>${openSet.has(b.id)?'▲':'▼'}</div></div>
  <div class="bb">${b.groups.map(g=>`<div class="gt">${esc(g.title)}</div>`+g.items.map(i=>`<div class="it ${i.new?'new':''}">${esc(i.text)}${i.new?`<span class="nw">🆕 ${i.since.slice(5)}</span>`:''}</div>`).join('')).join('')}</div></div>`).join('');
 document.getElementById('cf').innerHTML=['전체',...D.targets].map(t=>`<button class="chip ${fc==t?'on':''}" onclick="setF('${t}')">${t}</button>`).join('');
 let day='',h='';
 for(const c of D.changes){if(fc!='전체'&&c.target!=fc)continue;if(c.date!=day){day=c.date;h+=`<div class="day">${day}</div>`}
  h+=`<div class="ch" style="border-left-color:${c.color}"><span class="tg" style="background:${c.color}">${c.icon} ${esc(c.target)}</span>${esc(c.text)}</div>`}
 document.getElementById('cl').innerHTML=h||'<div class="ch">기록 없음</div>';
 document.getElementById('gl').innerHTML=D.git.map(g=>`<div class="git">${g.when} <b>${esc(g.msg)}</b></div>`).join('');
}
async function load(){try{D=await (await fetch('api/bots')).json();
 const n=D.bots.reduce((a,b)=>a+b.new,0);document.getElementById('at').textContent=`봇 ${D.bots.length}개 · 최근 7일 새 기능 ${n}개 · 변경 이력 ${D.changes.length}건`;render()}
 catch(e){document.getElementById('at').textContent='⚠️ 불러오기 실패'}}
try{tab(+localStorage.getItem('bots_tab')||1)}catch(e){}
load();
</script></body></html>"""
