"use strict";
// Execute only inside the authorized prototype worker sandbox, never as a host verification step.
const model=require("./model.js");
const failures=model.checks().filter(check=>!check.passed);
if(failures.length)throw new Error(JSON.stringify(failures));
console.log("Synthetic model self-checks passed; not independent domain validation.");

const assert=require('node:assert/strict');
const near=(a,b)=>assert.ok(Math.abs(a-b)<1e-7,`${a} != ${b}`);
const practical=r=>({...r,policy:''});
let audited=0;
function audit(input){
  const frozen=Object.freeze({...input});
  const r=model.comparePolicies(frozen),p=r.parameters;
  assert.deepEqual(r,model.comparePolicies(frozen));
  assert.deepEqual(r.policies.map(x=>x.policy),['P0','P1','P2']);
  assert.equal(r.demandTrace.length,p.days*p.ordersPerDay);
  assert.equal(new Set(r.demandTrace.map(d=>d.id)).size,r.demandTrace.length);
  assert.equal(r.shockTrace.length,p.days);
  for(const d of r.demandTrace){assert.equal(d.quantity,1);assert.ok(d.arrivalAt>=0&&d.arrivalAt<p.days);near(d.dueAt-d.arrivalAt,2);}
  for(const x of r.policies){
    audited++;
    assert.deepEqual(x.orders.map(o=>o.id),r.demandTrace.map(d=>d.id));
    const byPO=new Map(x.purchaseOrders.map(o=>[o.id,o]));
    assert.equal(byPO.size,x.purchaseOrders.length);
    assert.equal(new Set(x.receipts.map(y=>y.purchaseOrderId+':'+y.mode)).size,x.receipts.length);
    for(const po of x.purchaseOrders){
      assert.ok(po.quantity>0&&Number.isInteger(po.quantity));assert.ok(po.emergencyQuantity>=0&&po.emergencyQuantity<=po.quantity);
      assert.ok(po.orderedAt<po.regularDepartureAt);assert.ok(po.regularDepartureAt<po.regularArrivalAt);
      if(po.emergencyQuantity){assert.equal(x.policy,'P2');assert.ok(po.emergencyQuantity<=p.emergencyCapacity);assert.ok(po.orderedAt<po.emergencyDepartureAt&&po.emergencyDepartureAt<po.regularDepartureAt);assert.ok(po.emergencyArrivalAt<po.regularArrivalAt);}
      else{assert.equal(po.emergencyDepartureAt,null);assert.equal(po.emergencyArrivalAt,null);}
      for(const mode of ['regular','emergency']){
        const qty=mode==='regular'?po.quantity-po.emergencyQuantity:po.emergencyQuantity;
        const time=mode==='regular'?po.regularArrivalAt:po.emergencyArrivalAt;
        const actual=x.receipts.filter(y=>y.purchaseOrderId===po.id&&y.mode===mode);
        assert.equal(actual.length,qty>0&&time<=p.days?1:0);
        if(actual.length){assert.equal(actual[0].quantity,qty);assert.equal(actual[0].time,time);}
      }
    }
    x.receipts.forEach(y=>{assert.ok(byPO.has(y.purchaseOrderId));assert.ok(y.time<=p.days);assert.ok(['regular','emergency'].includes(y.mode));});
    x.orders.forEach((o,i)=>{
      for(const key of ['reservedAt','shippedAt','deliveredAt'])if(o[key]!==null)assert.ok(o[key]>=r.demandTrace[i].arrivalAt&&o[key]<=p.days);
      if(o.shippedAt!==null){assert.notEqual(o.reservedAt,null);near(o.shippedAt-o.reservedAt,.25);}
      if(o.deliveredAt!==null){assert.notEqual(o.shippedAt,null);near(o.deliveredAt-o.shippedAt,.5);}
    });
    assert.equal(x.stateLog[0].time,0);assert.equal(x.stateLog[0].onHand,p.initialStock);assert.equal(x.stateLog.at(-1).time,p.days);
    const endAtTime=new Map();
    x.stateLog.forEach((s,i)=>{
      if(i)assert.ok(s.time>=x.stateLog[i-1].time);
      for(const key of ['onHand','reserved','backlog','onOrder','received','shipped','completed'])assert.ok(Number.isInteger(s[key])&&s[key]>=0);
      assert.ok(s.onHand>=s.reserved);assert.equal(p.initialStock+s.received,s.onHand+s.shipped);
      assert.equal(s.inventoryPosition,s.onHand-s.reserved+s.onOrder-s.backlog);endAtTime.set(s.time,s);
    });
    // 같은 시각의 마지막 상태를 원시 주문·입고·발주와 대조한다.
    for(const [t,s] of endAtTime){
      const happened=(o,k)=>o[k]!==null&&o[k]<=t;
      const received=x.receipts.filter(y=>y.time<=t).reduce((n,y)=>n+y.quantity,0);
      const shipped=x.orders.filter(o=>happened(o,'shippedAt')).length;
      const completed=x.orders.filter(o=>happened(o,'deliveredAt')).length;
      const reserved=x.orders.filter(o=>happened(o,'reservedAt')&&!happened(o,'shippedAt')).length;
      const backlog=x.orders.filter((o,i)=>r.demandTrace[i].arrivalAt<=t&&!happened(o,'reservedAt')).length;
      assert.equal(s.received,received);assert.equal(s.shipped,shipped);assert.equal(s.completed,completed);assert.equal(s.reserved,reserved);assert.equal(s.backlog,backlog);
      assert.equal(s.onOrder,x.purchaseOrders.filter(o=>o.orderedAt<=t).reduce((n,o)=>n+o.quantity,0)-received);
      assert.equal(s.onHand,p.initialStock+received-shipped);
    }
    const done=x.orders.filter(o=>o.deliveredAt!==null).length;
    const otif=x.orders.filter((o,i)=>o.deliveredAt!==null&&o.deliveredAt<=r.demandTrace[i].dueAt).length;
    const duration=x.orders.reduce((n,o,i)=>n+(o.deliveredAt===null?0:o.deliveredAt-r.demandTrace[i].arrivalAt),0);
    const end=x.stateLog.at(-1),m=x.metrics,c=x.costs;
    assert.equal(m.arrivals,r.demandTrace.length);assert.equal(m.completed,done);assert.equal(m.backlog,end.backlog);assert.equal(m.reserved,end.reserved);assert.equal(m.inTransit,end.shipped-done);
    assert.equal(m.arrivals,m.completed+m.backlog+m.reserved+m.inTransit);
    near(m.otif,m.arrivals?otif/m.arrivals:0);near(m.meanOrderDays,done?duration/done:0);
    const integral=x.stateLog.slice(1).reduce((sum,s,i)=>sum+x.stateLog[i].onHand*(s.time-x.stateLog[i].time),0);
    near(m.holdingUnitDays,integral);assert.equal(c.holdingRate,.1);assert.equal(c.regularUnitRate,1);assert.equal(c.emergencyUnitRate,4);assert.equal(c.orderFee,2);
    near(c.holding,integral*.1);near(c.regular,x.purchaseOrders.reduce((n,o)=>n+o.quantity-o.emergencyQuantity,0));near(c.emergency,x.purchaseOrders.reduce((n,o)=>n+o.emergencyQuantity,0)*4);near(c.ordering,x.purchaseOrders.length*2);near(c.total,c.holding+c.regular+c.emergency+c.ordering);
  }
  return r;
}
const base=audit({});
for(const input of [{ordersPerDay:0},{initialStock:0},{safetyStockDays:0},{emergencyCapacity:0},{days:1,initialStock:0},{days:30,ordersPerDay:100,initialStock:0,safetyStockDays:10,emergencyCapacity:100},{days:3,ordersPerDay:0,initialStock:0},{seed:999999},{initialStock:10000}])audit(input);
for(let seed=1;seed<=6;seed++)audit({days:5,ordersPerDay:seed,initialStock:seed,safetyStockDays:seed%3,emergencyCapacity:seed%4,seed});
assert.deepEqual(practical(model.comparePolicies({safetyStockDays:0}).policies[1]),practical(base.policies[0]));
assert.deepEqual(practical(model.comparePolicies({emergencyCapacity:0}).policies[2]),practical(base.policies[0]));
assert.ok(base.policies[2].purchaseOrders.some(o=>o.emergencyQuantity>0));
assert.deepEqual(base.demandTrace,model.comparePolicies({safetyStockDays:7,emergencyCapacity:0}).demandTrace);
assert.deepEqual(base.shockTrace,model.comparePolicies({ordersPerDay:0}).shockTrace);
assert.notDeepEqual(base.demandTrace,model.comparePolicies({seed:43}).demandTrace);
assert.notDeepEqual(base.policies[0].metrics,model.comparePolicies({initialStock:0}).policies[0].metrics);
for(const key of Object.keys(model.policyDefaults))for(const v of [NaN,Infinity,null,'2',-1,.5])assert.throws(()=>model.comparePolicies({[key]:v}));
for(const input of [{days:31},{ordersPerDay:101},{initialStock:10001},{seed:0},{seed:1000000},{safetyStockDays:11},{emergencyCapacity:101},{unexpected:1},null,[]])assert.throws(()=>model.comparePolicies(input));
assert.ok(model.policyChecks().every(c=>c.passed));
console.log(JSON.stringify({scope:'DAS-RD 자체 수치 검사; 독립 검증 아님',legacyChecks:model.checks().length,policyChecks:model.policyChecks().length,auditedPolicyResults:audited,baseline:base.policies.map(x=>({policy:x.policy,...x.metrics,costs:x.costs,emergencyQuantity:x.purchaseOrders.reduce((s,o)=>s+o.emergencyQuantity,0)}))},null,2));

