"""Offline geographic network scaffold, separate from the inventory prototype.

This seed establishes a bounded model/UI contract. It is not a researched client
case or finished employee deliverable; PM-selected evidence and scope must be
implemented by the worker and independently reviewed.
"""

PROFILE = "supply-network-gis-v1"
REQUIRED_IDS = {"model-form", "run-model", "model-results", "model-checks", "model-scene",
                "network-map", "top-kpis", "scenario-select", "demand-multiplier", "capacity-multiplier",
                "max-hubs", "disruption-hub", "disruption-days", "seed", "days"}

README = """# DAS Lab 공급망 입지·네트워크·배송 시뮬레이션 기초

이 파일은 프로필 계약과 합성 기본 모델이다. 완성된 산업 사례나 고객 납품물이 아니다.
PM이 조사한 실제 문제·목적·출처·데이터 상태·완료 기준을 반영해 소스와 이 문서를 수정해야 한다.
문제 선정, 산업 자료의 근거, 비교 대안의 타당성은 이 기본 모델만으로 검증되지 않는다.

## 고정 실행 계약
브라우저 window.DASNetwork.run(params)는 JSON으로 직렬화할 수 있는 결과를 반환한다.
params: seed=42, days=7(1~30), demandMultiplier=1(0~3), capacityMultiplier=1(0~3),
maxHubs=2(1~시설 수), disruptionHub='' 또는 거점 id, disruptionDays=0(0~days).
선택 data: facilities[{id,name,lon,lat,capacityPerDay,fixedCostPerDay}],
customers[{id,name,lon,lat,demandPerDay}], provenance 객체. 시설2~5개·수요지1~8개.
합성 입력은 provenance.kind='synthetic'를 보존한다. source/date/description 등 메타데이터를 추가할 수 있다.
data를 생략한 run의 기본 데이터와 최초 화면의 기본 사례는 같아야 한다. 별도 검사용 사례는 명시적 data로 선택한다.
EPSG:4326 위경도. 기본 좌표는 도시권 근처의 예시 위치, 수요·용량·비용은 합성이다.
도로망·지형·고객 실제 자료가 아니다. 지도에 위경도 좌표망과 실제 모델의 연결을 표시한다.

run 반환: profile, parameters, data, baseline, optimized, candidates.
각 대안은 selectedHubs, planning, simulation으로 구성한다.
planning.allocations: hubId,customerId,unitsPerDay,distanceKm,unitCost.
planning.unserved: customerId,unitsPerDay.
planning: fixedCostPerDay,transportCostPerDay,unservedPenaltyPerDay,totalCostPerDay,
servedUnitsPerDay,totalDemandPerDay. 수요·용량은 Math.round(기준값*배율).
거리 Haversine(지구반경6371km), 단위 운송비=거리*0.12, 미충족 비용=200/개/일.
baseline은 데이터 첫·마지막 시설을 maxHubs 범위에서 선택한다.
optimized는 공집합을 포함한 maxHubs 이하 모든 시설 부분집합을 열거하고,
각 부분집합의 정수 용량제약 최소비용 배분을 잔여 그래프 최단경로로 계산한다.
정의한 작은 단일기간 문제에서의 최적화이며 도로·재고·다기간 입지 최적화가 아니다.

simulation.orders: id='d{day}-{customerId}-{ordinal}',day(0부터),customerId,hubId 또는 null,
quantity=1,distanceKm,createdAt,dueAt,exogenousDelayHours,dispatchedAt/deliveredAt 또는 null,
unservedReason. 매일 고정 수요이며 계획 배분을 hubId 순서로 적용한다.
createdAt=day*24+8+ordinal*0.01, dueAt=createdAt+8, dispatchedAt=createdAt+1,
deliveredAt=dispatchedAt+distanceKm/50+exogenousDelayHours.
외생 지연은 시드·날짜·고객·주문번호 기반 [0,3)시간으로 대안에 공통이다.
장애 거점은 처음 disruptionDays일 출고 불가. 긴급 재배정·재고이월은 없다.
daily: day,demand,delivered,onTime,unserved. kpis: demand,delivered,unserved,onTime,
serviceRate,onTimeRate,meanLeadHours,fixedCost,transportCost,penaltyCost,totalCost.
서비스·정시율 분모는 전체 수요. 수요0은 비율0, 평균리드0으로 표시하며 달성 실적으로 해석하지 않는다. 정시=deliveredAt<=dueAt.
고정비는 선택거점 일비용*days, 운송비는 실제 출고 물량, 벌점은 미충족 물량만 계상한다.

## 화면 계약
model-form/run-model/model-results/model-checks/model-scene를 유지한다.
network-map SVG는 위경도 기반이며 data-selected-hubs는 선택id의 쉼표목록이다.
거점/고객은 data-hub-id/data-customer-id 및 data-lon/data-lat,
운송선은 data-route-hub/data-route-customer/data-units를 가진다.
모든 거점·수요 marker는 circle(cx,cy)이다. 좌표 범위는 전체 점의 경도 최소/최대에
-.35/+.35, 위도 최소/최대에 -.3/+.3을 더한 west/east/south/north이다.
투영식 x=55+(lon-west)/(east-west)*790, y=490-(lat-south)/(north-south)*430.
viewBox=0 0 900 540이며 회전·중첩 변환 없이 경로와 점에 같은 식을 쓴다.
top-kpis의 data-metric와 data-value는 비용·비율·주문 수의 원시 수치를 표시 문구와 함께 보존한다.
top-kpis와 model-results의 data-scenario/data-total-cost/data-service-rate/data-on-time-rate/
data-demand/data-delivered는 현재 scenario-select(baseline/optimized)와 일치해야 한다.
수요·용량·거점수·장애·시드·기간 입력을 바꾼 뒤 run-model로 실제 결과를 다시 계산한다.
직원 코드는 이 공개 계약을 보존한다. 모델 계산과 화면 표현 외 주장을 검사 통과로 대신하지 않는다.

## 작업과 검증
6개 파일만 수정. 외부 패키지·네트워크·설치·원격 지도·고객 접촉·공개 배포 금지.
최종 파일은 README를 포함해 각각 500,000바이트 이하, 전체 1,000,000바이트 이하이다.
model.test.cjs의 정확한 require는 ./model.js, node:assert/strict, node:fs, node:vm만 허용한다.
node:crypto·동적 require·그 외 import는 허용하지 않는다. 검증 기록은 핵심 지표·조건·판정의 간결한 요약으로 남긴다.
공개 조사 결과는 PM이 제공한 근거를 출처·날짜와 함께 반영한다. 자료 부족은 명시한다.
생성한 model.test.cjs는 허용된 직원 샌드박스 안에서만 실행한다.
서버는 파일·문법 검사 후 격리 브라우저에서 별도의 모델·화면 검사를 수행한다.
기본 seed의 검사는 구현 연결의 검증이며 고객 효과·제품 완성도 검증이 아니다.
"""

