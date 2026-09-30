'use strict';
const $ = (selector, root = document) => root.querySelector(selector);
const legacyReadOnly = /^\/legacy\/?$/.test(location.pathname);
const state = {health:null,tasks:[],selected:null,detail:null,busy:false,refreshing:false,request:0,drafts:new Map(),actionError:null};
const outcomeNames = {achieved:'달성',partial:'일부 달성',blocked:'진행 막힘',pending:'대기',running:'진행 중',unknown:'달성 미확인'};
const complexityNames = {simple:'간단',standard:'보통',moderate:'보통',complex:'복잡'};
const priorityNames = {high:'높음',normal:'보통',low:'낮음'};
function el(tag,cls,text){const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=String(text);return n;}
function date(value){return value ? new Date(value).toLocaleString('ko-KR',{month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit',hour12:false}) : '—';}
function duration(seconds){if(!Number.isFinite(seconds))return '—';const s=Math.max(0,Math.floor(seconds));if(s<60)return s+'초';if(s<3600)return Math.floor(s/60)+'분'+(s%60?' '+s%60+'초':'');return Math.floor(s/3600)+'시간 '+Math.floor(s%3600/60)+'분';}
function project(id){const names={daslab:'다스랩',franchise:'프랜차이즈 · 출점',ventures:'신사업 · 미디어'};return names[id]||state.health?.projects?.find(p=>p.id===id)?.name||'미션';}
function overview(task){return task.overview||{outcome:task.status==='running'?'running':task.status==='queued'?'pending':task.status==='failed'?'blocked':'unknown',progress_percent:null,summary:task.goal||'',duration_seconds:null,complexity:'standard',priority:task.priority,process:[]};}
function outcome(task){return task.status==='cancelled'?'cancelled':overview(task).outcome;}
function outcomeLabel(task){return task.status==='cancelled'?'취소됨':outcomeNames[outcome(task)]||'달성 미확인';}
function badge(task){return el('span','badge '+outcome(task),outcomeLabel(task));}
function attention(task){return ['partial','blocked'].includes(outcome(task))||(task.status==='queued'&&Boolean(task.start_error)&&!task.start_error.startsWith('다른 미션을 처리 중'));}
function hasReport(task){return ['review','completed'].includes(task.status);}
function matches(task,filter){if(!filter)return true;if(filter==='attention')return attention(task);if(filter==='reports')return hasReport(task);return outcome(task)===filter;}
function errorMessage(error){return error?.message||'요청을 처리하지 못했습니다.';}
async function api(path,data){if(legacyReadOnly&&data!==undefined)throw new Error('이전 미션 기록은 읽기 전용입니다. 조직 운영 화면에서 업무를 맡겨 주세요.');const options={headers:{'X-DAS-Office':'1'}};if(data!==undefined){options.method='POST';options.headers['Content-Type']='application/json';options.body=JSON.stringify(data);}const response=await fetch(path,options);let result;try{result=await response.json();}catch{throw new Error('서버에 연결하지 못했어요. 잠시 후 다시 시도해 주세요.');}if(!response.ok)throw new Error(result.error||'요청을 처리하지 못했습니다.');return result;}
function showError(error,target=$('#global-error')){target.textContent=errorMessage(error);target.hidden=false;}
let toastTimer;
function toast(message){$('#toast').textContent=message;$('#toast').hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>{$('#toast').hidden=true;},6500);}
function renderHealth(){const h=state.health;if(!h)return;const w=h.worker||{};$('#worker-title').textContent=w.available?'AI 연결됨':'AI 연결 필요';$('#worker-dot').className='dot'+(w.available?'':' offline');$('#worker-message').textContent=w.message||'연결 상태를 확인하고 있습니다.';const l=h.limits||{},u=h.usage||{};$('#worker-limits').textContent='오늘 실행 '+(u.runs_today??0)+' / '+(l.daily_runs??'—')+'회 · 한 번에 '+(l.concurrency??1)+'개 처리 · 회당 최대 '+Math.round((l.timeout_seconds||600)/60)+'분';}
function renderTasks(){
 const tasks=state.tasks,filter=$('#status-filter').value,term=$('#search').value.trim().toLocaleLowerCase();
 $('#count-all').textContent=tasks.length;$('#count-running').textContent=tasks.filter(t=>t.status==='running').length;$('#count-achieved').textContent=tasks.filter(t=>outcome(t)==='achieved').length;const count=tasks.filter(attention).length;$('#count-attention').textContent=count;
 $('#board-summary').textContent=legacyReadOnly?'보존된 미션의 결과 보고서와 실행 이력을 확인할 수 있습니다.':count?count+'개 미션에 남은 일이나 진행을 막는 문제가 있어요.':tasks.length?'미션을 누르면 결과 보고서를 볼 수 있어요.':'첫 미션을 맡기면 결과가 이곳에 모입니다.';
 document.querySelectorAll('.stat').forEach(b=>{const selected=b.dataset.filter===filter;b.classList.toggle('active',selected);b.setAttribute('aria-pressed',String(selected));});
 const filtered=tasks.filter(t=>matches(t,filter)&&(!term||[t.title,t.goal,overview(t).summary,project(t.project_id)].join(' ').toLocaleLowerCase().includes(term)));
 $('#list-count').textContent=filtered.length;const list=$('#task-list');const focusedId=document.activeElement?.dataset?.taskId;list.replaceChildren();
 if(!filtered.length){const empty=el('div','empty-list');empty.append(el('strong','',tasks.length?'조건에 맞는 미션이 없어요.':legacyReadOnly?'보존된 이전 미션이 없습니다.':'한 문장으로 시작하세요.'),el('p','',tasks.length?'다른 검색어나 상태를 선택해 보세요.':legacyReadOnly?'새 업무는 조직 운영 화면에서 맡길 수 있습니다.':'하고 싶은 일을 적으면, 처리 방법부터 결과 정리까지 맡아 진행합니다.'));list.append(empty);return;}
 for(const task of filtered){
  const o=overview(task),row=el('button','mission-row');row.type='button';row.dataset.taskId=task.id;row.setAttribute('aria-label',task.title+' · '+outcomeLabel(task)+' · '+(hasReport(task)?'보고서 보기':'진행 상황 보기'));
  const main=el('div','mission-main');main.append(el('h3','mission-title',task.title),el('p','mission-summary',o.summary));
  const category=el('div','mission-category');category.append(el('span','',project(task.project_id)));if(o.priority==='high')category.append(el('span','priority-tag','우선 처리'));category.append(el('span','mobile-duration',duration(o.duration_seconds)+' · '+(complexityNames[o.complexity]||'미분류')));main.append(category);
  const progress=el('div','mission-progress');progress.append(badge(task));if(Number.isFinite(o.progress_percent)){const n=el('div','progress-number',o.progress_percent+'%');n.append(el('small','','기준 충족'));progress.append(n);const bar=el('progress','progress-track');bar.max=100;bar.value=o.progress_percent;bar.setAttribute('aria-label','완료 기준 충족률');progress.append(bar);}else progress.append(el('p','progress-note',task.status==='running'?'완료 항목 확인 중':task.status==='queued'?'실행 전':task.status==='cancelled'?'실행 중단':'달성률 미확인'));
  const time=el('div','duration-cell');time.append(el('div','duration-value',duration(o.duration_seconds)),el('div','duration-sub',task.status==='running'?'현재까지':'실행 시간'));
  row.append(main,progress,time,el('span','complexity',complexityNames[o.complexity]||'미분류'),el('span','row-link',hasReport(task)?'보고서 ↗':'현황 ↗'));row.addEventListener('click',()=>selectTask(task.id));list.append(row);
 }
 if(focusedId)Array.from(list.children).find(n=>n.dataset.taskId===focusedId)?.focus({preventScroll:true});
}
function section(title,content,cls=''){const s=el('section','report-section '+cls);s.append(el('h3','',title));if(Array.isArray(content)){const ul=el('ul');content.forEach(x=>ul.append(el('li','',x)));s.append(ul);}else if(content)s.append(prose(content));return s;}
function prose(text){const block=el('div','report-prose');let paragraph=[],list=null,inCode=false,code=[];
 const flush=()=>{if(paragraph.length){block.append(el('p','',paragraph.join('\n')));paragraph=[];}list=null;};
 for(const raw of String(text||'').split('\n')){const line=raw.trim();if(line.startsWith('~~~')||line.startsWith(String.fromCharCode(96).repeat(3))){flush();if(inCode){block.append(el('pre','',code.join('\n')));code=[];}inCode=!inCode;continue;}if(inCode){code.push(raw);continue;}if(!line){flush();continue;}const heading=line.match(/^#{1,6}\s+(.+)/);if(heading){flush();block.append(el('h4','',heading[1]));continue;}const bullet=line.match(/^(?:[-*•]|\d+\.)\s+(.+)/);if(bullet){if(paragraph.length)flush();if(!list){list=el('ul');block.append(list);}list.append(el('li','',bullet[1]));}else{list=null;paragraph.push(raw);}}
 flush();if(code.length)block.append(el('pre','',code.join('\n')));return block;
}
function disclosure(key,title){const d=el('details','disclosure');d.dataset.key=key;d.append(el('summary','',title));const body=el('div');d.append(body);return {root:d,body};}
function button(label,cls,handler){const b=el('button','button '+cls,label);b.type='button';b.disabled=state.busy;b.addEventListener('click',handler);return b;}
function safeFileUrl(value){try{const u=new URL(value,location.origin);return u.origin===location.origin&&/^\/api\/runs\/[a-f0-9]{32}\/files\/[a-z_.]+$/.test(u.pathname)?u.pathname:null;}catch{return null;}}
async function previewArtifact(file){const url=safeFileUrl(file.url);if(!url)return;$('#artifact-title').textContent=file.name;$('#artifact-content').textContent='기록을 불러오고 있습니다.';$('#artifact-dialog').showModal();try{const response=await fetch(url);if(!response.ok)throw new Error('기록을 불러오지 못했습니다.');$('#artifact-content').textContent=await response.text();}catch(error){$('#artifact-content').textContent=errorMessage(error);}}
function renderDetail(){
 const detail=state.detail;if(!detail?.task)return;const {task,runs=[],events=[]}=detail,o=overview(task),report=detail.report,latest=runs.at(-1),panel=$('#task-detail');
 const openKeys=new Set(Array.from(panel.querySelectorAll('details[open]')).map(d=>d.dataset.key));const active=document.activeElement;const focusedRevision=active?.id==='revision-note';const selection=focusedRevision?[active.selectionStart,active.selectionEnd]:null;
 panel.replaceChildren();const header=el('header','detail-header'),top=el('div','detail-topline');top.append(el('span','',project(task.project_id)),badge(task));const title=el('h2','',task.title);title.id='report-title';header.append(top,title,el('p','detail-meta','등록 '+date(task.created_at)+' · 우선순위 '+(priorityNames[o.priority]||'보통')));panel.append(header);
 panel.append(el('p','result-summary',o.summary));
 const metrics=el('div','result-metrics');for(const [label,value,sub] of [['달성 현황',Number.isFinite(o.progress_percent)?o.progress_percent+'%':outcomeLabel(task),Number.isFinite(o.progress_percent)?'AI가 보고한 완료 기준 충족률':task.status==='running'?'완료 근거가 모이면 보고해요':'확인된 수치 없음'],['소요 시간',duration(o.duration_seconds),'실행 시간 합계 · 대기 제외'],['난이도',complexityNames[o.complexity]||'미분류','업무 내용에 따라 자동 판단']]){const m=el('div','result-metric');m.append(el('span','',label),el('strong','',value),el('small','',sub));metrics.append(m);}panel.append(metrics);
 const note=report?.assessment_source==='legacy_report'?'이전 보고서는 달성률을 기록하지 않았어요. 저장된 결과는 아래에서 바로 읽을 수 있습니다.':o.progress_basis;
 if(note&&note!==o.summary)panel.append(el('p','assessment-note',note));
 if(state.actionError)panel.append(el('p','notice error',state.actionError));
 if(task.error&&task.error!==o.summary)panel.append(el('p','notice error',task.error));else if(task.start_error&&task.status==='queued')panel.append(el('p','notice info',task.start_error));
 if(task.status==='running')panel.append(section('진행 상황','목표와 자료를 검토하고 결과를 작성 중입니다. 완료하면 이 화면에 보고서를 정리해 드릴게요.'));
 if(report){
  if(!['review','completed'].includes(task.status))panel.append(el('p','notice info','아래는 이전 실행의 결과입니다. 이번 요청의 달성 여부는 위 상태를 확인해 주세요.'));
  if(report.accomplishments?.length)panel.append(section('완료한 일',report.accomplishments.slice(0,3)));
  const isLegacy=report.assessment_source==='legacy_report';
  if(report.remaining?.length&&!isLegacy)panel.append(section('남은 일 · 필요한 결정',report.remaining.slice(0,3),'remaining'));
  const full=disclosure('report','전체 보고서 읽기');for(const item of report.sections||[])full.body.append(section(item.title,item.content));if(report.accomplishments?.length>3)full.body.append(section('완료한 일',report.accomplishments.slice(3)));if(isLegacy&&report.remaining?.length)full.body.append(section('제안된 다음 단계',report.remaining));else if(report.remaining?.length>3)full.body.append(section('이어서 할 일',report.remaining.slice(3)));if(report.limitations?.length)full.body.append(section('결과의 한계',report.limitations));panel.append(full.root);
 }
 const actions=el('div','detail-actions');
 const reportFile=latest?.artifacts?.find(a=>a.name==='report.md'),reportUrl=reportFile&&safeFileUrl(reportFile.url);
 if(reportUrl){const a=el('a','button secondary','보고서 저장 ↓');a.href=reportUrl;a.download=task.title.replace(/[\\/:*?"<>|]/g,'_')+' 보고서.md';actions.append(a);}
 if(!legacyReadOnly&&['queued','failed','cancelled'].includes(task.status)){const run=button(task.status==='queued'?'지금 시작':'다시 맡기기','primary',()=>taskAction('run'));run.disabled=state.busy||!state.health?.worker?.available;actions.append(run);}
 if(!legacyReadOnly&&['queued','running'].includes(task.status))actions.append(button('중단','subtle',()=>taskAction('cancel')));
 panel.append(actions);
 if(!legacyReadOnly&&['review','completed'].includes(task.status)){
  const revision=disclosure('revision','방향을 바꾸거나 보완하고 싶다면');revision.body.className='revision-form';const note=el('textarea');note.id='revision-note';note.rows=3;note.maxLength=6000;note.placeholder='예: 더 짧게 정리해줘. 추천한 기능의 실행 순서도 추가해줘.';note.setAttribute('aria-label','보완할 내용');note.value=state.drafts.get(task.id)||'';note.addEventListener('input',()=>state.drafts.set(task.id,note.value));revision.body.append(note,button('수정해서 다시 맡기기','primary',()=>{if(!note.value.trim()){note.focus();return;}taskAction('revise',{note:note.value.trim()});}),el('p','muted','바꾸고 싶은 점만 알려주세요. 다시 실행해 새 결과를 보고합니다.'));panel.append(revision.root);
 }
 const plan=disclosure('plan','AI가 판단한 처리 방법');plan.body.append(section('맡긴 미션',task.mission||task.goal));if(o.process?.length)plan.body.append(section('처리 순서',o.process));if(task.acceptance_criteria?.length)plan.body.append(section('내부 완료 기준',task.acceptance_criteria));if(task.inputs)plan.body.append(section('참고한 내용',task.inputs));panel.append(plan.root);
 const records=disclosure('records','근거와 실행 기록');if(!runs.length)records.body.append(el('p','muted','실행하면 기록이 저장됩니다.'));
 if(report?.milestones?.length){const breakdown=section('진행률에 반영한 결과');for(const item of report.milestones){const row=el('div','evidence-item');row.append(el('strong','',({met:'완료',unmet:'미완료',unknown:'미확인'}[item.status]||'미확인')+' · '+item.deliverable),el('small','',item.explanation));breakdown.append(row);}records.body.append(breakdown);}
 for(const run of [...runs].reverse()){const group=el('div','record-group');group.append(el('p','',run.attempt+'차 실행 · '+date(run.started_at)+(run.finished_at?' → '+date(run.finished_at):' · 진행 중')));if(run.verification){const checks=run.verification.checks||[];group.append(el('p','muted','자동 검사 '+checks.filter(c=>c.passed).length+'/'+checks.length+' 통과 · 내용의 독립 검증은 포함하지 않습니다.'));const checksView=disclosure('checks-'+run.id,'검사 항목 보기');for(const check of checks){const row=el('div','evidence-item');row.append(el('strong','',(check.passed?'✓ ':'— ')+check.name),el('small','',check.evidence));checksView.body.append(row);}group.append(checksView.root);}
  const files=disclosure('files-'+run.id,'원본 파일 보기');for(const file of run.artifacts||[]){const url=safeFileUrl(file.url);if(!url)continue;const row=el('div','artifact-row');row.append(el('span','',file.name),button('열기','secondary small',()=>previewArtifact(file)));files.body.append(row);}group.append(files.root);records.body.append(group);
 }
 if(events.length){const history=disclosure('history','활동 기록');for(const event of [...events].reverse()){const item=el('div','evidence-item');item.append(el('span','',event.message||'업무 기록'),el('small','',date(event.created_at)));history.body.append(item);}records.body.append(history.root);}panel.append(records.root);
 panel.querySelectorAll('details').forEach(d=>{d.open=openKeys.has(d.dataset.key);});if(focusedRevision){const restored=$('#revision-note');if(restored){restored.focus({preventScroll:true});restored.setSelectionRange(...selection);}}
}
async function selectTask(id){state.selected=id;state.detail=null;state.actionError=null;const token=++state.request;$('#task-detail').replaceChildren(el('p','empty-list','결과를 불러오고 있어요.'));if(!$('#report-dialog').open)$('#report-dialog').showModal();try{const detail=await api('/api/tasks/'+encodeURIComponent(id));if(token!==state.request||state.selected!==id)return;state.detail=detail;renderDetail();}catch(error){$('#task-detail').replaceChildren(el('p','notice error',errorMessage(error)));}}
async function taskAction(kind,data={}){
 if(legacyReadOnly||state.busy||!state.selected)return;const id=state.selected;state.busy=true;state.actionError=null;renderDetail();
 try{let result;if(kind==='revise'){result=await api('/api/tasks/'+id+'/revise',{note:data.note});state.drafts.delete(id);if(!result.started&&result.queued===false&&result.start_error)state.actionError=result.start_error;}else result=await api('/api/tasks/'+id+'/'+kind,data);toast(kind==='cancel'?'미션을 중단했습니다.':result.start_error||'미션을 맡겼습니다. 결과가 준비되면 여기에 보고합니다.');}
 catch(error){state.actionError=errorMessage(error);toast(state.actionError);}
 finally{state.busy=false;await refresh(true);renderDetail();}
}
async function refresh(withHealth=false){
 if(state.refreshing)return;state.refreshing=true;
 try{const results=await Promise.all([api('/api/tasks'),...(withHealth||!state.health?[api('/api/health')]:[])]);const changed=JSON.stringify(state.tasks)!==JSON.stringify(results[0].tasks||[]);const initial=!state.health;state.tasks=results[0].tasks||[];if(results[1]){state.health=results[1];renderHealth();}if(changed||initial)renderTasks();if(state.selected&&$('#report-dialog').open){const id=state.selected,token=++state.request;const detail=await api('/api/tasks/'+encodeURIComponent(id));if(token===state.request&&id===state.selected){const previous=JSON.stringify(state.detail);state.detail=detail;if(previous!==JSON.stringify(detail))renderDetail();}}$('#global-error').hidden=true;}
 catch(error){showError(error);}finally{state.refreshing=false;}
}
$('#mission-form').addEventListener('submit',async event=>{
 event.preventDefault();if(legacyReadOnly||$('#submit-mission').disabled)return;const mission=$('#mission').value.trim(),inputs=$('#mission-inputs').value.trim();if(!mission){$('#mission').focus();return;}$('#submit-mission').disabled=true;$('#submit-mission').textContent='맡기는 중…';$('#form-error').hidden=true;
 try{const result=await api('/api/missions',{mission,inputs});event.target.reset();$('.context-input').open=false;$('#mission-dialog').close();$('#status-filter').value='';$('#search').value='';await refresh(true);toast(result.started?'미션을 맡겼어요. 처리와 결과 정리까지 이어서 진행합니다.':result.start_error||'미션을 받았어요. 앞선 업무가 끝나면 이어서 처리합니다.');}
 catch(error){showError(error,$('#form-error'));}finally{$('#submit-mission').disabled=false;$('#submit-mission').textContent='미션 맡기기 ↗';}
});
$('#refresh').addEventListener('click',()=>refresh(true));$('#search').addEventListener('input',renderTasks);$('#status-filter').addEventListener('change',renderTasks);
document.querySelectorAll('.stat').forEach(b=>b.addEventListener('click',()=>{$('#status-filter').value=b.dataset.filter;renderTasks();}));
$('#nav-reports').addEventListener('click',()=>{$('#status-filter').value='reports';renderTasks();$('.mission-board').scrollIntoView({behavior:'smooth'});});
$('#close-report').addEventListener('click',()=>$('#report-dialog').close());$('#report-dialog').addEventListener('close',()=>{state.selected=null;state.detail=null;++state.request;});
$('#connection-toggle').addEventListener('click',()=>$('#connection-dialog').showModal());$('#close-connection').addEventListener('click',()=>$('#connection-dialog').close());$('#close-artifact').addEventListener('click',()=>$('#artifact-dialog').close());
$('#new-mission').addEventListener('click',()=>{if(legacyReadOnly)return;$('#mission-dialog').showModal();$('#mission').focus();});
$('#close-mission').addEventListener('click',()=>$('#mission-dialog').close());
async function poll(){if(!document.hidden&&!state.busy)await refresh(true);setTimeout(poll,state.tasks.some(t=>t.status==='running')?4000:15000);}
if(legacyReadOnly){$('#legacy-notice').hidden=false;$('#new-mission').hidden=true;$('#new-mission').disabled=true;$('#submit-mission').disabled=true;$('.page-heading h1').textContent='이전 미션 기록';$('.page-intro').textContent='보고서와 실행 이력을 보존했습니다. 이 화면에서는 업무를 변경하지 않습니다.';document.title='DAS Lab · 이전 미션 기록';}
refresh(true).then(()=>setTimeout(poll,4000));
