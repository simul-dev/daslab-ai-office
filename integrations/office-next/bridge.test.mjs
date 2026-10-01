import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,readFile,writeFile,rm,mkdir,symlink} from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {Readable} from 'node:stream';
import {allowedRequest,projectSnapshot,createBridge} from './bridge.mjs';
test('live run is required to animate work; a todo alone is idle',()=>{
  const agents=[{id:'a',name:'연구원',role:'engineer',status:'idle',adapterConfig:{env:{SECRET:'must-not-leak'}}}];
  const issues=[{id:'i',title:'분석',status:'todo',assigneeAgentId:'a'}];
  assert.equal(projectSnapshot({id:'c',name:'회사'},agents,issues).agents[0].status,'idle');
  const observed=projectSnapshot({id:'c',name:'회사'},agents,issues,[{id:'r',agentId:'a',status:'running'}]);
  assert.equal(observed.agents[0].status,'running');assert.ok(!JSON.stringify(observed).includes('SECRET'));
});
test('blocked and reviewer assignments are projected from actual task state',()=>{
  const agents=[{id:'a',name:'개발',status:'idle'},{id:'b',name:'검수',status:'idle'}];
  const snap=projectSnapshot({id:'c'},agents,[{id:'i',status:'blocked',assigneeAgentId:'a'},{id:'j',status:'in_review',assigneeAgentId:'b'}]);
  assert.equal(snap.agents[0].status,'blocked');assert.equal(snap.agents[1].status,'review');
});
test('loopback host and same origin only; DNS rebinding and foreign origins denied',()=>{
  assert.equal(allowedRequest({headers:{host:'127.0.0.1:8790',origin:'http://127.0.0.1:8790'}},8790),true);
  assert.equal(allowedRequest({headers:{host:'evil.example:8790'}},8790),false);
  assert.equal(allowedRequest({headers:{host:'127.0.0.1:8790',origin:'https://evil.example'}},8790),false);
});

test('approval and pause state remain visible even with an assigned task',()=>{
  for(const status of ['paused','pending_approval','disabled']) {
    const snapshot=projectSnapshot({id:'c'},[{id:'a',name:'staff',status}],[{id:'i',status:'todo',assigneeAgentId:'a'}]);
    assert.equal(snapshot.agents[0].status,status);
    assert.equal(snapshot.agents[0].activeRun,null);
  }
  const draining=projectSnapshot({id:'c'},[{id:'a',name:'staff',status:'paused'}],[],[{id:'r',agentId:'a',status:'running'}]);
  assert.equal(draining.agents[0].status,'paused');
  assert.equal(draining.agents[0].activeRun.status,'running');
});

const COMPANY='11111111-1111-4111-8111-111111111111';
const AGENT='22222222-2222-4222-8222-222222222222';
const ISSUE='33333333-3333-4333-8333-333333333333';
const REQUEST='44444444-4444-4444-8444-444444444444';
const validPayload={agentId:AGENT,instruction:'검증 가능한 작은 결과물을 만들어줘.',requestId:REQUEST};
const uiHeaders={'x-office-next':'1',origin:'http://127.0.0.1:8790'};

/** Exercise the real HTTP callback without binding a port or touching a live server. */
function request(server,{method='GET',url='/api/office/snapshot',headers={},payload}={}) {
  const data=payload===undefined?'':JSON.stringify(payload);
  const req=Readable.from(data?[Buffer.from(data)]:[]);
  req.method=method;req.url=url;req.headers={host:'127.0.0.1:8790',...headers};
  return new Promise((resolve,reject)=>{
    const timeout=setTimeout(()=>reject(new Error('Injected HTTP request timed out')),3000);
    const result={status:0,headers:{}};
    const res={
      writeHead(status,values){result.status=status;result.headers=values;},
      end(bytes){
        clearTimeout(timeout);
        try{
          const content=String(bytes ?? '');
          resolve({...result,body:result.headers['Content-Type']?.startsWith('application/json')?JSON.parse(content || 'null'):content});
        }catch(error){reject(error);}
      },
    };
    server.emit('request',req,res);
  });
}

