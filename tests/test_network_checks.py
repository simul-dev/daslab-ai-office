"""Hand-derived network evidence and independent defect injection, no model calls."""
import copy
import math
import tempfile
import threading
import unittest
from unittest.mock import patch

from office.browser_verification import BrowserUnavailable
from office.network_checks import (DEFAULT_INPUT, PROFILE, NetworkVerifier, distance_km,
                                   independent_optimum, placement_cost, validate_map_display,
                                   validate_network_run)


def evidence(demand=6, capacity=10, days=2, disruption=""):
    """Two co-located hubs: costs 10 and 5; a known optimum of 5 when serving.

    Results are hand-calculated; no seed, worker solver or verifier is used to
    produce the expected allocations, orders, totals or candidate objectives.
    """
    params = {**DEFAULT_INPUT, "days": days, "maxHubs": 1, "disruptionHub": disruption,
              "disruptionDays": 1 if disruption else 0}
    data = {"crs": "EPSG:4326", "facilities": [
        {"id": "B", "name": "Expensive", "lon": 127, "lat": 37, "capacityPerDay": capacity, "fixedCostPerDay": 10},
        {"id": "A", "name": "Cheap", "lon": 127, "lat": 37, "capacityPerDay": capacity, "fixedCostPerDay": 5}],
        "customers": [{"id": "X", "name": "Demand", "lon": 127, "lat": 37, "demandPerDay": demand}],
        "provenance": {"kind": "synthetic"}}

    def alternative(hubs, fixed):
        served = min(demand, capacity) if hubs else 0
        unmet = demand - served
        plan = {"allocations": [{"hubId": hubs[0], "customerId": "X", "unitsPerDay": served,
                                 "distanceKm": 0, "unitCost": 0}] if served else [],
                "unserved": [{"customerId": "X", "unitsPerDay": unmet}],
                "fixedCostPerDay": fixed, "transportCostPerDay": 0, "unservedPenaltyPerDay": unmet * 200,
                "totalCostPerDay": fixed + unmet * 200, "servedUnitsPerDay": served, "totalDemandPerDay": demand}
        orders, daily = [], []
        for day in range(days):
            delivered = 0
            for ordinal in range(demand):
                hid = hubs[0] if ordinal < served else None
                blocked = hid is None or (hid == disruption and day == 0)
                created = day * 24 + 8 + ordinal * .01
                orders.append({"id": f"d{day}-X-{ordinal}", "day": day, "customerId": "X", "hubId": hid,
                               "quantity": 1, "distanceKm": 0, "createdAt": created, "dueAt": created + 8,
                               "exogenousDelayHours": .5, "dispatchedAt": None if blocked else created + 1,
                               "deliveredAt": None if blocked else created + 1.5,
                               "unservedReason": "no capacity / disruption" if blocked else None})
                delivered += not blocked
            daily.append({"day": day, "demand": demand, "delivered": delivered, "onTime": delivered,
                          "unserved": demand - delivered})
        total, delivered = demand * days, sum(row["delivered"] for row in daily)
        kpis = {"demand": total, "delivered": delivered, "unserved": total - delivered, "onTime": delivered,
                "serviceRate": delivered / total if total else 0, "onTimeRate": delivered / total if total else 0,
                "meanLeadHours": 1.5 if delivered else 0, "fixedCost": fixed * days, "transportCost": 0,
                "penaltyCost": (total - delivered) * 200, "totalCost": fixed * days + (total - delivered) * 200}
        return {"selectedHubs": hubs, "planning": plan, "simulation": {"orders": orders, "daily": daily, "kpis": kpis}}

    candidates = [{"selectedHubs": [], "totalCostPerDay": demand * 200},
                  {"selectedHubs": ["B"], "totalCostPerDay": 10 + max(0, demand - capacity) * 200},
                  {"selectedHubs": ["A"], "totalCostPerDay": 5 + max(0, demand - capacity) * 200}]
    best = alternative(["A"], 5) if demand and capacity else alternative([], 0)
    return {"profile": PROFILE, "parameters": params, "data": data, "baseline": alternative(["B"], 10),
            "optimized": best, "candidates": candidates}


def display(summary, scenario="optimized"):
    alternative = summary["alternatives"][scenario]
    def markers(source, hubs=False):
        return [{"id": key, "lon": str(row["lon"]), "lat": str(row["lat"]), "x": "450", "y": "275",
                 **({"selected": "true" if key in alternative["selectedHubs"] else "false"} if hubs else {})}
                for key, row in source.items()]
    values = alternative["kpis"]
    return {"scenario": scenario, "visible": True, "topAboveMap": True, "values": {key: str(values[key]) for key in
            ("totalCost", "serviceRate", "onTimeRate", "demand", "delivered")},
            "selected": ",".join(alternative["selectedHubs"]),
            "hubs": markers(summary["facilities"], True), "customers": markers(summary["customers"]),
            "routes": [{"hubId": row["hubId"], "customerId": row["customerId"], "units": str(row["unitsPerDay"]),
                        "x1": "450", "y1": "275", "x2": "450", "y2": "275"} for row in alternative["allocations"]],
            "top": [{"metric": key, "value": str(values[key]),
                     "text": str(round(values[key] * 100, 1)) + "%" if key.endswith("Rate") else str(round(values[key], 1 if key == "meanLeadHours" else 0))}
                    for key in ("totalCost", "serviceRate", "onTimeRate", "demand", "delivered", "meanLeadHours", "unserved")]}


