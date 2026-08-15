pdfjsLib.GlobalWorkerOptions.workerSrc="https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js";
let SRC=null, FILES={original:[],raw:[]}, curFile=null, pdf=null, cur=1, num=0;
let edits={}, steps=[], mode=null, anchor=1, renderTask=null;
const $=id=>document.getElementById(id);
const qs=()=>`s=${SRC.subject}&t=${SRC.stage}&l=${SRC.level}&src=${SRC.source}`;
const J=async(u,opt)=>{const r=await fetch(u,opt);const j=await r.json();if(j.error)throw j.error;return j};

function applyTheme(t){document.body.classList.toggle('dark',t==='dark');$('themeBtn').innerHTML=IC(t==='dark'?'sun':'moon');}
function toggleTheme(){const t=document.body.classList.contains('dark')?'light':'dark';try{localStorage.setItem('qg_theme',t)}catch(e){}applyTheme(t)}
try{applyTheme(localStorage.getItem('qg_theme')||'light')}catch(e){}
// ---------------- i18n (cn default; en overlay). Static: data-en / data-en-ph / data-en-title
// on the element (Chinese stays the authored text, cached on first switch). Dynamic strings:
// T(cn,en). Persisted like theme; switching re-renders the active tab.
let LANG='cn';
function T(cn,en){return LANG==='en'?(en!=null?en:cn):cn;}
// ---------------- line-sketch icons (inline SVG, stroke = currentColor) ----------------
const ICONS={
 gear:'<path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/><circle cx="12" cy="12" r="3"/>',
 moon:'<path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/>',
 sun:'<circle cx="12" cy="12" r="4"/><path d="M12 2v2"/><path d="M12 20v2"/><path d="m4.93 4.93 1.41 1.41"/><path d="m17.66 17.66 1.41 1.41"/><path d="M2 12h2"/><path d="M20 12h2"/><path d="m6.34 17.66-1.41 1.41"/><path d="m19.07 4.93-1.41 1.41"/>',
 eye:'<path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7Z"/><circle cx="12" cy="12" r="3"/>',
 eyeoff:'<path d="M9.88 9.88a3 3 0 1 0 4.24 4.24"/><path d="M10.73 5.08A10.43 10.43 0 0 1 12 5c7 0 10 7 10 7a13.16 13.16 0 0 1-1.67 2.68"/><path d="M6.61 6.61A13.526 13.526 0 0 0 2 12s3 7 10 7a9.74 9.74 0 0 0 5.39-1.61"/><path d="m2 2 20 20"/>',
 chevL:'<path d="m15 18-6-6 6-6"/>',
 chevR:'<path d="m9 18 6-6-6-6"/>',
 scissors:'<circle cx="6" cy="6" r="3"/><path d="M8.12 8.12 12 12"/><path d="M20 4 8.12 15.88"/><circle cx="6" cy="18" r="3"/><path d="M14.8 14.8 20 20"/>',
 square:'<path d="M5 3a2 2 0 0 0-2 2"/><path d="M19 3a2 2 0 0 1 2 2"/><path d="M21 19a2 2 0 0 1-2 2"/><path d="M5 21a2 2 0 0 1-2-2"/><path d="M9 3h1"/><path d="M9 21h1"/><path d="M14 3h1"/><path d="M14 21h1"/><path d="M3 9v1"/><path d="M21 9v1"/><path d="M3 14v1"/><path d="M21 14v1"/>',
 upload:'<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="m17 8-5-5-5 5"/><path d="M12 3v12"/>',
 play:'<path d="M6 3 20 12 6 21Z"/>',
 stop:'<rect width="18" height="18" x="3" y="3" rx="2"/>',
 save:'<path d="M15.2 3a2 2 0 0 1 1.4.6l3.8 3.8a2 2 0 0 1 .6 1.4V19a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z"/><path d="M17 21v-7a1 1 0 0 0-1-1H8a1 1 0 0 0-1 1v7"/><path d="M7 3v4a1 1 0 0 0 1 1h7"/>',
 format:'<path d="M21 6H3"/><path d="M15 12H3"/><path d="M17 18H3"/>',
 clock:'<circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/>',
 book:'<path d="M12 7v14"/><path d="M3 18a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h5a4 4 0 0 1 4 4 4 4 0 0 1 4-4h5a1 1 0 0 1 1 1v13a1 1 0 0 1-1 1h-6a3 3 0 0 0-3 3 3 3 0 0 0-3-3z"/>',
 dice:'<rect width="18" height="18" x="3" y="3" rx="2" ry="2"/><path d="M16 8h.01"/><path d="M8 8h.01"/><path d="M8 16h.01"/><path d="M16 16h.01"/><path d="M12 12h.01"/>',
 sparkles:'<path d="M9.937 15.5A2 2 0 0 0 8.5 14.063l-6.135-1.582a.5.5 0 0 1 0-.962L8.5 9.936A2 2 0 0 0 9.937 8.5l1.582-6.135a.5.5 0 0 1 .963 0L14.063 8.5A2 2 0 0 0 15.5 9.937l6.135 1.581a.5.5 0 0 1 0 .964L15.5 14.063a2 2 0 0 0-1.437 1.437l-1.582 6.135a.5.5 0 0 1-.963 0z"/><path d="M20 3v4"/><path d="M22 5h-4"/><path d="M4 17v2"/><path d="M5 18H3"/>',
 trash:'<path d="M3 6h18"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><path d="M10 11v6"/><path d="M14 11v6"/>',
 check:'<path d="M20 6 9 17l-5-5"/>',
 x:'<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
 clip:'<path d="m21.44 11.05-9.19 9.19a6 6 0 0 1-8.49-8.49l8.57-8.57A4 4 0 1 1 18 8.84l-8.59 8.57a2 2 0 0 1-2.83-2.83l8.49-8.48"/>',
 books:'<path d="m16 6 4 14"/><path d="M12 6v14"/><path d="M8 8v12"/><path d="M4 4v16"/>',
 file:'<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/><path d="M10 9H8"/><path d="M16 13H8"/><path d="M16 17H8"/>',
 key:'<path d="m15.5 7.5 2.3 2.3a1 1 0 0 0 1.4 0l2.1-2.1a1 1 0 0 0 0-1.4L19 4"/><path d="m21 2-9.6 9.6"/><circle cx="7.5" cy="15.5" r="5.5"/>',
 pin:'<path d="M20 10c0 4.993-5.539 10.193-7.399 11.799a1 1 0 0 1-1.202 0C9.539 20.193 4 14.993 4 10a8 8 0 0 1 16 0"/><circle cx="12" cy="10" r="3"/>',
 warn:'<path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z"/><path d="M12 9v4"/><path d="M12 17h.01"/>',
 plus:'<path d="M5 12h14"/><path d="M12 5v14"/>',
 restore:'<path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/>',
 table:'<path d="M12 3v18"/><rect width="18" height="18" x="3" y="3" rx="2"/><path d="M3 9h18"/><path d="M3 15h18"/>',
 edit:'<path d="M17 3a2.828 2.828 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5L17 3z"/><path d="m15 5 4 4"/>',
 flag:'<path d="M4 15s1-1 4-1 5 2 8 2 4-1 4-1V3s-1 1-4 1-5-2-8-2-4 1-4 1z"/><path d="M4 22v-7"/>',
 folderopen:'<path d="m6 14 1.5-2.9A2 2 0 0 1 9.24 10H20a2 2 0 0 1 1.94 2.5l-1.54 6a2 2 0 0 1-1.95 1.5H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h3.9a2 2 0 0 1 1.69.9l.81 1.2a2 2 0 0 0 1.67.9H18a2 2 0 0 1 2 2v2"/>'
};
function IC(name,extra){return '<svg class="ic'+(extra?' '+extra:'')+'" viewBox="0 0 24 24" aria-hidden="true">'+(ICONS[name]||'')+'</svg>';}
function injectIcons(root){(root||document).querySelectorAll('[data-ic]').forEach(el=>{if(!el.querySelector('svg.ic'))el.insertAdjacentHTML('afterbegin',IC(el.dataset.ic));});}
// a masked secret field (API token / key) with a show/hide eye toggle
function secretInput(attrs,value,ph){
  return `<div class="secret"><input type="password" ${attrs} value="${esc(value||'')}" placeholder="${esc(ph||'')}">`
    +`<button type="button" class="eye" onclick="toggleSecret(this)" title="${T('显示/隐藏','show/hide')}">${IC('eye')}</button></div>`;
}
function toggleSecret(btn){const inp=btn.parentNode.querySelector('input');const show=inp.type==='password';inp.type=show?'text':'password';btn.innerHTML=IC(show?'eyeoff':'eye');}
function applyLang(){
  document.documentElement.lang=LANG==='en'?'en':'zh';
  $('langBtn').textContent=LANG==='en'?'中文':'EN';
  document.querySelectorAll('[data-en]').forEach(el=>{
    if(el.children.length)return;          // never overwrite a container's children (would drop inputs/selects)
    if(el._cn==null)el._cn=el.textContent;
    el.textContent=LANG==='en'?el.getAttribute('data-en'):el._cn;
  });
  document.querySelectorAll('[data-en-ph]').forEach(el=>{
    if(el._cnph==null)el._cnph=el.getAttribute('placeholder')||'';
    el.setAttribute('placeholder',LANG==='en'?el.getAttribute('data-en-ph'):el._cnph);
  });
  document.querySelectorAll('[data-en-title]').forEach(el=>{
    if(el._cnt==null)el._cnt=el.getAttribute('title')||'';
    el.setAttribute('title',LANG==='en'?el.getAttribute('data-en-title'):el._cnt);
  });
  if(typeof tab!=='undefined'){                 // re-render dynamic strings of the open tab
    if(tab==='bank'){refreshBankFilters(true);renderBank();renderCart();}
    if(tab==='pipe')loadPipe();
    if(tab==='gen')loadPending();
    if(tab==='sources')renderSteps();
  }
}
function toggleLang(){LANG=LANG==='en'?'cn':'en';try{localStorage.setItem('qg_lang',LANG)}catch(e){}applyLang();}
try{LANG=localStorage.getItem('qg_lang')||'cn';}catch(e){}
async function loadSources(keepCur){
  const list=await J('/api/sources');
  const want=keepCur&&SRC?JSON.stringify(SRC):(list.length?JSON.stringify(list[0]):'');
  $('srcSel').innerHTML=list.map(s=>`<option value='${JSON.stringify(s)}'>${s.subject}/${s.stage}/${s.level}/${s.source}</option>`).join('');
  if(want){$('srcSel').value=want; SRC=JSON.parse(want);}
  return list;
}
async function init(){
  injectIcons();                                // line-sketch icons into [data-ic] elements
  try{applyTheme(localStorage.getItem('qg_theme')||'light')}catch(e){}   // set theme icon (DOM ready)
  applyLang();                                  // apply persisted CN/EN to static chrome
  loadProblemTypes();
  const list=await loadSources(false);
  $('srcSel').onchange=()=>{SRC=JSON.parse($('srcSel').value);TAX={};loadFiles();if(tab==='pipe')loadPipe();if(tab==='bank')loadBank();if(tab==='gen')loadGen()};
  if(list.length){SRC=list[0];loadFiles()}
}
// ---------------- pipeline tab
let tab='sources', jobN=0, polling=false, pipeSrc=null;
function showTab(t){tab=t;
  $('viewSources').style.display=t==='sources'?'flex':'none';
  $('viewPipe').style.display=t==='pipe'?'flex':'none';
  $('viewBank').style.display=t==='bank'?'flex':'none';
  $('viewGen').style.display=t==='gen'?'flex':'none';
  $('tabSources').classList.toggle('on',t==='sources');$('tabPipe').classList.toggle('on',t==='pipe');
  $('tabBank').classList.toggle('on',t==='bank');$('tabGen').classList.toggle('on',t==='gen');
  if(t==='pipe'){loadPipe();pollJob()}
  if(t==='bank')loadBank();
  if(t==='gen')loadGen()}
