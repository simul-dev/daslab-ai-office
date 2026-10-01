"""Independent checks for the synthetic supply-network-gis-v1 contract.

The generated worker tests are never executed by this verifier. Placement costs,
flow constraints, order accounting and UI values are recomputed from raw data.
These checks do not establish customer suitability or real-world optimality.
"""
import asyncio
import copy
import itertools
import json
import math
import re
import time
from datetime import datetime, timezone
from uuid import uuid4

from .browser_verification import (BrowserVerifier, BrowserUnavailable, _preview_url,
                                   _origin, _route_request, _installed_browser, _output_directory)
from .process_env import child_env, require_clean_process_env


PROFILE = "supply-network-gis-v1"
DEFAULT_INPUT = {"seed": 42, "days": 7, "demandMultiplier": 1, "capacityMultiplier": 1,
                 "maxHubs": 2, "disruptionHub": "", "disruptionDays": 0}
KNOWN_DATA = {"facilities": [
    {"id": "B", "name": "Far hub", "lon": 128, "lat": 37, "capacityPerDay": 10, "fixedCostPerDay": 1},
    {"id": "A", "name": "Local hub", "lon": 127, "lat": 37, "capacityPerDay": 10, "fixedCostPerDay": 5}],
    "customers": [{"id": "X", "name": "Demand", "lon": 127, "lat": 37, "demandPerDay": 6}],
    "provenance": {"kind": "synthetic", "source": "Independent known-optimum verification fixture"}}


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _number(value, name, *, maximum=1e12, integer=False, minimum=0):
    _require(type(value) in (int, float) and math.isfinite(value)
             and minimum <= value <= maximum and (not integer or int(value) == value),
             name + ": invalid finite number")
    return value


def _equal(actual, expected, name):
    _number(actual, name)
    _require(math.isclose(actual, expected, rel_tol=1e-8, abs_tol=1e-6), name + ": raw evidence mismatch")


def _rows(value, name, maximum):
    _require(isinstance(value, list) and len(value) <= maximum
             and all(isinstance(row, dict) for row in value), name + ": invalid record list")
    return value


def _indexed(rows, name):
    ids = [row.get("id") for row in rows]
    _require(all(isinstance(key, str) and key and len(key) <= 80 for key in ids)
             and len(set(ids)) == len(ids), name + ": duplicate or invalid id")
    return dict(zip(ids, rows))


def distance_km(a, b):
    """Contract distance: spherical great-circle distance, R = 6371 km."""
    lat1, lat2 = math.radians(a["lat"]), math.radians(b["lat"])
    dlat, dlon = lat2 - lat1, math.radians(b["lon"] - a["lon"])
    angle = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(min(1, max(0, angle))))


def _rounded(value):
    # All contract quantities are nonnegative; match JavaScript Math.round.
    return math.floor(value + 0.5)


def placement_cost(facilities, customers, selected, capacities, demands):
    """Independent residual-network shortest augmenting paths, including unmet demand.

    Bellman-Ford handles reverse arcs, so a greedy nearest-customer assignment
    cannot pass as a capacitated optimum. No generated solver code is imported.
    """
    hubs, destinations = list(selected), list(customers)
    source, sink = 0, len(hubs) + len(destinations) + 1
    graph = [[] for _ in range(sink + 1)]

    def edge(a, b, capacity, cost):
        graph[a].append([b, len(graph[b]), capacity, cost])
        graph[b].append([a, len(graph[a]) - 1, 0, -cost])

    for index, hid in enumerate(hubs, 1):
        edge(source, index, capacities[hid], 0)
        for j, cid in enumerate(destinations, len(hubs) + 1):
            edge(index, j, demands[cid], distance_km(facilities[hid], customers[cid]) * 0.12)
    for j, cid in enumerate(destinations, len(hubs) + 1):
        edge(source, j, demands[cid], 200)
        edge(j, sink, demands[cid], 0)
    remaining, cost = sum(demands.values()), 0.0
    while remaining:
        dist, previous = [math.inf] * len(graph), [None] * len(graph)
        dist[source] = 0
        for _ in range(len(graph) - 1):
            changed = False
            for node, arcs in enumerate(graph):
                if not math.isfinite(dist[node]):
                    continue
                for index, (target, _, available, price) in enumerate(arcs):
                    if available > 0 and dist[target] > dist[node] + price + 1e-10:
                        dist[target], previous[target] = dist[node] + price, (node, index)
                        changed = True
            if not changed:
                break
        _require(previous[sink] is not None, "Independent flow calculation failed")
        quantity, target = remaining, sink
        while target != source:
            node, index = previous[target]
            quantity = min(quantity, graph[node][index][2])
            target = node
        target = sink
        while target != source:
            node, index = previous[target]
            arc = graph[node][index]
            arc[2] -= quantity
            graph[target][arc[1]][2] += quantity
            target = node
        remaining -= quantity
        cost += quantity * dist[sink]
    return cost + sum(facilities[hid]["fixedCostPerDay"] for hid in hubs)


