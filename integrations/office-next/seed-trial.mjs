// One bounded synthetic delivery exercise. Never resets or duplicates an existing run.
import {readFile,writeFile,mkdir} from 'node:fs/promises';
import path from 'node:path';
const root=path.join(process.env.LOCALAPPDATA,'DASLab','ai-office-next');
const cfg=JSON.parse(await readFile(path.join(root,'office.json'),'utf8'));
const trialFile=path.join(root,'trial.json');
try {const prior=JSON.parse(await readFile(trialFile,'utf8')); console.log(JSON.stringify(prior));process.exit(0);}catch(e){if(e.code!=='ENOENT')throw e;}
const target=path.join(cfg.workspace,'supply-chain-trial');
await mkdir(target,{recursive:true});
const input={label:'Synthetic integration test, not customer evidence',demand:[40,60,30],baseline:'A',candidates:[
  {id:'A',name:'수원',lat:37.26,lon:127.03,capacity:150,fixedCost:1000,unitCosts:[5,8,10]},
  {id:'B',name:'천안',lat:36.81,lon:127.15,capacity:80,fixedCost:30,unitCosts:[4,6,8]},
  {id:'C',name:'대전',lat:36.35,lon:127.38,capacity:180,fixedCost:100,unitCosts:[8,7,5]}
]};
await writeFile(path.join(target,'input.json'),JSON.stringify(input,null,2));
await writeFile(path.join(target,'legacy-estimate.mjs'),`// Deliberately unverified synthetic starting fixture for independent QA.\nexport function estimate(input) {\n  return input.candidates.map(c=>({id:c.id,total:input.demand.reduce((s,d,i)=>s+d*c.unitCosts[i],0)})).sort((a,b)=>a.total-b.total)[0];\n}\n`);
const brief=`이것은 새 사무실의 실제 직원 실행·오류 발견·수정·검수 경로를 검증하는 합성 과제다. 실제 고객 자료나 사업성과가 아니다.
목표: supply-chain-trial/input.json을 이용하여 단일 물류거점 후보의 용량 제약과 고정비+배송비를 비교하고 대표가 후보·기준안을 바꿔 결과를 확인하는 작고 설명 가능한 데모를 납품한다. 임의 지도를 실제 도로망/GIS 최적화로 주장하지 않는다. 외부 패키지나 모델 API 설치 불필요; Node.js 내장 기능으로 작성한다.
기존 legacy-estimate.mjs는 의도적으로 검증되지 않은 회귀 시작점이다. 독립 QA가 먼저 이 계산을 실행하여 비용·용량 기준 대비 실패를 확인하고 증거를 남겨야 한다. 그 후 개발 직원에게 같은 목표의 수정 작업을 맡기고 QA 재검수→PM 승인으로 끝낸다. PM이 직접 코드와 검수 결과를 대신 작성하지 않는다. 무한 재시도·중복 배정 금지.
수용 기준: 총수요=sum(demand), 용량미달 제외, 후보비용=fixedCost+sum(demand_i*unitCost_i), feasible 후보 최소비용, 고정 기준안 A와 절감액/비율, 입력오류 및 feasible없음 구분, 독립 수계산 및 테스트. 실제 최적 후보는 이 수식과 자료로 계산할 것. 결과를 과장하지 않는다.
출력: supply-chain-trial 안의 계산 모듈, Node 테스트, 입력과 후보/통계량을 확인할 수 있는 self-contained HTML, QA-before.md, QA-after.md, PM-report.md. HTML의 지도/위치는 경위도 도식 또는 명시적 합성도식이어도 되며 실제 GIS/도로거리라 부르지 않는다. 외부게시/기존 운영앱 변경 금지.
team.json에서 실제 PM/개발/검수 ID를 읽고 원래 parentId로 하위 작업을 생성한다. 초기 QA는 canAssignTasks:false이므로 검증 코멘트/산출물로 PM에 반환한다. 개발 하위 이슈에는 실행정책을 지정한다:
executionPolicy={mode:'normal',commentRequired:true,maxReviewRounds:2,stages:[{type:'review',participants:[{type:'agent',agentId:QA_ID}]},{type:'approval',participants:[{type:'agent',agentId:PM_ID}]}]}.
각 단계는 자기 run 인증으로 실제 API를 사용하며 board인 척하지 않는다. 아직 끝나지 않은 자식이 있으면 부모를 완료하지 않는다. 후속 자식 완료 시 PM이 실제로 다시 검토한다. 적절한 blockedByIssueIds/검수 wake를 사용하고 polling하지 않는다.
gstack 방법론은 PM의 plan-eng-review와 QA의 review에서 관련 부분만 읽어 사용한 내용과 한계를 보고에 기록한다. 현존하는 지시·자료로 답할 수 있는 질문을 대표에게 되묻지 않는다. 최종보고는 한국어로 결과/검증/제약/다음 행동만 간결하게 남긴다.`;
await writeFile(path.join(target,'BRIEF.md'),brief);
const response=await fetch(cfg.paperclipUrl+`/api/companies/${cfg.companyId}/issues`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({title:'새 사무실 검증: 공급망 입지 비교를 QA→개발→재검수→PM 승인으로 납품',description:brief,assigneeAgentId:cfg.agentIds.pm,status:'todo',priority:'high',idempotencyKey:'office-next-supply-chain-trial-v1'}),signal:AbortSignal.timeout(30000)});
if(!response.ok)throw new Error(`Trial creation failed: ${response.status}: ${(await response.text()).slice(0,600)}`);
const issue=await response.json();
const record={issueId:issue.id,identifier:issue.identifier,createdAt:issue.createdAt,companyId:issue.companyId,humanAttribution:issue.responsibleUserId ?? issue.createdByUserId ?? null,synthetic:true};
await writeFile(trialFile,JSON.stringify(record,null,2));console.log(JSON.stringify(record));
