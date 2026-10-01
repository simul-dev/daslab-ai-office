import {readFile,writeFile,mkdir,readdir} from 'node:fs/promises';
import path from 'node:path';import {createHash} from 'node:crypto';
const root=path.join(process.env.LOCALAPPDATA,'DASLab','ai-office-next');
const cfg=JSON.parse(await readFile(path.join(root,'office.json'),'utf8'));
async function api(route){const r=await fetch(cfg.paperclipUrl+route,{signal:AbortSignal.timeout(15000)});if(!r.ok)throw new Error(`Evidence read failed ${r.status}`);return r.json();}
const [agents,issues,runs]=await Promise.all(['agents','issues','heartbeat-runs'].map(type=>api(`/api/companies/${cfg.companyId}/${type}`)));
const reports=[];for(const i of issues){const detail=await api(`/api/issues/${i.id}`);const comments=await api(`/api/issues/${i.id}/comments`);reports.push({id:i.id,identifier:i.identifier,title:i.title,parentId:i.parentId,status:i.status,assigneeAgentId:i.assigneeAgentId,createdByAgentId:i.createdByAgentId,createdByUserId:i.createdByUserId,executionPolicy:detail.executionPolicy,executionState:detail.executionState,blockedBy:detail.blockedBy?.map(x=>({id:x.id,identifier:x.identifier,status:x.status})),comments:comments.map(c=>({id:c.id,authorAgentId:c.authorAgentId,authorUserId:c.authorUserId,runId:c.runId,body:c.body,createdAt:c.createdAt}))});}
const files=[];const artifactDir=path.join(cfg.workspace,'supply-chain-trial');
for(const entry of await readdir(artifactDir,{withFileTypes:true})){if(entry.isFile()){const bytes=await readFile(path.join(artifactDir,entry.name));files.push({name:entry.name,size:bytes.length,sha256:createHash('sha256').update(bytes).digest('hex')});}}
const evidence={observedAt:new Date().toISOString(),companyId:cfg.companyId,workspace:cfg.workspace,syntheticExercise:true,productionMigrated:false,
 agents:agents.map(a=>({id:a.id,name:a.name,status:a.status,reportsTo:a.reportsTo,adapterType:a.adapterType})),
 runs:runs.map(r=>({id:r.id,agentId:r.agentId,status:r.status,startedAt:r.startedAt,finishedAt:r.finishedAt,errorCode:r.errorCode})),issues:reports,artifacts:files};
await mkdir(path.join(root,'verification'),{recursive:true});await writeFile(path.join(root,'verification','office-next-evidence.json'),JSON.stringify(evidence,null,2));
console.log(JSON.stringify({issues:reports.map(i=>({identifier:i.identifier,status:i.status})),runs:runs.length,artifacts:files.length}));