MODEL = r'''"use strict";
(function (root) {
  const PROFILE = "supply-network-gis-v1", RATE = 0.12, PENALTY = 200;
  const DEFAULT_DATA = {
    crs: "EPSG:4326",
    provenance: {kind:"synthetic", description:"도시권 근처 예시 좌표와 합성 수요·용량·가상 비용. 도로·고객 자료 아님.", source:"DAS Lab bounded scaffold", date:"2026-09-30"},
    facilities: [
      {id:"H1",name:"수도권 후보",lon:127.05,lat:37.45,capacityPerDay:90,fixedCostPerDay:160},
      {id:"H2",name:"중부권 후보",lon:127.40,lat:36.35,capacityPerDay:110,fixedCostPerDay:130},
      {id:"H3",name:"영남 내륙 후보",lon:128.60,lat:35.87,capacityPerDay:100,fixedCostPerDay:120},
      {id:"H4",name:"동남권 후보",lon:129.07,lat:35.18,capacityPerDay:80,fixedCostPerDay:150}
    ],
    customers: [
      {id:"D1",name:"수도권 수요",lon:126.98,lat:37.56,demandPerDay:40},
      {id:"D2",name:"서부권 수요",lon:126.71,lat:37.46,demandPerDay:25},
      {id:"D3",name:"중부권 수요",lon:127.43,lat:36.32,demandPerDay:30},
      {id:"D4",name:"호남권 수요",lon:126.85,lat:35.16,demandPerDay:20},
      {id:"D5",name:"영남권 수요",lon:128.61,lat:35.88,demandPerDay:30},
      {id:"D6",name:"동남권 수요",lon:129.06,lat:35.16,demandPerDay:35}
    ]
  };
  const clone = value => JSON.parse(JSON.stringify(value));
  function number(value, fallback, low, high, integer=false) {
    const n = value === undefined ? fallback : Number(value);
    if (!Number.isFinite(n) || n < low || n > high || (integer && !Number.isInteger(n))) throw new Error("입력 범위를 확인하세요.");
    return n;
  }
  function dataFor(input) {
    const data = clone(input || DEFAULT_DATA);
    if (!Array.isArray(data.facilities) || data.facilities.length < 2 || data.facilities.length > 5 || !Array.isArray(data.customers) || !data.customers.length || data.customers.length > 8) throw new Error("유한한 거점·수요지 자료가 필요합니다.");
    const ids = new Set();
    for (const row of [...data.facilities, ...data.customers]) {
      if (!/^[A-Za-z0-9_-]{1,30}$/.test(row.id) || ids.has(row.id) || typeof row.name !== "string" || row.name.length > 80) throw new Error("거점·수요지 식별자가 올바르지 않습니다.");
      ids.add(row.id);
      if(typeof row.lon!=="number"||typeof row.lat!=="number") throw new Error("숫자 위경도 좌표가 필요합니다.");
      number(row.lon,0,-180,180); number(row.lat,0,-80,80);
    }
    for (const f of data.facilities) {if(typeof f.capacityPerDay!=="number"||typeof f.fixedCostPerDay!=="number")throw new Error("숫자 용량·비용이 필요합니다.");number(f.capacityPerDay,0,0,1000,true); number(f.fixedCostPerDay,0,0,100000);}
    for (const c of data.customers) {if(typeof c.demandPerDay!=="number")throw new Error("숫자 수요가 필요합니다.");number(c.demandPerDay,0,0,1000,true);}
    data.crs = "EPSG:4326";
    data.provenance = data.provenance || {kind:"unverified",description:"입력 자료의 출처를 확인하지 못했습니다."};
    return data;
  }
  function distance(a,b) {
    const rad=Math.PI/180, dy=(b.lat-a.lat)*rad, dx=(b.lon-a.lon)*rad;
    const z=Math.sin(dy/2)**2+Math.cos(a.lat*rad)*Math.cos(b.lat*rad)*Math.sin(dx/2)**2;
    return 6371*2*Math.asin(Math.min(1,Math.sqrt(z)));
  }
  function plan(data,p,selected) {
    const customers=data.customers, hubs=data.facilities.filter(f=>selected.includes(f.id));
    const sink=customers.length+hubs.length+1, graph=Array.from({length:sink+1},()=>[]), routes=[], missed=[];
    const add=(from,to,cap,cost)=>{
      const forward={to,cap,cost,initial:cap,rev:graph[to].length}, backward={to:from,cap:0,cost:-cost,initial:0,rev:graph[from].length};
      graph[from].push(forward); graph[to].push(backward); return forward;
    };
    let totalDemand=0;
    customers.forEach((c,ci)=>{
      const demand=Math.round(c.demandPerDay*p.demandMultiplier); totalDemand+=demand;
      add(0,ci+1,demand,0);
      hubs.forEach((h,hi)=>{const km=distance(h,c); routes.push({hubId:h.id,customerId:c.id,distanceKm:km,unitCost:km*RATE,edge:add(ci+1,customers.length+1+hi,demand,km*RATE)});});
      missed.push({customerId:c.id,edge:add(ci+1,sink,demand,PENALTY)});
    });
    hubs.forEach((h,hi)=>add(customers.length+1+hi,sink,Math.round(h.capacityPerDay*p.capacityMultiplier),0));
    let left=totalDemand;
    while(left>0) {
      const distances=Array(graph.length).fill(Infinity), previous=Array(graph.length).fill(null); distances[0]=0;
      for(let iteration=0;iteration<graph.length-1;iteration++) {
        let changed=false;
        graph.forEach((edges,from)=>edges.forEach((edge,index)=>{
          if(edge.cap>0 && distances[from]+edge.cost<distances[edge.to]-1e-9) {distances[edge.to]=distances[from]+edge.cost;previous[edge.to]=[from,index];changed=true;}
        }));
        if(!changed) break;
      }
      if(!previous[sink]) throw new Error("수요 배분 경로가 없습니다.");
      let quantity=left;
      for(let at=sink;at!==0;) {const [from,index]=previous[at];quantity=Math.min(quantity,graph[from][index].cap);at=from;}
      for(let at=sink;at!==0;) {const [from,index]=previous[at],edge=graph[from][index];edge.cap-=quantity;graph[at][edge.rev].cap+=quantity;at=from;}
      left-=quantity;
    }
    const allocations=routes.map(({edge,...row})=>({...row,unitsPerDay:edge.initial-edge.cap})).filter(a=>a.unitsPerDay>0).sort((a,b)=>a.hubId.localeCompare(b.hubId)||a.customerId.localeCompare(b.customerId));
    const unserved=missed.map(({edge,...row})=>({...row,unitsPerDay:edge.initial-edge.cap}));
    const fixedCostPerDay=hubs.reduce((n,h)=>n+h.fixedCostPerDay,0), transportCostPerDay=allocations.reduce((n,a)=>n+a.unitsPerDay*a.unitCost,0), unservedPenaltyPerDay=unserved.reduce((n,u)=>n+u.unitsPerDay*PENALTY,0);
    return {allocations,unserved,fixedCostPerDay,transportCostPerDay,unservedPenaltyPerDay,totalCostPerDay:fixedCostPerDay+transportCostPerDay+unservedPenaltyPerDay,
      servedUnitsPerDay:allocations.reduce((n,a)=>n+a.unitsPerDay,0),totalDemandPerDay:totalDemand};
  }
  function delay(seed,day,id,ordinal) {
    let h=(2166136261 ^ seed)>>>0;
    for(const ch of `${day}:${id}:${ordinal}`) h=Math.imul(h^ch.charCodeAt(0),16777619)>>>0;
    h^=h>>>16;h=Math.imul(h,2246822507)>>>0;h^=h>>>13;
    return (h>>>0)/4294967296*3;
  }
  function simulate(data,p,planning) {
    const orders=[],daily=[];
    for(let day=0;day<p.days;day++) {
      const row={day,demand:0,delivered:0,onTime:0,unserved:0};
      for(const customer of data.customers) {
        const assigned=planning.allocations.filter(a=>a.customerId===customer.id), slots=[];
        for(const route of assigned) for(let n=0;n<route.unitsPerDay;n++) slots.push(route);
        const quantity=Math.round(customer.demandPerDay*p.demandMultiplier);
        for(let ordinal=0;ordinal<quantity;ordinal++) {
          const route=slots[ordinal], blocked=route&&route.hubId===p.disruptionHub&&day<p.disruptionDays;
          const createdAt=day*24+8+ordinal*0.01, exogenousDelayHours=delay(p.seed,day,customer.id,ordinal), dispatchedAt=route&&!blocked?createdAt+1:null;
          const deliveredAt=dispatchedAt===null?null:dispatchedAt+route.distanceKm/50+exogenousDelayHours;
          const order={id:`d${day}-${customer.id}-${ordinal}`,day,customerId:customer.id,hubId:route?route.hubId:null,quantity:1,distanceKm:route?route.distanceKm:0,createdAt,dueAt:createdAt+8,exogenousDelayHours,dispatchedAt,deliveredAt,unservedReason:!route?"capacity":blocked?"hub-disruption":null};
          orders.push(order);row.demand++;
          if(deliveredAt!==null) {row.delivered++;if(deliveredAt<=order.dueAt) row.onTime++;} else row.unserved++;
        }
      }
      daily.push(row);
    }
    const demand=orders.length, delivered=orders.filter(o=>o.deliveredAt!==null), onTime=delivered.filter(o=>o.deliveredAt<=o.dueAt).length;
    const fixedCost=planning.fixedCostPerDay*p.days, transportCost=delivered.reduce((n,o)=>n+o.distanceKm*RATE,0), penaltyCost=(demand-delivered.length)*PENALTY;
    const kpis={demand,delivered:delivered.length,unserved:demand-delivered.length,onTime,
      serviceRate:demand?delivered.length/demand:0,onTimeRate:demand?onTime/demand:0,
      meanLeadHours:delivered.length?delivered.reduce((n,o)=>n+o.deliveredAt-o.createdAt,0)/delivered.length:0,
      fixedCost,transportCost,penaltyCost,totalCost:fixedCost+transportCost+penaltyCost};
    return {orders,daily,kpis};
  }
  function run(input={}) {
    const data=dataFor(input.data), p={seed:number(input.seed,42,0,2147483647,true),days:number(input.days,7,1,30,true),demandMultiplier:number(input.demandMultiplier,1,0,3),capacityMultiplier:number(input.capacityMultiplier,1,0,3),maxHubs:number(input.maxHubs,2,1,data.facilities.length,true),disruptionHub:input.disruptionHub||"",disruptionDays:number(input.disruptionDays,0,0,30,true)};
    if(p.disruptionDays>p.days || (p.disruptionHub&&!data.facilities.some(f=>f.id===p.disruptionHub))) throw new Error("장애 거점·기간을 확인하세요.");
    if(data.customers.reduce((n,c)=>n+Math.round(c.demandPerDay*p.demandMultiplier),0)*p.days>20000) throw new Error("시연 주문은 20,000개 이내로 제한합니다.");
    const candidates=[];let best=null;
    for(let mask=0;mask<2**data.facilities.length;mask++) {
      const selectedHubs=data.facilities.filter((_,index)=>mask&(1<<index)).map(f=>f.id);
      if(selectedHubs.length>p.maxHubs) continue;
      const planning=plan(data,p,selectedHubs), alternative={selectedHubs,planning};
      candidates.push({selectedHubs:[...selectedHubs],totalCostPerDay:planning.totalCostPerDay});
      if(!best||planning.totalCostPerDay<best.planning.totalCostPerDay-1e-7) best=alternative;
    }
    const baselineHubs=[data.facilities[0].id,data.facilities[data.facilities.length-1].id].slice(0,p.maxHubs);
    const baseline={selectedHubs:baselineHubs,planning:plan(data,p,baselineHubs)};
    baseline.simulation=simulate(data,p,baseline.planning);best.simulation=simulate(data,p,best.planning);
    return {profile:PROFILE,parameters:p,data,baseline,optimized:best,candidates,
      assumptions:["단일 품목·일일 고정 수요", "위경도 대권거리; 실제 도로·속도 자료 미반영", "합성 지연·처리량과 가상 비용", "장애 발생 시 재배정·재고이월 없음", "배송 완료시각을 끝까지 관찰; 기간은 주문 발생일 기준"],
      optimality:{scope:"bounded_single_period_capacitated_location",method:"facility_subset_enumeration_and_integer_min_cost_flow",industrialValidity:false}};
  }
  const api={run,defaults:()=>clone(DEFAULT_DATA)};
  if(typeof module!=="undefined"&&module.exports) module.exports=api;
  root.DASNetwork=api;
})(typeof window!=="undefined"?window:globalThis);
'''

