"""Independent browser checks for the documented supply-chain demo contract.

These are synthetic conservation/edge-case checks, not industrial validation.
Generated model.test.cjs is never executed on the host.
"""
import asyncio
import json
import math
import re
import time
from datetime import datetime, timezone

from .browser_verification import (BrowserVerifier, BrowserUnavailable, _preview_url,
                                   _origin, _allowed_request, _installed_browser, _output_directory)
from .process_env import child_env, require_clean_process_env


POLICY_INPUT = {"days": 14, "ordersPerDay": 6, "initialStock": 12, "seed": 42,
                "safetyStockDays": 2, "emergencyCapacity": 6}


def _number(value, name, *, integer=False):
    if (type(value) not in (int, float) or not math.isfinite(value) or value < 0
            or (integer and int(value) != value)):
        raise ValueError(name + ": 음수·비유한 값 또는 잘못된 수량")
    return value


def _equal(actual, expected, name):
    _number(actual, name)
    if not math.isclose(actual, expected, rel_tol=1e-8, abs_tol=1e-7):
        raise ValueError(name + ": 원시 기록과 집계 불일치")


def _rows(value, name, maximum=10000):
    if not isinstance(value, list) or len(value) > maximum or any(not isinstance(row, dict) for row in value):
        raise ValueError(name + ": 유한한 기록 배열 필요")
    return value


def _identities(rows, name):
    ids = [row.get("id") for row in rows]
    if any(not isinstance(value, str) or not value for value in ids) or len(set(ids)) != len(ids):
        raise ValueError(name + ": 비어 있거나 중복된 id")
    return {row["id"]: row for row in rows}


