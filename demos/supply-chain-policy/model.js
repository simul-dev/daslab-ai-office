"use strict";
(function(root){
  const defaults={days:7,ordersPerDay:12,initialStock:30,reorderPoint:20,batch:40,leadTime:2,servers:1,serviceTime:0.05,seed:42};
  function run(input={}){
    const p={...defaults,...input};
    const bounds={days:[1,30],ordersPerDay:[0,100],initialStock:[0,10000],reorderPoint:[0,10000],batch:[0,10000],leadTime:[0.1,30],servers:[1,20],serviceTime:[0.01,2],seed:[1,999999]};
    for(const [key,[low,high]] of Object.entries(bounds)){
      if(!Number.isFinite(p[key])||p[key]<low||p[key]>high) throw new Error("입력 범위 오류: "+key);
      if(!["leadTime","serviceTime"].includes(key)&&!Number.isInteger(p[key])) throw new Error("정수 입력 필요: "+key);
    }
    let rng=p.seed>>>0, serial=0, stock=p.initialStock, received=0, allocated=0, completed=0, arrivals=0, active=0, incoming=0, totalTime=0, minimumStock=stock;
    const random=()=>{rng=(Math.imul(1664525,rng)+1013904223)>>>0;return rng/4294967296;};
    const events=[], waiting=[];
    const add=(time,type,order)=>events.push({time,type,order,id:serial++});
    for(let day=0;day<p.days;day++)for(let i=0;i<p.ordersPerDay;i++)add(day+(i+0.2+random()*0.6)/p.ordersPerDay,"order");
    function replenish(t){
      if(p.batch&&stock+incoming-waiting.length<=p.reorderPoint){incoming+=p.batch;add(t+p.leadTime,"stock");}
    }
    function serve(t){
      while(waiting.length&&stock>0&&active<p.servers){const order=waiting.shift();stock--;allocated++;active++;add(t+p.serviceTime,"complete",order);}
      minimumStock=Math.min(minimumStock,stock);replenish(t);
    }
    replenish(0);
    let iterations=0;
    while(events.length){
      events.sort((a,b)=>a.time-b.time||a.id-b.id);
      const event=events.shift();if(event.time>p.days)break;
      if(++iterations>30000)throw new Error("사건 수 제한 초과");
      if(event.type==="order"){arrivals++;waiting.push({arrived:event.time});}
      else if(event.type==="stock"){stock+=p.batch;incoming-=p.batch;received+=p.batch;}
      else {active--;completed++;totalTime+=event.time-event.order.arrived;}
      serve(event.time);
    }
    return {parameters:p,arrivals,completed,backlog:waiting.length,inService:active,stock,received,allocated,incoming,
      completionRate:arrivals?completed/arrivals:0,meanOrderDays:completed?totalTime/completed:0,
      invariants:{inventoryBalance:p.initialStock+received===stock+allocated,orderBalance:arrivals===completed+waiting.length+active,nonnegative:minimumStock>=0&&active>=0&&incoming>=0}};
  }
  function checks(){
    const results=[];
    const test=(name,fn)=>{try{results.push({name,passed:!!fn()});}catch(error){results.push({name,passed:false,error:String(error.message)});}};
    test("같은 입력·시드의 결과 재현",()=>JSON.stringify(run())===JSON.stringify(run()));
    test("기준 시나리오 재고·주문 보존",()=>Object.values(run().invariants).every(Boolean));
    test("무수요 시나리오",()=>{const r=run({ordersPerDay:0});return r.arrivals===0&&r.completed===0&&r.meanOrderDays===0&&Object.values(r.invariants).every(Boolean);});
    test("재고 없이 보충 중지",()=>{const r=run({initialStock:0,batch:0});return r.completed===0&&r.backlog===r.arrivals&&r.arrivals===84&&Object.values(r.invariants).every(Boolean);});
    test("충분한 재고에서 피킹 용량 비교",()=>{const base={initialStock:1000,batch:0,ordersPerDay:40,serviceTime:0.2};const a=run({...base,servers:1}),b=run({...base,servers:2});return b.completed>a.completed&&b.backlog<a.backlog&&Object.values(b.invariants).every(Boolean);});
    test("입력 오류 거절",()=>{try{run({servers:0});return false;}catch(error){return true;}});
    return results;
  }
  root.DASModel={defaults,run,checks};
  if(typeof module!=="undefined"&&module.exports)module.exports=root.DASModel;
})(globalThis);