HTML = '''<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>DAS Lab · 공급망 의사결정 실험</title><link rel="stylesheet" href="style.css"><script src="model.js" defer></script><script src="app.js" defer></script></head>
<body><main><header><p class="eyebrow">DAS LAB / NETWORK LAB</p><h1>어디서, 얼마나 공급할 것인가</h1><p>거점 선택 → 용량 배분 → 배송 지연을 같은 조건에서 비교합니다.</p><p class="notice">합성 모델 기초 · PM 조사와 산업 문제 반영이 필요한 작업본</p></header>
<form id="model-form"><label>수요 배율<input id="demand-multiplier" type="number" min="0" max="3" step="0.1" value="1"></label><label>용량 배율<input id="capacity-multiplier" type="number" min="0" max="3" step="0.1" value="1"></label><label>최대 거점 수<input id="max-hubs" type="number" min="1" max="4" value="2"></label><label>관측 주문 일수<input id="days" type="number" min="1" max="30" value="7"></label><label>시드<input id="seed" type="number" min="0" max="2147483647" value="42"></label><label>장애 거점<select id="disruption-hub"><option value="">없음</option><option value="H1">수도권</option><option value="H2">중부권</option><option value="H3">영남 내륙</option><option value="H4">동남권</option></select></label><label>장애 일수<input id="disruption-days" type="number" min="0" max="30" value="0"></label><button id="run-model" type="submit">다시 계산</button></form>
<div class="compare"><label>지도·지표에 표시할 대안 <select id="scenario-select"><option value="optimized">최적화안</option><option value="baseline">기준안</option></select></label><p id="comparison-summary"></p></div>
<section id="model-results"><div id="top-kpis" aria-live="polite"></div><div class="workspace"><section id="model-scene"><h2>거점·수요·공급 연결</h2><svg id="network-map" viewBox="0 0 900 540" role="img" aria-label="위경도 좌표 기반 공급망 지도"></svg><p class="legend">큰 ● 선택 거점 · ○ 후보 거점 · 작은 주황 ● 수요지 · 선 굵기: 일일 배분량</p><p class="fine">EPSG:4326 좌표망. 예시 위치이며 도로·지형 지도는 포함하지 않았습니다.</p></section><aside><h2>현재 의사결정</h2><div id="selection-detail"></div><h3>해석 조건</h3><ul id="assumptions"></ul><p id="data-provenance"></p></aside></div><section><h2>기준안과 최적화안</h2><div class="table-wrap"><table><thead><tr><th>대안</th><th>선택 거점</th><th>기간 비용</th><th>서비스율</th><th>정시율</th><th>평균 리드타임</th></tr></thead><tbody id="scenario-table"></tbody></table></div></section></section>
<p id="model-checks" role="status">계산 준비 중</p><footer>목표와 자료의 적합성은 PM 검토 대상입니다. 검사 통과가 고객 효과·실제 최적 입지를 보장하지 않습니다.</footer></main></body></html>'''