def validate_policy_comparison(value, expected):
    """Recompute the small synthetic policy contract from browser-returned logs.

    This never executes employee-generated code on the host. Values are synthetic
    accounting evidence, not proof that a policy is effective for a real customer.
    """
    if not isinstance(value, dict) or not isinstance(value.get("parameters"), dict):
        raise ValueError("정책 비교 입력 기록 없음")
    for key, wanted in expected.items():
        if type(value["parameters"].get(key)) not in (int, float) or value["parameters"][key] != wanted:
            raise ValueError("정책 비교 입력 미반영: " + key)
    days, initial = expected["days"], expected["initialStock"]
    demand = _rows(value.get("demandTrace"), "공통 수요")
    demand_by_id = _identities(demand, "공통 수요")
    if len(demand) != days * expected["ordersPerDay"]:
        raise ValueError("공통 수요 건수 불일치")
    for order in demand:
        arrival = _number(order.get("arrivalAt"), "수요 도착 시각")
        due = _number(order.get("dueAt"), "약속 시각")
        if arrival >= days or due < arrival or type(order.get("quantity")) is not int or order["quantity"] != 1:
            raise ValueError("주문당 1개·관측 기간·납기 계약 위반")
    if not isinstance(value.get("shockTrace"), list) or len(value["shockTrace"]) > 100:
        raise ValueError("공통 공급 충격 기록 필요")
    policies = _rows(value.get("policies"), "정책", 3)
    if len(policies) != 3 or {p.get("policy") for p in policies} != {"P0", "P1", "P2"}:
        raise ValueError("P0/P1/P2 정책이 각각 필요")
    summaries = {}
    for policy in policies:
        name = policy["policy"]
        orders = _rows(policy.get("orders"), name + " 주문")
        by_id = _identities(orders, name + " 주문")
        if by_id.keys() != demand_by_id.keys():
            raise ValueError(name + ": 공통 수요와 주문 id 불일치")
        for order in orders:
            previous = demand_by_id[order["id"]]["arrivalAt"]
            absent = False
            for key in ("reservedAt", "shippedAt", "deliveredAt"):
                current = order[key]
                if current is None:
                    absent = True
                elif absent or _number(current, name + " " + key) < previous or current > days:
                    raise ValueError(name + ": 주문 상태 시각 순서 오류")
                else:
                    previous = current
        purchases = _rows(policy.get("purchaseOrders"), name + " 발주")
        purchase_ids = _identities(purchases, name + " 발주")
        emergency_units = regular_units = 0
        for po in purchases:
            ordered = _number(po.get("orderedAt"), name + " 발주 시각")
            quantity = _number(po.get("quantity"), name + " 발주량", integer=True)
            emergency = _number(po.get("emergencyQuantity"), name + " 긴급 전환량", integer=True)
            depart = _number(po.get("regularDepartureAt"), name + " 정규 출발")
            arrive = _number(po.get("regularArrivalAt"), name + " 정규 도착")
            if not quantity or ordered > days or ordered > depart or depart > arrive or emergency > quantity:
                raise ValueError(name + ": 발주·전환 물량/시각 오류")
            if name != "P2" and emergency or emergency > expected["emergencyCapacity"]:
                raise ValueError(name + ": 허용되지 않은 긴급 전환")
            if emergency:
                expedited_depart = _number(po.get("emergencyDepartureAt"), name + " 긴급 출발")
                expedited_arrive = _number(po.get("emergencyArrivalAt"), name + " 긴급 도착")
                if not ordered <= expedited_depart <= depart or expedited_arrive < expedited_depart:
                    raise ValueError(name + ": 출발 전 전환 계약 위반")
            elif po.get("emergencyDepartureAt") is not None or po.get("emergencyArrivalAt") is not None:
                raise ValueError(name + ": 긴급 물량 없는 운송 기록")
            emergency_units += emergency
            regular_units += quantity - emergency
        receipts = _rows(policy.get("receipts"), name + " 입고")
        _identities(receipts, name + " 입고")
        received_keys = set()
        for entry in receipts:
            key = (entry.get("purchaseOrderId"), entry.get("mode"))
            po = purchase_ids.get(key[0])
            if po is None or key[1] not in ("regular", "emergency") or key in received_keys:
                raise ValueError(name + ": 출처 없는 입고 또는 동일 발주 중복 입고")
            received_keys.add(key)
            quantity = po["emergencyQuantity"] if key[1] == "emergency" else po["quantity"] - po["emergencyQuantity"]
            when = po[key[1] + "ArrivalAt"]
            if quantity <= 0 or when is None or when > days or entry.get("time") != when:
                raise ValueError(name + ": 예정 물량/시각과 실제 입고 불일치")
            _equal(entry.get("quantity"), quantity, name + " 전환 후 입고량")
        for po in purchases:
            for mode in ("regular", "emergency"):
                qty = po["emergencyQuantity"] if mode == "emergency" else po["quantity"] - po["emergencyQuantity"]
                if qty > 0 and po[mode + "ArrivalAt"] <= days and (po["id"], mode) not in received_keys:
                    raise ValueError(name + ": 기간 내 예정 입고 누락")
        states = _rows(policy.get("stateLog"), name + " 상태 기록")
        if not states or states[0].get("time") != 0 or states[-1].get("time") != days:
            raise ValueError(name + ": 초기·종료 상태 기록 필요")
        # At equal event times, the last snapshot is the state after all events.
        # Every inventory change time must occur so the holding integral is exact.
        by_time = {}
        previous = -1
        for state in states:
            when = _number(state.get("time"), name + " 상태 시각")
            if when < previous or when > days:
                raise ValueError(name + ": 상태 기록 시각 순서 오류")
            previous = when
            for key in ("onHand", "reserved", "backlog", "onOrder", "received", "shipped", "completed"):
                _number(state.get(key), name + " " + key, integer=True)
            if type(state.get("inventoryPosition")) not in (int, float) or not math.isfinite(state["inventoryPosition"]):
                raise ValueError(name + ": 잘못된 재고 위치")
            if state["reserved"] > state["onHand"]:
                raise ValueError(name + ": 예약량이 실재고 초과")
            position = state["onHand"] - state["reserved"] + state["onOrder"] - state["backlog"]
            if state["inventoryPosition"] != position:
                raise ValueError(name + ": 예약/미예약 주문 이중 차감")
            by_time[when] = state
        times = {0, days}
        times.update(entry["time"] for entry in receipts)
        times.update(po["orderedAt"] for po in purchases)
        times.update(order["arrivalAt"] for order in demand)
        times.update(order[key] for order in orders for key in ("reservedAt", "shippedAt", "deliveredAt") if order[key] is not None)
        if not times <= by_time.keys():
            raise ValueError(name + ": 상태 전이 시각 기록 누락")
        holding = 0
        ordered_states = list(by_time.items())
        for index, (when, state) in enumerate(ordered_states):
            received = sum(r["quantity"] for r in receipts if r["time"] <= when)
            shipped = sum(o["shippedAt"] is not None and o["shippedAt"] <= when for o in orders)
            completed = sum(o["deliveredAt"] is not None and o["deliveredAt"] <= when for o in orders)
            reserved = sum(o["reservedAt"] is not None and o["reservedAt"] <= when and (o["shippedAt"] is None or o["shippedAt"] > when) for o in orders)
            arrived = sum(o["arrivalAt"] <= when for o in demand)
            on_order = sum(po["quantity"] for po in purchases if po["orderedAt"] <= when) - received
            actual = {"received": received, "shipped": shipped, "completed": completed,
                      "onHand": initial + received - shipped, "reserved": reserved,
                      "backlog": arrived - shipped - reserved, "onOrder": on_order}
            for key, expected_value in actual.items():
                _equal(state[key], expected_value, name + " " + key)
            if index + 1 < len(ordered_states):
                holding += state["onHand"] * (ordered_states[index + 1][0] - when)
        metrics, costs = policy["metrics"], policy["costs"]
        done = [o for o in orders if o["deliveredAt"] is not None]
        final = states[-1]
        actual = {"arrivals": len(demand), "completed": len(done), "backlog": final["backlog"],
                  "reserved": final["reserved"], "inTransit": final["shipped"] - len(done),
                  "otif": sum(o["deliveredAt"] <= demand_by_id[o["id"]]["dueAt"] for o in done) / len(demand) if demand else 0,
                  "meanOrderDays": sum(o["deliveredAt"] - demand_by_id[o["id"]]["arrivalAt"] for o in done) / len(done) if done else 0,
                  "holdingUnitDays": holding}
        for key, expected_value in actual.items():
            _equal(metrics.get(key), expected_value, name + " KPI " + key)
        for key in ("holdingRate", "regularUnitRate", "emergencyUnitRate", "orderFee"):
            _number(costs.get(key), name + " " + key)
        amounts = {"holding": holding * costs["holdingRate"], "regular": regular_units * costs["regularUnitRate"],
                   "emergency": emergency_units * costs["emergencyUnitRate"], "ordering": len(purchases) * costs["orderFee"]}
        for key, expected_value in amounts.items():
            _equal(costs.get(key), expected_value, name + " 비용 " + key)
        _equal(costs.get("total"), sum(amounts.values()), name + " 총비용")
        summaries[name] = {"metrics": metrics, "costs": costs, "orders": orders,
                           "purchaseOrders": purchases, "receipts": receipts, "stateLog": states}
    rates = ("holdingRate", "regularUnitRate", "emergencyUnitRate", "orderFee")
    if any(any(summaries[name]["costs"][key] != summaries["P0"]["costs"][key] for key in rates) for name in ("P1", "P2")):
        raise ValueError("정책 간 비용 단가가 다름")
    return summaries