async function fixture(t,{actionsEnabled=true,fetchImpl}={}) {
  const dir=await mkdtemp(path.join(os.tmpdir(),'office-next-bridge-test-'));
  t.after(async()=>{
    const resolved=path.resolve(dir);
    assert.ok(resolved.startsWith(path.resolve(os.tmpdir())+path.sep));
    assert.ok(path.basename(resolved).startsWith('office-next-bridge-test-'));
    await rm(resolved,{recursive:true,force:true});
  });
  const configPath=path.join(dir,'instance.json');
  await writeFile(configPath,JSON.stringify({paperclipUrl:'http://127.0.0.1:3190',companyId:COMPANY,actionsEnabled}));
  const calls=[];
  const upstream=async(url,options={})=>{
    calls.push({url:String(url),options});
    if(fetchImpl)return fetchImpl(url,options);
    const pathname=new URL(url).pathname;
    let value;
    if(pathname.endsWith('/agents'))value=[{id:AGENT,name:'PM',status:'idle'}];
    else if(pathname.endsWith('/issues') && options.method==='POST')value={id:ISSUE,identifier:'TEST-1',status:'todo'};
    else throw new Error(`Unexpected mock endpoint: ${pathname}`);
    return {ok:true,status:200,json:async()=>value};
  };
  return {dir,configPath,calls,upstream,server:await createBridge({configPath,fetchImpl:upstream})};
}

test('HTTP mutation rejects missing UI header and foreign origin before upstream execution',async(t)=>{
  const f=await fixture(t);
  const noHeader=await request(f.server,{method:'POST',url:'/api/office/issues',payload:validPayload});
  assert.equal(noHeader.status,403);
  const foreign=await request(f.server,{method:'POST',url:'/api/office/issues',headers:{...uiHeaders,origin:'https://foreign.example'},payload:validPayload});
  assert.equal(foreign.status,403);
  const rebound=await request(f.server,{headers:{host:'attacker.example:8790'}});
  assert.equal(rebound.status,403);
  assert.equal(f.calls.length,0);
});

test('HTTP duplicate request IDs produce one upstream issue even concurrently and after ledger reload',async(t)=>{
  const f=await fixture(t);
  const opts={method:'POST',url:'/api/office/issues',headers:uiHeaders,payload:validPayload};
  const [first,duplicate]=await Promise.all([request(f.server,opts),request(f.server,opts)]);
  assert.equal(first.status,201);assert.equal(duplicate.status,201);
  assert.deepEqual(first.body,{id:ISSUE,identifier:'TEST-1',status:'todo'});
  assert.deepEqual(duplicate.body,first.body);
  const posts=f.calls.filter(call=>call.options.method==='POST');
  assert.equal(posts.length,1);
  const sent=JSON.parse(posts[0].options.body);
  assert.equal(sent.assigneeAgentId,AGENT);
  assert.equal(sent.idempotencyKey,`office-next-${REQUEST}`);
  const persisted=JSON.parse(await readFile(path.join(f.dir,'requests.json'),'utf8'));
  assert.deepEqual(persisted[REQUEST],first.body);
  const reloaded=await createBridge({configPath:f.configPath,fetchImpl:f.upstream});
  const count=f.calls.length;
  const retry=await request(reloaded,opts);
  assert.equal(retry.status,201);assert.deepEqual(retry.body,first.body);
  assert.equal(f.calls.length,count);
});

test('HTTP foreign-company issue is denied before comments or private content is returned',async(t)=>{
  const f=await fixture(t,{fetchImpl:async()=>({ok:true,status:200,json:async()=>({id:ISSUE,companyId:'another-company',description:'PRIVATE-DESCRIPTION'})})});
  const result=await request(f.server,{url:`/api/office/issues/${ISSUE}`});
  assert.equal(result.status,404);
  assert.equal(f.calls.length,1);
  assert.ok(!JSON.stringify(result.body).includes('PRIVATE-DESCRIPTION'));
  assert.deepEqual(Object.keys(result.body),['error']);
});