// ---------------- AI generate
function renderGenRefs(){
  const box=$('gRefs'); if(!box)return;
  if(!REFS.length){box.innerHTML='<div class="hint">'+T('未选参考题 → 按所选 topic 从 bank 随机采 k 题。','No refs picked → k are sampled from the bank by topic.')+'<br>'+T('在 Bank 里点「AI 参考」可指定参考题。','Pick refs with the “AI ref” button in Bank.')+'</div>';return;}
  box.innerHTML='<div class="hint" style="margin-bottom:3px"><b>'+T('已选参考题','Picked refs')+' '+REFS.length+'</b> '+T('(覆盖随机采样)','(overrides sampling)')+' '
    +'<button onclick="clearRefs()" style="margin-left:6px">'+T('清空','Clear')+'</button></div>'
    +REFS.map(e=>`<div class="hint" style="display:flex;gap:4px;align-items:center">`
      +`<button class="danger" style="padding:0 5px" onclick="toggleRef('${e.qid}')">${IC('x')}</button>`
      +`<span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(e.qid)}</span></div>`).join('');
}
let GENTAX={};
async function loadGen(){
  if(!PTYPES.length)await loadProblemTypes();
  {const cur=$('gType').value; $('gType').innerHTML='<option value="">'+T('任意','any')+'</option>'+typeOpts(cur); $('gType').value=cur;}
  try{GENTAX=await J('/api/taxonomy?'+qs())}catch(e){GENTAX={}}
  const prev=new Set(selTopics());
  const ids=Object.keys(GENTAX);
  $('gTopicList').innerHTML=ids.length?ids.map(id=>`<label class="chkrow"><input type="checkbox" class="gtopic" value="${id}"${prev.has(id)?' checked':''} onchange="renderObjs()"><span><b>${id}</b> — ${esc((GENTAX[id]||{}).name||'')}</span></label>`).join(''):'<div class="hint" style="padding:6px">'+T('无 taxonomy','no taxonomy')+'</div>';
  renderObjs(); renderGenRefs(); loadPending();
}
function selTopics(){return [...document.querySelectorAll('.gtopic:checked')].map(c=>c.value);}
function renderObjs(){
  const box=$('gObjList'); if(!box)return;
  const sel=selTopics();
  if(!sel.length){box.innerHTML='<div class="hint" style="padding:6px">'+T('选 topic 后显示其学习目标','Learning objectives appear after picking a topic')+'</div>';return;}
  const prev=new Set([...box.querySelectorAll('.gobj:checked')].map(c=>c.value));
  let h='';
  sel.forEach(id=>{
    const los=(GENTAX[id]||{}).los||[];
    if(!los.length)return;
    h+=`<div class="objgrp">${esc(id)}</div>`+los.map(lo=>{const v=id+'::'+lo;return `<label class="chkrow"><input type="checkbox" class="gobj" value="${esc(v)}"${prev.has(v)?' checked':''}><span>${esc(lo)}</span></label>`;}).join('');
  });
  box.innerHTML=h||'<div class="hint" style="padding:6px">'+T('所选 topic 无学习目标','selected topic has no learning objectives')+'</div>';
}
async function genRun(){
  const topics=selTopics();
  if(!topics.length && !REFS.length){$('gMsg').textContent=T('请选择 topic 或勾选 bank 参考题','Pick a topic or check bank references');return;}
  const objectives=[...document.querySelectorAll('.gobj:checked')].map(c=>{const p=c.value.split('::');return {topic:p[0],lo:p.slice(1).join('::')};});
  $('gRunBtn').disabled=true; $('gMsg').textContent=T('生成中… (视模型可能数十秒)','Generating… (may take tens of seconds)');
  try{
    const r=await J('/api/generate?'+qs(),{method:'POST',body:JSON.stringify({
      topics,objectives,difficulty:$('gDiff').value,qtype:$('gType').value,n:+$('gN').value,k:+$('gK').value,
      with_solutions:$('gSol').checked,model:$('gModel').value,prompt:$('gPrompt').value,
      refs:REFS})});
    $('gMsg').textContent=T('已生成 '+r.n+' 题, 入待审','Generated '+r.n+' — queued for review');
    loadPending();
  }catch(e){$('gMsg').textContent=T('生成失败: ','Generation failed: ')+e;}
  $('gRunBtn').disabled=false;
}
async function toggleRefs(ts){
  const p=$('genRefPanel'); if(!p)return;
  if(p.dataset.open==='1'){p.innerHTML='';p.dataset.open='0';return;}
  let ex=[]; try{ex=(await J('/api/gen_refs?'+qs()+'&ts='+encodeURIComponent(ts))).examples||[]}catch(e){}
  if(!ex.length){p.innerHTML='<div class="hint">'+T('该批无参考题记录','no reference questions recorded for this batch')+'</div>';p.dataset.open='1';return;}
  p.innerHTML='<div class="hint" style="margin:4px 0">'+IC('books')+' '+T('喂给 AI 的参考题','references fed to the AI')+' ('+ex.length+') · '+T('批次','batch')+' '+esc(ts)+'</div>'+ex.map(e=>{
    const fs=e.fs||''; const parts=renderParts(e.parts,fs,0);
    const opts=e.options?`<div class="opts">${Object.entries(e.options).map(([k,v])=>`<span class="opt"><b>${esc(optLabel(k))}</b> ${rich(v,fs)}</span>`).join('')}</div>`:'';
    const ans=e.answer?`<div class="hint"><b>Ans:</b> ${rich(String(e.answer),fs)}</div>`:'';
    return `<div class="gcard" style="border-left-color:var(--dim)"><div class="hint"><b>${esc(e.qid||'')}</b> · ${(e.topic||[]).join(',')} · ${esc(e.difficulty||'')}</div>${e.stem?`<div>${rich(e.stem,fs)}</div>`:''}${opts}${parts}${ans}</div>`;
  }).join('');
  p.dataset.open='1';
  if(window.MathJax&&MathJax.typesetPromise)MathJax.typesetPromise([p]).catch(()=>{});
}
async function loadPending(){
  let ents=[]; try{ents=(await J('/api/gen_pending?'+qs())).entries||[]}catch(e){}
  if(!ents.length){$('genList').innerHTML=`<div class="hint">${T('待审队列为空。左侧生成新题。','Review queue empty. Generate on the left.')}</div>`;return;}
  const lastTs=ents.map(e=>e.meta&&e.meta.gen&&e.meta.gen.ts).filter(Boolean).sort().slice(-1)[0]||'';
  $('genList').innerHTML='<div class="hint" style="margin-bottom:8px">'+T('待审 '+ents.length+' 题','Pending '+ents.length)
    +(lastTs?` <button onclick="toggleRefs('${lastTs}')" style="margin-left:8px">${IC('books')}${T('查看参考题','view references')}</button>`:'')
    +'</div><div id="genRefPanel" data-open="0"></div>'+ents.map(e=>{
    const parts=renderParts(e.parts,'ai_generated',0);
    const opts=e.options?`<div class="opts">${Object.entries(e.options).map(([k,v])=>`<span class="opt"><b>${esc(optLabel(k))}</b> ${rich(v,'ai_generated')}</span>`).join('')}</div>`:'';
    const sol=(e.answer||e.solution)?`<details><summary>${T('答案 / 解答','Answer / Solution')}</summary>
        ${e.answer?`<div><b>Ans:</b> ${rich(String(e.answer.value??''),'ai_generated')}</div>`:''}
        ${e.solution?`<div>${rich(e.solution,'ai_generated')}</div>`:''}</details>`:'';
    const tg=e.tags?`<span class="badge" style="background:var(--tag);color:var(--acc)">${(e.tags.topic||[]).join(',')} · ${e.tags.type||'?'} · ${e.tags.difficulty||'?'}</span>`:'';
    const nov=e.meta&&e.meta.gen&&e.meta.gen.novelty;   // similarity to bank/reference (anti-copy check)
    const nb=nov?`<span class="badge" title="与已有题最高相似度; 匹配 ${esc(nov.match||'')} (${esc(nov.where||'')})" style="background:${nov.score>=0.5?'var(--coral)':'var(--nobg)'};color:${nov.score>=0.5?'#fff':'var(--dim)'}">${nov.score>=0.5?IC('warn')+' ':''}${T('相似','similar')} ${Math.round(nov.score*100)}%</span>`:'';
    return `<div class="gcard"><div class="hd"><div class="hd-main"><b>${esc(e.qid)}</b> <span class="badge no">AI</span> ${tg} ${nb}</div>
      <div class="hd-act">
      <button class="okbtn" onclick="genAccept('${e.qid}')">${IC('check')}${T('接受','accept')}</button>
      <button class="danger" onclick="genReject('${e.qid}')">${IC('x')}${T('拒绝','reject')}</button></div></div>
      ${e.stem&&e.stem.trim()?`<div>${rich(e.stem,'ai_generated')}</div>`:''}${opts}${parts}${sol}</div>`;
  }).join('');
  if(window.MathJax&&MathJax.typesetPromise)MathJax.typesetPromise([$('genList')]).catch(()=>{});
}
async function genAccept(qid){
  try{await J('/api/gen_accept?'+qs(),{method:'POST',body:JSON.stringify({qid})});
    loadPending(); loadSources(true);   // ai_generated source may be new -> refresh dropdown
  }catch(e){alert(T('accept 失败: ','accept failed: ')+e)}
}
async function genReject(qid){
  try{await J('/api/gen_reject?'+qs(),{method:'POST',body:JSON.stringify({qid})});loadPending();}
  catch(e){alert(T('reject 失败: ','reject failed: ')+e)}
}
// ---------------- settings (two tabs: API keys / models · problem types)
// registration links per provider so a new user can get a key
const KEY_LINKS={mineru:['https://mineru.net/apiManage/token',T('获取 MinerU API token','Get MinerU API token')],
  gen:['https://platform.deepseek.com/api_keys','DeepSeek API key'],
  llm:['https://platform.deepseek.com/api_keys','DeepSeek API key'],
  vlm:['https://bailian.console.aliyun.com/','DashScope (Qwen) API key']};