POLICY_CAPTURE_SCRIPT = """input => {
    if (typeof window.DASModel.comparePolicies !== 'function') return {present:false};
    const compare = window.DASModel.comparePolicies;
    const result = {present:true, runs:{
        baseline:compare({...input}), repeat:compare({...input}),
        no_emergency:compare({...input,emergencyCapacity:0}),
        no_safety:compare({...input,safetyStockDays:0}),
        no_demand:compare({...input,ordersPerDay:0})}, display:[]};
    for (const policy of ['P0','P1','P2']) {
        const row=document.querySelector('#policy-comparison [data-policy="'+policy+'"]');
        result.display.push({policy,otif:row?.getAttribute('data-otif'),
            total:row?.getAttribute('data-total-cost')});
    }
    const raw=JSON.stringify(result);
    if(raw.length>2000000) throw new Error('정책 비교 검사 기록 크기 제한 초과');
    return JSON.parse(raw);
}"""


def policy_comparison_checks(capture):
    """Validate optional comparison evidence without trusting self-check flags."""
    if not isinstance(capture, dict) or capture.get("present") is not True:
        return [], {"present": False, "status": "not_present", "scope": "기존 기본 모델 검사만 수행"}
    checks, summaries, errors = [], {}, []
    variations = {"baseline": {}, "repeat": {}, "no_emergency": {"emergencyCapacity": 0},
                  "no_safety": {"safetyStockDays": 0}, "no_demand": {"ordersPerDay": 0}}
    runs = capture.get("runs") or {}
    for name, changes in variations.items():
        try:
            summaries[name] = validate_policy_comparison(runs.get(name), {**POLICY_INPUT, **changes})
            checks.append({"name": "정책 원시 기록·보존식·KPI·비용 재계산 " + name, "status": "passed"})
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            detail = str(exc)[:200] or type(exc).__name__
            errors.append(name + ": " + detail)
            checks.append({"name": "정책 원시 기록·보존식·KPI·비용 재계산 " + name,
                           "status": "failed", "detail": detail})
    if len(summaries) == len(variations):
        def record(name, passed):
            checks.append({"name": name, "status": "passed" if passed else "failed"})
            if not passed:
                errors.append(name)
        record("정책 비교 같은 입력 재현성", runs["baseline"] == runs["repeat"])
        record("비교 정책 변경에도 같은 외생 수요·충격", all(
            runs[name][key] == runs["baseline"][key]
            for name in ("no_emergency", "no_safety") for key in ("demandTrace", "shockTrace")))
        for name, alternative in (("no_emergency", "P2"), ("no_safety", "P1")):
            record("대응 기능 0이면 기준 정책과 동일 " + name, all(
                summaries[name]["P0"][key] == summaries[name][alternative][key]
                for key in ("metrics", "costs", "orders")))
        record("기본 합성 조건에서 실제 긴급 전환 발생", any(
            po["emergencyQuantity"] > 0 for po in summaries["baseline"]["P2"]["purchaseOrders"]))
        display = capture.get("display")
        try:
            if not isinstance(display, list) or len(display) != 3:
                raise ValueError("정책 비교 화면 없음")
            displayed = {row["policy"]: row for row in display}
            if set(displayed) != {"P0", "P1", "P2"}:
                raise ValueError("정책 비교 화면 행 누락")
            for name, result in summaries["baseline"].items():
                _equal(float(displayed[name]["otif"]), result["metrics"]["otif"], name + " 화면 OTIF")
                _equal(float(displayed[name]["total"]), result["costs"]["total"], name + " 화면 비용")
            record("정책 비교 화면과 실제 집계 일치", True)
        except (ValueError, TypeError, KeyError) as exc:
            errors.append(str(exc)[:200])
            record("정책 비교 화면과 실제 집계 일치", False)
    receipt = {"present": True, "status": "passed" if checks and not errors else "failed",
               "scope": "단일 품목 14일 합성 정책의 원시 기록·보존식·납기·비용 및 경계 조건. 산업적 우월성 검증 아님",
               "scenarios": list(variations), "errors": errors}
    return checks, receipt