// 정책 비교 v1: 기존 피킹 모델과 별도의 합성 배송 모형.
(function(root){
  const policyDefaults={days:14,ordersPerDay:6,initialStock:12,seed:42,safetyStockDays:2,emergencyCapacity:6};
  function comparePolicies(input={}){
    if(!input||typeof input!=="object"||Array.isArray(input))throw new Error("입력 객체 필요");
    const p={...policyDefaults,...input};
    const bounds={days:[1,30],ordersPerDay:[0,100],initialStock:[0,10000],seed:[1,999999],safetyStockDays:[0,10],emergencyCapacity:[0,100]};
    for(const key of Object.keys(input))if(!Object.hasOwn(bounds,key))throw new Error("지원하지 않는 입력: "+key);
    for(const [key,[min,max]] of Object.entries(bounds))if(!Number.isInteger(p[key])||p[key]<min||p[key]>max)throw new Error("정수 입력 범위 오류: "+key);
    const generator=seed=>()=>{seed=(Math.imul(seed,1664525)+1013904223)>>>0;return seed/4294967296;};
    const demandRandom=generator(p.seed),shockRandom=generator(p.seed^0x5a5a5a5a);
    const demandTrace=[],shockTrace=[];
    for(let d=0;d<p.days;d++){
      shockTrace.push({day:d,regularDelayDays:d>=2&&d<=7?3:Math.floor(shockRandom()*2)});
      for(let i=0;i<p.ordersPerDay;i++){
        const arrivalAt=d+(i+0.2+0.6*demandRandom())/p.ordersPerDay;
        demandTrace.push({id:"D"+(demandTrace.length+1),arrivalAt,dueAt:arrivalAt+2,quantity:1});
      }
    }
    const policies=["P0","P1","P2"].map(policy=>{
      const orders=demandTrace.map(d=>({id:d.id,reservedAt:null,shippedAt:null,deliveredAt:null}));
      const purchaseOrders=[],receipts=[],stateLog=[],events=[],waiting=[];
      let onHand=p.initialStock,reserved=0,onOrder=0,received=0,shipped=0,completed=0,serial=0;
      const snapshot=time=>stateLog.push({time,onHand,reserved,backlog:waiting.length,onOrder,inventoryPosition:onHand-reserved+onOrder-waiting.length,received,shipped,completed});
      const add=(time,rank,fn)=>events.push({time,rank,serial:serial++,fn});
      const allocate=t=>{
        while(waiting.length&&onHand>reserved){
          const index=waiting.shift(),order=orders[index];reserved++;order.reservedAt=t;snapshot(t);
          add(t+0.25,2,()=>{reserved--;onHand--;shipped++;order.shippedAt=t+0.25;snapshot(t+0.25);
            add(t+0.75,1,()=>{completed++;order.deliveredAt=t+0.75;snapshot(t+0.75);});
          });
        }
      };
      snapshot(0);
      for(let day=0;day<p.days;day++)add(day,4,()=>{
        const target=p.ordersPerDay*(3+(policy==="P1"?p.safetyStockDays:0));
        const quantity=Math.max(0,target-(onHand-reserved+onOrder-waiting.length));
        if(!quantity)return;
        const delay=shockTrace[day].regularDelayDays;
        const emergencyQuantity=policy==="P2"&&delay>0?Math.min(quantity,p.emergencyCapacity):0;
        const po={id:"PO"+(purchaseOrders.length+1),orderedAt:day,quantity,regularDepartureAt:day+0.5,regularArrivalAt:day+2+delay,emergencyQuantity,emergencyDepartureAt:emergencyQuantity?day+0.25:null,emergencyArrivalAt:emergencyQuantity?day+1:null};
        purchaseOrders.push(po);onOrder+=quantity;snapshot(day);
        const receive=(mode,time,qty)=>{if(!qty)return;add(time,0,()=>{
          receipts.push({id:po.id+"-"+mode,purchaseOrderId:po.id,mode,time,quantity:qty});
          onHand+=qty;received+=qty;onOrder-=qty;snapshot(time);allocate(time);
        });};
        receive("regular",po.regularArrivalAt,quantity-emergencyQuantity);
        receive("emergency",po.emergencyArrivalAt,emergencyQuantity);
      });
      demandTrace.forEach((d,i)=>add(d.arrivalAt,3,()=>{waiting.push(i);snapshot(d.arrivalAt);allocate(d.arrivalAt);}));
      while(events.length){events.sort((a,b)=>a.time-b.time||a.rank-b.rank||a.serial-b.serial);const event=events.shift();if(event.time>p.days)break;event.fn();}
      snapshot(p.days);
      let holdingUnitDays=0;
      for(let i=1;i<stateLog.length;i++)holdingUnitDays+=stateLog[i-1].onHand*(stateLog[i].time-stateLog[i-1].time);
      const done=orders.filter(o=>o.deliveredAt!==null);
      const onTime=orders.filter((o,i)=>o.deliveredAt!==null&&o.deliveredAt<=demandTrace[i].dueAt).length;
      const metrics={arrivals:demandTrace.length,completed:done.length,backlog:waiting.length,reserved,inTransit:shipped-completed,otif:demandTrace.length?onTime/demandTrace.length:0,meanOrderDays:done.length?orders.reduce((s,o,i)=>s+(o.deliveredAt===null?0:o.deliveredAt-demandTrace[i].arrivalAt),0)/done.length:0,holdingUnitDays};
      const costs={holdingRate:0.1,regularUnitRate:1,emergencyUnitRate:4,orderFee:2};
      costs.holding=holdingUnitDays*costs.holdingRate;
      costs.regular=purchaseOrders.reduce((s,o)=>s+o.quantity-o.emergencyQuantity,0)*costs.regularUnitRate;
      costs.emergency=purchaseOrders.reduce((s,o)=>s+o.emergencyQuantity,0)*costs.emergencyUnitRate;
      costs.ordering=purchaseOrders.length*costs.orderFee;
      costs.total=costs.holding+costs.regular+costs.emergency+costs.ordering;
      return {policy,orders,purchaseOrders,receipts,stateLog,metrics,costs};
    });
    return {parameters:p,demandTrace,shockTrace,policies};
  }
  function policyChecks(){
    const results=[],test=(name,fn)=>{try{results.push({name,passed:!!fn()});}catch(e){results.push({name,passed:false,error:e.message});}};
    const same=(a,b)=>JSON.stringify({...a,policy:""})===JSON.stringify({...b,policy:""});
    test("정책 비교 결정성",()=>JSON.stringify(comparePolicies())===JSON.stringify(comparePolicies()));
    test("예약 포함 재고 보존·재고위치",()=>comparePolicies().policies.every(r=>r.stateLog.every(s=>s.onHand>=s.reserved&&s.reserved>=0&&s.onOrder>=0&&s.backlog>=0&&12+s.received===s.onHand+s.shipped&&s.inventoryPosition===s.onHand-s.reserved+s.onOrder-s.backlog)));
    test("안전재고 0: P1=P0",()=>{const r=comparePolicies({safetyStockDays:0});return same(r.policies[0],r.policies[1]);});
    test("긴급 용량 0: P2=P0",()=>{const r=comparePolicies({emergencyCapacity:0});return same(r.policies[0],r.policies[2]);});
    test("무수요 OTIF·완료·발주 0",()=>comparePolicies({ordersPerDay:0}).policies.every(r=>r.metrics.otif===0&&r.metrics.completed===0&&!r.purchaseOrders.length));
    test("기본 조건의 실제 긴급 전환·중복 입고 방지",()=>{const r=comparePolicies().policies[2];return r.purchaseOrders.some(o=>o.emergencyQuantity>0)&&r.purchaseOrders.every(o=>r.receipts.filter(x=>x.purchaseOrderId===o.id).reduce((s,x)=>s+x.quantity,0)<=o.quantity)&&new Set(r.receipts.map(x=>x.id)).size===r.receipts.length;});
    test("원시기록 OTIF·비용 재계산",()=>{const r=comparePolicies();return r.policies.every(x=>{
      const otif=x.orders.filter((o,i)=>o.deliveredAt!==null&&o.deliveredAt<=r.demandTrace[i].dueAt).length/r.demandTrace.length;
      const holding=x.stateLog.slice(1).reduce((s,row,i)=>s+x.stateLog[i].onHand*(row.time-x.stateLog[i].time),0)*x.costs.holdingRate;
      const total=holding+x.purchaseOrders.reduce((s,o)=>s+(o.quantity-o.emergencyQuantity)*x.costs.regularUnitRate+o.emergencyQuantity*x.costs.emergencyUnitRate+x.costs.orderFee,0);
      return otif===x.metrics.otif&&Math.abs(total-x.costs.total)<1e-8;
    });});
    test("비정상 입력 거절",()=>{try{comparePolicies({ordersPerDay:-1});return false;}catch(e){return true;}});
    return results;
  }
  Object.assign(root.DASModel,{comparePolicies,policyDefaults,policyChecks});
})(globalThis);