function setTab(w){
  ['api','types'].forEach(t=>{$('setPane_'+t).style.display=t===w?'block':'none';
    $('setTab_'+t).classList.toggle('on',t===w);});
}
async function openSettings(){
  let r={}; try{r=await J('/api/config')}catch(e){$('setMsg').textContent=T('读取失败','load failed');}
  const c=r.config||{}, secs=['gen','llm','vlm'];
  const lnk=k=>{const L=KEY_LINKS[k];return L?` <a href="${L[0]}" target="_blank" rel="noopener" style="color:var(--blue);font-size:11px">${IC('key')} ${esc(L[1])} →</a>`:'';};
  const mineru=`<div class="setSec" data-mineru="1"><h4>MinerU ${T('(PDF 解析云服务)','(PDF extraction cloud)')}${lnk('mineru')}</h4>
    <label>API token</label>${secretInput('id="setMineru"', r.mineru_token||'', T('留空=不改','empty=keep'))}</div>`;
  const api=mineru+secs.map(s=>{const d=c[s]||{};const nm={gen:T(' (AI 生成)',' (AI generation)'),llm:T(' (清洗/文本)',' (clean/text)'),vlm:T(' (VLM 读图)',' (VLM figures)')}[s];
    return `<div class="setSec" data-sec="${s}"><h4>${s}${nm}${lnk(s)}</h4>
    <label>base_url</label><input type="text" data-f="base_url" value="${esc(d.base_url||'')}">
    <label>model</label><input type="text" data-f="model" value="${esc(d.model||'')}">
    <label>API key</label>${secretInput('data-f="key"', d.key||'', T('留空=不改','empty=keep'))}
    <div class="row" style="margin-top:5px"><label style="margin:0">temperature <input type="text" data-f="temperature" value="${d.temperature!=null?d.temperature:''}" style="width:56px"></label>
      <label style="margin:0"><input type="checkbox" data-f="thinking" ${d.thinking?'checked':''}> thinking</label></div>
  </div>`;}).join('');
  const types=`<div class="setSec"><h4>${T('问题类型 problem_types','Problem types')}</h4>
    <label>${T('逗号分隔 (tag + 生成共用; 仅小写字母/数字/下划线)','comma-separated (used by tag + generation; lowercase a-z0-9_ only)')}</label>
    <input type="text" id="setTypes" value="${esc((r.problem_types||[]).join(', '))}"></div>`;
  $('setBody').innerHTML=`<div id="setTabs" style="display:flex;gap:8px;margin-bottom:4px">
      <button id="setTab_api" class="on" onclick="setTab('api')">${T('API / 模型','API / Models')}</button>
      <button id="setTab_types" onclick="setTab('types')">${T('题型','Question types')}</button></div>
    <div id="setPane_api">${api}</div><div id="setPane_types" style="display:none">${types}</div>`;
  $('setMsg').textContent=''; $('setModal').classList.add('on');
}
async function saveSettings(){
  const body={};
  document.querySelectorAll('#setBody .setSec[data-sec]').forEach(sec=>{
    const s=sec.dataset.sec, d={};
    sec.querySelectorAll('[data-f]').forEach(el=>{
      const f=el.dataset.f;
      if(f==='thinking')d[f]=el.checked;
      else if(f==='temperature')d[f]=parseFloat(el.value)||0;
      else d[f]=el.value;
    });
    body[s]=d;
  });
  const mt=$('setMineru'); if(mt&&mt.value.trim())body.mineru_token=mt.value.trim();
  const tv=$('setTypes'); if(tv)body.problem_types=tv.value.split(',').map(x=>x.trim()).filter(Boolean);
  $('setMsg').textContent=T('保存中…','Saving…');
  try{await J('/api/config',{method:'POST',body:JSON.stringify(body)});$('setMsg').textContent=T('✓ 已保存并热重载','✓ Saved & hot-reloaded');
    loadProblemTypes();   // refresh cached type list for dropdowns
  }catch(e){$('setMsg').textContent=T('保存失败: ','Save failed: ')+e;}
}
async function loadPipe(){
  const p=await J('/api/pipeline?'+qs());
  const c=p.ctx||{};
  $('pipeCtx').innerHTML=`${IC('pin')} <b>${esc(c.subject||'')}</b> / <b>${esc(c.stage||'')}</b> / <b>${esc(c.level||'')}</b> / <b style="color:var(--acc)">${esc(c.source||'')}</b>`;
  if(pipeSrc!==c.source){pipeSrc=c.source; $('optChem').checked=/^chem/i.test(c.subject||'');}  // auto-suggest per source (covers chemistry/chemestry)
  $('pipeStat').textContent=(p.mock?'MOCK · ':'')+(p.token?T('token ✓','token ok'):IC('warn')+T(' 无 MinerU token',' no MinerU token'))
    +' · '+(p.llm?('LLM: '+p.llm):T('⚠ 无 LLM 端点','⚠ no LLM endpoint'))
    +' · '+(p.vlm?('VLM: '+p.vlm):T('VLM 未配置','VLM off'))
    +' · DB: '+(p.db?'✓':'—');
  const liveBadge=s=>s?`<span class="badge ok" title="${T('Bank 读取的阶段','stage the Bank serves')}">${s}</span>`:`<span class="badge no">—</span>`;
  $('pipeRows').innerHTML=p.files.map(f=>{
    const it=f.interim, cl=f.clean, tg=f.tagged;
    // a run that dropped blocks / produced 0 questions is a data-loss signal, not a quiet "0"
    const itWarn=it&&(it.warnings||[]).length;
    const itBadge=it?`<span class="badge ${itWarn?'':'ok'}" style="${itWarn?'background:var(--coral);color:#fff':''}" title="${itWarn?esc(it.warnings.join(' | ')):'questions / flagged'}">${itWarn?IC('warn'):''}${it.questions}q · ${it.with_solution}sol · ${it.flagged}${IC('flag')}</span>`
                    :`<span class="badge no">—</span>`;
    const clBadge=cl?`<span class="badge ok" title="fixed / severe">${cl.fixed??'?'} / ${cl.severe??0}</span>`
                    :`<span class="badge no">—</span>`;
    const tgBadge=tg?`<span class="badge ok" title="tagged / bad_topic">${tg.tagged??'?'}${tg.bad_topic?' · '+tg.bad_topic+'✗':''}</span>`
                    :`<span class="badge no">—</span>`;
    return `<tr>
    <td><input type="checkbox" class="pchk" value="${f.name}" ${it?'':'checked'}></td>
    <td>${f.name}</td><td>${f.size_mb} MB</td>
    <td><span class="badge ${f.extracted?'ok':'no'}">${f.extracted?'extracted':'—'}</span></td>
    <td>${itBadge}</td><td>${clBadge}</td><td>${tgBadge}</td><td>${liveBadge(f.live)}</td></tr>`}).join('');
}
// A pipeline run REWRITES the bank for the files it covers (interim also invalidates
// clean/tagged), so the server refuses the first attempt when entries already exist and
// reports what would be lost; we show it and let the user snapshot in the same click.
let WARN=null;
async function runStep(step,opts){
  const files=[...document.querySelectorAll('.pchk:checked')].map(c=>c.value);
  if(!files.length)return;
  const use_vlm=$('optVlm').checked, thinking=$('optThink').checked, force=$('optForce').checked, chem=$('optChem').checked;
  const body={step,files,use_vlm,thinking,force,chem,...(opts||{})};
  try{
    const r=await J('/api/run?'+qs(),{method:'POST',body:JSON.stringify(body)});
    if(r.needs_confirm){WARN={step,files};showWarn(r);return}
    closeWarn();
    $('jobLog').textContent=r.backup?('['+T('已备份题库','bank backed up')+': '+r.backup+']\n'):'';jobN=0;pollJob()
  }catch(e){$('jobLog').textContent='ERROR: '+e}
}
function showWarn(r){
  const k=r.risk||{}, rows=(k.files||[]).map(f=>`<tr><td>${esc(f.file)}</td><td>${f.stage}</td>
    <td>${f.n} ${T('题','q')}</td><td>${f.edited?'<b style="color:var(--red)">'+f.edited+' '+T('已编辑','edited')+'</b>':'—'}</td>
    <td>${f.verified?f.verified+' '+T('已验证','verified'):'—'}</td></tr>`).join('');
  $('warnBody').innerHTML=`<div>${T('步骤','Step')} <b>${esc(k.step||'')}</b> ${T('会重写','will overwrite')}: <b>${esc(k.target||'')}</b></div>
    <div style="margin:6px 0">${T('当前题库共','This bank has')} <b>${k.entries||0}</b> ${T('题','questions')}`
    +(k.edited?` · <b style="color:var(--red)">${k.edited} ${T('题有人工编辑','manually edited')}</b>`:'')
    +(k.verified?` · ${k.verified} ${T('题已验证','verified')}`:'')
    +` — ${T('重跑后这些人工内容会被覆盖 (无法由重跑还原)。','a re-run overwrites this manual work (not recoverable by re-running).')}</div>
    <table><thead><tr><th>${T('文件','File')}</th><th>${T('当前阶段','Stage')}</th><th>${T('条目','Entries')}</th><th>${T('人工编辑','Edited')}</th><th>${T('验证','Verified')}</th></tr></thead><tbody>${rows}</tbody></table>
    <div class="hint" style="margin-top:8px">${T('已有备份','Backups')}: ${r.backups||0}${T(' 份','')}. ${T('备份 = 当前来源 interim/ 的完整快照, 可在 Bank 页「恢复选中备份」回滚。','A backup is a full snapshot of the current source interim/; roll back via “Restore selected” in Bank.')}</div>`;
  $('warnModal').classList.add('on');
}
function closeWarn(){$('warnModal').classList.remove('on');WARN=null;}
function warnGo(backup){
  if(!WARN)return closeWarn();
  const w=WARN; closeWarn();
  runStep(w.step,{confirm:true,backup_first:!!backup});
}
async function pollJob(){
  if(polling)return;polling=true;
  try{while(true){
    const r=await J('/api/job?from='+jobN);
    if(r.log.length){$('jobLog').textContent+=r.log.join('\n')+'\n';jobN=r.n;$('jobLog').scrollTop=1e9}
    if(r.state!=='running'){if(r.name)$('jobLog').textContent+=`[${r.state}${r.dt?' '+r.dt+'s':''}]\n`;break}
    await new Promise(res=>setTimeout(res,1500));
  }}finally{polling=false;if(tab==='pipe')loadPipe()}
}
async function loadFiles(){
  prefillUpload();
  FILES=await J('/api/files?'+qs());
  const li=(f,del)=>`<div class="file ${f.name===curFile?'on':''}" onclick="openFile('${f.name}')">
    <span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${f.name.split('/')[1]}</span>
    <small>${f.pages??'?'}p</small>${del?`<span class="x" onclick="event.stopPropagation();delRaw('${f.name}')">✕</span>`:''}</div>`;
  const folderBtn=w=>`<button class="foldbtn" onclick="openFolder('${w}')" title="${T('在文件管理器中打开','open in file manager')}">${IC('folderopen')}</button>`;
  $('files').innerHTML='<h4>original/ '+folderBtn('original')+'</h4>'+FILES.original.map(f=>li(f,0)).join('')
                      +'<h4>raw/ '+folderBtn('raw')+'</h4>'+FILES.raw.map(f=>li(f,1)).join('');
  refreshOps();
}
function openFolder(which){
  if(!SRC){toast(T('请先选择来源','pick a source first'));return;}
  J('/api/open_folder?'+qs()+'&which='+which).catch(e=>toast(T('无法打开文件夹','could not open folder')));
}
function prefillUpload(){   // seed the upload target from the current source (still editable)
  if(!SRC)return;
  const m={upSubj:'subject',upStage:'stage',upLevel:'level',upSource:'source'};
  for(const id in m){const el=$(id); if(el&&!el.value)el.value=SRC[m[id]]||'';}
}
async function uploadSources(){
  const subject=$('upSubj').value.trim(),stage=$('upStage').value.trim(),
        level=$('upLevel').value.trim(),source=$('upSource').value.trim(),into=$('upInto').value;
  const files=[...$('upFile').files];
  if(!subject||!stage||!level||!source){$('upMsg').textContent=T('请填全 subject/stage/level/source','Fill in subject/stage/level/source');return;}
  if(!files.length){$('upMsg').textContent=T('请选择文件 (PDF 或图片)','Pick file(s) — PDF or image');return;}
  $('upBtn').disabled=true;
  let ok=0;
  try{
    for(const f of files){
      $('upMsg').textContent=T('上传中… ','Uploading… ')+f.name;
      const data=await new Promise(r=>{const fr=new FileReader();fr.onload=()=>r(fr.result);fr.readAsDataURL(f);});
      await J('/api/upload_source',{method:'POST',body:JSON.stringify({subject,stage,level,source,into,name:f.name,data})});
      ok++;
    }
    $('upMsg').textContent=T(`✓ 已上传 ${ok} 个到 ${level}/${source}/${into}`,`✓ Uploaded ${ok} to ${level}/${source}/${into}`);
    $('upFile').value='';
    const list=await loadSources(true);          // new source shows in the dropdown
    const want=list.find(s=>s.subject===subject&&s.stage===stage&&s.level===level&&s.source===source);
    if(want){$('srcSel').value=JSON.stringify(want);SRC=want;}
    loadFiles();
  }catch(e){$('upMsg').textContent='ERROR: '+e;}
  $('upBtn').disabled=false;
}
async function openFile(f){
  curFile=f; edits[f]=edits[f]||{}; loadFiles();
  $('stat').textContent='loading '+f+' …';
  pdf=await pdfjsLib.getDocument({url:'/api/pdf?'+qs()+'&f='+encodeURIComponent(f)}).promise;
  num=pdf.numPages;$('pcount').textContent='/ '+num;$('stat').textContent=midEllipsis(f,54);$('stat').title=f;
  buildThumbs(); goto(1);
}
function buildThumbs(){
  const t=$('thumbs');t.innerHTML='';
  const io=new IntersectionObserver(es=>es.forEach(async e=>{
    if(!e.isIntersecting)return; io.unobserve(e.target);
    const p=+e.target.dataset.p, page=await pdf.getPage(p), vp=page.getViewport({scale:96/page.getViewport({scale:1}).width});
    const c=document.createElement('canvas');c.width=vp.width;c.height=vp.height;
    await page.render({canvasContext:c.getContext('2d'),viewport:vp}).promise;
    e.target.appendChild(c);
  }),{root:t,rootMargin:'300px'});
  for(let p=1;p<=num;p++){
    const d=document.createElement('div');d.className='thumb';d.dataset.p=p;
    d.innerHTML=`<i>${p}</i><em id="tb${p}"></em>`;
    d.onclick=ev=>{if(ev.shiftKey){$('range').value=Math.min(anchor,p)+'-'+Math.max(anchor,p);markSel()}else{anchor=p;goto(p)}};
    t.appendChild(d);io.observe(d);
  }
  markEditBadges();
}
function markSel(){
  let sel=new Set();try{sel=new Set(parseRange($('range').value))}catch(e){}
  document.querySelectorAll('.thumb').forEach(d=>d.classList.toggle('sel',sel.has(+d.dataset.p)));
}
// middle-ellipsis a long path so the start and the .pdf extension stay visible
function midEllipsis(s,max){s=String(s);if(s.length<=max)return s;const keep=max-1,head=Math.ceil(keep*0.62),tail=keep-head;return s.slice(0,head)+'…'+s.slice(s.length-tail);}
function markEditBadges(){
  for(let p=1;p<=num;p++){const e=(edits[curFile]||{})[p];const b=$('tb'+p);
    if(b)b.innerHTML=e?((e.crop?IC('scissors'):'')+(e.masks&&e.masks.length?IC('square'):'')):''}
}
async function goto(p){
  if(!pdf||p<1||p>num)return; cur=p;$('pageIn').value=p;
  document.querySelectorAll('.thumb').forEach(d=>d.classList.toggle('cur',+d.dataset.p===p));
  const th=document.querySelector(`.thumb[data-p="${p}"]`);if(th)th.scrollIntoView({block:'nearest'});
  const page=await pdf.getPage(p), base=page.getViewport({scale:1});
  const scale=Math.min(660/base.width, 1.3), vp=page.getViewport({scale});
  const c=$('pg');c.width=vp.width;c.height=vp.height;
  if(renderTask)renderTask.cancel();
  renderTask=page.render({canvasContext:c.getContext('2d'),viewport:vp});
  try{await renderTask.promise}catch(e){return} renderTask=null;
  $('ov').style.width=vp.width+'px';$('ov').style.height=vp.height+'px';
  drawOverlay();
}
function setMode(m){mode=(mode===m?null:m);$('mCrop').classList.toggle('on',mode==='crop');$('mMask').classList.toggle('on',mode==='mask');
  $('ov').className=mode||'';}
function pageEdits(){const f=edits[curFile]=edits[curFile]||{};return f[cur]=f[cur]||{}}
function clearPage(){delete (edits[curFile]||{})[cur];drawOverlay();markEditBadges()}
function drawOverlay(){
  const ov=$('ov');ov.querySelectorAll('.r').forEach(x=>x.remove());
  const e=(edits[curFile]||{})[cur];if(!e)return;
  const W=ov.clientWidth,H=ov.clientHeight;
  const put=(r,cls)=>{const d=document.createElement('div');d.className='r '+cls;
    d.style.left=r[0]*W+'px';d.style.top=r[1]*H+'px';d.style.width=(r[2]-r[0])*W+'px';d.style.height=(r[3]-r[1])*H+'px';ov.appendChild(d)};
  (e.masks||[]).forEach(m=>put(m,'mask')); if(e.crop)put(e.crop,'crop');
}
(()=>{ // drag-to-draw
  const ov=$('ov');let sx,sy,box=null;
  ov.onmousedown=ev=>{if(!mode)return;const b=ov.getBoundingClientRect();sx=ev.clientX-b.left;sy=ev.clientY-b.top;
    box=document.createElement('div');box.className='r drag';ov.appendChild(box);ev.preventDefault()};
  ov.onmousemove=ev=>{if(!box)return;const b=ov.getBoundingClientRect(),x=ev.clientX-b.left,y=ev.clientY-b.top;
    box.style.left=Math.min(sx,x)+'px';box.style.top=Math.min(sy,y)+'px';
    box.style.width=Math.abs(x-sx)+'px';box.style.height=Math.abs(y-sy)+'px'};
  ov.onmouseup=ev=>{if(!box)return;const b=ov.getBoundingClientRect(),x=ev.clientX-b.left,y=ev.clientY-b.top;
    box.remove();box=null;const W=ov.clientWidth,H=ov.clientHeight;
    const r=[Math.min(sx,x)/W,Math.min(sy,y)/H,Math.max(sx,x)/W,Math.max(sy,y)/H].map(v=>+Math.min(Math.max(v,0),1).toFixed(4));
    if((r[2]-r[0])*W<8||(r[3]-r[1])*H<8)return;
    const e=pageEdits(); if(mode==='crop')e.crop=r; else (e.masks=e.masks||[]).push(r);
    drawOverlay();markEditBadges()};
})();
function parseRange(s){const out=[];for(const part of String(s).split(',')){const t=part.trim();if(!t)continue;
  const m=t.match(/^(\d+)\s*-\s*(\d+)$/);if(m){for(let i=+m[1];i<=+m[2];i++)out.push(i)}else if(/^\d+$/.test(t))out.push(+t);else throw'bad range: '+t}
  if(!out.length)throw'empty range';return out}
