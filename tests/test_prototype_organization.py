"""Isolated prototype dispatch and preview receipts, using synthetic workers."""
import http.client
import json
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

import tests.test_organization as organization_tests
from office.organization import OrganizationEngine


class PrototypeRecordingWorker(organization_tests.RecordingWorker):
    def __init__(self):
        super().__init__()
        self.workspaces = []
        self.kinds = []
        self.research_flags = []
        self.unexpected_file = False
        self.change_model = True

    def execute(self, run_dir, prompt, timeout_seconds, cancel_event, on_event=None,
                workspace_dir=None, research=False, workspace_kind="office-ui"):
        self.workspaces.append(workspace_dir)
        self.kinds.append(workspace_kind)
        self.research_flags.append(research)
        if workspace_dir is not None:
            model = workspace_dir / "model.js"
            if self.change_model:
                model.write_text(model.read_text(encoding="utf-8") + "\n// Synthetic fixture adjustment.\n", encoding="utf-8")
            if self.unexpected_file:
                (workspace_dir / "extra.txt").write_text("Out of scope fixture", encoding="utf-8")
        return super().execute(run_dir, prompt, timeout_seconds, cancel_event, on_event)


class PrototypeBrowserFixture:
    def __init__(self):
        self.calls = []
        self.status = "passed"
        self.on_verify = None

    def verify(self, url, artifacts, output_dir, cancel_event=None):
        self.calls.append({"url": url, "artifacts": dict(artifacts), "output_dir": output_dir})
        if self.on_verify:
            self.on_verify(output_dir)
        return {"status": self.status, "artifacts": dict(artifacts), "business_acceptance": False,
                "summary": "Synthetic browser fixture; no actual browser or model evaluation.",
                "checks": [{"name": "Synthetic contract check", "status": self.status}],
                "errors": [] if self.status == "passed" else ["Synthetic unavailable browser"], "screenshots": []}


class PrototypeOrganizationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "config").mkdir()
        (self.root / "knowledge").mkdir()
        (self.root / "static").mkdir()
        (self.root / "static/office.css").write_text("Preserved operating UI", encoding="utf-8")
        (self.root / "config/office.json").write_text(
            json.dumps({"daily_runs": 20, "max_attempts": 3, "timeout_seconds": 10}), encoding="utf-8")
        source = Path(__file__).resolve().parents[1]
        shutil.copy2(source / "config/prototypes.json", self.root / "config/prototypes.json")
        (self.root / "knowledge/company-charter.md").write_text("DAS Lab priority.", encoding="utf-8")
        (self.root / "knowledge/daslab-team.md").write_text("Scope, evidence and owner expectations.", encoding="utf-8")
        environment = patch.dict(os.environ, {key: "" for key in (
            "OPENAI_API_KEY", "CODEX_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "AZURE_OPENAI_API_KEY")})
        environment.start()
        self.addCleanup(environment.stop)
        self.worker = PrototypeRecordingWorker()
        self.engine = OrganizationEngine(self.root, self.root / "data", worker=self.worker)
        self.browser = PrototypeBrowserFixture()
        self.engine.prototype_verifier = self.browser
        self.addCleanup(self.cleanup)
        self.request_number = 0

    def cleanup(self):
        self.worker.release.set()
        self.engine.close()

    def payload(self, **changes):
        self.request_number += 1
        payload = {"request_id": f"prototype-{self.request_number}", "text": "합성 공급망 모델의 분리된 데모 초안을 개선해 주세요.",
                   "employee_id": "das-rd", "execution_mode": "prototype", "context": {"project_id": "daslab-growth"}}
        payload.update(changes)
        return payload

    def submit(self, **changes):
        return self.engine.submit(self.payload(**changes))["mission"]["id"]

    def until(self, mission_id, states):
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            detail = self.engine.detail(mission_id)
            if detail["mission"]["status"] in states:
                return detail
            self.engine.wait_revision(detail["revision"], .03)
        self.fail(f"Expected {states}, got {self.engine.detail(mission_id)['mission']}")

    def test_enabled_prototypes_do_not_execute_on_startup(self):
        snapshot = self.engine.snapshot()
        self.assertTrue(snapshot["execution"]["prototype_enabled"])
        self.assertEqual(snapshot["missions"], [])
        self.assertEqual(self.worker.calls, [])
        self.assertEqual(self.worker.probes, 0)

    def test_rd_and_pm_use_real_prototype_workspace_and_scoped_verifier_receipt(self):
        with patch.object(self.engine.development, "prepare", side_effect=AssertionError("Office UI must not be prepared")):
            for employee in ("das-rd", "das-pm"):
                detail = self.until(self.submit(employee_id=employee), {"review", "failed", "deferred"})
                self.assertEqual(detail["mission"]["status"], "review", detail["mission"])
                receipt = detail["mission"]["prototype"]
                self.assertTrue(receipt["ready"])
                self.assertIn("model.js", receipt["changed_files"])
                self.assertTrue(receipt["browser_verified"])
                self.assertTrue(receipt["model_verified"])
                self.assertFalse(receipt["browser"]["business_acceptance"])
                self.assertEqual(receipt["artifacts"], self.browser.calls[-1]["artifacts"])
                self.assertFalse(receipt["applied_to_live"])
                self.assertEqual(detail["attempts"][0]["prototype"], receipt)
                workspace = self.worker.workspaces[-1]
                self.assertTrue(workspace.is_relative_to(self.root / "data/organization-runs"))
                self.assertEqual(workspace.name, "workspace")
                self.assertFalse((workspace / "static/office.html").exists())
                context = json.loads((workspace.parent / "input.json").read_text(encoding="utf-8"))
                self.assertIn("prototype", context)
                self.assertNotIn("development", context)
                self.assertEqual(detail["mission"]["verification"], "structural_only")
        self.assertEqual(self.worker.kinds, ["prototype", "prototype"])
        self.assertEqual(self.worker.research_flags, [False, False])
        self.assertEqual(self.engine.snapshot()["metrics"]["verified_outcomes"], 0)
        self.assertEqual((self.root / "static/office.css").read_text(encoding="utf-8"), "Preserved operating UI")

    def test_prototype_scope_employee_and_ui_source_combinations_are_rejected(self):
        for changes in ({"context": None}, {"context": {"project_id": "office-ui"}},
                        {"context": {"project_id": "other"}}, {"employee_id": "das-mkt"},
                        {"employee_id": "das-sales"}, {"source_attempt_id": "0" * 32}, {"delivery_operation": "push"}):
            with self.subTest(changes=changes), self.assertRaises((ValueError, PermissionError)):
                self.engine.submit(self.payload(**changes))
        self.assertEqual(self.engine.snapshot()["missions"], [])
        self.assertEqual(self.worker.calls, [])
        self.assertEqual(self.worker.probes, 0)

    def test_missing_prototype_policy_does_not_grant_workspace_tools(self):
        self.engine.prototype_policy = {}
        with self.assertRaises(PermissionError):
            self.submit()
        self.assertEqual(self.worker.calls, [])
        self.assertEqual(self.engine.snapshot()["missions"], [])

    def test_growth_context_does_not_enable_prototype_implicitly(self):
        payload = self.payload()
        del payload["execution_mode"]
        detail = self.until(self.engine.submit(payload)["mission"]["id"], {"review"})
        self.assertEqual(detail["mission"]["execution_mode"], "analysis")
        self.assertEqual(self.worker.workspaces, [None])
        self.assertFalse(detail["mission"].get("prototype"))

    def test_scope_violation_does_not_produce_a_ready_prototype(self):
        self.worker.unexpected_file = True
        detail = self.until(self.submit(), {"review", "failed"})
        self.assertEqual(detail["mission"]["status"], "failed")
        self.assertFalse((detail["mission"].get("prototype") or {}).get("ready"))
        self.assertEqual(self.engine.snapshot()["metrics"]["verified_outcomes"], 0)

    def test_unchanged_seed_is_not_reported_as_completed_development(self):
        self.worker.change_model = False
        detail = self.until(self.submit(), {"review", "failed"})
        self.assertEqual(detail["mission"]["status"], "failed")
        self.assertFalse((detail["mission"].get("prototype") or {}).get("preview_url"))
        self.assertEqual(self.browser.calls, [])

    def test_unavailable_browser_blocks_claimed_achievement_and_keeps_prototype(self):
        self.browser.status = "unavailable"
        detail = self.until(self.submit(), {"review", "blocked", "failed"})
        self.assertEqual(detail["mission"]["status"], "blocked", detail["mission"])
        self.assertEqual(detail["mission"]["outcome"], "blocked")
        self.assertIsNone(detail["mission"]["progress_percent"])
        receipt = detail["mission"]["prototype"]
        self.assertTrue(receipt["ready"])
        self.assertFalse(receipt["browser_verified"])
        self.assertFalse(receipt["model_verified"])
        self.assertEqual(receipt["browser"]["status"], "unavailable")

    def test_model_change_during_browser_verification_invalidates_receipt(self):
        def mutate(folder):
            model = folder / "workspace/model.js"
            model.write_text(model.read_text(encoding="utf-8") + "\n// Changed during verification.\n", encoding="utf-8")
        self.browser.on_verify = mutate
        detail = self.until(self.submit(), {"review", "blocked", "failed"})
        self.assertEqual(detail["mission"]["status"], "failed", detail["mission"])
        with self.assertRaises(ValueError):
            self.engine.prototype_preview(detail["attempts"][0]["id"])

    def test_prototype_preview_is_loopback_read_only_and_hides_engine_files(self):
        detail = self.until(self.submit(), {"review", "failed"})
        self.assertEqual(detail["mission"]["status"], "review", detail["mission"])
        receipt = detail["mission"]["prototype"]
        location = urlsplit(self.engine.prototype_preview(receipt["attempt_id"]))
        self.assertEqual(location.hostname, "127.0.0.1")
        for method, path, expected in (("GET", "/", 200), ("GET", "/api/org", 404),
                                       ("GET", "/README.md", 404), ("POST", "/", 405)):
            with self.subTest(method=method, path=path):
                connection = http.client.HTTPConnection(location.hostname, location.port, timeout=3)
                try:
                    connection.request(method, path)
                    response = connection.getresponse()
                    self.assertEqual(response.status, expected)
                    self.assertIn("connect-src 'none'", response.getheader("Content-Security-Policy"))
                    response.read()
                finally:
                    connection.close()

    def test_changed_preview_artifacts_cannot_be_reopened_as_verified_original(self):
        detail = self.until(self.submit(), {"review", "failed"})
        self.assertEqual(detail["mission"]["status"], "review", detail["mission"])
        attempt_id = detail["mission"]["prototype"]["attempt_id"]
        model = self.worker.workspaces[-1] / "model.js"
        model.write_text(model.read_text(encoding="utf-8") + "\n// Changed after receipt.\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.engine.prototype_preview(attempt_id)

    def test_prototype_requires_capable_worker_before_creating_attempt(self):
        self.engine.worker = organization_tests.RecordingWorker()
        detail = self.until(self.submit(), {"deferred"})
        self.assertEqual(detail["attempts"], [])
        self.assertEqual(self.engine.worker.calls, [])

    def test_prototype_reassignment_and_resume_recheck_authority(self):
        self.worker.available = False
        mission_id = self.submit()
        self.until(mission_id, {"deferred"})
        with self.assertRaises(PermissionError):
            self.engine.action(mission_id, {"action": "reassign", "employee_id": "das-mkt"})
        self.engine.prototype_policy["enabled"] = False
        with self.assertRaises(PermissionError):
            self.engine.action(mission_id, {"action": "resume"})
        self.assertEqual(self.worker.calls, [])


if __name__ == "__main__":
    unittest.main()
