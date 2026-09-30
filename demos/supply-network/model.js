"use strict";
(function (root) {
  const PROFILE = "supply-network-gis-v1", RATE = 0.12, PENALTY = 200;
  const REGIONAL_DATA = {
    crs: "EPSG:4326",
    provenance: {kind:"synthetic",synthetic:true,version:"KR-regional-2026-09-30",coordinateSystem:"EPSG:4326",quantityUnit:"item",capacityUnit:"item/day",costUnit:"synthetic_cost_unit",timeUnit:"hour",verification:"synthetic_not_field_validated", description:"도시권 근처 예시 좌표와 합성 수요·용량·가상 비용. 도로·고객 자료 아님.", source:"DAS Lab bounded scaffold", date:"2026-09-30"},
    facilities: [
      {id:"H1",name:"성남 후보",lon:127.05,lat:37.45,capacityPerDay:90,fixedCostPerDay:160},
      {id:"H2",name:"대전 후보",lon:127.40,lat:36.35,capacityPerDay:110,fixedCostPerDay:130},
      {id:"H3",name:"대구 후보",lon:128.60,lat:35.87,capacityPerDay:100,fixedCostPerDay:120},
      {id:"H4",name:"부산 후보",lon:129.07,lat:35.18,capacityPerDay:80,fixedCostPerDay:150}
    ],
    customers: [
      {id:"D1",name:"서울 수요",lon:126.98,lat:37.56,demandPerDay:40},
      {id:"D2",name:"인천 수요",lon:126.71,lat:37.46,demandPerDay:25},
      {id:"D3",name:"대전 수요",lon:127.43,lat:36.32,demandPerDay:30},
      {id:"D4",name:"광주 수요",lon:126.85,lat:35.16,demandPerDay:20},
      {id:"D5",name:"대구 수요",lon:128.61,lat:35.88,demandPerDay:30},
      {id:"D6",name:"부산 수요",lon:129.06,lat:35.16,demandPerDay:35}
    ]
  };
  const provenance = version => ({kind:"synthetic",synthetic:true,version,source:"DAS-RD internal synthetic fixture",createdDate:"2026-09-30",verification:"synthetic_fixture",coordinateSystem:"EPSG:4326",quantityUnit:"item",capacityUnit:"item/day",costUnit:"synthetic_cost_unit",timeUnit:"hour",description:"합성 검증용 좌표·수요·용량·비용. 실제 시설·도로·고객 자료 아님."});
  const DEFAULT_DATA = {provenance:provenance("S2-contract-2026-09-30"),facilities:[
    {id:"F1",name:"합성 남측 거점",lon:127,lat:37,capacityPerDay:8,fixedCostPerDay:2},
    {id:"F2",name:"합성 중앙 거점",lon:127,lat:37.03,capacityPerDay:8,fixedCostPerDay:2},
    {id:"F3",name:"합성 북측 거점",lon:127,lat:37.06,capacityPerDay:8,fixedCostPerDay:2}],customers:[
    {id:"D1",name:"합성 남측 수요지",lon:127,lat:37.01,demandPerDay:4},
    {id:"D2",name:"합성 북측 수요지",lon:127,lat:37.05,demandPerDay:4}]};
  const R2 = {provenance:provenance("R2-contract-2026-09-30"),facilities:[
    {id:"A",name:"합성 A",lon:127,lat:37,capacityPerDay:1,fixedCostPerDay:0},
    {id:"B",name:"합성 B",lon:127,lat:37.03,capacityPerDay:1,fixedCostPerDay:0}],customers:[
    {id:"D1",name:"합성 D1",lon:127,lat:37.01,demandPerDay:1},
    {id:"D2",name:"합성 D2",lon:127,lat:36.98,demandPerDay:1}]};
  const clone = value => JSON.parse(JSON.stringify(value));
  const compareId=(a,b)=>a<b?-1:a>b?1:0;
  const isOnTime=o=>o.deliveredAt!==null && o.deliveredAt<=o.dueAt;
  function number(value, fallback, low, high, integer=false) {
    const n = value === undefined ? fallback : Number(value);
    if (!Number.isFinite(n) || n < low || n > high || (integer && !Number.isInteger(n))) throw new Error("입력 범위를 확인하세요.");
    return n;
  }
  function dataFor(input) {
    const data = clone(input || REGIONAL_DATA);
    if (!Array.isArray(data.facilities) || data.facilities.length < 2 || data.facilities.length > 5 || !Array.isArray(data.customers) || !data.customers.length || data.customers.length > 8) throw new Error("유한한 거점·수요지 자료가 필요합니다.");
    const ids = new Set();
    for (const row of [...data.facilities, ...data.customers]) {
      if (!/^[A-Za-z0-9_-]{1,30}$/.test(row.id) || ids.has(row.id) || typeof row.name !== "string" || row.name.length > 80) throw new Error("거점·수요지 식별자가 올바르지 않습니다.");
      ids.add(row.id);
      if(typeof row.lon!=="number"||typeof row.lat!=="number") throw new Error("숫자 위경도 좌표가 필요합니다.");
      number(row.lon,0,-180,180); number(row.lat,0,-90,90);
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
    const solverTrace=[];
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
      const path=[];let reverseEdges=0;
      for(let at=sink;at!==0;) {const [from,index]=previous[at];quantity=Math.min(quantity,graph[from][index].cap);at=from;}
      for(let at=sink;at!==0;) {const [from,index]=previous[at],edge=graph[from][index];path.unshift([from,at]);if(edge.cost<0)reverseEdges++;edge.cap-=quantity;graph[at][edge.rev].cap+=quantity;at=from;}
      solverTrace.push({path,quantity,unitPathCost:distances[sink],reverseEdges});
      left-=quantity;
    }
    const allocations=routes.map(({edge,...row})=>({...row,unitsPerDay:edge.initial-edge.cap})).filter(a=>a.unitsPerDay>0).sort((a,b)=>compareId(a.hubId,b.hubId)||compareId(a.customerId,b.customerId));
    const unserved=missed.map(({edge,...row})=>({...row,unitsPerDay:edge.initial-edge.cap}));
    const fixedCostPerDay=hubs.reduce((n,h)=>n+h.fixedCostPerDay,0), transportCostPerDay=allocations.reduce((n,a)=>n+a.unitsPerDay*a.unitCost,0), unservedPenaltyPerDay=unserved.reduce((n,u)=>n+u.unitsPerDay*PENALTY,0);
    return {allocations,unserved,fixedCostPerDay,transportCostPerDay,unservedPenaltyPerDay,totalCostPerDay:fixedCostPerDay+transportCostPerDay+unservedPenaltyPerDay,
      servedUnitsPerDay:allocations.reduce((n,a)=>n+a.unitsPerDay,0),totalDemandPerDay:totalDemand,solverTrace};
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
          const order={id:`d${day}-${customer.id}-${ordinal}`,day,customerId:customer.id,hubId:route?route.hubId:null,quantity:1,distanceKm:route?route.distanceKm:0,createdAt,dueAt:createdAt+8,exogenousDelayHours,dispatchedAt,deliveredAt,unservedReason:!route?"planning_unserved":blocked?"disruption":null};
          orders.push(order);row.demand++;
          if(deliveredAt!==null) {row.delivered++;if(isOnTime(order)) row.onTime++;} else row.unserved++;
        }
      }
      daily.push(row);
    }
    const demand=orders.length, delivered=orders.filter(o=>o.deliveredAt!==null), onTime=delivered.filter(isOnTime).length;
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
  const api={run,defaults:()=>clone(REGIONAL_DATA),fixtures:()=>({S2:clone(DEFAULT_DATA),R2:clone(R2),regional:clone(REGIONAL_DATA)}),isOnTime};
  if(typeof module!=="undefined"&&module.exports) module.exports=api;
  root.DASNetwork=api;
})(typeof window!=="undefined"?window:globalThis);