// 최소 DOM 대역을 이용한 앱 연결 자체 검사. 실제 브라우저 검사가 아니다.
const fs=require('node:fs'),vm=require('node:vm');
class Element {
  constructor(){this.children=[];this.dataset={};this.style={};this.listeners={};this.textContent='';}
  append(...nodes){this.children.push(...nodes);}
  replaceChildren(...nodes){this.children=[...nodes];}
  setAttribute(k,v){this[k]=v;}
  addEventListener(k,fn){this.listeners[k]=fn;}
  trigger(k){assert.ok(this.listeners[k],`missing handler ${k}`);this.listeners[k]({preventDefault(){}});}
}
const elements=new Map();
const html=fs.readFileSync('./index.html','utf8');
for(const match of html.matchAll(/id="([^"]+)"/g)){assert.ok(!elements.has('#'+match[1]),'duplicate DOM id');elements.set('#'+match[1],new Element());}
const el=id=>{assert.ok(elements.has(id),`missing DOM ${id}`);return elements.get(id);};
el('#model-form').values={...model.defaults};el('#policy-form').values={...model.policyDefaults};
el('#policy-form').reset=function(){this.values={...model.policyDefaults};};
class FormDataStub {
  constructor(form){this.form=form;}
  entries(){return Object.entries(this.form.values).map(([k,v])=>[k,String(v)])[Symbol.iterator]();}
  [Symbol.iterator](){return this.entries();}
}
vm.runInNewContext(fs.readFileSync('./app.js','utf8'),{DASModel:model,FormData:FormDataStub,document:{querySelector:el,createElement:()=>new Element()}});
assert.equal(el('#model-results').dataset.status,'passed');assert.equal(el('#model-checks').dataset.status,'passed');assert.equal(el('#policy-checks').dataset.status,'passed');
function rows(){return el('#policy-comparison').children[0].children[0].children[0].children;}
function checkUI(input){
  const expected=model.comparePolicies(input);
  assert.equal(rows().length,3);
  rows().forEach((row,i)=>{assert.equal(row.dataset.policy,expected.policies[i].policy);assert.equal(Number(row.dataset.otif),expected.policies[i].metrics.otif);assert.equal(Number(row.dataset.totalCost),expected.policies[i].costs.total);});
  assert.deepEqual(JSON.parse(el('#policy-trace').textContent),expected);
  assert.equal(el('#policy-state').children.length,3);
}
checkUI({});
el('#policy-form').values.ordersPerDay=0;el('#policy-form').trigger('submit');checkUI({ordersPerDay:0});
assert.ok(el('#policy-state').children.every(row=>row.children[0].textContent.includes('완료 0')));
el('#policy-form').values.days=-1;el('#policy-form').trigger('submit');assert.equal(el('#policy-comparison').dataset.status,'failed');assert.equal(el('#policy-state').children.length,0);assert.equal(el('#policy-trace').textContent,'');
el('#reset-policies').trigger('click');checkUI({});
el('#check-policies').trigger('click');assert.equal(el('#policy-checks').dataset.status,'passed');
el('#model-form').values.ordersPerDay=0;el('#model-form').trigger('submit');assert.equal(el('#model-results').dataset.completed,'0');
assert.ok(html.includes('<details><summary>전체 원시 추적'));
console.log('App wiring self-check passed: initial render, changed/invalid input, reset, checks, raw trace, legacy form. DOM stub only; browser behavior unverified.');