test('HTTP unavailable upstream returns 503 without synthetic staff or leaking diagnostics',async(t)=>{
  const f=await fixture(t,{fetchImpl:async()=>{throw new Error('SECRET-UPSTREAM-TRANSPORT-DETAIL');}});
  const result=await request(f.server);
  assert.equal(result.status,503);
  assert.equal(result.headers['Cache-Control'],'no-store');
  assert.deepEqual(Object.keys(result.body),['error']);
  assert.ok(!JSON.stringify(result.body).includes('SECRET-UPSTREAM'));
  assert.equal(result.body.agents,undefined);
  assert.equal(result.body.issues,undefined);
});

test('HTTP non-success upstream response is a gateway error, never a successful empty snapshot',async(t)=>{
  const f=await fixture(t,{fetchImpl:async()=>({ok:false,status:500})});
  const result=await request(f.server);
  assert.equal(result.status,502);
  assert.deepEqual(Object.keys(result.body),['error']);
});

test('HTTP read-only pilot refuses creation without touching upstream',async(t)=>{
  const f=await fixture(t,{actionsEnabled:false});
  const result=await request(f.server,{method:'POST',url:'/api/office/issues',headers:uiHeaders,payload:validPayload});
  assert.equal(result.status,409);
  assert.equal(f.calls.length,0);
});

test('HTTP unapproved or paused employees cannot receive new tasks',async(t)=>{
  for(const status of ['paused','pending_approval','disabled','terminated']) {
    const f=await fixture(t,{fetchImpl:async()=>({ok:true,status:200,json:async()=>[{id:AGENT,name:'staff',status}]})});
    const result=await request(f.server,{method:'POST',url:'/api/office/issues',headers:uiHeaders,payload:validPayload});
    assert.equal(result.status,409,status);
    assert.equal(f.calls.length,1);
    assert.equal(f.calls[0].options.method,undefined);
  }
});

const CHILD='55555555-5555-4555-8555-555555555555';
const WORKER='66666666-6666-4666-8666-666666666666';
function dependencySnapshot({dependencyStatus='in_progress',explicit=true,workerStatus='idle',known=true,runs=[],extraBlockers=[],executionBlocker=null}={}) {
  return projectSnapshot({id:COMPANY},[{id:AGENT,name:'PM',status:'idle'},{id:WORKER,name:'QA',status:workerStatus}],
    [{id:ISSUE,identifier:'DAS-1',status:'blocked',assigneeAgentId:AGENT,executionBlocker,
      blockedBy:explicit?[{id:CHILD},...extraBlockers]:[]},
      ...(known?[{id:CHILD,identifier:'DAS-2',parentId:ISSUE,status:dependencyStatus,assigneeAgentId:WORKER}]:[])],runs);
}

test('explicit progressing dependencies are team waiting, never owner-attention blockers',()=>{
  for(const dependencyStatus of ['todo','in_progress','in_review','running']) {
    const snap=dependencySnapshot({dependencyStatus});
    const pm=snap.agents.find(agent=>agent.id===AGENT);
    assert.equal(pm.status,'waiting_on_team',dependencyStatus);
    assert.equal(pm.blockedReason,null);
    assert.match(pm.waitingReason,/DAS-2/);
    assert.deepEqual(pm.currentIssue.blockedByIssueIds,[CHILD]);
    assert.equal(pm.currentIssue.waitingOnTeam,true);
  }
});

