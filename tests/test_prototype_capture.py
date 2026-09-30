"""Operator-supplied pins preserve current bytes, never claim an old successful run."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from office.organization import OrganizationEngine
from office.prototype_capture import capture_prototype_repair_source
from office.prototypes import FILES, NETWORK_PROFILE, PrototypeWorkspace
from tests.test_report_recovery import FixtureEngine, MISSION_ID, ATTEMPT_ID
from tests import test_organization_http as http_tests


PARENT_ID = "c" * 32


class PrototypeCaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.engine = FixtureEngine(self.root)
        self.addCleanup(self.engine.db.close)
        self.engine._require_prototyper = lambda employee: None
        self.engine._prototype_receipt = OrganizationEngine._prototype_receipt
        self.engine.prototypes = PrototypeWorkspace(self.root, self.root)
        self.addCleanup(self.engine.prototypes.close)
        self.folder = self.root / "organization-runs" / ATTEMPT_ID
        self.identity = {"project_id": PARENT_ID, "profile": NETWORK_PROFILE}
        self.engine.prototypes.prepare(self.folder, **self.identity)
        model = self.folder / "workspace/model.js"
        model.write_bytes(model.read_bytes() + b"\nconst unfinished = ;\n")
        (self.folder / "execution.json").write_text('{"completed":false,"error":"timed out"}', encoding="utf-8")
        (self.folder / "events.jsonl").write_text('{"type":"thread.started"}\n', encoding="utf-8")
        self.pin = self.engine.prototypes.repair_snapshot(self.folder, **self.identity)
        self.parent = {"id": PARENT_ID, "status": "paused", "pending_action": None,
                       "workflow": {"kind": "business", "profile": NETWORK_PROFILE, "child_ids": [MISSION_ID]}}
        self.failure = {"ready": False, "artifacts": {}, "error": "Original failed check"}
        self.mission = {"id": MISSION_ID, "employee_id": "das-rd", "execution_mode": "prototype",
                        "parent_mission_id": PARENT_ID, "prototype_project_id": PARENT_ID,
                        "prototype_profile": NETWORK_PROFILE, "status": "failed", "error": "Original timeout",
                        "pending_action": None, "result": None, "verification": "not_verified",
                        "prototype": copy.deepcopy(self.failure)}
        self.attempt = {"id": ATTEMPT_ID, "mission_id": MISSION_ID, "execution_mode": "prototype",
                        "status": "failed", "ended_at": "2026-09-30T13:00:00+00:00", "duration_seconds": 600,
                        "error": "Original timeout", "prototype": copy.deepcopy(self.failure), "verification": None}
        self.save_original()

    def save_original(self):
        with self.engine.db:
            self.engine._save("missions", self.parent)
            self.engine._save("missions", self.mission)
            self.engine._save("attempts", self.attempt)

    def capture(self, **changes):
        args = {"engine": self.engine, "mission_id": MISSION_ID, "attempt_id": ATTEMPT_ID,
                "expected_artifacts": self.pin["repair_artifacts"],
                "expected_baseline_sha256": self.pin["repair_baseline_sha256"]}
        args.update(changes)
        return capture_prototype_repair_source(**args)

    def test_capture_is_idempotent_preserves_failed_run_and_only_supplies_repair_bytes(self):
        original_files = {str(p.relative_to(self.folder)): p.read_bytes() for p in self.folder.rglob("*") if p.is_file()}
        with patch("office.prototypes.subprocess.run", side_effect=AssertionError("Capture must not execute or parse code")):
            result = self.capture()
            receipt = result["prototype"]
            self.assertTrue(result["captured"])
            self.assertFalse(result["reused"])
            self.assertTrue(receipt["captured_after_failure"])
            self.assertFalse(receipt["original_byte_identity_verified"])
            self.assertFalse(receipt["ready"])
            self.assertFalse(receipt["browser_verified"])
            self.assertEqual(receipt["artifacts"], {})
            self.assertTrue(self.capture()["reused"])
            self.assertEqual(len(self.engine.events), 1)
            after = self.engine._get("attempts", ATTEMPT_ID)
            self.assertEqual(after.pop("prototype_repair_capture"), receipt)
            self.assertEqual(after, self.attempt)
            mission = self.engine._get("missions", MISSION_ID)
            self.assertEqual(mission["prototype"], receipt)
            mission.pop("updated_at")
            mission["prototype"] = self.failure
            self.assertEqual(mission, self.mission)
            stored = self.engine._get("attempts", ATTEMPT_ID)
            self.assertEqual(self.engine._prototype_receipt(stored), receipt)
            with self.assertRaises(KeyError):
                OrganizationEngine.prototype_preview(self.engine, ATTEMPT_ID)
            target = self.root / "organization-runs" / ("d" * 32)
            self.engine.prototypes.prepare(target, self.folder, **self.identity, repair_source=receipt)
            baseline = json.loads((target / "prototype-baseline.json").read_text(encoding="utf-8"))
            self.assertEqual(baseline["files"], self.pin["repair_artifacts"])
            self.assertEqual((target / "workspace/model.js").read_bytes(), (self.folder / "workspace/model.js").read_bytes())
        self.assertEqual({str(p.relative_to(self.folder)): p.read_bytes() for p in self.folder.rglob("*") if p.is_file()}, original_files)
        self.assertEqual(self.engine._get("missions", PARENT_ID), self.parent)

    def test_active_nonterminal_foreign_and_nonlatest_attempts_are_rejected(self):
        mutations = [("parent", {"status": "queued"}), ("parent", {"status": "running"}),
                     ("parent", {"workflow": {**self.parent["workflow"], "profile": "inventory-policy-v1"}}),
                     ("parent", {"workflow": {**self.parent["workflow"], "child_ids": []}}),
                     ("mission", {"prototype_project_id": "d" * 32}),
                     ("mission", {"status": "running"}), ("mission", {"pending_action": "pause"}),
                     ("attempt", {"mission_id": "d" * 32}),
                     ("attempt", {"status": "running"}), ("attempt", {"ended_at": None})]
        for kind, changes in mutations:
            self.save_original()
            original = {"parent": self.parent, "mission": self.mission, "attempt": self.attempt}[kind]
            with self.subTest(kind=kind, changes=changes), self.engine.db:
                self.engine._save("attempts" if kind == "attempt" else "missions", {**original, **changes})
                with self.assertRaises(ValueError):
                    self.capture()
        self.save_original()
        for active, closed in (({"mission_id": "e" * 32}, False), (None, True)):
            self.engine._active, self.engine.closed = active, closed
            with self.assertRaises(ValueError):
                self.capture()
        self.engine._active, self.engine.closed = None, False
        with self.engine.db:
            self.engine._save("attempts", {**self.attempt, "id": "f" * 32})
        with self.assertRaisesRegex(ValueError, "최신"):
            self.capture()
        self.assertEqual(self.engine.events, [])

    def test_different_pins_and_changes_after_first_capture_cannot_repin(self):
        for changes in ({"expected_artifacts": {name: "0" * 64 for name in FILES}},
                        {"expected_baseline_sha256": "0" * 64},
                        {"expected_artifacts": {}}, {"attempt_id": "../escape"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.capture(**changes)
        self.capture()
        model = self.folder / "workspace/model.js"
        model.write_bytes(model.read_bytes() + b"\n// Later change\n")
        with self.assertRaises(ValueError):
            self.capture()
        changed = self.engine.prototypes.repair_snapshot(self.folder, **self.identity)
        with self.assertRaisesRegex(ValueError, "첫 자료 고정"):
            self.capture(expected_artifacts=changed["repair_artifacts"])
        self.assertEqual(len(self.engine.events), 1)

    def test_change_during_capture_writes_no_receipt_or_event(self):
        original = self.engine.prototypes.repair_snapshot
        calls = []
        def mutate(*args, **kwargs):
            result = original(*args, **kwargs)
            calls.append(result)
            if len(calls) == 1:
                path = self.folder / "workspace/model.js"
                path.write_bytes(path.read_bytes() + b"\n// concurrent change\n")
            return result
        with patch.object(self.engine.prototypes, "repair_snapshot", side_effect=mutate), self.assertRaises(ValueError):
            self.capture()
        self.assertEqual(self.engine._get("attempts", ATTEMPT_ID), self.attempt)
        self.assertEqual(self.engine._get("missions", MISSION_ID), self.mission)
        self.assertEqual(self.engine.events, [])

    def test_original_ready_or_end_of_run_pin_is_never_replaced(self):
        for original in ({"ready": True, "artifacts": self.pin["repair_artifacts"]},
                         {"ready": False, "repairable": True}):
            with self.engine.db:
                self.engine._save("attempts", {**self.attempt, "prototype": original})
            with self.subTest(original=original), self.assertRaises(ValueError):
                self.capture()
        ready = {"ready": True, "artifacts": self.pin["repair_artifacts"]}
        self.assertEqual(self.engine._prototype_receipt({"prototype": ready,
                         "prototype_repair_capture": {"ready": False, "repairable": True}}), ready)
        self.assertEqual(self.engine.events, [])


class PrototypeCaptureHTTPTests(unittest.TestCase):
    setUp = http_tests.OrganizationHTTPTests.setUp
    start_server = http_tests.OrganizationHTTPTests.start_server
    stop_server = http_tests.OrganizationHTTPTests.stop_server
    cleanup_runtime = http_tests.OrganizationHTTPTests.cleanup_runtime
    request = http_tests.OrganizationHTTPTests.request

    def test_capture_route_requires_protected_post_and_passes_exact_operator_pins(self):
        path = f"/api/org/missions/{MISSION_ID}/capture-prototype-repair"
        hashes = {name: "d" * 64 for name in FILES}
        body = {"attempt_id": ATTEMPT_ID, "expected_artifacts": hashes, "expected_baseline_sha256": "e" * 64}
        with patch("server.capture_prototype_repair_source", return_value={"captured": True}) as capture:
            status, _, _ = self.request(path, "POST", body, headers={"X-DAS-Office": ""})
            self.assertEqual(status, 403)
            capture.assert_not_called()
            status, _, _ = self.request(path)
            self.assertEqual(status, 404)
            capture.assert_not_called()
            status, _, result = self.request(path, "POST", body)
            self.assertEqual(status, 200, result)
            capture.assert_called_once_with(self.organization, MISSION_ID, ATTEMPT_ID, hashes, "e" * 64)


if __name__ == "__main__":
    unittest.main()