def independent_optimum(facilities, customers, capacities, demands, max_hubs):
    values = []
    for size in range(min(max_hubs, len(facilities)) + 1):
        for selected in itertools.combinations(facilities, size):
            values.append(placement_cost(facilities, customers, selected, capacities, demands))
    return min(values)


def validate_network_run(value, expected=None):
    """Validate one model result and return independently recomputed summaries."""
    _require(isinstance(value, dict) and value.get("profile") == PROFILE, "Wrong network profile")
    params = value.get("parameters")
    _require(isinstance(params, dict), "Missing parameters")
    for key in ("seed", "days", "maxHubs", "disruptionDays"):
        _number(params.get(key), key, maximum={"seed": 4294967295, "days": 30, "maxHubs": 5, "disruptionDays": 30}[key],
                integer=True, minimum=1 if key in ("days", "maxHubs") else 0)
    for key in ("demandMultiplier", "capacityMultiplier"):
        _number(params.get(key), key, maximum=3)
    _require(params["disruptionDays"] <= params["days"], "Disruption exceeds observation days")
    _require(isinstance(params.get("disruptionHub"), str), "Invalid disruption hub")
    for key, requested in (expected or {}).items():
        if key != "data":
            _require(params.get(key) == requested, "Requested parameter ignored: " + key)
    data = value.get("data")
    _require(isinstance(data, dict) and data.get("crs") == "EPSG:4326", "Geographic coordinate reference missing")
    facilities = _indexed(_rows(data.get("facilities"), "facilities", 5), "facilities")
    customers = _indexed(_rows(data.get("customers"), "customers", 8), "customers")
    _require(2 <= len(facilities) <= 5 and customers, "Invalid network size")
    _require(isinstance(data.get("provenance"), dict) and data["provenance"].get("kind") == "synthetic",
             "Synthetic data provenance required")
    _require(not params["disruptionHub"] or params["disruptionHub"] in facilities, "Unknown disrupted hub")
    for name, records in (("facilities", facilities), ("customers", customers)):
        for row in records.values():
            _number(row.get("lon"), name + " longitude", maximum=180, minimum=-180)
            _number(row.get("lat"), name + " latitude", maximum=90, minimum=-90)
            if name == "facilities":
                _number(row.get("capacityPerDay"), "capacity", maximum=10000)
                _number(row.get("fixedCostPerDay"), "fixed cost", maximum=1e6)
            else:
                _number(row.get("demandPerDay"), "demand", maximum=10000)
    if expected and "data" in expected:
        for key, fields in (("facilities", ("id", "lon", "lat", "capacityPerDay", "fixedCostPerDay")),
                            ("customers", ("id", "lon", "lat", "demandPerDay"))):
            actual = [{field: row.get(field) for field in fields} for row in data[key]]
            requested = [{field: row.get(field) for field in fields} for row in expected["data"][key]]
            _require(actual == requested, "Requested network data ignored: " + key)
    demands = {key: _rounded(row["demandPerDay"] * params["demandMultiplier"]) for key, row in customers.items()}
    capacities = {key: _rounded(row["capacityPerDay"] * params["capacityMultiplier"]) for key, row in facilities.items()}
    total_demand = sum(demands.values())
    _require(total_demand * params["days"] <= 50000, "Order verification size limit exceeded")
    candidate_costs = {}
    for size in range(min(params["maxHubs"], len(facilities)) + 1):
        for selected in itertools.combinations(facilities, size):
            candidate_costs[tuple(sorted(selected))] = placement_cost(facilities, customers, selected, capacities, demands)
    optimum = min(candidate_costs.values())
    candidates = _rows(value.get("candidates"), "placement candidates", 32)
    seen_candidates = set()
    for candidate in candidates:
        selected = candidate.get("selectedHubs")
        _require(isinstance(selected, list) and all(isinstance(hid, str) for hid in selected), "Invalid placement candidate")
        key = tuple(sorted(selected))
        _require(key in candidate_costs and key not in seen_candidates, "Unknown or duplicate placement candidate")
        seen_candidates.add(key)
        _equal(candidate.get("totalCostPerDay"), candidate_costs[key], "Candidate independent cost")
    _require(seen_candidates == set(candidate_costs), "Placement candidates omitted")
    summaries = {}
    for name in ("baseline", "optimized"):
        alternative = value.get(name)
        _require(isinstance(alternative, dict), "Missing alternative: " + name)
        hubs = alternative.get("selectedHubs")
        _require(isinstance(hubs, list) and all(isinstance(hid, str) and hid in facilities for hid in hubs)
                 and len(hubs) == len(set(hubs)) and len(hubs) <= params["maxHubs"], "Invalid selected hubs")
        if name == "baseline":
            expected_hubs = list(dict.fromkeys([next(iter(facilities)), next(reversed(facilities))]))[:params["maxHubs"]]
            _require(hubs == expected_hubs, "Baseline selected hubs changed")
        plan = alternative.get("planning")
        _require(isinstance(plan, dict), "Missing planning evidence")
        allocations = _rows(plan.get("allocations"), "allocations", 40)
        unmet = _rows(plan.get("unserved"), "unserved", 8)
        by_customer, by_hub, allocation_keys = {cid: 0 for cid in customers}, {hid: 0 for hid in hubs}, set()
        assignments = {cid: [] for cid in customers}
        transport = 0.0
        for row in allocations:
            hid, cid = row.get("hubId"), row.get("customerId")
            _require(hid in by_hub and cid in customers and (hid, cid) not in allocation_keys,
                     "Unknown, unselected or duplicate allocation")
            allocation_keys.add((hid, cid))
            amount = _number(row.get("unitsPerDay"), "allocation units", maximum=50000, integer=True)
            distance = distance_km(facilities[hid], customers[cid])
            _equal(row.get("distanceKm"), distance, "Allocation distance")
            _equal(row.get("unitCost"), distance * 0.12, "Allocation unit cost")
            by_customer[cid] += amount
            by_hub[hid] += amount
            assignments[cid].extend([hid] * int(amount))
            transport += amount * distance * 0.12
        unmet_counts = {}
        for row in unmet:
            cid = row.get("customerId")
            _require(cid in customers and cid not in unmet_counts, "Unknown or duplicate unmet demand")
            unmet_counts[cid] = _number(row.get("unitsPerDay"), "unmet units", maximum=50000, integer=True)
        for cid in customers:
            _require(by_customer[cid] + unmet_counts.get(cid, 0) == demands[cid], "Demand conservation violated")
            assignments[cid].sort()
            assignments[cid].extend([None] * int(unmet_counts.get(cid, 0)))
        _require(all(by_hub[hid] <= capacities[hid] for hid in hubs), "Facility capacity exceeded")
        fixed, penalty = sum(facilities[hid]["fixedCostPerDay"] for hid in hubs), sum(unmet_counts.values()) * 200
        computed = {"fixedCostPerDay": fixed, "transportCostPerDay": transport, "unservedPenaltyPerDay": penalty,
                    "totalCostPerDay": fixed + transport + penalty, "servedUnitsPerDay": sum(by_customer.values()),
                    "totalDemandPerDay": total_demand}
        for key, amount in computed.items():
            _equal(plan.get(key), amount, "Planning " + key)
        optimal_cost = optimum if name == "optimized" else placement_cost(facilities, customers, hubs, capacities, demands)
        _equal(computed["totalCostPerDay"], optimal_cost, name + " independent optimum")
        simulation = alternative.get("simulation")
        _require(isinstance(simulation, dict), "Missing simulation")
        orders = _indexed(_rows(simulation.get("orders"), "orders", 50000), "orders")
        expected_ids = {f"d{day}-{cid}-{ordinal}" for day in range(params["days"])
                        for cid in customers for ordinal in range(demands[cid])}
        _require(set(orders) == expected_ids, "Missing or invented demand orders")
        daily = [{"day": day, "demand": 0, "delivered": 0, "onTime": 0, "unserved": 0}
                 for day in range(params["days"])]
        delivered, on_time, lead, transport_cost = 0, 0, 0.0, 0.0
        external = {}
        for day in range(params["days"]):
            loads = {hid: 0 for hid in hubs}
            for cid in customers:
                for ordinal, hid in enumerate(assignments[cid]):
                    oid = f"d{day}-{cid}-{ordinal}"
                    row = orders[oid]
                    _require(row.get("day") == day and row.get("customerId") == cid and row.get("hubId") == hid
                             and type(row.get("quantity")) in (int, float) and row["quantity"] == 1,
                             "Order assignment differs from plan")
                    created = day * 24 + 8 + ordinal * 0.01
                    _equal(row.get("createdAt"), created, "Order arrival")
                    _equal(row.get("dueAt"), created + 8, "Order deadline")
                    noise = _number(row.get("exogenousDelayHours"), "Exogenous delay", maximum=3)
                    _require(noise < 3, "Exogenous delay outside contract")
                    external[oid] = (day, cid, row["quantity"], created, created + 8, noise)
                    distance = distance_km(facilities[hid], customers[cid]) if hid else 0
                    _equal(row.get("distanceKm"), distance, "Order geographic distance")
                    blocked = hid is None or (hid == params["disruptionHub"] and day < params["disruptionDays"])
                    daily[day]["demand"] += 1
                    if blocked:
                        _require(row.get("dispatchedAt") is None and row.get("deliveredAt") is None
                                 and isinstance(row.get("unservedReason"), str) and row["unservedReason"].strip(),
                                 "Unserved or disrupted order was delivered")
                        daily[day]["unserved"] += 1
                    else:
                        _equal(row.get("dispatchedAt"), created + 1, "Dispatch time")
                        arrival = created + 1 + distance / 50 + noise
                        _equal(row.get("deliveredAt"), arrival, "Delivery time")
                        loads[hid] += 1
                        delivered += 1
                        lead += arrival - created
                        transport_cost += distance * 0.12
                        daily[day]["delivered"] += 1
                        if arrival <= created + 8:
                            on_time += 1
                            daily[day]["onTime"] += 1
            _require(all(loads[hid] <= capacities[hid] for hid in hubs), "Simulation facility capacity exceeded")
        daily_rows = _rows(simulation.get("daily"), "daily", 30)
        _require(len(daily_rows) == len(daily), "Missing daily records")
        for actual, calculated in zip(daily_rows, daily):
            for key, amount in calculated.items():
                _equal(actual.get(key), amount, "Daily " + key)
        demand = total_demand * params["days"]
        calculated = {"demand": demand, "delivered": delivered, "unserved": demand - delivered, "onTime": on_time,
                      "serviceRate": delivered / demand if demand else 0,
                      "onTimeRate": on_time / demand if demand else 0,
                      "meanLeadHours": lead / delivered if delivered else 0,
                      "fixedCost": fixed * params["days"], "transportCost": transport_cost,
                      "penaltyCost": (demand - delivered) * 200}
        calculated["totalCost"] = calculated["fixedCost"] + transport_cost + calculated["penaltyCost"]
        kpis = simulation.get("kpis")
        _require(isinstance(kpis, dict), "Missing KPI values")
        for key, amount in calculated.items():
            _equal(kpis.get(key), amount, "Simulation " + key)
        summaries[name] = {"planning": computed, "kpis": calculated, "external": external,
                           "selectedHubs": hubs, "allocations": allocations}
    _require(summaries["baseline"]["external"] == summaries["optimized"]["external"],
             "Alternatives use different exogenous demand or delays")
    return {"optimum": optimum, "alternatives": summaries, "parameters": params,
            "facilities": facilities, "customers": customers}


