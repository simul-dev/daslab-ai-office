"""Isolated simulation prototypes, static inspection, and frozen local previews.

Generated tests are never executed on the host by this module. A successful
finish means bounded files and syntax, not verified simulation or business value.
"""
import json
import re
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from .development import _hash, _safe, _HTML


FILES = ("README.md", "index.html", "style.css", "model.js", "app.js", "model.test.cjs")
PAGE_FILES = {"index.html": "text/html", "style.css": "text/css", "model.js": "text/javascript", "app.js": "text/javascript"}
REQUIRED_IDS = {"model-form", "run-model", "model-results", "model-checks", "model-scene"}
MAX_PREVIEWS = 16
FILE_LIMIT_BYTES = 500_000
WORKSPACE_LIMIT_BYTES = 1_000_000
TEST_IMPORTS = ("./model.js", "node:assert/strict", "node:fs", "node:vm")
DEFAULT_PROFILE = "inventory-policy-v1"
NETWORK_PROFILE = "supply-network-gis-v1"

README = """# DAS Lab 공급망 DES 초안

합성 데이터로 주문 도착 → 재고 할당 → 피킹 완료, 재주문 → 입고를 비교한다.
시간 단위는 일이며 실고객 데이터·지도·공인 검증 모델이 아니다.

model.js는 시드가 고정된 주문 도착과 사건 목록으로 동작한다. 창고 재고,
대기 주문, 작업 중 주문을 분리하며 기말 미완료 주문을 완료 주문으로 세지 않는다.
주문 하나는 제품 한 개, 피킹 시간과 보충 리드타임은 고정이고 운송·비용은 미포함이다.
3D 장면이나 에셋이 아닌 단순 SVG 도식이며 실제 계산 결과와 연결한다.

허용 파일: README.md, index.html, style.css, model.js, app.js, model.test.cjs.
설치·외부 패키지·네트워크·외부 파일·운영 서비스 수정·배포는 범위 밖이다.
실제 개선 시 이 가정과 문제정의, 변경 이유, 실행한 검증·못 한 검증을 갱신한다.

브라우저에서 model.js의 DASModel.checks()가 재현성, 재고·주문 보존,
무수요, 재고 부족, 피킹 용량 대안의 시나리오를 실행한다. 초안 코드의 자체
검사는 독립 검증이 아니다. 서버 finish는 문법·범위만 검사하며 model.test.cjs를
호스트에서 실행하지 않는다. 기준 입력과 기대 결과의 외부 검토가 별도로 필요하다.

## 유지해야 하는 검사 계약

window.DASModel.run(input)는 순수하고 결정적인 계산이며 defaults와 checks()를 함께
제공한다. 입력은 days, ordersPerDay, initialStock, reorderPoint, batch, leadTime,
servers, serviceTime, seed 숫자다. batch=0은 보충 중지이고 ordersPerDay=0도 허용한다.
출력 키는 parameters, arrivals, completed, backlog, inService, stock, received,
allocated, incoming, completionRate, meanOrderDays, invariants를 유지한다.
재고 보존: initialStock+received=stock+allocated. 주문 보존:
arrivals=completed+backlog+inService. allocated=completed+inService.
관측기간의 arrivals=days*ordersPerDay이며 모든 개수와 평균 시간은 0 이상이다.
기말 미완료 수요를 완료율·평균 완료 시간의 완료 주문에 포함하면 안 된다.
DOM #model-form, #run-model, #model-results, #model-checks, #model-scene와
#days/#orders/#stock/#reorder/#batch/#lead/#servers/#service/#seed 입력을 보존한다.
결과 요소의 data-status/data-completed/data-backlog, 검사 목록의 data-status를
실제 실행 결과에 맞춘다. 검사 통과를 하드코딩하지 않는다. 원래 DES 문제와 계약을
바꿔야 하면 기존 검증을 통과했다고 주장하지 말고 변경 필요성과 새 검증 기준을 보고한다.
"""
HTML = """<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>DAS Lab · 공급망 DES 초안</title><link rel="stylesheet" href="style.css">
<script src="model.js" defer></script><script src="app.js" defer></script></head>
<body><header><strong>DAS Lab</strong><span>공급망 DES · 분리된 연구 초안</span></header>
<main><h1>재고와 피킹 용량을 비교합니다</h1><p>주문 도착·피킹·재입고를 사건 단위로 계산하는 합성 데모입니다. 실고객 성과나 검증된 운영 모델을 뜻하지 않습니다.</p>
<form id="model-form"><fieldset><legend>같은 수요 조건에서 대안을 실험하세요</legend>
<label>관측 기간 (일)<input id="days" name="days" type="number" min="1" max="30" value="7"></label>
<label>하루 주문 수<input id="orders" name="ordersPerDay" type="number" min="0" max="100" value="12"></label>
<label>초기 재고<input id="stock" name="initialStock" type="number" min="0" max="10000" value="30"></label>
<label>재주문 기준<input id="reorder" name="reorderPoint" type="number" min="0" max="10000" value="20"></label>
<label>보충 수량 (0은 중지)<input id="batch" name="batch" type="number" min="0" max="10000" value="40"></label>
<label>보충 리드타임 (일)<input id="lead" name="leadTime" type="number" min="0.1" max="30" step="0.1" value="2"></label>
<label>피킹 인원<input id="servers" name="servers" type="number" min="1" max="20" value="1"></label>
<label>건당 피킹 시간 (일)<input id="service" name="serviceTime" type="number" min="0.01" max="2" step="0.01" value="0.05"></label>
<label>수요 난수 시드<input id="seed" name="seed" type="number" min="1" max="999999" value="42"></label>
</fieldset><button id="run-model" type="submit">시나리오 계산</button></form>
<section aria-labelledby="results-title"><h2 id="results-title">모델 계산 결과</h2><div id="model-results" aria-live="polite"></div>
<svg id="model-scene" viewBox="0 0 600 140" role="img" aria-label="기말 재고와 주문 상태"><rect x="10" y="20" width="160" height="90" rx="8" fill="#e4f2f2"></rect><rect x="220" y="20" width="160" height="90" rx="8" fill="#e6edf8"></rect><rect x="430" y="20" width="160" height="90" rx="8" fill="#f7eddc"></rect><text x="90" y="53" text-anchor="middle">창고 재고</text><text id="scene-stock" x="90" y="85" text-anchor="middle">—</text><text x="300" y="53" text-anchor="middle">피킹 중</text><text id="scene-active" x="300" y="85" text-anchor="middle">—</text><text x="510" y="53" text-anchor="middle">대기 주문</text><text id="scene-backlog" x="510" y="85" text-anchor="middle">—</text></svg></section>
<section><h2>초안의 시나리오 검사</h2><p>아래는 모델 코드가 실행한 자체 검사입니다. 독립 검증·현장 데이터 적합성 확인은 별도입니다.</p><ul id="model-checks" aria-live="polite"></ul></section>
<footer>단일 창고 · 주문당 1개 · 고정 피킹/보충 시간 · 운송·가격·비용 제외 · 외부 연결 없음</footer></main></body></html>"""
CSS = """*{box-sizing:border-box}body{margin:0;background:#f5f7fa;color:#172b3a;font:16px/1.6 system-ui,sans-serif}header{padding:18px max(20px,calc((100% - 1040px)/2));background:#153d58;color:white;display:flex;gap:24px;flex-wrap:wrap}main{max-width:1080px;margin:auto;padding:24px}h1{font-size:1.9rem;line-height:1.3}fieldset{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px;border:1px solid #c8d3df;border-radius:8px;padding:20px;background:white}legend{font-weight:600}label{display:flex;flex-direction:column;gap:6px}input{min-width:0;width:100%;padding:9px;border:1px solid #9bacba;border-radius:4px;font:inherit}button{margin:16px 0;padding:12px 22px;border:0;border-radius:5px;background:#165f79;color:white;font:600 16px system-ui;cursor:pointer}button:focus-visible,input:focus-visible{outline:3px solid #efb642;outline-offset:3px}section{background:white;border:1px solid #d9e2ea;border-radius:8px;padding:20px;margin:18px 0}#model-results{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}.metric{padding:12px;background:#f3f6f8}.metric strong{display:block;font-size:1.4rem}svg{width:100%;max-height:190px;margin-top:18px}text{font:16px system-ui;fill:#172b3a}[data-status=failed]{color:#a52222}[data-status=passed]{color:#216146}footer{font-size:.85rem;color:#536575;margin:28px 0}@media(max-width:650px){main{padding:16px}fieldset,#model-results{grid-template-columns:repeat(2,minmax(0,1fr))}h1{font-size:1.5rem}header span{font-size:.85rem}}
"""
MODEL = r'''"use strict";
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
'''
APP = r'''"use strict";
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
'''
TEST = r'''"use strict";
// Execute only inside the authorized prototype worker sandbox, never as a host verification step.
const model=require("./model.js");
const failures=model.checks().filter(check=>!check.passed);
if(failures.length)throw new Error(JSON.stringify(failures));
console.log("Synthetic model self-checks passed; not independent domain validation.");
'''
SEED = {"README.md": README, "index.html": HTML, "style.css": CSS, "model.js": MODEL, "app.js": APP, "model.test.cjs": TEST}