test('implicit children, unresolved dependencies and execution errors still require attention',()=>{
  for(const opts of [
    {explicit:false},{known:false},{dependencyStatus:'blocked'},{dependencyStatus:'error'},
    {dependencyStatus:'done'},{workerStatus:'error'},{workerStatus:'paused'},
    {extraBlockers:[{id:REQUEST}]},{executionBlocker:{reason:'runtime failure'}},
  ]) {
    const pm=dependencySnapshot(opts).agents.find(agent=>agent.id===AGENT);
    assert.equal(pm.status,'blocked',JSON.stringify(opts));
    assert.equal(pm.currentIssue.waitingOnTeam,false);
    assert.ok(pm.blockedReason);
  }
  const recovered=dependencySnapshot({workerStatus:'error',runs:[{id:'live-run',agentId:WORKER,status:'running'}]});
  assert.equal(recovered.agents.find(agent=>agent.id===AGENT).status,'waiting_on_team');
});

test('HTTP snapshot obtains explicit dependency edges from same-company issue detail',async(t)=>{
  const f=await fixture(t,{fetchImpl:async(url)=>{
    const route=new URL(url).pathname;
    const data={
      [`/api/companies/${COMPANY}`]:{id:COMPANY,name:'Test company'},
      [`/api/companies/${COMPANY}/agents`]:[{id:AGENT,name:'PM',status:'idle'},{id:WORKER,name:'QA',status:'idle'}],
      [`/api/companies/${COMPANY}/issues`]:[{id:ISSUE,identifier:'DAS-1',status:'blocked',assigneeAgentId:AGENT},{id:CHILD,identifier:'DAS-2',parentId:ISSUE,status:'in_progress',assigneeAgentId:WORKER}],
      [`/api/companies/${COMPANY}/live-runs`]:[{id:'r',agentId:WORKER,status:'running'}],
      [`/api/issues/${ISSUE}`]:{id:ISSUE,companyId:COMPANY,status:'blocked',blockedBy:[{id:CHILD,secret:'must-not-leak'}]},
    };
    assert.ok(route in data,route);
    return {ok:true,status:200,json:async()=>data[route]};
  }});
  const result=await request(f.server);
  assert.equal(result.status,200);
  assert.equal(result.body.agents[0].status,'waiting_on_team');
  assert.equal(result.body.agents[1].status,'running');
  assert.ok(f.calls.some(call=>call.url.endsWith(`/api/issues/${ISSUE}`)));
  assert.ok(!JSON.stringify(result.body).includes('must-not-leak'));
});

test('HTTP detail includes the human-readable issue identifier',async(t)=>{
  const f=await fixture(t,{fetchImpl:async(url)=>({ok:true,status:200,json:async()=>new URL(url).pathname.endsWith('/comments')?[]:{id:ISSUE,identifier:'DAS-1',companyId:COMPANY,title:'Delegated project',status:'blocked'}})});
  const result=await request(f.server,{url:`/api/office/issues/${ISSUE}`});
  assert.equal(result.status,200);
  assert.equal(result.body.issue.identifier,'DAS-1');
});

async function demoFixture(t,{artifact='demo.html',configure=true,fetchImpl}={}) {
  const f=await fixture(t,{fetchImpl});
  const workspace=path.join(f.dir,'workspace');
  await mkdir(workspace);
  const html='<!doctype html><html><style>body{color:green}</style><body>Verified demo<script>document.title="Demo"</script></body></html>';
  await writeFile(path.join(workspace,'demo.html'),html);
  const config={...JSON.parse(await readFile(f.configPath,'utf8')),workspace};
  if(configure)config.demoArtifactPath=path.resolve(workspace,artifact);
  await writeFile(f.configPath,JSON.stringify(config));
  return {...f,workspace,html,server:await createBridge({configPath:f.configPath,fetchImpl:f.upstream})};
}

test('HTTP demo is unavailable until an exact artifact path is configured',async(t)=>{
  const f=await demoFixture(t,{configure:false});
  const disabled=await request(f.server,{url:'/demo/supply-chain'});
  assert.equal(disabled.status,404);
  const unknown=await request(f.server,{url:'/demo/other.html'});
  assert.equal(unknown.status,404);
  assert.equal(f.calls.length,0);
  const missing=await demoFixture(t,{artifact:'not-produced.html'});
  assert.equal((await request(missing.server,{url:'/demo/supply-chain'})).status,404);
});

