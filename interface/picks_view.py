"""
picks_view.py — 대시보드 '🎯 후보' 탭: 아침 09:10 고무공·반도체·방패 한 장 (2026-11-26, 대장 "바쁘면 잊어먹고 지나갈 수 있거든")
================================================================
dashboard.py의 /picks(화면)·/api/picks(데이터). 데이터는 리나가 매 거래일 09:10에 만드는 intelligence/morning_picks.json.
오늘 것이 아니면 '어제(마지막) 기록'이라고 표시. 읽기 전용.
"""

HTML = """<!DOCTYPE html>
<html lang="ko"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="theme-color" content="#0f1117"><title>오늘의 후보</title>
<style>
 :root{--bg:#0f1117;--card:#1a1d27;--bd:#2a2d3a;--tx:#e2e8f0;--sub:#94a3b8;--g:#4ade80;--r:#f87171;--y:#facc15}
 *{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--tx);font-family:-apple-system,'Noto Sans KR',sans-serif;padding:4px 16px 40px}
 h1{font-size:18px;margin:6px 0 2px}.sub{color:var(--sub);font-size:12px;margin-bottom:8px}
 .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:10px}
 .card{background:var(--card);border:1px solid var(--bd);border-radius:12px;padding:12px}
 h2{font-size:15px;margin:0 0 4px}.why{font-size:11px;color:var(--sub);margin-bottom:8px}
 .row{display:flex;justify-content:space-between;gap:8px;padding:6px 0;border-top:1px solid var(--bd);font-size:13px}
 .row b{font-weight:600}.small{font-size:11px;color:var(--sub)}.p{color:var(--g)}.m{color:var(--r)}
 .warn{color:var(--y);font-size:12px;margin:4px 0}
</style></head><body>
<h1>🎯 오늘의 후보</h1><div class="sub" id="sub">불러오는 중…</div>
<div class="grid">
 <div class="card"><h2>🏀 고무공</h2><div class="why">3개월 주도 유지 & 1개월 고점 -15%↓ · 과거 급락 뒤 되돌림 순 — 짧게 튀는 쪽</div><div id="rb"></div></div>
 <div class="card"><h2>🔬 반도체 (김태성 전략)</h2><div class="why">소부장 6개월 고점 -50%↓ & 올해 영업이익 증가 — 가치 회복 쪽</div><div id="sm"></div></div>
 <div class="card"><h2>🛡️ 방패</h2><div class="why">대장 관심그룹 '방패_' · AI 빠지는데 방패 버티면 헤지 작동 중</div><div id="sh"></div></div>
</div>
<div class="small" style="margin-top:10px">리나가 거래일 09:10(특별일은 밀림)에 계산 · 매매 신호 아님 — 대장 판단 재료 · 리나 <b>!고무공</b> <b>!반도체</b> <b>!방패</b>로 지금 값 다시 보기</div>
<script>
const esc=s=>String(s??'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
const sg=v=>v>0?'+':'';const pc=v=>v==null?'-':`<span class="${v>0?'p':(v<0?'m':'')}">${sg(v)}${(+v).toFixed(1)}%</span>`;
fetch('api/picks?t='+Date.now(),{cache:'no-store'}).then(r=>r.json()).then(d=>{
 const today=new Date(Date.now()+9*3600e3).toISOString().slice(0,10);
 document.getElementById('sub').innerHTML=d.date?`${esc(d.date)} ${esc(d.at)} 계산`+(d.date!==today?' · <span class="warn">⚠️ 오늘 것 아님(마지막 기록)</span>':''):'아직 계산 기록 없음 — 거래일 09:10에 생겨요';
 const rb=d.rubber||[];
 document.getElementById('rb').innerHTML=d.rubber_err?`<div class="warn">계산 오류: ${esc(d.rubber_err)}</div>`:(rb.length?rb.map((r,i)=>`<div class="row"><span>${i+1}. ${r.mat?'🔥':''}<b>${esc(r.name)}</b> <span class="small">${esc((r.groups||[]).slice(0,2).join('·'))}</span></span>
  <span class="small">1개월 ${(+r.dd1m).toFixed(0)}% · 3개월 ${sg(r.r3m)}${(+r.r3m).toFixed(0)}%${r.rb_avg!=null?` · 반등 +${(+r.rb_avg).toFixed(0)}%`:''}</span></div>`).join(''):'<div class="small">오늘 후보 없음(3개월 주도 유지 종목이 급락한 게 없음)</div>');
 const s=d.semi||{};
 document.getElementById('sm').innerHTML=d.semi_err?`<div class="warn">계산 오류: ${esc(d.semi_err)}</div>`:
  ((s.good||[]).map((r,i)=>`<div class="row"><span>${i+1}. <b>${esc(r.name)}</b> <span class="small">${esc((r.groups||[]).slice(0,1).join(''))}</span></span>
   <span class="small">고점 ${(+r.dd).toFixed(0)}% · ${esc(r.y_this||'')}E 영업익 ${r.op_this!=null?Math.round(r.op_this).toLocaleString()+'억':'-'}${r.op_diff!=null?` (${sg(r.op_diff)}${Math.round(r.op_diff).toLocaleString()}억)`:''}</span></div>`).join('')||'<div class="small">없음</div>')+
  ((s.swap||[]).length?`<div class="small" style="margin-top:6px">🔁 교체 후보(이익 감소): ${(s.swap||[]).map(r=>esc(r.name)+'('+(+r.dd).toFixed(0)+'%)').join(', ')}</div>`:'')+
  ((s.hold||[]).length?`<div class="small" style="margin-top:6px">📒 내 보유: ${(s.hold||[]).map(h=>`${h[5]}${esc(h[0])}(${h[1]}) ${sg(h[4])}${(+h[4]).toFixed(0)}%`).join(' · ')}</div>`:'');
 const S=d.shield||{};
 document.getElementById('sh').innerHTML=d.shield_err?`<div class="warn">계산 오류: ${esc(d.shield_err)}</div>`:
  ((S.groups||[]).length?`<div class="row"><b>방패 평균 ${pc(S.avg)}</b><span>🤖 AI 분야 ${pc(S.ai)}</span></div>`+
   S.groups.map(g=>`<div class="row"><span><b>${esc(g.group)}</b> ${pc(g.chg_w)}</span><span class="small">${g.members.slice(0,4).map(m=>esc(m.name)+' '+pc(m.chg)).join(' · ')}</span></div>`).join('')
   :'<div class="small">방패 분야 없음 — 한투 관심그룹을 "방패_…"로</div>');
}).catch(e=>{document.getElementById('sub').textContent='불러오기 실패: '+e});
</script></body></html>"""
