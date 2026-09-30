"use strict";
(() => {
  const form=document.querySelector("#model-form"),output=document.querySelector("#model-results"),checks=document.querySelector("#model-checks");
  function draw(){
    output.replaceChildren();
    try{
      const parameters=Object.fromEntries(new FormData(form).entries());
      for(const name in parameters)parameters[name]=Number(parameters[name]);
      const result=DASModel.run(parameters);
      const values=[["도착 주문",result.arrivals],["완료 주문",result.completed],["기말 대기 주문",result.backlog],["피킹 중",result.inService],["기말 재고",result.stock],["완료 주문 평균 소요일",result.meanOrderDays.toFixed(3)]];
      for(const [label,value] of values){const box=document.createElement("div"),strong=document.createElement("strong");box.className="metric";box.textContent=label;strong.textContent=String(value);box.append(strong);output.append(box);}
      document.querySelector("#scene-stock").textContent=result.stock;
      document.querySelector("#scene-active").textContent=result.inService;
      document.querySelector("#scene-backlog").textContent=result.backlog;
      output.dataset.status=Object.values(result.invariants).every(Boolean)?"passed":"failed";
      output.dataset.completed=String(result.completed);
      output.dataset.backlog=String(result.backlog);
    }catch(error){output.textContent=error.message;output.dataset.status="failed";}
  }
  form.addEventListener("submit",event=>{event.preventDefault();draw();});
  for(const check of DASModel.checks()){const row=document.createElement("li");row.dataset.status=check.passed?"passed":"failed";row.textContent=(check.passed?"통과 · ":"실패 · ")+check.name;checks.append(row);}
  checks.dataset.status=[...checks.children].every(row=>row.dataset.status==="passed")?"passed":"failed";
  draw();
})();

(() => {
  const form=document.querySelector("#policy-form"),output=document.querySelector("#policy-comparison");
  const labels={P0:"P0 기본",P1:"P1 안전재고 증가",P2:"P2 긴급 전환"};
  function drawPolicies(){
    output.replaceChildren();document.querySelector("#policy-state").replaceChildren();document.querySelector("#policy-trace").textContent="";
    try{
      const input=Object.fromEntries([...new FormData(form)].map(([k,v])=>[k,v.trim()===""?NaN:Number(v)]));
      const result=DASModel.comparePolicies(input);
      const scroll=document.createElement("div");scroll.className="table-scroll";
      const table=document.createElement("table");
      table.innerHTML='<caption>동일 수요 '+result.demandTrace.length+'건 · 관측 '+result.parameters.days+'일 · 비용 CU</caption><thead><tr><th scope="col">정책</th><th scope="col">OTIF</th><th scope="col">배송 완료</th><th scope="col">완료 평균 (일)</th><th scope="col">보유비</th><th scope="col">정규비</th><th scope="col">긴급비</th><th scope="col">발주비</th><th scope="col">총비용</th></tr></thead>';
      const tbody=document.createElement("tbody");
      for(const r of result.policies){
        const row=document.createElement("tr");row.dataset.policy=r.policy;row.dataset.otif=String(r.metrics.otif);row.dataset.totalCost=String(r.costs.total);
        const values=[labels[r.policy],(100*r.metrics.otif).toFixed(1)+"%",r.metrics.completed,r.metrics.meanOrderDays.toFixed(3),r.costs.holding.toFixed(2),r.costs.regular.toFixed(2),r.costs.emergency.toFixed(2),r.costs.ordering.toFixed(2),r.costs.total.toFixed(2)];
        values.forEach((v,i)=>{const cell=document.createElement(i===0?"th":"td");if(i===0)cell.scope="row";cell.textContent=v;row.append(cell);});tbody.append(row);
        const state=document.createElement("div");state.className="state-row";
        const counts=[r.metrics.completed,r.metrics.inTransit,r.metrics.reserved,r.metrics.backlog];
        const end=r.stateLog[r.stateLog.length-1];
        const label=document.createElement("div");label.textContent=labels[r.policy]+" — 완료 "+counts[0]+" / 배송 중 "+counts[1]+" / 예약 "+counts[2]+" / 대기 "+counts[3]+"건 · 물리재고 "+end.onHand+"개 · 미입고 "+end.onOrder+"개";
        const bar=document.createElement("div");bar.className="state-bar";bar.setAttribute("aria-hidden","true");
        counts.forEach(n=>{const part=document.createElement("span");part.style.width=(r.metrics.arrivals?100*n/r.metrics.arrivals:0)+"%";bar.append(part);});state.append(label,bar);document.querySelector("#policy-state").append(state);
      }
      table.append(tbody);scroll.append(table);output.append(scroll);output.dataset.status="calculated";
      document.querySelector("#policy-trace").textContent=JSON.stringify(result,null,2);
    }catch(error){output.textContent=error.message;output.dataset.status="failed";}
  }
  function runChecks(){
    const list=document.querySelector("#policy-checks");list.replaceChildren();const results=DASModel.policyChecks();
    for(const c of results){const li=document.createElement("li");li.dataset.status=c.passed?"passed":"failed";li.textContent=(c.passed?"통과 · ":"실패 · ")+c.name+(c.error?" · "+c.error:"");list.append(li);}
    list.dataset.status=results.every(c=>c.passed)?"passed":"failed";
  }
  form.addEventListener("submit",event=>{event.preventDefault();drawPolicies();});

  document.querySelector("#reset-policies").addEventListener("click",()=>{form.reset();drawPolicies();});
  document.querySelector("#check-policies").addEventListener("click",runChecks);
  drawPolicies();runChecks();
})();

