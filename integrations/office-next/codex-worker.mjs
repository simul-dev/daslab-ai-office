// Windows-compatible Paperclip process adapter. Native Codex owns authentication.
// No credential copy, managed-home symlink, global config write, or API billing.
import {readFile,writeFile,mkdir,realpath} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {spawn} from 'node:child_process';
import {redactStream} from './redact-stream.mjs';
import {attachWorkerWatchdog} from './worker-watchdog.mjs';
const here=path.dirname(fileURLToPath(import.meta.url));
const root=path.join(process.env.LOCALAPPDATA,'DASLab','ai-office-next');
const cfg=JSON.parse(await readFile(path.join(root,'office.json'),'utf8'));
// Store-app virtualization is per-user; the sandbox user needs the real path.
const workspace=await realpath(cfg.workspace);
const [slug,codex]=process.argv.slice(2);
if(cfg.agentIds[slug]!==process.env.PAPERCLIP_AGENT_ID || cfg.companyId!==process.env.PAPERCLIP_COMPANY_ID)throw new Error('Worker identity does not match isolated company.');
if(!codex || !path.isAbsolute(codex) || !process.env.PAPERCLIP_API_KEY)throw new Error('Existing Codex and run-scoped Paperclip authentication are required.');
const apiBase=new URL(process.env.PAPERCLIP_API_URL);
if(!['127.0.0.1','localhost'].includes(apiBase.hostname)||apiBase.protocol!=='http:')throw new Error('This worker is restricted to the local trial control plane.');
const runResponse=await fetch(new URL(`/api/heartbeat-runs/${process.env.PAPERCLIP_RUN_ID}`,apiBase),{headers:{Authorization:`Bearer ${process.env.PAPERCLIP_API_KEY}`},signal:AbortSignal.timeout(15000)});
if(!runResponse.ok)throw new Error(`Cannot read own wake context (${runResponse.status}).`);
const run=await runResponse.json();
const wake=run.contextSnapshot ?? {};
const wakeIssueId=wake.issueId ?? wake.paperclipIssue?.id ?? null;
const wakeCommentId=wake.wakeCommentId ?? wake.commentId ?? null;
const executionStage=wake.executionStage ?? null;
const checkedOutByHarness=wake.paperclipHarnessCheckedOut===true || wake.checkedOutByHarness===true;
const methodRoot=path.join(workspace,'.office-methods').replaceAll('\\','/');
const instructions=(await Promise.all(['common',slug].map(name=>readFile(path.join(here,'roles',name+'.md'),'utf8')))).map(text=>text.replaceAll('C:/Users/User/AppData/Local/DASLab/ai-office-next',methodRoot));
const prompt=`${instructions.join('\n\n')}

Runtime contract for this Paperclip heartbeat:
You are ${slug}; agent ID ${process.env.PAPERCLIP_AGENT_ID}; company ID ${cfg.companyId}; run ID ${process.env.PAPERCLIP_RUN_ID}.
Work only in ${workspace}. Use this exact physical path as tool workdir; do not substitute the virtual AppData alias, which fails under the sandbox account. Company context is knowledge/*.md with snapshot-provenance.json. team.json lists coworkers. Do not edit another checkout or operating data. Do not spawn nested model CLIs or use paid APIs.
The common role instructions are included above. Optional local copies are in roles/common.md and roles/${slug}.md; there is no need to search for a common.md at workspace root.
Use Node.js and PowerShell. Git Bash if needed is C:/Program Files/Git/bin/bash.exe. Your current tools are provided by Codex CLI, not by an interactive desktop browser.
Use the Paperclip REST API with the runtime PAPERCLIP_API_KEY from your environment. Never print, write, or put that token in command arguments. The installed helper handles headers safely: node worker-api.mjs METHOD /api/path [body-file.json]. GET has no body file; mutation body files must contain only ordinary task data, never credentials. It attaches the real run ID automatically. Do not use unauthenticated board requests.
Wake issue ID: ${wakeIssueId ?? 'none'}; wake comment ID: ${wakeCommentId ?? 'none'}; already checked out by server: ${checkedOutByHarness}. If a wake issue is provided, read it and relevant comments first and handle the actual review/approval stage; do not substitute generic inbox selection. Otherwise GET /api/companies/${cfg.companyId}/issues?assigneeAgentId=${process.env.PAPERCLIP_AGENT_ID}&status=todo,in_progress,in_review,blocked. Work your assigned in_progress or in_review before todo. Read relevant issue and comments. A review/approval task in in_review must NEVER be checked out: confirm currentParticipant and send the decision PATCH directly, preserving the review stage. For executor work only, if the server has not already checked it out, POST /api/issues/ID/checkout with {agentId:your_id,expectedStatuses:['todo','in_progress','blocked','backlog']}. A 409 means someone else owns it: do not retry. No assigned actionable work means exit.
Execution stage supplied by the server: ${JSON.stringify(executionStage)}. A review/approval wake is a decision on the existing artifact, not permission to reimplement it. Current issue state must confirm you are the current participant.
GET /api/issues/ID and /api/issues/ID/comments reads work. POST /api/companies/COMPANY/issues creates a child with title,description,status:'todo',assigneeAgentId,parentId,idempotencyKey. Only PM/assistant may assign. Mutation responses must confirm stored state.
For implementation children assigned to developer ${cfg.agentIds.developer}, PM/assistant MUST include responsibleUserId:'local-board' and executionPolicy:{mode:'normal',commentRequired:true,maxReviewRounds:2,stages:[{type:'review',participants:[{type:'agent',agentId:'${cfg.agentIds.qa}'}]},{type:'approval',participants:[{type:'agent',agentId:'${cfg.agentIds.pm}'}]}]}. Confirm the stored policy and distinct reviewer before beginning implementation. A short status answer does not require creating implementation work. The role prompt is a workflow instruction, not an authorization boundary.
PATCH /api/issues/ID with {status:'done',comment:'actual evidence'} completes work or advances executionPolicy. In an in_review issue where you are currentParticipant, PATCH done+comment approves; PATCH in_progress+comment requests changes and returns it to the original executor. Do not pretend a review decision has happened without the returned state. PM must not complete a parent until its children and review stages finish.
Immediately before marking blocked, requesting changes, or accepting/completing, reread the current issue and its latest comments: new evidence may have arrived during this run. Evaluate independent operator evidence by source, time, artifact hash and scenario coverage; never claim it was your own browser run. Preserve failed checks and distinguish a missing tool from a product defect. Do not rerun a known blocked environment without a changed condition.
When waiting for a child, PATCH parent {status:'blocked',blockedByIssueIds:[child_id],comment:'next action after child completion'}. Do not busy-poll. When a blocker is resolved and you are woken, continue the same parent. Mark a real unresolved blocker honestly; do not leave an actionable task with only a plan. Two failed identical writes mean stop and report the error; no infinite retries.
For a nondependency blocked task, use {status:'blocked',unblockDescriptor:{owner:{agentId:your_own_id},action:'concrete next action, at most 2000 characters'},comment:'evidence and help needed'}. An agent may name only itself as unblock owner; naming PM, another agent, a user or board is rejected. Ask the PM for help through the issue's normal comment/mention flow while keeping the actual unblock ownership and state truthful. Read the installed API error before adjusting a failed write; do not cycle through guessed owners.
Every result must be preserved in the issue comment plus actual workspace files. Never claim synthetic tests are customer results, never claim browser verification without browser execution. gstack relevant skill files are readable at the paths given above; apply only needed steps. Finish in Korean with concrete results and remaining work.
`;
const allow=['PATH','PATHEXT','SYSTEMROOT','WINDIR','COMSPEC','USERPROFILE','APPDATA','LOCALAPPDATA','TEMP','TMP','HOMEDRIVE','HOMEPATH','USERNAME','OS','PROCESSOR_ARCHITECTURE','NUMBER_OF_PROCESSORS','PAPERCLIP_AGENT_ID','PAPERCLIP_COMPANY_ID','PAPERCLIP_API_URL','PAPERCLIP_API_KEY','PAPERCLIP_RUN_ID'];
const env=Object.fromEntries(Object.entries(process.env).filter(([key])=>allow.includes(key.toUpperCase())));
env.GSTACK_OUTSIDE_REVIEW='off'; env.NO_COLOR='1';
const args=['exec','--ignore-user-config','--ephemeral','--sandbox','workspace-write','--json','--color','never','-C',workspace,
  '-c','approval_policy="never"','-c','windows.sandbox="elevated"','-c','sandbox_workspace_write.network_access=true','-c','shell_environment_policy.inherit="all"','-c','shell_environment_policy.ignore_default_excludes=true','-'];