def _mask_regex_literals(source):
    """Hide only unambiguous regex bodies from the forbidden-token inspection.

    This is not a JavaScript validator. Strings, comments and template text stay
    visible to the existing conservative checks; executable template expressions
    are scanned as code. Ambiguous slashes (including division after an operand)
    remain visible. The unchanged source still passes through node --check.
    """
    masked, length = list(source), len(source)
    legacy_code_marker = False

    def quoted(index, quote):
        index += 1
        while index < length:
            char = source[index]
            if char == "\\":
                index += 2
            elif char == quote:
                return index + 1
            else:
                index += 1
        return length

    def regex_end(index):
        cursor, in_class = index + 1, False
        while cursor < length:
            char = source[cursor]
            if char in "\r\n\u2028\u2029":
                return None
            if char == "\\":
                if cursor + 1 >= length or source[cursor + 1] in "\r\n\u2028\u2029":
                    return None
                cursor += 2
                continue
            if char == "[":
                in_class = True
            elif char == "]":
                in_class = False
            elif char == "/" and not in_class:
                return cursor + 1
            cursor += 1
        return None

    def template(index, depth):
        while index < length:
            if source[index] == "\\":
                index += 2
            elif source[index] == "`":
                return index + 1
            elif source.startswith("${", index):
                index = code(index + 2, True, depth + 1)
            else:
                index += 1
        return length

    def code(index=0, expression=False, depth=0):
        nonlocal legacy_code_marker
        if depth > 100:
            raise ValueError("Prototype template nesting limit exceeded")
        braces, regex_position = 0, True
        while index < length:
            char = source[index]
            if char.isspace():
                index += 1
                continue
            # Annex B comments/hashbangs are special only in code. Ordinary
            # quoted/template text and regex bodies never enter this branch.
            # Fall back before a legacy comment's quotes can confuse scanning.
            if any(source.startswith(marker, index) for marker in ("<!--", "-->", "#!")):
                legacy_code_marker = True
                return length
            if source.startswith("//", index):
                while index < length and source[index] not in "\r\n\u2028\u2029":
                    index += 1
                continue
            if source.startswith("/*", index):
                end = source.find("*/", index + 2)
                index = length if end < 0 else end + 2
                continue
            if char in "\"'":
                index, regex_position = quoted(index, char), False
                continue
            if char == "`":
                index, regex_position = template(index + 1, depth), False
                continue
            if char == "/":
                end = regex_end(index) if regex_position else None
                if end is not None:
                    masked[index:end] = " " * (end - index)
                    index = end
                    # Flags are left visible; invalid flags still fail parsing.
                    while index < length and source[index].isalpha():
                        index += 1
                else:
                    index += 2 if source.startswith("/=", index) else 1
                regex_position = False
                continue
            if char.isalnum() or char in "_$\\":
                index += 1
                while index < length and (source[index].isalnum() or source[index] in "_$\\"):
                    index += 1
                regex_position = False
                continue
            if char == "{":
                braces += 1
            elif char == "}":
                if expression and braces == 0:
                    return index + 1
                braces = max(0, braces - 1)
            # Only syntactically certain operand starts; do not guess after
            # identifiers, closing braces/parentheses, or postfix operators.
            regex_position = char in "=([{:;,?"
            index += 1
        return index

    code()
    return source if legacy_code_marker else "".join(masked)