class PrototypeVerifier(BrowserVerifier):
    def verify(self, url, artifacts, output_dir, cancel_event=None):
        started = time.monotonic()
        receipt = {"status": "failed", "kind": "supply_chain_model_browser_checks",
                   "started_at": datetime.now(timezone.utc).isoformat(), "artifacts": artifacts,
                   "checks": [], "errors": [], "screenshots": [], "business_acceptance": False,
                   "scope": "합성 공급망 모델의 보존식·경계조건·재현성과 화면 입력·반응형 표시"}
        try:
            _preview_url(url)
            if not isinstance(artifacts, dict) or not {"index.html", "model.js", "app.js"} <= artifacts.keys():
                raise ValueError("Pinned prototype files are required")
            if any(not isinstance(v, str) or not re.fullmatch(r"[a-f0-9]{64}", v) for v in artifacts.values()):
                raise ValueError("Invalid prototype hash")
            output = _output_directory(output_dir) / "prototype-browser"
            output.mkdir(exist_ok=False)
            asyncio.run(self._run_bounded(url, output, receipt, cancel_event))
            if receipt["checks"] and not receipt["errors"] and all(c["status"] == "passed" for c in receipt["checks"]):
                receipt["status"] = "passed"
        except BrowserUnavailable as exc:
            receipt.update(status="unavailable")
            receipt["errors"].append(str(exc))
        except Exception as exc:
            receipt["errors"].append(str(exc)[:500] or type(exc).__name__)
        receipt["elapsed_seconds"] = round(time.monotonic() - started, 2)
        receipt["summary"] = "모델 기본 보존식과 실제 화면 동작을 확인했습니다. 현장 적합성·3D 품질은 별도 검수입니다." if receipt["status"] == "passed" else "데모의 모델·화면 검사를 통과하지 못했습니다."
        return receipt

    async def _run_browser(self, url, output, receipt):
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            raise BrowserUnavailable("격리 브라우저 검증 도구가 필요합니다.") from None
        expected_origin = _origin(url)
        require_clean_process_env()
        async with async_playwright() as p:
            try:
                browser = await p.chromium.launch(headless=True, channel=_installed_browser(), timeout=12000, env=child_env(),
                    args=["--disable-background-networking", "--disable-component-update", "--dns-prefetch-disable"])
            except Exception as exc:
                raise BrowserUnavailable("격리 브라우저 실행 실패: " + str(exc)[:150]) from exc
            try:
                context = await browser.new_context(viewport={"width": 1280, "height": 960}, permissions=[], service_workers="block")
                async def guard(route):
                    if _allowed_request(route.request.url, route.request.method, expected_origin):
                        await route.continue_()
                    else:
                        receipt["errors"].append("범위 밖 네트워크 요청 차단")
                        await route.abort()
                await context.route("**/*", guard)
                page = await context.new_page()
                page.set_default_timeout(7000)
                page.on("pageerror", lambda error: receipt["errors"].append(str(error)[:200]))
                await page.goto(url, wait_until="load")
                checks = await page.evaluate("""() => {
                    const run=window.DASModel.run, rows=[];
                    const check=(name,pass)=>rows.push({name,status:pass?'passed':'failed'});
                    for(const seed of [1,42,987]) {
                        const a=run({seed}), b=run({seed});
                        check('동일 입력 재현성 '+seed,JSON.stringify(a)===JSON.stringify(b));
                        check('재고 보존 '+seed,a.parameters.initialStock+a.received===a.stock+a.allocated);
                        check('주문 보존 '+seed,a.arrivals===a.completed+a.backlog+a.inService);
                        check('수요 건수 '+seed,a.arrivals===a.parameters.days*a.parameters.ordersPerDay);
                        check('음수·비유한 값 없음 '+seed,['arrivals','completed','backlog','inService','stock','received','allocated','incoming'].every(k=>Number.isFinite(a[k])&&a[k]>=0));
                    }
                    const noDemand=run({ordersPerDay:0}),noStock=run({initialStock:0,batch:0});
                    check('수요 0',noDemand.arrivals===0&&noDemand.completed===0&&noDemand.backlog===0);
                    check('공급 0',noStock.completed===0&&noStock.backlog===noStock.arrivals&&noStock.arrivals>0);
                    const input={initialStock:1000,batch:0,ordersPerDay:40,serviceTime:0.2};
                    const slow=run({...input,servers:1}),fast=run({...input,servers:2});
                    check('용량 증가 시 처리 증가',fast.completed>slow.completed&&fast.backlog<slow.backlog);
                    return rows;
                }""")
                receipt["checks"].extend(checks)
                comparison = await page.evaluate(POLICY_CAPTURE_SCRIPT, POLICY_INPUT)
                policy_checks, policy_receipt = policy_comparison_checks(comparison)
                receipt["checks"].extend(policy_checks)
                receipt["policy_comparison"] = policy_receipt
                if policy_receipt["present"]:
                    path = output / "policy-comparison.json"
                    path.write_text(json.dumps(comparison, ensure_ascii=False), encoding="utf-8")
                    policy_receipt["evidence_path"] = str(path)
                await page.locator("#stock").fill("0")
                await page.locator("#batch").fill("0")
                await page.locator("#run-model").click()
                displayed = await page.locator("#model-results").get_attribute("data-completed")
                receipt["checks"].append({"name": "화면 입력이 모델 결과에 반영", "status": "passed" if displayed == "0" else "failed"})
                for width, name in ((1280, "desktop"), (390, "mobile")):
                    await page.set_viewport_size({"width": width, "height": 960})
                    overflow = await page.evaluate("document.documentElement.scrollWidth > innerWidth + 2")
                    receipt["checks"].append({"name": name + " 가로 넘침 없음", "status": "failed" if overflow else "passed"})
                    path = output / (name + ".png")
                    await page.screenshot(path=str(path), full_page=True)
                    receipt["screenshots"].append({"name": name, "path": str(path)})
            finally:
                await browser.close()