await mkdir(path.join(root,'verification'),{recursive:true});
await writeFile(path.join(root,'verification',`worker-${process.env.PAPERCLIP_RUN_ID}.json`),JSON.stringify({adapter:'process',worker:slug,runId:process.env.PAPERCLIP_RUN_ID,startedAt:new Date().toISOString(),workspace,subscription:true,sandbox:'workspace-write',userConfigIgnored:true,credentialFilesCopied:false},null,2));
const launchedAt=Date.now();
const child=spawn(codex,args,{cwd:workspace,env,windowsHide:true,stdio:['pipe','pipe','pipe']});
child.on('error',err=>{console.error('Codex launch failed:',err.code);process.exitCode=1;});
child.on('exit',(code)=>{process.exitCode=code ?? 1;});
child.stdin.on('error',()=>{process.exitCode=1;});
child.stdout.pipe(redactStream([process.env.PAPERCLIP_API_KEY])).pipe(process.stdout);
child.stderr.pipe(redactStream([process.env.PAPERCLIP_API_KEY])).pipe(process.stderr);
let watcher;
try {watcher=await attachWorkerWatchdog({child,executable:codex,launchedAt,deadlineAt:launchedAt+570000,env:process.env});}
catch {child.kill();throw new Error('Worker lifecycle guard could not attach; no prompt was sent.');}
if(watcher && child.exitCode===null)child.stdin.end(prompt);
else process.exitCode=child.exitCode ?? 1;