CSS = '''*{box-sizing:border-box}body{margin:0;background:#edf2f4;color:#193439;font-family:Arial,"Malgun Gothic",sans-serif}main{max-width:1440px;margin:auto;padding:28px}header{padding:12px 0 24px}h1{font-size:clamp(26px,4vw,42px);margin:12px 0}h2{font-size:19px;margin:0 0 16px}h3{font-size:15px}.eyebrow{font-size:12px;letter-spacing:.16em;color:#227569}.notice{display:inline-block;padding:8px 12px;background:#fff0d9;border-radius:6px;font-size:13px}form{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;padding:20px;background:#fff;border:1px solid #d6e1e4;border-radius:12px}label{display:flex;gap:7px;flex-direction:column;font-size:13px;color:#3f5b61}input,select,button{font:inherit;min-width:0;border:1px solid #bbcdd1;border-radius:5px;padding:10px;background:#fff;color:#193439}button{background:#135d58;color:#fff;cursor:pointer;align-self:end}.compare{display:flex;justify-content:space-between;align-items:center;gap:18px;margin:20px 0}.compare label{min-width:220px}#comparison-summary{font-size:14px;color:#48676b}#top-kpis{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:12px;margin:18px 0}.kpi{background:#fff;border:1px solid #d6e1e4;border-radius:10px;padding:18px}.kpi small{display:block;color:#527076;font-size:12px}.kpi strong{display:block;font-size:25px;margin:9px 0 4px}.kpi span{font-size:11px;color:#527076}.workspace{display:grid;grid-template-columns:3fr 1fr;gap:18px;margin-bottom:22px}section>section,aside,#model-scene{background:#fff;border:1px solid #d6e1e4;border-radius:12px;padding:20px;min-width:0}#network-map{display:block;width:100%;height:auto;max-height:580px;background:#eef5f4;border:1px solid #dce7e6;border-radius:8px}#network-map .grid{stroke:#d7e3e2;stroke-width:1}#network-map .route{stroke:#2b8c80;opacity:.42}#network-map text{font-size:12px;fill:#3b5e60}#network-map .hub{stroke:#174f4a;stroke-width:2;fill:white}#network-map .selected{fill:#19786d}#network-map .customer{fill:#c06c30;stroke:#fff;stroke-width:1.5}.legend,.fine,footer{font-size:12px;color:#5c7376;line-height:1.6}aside ul{padding-left:18px;font-size:13px;line-height:1.7}#selection-detail{font-size:14px;line-height:1.8}#data-provenance{font-size:12px;padding:12px;background:#f5f6f0;line-height:1.6}.table-wrap{overflow:auto}table{width:100%;border-collapse:collapse;font-size:13px}td,th{text-align:left;border-bottom:1px solid #e1e9eb;padding:12px;white-space:nowrap}th{color:#627a7e;font-weight:400}#model-checks{font-size:13px;padding:12px;color:#335954}footer{margin-top:24px}@media(max-width:800px){main{padding:16px}.workspace{grid-template-columns:1fr}form{grid-template-columns:repeat(2,minmax(0,1fr))}#top-kpis{grid-template-columns:repeat(2,minmax(0,1fr))}.compare{align-items:stretch;flex-direction:column}.kpi strong{font-size:23px}}'''