test('HTTP demo serves only configured HTML with opaque sandbox and no network permissions',async(t)=>{
  const f=await demoFixture(t);
  const result=await request(f.server,{url:'/demo/supply-chain?path=../../outside.html'});
  assert.equal(result.status,200);
  assert.equal(result.body,f.html); // Query strings are never used as file paths.
  assert.equal(result.headers['Content-Type'],'text/html; charset=utf-8');
  assert.equal(result.headers['Cache-Control'],'no-store');
  assert.equal(result.headers['X-Content-Type-Options'],'nosniff');
  assert.equal(result.headers['Referrer-Policy'],'no-referrer');
  const csp=result.headers['Content-Security-Policy'];
  assert.match(csp,/(?:^|; )sandbox allow-scripts(?:;|$)/);
  assert.ok(!csp.includes('allow-same-origin'));
  for(const directive of ["default-src 'none'","script-src 'unsafe-inline'","style-src 'unsafe-inline'","connect-src 'none'","img-src data:","form-action 'none'","frame-ancestors 'none'"])assert.ok(csp.includes(directive),directive);
  assert.equal(f.calls.length,0);
});

test('HTTP demo denies a configured file outside workspace including a sibling with same prefix',async(t)=>{
  const f=await demoFixture(t,{artifact:'../workspace-other/outside.html'});
  await mkdir(path.join(f.dir,'workspace-other'));
  await writeFile(path.join(f.dir,'workspace-other','outside.html'),'PRIVATE-OUTSIDE-CONTENT');
  const result=await request(f.server,{url:'/demo/supply-chain'});
  assert.equal(result.status,403);
  assert.ok(!JSON.stringify(result.body).includes('PRIVATE-OUTSIDE-CONTENT'));
  assert.equal(f.calls.length,0);
});

test('HTTP demo refuses arbitrary files and directories',async(t)=>{
  const nonHtml=await demoFixture(t,{artifact:'result.json'});
  await writeFile(path.join(nonHtml.workspace,'result.json'),'PRIVATE-JSON');
  assert.equal((await request(nonHtml.server,{url:'/demo/supply-chain'})).status,403);
  const directory=await demoFixture(t,{artifact:'directory.html'});
  await mkdir(path.join(directory.workspace,'directory.html'));
  assert.equal((await request(directory.server,{url:'/demo/supply-chain'})).status,403);
});

test('HTTP demo denies symlink or junction escapes from workspace',async(t)=>{
  const f=await demoFixture(t,{artifact:'external/demo.html'});
  const outside=path.join(f.dir,'outside');
  await mkdir(outside);
  await writeFile(path.join(outside,'demo.html'),'PRIVATE-JUNCTION-CONTENT');
  try{await symlink(outside,path.join(f.workspace,'external'),process.platform==='win32'?'junction':'dir');}
  catch(error){if(['EPERM','EACCES'].includes(error.code)){t.skip('Host disallows test symlinks');return;}throw error;}
  const result=await request(f.server,{url:'/demo/supply-chain'});
  assert.equal(result.status,403);
  assert.ok(!JSON.stringify(result.body).includes('PRIVATE-JUNCTION-CONTENT'));
});

test('snapshot advertises a demo only for an existing configured allowed HTML artifact',async(t)=>{
  const fetchImpl=async(url)=>({ok:true,status:200,json:async()=>{
    const pathname=new URL(url).pathname;
    if(pathname===`/api/companies/${COMPANY}`)return {id:COMPANY,name:'Office'};
    if(pathname.endsWith('/agents'))return [{id:AGENT,name:'PM',status:'idle'}];
    return [];
  }});
  for(const [options,expected] of [[{},true],[{configure:false},false],[{artifact:'absent.html'},false],[{artifact:'../outside.html'},false],[{artifact:'report.json'},false]]) {
    const f=await demoFixture(t,{...options,fetchImpl});
    const result=await request(f.server);
    assert.equal(result.status,200);
    assert.equal(result.body.capabilities.demoAvailable,expected,JSON.stringify(options));
    assert.equal(result.body.demoArtifactPath,undefined);
  }
});

