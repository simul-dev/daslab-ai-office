import copy
import unittest
from unittest.mock import patch

from office.prototype_checks import (POLICY_INPUT, policy_comparison_checks,
                                     validate_policy_comparison)


INPUT = {"days": 1, "ordersPerDay": 2, "initialStock": 1, "seed": 42,
         "safetyStockDays": 2, "emergencyCapacity": 1}


def comparison(changes=None):
    """Hand-accounted fixture: one delivery, one in transit, 0.4 unit-days."""
    p = {**INPUT, **(changes or {})}
    demand = [{"id": "A", "arrivalAt": .1, "dueAt": .4, "quantity": 1},
              {"id": "B", "arrivalAt": .15, "dueAt": .8, "quantity": 1}]
    orders = [{"id": "A", "reservedAt": .1, "shippedAt": .2, "deliveredAt": .3},
              {"id": "B", "reservedAt": .4, "shippedAt": .6, "deliveredAt": None}]
    # time, onHand, reserved, backlog, onOrder, received, shipped, completed
    facts = [(0, 1, 0, 0, 1, 0, 0, 0), (.1, 1, 1, 0, 1, 0, 0, 0),
             (.15, 1, 1, 1, 1, 0, 0, 0), (.2, 0, 0, 1, 1, 0, 1, 0),
             (.3, 0, 0, 1, 1, 0, 1, 1), (.4, 1, 1, 0, 0, 1, 1, 1),
             (.6, 0, 0, 0, 0, 1, 2, 1), (1, 0, 0, 0, 0, 1, 2, 1)]
    states = []
    for row in facts:
        state = dict(zip(("time", "onHand", "reserved", "backlog", "onOrder",
                          "received", "shipped", "completed"), row))
        state["inventoryPosition"] = state["onHand"] - state["reserved"] + state["onOrder"] - state["backlog"]
        states.append(state)
    metrics = {"arrivals": 2, "completed": 1, "backlog": 0, "reserved": 0,
               "inTransit": 1, "otif": .5, "meanOrderDays": .2, "holdingUnitDays": .4}
    policies = []
    for name in ("P0", "P1", "P2"):
        emergency = name == "P2" and p["emergencyCapacity"] > 0
        po = {"id": "PO1", "orderedAt": 0, "quantity": 1, "regularDepartureAt": .2,
              "regularArrivalAt": .8 if emergency else .4, "emergencyQuantity": int(emergency),
              "emergencyDepartureAt": .05 if emergency else None, "emergencyArrivalAt": .4 if emergency else None}
        receipt = {"id": "R1", "purchaseOrderId": "PO1", "mode": "emergency" if emergency else "regular",
                   "time": .4, "quantity": 1}
        costs = {"holdingRate": 1, "regularUnitRate": 2, "emergencyUnitRate": 6, "orderFee": 4,
                 "holding": .4, "regular": 0 if emergency else 2,
                 "emergency": 6 if emergency else 0, "ordering": 4, "total": 10.4 if emergency else 6.4}
        policies.append({"policy": name, "orders": copy.deepcopy(orders), "purchaseOrders": [po],
                         "receipts": [receipt], "stateLog": copy.deepcopy(states),
                         "metrics": metrics.copy(), "costs": costs})
    if p["ordersPerDay"] == 0:
        demand = []
        for policy in policies:
            policy.update(orders=[], purchaseOrders=[], receipts=[], stateLog=[
                {"time": t, "onHand": 1, "reserved": 0, "backlog": 0, "onOrder": 0,
                 "inventoryPosition": 1, "received": 0, "shipped": 0, "completed": 0} for t in (0, 1)])
            policy["metrics"] = {key: 0 for key in metrics}
            policy["metrics"]["holdingUnitDays"] = 1
            policy["costs"].update(holding=1, regular=0, emergency=0, ordering=0, total=1)
    return {"parameters": p, "demandTrace": demand,
            "shockTrace": [{"time": .2, "delay": .4}], "policies": policies}


def capture():
    base = comparison()
    return {"present": True, "runs": {"baseline": base, "repeat": copy.deepcopy(base),
            "no_emergency": comparison({"emergencyCapacity": 0}),
            "no_safety": comparison({"safetyStockDays": 0}),
            "no_demand": comparison({"ordersPerDay": 0})},
            "display": [{"policy": p["policy"], "otif": ".5", "total": str(p["costs"]["total"])}
                        for p in base["policies"]]}


