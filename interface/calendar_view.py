"""
calendar_view.py — 대시보드 '📅 달력' 탭: 1년치 이벤트 + 미리 사기 창 (2026-11-28, 대장)
================================================================
대장: "대시보드에 달력 — 1년치 이벤트, 해당일 이벤트 표시. 키움에서 '오늘 살 게 진짜 없네' 할 때 다가오는 이벤트 보고
미리 선점." 데이터 = intelligence/event_hub.calendar_data() (/api/calendar). 읽기 전용.
  · 맨 위: 다가오는 '미리 사기 창'(✅ 검증 통과 · 🟡 참고 · ❌ 피할 것) + 앞으로 30일 이벤트
  · 아래: 월 달력(앞뒤 이동) — 날짜 칸에 이벤트 점(색 = 주기), 누르면 그날 이벤트·출처
"""

HTML = """<!DOCTYPE html>
<html lang="ko"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="theme-color" content="#0f1117"><title>이벤트 달력</title>
<style>
 :root{--bg:#0f1117;--card:#1a1d27;--bd:#2a2d3a;--tx:#e2e8f0;--sub:#94a3b8;--g:#4ade80;--r:#f87171;--y:#facc15;--b:#60a5fa;--p:#c084fc}
 *{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--tx);font-family:-apple-system,'Noto Sans KR',sans-serif;padding:4px 16px 40px}
 h1{font-size:18px;margin:6px 0 2px}.sub{color:var(--sub);font-size:12px;margin-bottom:8px}
 .grid2{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:10px;margin-bottom:12px}
 .card{background:var(--card);border:1px solid var(--bd);border-radius:12px;padding:12px}
 h2{font-size:15px;margin:0 0 6px}.row{display:flex;justify-content:space-between;gap:8px;padding:6px 0;border-top:1px solid var(--bd);font-size:13px}
 .small{font-size:11px;color:var(--sub)}.ok{color:var(--g)}.maybe{color:var(--y)}.avoid{color:var(--r)}.now{background:#1f2a1f}
 .mhead{display:flex;align-items:center;justify-content:space-between;margin:4px 0 8px}.mhead button{background:var(--card);color:var(--tx);border:1px solid var(--bd);border-radius:8px;padding:6px 12px;font-size:14px}
 .cal{display:grid;grid-template-columns:repeat(7,1fr);gap:4px}.dow{font-size:11px;color:var(--sub);text-align:center}
 .day{min-height:74px;background:var(--card);border:1px solid var(--bd);border-radius:8px;padding:4px;font-size:11px;cursor:pointer;overflow:hidden}
 .day.out{opacity:.35}.day.today{border-color:var(--y);border-width:2px}.day.sel{outline:2px solid var(--b)}.day.win{box-shadow:inset 0 -3px 0 var(--g)}
 .dn{font-weight:700;font-size:12px}.ev{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-top:2px}
 .yearly{color:var(--p)}.quarterly{color:var(--b)}.monthly{color:var(--sub)}.weekly{color:#64748b}.oneoff{color:var(--y)}
 .legend span{margin-right:10px}#detail .row{display:block}
 @media(max-width:640px){.day{min-height:52px}.ev{display:none}.day .dots{display:block}}
 .dots{display:none;font-size:10px;line-height:1}
</style></head><body>
<h1>📅 이벤트 달력</h1>
<div class="sub" id="sub">불러오는 중…</div>
<div class="grid2">
 <div class="card"><h2>🎯 미리 사기 창 <span class="small">(이벤트 2~4주 전 매수 → 셀온 전 · 섹터 ETF 10년 검증)</span></h2><div id="wins"></div></div>
 <div class="card"><h2>⏳ 앞으로 30일</h2><div id="next"></div></div>
</div>
<div class="card">
 <div class="mhead"><button id="prev">◀</button><b id="mtitle"></b><button id="nextm">▶</button></div>
 <div class="legend small"><span class="yearly">● 매년</span><span class="quarterly">● 매분기</span><span class="monthly">● 매월</span><span class="weekly">● 매주</span><span class="oneoff">● 돌발</span><span style="color:var(--g)">▁ 미리 사기 창</span></div>
 <div class="cal" id="cal"></div>
</div>
<div class="card" id="detail" style="margin-top:10px"><h2 id="dtitle">날짜를 누르면 그날 이벤트</h2><div id="dlist"></div></div>
<div class="small" style="margin-top:10px">출처: 규칙 달력(event_calendar) · 세이브티커 PDF [경제지표] · MTN 아침 받아쓰기(AI가 방송 문장만 뽑음, 원문 출처) · FOMC·금통위 공식 일정 — 매매 신호 아님, 대장 판단 재료</div>
<script>
const esc=s=>String(s??'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
const V={ok:['✅','ok','검증 통과'],maybe:['🟡','maybe','참고(몇 해 쏠림)'],avoid:['❌','avoid','피할 것']};
const md=s=>s.slice(5).replace('-','/');let D=null,cur=null,sel=null;
const byDay={};
function inWin(d){return (D.windows||[]).filter(w=>w.verdict==='ok'&&d>=w.buy&&d<=w.sell)}
function render(){
 const y=cur.getFullYear(),m=cur.getMonth();document.getElementById('mtitle').textContent=`${y}년 ${m+1}월`;
 const first=new Date(y,m,1),start=new Date(first);start.setDate(1-first.getDay());
 let h=['일','월','화','수','목','금','토'].map(x=>`<div class="dow">${x}</div>`).join('');
 for(let i=0;i<42;i++){const d=new Date(start);d.setDate(start.getDate()+i);
  const k=`${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
  const evs=byDay[k]||[];const gs=(D.day_groups||{})[k]||[];const cls=['day'];if(d.getMonth()!==m)cls.push('out');if(k===D.today)cls.push('today');if(k===sel)cls.push('sel');if(inWin(k).length)cls.push('win');
  const top=gs.filter(e=>e.impact==='높음'||e.recurrence==='yearly'||e.recurrence==='oneoff'||e.recurrence==='quarterly').slice(0,3);
  h+=`<div class="${cls.join(' ')}" data-k="${k}"><div class="dn">${d.getDate()}</div>`+
   top.map(e=>`<div class="ev ${e.recurrence||''}" title="${esc(e.members.join(' / '))}">${esc(e.title)}${e.n>1?` <span class="small">×${e.n}</span>`:''}</div>`).join('')+
   (gs.length>top.length?`<div class="small">+${gs.length-top.length}</div>`:'')+
   `<div class="dots">${gs.slice(0,5).map(e=>`<span class="${e.recurrence||''}">●</span>`).join('')}</div></div>`}
 const cal=document.getElementById('cal');cal.innerHTML=h;
 cal.querySelectorAll('.day').forEach(el=>el.onclick=()=>{sel=el.dataset.k;render();detail(sel)});
}
function detail(k){
 const evs=byDay[k]||[];const w=(D.windows||[]).filter(w=>k>=w.buy&&k<=w.sell);
 document.getElementById('dtitle').textContent=`${k} 이벤트 ${evs.length}건`;
 document.getElementById('dlist').innerHTML=w.map(x=>`<div class="row ${V[x.verdict][1]}">${V[x.verdict][0]} ${esc(x.event)} ${md(x.date)} — ${esc(x.sector)} ${x.verdict==='avoid'?'피하는 기간':'미리 사기 창'} 안 (${md(x.buy)}~${md(x.sell)})</div>`).join('')+
  (evs.sort((a,b)=>(a.title>b.title)-(a.title<b.title)).map(e=>`<div class="row"><b class="${e.recurrence||''}">${esc(e.title)}</b> <span class="small">${esc(e.time||'')} · ${esc(D.rec_names[e.recurrence]||'주기 미정')} · ${esc(e.kind)}${e.sectors?' · '+esc(e.sectors):''}</span>
   ${e.detail?`<div class="small">${esc(e.detail)}</div>`:''}<div class="small">출처: ${esc(e.source)}</div></div>`).join('')||'<div class="small">이벤트 없음</div>');
}
fetch('api/calendar?t='+Date.now(),{cache:'no-store'}).then(r=>r.json()).then(d=>{D=d;
 (d.events||[]).forEach(e=>(byDay[e.date]=byDay[e.date]||[]).push(e));
 document.getElementById('sub').textContent=`오늘 ${d.today} · 이벤트 ${d.events.length}건 (${d.start}~${d.end})`;
 const now=d.today;
 document.getElementById('wins').innerHTML=(d.windows||[]).filter(w=>w.sell>=now).map(w=>{const on=now>=w.buy&&now<=w.sell;
  return `<div class="row ${V[w.verdict][1]} ${on?'now':''}"><span>${V[w.verdict][0]} <b>${esc(w.event)}</b> ${md(w.date)} · ${esc(w.sector)}</span>
  <span class="small">${w.verdict==='avoid'?'피하기':'사기'} ${md(w.buy)} → ${w.verdict==='avoid'?'':'팔기 '}${md(w.sell)}${on?' · <b>지금 창 안</b>':''}</span></div><div class="small">${esc(w.why)}</div>`}).join('')||'<div class="small">다가오는 창 없음</div>';
 const end=new Date(Date.parse(now)+30*864e5).toISOString().slice(0,10);
 const nx=d.upcoming||[];   // ★ 11-28 대장 "정리 필요" — 같은 일(이름만 다른 MTN 반복 언급)은 한 줄로, 기간·언급 횟수 표시
 document.getElementById('next').innerHTML=nx.slice(0,25).map(g=>`<div class="row" title="${esc(g.members.join(' / '))}"><span>${md(g.start)}${g.end!==g.start?'~'+md(g.end):''} <b class="${g.recurrence||''}">${esc(g.title)}</b>${g.n>1?` <span class="small">언급 ${g.n}회</span>`:''}</span><span class="small">${esc(d.rec_names[g.recurrence]||'')}${g.sectors?' · '+esc(g.sectors):''}</span></div>`).join('')||'<div class="small">없음</div>';
 cur=new Date(Date.parse(now));cur.setDate(1);sel=now;render();detail(now);
 document.getElementById('prev').onclick=()=>{cur.setMonth(cur.getMonth()-1);render()};
 document.getElementById('nextm').onclick=()=>{cur.setMonth(cur.getMonth()+1);render()};
}).catch(e=>{document.getElementById('sub').textContent='불러오기 실패: '+e});
</script></body></html>"""