class PrototypeWorkspace:
    def __init__(self, root, data_dir):
        self.root, self.data_dir = Path(root).absolute(), Path(data_dir).absolute()
        self.lock, self.servers = threading.RLock(), {}

    def _folder(self, folder):
        return _safe(folder, self.data_dir)

    def _files(self, workspace, *, repair=False):
        workspace = _safe(workspace, self.data_dir)
        if not workspace.is_dir():
            raise ValueError("Prototype workspace is missing")
        found = {}
        total = 0
        for candidate in workspace.iterdir():
            path = _safe(candidate, workspace)
            if path.name not in FILES or not path.is_file():
                raise ValueError("Unexpected prototype file: " + path.name)
            limit = WORKSPACE_LIMIT_BYTES if repair and path.name == "README.md" else FILE_LIMIT_BYTES
            if path.stat().st_size > limit:
                raise ValueError("Prototype file limit exceeded")
            raw = path.read_bytes()
            if len(raw) > limit:
                raise ValueError("Prototype file limit exceeded during read")
            raw.decode("utf-8")
            total += len(raw)
            if total > WORKSPACE_LIMIT_BYTES:
                raise ValueError("Prototype size limit exceeded")
            found[path.name] = raw
        if set(found) != set(FILES):
            raise ValueError("Required prototype files are missing")
        return found

    @staticmethod
    def _identity(project_id, profile):
        if not isinstance(project_id, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,79}", project_id):
            raise ValueError("Invalid prototype project identity")
        if profile not in (DEFAULT_PROFILE, NETWORK_PROFILE):
            raise ValueError("Unknown prototype profile")
        return {"project_id": project_id, "profile": profile}

    def identity(self, folder):
        folder = self._folder(folder)
        baseline = json.loads(_safe(folder / "prototype-baseline.json", folder).read_text(encoding="utf-8"))
        # Historical workspaces predate profile metadata and belong only to the
        # original inventory project. They can never seed a new business project.
        return self._identity(baseline.get("project_id", "daslab-growth"), baseline.get("profile", DEFAULT_PROFILE))

    def repair_snapshot(self, folder, *, project_id, profile, expected_baseline_sha256=None):
        """Pin bounded source bytes for repair only; never parse or execute code."""
        snapshot, _ = self._repair_material(folder, project_id=project_id, profile=profile,
                                            expected_baseline_sha256=expected_baseline_sha256)
        return snapshot

    def _repair_material(self, folder, *, project_id, profile, expected_baseline_sha256=None):
        identity = self._identity(project_id, profile)
        folder = self._folder(folder)
        path = _safe(folder / "prototype-baseline.json", folder)
        if not path.is_file() or path.stat().st_size > 100_000 or path.stat().st_nlink != 1:
            raise ValueError("Invalid prototype repair baseline")
        raw = path.read_bytes()
        if len(raw) > 100_000:
            raise ValueError("Prototype repair baseline limit exceeded")
        baseline_hash = _hash(raw)
        if expected_baseline_sha256 is not None and baseline_hash != expected_baseline_sha256:
            raise ValueError("Prototype repair baseline changed")
        baseline = json.loads(raw)
        if {key: baseline.get(key) for key in identity} != identity:
            raise ValueError("Prototype repair belongs to a different project or profile")
        workspace = _safe(folder / "workspace", folder)
        for candidate in workspace.iterdir():
            checked = _safe(candidate, workspace)
            if checked.is_file() and checked.stat().st_nlink != 1:
                raise ValueError("Prototype repair files must not be hard links")
        # Oversized verification prose may be preserved for editing only. Code
        # keeps the release cap, and the six-file aggregate cap never changes.
        files = self._files(workspace, repair=True)
        if _hash(_safe(path, folder).read_bytes()) != baseline_hash:
            raise ValueError("Prototype repair baseline changed during inspection")
        return ({**identity, "repairable": True, "repair_artifacts": {name: _hash(value) for name, value in files.items()},
                 "repair_baseline_sha256": baseline_hash}, files)

    def _repair_files(self, folder, receipt, identity):
        """Copy authority is a server receipt, not a worker claim or passing test."""
        if (not isinstance(receipt, dict) or receipt.get("repairable") is not True
                or receipt.get("ready") is not False
                or receipt.get("attempt_id") != Path(folder).name
                or {key: receipt.get(key) for key in identity} != identity):
            raise ValueError("Invalid prototype repair source")
        expected = receipt.get("repair_artifacts")
        baseline_hash = receipt.get("repair_baseline_sha256")
        if (not isinstance(expected, dict) or set(expected) != set(FILES)
                or any(not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value) for value in expected.values())
                or not isinstance(baseline_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", baseline_hash)):
            raise ValueError("Invalid prototype repair hashes")
        snapshot, files = self._repair_material(folder, **identity, expected_baseline_sha256=baseline_hash)
        if snapshot["repair_artifacts"] != expected:
            raise ValueError("Prototype repair source changed")
        return files

    def prepare(self, folder, previous_folder=None, *, project_id="daslab-growth", profile=DEFAULT_PROFILE,
                repair_source=None):
        identity = self._identity(project_id, profile)
        folder = self._folder(folder)
        workspace = _safe(folder / "workspace", folder)
        if workspace.exists():
            raise ValueError("Prototype workspace already exists")
        if profile == NETWORK_PROFILE:
            from .network_prototypes import SEED as seed
        else:
            seed = SEED
        files = {name: value.encode("utf-8") for name, value in seed.items()}
        inherited = None
        if repair_source is not None and previous_folder is None:
            raise ValueError("Prototype repair needs an explicit previous workspace")
        if previous_folder is not None:
            previous = self._folder(previous_folder)
            if repair_source is not None:
                files = self._repair_files(previous, repair_source, identity)
            else:
                if self.identity(previous) != identity:
                    raise ValueError("Previous prototype belongs to a different project or profile")
                receipt = self.finish(previous)
                if not receipt["ready"]:
                    raise ValueError("Previous prototype failed static checks")
                files = self._files(previous / "workspace")
                if {name: _hash(raw) for name, raw in files.items()} != receipt["artifacts"]:
                    raise ValueError("Previous prototype changed during copy")
            inherited = str(previous)
        workspace.mkdir(parents=True)
        for name, raw in files.items():
            (workspace / name).write_bytes(raw)
        seed_source = "built-in synthetic network GIS seed" if profile == NETWORK_PROFILE else "built-in synthetic DES seed"
        baseline = {"files": {name: _hash(raw) for name, raw in files.items()}, "source": inherited or seed_source,
                    **identity, "seed": inherited is None}
        if repair_source is not None:
            baseline["repair_source"] = {"attempt_id": repair_source.get("attempt_id"),
                                         "baseline_sha256": repair_source["repair_baseline_sha256"],
                                         "note": "Unverified source preserved for repair; original failure remains."}
        (folder / "prototype-baseline.json").write_text(json.dumps(baseline), encoding="utf-8")
        return {**identity, "workspace": str(workspace), "files": list(FILES),
                "target": "Isolated synthetic simulation prototype", "source": baseline["source"],
                "scope": "Only these six files; no existing website/app edits, external dependencies, network, installs, publication or customer contact.",
                "preview_read_only": True, "model_verified": False,
                "release_contract": {
                    "per_file_limit_bytes": FILE_LIMIT_BYTES, "total_limit_bytes": WORKSPACE_LIMIT_BYTES,
                    "test_literal_require_allowlist": list(TEST_IMPORTS),
                    "imports": "model.test.cjs에서만 위 문자열의 정확한 require 호출을 허용합니다. node:crypto를 포함한 다른 모듈, 동적 require, import는 금지합니다. model.js/app.js는 모듈 import 없이 작성하세요.",
                    "verification_summary": "README에는 문제·가정과 검증 시나리오, 핵심 지표, 허용오차, 통과·실패 요약만 간결하게 남기세요. 주문별 전체 JSON·긴 실행 로그·브라우저 덤프를 붙이지 마세요.",
                    "repair_only_readme": "재작업용 README는 6파일 전체 1,000,000바이트 안에서 임시 보존할 수 있습니다. 최종 제출 전 README를 포함한 각 파일을 500,000바이트 이내로 줄여야 하며, 초과 상태는 미리보기·완료 검사를 통과하지 못합니다."},
                "checks": "Host performs file/syntax inspection only. Generated tests may run only inside the authorized worker sandbox; browser verification is separate."}

    def finish(self, folder):
        checks, changed = [], []
        try:
            folder = self._folder(folder)
            files = self._files(folder / "workspace")
            baseline = json.loads(_safe(folder / "prototype-baseline.json", folder).read_text(encoding="utf-8"))
            identity = self._identity(baseline.get("project_id", "daslab-growth"), baseline.get("profile", DEFAULT_PROFILE))
            if identity["profile"] == NETWORK_PROFILE:
                from .network_prototypes import REQUIRED_IDS as required_ids
            else:
                required_ids = REQUIRED_IDS
            html = _HTML(files["index.html"].decode("utf-8"))
            if (html.unsafe or html.body_count != 1 or not required_ids <= html.ids
                    or html.scripts != ["model.js", "app.js"] or html.styles != ["style.css"]):
                raise ValueError("Prototype HTML must preserve local scripts, styles and model controls")
            source = files["index.html"].decode("utf-8")
            if not re.search(r"</body\s*>", source, re.I):
                raise ValueError("Prototype HTML body is incomplete")
            if re.search(r"(?:src|href|action|poster)\s*=\s*[\"']\s*(?:[a-z][a-z0-9+.-]*:|//)", source, re.I):
                raise ValueError("External HTML resources are not allowed")
            css = files["style.css"].decode("utf-8")
            if re.search(r"@import\b|url\s*\(", css, re.I):
                raise ValueError("External CSS resources are not allowed")
            for name in ("model.js", "app.js", "model.test.cjs"):
                # Mask before removing allowed calls: otherwise an operand such
                # as require('node:fs') / ... could look like a regex position.
                script = _mask_regex_literals(files[name].decode("utf-8"))
                if name == "model.test.cjs":
                    # These exact local/builtin imports support worker-sandbox
                    # assertions and DOM stubs. This file is never served or
                    # executed by the host; finish only runs node --check.
                    allowed = "|".join(re.escape(value) for value in TEST_IMPORTS)
                    script = re.sub(r"\brequire\s*\(\s*(['\"])(?:" + allowed + r")\1\s*\)", "", script)
                if re.search(r"\b(?:import|require|fetch|XMLHttpRequest|WebSocket|EventSource|importScripts)\b|\b(?:sendBeacon|serviceWorker)\b", script):
                    raise ValueError("Network access and external code imports are not allowed")
            checks.append("Six bounded UTF-8 files; no links, reparse points, external references or imports")
            node = shutil.which("node")
            if not node:
                raise ValueError("Node.js unavailable: syntax inspection cannot run")
            for name in ("model.js", "app.js", "model.test.cjs"):
                result = subprocess.run([node, "--check", str(folder / "workspace" / name)], capture_output=True, text=True, timeout=20, shell=False)
                if result.returncode:
                    raise ValueError("Prototype syntax check failed: " + name + " " + result.stderr[:500])
            checks.append("JavaScript syntax parsed with node --check; model tests were NOT executed on host")
            artifacts = {name: _hash(raw) for name, raw in files.items()}
            changed = sorted(name for name, digest in artifacts.items() if baseline["files"].get(name) != digest)
            seed_only = not changed and (baseline.get("seed") is True or baseline["source"] == "built-in synthetic DES seed")
            return {"ready": True, **identity, "artifacts": artifacts, "changed_files": changed, "seed_only": seed_only,
                    "checks": checks, "error": None, "model_verified": False, "browser_verified": False, "applied_to_live": False}
        except (ValueError, OSError, UnicodeError, KeyError, subprocess.SubprocessError) as exc:
            return {"ready": False, "artifacts": {}, "changed_files": changed, "checks": checks, "error": str(exc),
                    "model_verified": False, "browser_verified": False, "applied_to_live": False}

    def open_preview(self, folder, expected_artifacts=None):
        with self.lock:
            folder = self._folder(folder)
            key = str(folder)
            if key in self.servers and expected_artifacts is None:
                return self._touch_preview(key)[1]
            result = self.finish(folder)
            if not result["ready"]:
                raise ValueError(result["error"])
            files = self._files(folder / "workspace")
            artifacts = {name: _hash(raw) for name, raw in files.items()}
            if artifacts != result["artifacts"] or (expected_artifacts is not None and artifacts != expected_artifacts):
                raise ValueError("Prototype no longer matches verified artifacts")
            if key in self.servers:
                if self.servers[key][3] != artifacts:
                    raise ValueError("Frozen preview no longer matches pinned prototype")
                return self._touch_preview(key)[1]
            if len(self.servers) >= MAX_PREVIEWS:
                oldest = next(iter(self.servers))
                self._close_preview(self.servers.pop(oldest))

            class Handler(BaseHTTPRequestHandler):
                def log_message(self, *args):
                    pass

                def respond(self, status, body, mime="application/json"):
                    self.send_response(status)
                    self.send_header("Content-Type", mime + "; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.send_header("Referrer-Policy", "no-referrer")
                    self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'none'; object-src 'none'; frame-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'; worker-src 'none'")
                    self.end_headers()
                    try:
                        self.wfile.write(body)
                    except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                        pass

                def do_POST(self):
                    self.respond(405, b'{"error":"Read-only simulation preview"}')

                do_PUT = do_DELETE = do_PATCH = do_POST

                def do_GET(self):
                    port = self.server.server_address[1]
                    if self.headers.get("Host") not in (f"127.0.0.1:{port}", f"localhost:{port}"):
                        self.respond(403, b"{}")
                        return
                    path = unquote(urlsplit(self.path).path)
                    if "\\" in path or any(part in (".", "..") for part in path.split("/")):
                        self.respond(404, b"{}")
                        return
                    name = "index.html" if path == "/" else path[1:]
                    if name not in PAGE_FILES or path.startswith("//"):
                        self.respond(404, b"{}")
                        return
                    self.respond(200, files[name], PAGE_FILES[name])

            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            server.daemon_threads = True
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            url = f"http://127.0.0.1:{server.server_address[1]}/"
            self.servers[key] = (server, url, thread, artifacts)
            return url

    def _touch_preview(self, key):
        # Dict order is least-to-most recently used. Only successful access
        # refreshes a slot; an invalid artifact pin cannot evict another preview.
        entry = self.servers.pop(key)
        self.servers[key] = entry
        return entry

    @staticmethod
    def _close_preview(entry):
        server, _, thread, _ = entry
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    def close(self):
        with self.lock:
            for entry in self.servers.values():
                self._close_preview(entry)
            self.servers.clear()