function setRangeCur(){$('range').value=String(cur);markSel()}
$('range').addEventListener('input',markSel);
function addStep(){
  if(!curFile)return msg(T('先打开一个文件','open a file first'),'bad');
  let pages;const rv=$('range').value.trim();
  if(!rv){pages=Array.from({length:num},(_,i)=>i+1)}
  else{try{pages=parseRange(rv)}catch(e){return msg(e,'bad')}}
  const bad=pages.filter(p=>p<1||p>num);if(bad.length)return msg(T('页码超范围: ','page(s) out of range: ')+bad,'bad');
  const ed={};for(const p of pages){const e=(edits[curFile]||{})[p];if(e&&(e.crop||(e.masks||[]).length))ed[p]=e}
  steps.push({source:curFile,pages,edits:Object.keys(ed).length?ed:undefined});
  renderSteps();msg('','');
}
let dragI=null;
function dropStep(to){if(dragI===null||dragI===to)return;const[s]=steps.splice(dragI,1);steps.splice(to,0,s);dragI=null;renderSteps()}
function renderSteps(){
  $('steps').innerHTML=steps.map((s,i)=>`<div class="step" draggable="true" style="cursor:grab"
    ondragstart="dragI=${i}" ondragover="event.preventDefault()" ondrop="dropStep(${i})">
    <div><b>${i+1}.</b> ${s.source.split('/')[1]}
    <small>${s.pages.length} ${T('页','p')} (${s.pages[0]}–${s.pages[s.pages.length-1]})${s.edits?' · '+Object.keys(s.edits).length+' '+T('页有编辑','pages edited'):''}</small></div>
    <span class="x" style="cursor:pointer;color:var(--red)" onclick="steps.splice(${i},1);renderSteps()">${IC('x')}</span></div>`).join('');
}
async function save(){
  const name=$('outName').value.trim();
  if(!name)return msg(T('填输出名','enter an output name'),'bad'); if(!steps.length)return msg(T('没有 step','no steps'),'bad');
  $('saveBtn').disabled=true;msg(T('处理中…','Working…'),'');
  try{
    const r=await J('/api/save?'+qs(),{method:'POST',body:JSON.stringify({plan:{output:name.endsWith('.pdf')?name:name+'.pdf',steps}})});
    msg(T(`已保存 raw/${r.output} (${r.pages} 页)`,`Saved raw/${r.output} (${r.pages} pages)`),'good');steps=[];renderSteps();loadFiles();
  }catch(e){msg(e,'bad')}
  $('saveBtn').disabled=false;
}
async function delRaw(f){
  if(!confirm(T('删除 '+f+' ?','Delete '+f+'?')))return;
  try{await J('/api/delete?'+qs(),{method:'POST',body:JSON.stringify({f})});if(curFile===f){curFile=null}loadFiles()}
  catch(e){msg(e,'bad')}
}
async function refreshOps(){try{$('opsBox').textContent=JSON.stringify(await J('/api/ops?'+qs()),null,1)}catch(e){}}
function toggleOps(){const b=$('opsBox');b.style.display=b.style.display==='none'?'block':'none'}
function msg(t,cls){$('msg').textContent=t;$('msg').className=cls}
// ---------------- bank tab
let BANK=[], CART=[], TAX={}, REFS=[];   // REFS: bank entries picked as AI-gen references
let PTYPES=[];                            // problem_types (config-driven), for dropdowns
let USAGE={};                             // qid -> {count,last,titles} (real-teaching log)
let BANK_WARN=[];                         // files the server could not read in full
const PAGE=80;                            // cards materialised per render step
let RENDER_N=PAGE;
const useCount=qid=>((USAGE[qid]||{}).count)||0;
const optLabel=k=>/^\(.*\)$/.test(String(k))?String(k):'('+k+')';   // legacy keys w/o parens
async function loadProblemTypes(){try{PTYPES=(await J('/api/problem_types')).types||[]}catch(e){}}
function typeOpts(cur){return PTYPES.map(t=>`<option value="${t}"${cur===t?' selected':''}>${t}</option>`).join('');}
function isRef(qid){return REFS.some(e=>e.qid===qid);}
function toggleRef(qid){
  const i=REFS.findIndex(e=>e.qid===qid);
  if(i>=0)REFS.splice(i,1); else{const e=BANK.find(x=>x.qid===qid); if(e)REFS.push(e);}
  refreshCard(qid); if(typeof renderGenRefs==='function')renderGenRefs();   // in-place: no filter depends on ref state
}
function clearRefs(){REFS=[];renderBank();renderGenRefs();}
const esc=s=>s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
function renderParts(parts,fs,lvl){ // nested tree -> indented html (recursive)
  return (parts||[]).map(p=>`<div class="part" style="margin-left:${(lvl+1)*1.4}em">`
    +`<b>${esc(p.no||'')}</b> ${rich(p.text,fs)}`
    +(p.marks!=null?` <span class="ph">[${p.marks}]</span>`:'')
    +renderParts(p.children,fs,lvl+1)+`</div>`).join('');
}
function rich(txt,fs){ // text -> html: escape, but pass through tables & image markers
  if(!txt)return '';
  const pat=/(!\[\]\([^)]+\)|<table>[\s\S]*?<\/table>)/g;
  return txt.split(pat).map(seg=>{
    if(!seg)return '';
    const m=seg.match(/^!\[\]\(([^)]+)\)$/);
    if(m)return `<img loading="lazy" onclick="this.classList.toggle('zoom')" src="/api/img?${qs()}&f=${encodeURIComponent(fs+'/'+m[1])}">`;
    if(seg.startsWith('<table>'))return seg;
    if(!/\S/.test(seg))return ' ';   // whitespace-only (e.g. \n between images) -> space, so inline images row-flow
    return esc(seg).replace(/\\cents?\b/g,'¢').replace(/\\_/g,'_').replace(/\n/g,'<br>');
  }).join('')
   .replace(/\[ANSWER\]/g,'<span class="ph">[ANSWER]</span>')
   .replace(/\[QN\]/g,'<span class="ph">[QN]</span>');
}
// Search used to JSON.stringify every entry on every keystroke (≈1k entries × 3 fields)
// and re-render + re-typeset all cards synchronously — that was the "卡顿". Now each entry
// carries a lowercase search blob built once at load, input is debounced, the filter runs
// once per render, and only a page of cards is materialised.
function searchBlob(e){
  const parts=[];
  const walk=ps=>(ps||[]).forEach(p=>{parts.push(p.no||'',p.text||'');walk(p.children)});
  parts.push(e.stem||''); walk(e.parts);
  if(e.options)Object.entries(e.options).forEach(([k,v])=>parts.push(k,String(v)));
  if(e.answer)parts.push(String(e.answer.value??''));
  parts.push(e.qid||'');
  return parts.join(' ').toLowerCase();
}
let SEARCH_T=null;
function searchChanged(){clearTimeout(SEARCH_T);SEARCH_T=setTimeout(()=>{RENDER_N=PAGE;renderBank()},180);}
async function loadBank(){
  const r=await J('/api/bank?'+qs());
  BANK=r.entries; BANK_WARN=r.warnings||[];
  BANK.forEach(e=>{e.flags=e.flags||[];e._s=searchBlob(e)});
  try{USAGE=(await J('/api/usage?'+qs())).usage||{}}catch(e){USAGE={}}
  RENDER_N=PAGE;
  try{TAX=await J('/api/taxonomy?'+qs())}catch(e){TAX={}}
  refreshBankFilters();
  renderBank();
  loadBackups();
}
// Rebuild the bank filter chrome (file list + topic/type dropdowns + legend) in the CURRENT
// language. Called by loadBank AND by applyLang, so a language toggle re-translates the
// T()-built dropdown defaults (which carry no data-en for applyLang to swap). File-checkbox
// selections are preserved across the rebuild.
function refreshBankFilters(keep){
  // keep=true → preserve the current file-checkbox selection (used on a language toggle, same
  // source). Default (source switch / fresh load) → all files checked. Preserving across a
  // source switch is wrong: the old source's checked names don't match the new files, which
  // would leave them all unchecked.
  const prev=new Set([...document.querySelectorAll('#bkFiles .bkf:checked')].map(c=>c.value));
  const chk=f=>(!keep||prev.has(f))?' checked':'';
  const files=[...new Set(BANK.map(e=>e.file_stem))];
  $('bkFiles').innerHTML='<h4>'+T('文件','Files')+'</h4>'
    +`<label class="hint" style="display:block;border-bottom:1px solid var(--line);padding-bottom:3px;margin-bottom:3px"><input type="checkbox" checked onchange="document.querySelectorAll('.bkf').forEach(c=>c.checked=this.checked);renderBank()"> <b>All</b></label>`
    +files.map(f=>
    `<label class="hint" style="display:block"><input type="checkbox" class="bkf" value="${f}"${chk(f)} onchange="renderBank()"> ${f}</label>`).join('');
  // topic dropdown shows id — name; legend lists all used topics
  const topics=[...new Set(BANK.flatMap(e=>(e.tags&&e.tags.topic)||[]))].sort();
  const types=[...new Set(BANK.map(e=>e.tags&&e.tags.type).filter(Boolean))].sort();
  const tv=$('bkTopic').value, yv=$('bkType').value;
  $('bkTopic').innerHTML='<option value="">'+T('全部 topic','all topics')+'</option>'+topics.map(t=>{const nm=(TAX[t]||{}).name||'';return `<option value="${t}">${t}${nm?' — '+nm:''}</option>`;}).join('');
  $('bkType').innerHTML='<option value="">'+T('全部题型','all types')+'</option>'+types.map(t=>`<option>${t}</option>`).join('');
  $('bkTopic').value=tv; $('bkType').value=yv;
  const legend=topics.length?`<details style="margin-top:4px"><summary class="hint">${T('topic 图例','topic legend')} (${topics.length})</summary>`
    +topics.map(t=>`<div class="hint" style="padding:1px 0"><b style="color:var(--acc)">${t}</b> ${esc((TAX[t]||{}).name||'')}</div>`).join('')+`</details>`:'';
  $('bkLegend').innerHTML=legend;
}
async function loadBackups(){
  try{
    const r=await J('/api/backups?'+qs()); const sel=$('bkRestoreSel'); if(!sel)return;
    sel.innerHTML='<option value="">'+T('— 选择备份 —','— pick a backup —')+'</option>'+
      (r.backups||[]).map(b=>`<option value="${esc(b.file)}">${esc(b.file.replace(/\\.zip$/,''))} · ${(b.size/1024).toFixed(0)}KB</option>`).join('');
  }catch(e){}
}
async function backupDb(){
  $('bkupMsg').textContent=T('备份中…','Backing up…');
  try{
    const r=await J('/api/backup_db?'+qs(),{method:'POST',body:'{}'});
    $('bkupMsg').textContent=T('已备份 '+r.file+' ('+r.n+' 文件)','Backed up '+r.file+' ('+r.n+' files)'); loadBackups();
  }catch(e){$('bkupMsg').textContent=T('备份失败: ','Backup failed: ')+e;}
}
async function restoreDb(){
  const f=$('bkRestoreSel').value;
  if(!f){$('bkupMsg').textContent=T('请先选择一个备份','Pick a backup first');return;}
  if(!confirm(T('恢复备份 '+f+' ?\\n将用备份覆盖当前来源的 interim 题库。恢复前会自动再备份一次当前状态, 可回滚。','Restore backup '+f+'?\\nThis overwrites the current source interim bank; the current state is auto-backed up first so you can roll back.')))return;
  $('bkupMsg').textContent=T('恢复中…','Restoring…');
  try{
    const r=await J('/api/restore_db?'+qs(),{method:'POST',body:JSON.stringify({file:f})});
    $('bkupMsg').textContent=T('已恢复 '+r.restored+' 文件','Restored '+r.restored+' files'); CART=[]; await loadBank();
  }catch(e){$('bkupMsg').textContent=T('恢复失败: ','Restore failed: ')+e;}
}
function bankFiltered(){
  const fset=new Set([...document.querySelectorAll('.bkf:checked')].map(c=>c.value));
  const fl=$('bkFlag').value, s=$('bkSearch').value.trim().toLowerCase();
  const topic=$('bkTopic').value, type=$('bkType').value, diff=$('bkDiff').value;
  const pick=$('bkPick')?$('bkPick').value:'';
  const cartQ=new Set(CART.filter(sameSrc).map(it=>it.qid));   // current-source cart qids
  const verified=e=>e.flags.includes('verified');  // explicit human confirmation
  const prob=e=>e.flags.filter(f=>f!=='verified'&&f!=='ai_generated').length;  // problem flags only
  const pickOk=e=>{
    if(pick==='in_cart')return cartQ.has(e.qid);
    if(pick==='not_in_cart')return !cartQ.has(e.qid);
    if(pick==='used')return useCount(e.qid)>0;
    if(pick==='unused')return useCount(e.qid)===0;
    if(pick==='used_multi')return useCount(e.qid)>=2;
    return true;
  };
  return BANK.filter(e=>fset.has(e.file_stem)
    &&(fl!=='flagged'||prob(e))&&(fl!=='clean'||!prob(e))
    &&(fl!=='verified'||verified(e))&&(fl!=='unverified'||!verified(e))
    &&pickOk(e)
    &&(!topic||((e.tags&&e.tags.topic)||[]).includes(topic))
    &&(!type||(e.tags&&e.tags.type)===type)
    &&(!diff||(e.tags&&e.tags.difficulty)===diff)
    &&(!s||(e._s||(e._s=searchBlob(e))).includes(s)));
}
function moreBank(){RENDER_N+=PAGE;renderBank();}
// click a (same-source) cart item → scroll its bank card into view and flash it. If the card
// is paged out but still passes the current filter, render up to it first.
function jumpToCard(qid){
  const box=$('bankList'); if(!box)return;
  let el=box.querySelector('[data-qid="'+qid+'"]');
  if(!el){
    const idx=bankFiltered().findIndex(e=>e.qid===qid);
    if(idx>=0){RENDER_N=Math.max(RENDER_N,idx+PAGE);renderBank();el=box.querySelector('[data-qid="'+qid+'"]');}
  }
  if(!el){toast(T('该题不在当前筛选结果中','not in the current filter'));return;}
  el.scrollIntoView({behavior:'smooth',block:'center'});
  el.classList.remove('flash');void el.offsetWidth;el.classList.add('flash');   // restart the flash
  setTimeout(()=>{if(el)el.classList.remove('flash')},1600);
}
function toast(msg){
  let t=$('toast');
  if(!t){t=document.createElement('div');t.id='toast';document.body.appendChild(t);}
  t.textContent=msg;t.classList.add('on');
  clearTimeout(t._h);t._h=setTimeout(()=>t.classList.remove('on'),1800);
}
// The card header (badges + action buttons) — no LaTeX lives here, so it can be re-rendered
// in place without re-typesetting the body (which is what caused the raw-LaTeX flash).
function cardHeadHtml(e){
  const isVer=e.flags.includes('verified');
  const isAI=e.flags.includes('ai_generated');
  const inCart=inCartE(e);
  const flags=e.flags.filter(f=>f!=='verified'&&f!=='ai_generated').map(f=>`<span class="fbadge ${/^(llm_patched|rescued|answer_section)/.test(f)?'info':''}">${esc(f)}</span>`).join(' ');
  const topicNames=(e.tags&&e.tags.topic||[]).map(t=>{const nm=(TAX[t]||{}).name||'';return t+(nm?' ('+nm+')':'');}).join('; ');
  const tg=e.tags?`<span class="badge" style="background:var(--tag);color:var(--acc)" title="${esc(topicNames)}">${(e.tags.topic||[]).join(',')} · ${e.tags.type||'?'} · ${e.tags.difficulty||'?'}</span>`:'';
  const mk=(e.meta&&e.meta.marks!=null)?`<span class="badge no">${e.meta.marks} ${T('分','marks')}</span>`:'';
  const uc=useCount(e.qid);
  const ub=uc?`<span class="badge" style="background:var(--nobg);color:var(--dim)" title="${T('已用于教学','used in teaching')} ${esc((USAGE[e.qid]||{}).last||'')} · ${esc(((USAGE[e.qid]||{}).titles||[]).join(', '))}">${IC('book')} ${T('已用 '+uc+' 次','used '+uc+'×')}</span>`:'';
  return `<div class="hd-main"><b>${inCart?IC('check')+' ':''}${e.qid}</b>
      ${isAI?`<span class="badge" style="background:var(--tag);color:var(--coral)" title="${T('AI 生成','AI generated')}">${IC('sparkles')}AI</span>`:''}
      ${isVer?`<span class="badge ok" title="${T('已人工验证','human-verified')}">${IC('check')}verified</span>`:''}
      ${ub}
      <span class="badge ${e.cleaned?'ok':'no'}">${e.stage||(e.cleaned?'clean':'raw')}</span>
      <span class="badge no">${e.kind}</span> ${tg} ${mk} ${flags}</div>
      <div class="hd-act">
      <button class="${isRef(e.qid)?'refbtn':''}" title="${T('加入/移出 AI 生成参考','add/remove AI-gen reference')}" onclick="toggleRef('${e.qid}')">${IC('clip')}${isRef(e.qid)?T('参考中','ref'):T('AI 参考','AI ref')}</button>
      <button class="${isVer?'okbtn':''}" title="${T('标记/取消 人工验证','mark/unmark human-verified')}" onclick="toggleVerified('${e.qid}','${e.file_stem}')">${IC('check')}${isVer?T('已验证','verified'):T('验证','verify')}</button>
      <button onclick="openEdit('${e.qid}')">${IC('edit')}${T('编辑','Edit')}</button>
      <button class="${inCart?'danger':''}" onclick="${inCart?'delCartQid':'addCart'}('${e.qid}')">${inCart?IC('x')+T('移除','remove'):IC('plus')+T('选题','add')}</button>
      <button class="danger" title="${T('删除该题 (从所有阶段文件移除)','delete (from all stage files)')}" onclick="delEntry('${e.qid}','${e.file_stem}')">${IC('trash')}</button></div>`;
}
// Update ONE card's header + sel/ver classes in place (no innerHTML rebuild of the list, so
// the already-typeset math in the body is untouched → no re-render flash).
function refreshCard(qid){
  const e=BANK.find(x=>x.qid===qid); if(!e)return;
  const card=$('bankList').querySelector('[data-qid="'+qid+'"]'); if(!card){renderBank();return;}
  card.classList.toggle('sel',inCartE(e));
  card.classList.toggle('ver',e.flags.includes('verified'));
  const hd=card.querySelector('.hd'); if(hd)hd.innerHTML=cardHeadHtml(e);
}
function renderBank(){
  const all=bankFiltered();                    // filter once per render (was twice)
  const rows=all.slice(0,RENDER_N);
  $('bkCount').textContent=T(`${all.length} 条 (显示 ${rows.length})`,`${all.length} total (${rows.length} shown)`);
  // a file that could not be read in full means questions are MISSING from this view —
  // say so loudly rather than quietly showing a short bank
  const wb=$('bkWarn');
  if(wb)wb.innerHTML=BANK_WARN.length
    ? `<div style="color:var(--red);border:1px solid var(--red);border-radius:8px;padding:6px 8px">
       ${IC('warn')} ${T(BANK_WARN.length+' 个文件读取不完整, 题目可能缺失 (iCloud 未下载完?)',BANK_WARN.length+' file(s) read incomplete — questions may be missing (iCloud not fully downloaded?)')}:<br>${BANK_WARN.map(esc).join('<br>')}
       <button style="margin-top:4px" onclick="loadBank()">${T('重新载入','Reload')}</button></div>` : '';
  // build the whole list as ONE string and assign once: `innerHTML +=` (and reading
  // innerHTML back) re-serialises + re-parses every card — it cost ~300ms per keystroke.
  const html=rows.map(e=>{
    const isVer=e.flags.includes('verified'), inCart=inCartE(e);
    const opts=e.options?`<div class="opts">${Object.entries(e.options).map(([k,v])=>`<span class="opt"><b>${esc(optLabel(k))}</b> ${rich(v,e.file_stem)}</span>`).join('')}</div>`:'';
    const parts=renderParts(e.parts,e.file_stem,0);
    const sol=(e.answer||e.solution)?`<details><summary>${T('答案 / 解答','Answer / Solution')}</summary>
        ${e.answer?`<div><b>Ans:</b> ${rich(String(e.answer.value??''),e.file_stem)} <span class="hint">(${e.answer.kind})</span></div>`:''}
        ${e.answer&&e.solution?'<hr>':''}
        ${e.solution?`<div>${rich(e.solution,e.file_stem)}</div>`:''}</details>`:'';
    return `<div class="card ${inCart?'sel':''} ${isVer?'ver':''}" data-qid="${e.qid}"><div class="hd">${cardHeadHtml(e)}</div>
      ${e.stem&&e.stem.trim()?`<div>${rich(e.stem,e.file_stem)}</div>`:''}${opts}${parts}${sol}</div>`;
  }).join('')||`<div class="hint">${T('无匹配条目','No matching entries')}</div>`;
  const more=all.length>rows.length
    ? `<button onclick="moreBank()" style="width:100%;margin-bottom:10px">${T('显示更多','Show more')} (${all.length-rows.length})</button>` : '';
  $('bankList').innerHTML=html+more;
  typesetLater($('bankList'),html);
}
// MathJax.typesetPromise() burns ~150-200ms SYNCHRONOUSLY for a page of cards, which is
// what remained of the search lag. Defer it and keep only the last request: filtering
// stays instant, formulas appear a moment later.
let MJ_T=null, MJ_TOK=0;
function typesetLater(box,html){
  if(!(window.MathJax&&MathJax.typesetPromise)||!/[$\\]/.test(html))return;
  const tok=++MJ_TOK; clearTimeout(MJ_T);
  MJ_T=setTimeout(()=>{if(tok===MJ_TOK)MathJax.typesetPromise([box]).catch(()=>{})},80);
}
// The cart persists as you switch source, so it can mix sources — and qids are NOT unique
// across sources. Each cart item therefore carries its full source coords {s,t,l,src,qid}
// (+ mcq snapshot for sorting/badges when its source is no longer the one on screen).
function curItem(e){return {s:SRC.subject,t:SRC.stage,l:SRC.level,src:SRC.source,qid:e.qid,
  mcq:!!(e.kind==='mcq'||(e.options&&Object.keys(e.options).length))};}