class NetworkChecksTests(unittest.TestCase):
    def test_hand_calculated_optimum_and_raw_accounting(self):
        checked = validate_network_run(evidence())
        self.assertEqual(checked["optimum"], 5)
        self.assertEqual(checked["alternatives"]["baseline"]["kpis"]["totalCost"], 20)
        self.assertEqual(checked["alternatives"]["optimized"]["kpis"]["totalCost"], 10)
        self.assertEqual(checked["alternatives"]["optimized"]["kpis"]["delivered"], 12)

    def test_known_geodesic_distance(self):
        self.assertAlmostEqual(distance_km({"lon": 0, "lat": 0}, {"lon": 1, "lat": 0}), 111.19492664455873)

    def test_independent_flow_reroutes_non_greedy_assignment(self):
        facilities = {"A": {"id": "A", "fixedCostPerDay": 0}, "B": {"id": "B", "fixedCostPerDay": 0}}
        customers = {"X": {"id": "X"}, "Y": {"id": "Y"}}
        # Greedy A-X then B-Y costs 100. The optimal residual reroute is
        # B-X (cost 2) plus A-Y (cost 2), total 4.
        costs = {("A", "X"): 1, ("A", "Y"): 2, ("B", "X"): 2, ("B", "Y"): 99}
        with patch("office.network_checks.distance_km", side_effect=lambda a, b: costs[(a["id"], b["id"])] / .12):
            self.assertAlmostEqual(placement_cost(facilities, customers, ["A", "B"], {"A": 1, "B": 1}, {"X": 1, "Y": 1}), 4)
            self.assertAlmostEqual(independent_optimum(facilities, customers, {"A": 1, "B": 1}, {"X": 1, "Y": 1}, 2), 4)

    def test_zero_demand_zero_capacity_and_disruption(self):
        for result in (evidence(demand=0), evidence(capacity=0), evidence(disruption="A"), evidence(demand=15)):
            with self.subTest(parameters=result["parameters"], data=result["data"]):
                checked = validate_network_run(result)
                self.assertGreaterEqual(checked["alternatives"]["optimized"]["kpis"]["unserved"], 0)

    def test_self_consistent_suboptimal_solution_is_rejected(self):
        result = evidence()
        result["optimized"] = copy.deepcopy(result["baseline"])
        with self.assertRaisesRegex(ValueError, "independent optimum"):
            validate_network_run(result)

    def test_defects_in_raw_model_and_claimed_totals_are_rejected(self):
        defects = {
            "inflated KPI": lambda x: x["optimized"]["simulation"]["kpis"].update(delivered=99),
            "wrong objective": lambda x: x["optimized"]["planning"].update(totalCostPerDay=0),
            "missing orders": lambda x: x["optimized"]["simulation"]["orders"].pop(),
            "duplicate orders": lambda x: x["optimized"]["simulation"]["orders"].append(copy.deepcopy(x["optimized"]["simulation"]["orders"][0])),
            "fake distance": lambda x: x["optimized"]["planning"]["allocations"][0].update(distanceKm=100),
            "fake unit cost": lambda x: x["optimized"]["planning"]["allocations"][0].update(unitCost=7),
            "capacity overrun": lambda x: x["data"]["facilities"][1].update(capacityPerDay=1),
            "unknown assignment": lambda x: x["optimized"]["planning"]["allocations"][0].update(hubId="unknown"),
            "demand omitted": lambda x: x["optimized"]["planning"]["allocations"][0].update(unitsPerDay=5),
            "unknown selected hub": lambda x: x["optimized"].update(selectedHubs=["unknown"]),
            "duplicate selection": lambda x: x["optimized"].update(selectedHubs=["A", "A"]),
            "invented delivery": lambda x: x["optimized"]["simulation"]["orders"][0].update(deliveredAt=200),
            "shifted demand": lambda x: x["optimized"]["simulation"]["orders"][0].update(createdAt=9),
            "fake daily totals": lambda x: x["optimized"]["simulation"]["daily"][0].update(delivered=1),
            "candidate omitted": lambda x: x["candidates"].pop(),
            "false candidate cost": lambda x: x["candidates"][1].update(totalCostPerDay=0),
            "nonfinite KPI": lambda x: x["optimized"]["simulation"]["kpis"].update(totalCost=math.nan),
            "false source": lambda x: x["data"]["provenance"].update(kind="real_customer"),
        }
        for name, change in defects.items():
            with self.subTest(defect=name):
                result = evidence()
                change(result)
                with self.assertRaises(ValueError):
                    validate_network_run(result)

    def test_different_exogenous_delays_rejected_even_when_totals_are_consistent(self):
        result = evidence()
        simulation = result["optimized"]["simulation"]
        for order in simulation["orders"]:
            order["exogenousDelayHours"] = 1
            order["deliveredAt"] += .5
        simulation["kpis"]["meanLeadHours"] = 2
        with self.assertRaisesRegex(ValueError, "different exogenous"):
            validate_network_run(result)

    def test_disrupted_delivery_is_rejected(self):
        result = evidence(disruption="A")
        row = result["optimized"]["simulation"]["orders"][0]
        row.update(dispatchedAt=row["createdAt"] + 1, deliveredAt=row["createdAt"] + 1.5)
        with self.assertRaisesRegex(ValueError, "disrupted"):
            validate_network_run(result)

    def test_requested_parameters_and_fixture_data_cannot_be_ignored(self):
        result = evidence()
        with self.assertRaisesRegex(ValueError, "parameter ignored"):
            validate_network_run(result, {"demandMultiplier": 2})
        request = copy.deepcopy(result["data"])
        request["facilities"][0]["fixedCostPerDay"] = 11
        with self.assertRaisesRegex(ValueError, "network data ignored"):
            validate_network_run(result, {"data": request})

    def test_fractional_multipliers_use_contract_rounding(self):
        result = evidence(demand=1)
        result["parameters"]["demandMultiplier"] = .5
        # JavaScript Math.round(0.5) = 1; Python round(0.5) would lose the order.
        checked = validate_network_run(result)
        self.assertEqual(checked["alternatives"]["optimized"]["kpis"]["demand"], 2)

    def test_map_and_visible_top_kpi_match_independent_results(self):
        checked = validate_network_run(evidence())
        validate_map_display(display(checked), checked, "optimized")

    def test_map_and_top_kpi_defect_injection(self):
        checked = validate_network_run(evidence())
        defects = {
            "stale scenario": lambda c: c.update(scenario="baseline"),
            "hidden map": lambda c: c.update(visible=False),
            "KPI below map": lambda c: c.update(topAboveMap=False),
            "false selected hubs": lambda c: c.update(selected="B"),
            "false KPI attribute": lambda c: c["values"].update(totalCost="999"),
            "false visible KPI": lambda c: c["top"][0].update(text="999"),
            "false raw top KPI": lambda c: c["top"][0].update(value="999"),
            "missing KPI": lambda c: c["top"].pop(),
            "wrong geographic marker": lambda c: c["customers"][0].update(lon="128"),
            "wrong marker projection": lambda c: c["customers"][0].update(x="460"),
            "wrong selection styling": lambda c: c["hubs"][0].update(selected="true"),
            "false route volume": lambda c: c["routes"][0].update(units="77"),
            "unconnected route": lambda c: c["routes"][0].update(x2="77"),
            "missing route": lambda c: c["routes"].pop(),
        }
        for name, change in defects.items():
            with self.subTest(defect=name):
                capture = display(checked)
                change(capture)
                with self.assertRaises(ValueError):
                    validate_map_display(capture, checked, "optimized")

    def test_verifier_cancel_unavailable_and_no_checks_do_not_pass(self):
        artifacts = {name: "a" * 64 for name in ("index.html", "model.js", "app.js")}
        async def unavailable(*args):
            raise BrowserUnavailable("optional browser unavailable")
        async def empty(*args):
            pass
        with tempfile.TemporaryDirectory() as folder:
            receipt = NetworkVerifier(runner=unavailable).verify("http://127.0.0.1:12345/", artifacts, folder)
            self.assertEqual(receipt["status"], "unavailable")
            self.assertFalse(receipt["business_acceptance"])
            self.assertEqual(NetworkVerifier(runner=empty).verify("http://127.0.0.1:12345/", artifacts, folder)["status"], "failed")
            stopped = threading.Event()
            stopped.set()
            self.assertEqual(NetworkVerifier(runner=empty).verify("http://127.0.0.1:12345/", artifacts, folder, stopped)["status"], "failed")

    def test_verifier_rejects_external_url_and_invalid_artifacts(self):
        with tempfile.TemporaryDirectory() as folder:
            result = NetworkVerifier().verify("https://example.com/", {}, folder)
            self.assertEqual(result["status"], "failed")
            result = NetworkVerifier().verify("http://127.0.0.1:12345/", {"index.html": "bad"}, folder)
            self.assertEqual(result["status"], "failed")


if __name__ == "__main__":
    unittest.main()
