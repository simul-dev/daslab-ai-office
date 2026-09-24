'use strict';
const $ = (selector, root = document) => root.querySelector(selector);
const state = { health: null, tasks: [], selected: null, detail: null, busy: false, poll: null, detailRequest: 0, reviewDrafts: new Map() };
const statuses = { queued: '대기', running: '실행 중', review: '검토 필요', completed: '완료', failed: '실패', cancelled: '취소' };
const priorities = { high: '높음', normal: '보통', low: '낮음' };
const labels = { source:'출처', created_at:'기록 시각', updated_at:'변경 시각', project_id:'프로젝트', verification_status:'검증 상태', status:'상태', passed:'통과 여부', checks:'검증 항목', evidence:'검증 근거', name:'항목', message:'설명', reason:'이유', goal:'목표', title:'업무명', inputs:'입력 자료', acceptance_criteria:'완료 기준', assignee:'실행 담당', reviewer:'검증 담당', scope:'작업 범위', constraints:'제약 조건', deliverables:'산출물', objective:'목표', priority:'우선순위', assigned_to:'실행 담당', criteria:'완료 기준', summary:'요약', details:'상세', check:'검증 항목', actual:'실제 값', expected:'기대 값', path:'경로', sha256:'SHA-256', plan:'작업 계획', verification_plan:'검증 계획', planner:'PM', worker:'실행 담당', result:'결과', notes:'참고', note:'검토 의견', decision:'검토 결정' };
function el(tag, className, text) { const node = document.createElement(tag); if (className) node.className = className; if (text !== undefined) node.textContent = String(text); return node; }
function date(value) { if (!value) return '—'; const d = new Date(value); return Number.isNaN(d.getTime()) ? String(value) : d.toLocaleString('ko-KR', {month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}); }
function project(id) { return (state.health?.projects || []).find(item => item.id === id)?.name || id || '분류 없음'; }
function badge(status) { return el('span', `badge ${Object.hasOwn(statuses,status) ? status : ''}`, statuses[status] || status || '확인 중'); }
function errorMessage(error) { return error?.message || '요청을 처리하지 못했습니다.'; }
async function api(path, data) { const options = {headers:{'X-DAS-Office':'1'}}; if (data !== undefined) { options.method='POST'; options.headers['Content-Type']='application/json'; options.body=JSON.stringify(data); } const response = await fetch(path, options); let result; try { result=await response.json(); } catch { throw new Error('서버 응답을 읽지 못했습니다. 서버 실행 상태를 확인해 주세요.'); } if (!response.ok) throw new Error(result.error || `요청 실패 (${response.status})`); return result; }
function showError(error, target = $('#global-error')) { target.textContent=errorMessage(error); target.hidden=false; }
function clearError() { $('#global-error').hidden=true; }
let toastTimer;
function toast(message) { $('#toast').textContent=message; $('#toast').hidden=false; clearTimeout(toastTimer); toastTimer=setTimeout(()=>{$('#toast').hidden=true;},4000); }
function renderHealth() {
  const h=state.health; if (!h) return;
  const w=h.worker || {}, providers=h.providers || {};
  const actual=w.available && w.provider === 'codex';
  $('#worker-dot').className=`dot ${w.available ? '' : 'offline'}`;
  $('#worker-title').textContent=w.available ? (actual ? 'Codex 실제 작업자 연결' : `${w.provider || '작업자'} 연결`) : '실제 AI 실행 연결 필요';
  $('#worker-auth').textContent=[w.auth_mode,w.version].filter(Boolean).join(' · ');
  const l=h.limits||{}, u=h.usage||{};
  $('#worker-limits').textContent=`동시 실행 ${u.active_runs ?? 0}/${l.concurrency ?? 1} · 오늘 실행 ${u.runs_today ?? 0}/${l.daily_runs ?? '—'}회 · 업무당 ${l.max_attempts ?? '—'}회 · 제한 ${l.timeout_seconds ?? '—'}초`;
  const planner=providers.planner_provider==='code' ? '코드 기반 Planner' : `${providers.planner_provider || '미연결'} Planner`;
  const reviewer=providers.reviewer_provider==='code' ? '코드 기반 Reviewer' : `${providers.reviewer_provider || '미연결'} Reviewer`;
  const worker=actual ? '실제 Codex Worker' : `${w.provider || 'AI'} Worker · 연결 필요`;
  $('#worker-message').textContent=`${planner} → ${worker} → ${reviewer} · ${w.message || ''}`;
  const select=$('[name="project_id"]'), previous=select.value;
  select.replaceChildren(...(h.projects||[]).map(p=>{const o=el('option','',p.name);o.value=p.id;return o;}));
  if (previous) select.value=previous;
}
function renderTasks() { const tasks=state.tasks; for (const status of ['running','review','completed']) $(`#count-${status}`).textContent=tasks.filter(t=>t.status===status).length; $('#count-all').textContent=tasks.length; const term=$('#search').value.trim().toLocaleLowerCase(); const filter=$('#status-filter').value; const filtered=tasks.filter(t=>(!filter || t.status===filter) && (!term || `${t.title} ${t.goal} ${project(t.project_id)}`.toLocaleLowerCase().includes(term))); $('#list-count').textContent=filtered.length; const list=$('#task-list'); list.replaceChildren(); if (!filtered.length) {list.append(el('div','empty-list',tasks.length ? '조건에 맞는 업무가 없습니다.' : '아직 등록된 업무가 없습니다.\n첫 업무를 등록해 주세요.'));return;} for (const task of filtered) { const card=el('button',`task-card ${task.id===state.selected?'selected':''}`); card.type='button'; card.setAttribute('aria-pressed',String(task.id===state.selected)); const top=el('div','task-card-top'); top.append(badge(task.status),el('span',`priority ${task.priority}`,`${priorities[task.priority]||task.priority} 우선순위`)); const bottom=el('div','task-card-bottom'); bottom.append(el('span','project-name',project(task.project_id)),el('time','',date(task.created_at))); card.append(top,el('h3','',task.title),bottom); card.addEventListener('click',()=>selectTask(task.id)); list.append(card); } }
function section(title, right) {const wrapper=el('section','detail-section');const heading=el('div','section-heading');heading.append(el('h3','',title));if(right)heading.append(el('small','',right));wrapper.append(heading);return wrapper;}
function valueNode(value, depth=0) {if(value===null || value===undefined)return el('span','muted','—');if(typeof value==='boolean')return el('span','',value?'통과':'미통과');if(Array.isArray(value)){const list=el('ul','structured-value');for(const item of value){const li=el('li');li.append(valueNode(item,depth+1));list.append(li);}return list;}if(typeof value==='object'){if(depth>5)return el('span','structured-value',JSON.stringify(value,null,2));const list=el('dl','object-list');for(const [key,item]of Object.entries(value)){list.append(el('dt','',labels[key]||key));const dd=el('dd');dd.append(valueNode(item,depth+1));list.append(dd);}return list;}return el('span','structured-value',String(value));}
function actionButton(label, style, action) {const button=el('button',`button ${style}`,label);button.type='button';button.disabled=state.busy;button.addEventListener('click',action);return button;}
async function taskAction(kind, data={}) {if(state.busy)return;state.busy=true;renderDetail();clearError();try {await api(`/api/tasks/${encodeURIComponent(state.selected)}/${kind}`,data);await refresh(true);toast(kind==='run'?'실제 작업자 실행을 시작했습니다.':kind==='cancel'?'업무를 취소했습니다.':data.decision==='approve'?'결과를 승인했습니다.':'결과를 반려했습니다.');}catch(error){showError(error);}finally{state.busy=false;renderDetail();}}
function safeFileUrl(value) {try{const url=new URL(value,location.origin);return url.origin===location.origin && /^\/api\/runs\/[^/]+\/files\//.test(url.pathname) ? url.pathname : null;}catch{return null;}}
async function previewArtifact(artifact) {const path=safeFileUrl(artifact.url);if(!path)return;$('#artifact-title').textContent=artifact.name;$('#artifact-content').textContent='파일을 불러오는 중입니다.';$('#artifact-dialog').showModal();try{const response=await fetch(path,{headers:{'X-DAS-Office':'1'}});if(!response.ok)throw new Error('파일을 불러오지 못했습니다.');$('#artifact-content').textContent=await response.text();}catch(error){$('#artifact-content').textContent=errorMessage(error);}}
function renderRun(run) {
  const block=el('div','run-block');
  const top=el('div','run-top');
  top.append(el('span','',`${run.attempt || '?'}차 실행 · ${run.provider || 'Codex'}`),badge(run.status));
  block.append(top,el('p','run-meta',`${date(run.started_at)} 시작${run.finished_at ? ` → ${date(run.finished_at)} 종료` : ''}`));
  if(run.run_dir) block.append(el('p','run-meta',`실행 폴더: ${run.run_dir}`));
  if(run.error) block.append(el('p','notice error run-error',run.error));
  function artifactRow(file) {
    const row=el('div','artifact');
    const info=el('div','file-info');
    info.append(el('span','file-name',file.name),el('span','file-meta',`${Number.isFinite(file.size)?`${file.size.toLocaleString('ko-KR')} bytes`:'저장된 파일'}${file.sha256 ? ` · SHA-256 ${file.sha256.slice(0,16)}…` : ''}`));
    row.append(el('span','file-icon','▤'),info);
    const url=safeFileUrl(file.url);
    if(url) {
      row.append(actionButton('보기','secondary small',()=>previewArtifact(file)));
      const link=el('a','','저장');
      link.href=url;
      link.download=file.name;
      row.append(link);
    }
    return row;
  }
  if(run.artifacts?.length) {
    const primaryNames=['report.md','result.json','verification.json'];
    const primary=run.artifacts.filter(file=>primaryNames.includes(file.name)).sort((a,b)=>primaryNames.indexOf(a.name)-primaryNames.indexOf(b.name));
    const records=run.artifacts.filter(file=>!primaryNames.includes(file.name));
    if(primary.length) {
      const files=el('div','artifacts');
      primary.forEach(file=>files.append(artifactRow(file)));
      block.append(files);
    }
    if(records.length) {
      const details=el('details');
      details.append(el('summary','details-toggle',`실행 입력 및 원본 기록 ${records.length}개`));
      const files=el('div','artifacts');
      records.forEach(file=>files.append(artifactRow(file)));
      details.append(files);
      block.append(details);
    }
  }
  if(run.verification) {
    const checks=Array.isArray(run.verification.checks)?run.verification.checks:[];
    const passed=checks.filter(check=>check.passed).length;
    const summary=checks.length ? `기본 검사 ${passed}/${checks.length} 통과 · 대표 내용 검토 필요` : '검증 결과와 근거';
    const details=el('details');
    details.append(el('summary','details-toggle',summary));
    if(run.verification.scope) details.append(el('p','text-block',run.verification.scope));
    details.append(valueNode(run.verification));
    block.append(details);
  }
  return block;
}
function renderDetail() {if(!state.detail)return;const {task,runs=[],events=[],decisions=[]}=state.detail;if(!task)return;const panel=$('#task-detail');panel.replaceChildren();const head=el('header','detail-header');const top=el('div','detail-topline');top.append(el('span','',project(task.project_id)),badge(task.status));head.append(top,el('h2','',task.title));const meta=el('div','detail-meta');meta.append(el('span','',`우선순위 ${priorities[task.priority]||task.priority}`),el('span','',`등록 ${date(task.created_at)}`),el('span','',`실행 ${runs.length}회`));head.append(meta);const actions=el('div','detail-actions');if(['queued','failed','cancelled'].includes(task.status)) {const runButton=actionButton(task.status==='queued'?'Codex로 실행':'명시적으로 재실행','primary',()=>taskAction('run'));runButton.disabled=state.busy||!state.health?.worker?.available;actions.append(runButton);}if(['queued','running'].includes(task.status))actions.append(actionButton('업무 취소','subtle',()=>taskAction('cancel')));head.append(actions);panel.append(head);const body=el('div','detail-body');if(task.error){const err=section('실패 원인');err.append(el('p','notice error',task.error));body.append(err);}if(task.status==='running'){const progress=section('작업자가 실행 중입니다.');progress.append(el('p','text-block','이 화면은 실행 기록을 자동으로 갱신합니다. 산출물 저장과 자동 검증을 마치면 결과를 검토할 수 있습니다.'));body.append(progress);}const brief=section('업무 명세','PM 정리');brief.append(el('span','detail-label','목표'),el('p','text-block',task.goal),el('span','detail-label','입력 자료'),el('p','text-block',typeof task.inputs==='string'?task.inputs:JSON.stringify(task.inputs,null,2)),el('span','detail-label','완료 기준'));const criteria=el('ul','criteria');for(const item of task.acceptance_criteria||[])criteria.append(el('li','',item));brief.append(criteria);if(task.pm_spec){const details=el('details');details.append(el('summary','details-toggle','PM 실행 계획과 배정 보기'),valueNode(task.pm_spec));brief.append(details);}body.append(brief);const result=section('산출물 및 검증 근거',runs.length ? `${runs.length}회 실행 기록` : '실행 전');if(runs.length){for(const run of [...runs].reverse())result.append(renderRun(run));}else result.append(el('p','text-block','업무를 실행하면 별도 폴더에 산출물과 실행 기록이 저장됩니다.'));body.append(result);if(task.status==='review'){const review=el('section','review-box');review.append(el('h3','','대표 검토가 필요합니다'),el('p','','자동 검증 결과와 실제 산출물을 확인하세요. 업무 내용과 완료 기준을 충족하는지 검토한 뒤 승인하거나 반려할 수 있습니다.'));const note=el('textarea');note.rows=3;note.placeholder='승인 또는 반려의 판단 근거를 입력해 주세요. 검토 의견은 필수입니다.';note.maxLength=6000;note.value=state.reviewDrafts.get(task.id)||'';note.addEventListener('input',()=>state.reviewDrafts.set(task.id,note.value));note.id='review-note';note.setAttribute('aria-label','결과 검토 의견');review.append(note);const buttons=el('div','review-buttons');buttons.append(actionButton('승인 · 완료 처리','primary',()=>{if(!note.value.trim()){note.focus();toast('승인 판단의 근거를 검토 의견에 입력해 주세요.');return;}taskAction('review',{decision:'approve',note:note.value.trim()});}),actionButton('반려 · 수정 요청','danger',()=>{if(!note.value.trim()){note.focus();toast('수정할 내용을 검토 의견에 입력해 주세요.');return;}taskAction('review',{decision:'reject',note:note.value.trim()});}));review.append(buttons);body.append(review);}if(decisions.length){const decisionSection=section('결과 검토 기록');for(const decision of decisions){const d=el('div','run-block');d.append(valueNode(decision));decisionSection.append(d);}body.append(decisionSection);}if(events.length){const history=section('활동 기록');const list=el('ol','events');for(const event of [...events].reverse()){const li=el('li');li.append(el('span','',event.message||event.description||event.type||event.event_type||'업무 기록'),el('time','event-date',`${date(event.created_at||event.timestamp)}${event.source?` · ${event.source}`:''}`));if(event.data){const details=el('details');details.append(el('summary','details-toggle','기록 상세'),valueNode(event.data));li.append(details);}list.append(li);}history.append(list);body.append(history);}const provenance=section('기록 정보');provenance.append(valueNode({source:task.source||'대표 등록',project_id:project(task.project_id),updated_at:date(task.updated_at),verification_status:task.verification_status||'미검증'}));body.append(provenance);panel.append(body);}
async function selectTask(id) {state.selected=id;state.detail=null;renderTasks();$('#task-detail').replaceChildren(el('div','empty-detail','업무 내용을 불러오는 중입니다.'));const request=++state.detailRequest;try{const detail=await api(`/api/tasks/${encodeURIComponent(id)}`);if(request!==state.detailRequest)return;state.detail=detail;renderDetail();}catch(error){showError(error);}}
async function refresh(withHealth=false) {try{const requests=[api('/api/tasks')];if(withHealth||!state.health)requests.push(api('/api/health'));const results=await Promise.all(requests);state.tasks=results[0].tasks||[];if(results[1]){state.health=results[1];renderHealth();}renderTasks();if(!state.selected&&state.tasks.length)await selectTask(state.tasks[0].id);else if(state.selected){const selected=state.selected;const request=++state.detailRequest;const detail=await api(`/api/tasks/${encodeURIComponent(selected)}`);if(request!==state.detailRequest||selected!==state.selected)return;const previous=JSON.stringify(state.detail);state.detail=detail;if(JSON.stringify(detail)!==previous)renderDetail();}clearError();}catch(error){showError(error);}}
async function openTaskForm(useSample=false) {const form=$('#task-form');form.reset();$('#form-error').hidden=true;$('#task-dialog').showModal();if(useSample){try{const sample=await api('/api/sample');for(const field of ['title','project_id','goal','inputs','priority'])if(sample[field]!==undefined)form.elements[field].value=sample[field];form.elements.acceptance_criteria.value=Array.isArray(sample.acceptance_criteria)?sample.acceptance_criteria.join('\n'):sample.acceptance_criteria||'';}catch(error){showError(error,$('#form-error'));}}}
$('#new-task').addEventListener('click',()=>openTaskForm());$('#empty-new-task').addEventListener('click',()=>openTaskForm());$('#sample-task').addEventListener('click',()=>openTaskForm(true));$('#refresh').addEventListener('click',()=>refresh(true));$('#search').addEventListener('input',renderTasks);$('#status-filter').addEventListener('change',renderTasks);document.querySelectorAll('.close-dialog').forEach(button=>button.addEventListener('click',()=>$('#task-dialog').close()));$('#close-artifact').addEventListener('click',()=>$('#artifact-dialog').close());
$('#task-form').addEventListener('submit',async event=>{event.preventDefault();const form=event.currentTarget;const data=Object.fromEntries(new FormData(form));data.acceptance_criteria=data.acceptance_criteria.split('\n').map(line=>line.trim()).filter(Boolean);if(!data.acceptance_criteria.length||data.acceptance_criteria.length>20||data.acceptance_criteria.some(item=>item.length>2000)){showError(new Error('완료 기준은 1~20개, 각 항목은 2,000자 이내로 입력해 주세요.'),$('#form-error'));return;}if(new Set(data.acceptance_criteria).size!==data.acceptance_criteria.length){showError(new Error('완료 기준의 중복 항목을 제거해 주세요.'),$('#form-error'));return;}$('#submit-task').disabled=true;$('#form-error').hidden=true;try{const created=await api('/api/tasks',data);$('#task-dialog').close();state.selected=created.task?.id||created.id;await refresh(true);toast('업무가 등록되었습니다. 내용을 확인하고 실행해 주세요.');}catch(error){showError(error,$('#form-error'));}finally{$('#submit-task').disabled=false;}});
async function poll(){if(!document.hidden&&!state.busy)await refresh(true);state.poll=setTimeout(poll,state.tasks.some(t=>t.status==='running')?4000:15000);}
refresh(true).then(()=>{state.poll=setTimeout(poll,4000);});