function sameSrc(it){return it.s===SRC.subject&&it.t===SRC.stage&&it.l===SRC.level&&it.src===SRC.source;}
function cartIdxE(e){return CART.findIndex(it=>sameSrc(it)&&it.qid===e.qid);}   // current-source lookup
function inCartE(e){return cartIdxE(e)>=0;}
// a cart change only shifts which cards are shown when the cart/usage filter is by-cart
function cartFilterActive(){const p=$('bkPick')&&$('bkPick').value;return p==='in_cart'||p==='not_in_cart';}
function addCart(qid){const e=BANK.find(x=>x.qid===qid); if(e&&!inCartE(e)){CART.push(curItem(e));renderCart();cartFilterActive()?renderBank():refreshCard(qid);}}
// Random pick: draw from the current filter, preferring questions not yet used in real
// teaching (least-used first, random within a usage tier), then put MCQs at the front —
// worksheets conventionally open with the multiple-choice section.
function sortCartMcqFirst(){
  const idx=new Map(CART.map((it,i)=>[it,i]));
  CART.sort((a,b)=>((b.mcq?1:0)-(a.mcq?1:0))||(idx.get(a)-idx.get(b)));   // stable otherwise
  renderCart();
}
function randPick(){
  const n=Math.max(1,+$('randN').value||1);
  let pool=bankFiltered().filter(e=>!inCartE(e));
  for(let i=pool.length-1;i>0;i--){const j=Math.floor(Math.random()*(i+1));[pool[i],pool[j]]=[pool[j],pool[i]]}
  if($('optFresh')&&$('optFresh').checked)pool.sort((a,b)=>useCount(a.qid)-useCount(b.qid));  // stable within a tier
  CART.push(...pool.slice(0,n).map(curItem));
  if($('optMcqFirst')&&$('optMcqFirst').checked)sortCartMcqFirst();
  renderCart();renderBank();
}
function delCart(i){const it=CART[i];CART.splice(i,1);renderCart();if(cartFilterActive())renderBank();else if(it&&sameSrc(it))refreshCard(it.qid);}
function delCartQid(qid){const i=CART.findIndex(it=>sameSrc(it)&&it.qid===qid);if(i>=0){CART.splice(i,1);renderCart();cartFilterActive()?renderBank():refreshCard(qid);}}
async function toggleVerified(qid,stem){
  try{
    const r=await J('/api/toggle_verified?'+qs(),{method:'POST',body:JSON.stringify({qid,file_stem:stem})});
    const e=BANK.find(x=>x.qid===qid);
    if(e){e.flags=(e.flags||[]).filter(f=>f!=='verified'); if(r.verified)e.flags.push('verified');}
    ($('bkFlag')&&$('bkFlag').value)?renderBank():refreshCard(qid);   // flag filter active → visibility may change
  }catch(e){alert(T('验证失败: ','verify failed: ')+e)}
}
async function delEntry(qid,stem){
  if(!confirm(T('确认删除题目 '+qid+' ?\\n将从所有阶段文件 (tagged/clean/raw) 移除, 不可撤销。','Delete question '+qid+'?\\nRemoved from all stage files (tagged/clean/raw); cannot be undone.')))return;
  try{
    await J('/api/delete_entry?'+qs(),{method:'POST',body:JSON.stringify({qid,file_stem:stem})});
    BANK=BANK.filter(e=>e.qid!==qid);
    const i=CART.findIndex(it=>sameSrc(it)&&it.qid===qid); if(i>=0)CART.splice(i,1);
    renderCart(); renderBank();
  }catch(e){alert(T('删除失败: ','delete failed: ')+e)}
}
function mvCart(i,d){const j=i+d;if(j<0||j>=CART.length)return;[CART[i],CART[j]]=[CART[j],CART[i]];renderCart()}
function renderCart(){
  $('cartN').textContent=CART.length;
  $('cartList').innerHTML=CART.map((it,i)=>{
    const here=sameSrc(it); const uc=here?useCount(it.qid):0;   // USAGE is current-source only
    return `<div class="cartItem"><span class="lbl${here?' jump':''}"${here?` onclick="jumpToCard('${it.qid}')" title="${T('跳转到该题','jump to this question')}"`:''}>${i+1}. ${esc(it.qid)}`
    +(here?'':` <span class="badge no" title="${T('来自','from')} ${esc(it.l)}/${esc(it.src)}">↗${esc(it.src)}</span>`)
    +(it.mcq?' <span class="badge no">mcq</span>':'')
    +(uc?` <span class="badge no" title="${T('已用 '+uc+' 次','used '+uc+'×')}">${IC('book')}${uc}</span>`:'')+`</span>
    <span class="btns"><button onclick="mvCart(${i},-1)">↑</button><button onclick="mvCart(${i},1)">↓</button>
    <button class="danger" onclick="delCart(${i})">${IC('x')}</button></span></div>`;}).join('');
}
function capOpts(){
  return {style:$('capStyle')?$('capStyle').value:'italic',
          fig_word:$('capFigWord')?$('capFigWord').value:'Fig.',
          number:$('capNumber')?$('capNumber').value:'per_question',
          tables:$('capTables')?$('capTables').checked:true};
}
async function exportDocx(){
  if(!CART.length){$('expMsg').textContent=T('购物车为空','Cart is empty');return}
  const log=$('expLog').checked;
  if(log&&!confirm(T('本次导出将记录为「已用于实际教学」('+CART.length+' 题, 使用次数 +1)。确认?','This export will be logged as used in real teaching ('+CART.length+' questions, +1 use count). Confirm?'))){return}
  $('expMsg').textContent=T('导出中…','Exporting…');
  try{
    const r=await J('/api/export?'+qs(),{method:'POST',body:JSON.stringify(
      {items:CART,title:$('expTitle').value,answer_format:$('expAnsFmt').value,
       mcq_label:$('expMcq').value,blank:$('expBlank').value,marks_col:$('expMarks').checked,
       caption:capOpts(),sections:$('expSections').checked,show_total:$('expTotal').checked,
       log_usage:log})});
    const links=(r.files||[r.file]).map(f=>`<a style="color:var(--acc)" href="/api/download?${qs()}&f=${encodeURIComponent(f)}">${esc(f)}</a>`).join(' · ');
    const openBtn=` <button onclick="openFolder('outputs')" title="${T('在文件管理器中打开输出文件夹','open the outputs folder')}" style="padding:2px 9px;vertical-align:middle">${IC('folderopen')}${T('打开文件夹','Open folder')}</button>`;
    $('expMsg').innerHTML=`${T('已导出','Exported')} ${r.n} ${T('题','q')} → ${links}`+openBtn
      +(r.logged?' <span class="badge ok">'+IC('book')+' '+T('已记入使用记录','logged')+'</span>':'');
    if(r.logged)await refreshUsage();
  }catch(e){$('expMsg').textContent='ERROR: '+e}
}
// "used" is a teaching decision, never inferred from an export — recorded only on request.
async function markUsed(){
  if(!CART.length){$('expMsg').textContent=T('购物车为空','Cart is empty');return}
  if(!confirm(T('把选题车里的 '+CART.length+' 题标记为「已用于实际教学」(使用次数 +1)?','Mark the '+CART.length+' questions in the cart as used in real teaching (+1 use count)?')))return;
  try{
    const r=await J('/api/log_usage?'+qs(),{method:'POST',body:JSON.stringify(
      {items:CART,title:$('expTitle').value,kind:'manual'})});
    $('expMsg').textContent=T('已记录 '+r.n+' 题','Recorded '+r.n+' questions');
    await refreshUsage();
  }catch(e){$('expMsg').textContent='ERROR: '+e}
}
async function refreshUsage(){
  try{USAGE=(await J('/api/usage?'+qs())).usage||{}}catch(e){}
  renderCart();renderBank();
  if($('bkUsage')&&$('bkUsage').innerHTML)loadUsage();
}
async function loadUsage(){
  const box=$('bkUsage'); if(!box)return;
  let r={}; try{r=await J('/api/usage?'+qs())}catch(e){box.textContent=T('读取失败','Load failed');return}
  USAGE=r.usage||{};
  const n=Object.keys(USAGE).length, tot=Object.values(USAGE).reduce((a,b)=>a+(b.count||0),0);
  box.innerHTML=`<div style="margin:4px 0">${T('已用','Used')} <b>${n}</b> ${T('题','q')} · ${T('累计','total')} ${tot} ${T('次','×')}</div>`
    +(r.exports||[]).map(e=>`<div style="padding:1px 0">${esc(e.ts)} · ${esc(e.title||T('(无标题)','(untitled)'))} · ${e.n} ${T('题','q')} <span class="badge no">${esc(e.kind||'')}</span></div>`).join('')
    +`<button class="danger" style="width:100%;margin-top:5px" onclick="clearUsage()">${T('清除本来源的使用记录','Clear usage log for this source')}</button>`;
}
async function clearUsage(){
  if(!confirm(T('清除当前来源的全部使用记录 (次数归零)?','Clear all usage records for this source (counts reset to 0)?')))return;
  try{await J('/api/clear_usage?'+qs(),{method:'POST',body:'{}'});await refreshUsage();loadUsage();}
  catch(e){alert(T('清除失败: ','clear failed: ')+e)}
}
async function normalizeBank(){
  if(!confirm(T('把当前来源的选项标号统一为 (1)(2)(3)(4)、子题标号统一为 (a)/(i)?\\n会先自动备份, 可回滚。','Normalize option labels to (1)(2)(3)(4) and part labels to (a)/(i) for this source?\\nAuto-backs up first so you can roll back.')))return;
  $('bkNormMsg').textContent=T('处理中…','Working…');
  try{
    const r=await J('/api/normalize_bank?'+qs(),{method:'POST',body:JSON.stringify({backup:true})});
    $('bkNormMsg').textContent=T('规范化','Normalized')+' '+r.entries+' '+T('条','entries')+' ('+r.files.length+' '+T('文件','files')+')'+(r.backup?' · '+T('备份','backup')+' '+r.backup:'');
    await loadBank(); loadBackups();
  }catch(e){$('bkNormMsg').textContent=T('失败: ','Failed: ')+e}
}
// recovery path for item "编辑丢失": every entry write journals its before/after pair
async function loadJournal(){
  const box=$('bkJournal'); if(!box)return;
  box.textContent=T('载入中…','Loading…');
  let r={}; try{r=await J('/api/edit_journal?'+qs()+'&limit=25')}catch(e){box.textContent=T('读取失败','Load failed');return}
  const rows=r.journal||[];
  box.innerHTML=rows.length?rows.map(j=>`<div style="padding:2px 0;border-bottom:1px solid var(--line)">
    ${esc(j.ts)} · <b>${esc(j.kind)}</b> · ${esc(j.qid)}<br>
    <span style="opacity:.8">${esc(j.stem||'')}</span>
    ${j.has_after?`<button style="font-size:11px" onclick="restoreJournal('${esc(j.qid)}','${esc(j.ts)}')">${T('恢复此版本','Restore this version')}</button>`:''}
    </div>`).join(''):'<div>'+T('暂无记录','no records yet')+'</div>';
}
async function restoreJournal(qid,ts){
  if(!confirm(T('把 '+qid+' 恢复为 '+ts+' 保存的版本?','Restore '+qid+' to the version saved at '+ts+'?')))return;
  try{
    await J('/api/restore_entry?'+qs(),{method:'POST',body:JSON.stringify({qid,ts,which:'after'})});
    await loadBank(); loadJournal();
  }catch(e){alert(T('恢复失败: ','restore failed: ')+e)}
}
// ---------------- entry edit modal
let EDIT=null, lastTA=null;
function flatEditParts(parts,pre=''){let out=[];(parts||[]).forEach(p=>{const path=pre+(p.no||'');out.push({no:path,text:p.text||'',marks:p.marks,answer:p.answer||'',solution:p.solution||'',answer_area:p.answer_area||''});out=out.concat(flatEditParts(p.children,path))});return out;}
let pdfCur='q';
let LIVE_ON=true; try{LIVE_ON=localStorage.getItem('qg_livePrev')!=='0';}catch(e){}
function openEdit(qid){
  const e=BANK.find(x=>x.qid===qid); if(!e)return;
  openEntryEditor(e,false);
}
// manually add a new question — same editor, blank entry, plus topic/difficulty fields
function openNewEntry(){
  // file_stem 'manual' matches where add_entry saves it, so uploaded images land in the right
  // extraction dir (extracted/manual/images/) and resolve when the entry is saved.
  openEntryEditor({qid:null,file_stem:'manual',stem:'',parts:[],options:null,answer:null,solution:'',
    tags:{topic:[],type:'',difficulty:'medium'},meta:{},imgs:[],flags:[]}, true);
}
function openEntryEditor(e,isNew){
  EDIT=JSON.parse(JSON.stringify(e)); EDIT.imgs=EDIT.imgs||[]; EDIT._new=!!isNew;
  $('edTitle').textContent=isNew?T('新建题目','New question'):(T('编辑 ','Edit ')+e.qid);
  $('edMsg').textContent='';
  $('edVerBtn').style.display=isNew?'none':'';      // verify/delete only apply to an existing entry
  $('edDelBtn').style.display=isNew?'none':'';
  renderEditForm(); renderImgs();
  $('editModal').classList.add('on');
  if(isNew){                                        // no source page to reference — live preview only
    $('editRTabs').style.display='none'; $('editPdf').style.display='none';
    $('editPreview').style.display='block'; renderLivePreview();
  }else{
    $('editRTabs').style.display=''; $('editPdf').style.display='';
    updateEdVerBtn();
    pdfCur='q'; $('pdfTabQ').classList.add('on'); $('pdfTabA').classList.remove('on');
    applyLivePrev();                                // preview pane on top (per pref), PDF below
    renderEditPdf(); loadAnswerRef();
  }
  requestAnimationFrame(()=>{autoGrowAll();requestAnimationFrame(autoGrowAll);});
  setTimeout(autoGrowAll,150);
  if(document.fonts&&document.fonts.ready)document.fonts.ready.then(autoGrowAll);
}
function ta(label,val,rows,key){
  val=val||'';
  const est=Math.max(rows,(val.match(/\n/g)||[]).length+1,Math.ceil(val.length/70));
  return `<label>${label}</label><textarea rows="${est}" oninput="lastTA=this;autoGrow(this)" onfocus="lastTA=this" data-k="${key}">${esc(val)}</textarea>`;
}
function autoGrow(el){if(!el)return;el.style.height='0px';el.style.height=(el.scrollHeight+4)+'px';}
function autoGrowAll(){document.querySelectorAll('#editLeft textarea').forEach(autoGrow);}
window.addEventListener('resize',()=>{if($('editModal').classList.contains('on'))autoGrowAll();});
function renderEditForm(){
  const e=EDIT;
  const hr=`<div class="edsep"></div>`;
  // Section order: classification first (marks/type/difficulty/topic), then the stem, what
  // it asks (parts, options), then the answer side kept contiguous — answer / answer area /
  // solution used to be split apart by the tagging fields sitting between them.

  let h=`<label>${T('总分 total marks (可空)','Total marks (optional)')}</label><input id="edTotal" style="width:90px" value="${(e.meta&&e.meta.marks!=null)?e.meta.marks:''}">`;
  const curType=(e.tags&&e.tags.type)||'';
  const legacy=(curType&&!PTYPES.includes(curType))?`<option value="${curType}" selected>${curType} ${T('(旧, 已不在词表)','(legacy, not in vocab)')}</option>`:'';
  h+=`<label>${T('问题类型 type (tag)','Question type (tag)')}</label><select id="edType" style="width:200px">`
    +`<option value=""${curType?'':' selected'}>${T('(未标注)','(untagged)')}</option>`
    +typeOpts(curType)+legacy+`</select>`;
  const curDiff=(e.tags&&e.tags.difficulty)||'medium';
  h+=`<label>${T('难度 difficulty','Difficulty')}</label><select id="edDiff" style="width:160px">`
    +['basic','medium','advance'].map(d=>`<option value="${d}"${d===curDiff?' selected':''}>${d}</option>`).join('')+`</select>`;
  const curTopics=new Set((e.tags&&e.tags.topic)||[]);
  const tids=Object.keys(TAX||{}).sort();
  const trows=tids.length?tids.map(id=>`<label class="chkrow"><input type="checkbox" class="edtopic" value="${id}"${curTopics.has(id)?' checked':''}><span><b>${id}</b> ${esc((TAX[id]||{}).name||'')}</span></label>`).join(''):`<div class="hint" style="padding:6px">${T('无 taxonomy','no taxonomy')}</div>`;
  h+=`<label>${T('主题 topic','Topic')}</label><div id="edTopics" class="chklist" style="max-height:150px">${trows}</div>`;

  h+=hr;
  h+=ta(T('题干 stem (可空)','Stem (optional)'),e.stem,2,'stem');
  h+=hr;
  h+=`<label>${T('子题 parts (标号用完整路径如 (a)、(b)(i); 或 a,i; 或 a/i; 保存时自动嵌套)','Parts (use full labels e.g. (a), (b)(i); a,i; a/i; auto-nested on save)')}</label><div id="edParts"></div>`
    +`<button onclick="addPart()">${IC('plus')}${T('添加子题','add part')}</button>`;

  h+=hr;
  h+=`<label>${T('选项 options (MCQ)','Options (MCQ)')}</label><div id="edOpts"></div><button onclick="addOpt()">${IC('plus')}${T('添加选项','add option')}</button>`;

  h+=hr;                                   // answer side, kept contiguous
  // When the parts carry answers they are the source of truth and this box is regenerated
  // from them on save — say so instead of silently discarding what was typed here.
  const partAns=(function any(ps){return (ps||[]).some(p=>p.answer||any(p.children));})(e.parts);
  h+=`<label>${T('答案 answer','Answer')}</label>`
    +`<textarea id="edAns" rows="1"${partAns?' readonly':''} onfocus="lastTA=this" oninput="lastTA=this;autoGrow(this)">${esc((e.answer&&e.answer.value)||'')}</textarea>`
    +(partAns?`<div class="hint" style="margin-top:2px">${T('由各子题答案自动汇总 — 请在上面的子题里修改','generated from the sub-part answers — edit them above')}</div>`:'');
  h+=`<label>${T('答题区 answer_area (占位符 + 单位/符号)','Answer area (placeholder + unit/symbol)')}</label>`
    +`<textarea id="edArea" rows="1" onfocus="lastTA=this" oninput="lastTA=this;autoGrow(this)" placeholder="[ANSWER] cm^2">${esc(e.answer_area||'')}</textarea>`;
  h+=ta(T('解答 solution','Solution'),e.solution,3,'solution');
  h+=`<div id="edAnsRef"></div>`;

  h+=hr;
  h+=`<label>${T('图片 (marker 用 ![](path) 引用)','Images (reference with ![](path))')}</label><div id="edImgList"></div>`
    +`<input type="file" id="edUpload" accept="image/*" style="position:absolute;width:1px;height:1px;opacity:0;pointer-events:none" onchange="uploadImg(this,null)">`
    +`<label for="edUpload" class="btnlike">${IC('upload')}${T('上传新图','upload image')}</label>`;
  $('editLeft').innerHTML=h;
  renderEditParts(); renderEditOpts();
}
function renderImgs(){
  const box=$('edImgList'); if(!box)return;
  box.innerHTML=(EDIT.imgs||[]).map((a,i)=>`<div class="epRow" style="align-items:center;margin-bottom:6px">
    <img src="/api/img?${qs()}&f=${encodeURIComponent(EDIT.file_stem+(a.src==='ans'?'_ans':'')+'/'+a.path)}" style="width:70px;flex:0 0 70px;max-height:56px;object-fit:contain;background:#fff;border:1px solid var(--line);border-radius:4px">
    <div style="width:150px;flex:0 0 150px;font-size:11px;color:var(--dim);overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${esc(a.path)}">${esc(a.path.split('/').pop())}<br><span class="badge no">${a.src||'q'}</span></div>
    <div class="insBar" style="flex:1;justify-content:flex-end">
      <button onclick="locateImg(${i})">${T('定位','Locate')}</button>
      <button onclick="insTok('![](${a.path})')">${T('插入','Insert')}</button>
      <input type="file" accept="image/*" style="position:absolute;width:1px;height:1px;opacity:0;pointer-events:none" onchange="uploadImg(this,${i})" id="rep${i}">
      <label for="rep${i}" class="btnlike">${T('替换','Replace')}</label>
      <button class="danger" onclick="delImg(${i})">${T('删除','Delete')}</button></div></div>`).join('')
    ||'<div class="hint">'+T('无图片','no images')+'</div>';
}
function locateImg(i){
  const marker='![]('+EDIT.imgs[i].path+')';
  for(const el of $('editLeft').querySelectorAll('textarea')){
    const p=el.value.indexOf(marker);
    if(p>=0){el.focus();el.selectionStart=p;el.selectionEnd=p+marker.length;el.scrollTop=0;autoGrow(el);
      el.style.outline='2px solid var(--acc)';setTimeout(()=>el.style.outline='',1200);return;}
  }
  $('edMsg').textContent=T('题目文本中未找到该图 marker (点"插入"放入)','Image marker not found in the text (use “insert”)');
}
function delImg(i){
  const marker='![]('+EDIT.imgs[i].path+')';
  $('editLeft').querySelectorAll('textarea').forEach(el=>{el.value=el.value.split(marker).join('');autoGrow(el);});
  EDIT.imgs.splice(i,1); renderImgs();
}
async function uploadImg(input,replaceIdx){
  const file=input.files[0]; if(!file)return;
  const b64=await new Promise(r=>{const fr=new FileReader();fr.onload=()=>r(fr.result);fr.readAsDataURL(file);});
  const ext=(file.name.split('.').pop()||'jpg');
  const body={file_stem:EDIT.file_stem,data:b64,ext,src:replaceIdx!=null?(EDIT.imgs[replaceIdx].src||'q'):'q'};
  if(replaceIdx!=null)body.path=EDIT.imgs[replaceIdx].path;
  try{
    const r=await J('/api/upload_img?'+qs(),{method:'POST',body:JSON.stringify(body)});
    if(replaceIdx==null){
      EDIT.imgs.push({kind:'image',path:r.path,src:'q'});
      // insert into the last-focused field, or fall back to the stem so the marker always lands
      const ta=(lastTA&&$('editLeft').contains(lastTA))?lastTA:$('editLeft').querySelector('[data-k=stem]');
      if(ta){lastTA=ta; editInsert(ta,'![]('+r.path+')');}
    }
    renderImgs();$('edMsg').textContent=replaceIdx!=null?T('已替换','replaced'):T('已上传并插入 marker','uploaded & inserted marker');
  }catch(e){$('edMsg').textContent=T('上传失败: ','Upload failed: ')+e;}
  input.value='';
}
function pdfTab(t){pdfCur=t;
  $('pdfTabQ').classList.toggle('on',t==='q');$('pdfTabA').classList.toggle('on',t==='ans');
  renderEditPdf();}