class PolicyComparisonTests(unittest.TestCase):
    def test_hand_calculated_partial_delivery_and_holding_integral(self):
        result = validate_policy_comparison(comparison(), INPUT)
        self.assertEqual(result["P0"]["metrics"]["otif"], .5)
        self.assertEqual(result["P0"]["metrics"]["inTransit"], 1)
        self.assertEqual(result["P0"]["costs"]["total"], 6.4)

    def test_pending_orders_stay_in_otif_denominator(self):
        value = comparison()
        value["policies"][0]["metrics"]["otif"] = 1
        with self.assertRaisesRegex(ValueError, "KPI otif"):
            validate_policy_comparison(value, INPUT)

    def test_reserved_orders_cannot_be_deducted_twice(self):
        value = comparison()
        value["policies"][0]["stateLog"][2]["inventoryPosition"] -= 1
        with self.assertRaisesRegex(ValueError, "이중 차감"):
            validate_policy_comparison(value, INPUT)

    def test_fabricated_state_totals_fail_against_order_events(self):
        value = comparison()
        value["policies"][0]["stateLog"][-1]["completed"] = 2
        with self.assertRaisesRegex(ValueError, "completed"):
            validate_policy_comparison(value, INPUT)

    def test_holding_integral_requires_every_inventory_transition(self):
        value = comparison()
        value["policies"][0]["stateLog"] = [s for s in value["policies"][0]["stateLog"] if s["time"] != .2]
        with self.assertRaisesRegex(ValueError, "상태 전이"):
            validate_policy_comparison(value, INPUT)

    def test_duplicate_receipt_id_or_po_mode_rejected(self):
        for changed_id in (False, True):
            with self.subTest(changed_id=changed_id):
                value = comparison()
                duplicate = value["policies"][0]["receipts"][0].copy()
                if changed_id:
                    duplicate["id"] = "OTHER"
                value["policies"][0]["receipts"].append(duplicate)
                with self.assertRaisesRegex(ValueError, "중복"):
                    validate_policy_comparison(value, INPUT)

    def test_emergency_quantity_is_removed_from_regular_receipt(self):
        value = comparison()
        value["policies"][2]["receipts"].append({"id": "R2", "purchaseOrderId": "PO1",
                                                "mode": "regular", "time": .8, "quantity": 1})
        with self.assertRaisesRegex(ValueError, "입고 불일치"):
            validate_policy_comparison(value, INPUT)

    def test_emergency_cannot_depart_after_regular_departure(self):
        value = comparison()
        value["policies"][2]["purchaseOrders"][0]["emergencyDepartureAt"] = .25
        with self.assertRaisesRegex(ValueError, "출발 전"):
            validate_policy_comparison(value, INPUT)

    def test_costs_are_recomputed_from_quantities_not_just_sum(self):
        value = comparison()
        value["policies"][2]["costs"].update(emergency=3, total=7.4)
        with self.assertRaisesRegex(ValueError, "비용 emergency"):
            validate_policy_comparison(value, INPUT)

    def test_policy_demand_ids_must_match_common_trace(self):
        value = comparison()
        value["policies"][1]["orders"][1]["id"] = "C"
        with self.assertRaisesRegex(ValueError, "공통 수요"):
            validate_policy_comparison(value, INPUT)

    def test_nonfinite_or_boolean_metric_cannot_pass(self):
        for invalid in (float("nan"), float("inf"), True, -1):
            with self.subTest(invalid=invalid):
                value = comparison()
                value["policies"][0]["metrics"]["otif"] = invalid
                with self.assertRaises(ValueError):
                    validate_policy_comparison(value, INPUT)

    def test_optional_missing_api_keeps_legacy_checks(self):
        checks, receipt = policy_comparison_checks({"present": False})
        self.assertEqual(checks, [])
        self.assertEqual(receipt["status"], "not_present")

    def test_full_capture_passes_and_preserves_no_business_claim(self):
        with patch.dict(POLICY_INPUT, INPUT, clear=True):
            checks, receipt = policy_comparison_checks(capture())
        self.assertEqual(receipt["status"], "passed", receipt)
        self.assertTrue(all(row["status"] == "passed" for row in checks))
        self.assertIn("산업적 우월성 검증 아님", receipt["scope"])

    def test_display_must_match_raw_metric_not_rounded_claim(self):
        value = capture()
        value["display"][0]["otif"] = "1"
        with patch.dict(POLICY_INPUT, INPUT, clear=True):
            checks, receipt = policy_comparison_checks(value)
        self.assertEqual(receipt["status"], "failed")
        self.assertTrue(any(row["status"] == "failed" for row in checks))

    def test_invalid_scenario_is_recorded_as_failure(self):
        value = capture()
        value["runs"]["no_emergency"]["parameters"]["emergencyCapacity"] = 1
        with patch.dict(POLICY_INPUT, INPUT, clear=True):
            _, receipt = policy_comparison_checks(value)
        self.assertEqual(receipt["status"], "failed")
        self.assertTrue(any("입력 미반영" in error for error in receipt["errors"]))


if __name__ == "__main__":
    unittest.main()