class NetworkVerifier(BrowserVerifier):
    def verify(self, url, artifacts, output_dir, cancel_event=None):
        started = time.monotonic()
        receipt = {"status": "failed", "kind": "supply_network_gis_checks", "profile": PROFILE,
                   "started_at": datetime.now(timezone.utc).isoformat(), "artifacts": copy.deepcopy(artifacts),
                   "checks": [], "errors": [], "screenshots": [], "business_acceptance": False,
                   "scope": "합성 입지·네트워크 최적 목적값, 용량·수요 제약, 원시 주문 KPI·비용, 동일 조건 시뮬레이션, GIS·KPI 화면 일치"}
        try:
            _preview_url(url)
            _require(isinstance(artifacts, dict) and {"index.html", "model.js", "app.js"} <= artifacts.keys()
                     and len(artifacts) <= 32, "Pinned network files required")
            _require(all(isinstance(name, str) and re.fullmatch(r"[a-zA-Z0-9_.-]+", name)
                         and isinstance(digest, str) and re.fullmatch(r"[a-f0-9]{64}", digest)
                         for name, digest in artifacts.items()), "Invalid network artifact hashes")
            output = _output_directory(output_dir) / ("network-browser-" + uuid4().hex[:12])
            output.mkdir()
            if cancel_event is not None and cancel_event.is_set():
                raise RuntimeError("Browser verification cancelled")
            asyncio.run(self._run_bounded(url, output, receipt, cancel_event))
            if receipt["checks"] and not receipt["errors"] and all(row.get("status") == "passed" for row in receipt["checks"]):
                receipt["status"] = "passed"
        except BrowserUnavailable as exc:
            receipt["status"] = "unavailable"
            receipt["errors"].append(str(exc))
        except (Exception, asyncio.CancelledError) as exc:
            receipt["errors"].append(str(exc)[:1000] or type(exc).__name__)
        receipt["elapsed_seconds"] = round(time.monotonic() - started, 3)
        receipt["summary"] = {"passed": "합성 네트워크의 최적해·제약·원시 KPI 재계산과 지도·화면 동작 검사를 통과했습니다. 고객 적합성은 별도입니다.",
                              "failed": "네트워크 모델·지도·성과지표 검사를 통과하지 못했습니다.",
                              "unavailable": "격리 브라우저 검증 도구를 사용할 수 없습니다."}[receipt["status"]]
        return receipt

    async def _run_browser(self, url, output, receipt):
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            raise BrowserUnavailable("격리 브라우저 검증 도구가 필요합니다.") from None
        require_clean_process_env()
        async with async_playwright() as playwright:
            try:
                browser = await playwright.chromium.launch(headless=True, channel=_installed_browser(), timeout=12000, env=child_env(),
                    args=["--disable-background-networking", "--disable-component-update", "--dns-prefetch-disable"])
            except Exception as exc:
                raise BrowserUnavailable("격리 브라우저 실행 실패: " + str(exc)[:200]) from exc
            try:
                context = await browser.new_context(viewport={"width": 1280, "height": 960}, permissions=[], service_workers="block")
                blocked, http_errors = [], []
                await context.route("**/*", lambda route: _route_request(route, _origin(url), blocked, http_errors))
                page = await context.new_page()
                page.set_default_timeout(7000)
                page.on("pageerror", lambda error: receipt["errors"].append(str(error)[:300]))
                await page.goto(url, wait_until="load")
                base = dict(DEFAULT_INPUT)
                baseline = await page.evaluate("input => window.DASNetwork.run(input)", base)
                hid = next(iter((baseline.get("optimized") or {}).get("selectedHubs", [])), None)
                variants = {"baseline": base, "repeat": base, "no_demand": {**base, "demandMultiplier": 0},
                            "no_capacity": {**base, "capacityMultiplier": 0},
                            "higher_demand": {**base, "demandMultiplier": 2},
                            "different_seed": {**base, "seed": 123},
                            "known_optimum": {**base, "maxHubs": 1, "data": KNOWN_DATA}}
                if hid:
                    variants["disruption"] = {**base, "disruptionHub": hid, "disruptionDays": base["days"]}
                captures, summaries = {}, {}
                receipt["evidence_path"] = str(output / "network-evidence.json")
                for name, params in variants.items():
                    value = baseline if name == "baseline" else await page.evaluate("input => window.DASNetwork.run(input)", params)
                    captures[name] = value
                    # Keep failed evidence too; a failed validation must remain inspectable.
                    (output / "network-evidence.json").write_text(json.dumps(captures, ensure_ascii=False), encoding="utf-8")
                    summaries[name] = validate_network_run(value, params)
                    receipt["checks"].append({"name": name + " independent optimum / constraints / raw KPI", "status": "passed"})
                _require(captures["baseline"] == captures["repeat"], "Same seed is not reproducible")
                _equal(summaries["known_optimum"]["optimum"], 5, "Known fixture optimum")
                _require(summaries["known_optimum"]["alternatives"]["optimized"]["selectedHubs"] == ["A"], "Known fixture selected wrong location")
                _require(summaries["no_capacity"]["alternatives"]["optimized"]["kpis"]["delivered"] == 0, "Zero capacity ignored")
                _require(summaries["no_demand"]["alternatives"]["optimized"]["kpis"]["demand"] == 0, "Zero demand ignored")
                original = summaries["baseline"]["alternatives"]["optimized"]
                _require(summaries["higher_demand"]["alternatives"]["optimized"]["kpis"]["demand"] > original["kpis"]["demand"], "Demand scenario has no effect")
                _require(original["external"] != summaries["different_seed"]["alternatives"]["optimized"]["external"], "Seed has no exogenous effect")
                if hid:
                    _require(summaries["disruption"]["alternatives"]["optimized"]["kpis"]["delivered"] < original["kpis"]["delivered"], "Hub disruption has no effect")
                receipt["checks"].append({"name": "Known optimum, repeatability, equal exogenous inputs and scenario response", "status": "passed"})
                await self._check_ui(page, base, summaries["baseline"], receipt)
                changed = {**base, "demandMultiplier": 2}
                await self._check_ui(page, changed, summaries["higher_demand"], receipt)
                await self._check_ui(page, base, summaries["baseline"], receipt)
                for width, name in ((1280, "desktop"), (390, "mobile")):
                    await page.set_viewport_size({"width": width, "height": 960})
                    _require(not await page.evaluate("document.documentElement.scrollWidth > innerWidth + 2"), name + " horizontal overflow")
                    target = output / (name + ".png")
                    await page.screenshot(path=str(target), full_page=True)
                    receipt["screenshots"].append({"name": name, "path": str(target)})
                    receipt["checks"].append({"name": name + " responsive map and KPI display", "status": "passed"})
                receipt["errors"].extend(blocked + http_errors)
            finally:
                await browser.close()

    async def _check_ui(self, page, params, summary, receipt):
        controls = {"seed": "seed", "days": "days", "demandMultiplier": "demand-multiplier",
                    "capacityMultiplier": "capacity-multiplier", "maxHubs": "max-hubs", "disruptionDays": "disruption-days"}
        for key, element in controls.items():
            await page.locator("#" + element).fill(str(params[key]))
        await page.locator("#disruption-hub").select_option(params["disruptionHub"])
        await page.locator("#run-model").click()
        for name in ("baseline", "optimized"):
            await page.locator("#scenario-select").select_option(name)
            capture = await page.evaluate("""() => {
              const result=document.querySelector('#model-results'), map=document.querySelector('#network-map');
              return {scenario:result?.dataset.scenario, values:result?.dataset,
                selected:map?.getAttribute('data-selected-hubs'),
                hubs:[...map.querySelectorAll('[data-hub-id]')].map(e=>({id:e.dataset.hubId,lon:e.dataset.lon,lat:e.dataset.lat,selected:e.dataset.selected,x:e.getAttribute('cx'),y:e.getAttribute('cy')})),
                customers:[...map.querySelectorAll('[data-customer-id]')].map(e=>({id:e.dataset.customerId,lon:e.dataset.lon,lat:e.dataset.lat,x:e.getAttribute('cx'),y:e.getAttribute('cy')})),
                routes:[...map.querySelectorAll('[data-route-hub][data-route-customer]')].map(e=>({hubId:e.dataset.routeHub,customerId:e.dataset.routeCustomer,units:e.dataset.units,x1:e.getAttribute('x1'),y1:e.getAttribute('y1'),x2:e.getAttribute('x2'),y2:e.getAttribute('y2')})),
                top:[...document.querySelectorAll('#top-kpis [data-metric]')].map(e=>({metric:e.dataset.metric,value:e.dataset.value,text:e.textContent})),
                topAboveMap:document.querySelector('#top-kpis').getBoundingClientRect().bottom<=map.getBoundingClientRect().top,
                visible:!!map && map.checkVisibility({checkOpacity:true,checkVisibilityCSS:true}) && document.querySelector('#top-kpis').checkVisibility({checkOpacity:true,checkVisibilityCSS:true})};
            }""")
            validate_map_display(capture, summary, name)
            receipt["checks"].append({"name": name + " GIS routes and top KPI match recomputed model (demand multiplier " + str(params["demandMultiplier"]) + ")", "status": "passed"})