async function developerFixture(t,{agentIds={developer:AGENT,qa:CHILD,pm:WORKER},changeAgents}={}) {
  let agents=[{id:AGENT,companyId:COMPANY,name:'Developer',status:'idle'},
    {id:CHILD,companyId:COMPANY,name:'QA',status:'idle'},
    {id:WORKER,companyId:COMPANY,name:'PM',status:'running'}];
  if(changeAgents)agents=changeAgents(agents);
  const f=await fixture(t,{fetchImpl:async(url,options)=>({ok:true,status:200,json:async()=>{
    if(new URL(url).pathname.endsWith('/agents'))return agents;
    assert.equal(options.method,'POST');
    return {id:ISSUE,identifier:'TEST-1',status:'todo'};
  }})});
  const config=JSON.parse(await readFile(f.configPath,'utf8'));
  await writeFile(f.configPath,JSON.stringify({...config,agentIds}));
  return {...f,server:await createBridge({configPath:f.configPath,fetchImpl:f.upstream})};
}

test('direct developer assignment always supplies QA then PM gates and human escalation attribution',async(t)=>{
  const f=await developerFixture(t);
  const result=await request(f.server,{method:'POST',url:'/api/office/issues',headers:uiHeaders,payload:{...validPayload,
    executionPolicy:{mode:'normal',stages:[]},responsibleUserId:'ignored-user'}});
  assert.equal(result.status,201);
  const posts=f.calls.filter(call=>call.options.method==='POST');
  assert.equal(posts.length,1);
  const created=JSON.parse(posts[0].options.body);
  assert.equal(created.assigneeAgentId,AGENT);
  assert.equal(created.responsibleUserId,'local-board');
  assert.deepEqual(created.executionPolicy,{mode:'normal',commentRequired:true,maxReviewRounds:2,stages:[
    {type:'review',participants:[{type:'agent',agentId:CHILD}]},
    {type:'approval',participants:[{type:'agent',agentId:WORKER}]},
  ]});
});

test('direct developer assignment cannot silently drop unavailable review participants',async(t)=>{
  const conditions=[
    {agentIds:{developer:AGENT,pm:WORKER}},
    {agentIds:{developer:AGENT,qa:AGENT,pm:WORKER}},
    {agentIds:{developer:AGENT,qa:CHILD,pm:CHILD}},
    {agentIds:{developer:AGENT,qa:'invalid-reviewer-id',pm:WORKER}},
    {changeAgents:agents=>agents.filter(agent=>agent.id!==CHILD)},
    {changeAgents:agents=>agents.map(agent=>agent.id===CHILD?{...agent,companyId:'foreign-company'}:agent)},
    {changeAgents:agents=>agents.map(agent=>agent.id===AGENT?{...agent,companyId:'foreign-company'}:agent)},
    ...['paused','pending_approval','terminated','disabled','error','unknown'].flatMap(status=>[CHILD,WORKER].map(id=>({
      changeAgents:agents=>agents.map(agent=>agent.id===id?{...agent,status}:agent),
    }))),
  ];
  for(const condition of conditions) {
    const f=await developerFixture(t,condition);
    const result=await request(f.server,{method:'POST',url:'/api/office/issues',headers:uiHeaders,payload:validPayload});
    assert.equal(result.status,409);
    assert.equal(f.calls.filter(call=>call.options.method==='POST').length,0);
  }
});

test('simple/status roles keep their existing direct-assignment behavior',async(t)=>{
  const f=await developerFixture(t);
  const result=await request(f.server,{method:'POST',url:'/api/office/issues',headers:uiHeaders,payload:{...validPayload,agentId:WORKER,instruction:'현재 진행 상황을 보고해줘.'}});
  assert.equal(result.status,201);
  const created=JSON.parse(f.calls.find(call=>call.options.method==='POST').options.body);
  assert.equal(created.executionPolicy,undefined);
  assert.equal(created.responsibleUserId,undefined);
});
