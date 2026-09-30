"use strict";
(() => {
  const byId=id=>document.getElementById(id), format=(n,d=0)=>Number(n).toLocaleString("ko-KR",{maximumFractionDigits:d});
  let result;
  function chosenData(){const data=window.DASNetwork.fixtures()[byId("case-select").value];const factor=Number(byId("fixed-cost-multiplier").value);if(!Number.isFinite(factor)||factor<0||factor>10)throw new Error("고정비 배율은 0~10입니다.");data.facilities.forEach(h=>h.fixedCostPerDay*=factor);return data;}
  function setCase(){const data=chosenData();byId("max-hubs").max=data.facilities.length;byId("max-hubs").value=Math.min(Number(byId("max-hubs").value),data.facilities.length);byId("disruption-hub").replaceChildren(...[{id:"",name:"없음"},...data.facilities].map(h=>{const o=element("option",h.name);o.value=h.id;return o;}));byId("disruption-hub").value="";}
  function inspect(kind,id,current) {
    const p=result.parameters,plan=current.planning,rows=plan.allocations.filter(a=>a[kind==="hub"?"hubId":"customerId"]===id);
    const node=(kind==="hub"?result.data.facilities:result.data.customers).find(n=>n.id===id);
    const amount=rows.reduce((s,a)=>s+a.unitsPerDay,0);
    const orders=current.simulation.orders.filter(o=>o[kind==="hub"?"hubId":"customerId"]===id);
    const lines=[node.name,`계획 배분 ${amount}개/일`,kind==="hub"?`용량 ${Math.round(node.capacityPerDay*p.capacityMultiplier)}개/일 · ${current.selectedHubs.includes(id)?"선택 거점":"미선택 후보"}`:`수요 ${Math.round(node.demandPerDay*p.demandMultiplier)}개/일 · 계획 미충족 ${plan.unserved.find(u=>u.customerId===id).unitsPerDay}개/일`,...rows.map(a=>`${a.hubId} → ${a.customerId}: ${a.unitsPerDay}개/일`),`기간 실제 배송 ${orders.filter(o=>o.deliveredAt!==null).length}개 / 관련 주문 ${orders.length}개`,"장애 손실은 재배정하지 않으며 선은 계획 물량/일입니다."];
    byId("selection-detail").replaceChildren(...lines.map(t=>element("p",t)));
  }
  function selectable(node,kind,id,current) {
    node.setAttribute("tabindex","0");node.setAttribute("role","button");node.setAttribute("aria-label",`${id} 배분 상세 보기`);
    node.addEventListener("click",()=>inspect(kind,id,current));
    node.addEventListener("keydown",e=>{if(e.key==="Enter"||e.key===" "){e.preventDefault();inspect(kind,id,current);}});
  }
  function element(tag,text,className) {const node=document.createElement(tag);if(text!==undefined)node.textContent=text;if(className)node.className=className;return node;}
  function svg(tag,attrs,text) {const node=document.createElementNS("http://www.w3.org/2000/svg",tag);Object.entries(attrs).forEach(([key,value])=>node.setAttribute(key,String(value)));if(text!==undefined)node.textContent=text;return node;}
  function map(current) {
    const map=byId("network-map");map.replaceChildren();map.dataset.selectedHubs=current.selectedHubs.join(",");
    const points=[...result.data.facilities,...result.data.customers], west=Math.min(...points.map(p=>p.lon))-.35,east=Math.max(...points.map(p=>p.lon))+.35,south=Math.min(...points.map(p=>p.lat))-.3,north=Math.max(...points.map(p=>p.lat))+.3;
    const project=p=>({x:55+(p.lon-west)/(east-west)*790,y:490-(p.lat-south)/(north-south)*430});
    for(let i=0;i<=4;i++) {const x=55+i*790/4,y=60+i*430/4;map.append(svg("line",{x1:x,x2:x,y1:45,y2:495,class:"grid"}),svg("line",{x1:45,x2:855,y1:y,y2:y,class:"grid"}),svg("text",{x:x-18,y:520},format(west+(east-west)*i/4,2)+"°E"),svg("text",{x:5,y:y+4},format(north-(north-south)*i/4,2)+"°N"));}
    for(const route of current.planning.allocations) {const hub=result.data.facilities.find(f=>f.id===route.hubId),customer=result.data.customers.find(c=>c.id===route.customerId),a=project(hub),b=project(customer);const line=svg("line",{x1:a.x,y1:a.y,x2:b.x,y2:b.y,class:"route","stroke-width":1+Math.sqrt(route.unitsPerDay),"data-route-hub":hub.id,"data-route-customer":customer.id,"data-units":route.unitsPerDay});line.append(svg("title",{},`${hub.name} → ${customer.name}: ${route.unitsPerDay}개/일, ${format(route.distanceKm,1)}km`));map.append(line);}
    for(const c of result.data.customers) {const p=project(c),node=svg("circle",{cx:p.x,cy:p.y,r:5,class:"customer","data-customer-id":c.id,"data-lon":c.lon,"data-lat":c.lat});node.append(svg("title",{},`${c.name}: ${Math.round(c.demandPerDay*result.parameters.demandMultiplier)}개/일`));map.append(node,svg("text",{x:p.x+10,y:p.y+17},c.name));}
    for(const h of result.data.facilities) {const p=project(h),chosen=current.selectedHubs.includes(h.id),node=svg("circle",{cx:p.x,cy:p.y,r:chosen?9:6,class:"hub"+(chosen?" selected":"")+(h.id===result.parameters.disruptionHub&&result.parameters.disruptionDays>0?" disrupted":""),"data-hub-id":h.id,"data-lon":h.lon,"data-lat":h.lat,"data-selected":chosen});node.append(svg("title",{},`${h.name}: ${chosen?"선택":"후보"}, 용량 ${Math.round(h.capacityPerDay*result.parameters.capacityMultiplier)}개/일`));map.append(node,svg("text",{x:p.x+12,y:p.y-10},h.name));}
  }
  function render() {
    const scenario=byId("scenario-select").value,current=result[scenario],k=current.simulation.kpis,container=byId("model-results");
    Object.assign(container.dataset,{scenario,totalCost:k.totalCost,serviceRate:k.serviceRate,onTimeRate:k.onTimeRate,demand:k.demand,delivered:k.delivered});
    Object.assign(byId("top-kpis").dataset,container.dataset);
    const cards=[["기간 총비용",format(k.totalCost),"가상 비용 단위 · 미충족 포함"],["서비스율",format(k.serviceRate*100,1)+"%",`${format(k.delivered)} / ${format(k.demand)}개 배송`],["정시율",format(k.onTimeRate*100,1)+"%","전체 수요 중 약속 시각 내 배송"],["평균 리드타임",format(k.meanLeadHours,1)+" h","배송 완료 주문 기준"],["미충족 수요",format(k.unserved)+"개",`${current.selectedHubs.length}개 거점 선택`]];
    const keys=["totalCost","serviceRate","onTimeRate","meanLeadHours","unserved"];
    byId("top-kpis").replaceChildren(...cards.map(([title,value,note],index)=>{const card=element("div",undefined,"kpi"),strong=element("strong",value),sub=element("span",note);strong.dataset.metric=keys[index];strong.dataset.value=k[keys[index]];if(index===1){const count=element("span",format(k.delivered)),total=element("span",format(k.demand));count.dataset.metric="delivered";count.dataset.value=k.delivered;total.dataset.metric="demand";total.dataset.value=k.demand;sub.replaceChildren(count,document.createTextNode(" / "),total,document.createTextNode("개 배송"));}card.append(element("small",title),strong,sub);return card;}));
    const delta=result.baseline.planning.totalCostPerDay-result.optimized.planning.totalCostPerDay;
    byId("comparison-summary").textContent=`정상 조건 계획 비용 차이 ${format(delta,1)} /일 · 장애·지연은 아래 배송 실험에 반영`;
    byId("selection-detail").replaceChildren(element("p",`선택: ${current.selectedHubs.map(id=>result.data.facilities.find(f=>f.id===id).name).join(" · ")||"없음"}`),element("p",`계획 배분 ${format(current.planning.servedUnitsPerDay)} / ${format(current.planning.totalDemandPerDay)}개/일`),element("p",`전체 ${result.candidates.length}개 입지 조합을 같은 제약으로 비교했습니다.`));
    byId("assumptions").replaceChildren(...result.assumptions.map(text=>element("li",text)));
    byId("data-provenance").textContent=`${result.data.provenance.description||"출처 미확인 자료"} ${result.data.provenance.version||""} · ${result.data.provenance.createdDate||result.data.provenance.date||"날짜 미상"}`;
    byId("selection-detail").append(element("p",`계획 비용 ${format(current.planning.totalCostPerDay,4)}/일 · 운영 비용 ${format(k.totalCost,4)}/기간`),element("p",`운영 고정비 ${format(k.fixedCost,2)} + 실제 출고 운송비 ${format(k.transportCost,2)} + 미배송 벌점 ${format(k.penaltyCost,2)}`),element("p",`장애 미배송 ${current.simulation.orders.filter(o=>o.unservedReason==="disruption").length}개. 처리 대기·재배정은 모델 범위 밖입니다.`));
    byId("scenario-table").replaceChildren(...["baseline","optimized"].map(name=>{const a=result[name],v=a.simulation.kpis,row=element("tr");row.dataset.scenario=name;for(const value of [name==="baseline"?"기준안":"최적화안",a.selectedHubs.join(", ")||"없음",format(v.totalCost,1),format(v.serviceRate*100,1)+"%",format(v.onTimeRate*100,1)+"%",format(v.meanLeadHours,2)+" h"])row.append(element("td",value));return row;}));
    byId("capacity-table").replaceChildren(...result.data.facilities.map(h=>{const row=element("tr"),orders=current.simulation.orders.filter(o=>o.hubId===h.id);for(const v of [h.name,Math.round(h.capacityPerDay*result.parameters.capacityMultiplier),current.planning.allocations.filter(a=>a.hubId===h.id).reduce((s,a)=>s+a.unitsPerDay,0),orders.filter(o=>o.dispatchedAt!==null).length,orders.filter(o=>o.unservedReason==="disruption").length])row.append(element("td",String(v)));return row;}));
    map(current);
    byId("network-map").querySelectorAll("[data-hub-id]").forEach(n=>selectable(n,"hub",n.dataset.hubId,current));
    byId("network-map").querySelectorAll("[data-customer-id]").forEach(n=>selectable(n,"customer",n.dataset.customerId,current));
    byId("model-checks").textContent=(k.demand===0?"평가 대상 없음 · 수요 0의 비율과 평균은 계약값 0. ":k.delivered===0?"도착 표본 없음 · 평균 0은 배송 성과가 아닙니다. ":"")+"계산과 지도 갱신 완료 · 실제 브라우저 검수는 별도입니다.";
  }
  function calculate(event, changeCase=false) {
    if(event)event.preventDefault();
    try {if(changeCase)setCase();result=window.DASNetwork.run({data:chosenData(),seed:+byId("seed").value,days:+byId("days").value,demandMultiplier:+byId("demand-multiplier").value,capacityMultiplier:+byId("capacity-multiplier").value,maxHubs:+byId("max-hubs").value,disruptionHub:byId("disruption-hub").value,disruptionDays:+byId("disruption-days").value});byId("model-results").hidden=false;render();}
    catch(error){result=null;for(const id of ["model-results","top-kpis","network-map"]){const node=byId(id);for(const key of Object.keys(node.dataset))delete node.dataset[key];}byId("top-kpis").replaceChildren();byId("network-map").replaceChildren();byId("model-results").hidden=true;byId("comparison-summary").textContent="유효한 입력으로 다시 계산하세요.";byId("model-checks").textContent="계산 중지: "+error.message;}
  }
  byId("model-form").addEventListener("submit",calculate);byId("scenario-select").addEventListener("change",()=>{if(result)render();});byId("case-select").addEventListener("change",()=>calculate(null,true));calculate(null,true);
})();