// live preview is a TOGGLE: on = split right pane (preview top, PDF bottom); off = PDF only
function applyLivePrev(){
  $('livePrevToggle').classList.toggle('on',LIVE_ON);
  $('editPreview').style.display=LIVE_ON?'block':'none';
  if(LIVE_ON)renderLivePreview();
}
function toggleLivePrev(){
  LIVE_ON=!LIVE_ON; try{localStorage.setItem('qg_livePrev',LIVE_ON?'1':'0')}catch(e){}
  applyLivePrev();
}
function renderEditParts(){
  const flat=EDIT._pf||flatEditParts(EDIT.parts); EDIT._pf=null;
  // label | [ text + answer fields ] | marks | move | delete — the text and the answer
  // fields share one column, so they line up without hard-coding the controls' width.
  $('edParts').innerHTML=flat.map((p,i)=>`<div class="epRow">
    <input class="no" value="${esc(p.no)}" data-f="no">
    <div class="epMain">
      <textarea rows="1" data-f="text" onfocus="lastTA=this" oninput="lastTA=this;autoGrow(this)">${esc(p.text)}</textarea>
      <div class="epAns">
        <label>${T('答案','Answer')}</label><textarea rows="1" data-f="answer" onfocus="lastTA=this" oninput="lastTA=this;autoGrow(this)" placeholder="${T('该子题答案','answer for this part')}">${esc(p.answer||'')}</textarea>
        <label>${T('答题区','Answer area')}</label><textarea rows="1" data-f="answer_area" onfocus="lastTA=this" oninput="lastTA=this;autoGrow(this)" placeholder="[ANSWER] cm^2">${esc(p.answer_area||'')}</textarea>
        <label>${T('解答','Solution')}</label><textarea rows="1" data-f="solution" onfocus="lastTA=this" oninput="lastTA=this;autoGrow(this)" placeholder="${T('该子题解答','working for this part')}">${esc(p.solution||'')}</textarea>
      </div>
    </div>
    <input class="no" style="width:44px" title="${T('分值','marks')}" placeholder="${T('分','m')}" value="${p.marks!=null?p.marks:''}" data-f="marks">
    <span style="display:flex;flex-direction:column"><button onclick="movePart(${i},-1)" style="padding:0 5px">↑</button><button onclick="movePart(${i},1)" style="padding:0 5px">↓</button></span>
    <button class="danger" onclick="delPart(${i})">${IC('x')}</button></div>`)
    .join('<div class="edsep sub"></div>');
  $('edParts').querySelectorAll('textarea').forEach(autoGrow);
}
function collectParts(){
  const rows=[...$('edParts').querySelectorAll('.epRow')];
  const v=(r,f)=>{const el=r.querySelector(`[data-f=${f}]`);return el?el.value.trim():'';};
  return rows.map(r=>({no:v(r,'no'),
    text:r.querySelector('[data-f=text]').value,
    marks:v(r,'marks'),answer:v(r,'answer'),solution:v(r,'solution'),answer_area:v(r,'answer_area')}));
}
function addPart(){EDIT._pf=collectParts().concat([{no:'',text:'',marks:'',answer:'',solution:'',answer_area:''}]);renderEditParts();}
function delPart(i){const f=collectParts();f.splice(i,1);EDIT._pf=f;renderEditParts();}
function movePart(i,d){const f=collectParts();const j=i+d;if(j<0||j>=f.length)return;[f[i],f[j]]=[f[j],f[i]];EDIT._pf=f;renderEditParts();}
// Option labels are stored canonically as "(1)".."(n)" (the bank's one internal format);
// the editor shows and writes exactly that, and a bare "2"/"B" typed by hand is wrapped
// on collect. Letter/number display styles are an export choice, not stored data.
function renderEditOpts(){
  if(!$('edOpts'))return;
  const o=EDIT.options||{};
  $('edOpts').innerHTML=Object.entries(o).map(([k,v])=>`<div class="epRow">
    <input class="no" value="${esc(optLabel(k))}" data-ok="1" style="width:56px" title="${T('选项标号 (内部统一 (n) 格式)','option label (stored internally as (n))')}">
    <textarea rows="1" style="flex:1" data-ov="1" onfocus="lastTA=this" oninput="lastTA=this">${esc(v)}</textarea>
    <button class="danger" onclick="this.parentNode.remove()">${IC('x')}</button></div>`).join('');
}
function addOpt(){const d=document.createElement('div');d.className='epRow';
  const next='('+(($('edOpts').querySelectorAll('.epRow').length)+1)+')';
  d.innerHTML=`<input class="no" data-ok="1" style="width:56px" value="${next}"><textarea rows="1" style="flex:1" data-ov="1" onfocus="lastTA=this"></textarea><button class="danger" onclick="this.parentNode.remove()">${IC('x')}</button>`;
  $('edOpts').appendChild(d);}
