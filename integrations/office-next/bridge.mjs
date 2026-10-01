import http from 'node:http';
import {readFile, writeFile, mkdir, stat, realpath} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const UUID = /^[a-f0-9-]{36}$/i;
const ACTIVE = new Set(['running', 'queued']);
const DEPENDENCY_PROGRESS = new Set(['todo','in_progress','in_review','running']);
const text = (v, limit = 500) => typeof v === 'string' ? v.slice(0, limit) : '';
export function projectSnapshot(company, agents, issues, runs = [], now = new Date().toISOString()) {
  const list = Array.isArray(issues) ? issues : issues?.data ?? [];
  const publicIssues = list.map(i => ({id:i.id, identifier:i.identifier, title:text(i.title), status:i.status,
    priority:i.priority, assigneeAgentId:i.assigneeAgentId, parentId:i.parentId,
    updatedAt:i.updatedAt, description:text(i.description, 12000),
    blockedByIssueIds:[...new Set([
      ...(Array.isArray(i.blockedByIssueIds)?i.blockedByIssueIds:[]),
      ...(Array.isArray(i.blockedBy)?i.blockedBy.map(blocker=>blocker?.id):[]),
    ].filter(id=>typeof id==='string' && UUID.test(id)))],
    hasExecutionBlocker:!!i.executionBlocker}));
  const byId=new Map(publicIssues.map(issue=>[issue.id,issue]));
  for(const issue of publicIssues) {
    issue.blockers=issue.blockedByIssueIds.map(id=>{
      const blocker=byId.get(id);
      return blocker?{id,identifier:blocker.identifier,title:blocker.title,status:blocker.status,assigneeAgentId:blocker.assigneeAgentId}: {id,status:'unknown'};
    });
    issue.waitingOnTeam=issue.status==='blocked' && !issue.hasExecutionBlocker && issue.blockers.length>0 && issue.blockers.every(blocker=>{
      if(!DEPENDENCY_PROGRESS.has(blocker.status))return false;
      const staff=agents.find(agent=>agent.id===blocker.assigneeAgentId);
      if(staff && runs.some(run=>run.agentId===staff.id && run.status==='running'))return true;
      return !staff || !['error','paused','pending_approval','disabled','terminated'].includes(staff.status);
    });
  }
  return {observedAt:now, source:'paperclip', company:{id:company.id,name:company.name},
    agents:agents.filter(a => a.status !== 'terminated').map(a => {
      const activeRun = runs.find(r => r.agentId === a.id && ACTIVE.has(r.status));
      const own = publicIssues.filter(i => i.assigneeAgentId === a.id);
      const current = own.find(i => i.status === 'in_progress') ?? own.find(i => i.status === 'blocked') ?? own.find(i => i.status === 'in_review') ?? own.find(i => i.status === 'todo');
      const status = ['paused','pending_approval','disabled'].includes(a.status) ? a.status : activeRun?.status === 'running' ? 'running' : a.status === 'error' ? 'error' : current?.waitingOnTeam ? 'waiting_on_team' : current?.status === 'blocked' ? 'blocked' : current?.status === 'in_review' ? 'review' : activeRun ? 'queued' : 'idle';
      return {id:a.id,name:a.name,role:a.title || a.role,status,managerId:a.reportsTo,
        currentIssue:current ?? null, activeRun:activeRun ? {id:activeRun.id,status:activeRun.status}:null,
        lastResult:own.find(i => i.status === 'done')?.title ?? '',
        blockedReason:current?.status === 'blocked' && !current.waitingOnTeam ? '담당 업무의 막힘 사유를 확인해 주세요.':null,
        waitingReason:current?.waitingOnTeam ? `${current.blockers.map(blocker=>blocker.identifier || blocker.id).join(', ')} 선행 업무의 결과를 기다리고 있습니다.`:null};
    }), issues:publicIssues};
}
export function allowedRequest(req, port) {
  const hosts = new Set([`127.0.0.1:${port}`, `localhost:${port}`]);
  if (!hosts.has(req.headers.host)) return false;
  const origin = req.headers.origin;
  return !origin || [...hosts].some(h => origin === `http://${h}`);
}
async function body(req) {
  let data='';
  for await (const chunk of req) {data+=chunk; if (Buffer.byteLength(data)>20000) throw Object.assign(new Error('입력이 너무 깁니다.'),{status:413});}
  try {return JSON.parse(data || '{}');} catch {throw Object.assign(new Error('올바른 JSON이 필요합니다.'),{status:400});}
}
function json(res,status,value) {res.writeHead(status,{'Content-Type':'application/json; charset=utf-8','Cache-Control':'no-store','X-Content-Type-Options':'nosniff'});res.end(JSON.stringify(value));}
function directDeveloperReview(config,agentId,agents) {
  if(agentId!==config.agentIds?.developer)return {};
  const ids=[config.agentIds.developer,config.agentIds.qa,config.agentIds.pm];
  const reject=()=>{throw Object.assign(new Error('개발 업무의 QA 검수자와 PM 승인자를 먼저 확인해 주세요. 검수 없이 배정하지 않았습니다.'),{status:409});};
  if(ids.some(id=>typeof id!=='string' || !UUID.test(id)) || new Set(ids).size!==3)reject();
  const participants=ids.map(id=>agents.find(agent=>agent.id===id && agent.companyId===config.companyId));
  if(participants.some(agent=>!agent))reject();
  const available=new Set(['idle','running','active']);
  if(participants.slice(1).some(agent=>!available.has(agent.status)))reject();
  return {
    responsibleUserId:'local-board',
    executionPolicy:{mode:'normal',commentRequired:true,maxReviewRounds:2,stages:[
      {type:'review',participants:[{type:'agent',agentId:config.agentIds.qa}]},
      {type:'approval',participants:[{type:'agent',agentId:config.agentIds.pm}]},
    ]},
  };
}
const DEMO_CSP="sandbox allow-scripts; default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; connect-src 'none'; font-src 'none'; media-src 'none'; object-src 'none'; frame-src 'none'; worker-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'";
function inside(directory,file) {
  const relative=path.relative(directory,file);
  return !!relative && relative!=='..' && !relative.startsWith('..'+path.sep) && !path.isAbsolute(relative);
}
async function resolveDemoArtifact(config) {
  if(typeof config.demoArtifactPath!=='string' || !config.demoArtifactPath || typeof config.workspace!=='string' || !config.workspace) return null;
  const workspace=path.resolve(config.workspace);
  const artifact=path.resolve(workspace,config.demoArtifactPath);
  const forbidden=()=>Object.assign(new Error('허용된 HTML 데모가 아닙니다.'),{status:403});
  if(!inside(workspace,artifact) || path.extname(artifact).toLowerCase()!=='.html')throw forbidden();
  try {
    // Resolve both paths before reading so a junction/symlink cannot turn an
    // in-workspace filename into access to a file outside the pilot workspace.
    const [actualWorkspace,actualArtifact]=await Promise.all([realpath(workspace),realpath(artifact)]);
    if(!inside(actualWorkspace,actualArtifact) || path.extname(actualArtifact).toLowerCase()!=='.html')throw forbidden();
    if(!(await stat(actualArtifact)).isFile())throw forbidden();
    return actualArtifact;
  } catch(error) {
    if(['ENOENT','ENOTDIR'].includes(error.code))return null;
    throw error;
  }
}
export async function createBridge({port=8790, configPath, fetchImpl=fetch, dist=path.join(here,'pixel','dist')}) {
  const config=JSON.parse(await readFile(configPath,'utf8'));
  const upstream=new URL(config.paperclipUrl);
  if (upstream.hostname !== '127.0.0.1' || upstream.protocol !== 'http:') throw new Error('Paperclip must be a local loopback instance.');
  async function api(route, options={}) {
    const result=await fetchImpl(new URL(route,upstream), {...options,headers:{'Content-Type':'application/json',...options.headers},signal:AbortSignal.timeout(12000)});
    if (!result.ok) throw Object.assign(new Error(`업무 서버 요청 실패 (${result.status})`),{status:502});
    return result.status===204 ? null : result.json();
  }
  async function agentList(){return api(`/api/companies/${config.companyId}/agents`);}
  const requestLedgerPath=path.join(path.dirname(configPath),'requests.json');
  let ledger={}; try {ledger=JSON.parse(await readFile(requestLedgerPath,'utf8'));}catch(e){if(e.code!=='ENOENT') throw e;}
  let mutation=Promise.resolve();
  const server=http.createServer(async (req,res) => {
    if(!allowedRequest(req,port)) return json(res,403,{error:'허용되지 않은 접근입니다.'});
    const url=new URL(req.url,`http://127.0.0.1:${port}`);
    try {
      if(url.pathname==='/demo/supply-chain') {
        if(req.method!=='GET')return json(res,405,{error:'지원하지 않는 요청입니다.'});
        const artifactPath=await resolveDemoArtifact(config);
        if(artifactPath===null)return json(res,404,{error:'아직 연결된 데모가 없습니다.'});
        const artifact=await readFile(artifactPath);
        res.writeHead(200,{'Content-Type':'text/html; charset=utf-8','Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Referrer-Policy':'no-referrer','Content-Security-Policy':DEMO_CSP});
        return res.end(artifact);
      }
      if(url.pathname.startsWith('/demo/'))return json(res,404,{error:'없는 데모입니다.'});
      if(req.method==='GET' && url.pathname==='/api/office/snapshot') {
        const [company,agents,issues,runs]=await Promise.all([
          api(`/api/companies/${config.companyId}`),agentList(),api(`/api/companies/${config.companyId}/issues?limit=100`),
          api(`/api/companies/${config.companyId}/live-runs`)]);
        // List responses omit explicit dependency edges. Read blocked issues'
        // authorized detail instead of guessing from child/parent relationships.
        const list=Array.isArray(issues)?issues:issues?.data ?? [];
        const withDependencies=await Promise.all(list.map(async issue=>{
          if(issue.status!=='blocked')return issue;
          const detail=await api(`/api/issues/${issue.id}`);
          if(detail.companyId!==config.companyId)return {...issue,blockedBy:[],blockedByIssueIds:[]};
          return {...issue,blockedBy:detail.blockedBy,blockedByIssueIds:detail.blockedByIssueIds,executionBlocker:detail.executionBlocker};
        }));
        const snapshot=projectSnapshot(company,agents,withDependencies,runs);
        const demoAvailable=await resolveDemoArtifact(config).then(Boolean).catch(()=>false);
        snapshot.capabilities={actionsAvailable:config.actionsEnabled===true,demoAvailable};
        snapshot.paperclipUrl=config.paperclipUrl;
        snapshot.mode='pilot'; snapshot.legacyUrl='http://127.0.0.1:8772/';
        snapshot.notice='새 업무 환경 · 기존 업무 기록은 기존 오피스에 보존되어 있습니다.';
        return json(res,200,snapshot);
      }
      const issueMatch=url.pathname.match(/^\/api\/office\/issues\/([a-f0-9-]{36})$/i);
      if(req.method==='GET' && issueMatch) {
        const issue=await api(`/api/issues/${issueMatch[1]}`);
        if(issue.companyId!==config.companyId) return json(res,404,{error:'업무가 없습니다.'});
        const comments=await api(`/api/issues/${issue.id}/comments`);
        return json(res,200,{issue:{id:issue.id,identifier:issue.identifier,title:issue.title,status:issue.status,description:issue.description},comments:comments.map(c=>({id:c.id,body:c.body,createdAt:c.createdAt,authorAgentId:c.authorAgentId}))});
      }
      if(req.method==='POST' && url.pathname==='/api/office/issues') {
        if(config.actionsEnabled!==true) return json(res,409,{error:'연결 검증 중입니다. 아직 업무 실행을 열지 않았습니다.'});
        if(req.headers['x-office-next']!=='1') return json(res,403,{error:'화면에서 업무를 맡겨 주세요.'});
        const payload=await body(req);
        if(!UUID.test(payload.agentId ?? '') || typeof payload.instruction!=='string' || !payload.instruction.trim() || payload.instruction.length>12000 || !UUID.test(payload.requestId ?? '')) return json(res,400,{error:'직원과 업무 내용을 확인해 주세요.'});
        const operation=async()=>{
          if(ledger[payload.requestId]) return ledger[payload.requestId];
          const agents=await agentList();
          if(!agents.some(a=>a.id===payload.agentId && !['paused','terminated','pending_approval','disabled'].includes(a.status))) throw Object.assign(new Error('현재 업무를 받을 수 없는 직원입니다.'),{status:409});
          const review=directDeveloperReview(config,payload.agentId,agents);
          const issue=await api(`/api/companies/${config.companyId}/issues`,{method:'POST',body:JSON.stringify({title:payload.instruction.trim().slice(0,100),description:payload.instruction.trim(),status:'todo',priority:'medium',assigneeAgentId:payload.agentId,idempotencyKey:`office-next-${payload.requestId}`,...review})});
          const value={id:issue.id,identifier:issue.identifier,status:issue.status};
          ledger[payload.requestId]=value;
          await mkdir(path.dirname(requestLedgerPath),{recursive:true});
          await writeFile(requestLedgerPath,JSON.stringify(ledger,null,2));
          return value;
        };
        const pending=mutation.then(operation);mutation=pending.catch(()=>{});
        return json(res,201,await pending);
      }
      if(url.pathname.startsWith('/api/')) return json(res,404,{error:'없는 기능입니다.'});
      if(req.method!=='GET') return json(res,405,{error:'지원하지 않는 요청입니다.'});
      const decoded=decodeURIComponent(url.pathname);
      const target=path.resolve(dist,'.'+(decoded==='/'?'/index.html':decoded));
      if(!target.startsWith(path.resolve(dist)+path.sep)) return json(res,403,{error:'없는 파일입니다.'});
      let file=target;
      try {if(!(await stat(file)).isFile()) throw new Error();} catch {if(path.extname(decoded))return json(res,404,{error:'없는 파일입니다.'});file=path.join(dist,'index.html');}
      const mime={'.html':'text/html; charset=utf-8','.js':'text/javascript; charset=utf-8','.css':'text/css; charset=utf-8','.png':'image/png','.svg':'image/svg+xml','.json':'application/json','.woff2':'font/woff2'}[path.extname(file)] || 'application/octet-stream';
      res.writeHead(200,{'Content-Type':mime,'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Referrer-Policy':'no-referrer','Content-Security-Policy':"default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; font-src 'self'; connect-src 'self'; frame-ancestors 'none'"});res.end(await readFile(file));
    } catch(error) {json(res,error.status || 503,{error:error.status?error.message:'업무 서버에 연결하지 못했습니다. 확인되지 않은 상태는 표시하지 않습니다.'});}
  });
  return server;
}
if(process.argv[1] && path.resolve(process.argv[1])===fileURLToPath(import.meta.url)) {
  const configPath=process.argv[2];if(!configPath)throw new Error('Pass the local instance config path.');
  const port=Number(process.env.OFFICE_NEXT_PORT || 8790);
  const server=await createBridge({configPath,port});
  server.listen(port,'127.0.0.1',()=>console.log(`Office Next ready at http://127.0.0.1:${port}`));
}