def validate_map_display(capture, summary, scenario):
    """Validate browser-returned DOM values against the independent calculations."""
    _require(isinstance(capture, dict) and capture.get("visible") is True and capture.get("scenario") == scenario
             and capture.get("topAboveMap") is True,
             "Map/KPI scenario is hidden or stale")
    alternative = summary["alternatives"][scenario]
    values = capture.get("values") or {}
    for key in ("totalCost", "serviceRate", "onTimeRate", "demand", "delivered"):
        try:
            actual = float(values.get(key))
        except (TypeError, ValueError):
            raise ValueError("Missing displayed KPI: " + key) from None
        _equal(actual, alternative["kpis"][key], "Displayed " + key)
    selected = capture.get("selected")
    try:
        selected = json.loads(selected)
    except (ValueError, TypeError):
        selected = [part for part in str(selected or "").split(",") if part]
    _require(isinstance(selected, list) and sorted(selected) == sorted(alternative["selectedHubs"]), "GIS selected hubs mismatch")
    markers = {}
    coordinates = list(summary["facilities"].values()) + list(summary["customers"].values())
    west, east = min(row["lon"] for row in coordinates) - .35, max(row["lon"] for row in coordinates) + .35
    south, north = min(row["lat"] for row in coordinates) - .3, max(row["lat"] for row in coordinates) + .3
    for key in ("hubs", "customers"):
        rows = _indexed(_rows(capture.get(key), key + " markers", 8), key + " markers")
        source = summary["facilities" if key == "hubs" else "customers"]
        _require(set(rows) == set(source), "GIS markers missing or duplicated")
        for rid, row in rows.items():
            for coordinate in ("lon", "lat"):
                _require(math.isclose(float(row.get(coordinate)), source[rid][coordinate], abs_tol=1e-8), "GIS coordinate mismatch")
            _number(float(row.get("x")), "GIS x", maximum=10000)
            _number(float(row.get("y")), "GIS y", maximum=10000)
            _equal(float(row["x"]), 55 + (source[rid]["lon"] - west) / (east - west) * 790, "GIS longitude projection")
            _equal(float(row["y"]), 490 - (source[rid]["lat"] - south) / (north - south) * 430, "GIS latitude projection")
            if key == "hubs":
                _require(row.get("selected") == ("true" if rid in alternative["selectedHubs"] else "false"), "GIS selected marker mismatch")
        markers[key] = rows
    expected_routes = {(row["hubId"], row["customerId"]): row["unitsPerDay"]
                       for row in alternative["allocations"] if row["unitsPerDay"] > 0}
    routes = _rows(capture.get("routes"), "GIS routes", 40)
    _require(len(routes) == len(expected_routes), "GIS route count mismatch")
    seen = set()
    for route in routes:
        key = (route.get("hubId"), route.get("customerId"))
        _require(key in expected_routes and key not in seen, "Unexpected or duplicate GIS route")
        seen.add(key)
        _equal(float(route.get("units")), expected_routes[key], "GIS route volume")
        hub, customer = markers["hubs"][key[0]], markers["customers"][key[1]]
        for field, expected in (("x1", hub["x"]), ("y1", hub["y"]), ("x2", customer["x"]), ("y2", customer["y"])):
            _equal(float(route.get(field)), float(expected), "GIS route endpoint")
    top = _rows(capture.get("top"), "Top KPI", 20)
    by_metric = {row.get("metric"): row for row in top}
    _require(len(by_metric) == len(top), "Duplicate top KPI")
    for metric in ("totalCost", "serviceRate", "onTimeRate", "demand", "delivered", "meanLeadHours", "unserved"):
        _require(metric in by_metric, "Missing top KPI: " + metric)
        row = by_metric[metric]
        _equal(float(row.get("value")), alternative["kpis"][metric], "Top KPI " + metric)
        _require(isinstance(row.get("text"), str) and bool(re.search(r"\d", row["text"])), "Top KPI has no visible number")
        number = re.search(r"\d[\d,]*(?:\.\d+)?", row["text"])
        visible = float(number[0].replace(",", ""))
        expected = alternative["kpis"][metric] * (100 if metric.endswith("Rate") else 1)
        decimals = 1 if metric.endswith("Rate") or metric == "meanLeadHours" else 0
        _require(abs(visible - expected) <= 0.5 * 10 ** -decimals + 1e-7, "Visible top KPI differs from model: " + metric)