function collectOpts(){
  if(!$('edOpts'))return null;
  const out={};[...$('edOpts').querySelectorAll('.epRow')].forEach(r=>{
    const k=r.querySelector('[data-ok]').value.trim();const v=r.querySelector('[data-ov]').value;
    if(k)out[optLabel(k)]=v;});
  return Object.keys(out).length?out:null;
}
// insert/replace-selection through execCommand('insertText') so the browser keeps the
// native undo stack (Cmd/Ctrl+Z works). Falls back to a direct value-set if unsupported.
function editInsert(el,text){
  el.focus();
  let ok=false; try{ok=document.execCommand('insertText',false,text);}catch(e){}
  if(!ok){const s=el.selectionStart??el.value.length,e=el.selectionEnd??s;
    el.value=el.value.slice(0,s)+text+el.value.slice(e);
    el.selectionStart=el.selectionEnd=s+text.length;}
  autoGrow(el); scheduleLive();
  return ok;
}
function insTok(tok){
  const el=lastTA; if(!el){$('edMsg').textContent=T('先点一个文本框','click a text box first');return;}
  editInsert(el,tok);
}
// ---- AI assist: select text in a field → prompt → LLM → copy / replace / insert ----
let AI_TA=null, AI_SEL={start:0,end:0};
function openAiAssist(){
  const el=lastTA;
  if(!el){$('edMsg').textContent=T('先点一个文本框(可选中一段文本)','click a text box first (optionally select some text)');return;}
  AI_TA=el; AI_SEL={start:el.selectionStart??0, end:el.selectionEnd??(el.value||'').length};
  $('aiSel').value=(el.value||'').slice(AI_SEL.start,AI_SEL.end);
  $('aiPrompt').value=''; $('aiOut').value=''; $('aiOutWrap').style.display='none'; $('aiMsg').textContent='';
  $('aiModal').classList.add('on'); setTimeout(()=>$('aiPrompt').focus(),30);
}
function closeAi(){$('aiModal').classList.remove('on');}
async function aiRun(){
  const prompt=$('aiPrompt').value.trim();
  if(!prompt){$('aiMsg').textContent=T('请输入指令','enter an instruction');return;}
  const btn=$('aiRunBtn'); btn.disabled=true; $('aiMsg').textContent=T('AI 处理中…','thinking…');
  try{
    const r=await J('/api/ai_assist',{method:'POST',body:JSON.stringify({text:$('aiSel').value,prompt})});
    $('aiOut').value=r.output||''; $('aiOutWrap').style.display='block'; $('aiMsg').textContent=''; autoGrow($('aiOut'));
  }catch(e){$('aiMsg').textContent=T('失败: ','failed: ')+e;}
  finally{btn.disabled=false;}
}
function aiCopy(){
  const t=$('aiOut').value;
  if(navigator.clipboard)navigator.clipboard.writeText(t).then(()=>$('aiMsg').textContent=T('已复制','copied'),()=>{});
  else{$('aiOut').select();document.execCommand('copy');$('aiMsg').textContent=T('已复制','copied');}
}
function aiApply(mode){
  const out=$('aiOut').value; if(!AI_TA)return;
  AI_TA.focus();
  if(mode==='replace'){AI_TA.selectionStart=AI_SEL.start;AI_TA.selectionEnd=AI_SEL.end;}
  else{AI_TA.selectionStart=AI_TA.selectionEnd=AI_SEL.end;}   // insert right after the selection
  editInsert(AI_TA,out);                                      // undo-safe, updates live preview
  closeAi();
}
// quick HTML table (internal format keeps <table> as HTML) inserted at the cursor
function insertTable(){
  const R=Math.max(1,Math.min(20,+$('tblR').value||2)), C=Math.max(1,Math.min(12,+$('tblC').value||2));
  let rows='';
  for(let r=0;r<R;r++){let c='';for(let j=0;j<C;j++)c+='<td> </td>';rows+='<tr>'+c+'</tr>\n';}
  insTok('<table>\n'+rows+'</table>');
}
// B/I/U — wrap the selection in latex emphasis inside $...$ so it renders in preview & docx.
// Works best on plain-text words; a selection already containing $ is wrapped without adding
// outer $ (so existing math isn't broken).
const FMT_CMD={bold:'\\textbf',italic:'\\textit',underline:'\\underline'};
function fmtWrap(kind){
  const el=lastTA; if(!el){$('edMsg').textContent=T('先选中文本框里的文字','select text in a box first');return;}
  const cmd=FMT_CMD[kind]||'\\textbf';
  const s=el.selectionStart??el.value.length, e=el.selectionEnd??s;
  const sel=el.value.slice(s,e);
  const hasMath=sel.indexOf('$')>=0;
  const pre=(hasMath?'':'$')+cmd+'{', post='}'+(hasMath?'':'$');
  const body=sel||'…';
  el.focus(); el.setSelectionRange(s,e);
  editInsert(el,pre+body+post);                                 // undo-preserving
  el.setSelectionRange(s+pre.length,s+pre.length+body.length);  // reselect content
}
// ---- LIVE preview: right pane renders the CURRENT form state and refreshes as you type
function editFormState(){
  if(!$('editLeft').querySelector('[data-k=stem]'))return null;
  const g=k=>{const el=$('editLeft').querySelector(`[data-k=${k}]`);return el?el.value:'';};
  return {stem:g('stem'),solution:g('solution'),parts:collectParts(),options:collectOpts(),
    answer:$('edAns')?$('edAns').value:'',marks:$('edTotal')?$('edTotal').value.trim():''};
}
function renderLivePreview(){
  const box=$('editPreview'); if(!box||!EDIT||!LIVE_ON)return;
  const st=editFormState(), fs=EDIT.file_stem;
  if(!st){box.innerHTML='';return;}
  const optsHtml=st.options&&Object.keys(st.options).length?`<div class="opts">${Object.entries(st.options).map(([k,v])=>`<span class="opt"><b>${esc(optLabel(k))}</b> ${rich(v,fs)}</span>`).join('')}</div>`:'';
  const partsHtml=st.parts.filter(p=>(p.text||'').trim()||(p.no||'').trim()).map(p=>`<div class="part" style="margin-left:1.2em"><b>${esc(p.no||'')}</b> ${rich(p.text||'',fs)}${p.marks?` <span class="ph">[${esc(String(p.marks))}]</span>`:''}</div>`).join('');
  box.innerHTML=`<div class="card ver">
    ${st.stem&&st.stem.trim()?`<div>${rich(st.stem,fs)}</div>`:`<div class="hint">${T('(空题干)','(empty stem)')}</div>`}
    ${optsHtml}${partsHtml}
    ${(st.answer||st.solution)?`<hr>${st.answer?`<div><b>Ans:</b> ${rich(String(st.answer),fs)}</div>`:''}${st.solution?`<div>${rich(st.solution,fs)}</div>`:''}`:''}
    ${st.marks?`<div class="hint" style="margin-top:4px">${T('总分','Total')}: ${esc(st.marks)}</div>`:''}
    </div>`;
  if(window.MathJax&&MathJax.typesetPromise)MathJax.typesetPromise([box]).catch(()=>{});
}
let LIVE_T=null;
function scheduleLive(){if(!LIVE_ON)return;clearTimeout(LIVE_T);LIVE_T=setTimeout(renderLivePreview,180);}
// live-update as the user types in the edit form (delegated once; #editLeft element persists)
$('editLeft').addEventListener('input',()=>{if(LIVE_ON)scheduleLive();});
// read-only reference: answers the LLM parsed from the _ans PDF (segment sidecar).
// surfaces content the qno-merge could not attach, so a human can copy it in by hand.
let ANSREF=[];
async function loadAnswerRef(){
  const box=$('edAnsRef'); if(!box)return; box.innerHTML='';
  const stem=EDIT.file_stem, qno=String((EDIT.meta&&EDIT.meta.qno)||''), sec=(EDIT.meta&&EDIT.meta.section)||'';
  try{ANSREF=await J('/api/answer_ref?'+qs()+'&stem='+encodeURIComponent(stem));}catch(e){ANSREF=[];}
  if(!Array.isArray(ANSREF))ANSREF=[];                    // legacy dict sidecar -> ignore
  if(!ANSREF.length){box.innerHTML='<div class="hint" style="margin-top:6px">'+IC('file')+' '+T('无解答 PDF 提取参考','no answer-PDF extraction reference')+'</div>';return;}
  const norm=s=>String(s||'').replace(/\\W+/g,'').toLowerCase();
  // default: (section,qno) exact -> same qno -> first record
  let sel=sec?ANSREF.findIndex(r=>String(r.qno)===qno&&norm(r.section)===norm(sec)):-1;
  if(sel<0)sel=ANSREF.findIndex(r=>String(r.qno)===qno);
  const has=sel>=0; if(sel<0)sel=0;
  const opts=ANSREF.map((r,i)=>{const lbl=(r.section?esc(r.section)+' · ':'')+T('题','Q')+' '+esc(r.qno);
    return `<option value="${i}"${i===sel?' selected':''}>${lbl}${String(r.qno)===qno?T(' (本题)',' (this)'):''}</option>`;}).join('');
  box.innerHTML=`<div style="margin-top:8px;border:1px solid var(--line);border-radius:6px;padding:6px 8px">
    <div style="font-size:12px;opacity:.85">${IC('file')} ${T('解答 PDF 提取 · 只读参考','answer-PDF extraction · read-only')}
      <select id="ansRefSel" onchange="renderAnsRef(this.value)" style="margin-left:6px">${opts}</select></div>
    ${has?'':`<div class="hint" style="color:var(--coral);margin-top:3px">${T('本题号 ('+esc(qno)+') 无提取记录 — 可能编号不匹配, 请从上方切换查找','No extraction record for ('+esc(qno)+') — the number may not match; switch above to search')}</div>`}
    <div id="ansRefBody"></div></div>`;
  renderAnsRef(sel);
}
function renderAnsRef(k){
  const r=ANSREF[+k]||{}, body=$('ansRefBody'); if(!body)return;
  const ro=(lbl,val)=>`<div class="hint" style="margin-top:4px">${lbl}</div>`+
    `<textarea readonly onclick="this.select()" style="width:100%;background:rgba(127,127,127,.08)">${esc(val||'')}</textarea>`;
  let h=ro('answer', r.answer||T('(无)','(none)'))+ro('solution', r.solution||T('(无)','(none)'));
  if((r.figs||[]).length) h+='<div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:4px">'+
    r.figs.map(p=>`<img src="/api/img?${qs()}&f=${encodeURIComponent(EDIT.file_stem+'_ans/'+p)}" `+
    `style="max-height:72px;border:1px solid var(--line);border-radius:4px;background:#fff">`).join('')+'</div>';
  body.innerHTML=h;
  body.querySelectorAll('textarea').forEach(autoGrow);   // full height (wrapped long lines)
}
async function renderEditPdf(){
  const box=$('editPdf');
  const e=EDIT;
  box.innerHTML=`<div class="hint">${T('加载 PDF…','loading PDF…')}</div>`;
  const stem=e.file_stem;
  const f= pdfCur==='ans' ? ('raw/'+stem+'_ans.pdf') : ((e.meta&&e.meta.file)||('raw/'+stem+'.pdf'));
  try{
    const doc=await pdfjsLib.getDocument({url:'/api/pdf?'+qs()+'&f='+encodeURIComponent(f)}).promise;
    let show;
    if(pdfCur==='q'){
      const pages=(e.meta&&e.meta.pages||[]).map(p=>p+1).filter(p=>p>=1&&p<=doc.numPages);
      show=pages.length?pages:[1];
    }else{
      // answer file: per-question page mapping is unreliable (segment merged the key),
      // so show ALL answer pages — predictable, user scrolls to the relevant one.
      show=Array.from({length:doc.numPages},(_,i)=>i+1);
    }
    box.innerHTML='';
    for(const pn of show){
      const page=await doc.getPage(pn),vp=page.getViewport({scale:1.4});
      const c=document.createElement('canvas');c.width=vp.width;c.height=vp.height;
      box.appendChild(c);
      await page.render({canvasContext:c.getContext('2d'),viewport:vp}).promise;
    }
  }catch(err){box.innerHTML='<div class="bad">'+T('PDF 加载失败','PDF load failed')+' ('+f+'): '+err+'</div>';}
}
async function saveEdit(){
  const g=k=>{const el=$('editLeft').querySelector(`[data-k=${k}]`);return el?el.value:'';};
  const stripMk=t=>t.replace(/\s*\[\s*\d{1,2}\s*\]\s*$/,'');
  // marks: fold the per-part marks input back into text as [N] (pipeline re-extracts to field)
  const parts=collectParts().filter(p=>p.text.trim()||p.no.trim()).map(p=>{
    let t=stripMk(p.text); if(p.marks) t=t.replace(/\s+$/,'')+' ['+p.marks+']';
    return {no:p.no,text:t,answer:p.answer,solution:p.solution,answer_area:p.answer_area};
  });
  let stem=g('stem').replace(/\[\s*(?:total|T)\b[^\]]*\]/i,'').trim();
  const total=$('edTotal').value.trim(); if(total) stem=stem+'\n[Total: '+total+']';
  const topics=[...document.querySelectorAll('.edtopic:checked')].map(c=>c.value);
  const diff=$('edDiff')?$('edDiff').value:'medium';
  const entry={stem,solution:g('solution'),answer:$('edAns').value,
    answer_area:$('edArea')?$('edArea').value:'',
    parts,options:collectOpts(),imgs:EDIT.imgs,type:$('edType').value,topic:topics,difficulty:diff};
  $('edMsg').textContent=T('保存中…','Saving…');
  try{
    if(EDIT._new){                               // manual add → create a new bank entry
      const r=await J('/api/add_entry?'+qs(),{method:'POST',body:JSON.stringify({entry})});
      if(r.entry){r.entry._s=searchBlob(r.entry); BANK.push(r.entry);}
      $('edMsg').textContent=T('已新建 ','Created ')+r.qid;
      renderBank(); closeEdit(); return;
    }
    // `rev` = the revision this edit started from; the server refuses the write if the
    // entry changed on disk meanwhile (pipeline re-run / another tab) instead of
    // silently overwriting it. The response carries the saved entry, so the bank copy
    // is patched in place — no full re-fetch of ~1k entries per save.
    const r=await J('/api/edit_entry?'+qs(),{method:'POST',body:JSON.stringify(
      {qid:EDIT.qid,file_stem:EDIT.file_stem,rev:EDIT._rev,entry})});
    const i=BANK.findIndex(x=>x.qid===EDIT.qid);
    if(r.entry){r.entry._s=searchBlob(r.entry); if(i>=0)BANK[i]=r.entry; else BANK.push(r.entry);}
    $('edMsg').textContent=T('已保存 ('+(r.files||[]).length+' 个阶段文件)','Saved ('+(r.files||[]).length+' stage files)');
    renderBank(); renderCart(); closeEdit();
  }catch(e){
    $('edMsg').textContent=T('保存失败: ','Save failed: ')+e;
    if(String(e).indexOf('起点不一致')>=0&&confirm(T('磁盘上的版本比本次编辑更新, 未保存。\\n重新载入题库并放弃本次编辑?','The version on disk is newer than this edit and unsaved.\\nReload the bank and discard this edit?'))){
      await loadBank(); closeEdit();
    }
  }
}
async function delEntryFromEdit(){if(!EDIT)return;const qid=EDIT.qid,stem=EDIT.file_stem;await delEntry(qid,stem);if(!BANK.some(e=>e.qid===qid))closeEdit();}
function updateEdVerBtn(){const b=$('edVerBtn');if(!b||!EDIT)return;const v=(EDIT.flags||[]).includes('verified');const s=b.querySelector('span');if(s){s.textContent=v?T('已验证','verified'):T('验证','Verify');}b.classList.toggle('okbtn',v);}
async function verifyFromEdit(){if(!EDIT)return;await toggleVerified(EDIT.qid,EDIT.file_stem);const e=BANK.find(x=>x.qid===EDIT.qid);if(e)EDIT.flags=(e.flags||[]).slice();updateEdVerBtn();}
function closeEdit(){$('editModal').classList.remove('on');EDIT=null;}
// Ctrl/⌘ + B / I / U inside an edit text box → latex emphasis on the selection
document.addEventListener('keydown',e=>{
  if(!$('editModal').classList.contains('on')||!(e.ctrlKey||e.metaKey)||e.altKey)return;
  if(e.target.tagName!=='TEXTAREA')return;
  const k={b:'bold',i:'italic',u:'underline'}[e.key.toLowerCase()];
  if(k){e.preventDefault();lastTA=e.target;fmtWrap(k);}
});
document.addEventListener('keydown',e=>{
  if(e.key==='Escape'&&$('editModal').classList.contains('on'))closeEdit();
  if(e.target.tagName==='INPUT'||e.target.tagName==='TEXTAREA')return;
  if(e.key==='ArrowLeft')goto(cur-1); if(e.key==='ArrowRight')goto(cur+1);
  if(e.key==='c')setMode('crop'); if(e.key==='m')setMode('mask');});
init();