APP = r'''"use strict";
(() => {
  const byId=id=>document.getElementById(id), format=(n,d=0)=>Number(n).toLocaleString("ko-KR",{maximumFractionDigits:d});
  let result;
  function element(tag,text,className) {const node=document.createElement(tag);if(text!==undefined)node.textContent=text;if(className)node.className=className;return node;}
  function svg(tag,attrs,text) {const node=document.createElementNS("http://www.w3.org/2000/svg",tag);Object.entries(attrs).forEach(([key,value])=>node.setAttribute(key,String(value)));if(text!==undefined)node.textContent=text;return node;}
  function map(current) {
    const map=byId("network-map");map.replaceChildren();map.dataset.selectedHubs=current.selectedHubs.join(",");
    const points=[...result.data.facilities,...result.data.customers], west=Math.min(...points.map(p=>p.lon))-.35,east=Math.max(...points.map(p=>p.lon))+.35,south=Math.min(...points.map(p=>p.lat))-.3,north=Math.max(...points.map(p=>p.lat))+.3;
    const project=p=>({x:55+(p.lon-west)/(east-west)*790,y:490-(p.lat-south)/(north-south)*430});
    for(let i=0;i<=4;i++) {const x=55+i*790/4,y=60+i*430/4;map.append(svg("line",{x1:x,x2:x,y1:45,y2:495,class:"grid"}),svg("line",{x1:45,x2:855,y1:y,y2:y,class:"grid"}),svg("text",{x:x-18,y:520},format(west+(east-west)*i/4,2)+"°E"),svg("text",{x:5,y:y+4},format(north-(north-south)*i/4,2)+"°N"));}
    for(const route of current.planning.allocations) {const hub=result.data.facilities.find(f=>f.id===route.hubId),customer=result.data.customers.find(c=>c.id===route.customerId),a=project(hub),b=project(customer);const line=svg("line",{x1:a.x,y1:a.y,x2:b.x,y2:b.y,class:"route","stroke-width":1+Math.sqrt(route.unitsPerDay),"data-route-hub":hub.id,"data-route-customer":customer.id,"data-units":route.unitsPerDay});line.append(svg("title",{},`${hub.name} → ${customer.name}: ${route.unitsPerDay}개/일, ${format(route.distanceKm,1)}km`));map.append(line);}
    for(const c of result.data.customers) {const p=project(c),node=svg("circle",{cx:p.x,cy:p.y,r:5,class:"customer","data-customer-id":c.id,"data-lon":c.lon,"data-lat":c.lat});node.append(svg("title",{},`${c.name}: ${Math.round(c.demandPerDay*result.parameters.demandMultiplier)}개/일`));map.append(node,svg("text",{x:p.x+10,y:p.y+17},c.name));}
    for(const h of result.data.facilities) {const p=project(h),chosen=current.selectedHubs.includes(h.id),node=svg("circle",{cx:p.x,cy:p.y,r:chosen?9:6,class:"hub"+(chosen?" selected":""),"data-hub-id":h.id,"data-lon":h.lon,"data-lat":h.lat,"data-selected":chosen});node.append(svg("title",{},`${h.name}: ${chosen?"선택":"후보"}, 용량 ${Math.round(h.capacityPerDay*result.parameters.capacityMultiplier)}개/일`));map.append(node,svg("text",{x:p.x+12,y:p.y-10},h.name));}
  }
  function render() {
    const scenario=byId("scenario-select").value,current=result[scenario],k=current.simulation.kpis,container=byId("model-results");
    Object.assign(container.dataset,{scenario,totalCost:k.totalCost,serviceRate:k.serviceRate,onTimeRate:k.onTimeRate,demand:k.demand,delivered:k.delivered});
    const cards=[["기간 총비용",format(k.totalCost),"가상 비용 단위 · 미충족 포함"],["서비스율",format(k.serviceRate*100,1)+"%",`${format(k.delivered)} / ${format(k.demand)}개 배송`],["정시율",format(k.onTimeRate*100,1)+"%","전체 수요 중 약속 시각 내 배송"],["평균 리드타임",format(k.meanLeadHours,1)+" h","배송 완료 주문 기준"],["미충족 수요",format(k.unserved)+"개",`${current.selectedHubs.length}개 거점 선택`]];
    const keys=["totalCost","serviceRate","onTimeRate","meanLeadHours","unserved"];
    byId("top-kpis").replaceChildren(...cards.map(([title,value,note],index)=>{const card=element("div",undefined,"kpi"),strong=element("strong",value),sub=element("span",note);strong.dataset.metric=keys[index];strong.dataset.value=k[keys[index]];if(index===1){const count=element("span",format(k.delivered)),total=element("span",format(k.demand));count.dataset.metric="delivered";count.dataset.value=k.delivered;total.dataset.metric="demand";total.dataset.value=k.demand;sub.replaceChildren(count,document.createTextNode(" / "),total,document.createTextNode("개 배송"));}card.append(element("small",title),strong,sub);return card;}));
    const delta=result.baseline.planning.totalCostPerDay-result.optimized.planning.totalCostPerDay;
    byId("comparison-summary").textContent=`정상 조건 계획 비용 차이 ${format(delta,1)} /일 · 장애·지연은 아래 배송 실험에 반영`;
    byId("selection-detail").replaceChildren(element("p",`선택: ${current.selectedHubs.map(id=>result.data.facilities.find(f=>f.id===id).name).join(" · ")||"없음"}`),element("p",`계획 배분 ${format(current.planning.servedUnitsPerDay)} / ${format(current.planning.totalDemandPerDay)}개/일`),element("p",`전체 ${result.candidates.length}개 입지 조합을 같은 제약으로 비교했습니다.`));
    byId("assumptions").replaceChildren(...result.assumptions.map(text=>element("li",text)));
    byId("data-provenance").textContent=result.data.provenance.description||"출처 미확인 자료";
    byId("scenario-table").replaceChildren(...["baseline","optimized"].map(name=>{const a=result[name],v=a.simulation.kpis,row=element("tr");row.dataset.scenario=name;for(const value of [name==="baseline"?"기준안":"최적화안",a.selectedHubs.join(", ")||"없음",format(v.totalCost,1),format(v.serviceRate*100,1)+"%",format(v.onTimeRate*100,1)+"%",format(v.meanLeadHours,2)+" h"])row.append(element("td",value));return row;}));
    map(current);byId("model-checks").textContent="계산과 지도 갱신 완료 · 독립 서버 검수와 산업 타당성 검토는 별도입니다.";
  }
  function calculate(event) {
    if(event)event.preventDefault();
    try {result=window.DASNetwork.run({seed:+byId("seed").value,days:+byId("days").value,demandMultiplier:+byId("demand-multiplier").value,capacityMultiplier:+byId("capacity-multiplier").value,maxHubs:+byId("max-hubs").value,disruptionHub:byId("disruption-hub").value,disruptionDays:+byId("disruption-days").value});render();}
    catch(error){byId("model-checks").textContent="계산 중지: "+error.message;}
  }
  byId("model-form").addEventListener("submit",calculate);byId("scenario-select").addEventListener("change",()=>{if(result)render();});calculate();
})();
'''

TEST = '''"use strict";
// Run only in the authorized worker sandbox. Host checks syntax, never executes.
const assert = require("node:assert/strict");
const model = require("./model.js");
const result = model.run({seed:42});
assert.deepEqual(result, model.run({seed:42}));
assert.ok(result.optimized.planning.totalCostPerDay <= result.baseline.planning.totalCostPerDay + 1e-7);
for (const name of ["baseline","optimized"]) {
  const k=result[name].simulation.kpis;
  assert.equal(k.demand,k.delivered+k.unserved);
  assert.ok(k.onTime<=k.delivered);
}
'''

SEED = {"README.md": README, "index.html": HTML, "style.css": CSS, "model.js": MODEL, "app.js": APP, "model.test.cjs": TEST}
