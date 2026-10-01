import {readFile,writeFile,mkdir} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {execFileSync} from 'node:child_process';
import {prepareKnowledge} from './context.mjs';
import {bootstrapWorkspace,mergeBootstrapConfig} from './bootstrap-config.mjs';
const here=path.dirname(fileURLToPath(import.meta.url));
const root=path.join(process.env.LOCALAPPDATA,'DASLab','ai-office-next');
const base='http://127.0.0.1:3101';
const stateFile=path.join(root,'office.json');
let config;try{config=JSON.parse(await readFile(stateFile,'utf8'));}catch(e){if(e.code!=='ENOENT')throw e;}
const workspace=bootstrapWorkspace(config,path.join(process.env.USERPROFILE,'AI-Office-Next','workspace'));
const codex=process.argv[2];
if(!codex || !path.isAbsolute(codex))throw new Error('Provide the existing absolute Codex CLI path.');
async function api(route,method='GET',body) {
  const r=await fetch(base+route,{method,headers:{'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined,signal:AbortSignal.timeout(30000)});
  if(!r.ok)throw new Error(`Paperclip ${method} ${route}: ${r.status}: ${(await r.text()).slice(0,1000)}`);
  return r.status===204?null:r.json();
}
await mkdir(workspace,{recursive:true});
await prepareKnowledge({sourceRoot:path.resolve(here,'../..'),workspace});
try {execFileSync('git',['rev-parse','--git-dir'],{cwd:workspace,stdio:'ignore'});}catch {execFileSync('git',['init'],{cwd:workspace,stdio:'ignore'});}
const companyName='DAS Lab · 새 사무실';
let company=config?await api(`/api/companies/${config.companyId}`):(await api('/api/companies')).find(c=>c.name===companyName);
if(!company)company=await api('/api/companies','POST',{name:companyName,description:'Paperclip + Pixel Agents + gstack 전환 검증. 기존 운영 데이터와 별도 저장.'});
await api(`/api/companies/${company.id}`,'PATCH',{requireBoardApprovalForNewAgents:false});
const existing=await api(`/api/companies/${company.id}/agents`);
const roster=[
  {slug:'assistant',name:'대표 비서',role:'general',manager:null},
  {slug:'pm',name:'프로젝트 PM',role:'pm',manager:'assistant'},
  {slug:'researcher',name:'리서치',role:'researcher',manager:'pm'},
  {slug:'developer',name:'개발',role:'engineer',manager:'pm'},
  {slug:'qa',name:'검수',role:'qa',manager:'pm'},
  {slug:'investment-analyst',name:'전략·투자심사',role:'general',manager:null}
];
const common=await readFile(path.join(here,'roles','common.md'),'utf8');
const ids=config?.agentIds || {};
for(const row of roster){
  let agent=existing.find(a=>a.id===ids[row.slug]) ?? existing.find(a=>a.name===row.name);
  const role=await readFile(path.join(here,'roles',`${row.slug}.md`),'utf8');
  const instructions=`${common}\n\n${role}\n\nYour permitted work directory: ${workspace}.\nCompany knowledge is the read-only snapshot in this workspace's knowledge directory; read snapshot-provenance.json for source and date. Do not edit any other checkout or operating database. Use the injected Paperclip skill for API checkout, comments, delegation and review. Use only existing ChatGPT subscription authentication; do not set provider API keys. Do not dump environment variables, auth files, cookies, or tokens.\n`;
  const payload={name:row.name,role:row.role,title:row.name,reportsTo:row.manager?ids[row.manager]:null,adapterType:'codex_local',adapterConfig:{engine:'cli',command:codex,cwd:workspace,dangerouslyBypassApprovalsAndSandbox:false,fastMode:false,timeoutSec:600,graceSec:15,env:{GSTACK_OUTSIDE_REVIEW:'off',PAPERCLIP_CODEX_AUTH_CACHE:'0'}},instructionsBundle:{entryFile:'AGENTS.md',files:{'AGENTS.md':instructions}},runtimeConfig:{heartbeat:{enabled:false,intervalSec:0,wakeOnDemand:true,maxConcurrentRuns:1}},permissions:{canCreateAgents:false,canCreateSkills:false}};
  if(!agent)agent=await api(`/api/companies/${company.id}/agents`,'POST',payload);
  ids[row.slug]=agent.id;
  await api(`/api/agents/${agent.id}/permissions`,'PATCH',{canCreateAgents:false,canCreateSkills:false,canAssignTasks:['assistant','pm'].includes(row.slug)});
  // Store after every create so an interrupted setup can resume without duplicate staff.
  config=mergeBootstrapConfig(config,{paperclipUrl:base,companyId:company.id,agentIds:ids,workspace});
  await writeFile(stateFile,JSON.stringify(config,null,2));
}
await writeFile(path.join(workspace,'team.json'),JSON.stringify({companyId:company.id,agents:roster.map(r=>({id:ids[r.slug],slug:r.slug,name:r.name,managerId:ids[r.manager] ?? null}))},null,2));
console.log(JSON.stringify({companyId:company.id,agentCount:Object.keys(ids).length,actionsEnabled:config.actionsEnabled}));
