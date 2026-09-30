"""Repair copies pinned source only; syntax parsing never executes generated code."""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from office.organization import OrganizationEngine
from office.projects import ProjectWorkflow
from office.prototypes import DEFAULT_PROFILE, NETWORK_PROFILE, FILES, PrototypeWorkspace
from tests import test_projects as project_tests
from tests.test_prototype_organization import PrototypeBrowserFixture


class PrototypeRepairSourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.proto = PrototypeWorkspace(self.root, self.root / "data")
        self.addCleanup(self.proto.close)
        self.folder = self.root / "data" / ("a" * 32)
        self.identity = {"project_id": "b" * 32, "profile": NETWORK_PROFILE}
        self.proto.prepare(self.folder, **self.identity)
        self.model = self.folder / "workspace/model.js"
        self.original = self.model.read_bytes()
        self.model.write_bytes(self.original + b"\nconst unfinished = ;\n")

    def receipt(self):
        return {**self.proto.repair_snapshot(self.folder, **self.identity),
                "attempt_id": self.folder.name, "ready": False, "artifacts": {}}

    def target(self, name="next"):
        return self.root / "data" / name

    def test_syntax_failure_can_be_copied_for_repair_without_becoming_a_preview(self):
        real_run = subprocess.run
        with patch("office.prototypes.subprocess.run", wraps=real_run) as run:
            self.assertFalse(self.proto.finish(self.folder)["ready"])
            receipt = self.receipt()
            with self.assertRaises(ValueError):
                self.proto.open_preview(self.folder)
            target = self.target()
            self.proto.prepare(target, self.folder, **self.identity, repair_source=receipt)
            self.assertEqual((target / "workspace/model.js").read_bytes(), self.model.read_bytes())
            self.assertFalse(self.proto.finish(target)["ready"])
            baseline = json.loads((target / "prototype-baseline.json").read_text(encoding="utf-8"))
            self.assertEqual(baseline["files"], receipt["repair_artifacts"])
            self.assertEqual(baseline["repair_source"]["attempt_id"], self.folder.name)
            (target / "workspace/model.js").write_bytes(self.original + b"\n// Fixed syntax, preserving source.\n")
            fixed = self.proto.finish(target)
            self.assertTrue(fixed["ready"], fixed)
            self.assertFalse(fixed["browser_verified"])
            self.assertFalse(fixed["model_verified"])
        self.assertTrue(run.call_args_list)
        self.assertTrue(all(call.args[0][1] == "--check" for call in run.call_args_list))

    def test_explicit_same_project_profile_and_attempt_are_required(self):
        receipt = self.receipt()
        cases = [({"project_id": "c" * 32, "profile": NETWORK_PROFILE}, receipt),
                 ({"project_id": "b" * 32, "profile": DEFAULT_PROFILE}, receipt),
                 (self.identity, {**receipt, "attempt_id": "d" * 32}),
                 (self.identity, {**receipt, "ready": True})]
        for i, (identity, source) in enumerate(cases):
            with self.subTest(case=i), self.assertRaises(ValueError):
                self.proto.prepare(self.target(str(i)), self.folder, **identity, repair_source=source)
            self.assertFalse((self.target(str(i)) / "workspace").exists())
        with self.assertRaises(ValueError):
            self.proto.prepare(self.target(), **self.identity, repair_source=receipt)
        with self.assertRaises(ValueError):
            self.proto.prepare(self.target(), self.folder, **self.identity)

    def test_source_baseline_and_file_set_mutations_after_pin_are_rejected(self):
        receipt = self.receipt()
        baseline = self.folder / "prototype-baseline.json"
        original_baseline = baseline.read_bytes()
        original_model = self.model.read_bytes()
        for i, kind in enumerate(("model", "baseline", "extra")):
            with self.subTest(kind=kind):
                if kind == "model":
                    self.model.write_bytes(original_model + b"\n// after pin\n")
                elif kind == "baseline":
                    baseline.write_bytes(original_baseline + b" ")
                else:
                    (self.folder / "workspace/extra.txt").write_text("out of scope", encoding="utf-8")
                with self.assertRaises(ValueError):
                    self.proto.prepare(self.target(str(i)), self.folder, **self.identity, repair_source=receipt)
                self.assertFalse((self.target(str(i)) / "workspace").exists())
                self.model.write_bytes(original_model)
                baseline.write_bytes(original_baseline)
                (self.folder / "workspace/extra.txt").unlink(missing_ok=True)

    def test_hard_links_and_oversized_sources_are_not_repairable(self):
        outside = self.root / "outside.txt"
        outside.write_text("synthetic outside fixture", encoding="utf-8")
        self.model.unlink()
        try:
            os.link(outside, self.model)
        except OSError as exc:
            self.skipTest("Hard links unavailable: " + str(exc))
        with self.assertRaisesRegex(ValueError, "hard links"):
            self.receipt()
        self.model.unlink()
        self.model.write_bytes(b"x" * 500_001)
        with self.assertRaisesRegex(ValueError, "limit"):
            self.receipt()

    def test_large_readme_is_preserved_only_for_repair_and_must_shrink_before_release(self):
        readme = self.folder / "workspace/README.md"
        readme.write_bytes(b"# Verification transcript\n" + b"x" * (652_628 - 26))
        source_bytes = {name: (self.folder / "workspace" / name).read_bytes() for name in FILES}
        receipt = self.receipt()
        self.assertFalse(self.proto.finish(self.folder)["ready"])
        target = self.target()
        context = self.proto.prepare(target, self.folder, **self.identity, repair_source=receipt)
        self.assertEqual({name: (target / "workspace" / name).read_bytes() for name in FILES}, source_bytes)
        self.assertEqual(json.loads((target / "prototype-baseline.json").read_text(encoding="utf-8"))["files"], receipt["repair_artifacts"])
        self.assertFalse(self.proto.finish(target)["ready"])
        with self.assertRaisesRegex(ValueError, "limit"):
            self.proto.open_preview(target)
        (target / "workspace/README.md").write_text("# Compact verification\nSynthetic cases, core metrics and limitations only.\n", encoding="utf-8")
        (target / "workspace/model.js").write_bytes(self.original)
        self.assertTrue(self.proto.finish(target)["ready"])
        self.assertFalse(context["model_verified"])
        self.assertIn("최종 제출", context["release_contract"]["repair_only_readme"])

    def test_readme_exception_keeps_total_cap_and_pinned_bytes_unchanged(self):
        readme = self.folder / "workspace/README.md"
        for size in (1_000_001, 999_999):
            readme.write_bytes(b"x" * size)
            with self.subTest(size=size), self.assertRaisesRegex(ValueError, "limit"):
                self.receipt()
        readme.write_bytes(b"x" * 650_000)
        receipt = self.receipt()
        readme.write_bytes(readme.read_bytes() + b" changed after capture")
        with self.assertRaisesRegex(ValueError, "source changed"):
            self.proto.prepare(self.target(), self.folder, **self.identity, repair_source=receipt)
        self.assertFalse((self.target() / "workspace").exists())

    def test_release_contract_advertises_exact_imports_and_crypto_stays_disallowed(self):
        target = self.target()
        context = self.proto.prepare(target, **self.identity)
        contract = context["release_contract"]
        self.assertEqual(contract["per_file_limit_bytes"], 500_000)
        self.assertEqual(contract["total_limit_bytes"], 1_000_000)
        self.assertEqual(contract["test_literal_require_allowlist"], ["./model.js", "node:assert/strict", "node:fs", "node:vm"])
        self.assertIn("node:crypto", contract["imports"])
        self.assertIn("전체 JSON", contract["verification_summary"])
        test = target / "workspace/model.test.cjs"
        original = test.read_text(encoding="utf-8")
        test.write_text(original + "\nconst crypto = require('node:crypto');\n", encoding="utf-8")
        result = self.proto.finish(target)
        self.assertFalse(result["ready"])
        self.assertIn("imports", result["error"])
        test.write_text(original + "\nconst fs = require('node:fs'); const vm = require('node:vm');\n", encoding="utf-8")
        self.assertTrue(self.proto.finish(target)["ready"])


class RepairProjectWorker(project_tests.ProjectWorker):
    def __init__(self):
        super().__init__()
        self.decisions = ["research", "develop", "revise", "accept"]
        self.failure = "syntax"
        self.development_inputs = []
        self.workspaces = []
        self.timeouts = []

    def execute(self, run_dir, prompt, timeout_seconds, cancel_event, on_event=None,
                research=False, workspace_dir=None, workspace_kind="office-ui"):
        self.timeouts.append({"prototype": workspace_dir is not None, "seconds": timeout_seconds})
        if workspace_dir is not None:
            self.workspaces.append(workspace_dir)
            self.development_inputs.append({p.name: p.read_bytes() for p in workspace_dir.iterdir()})
            model = workspace_dir / "model.js"
            html = workspace_dir / "index.html"
            if len(self.workspaces) == 1:
                model.write_bytes(model.read_bytes() + b"\n// Selected sector implementation retained.\n"
                                  + (b"const unfinished = ;\n" if self.failure == "syntax" else b""))
                html.write_text(html.read_text(encoding="utf-8").replace("<h1>", "<h1>Sector fixture: ", 1), encoding="utf-8")
                if self.failure == "extra":
                    (workspace_dir / "extra.txt").write_text("outside file scope", encoding="utf-8")
            else:
                model.write_bytes(model.read_bytes().replace(b"const unfinished = ;\n", b"") + b"\n// Repaired execution.\n")
        execution = super().execute(run_dir, prompt, timeout_seconds, cancel_event, on_event, research=research)
        if workspace_dir is not None and len(self.workspaces) == 1 and self.failure == "report":
            (run_dir / "result.json").write_text("{invalid report", encoding="utf-8")
        return execution


class PrototypeRepairWorkflowTests(unittest.TestCase):
    # Reuse setup helpers only; do not run unrelated inherited test methods.
    cleanup = project_tests.ProjectWorkflowTests.cleanup
    payload = project_tests.ProjectWorkflowTests.payload
    submit = project_tests.ProjectWorkflowTests.submit
    children = project_tests.ProjectWorkflowTests.children

    def setUp(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node syntax checker unavailable")
        runtime_env = {key: value for key, value in os.environ.items()
                       if key.upper() in ("SYSTEMROOT", "WINDIR", "TEMP", "TMP", "PATH", "PATHEXT")}
        project_tests.ProjectWorkflowTests.setUp(self)
        os.environ.update(runtime_env)
        node_path = patch("office.prototypes.shutil.which", return_value=node)
        node_path.start()
        self.addCleanup(node_path.stop)
        self.engine.close()
        (self.root / "config/prototypes.json").write_bytes((project_tests.ROOT / "config/prototypes.json").read_bytes())
        self.worker = RepairProjectWorker()
        self.engine = OrganizationEngine(self.root, self.root / "data", worker=self.worker)
        self.browser = PrototypeBrowserFixture()
        verifier = patch("office.network_checks.NetworkVerifier", return_value=self.browser)
        verifier.start()
        self.addCleanup(verifier.stop)

    def until(self, mission_id):
        import time
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            detail = self.engine.detail(mission_id)
            if detail["mission"]["status"] in ("accepted", "blocked", "failed", "deferred"):
                return detail
            self.engine.wait_revision(detail["revision"], .03)
        self.fail("Project did not stop: " + str(self.engine.detail(mission_id)))

    def test_real_syntax_failure_revises_pinned_latest_files_and_keeps_failed_history(self):
        mid = self.submit(project_profile=NETWORK_PROFILE)
        final = self.until(mid)
        self.assertEqual(final["mission"]["status"], "accepted", final)
        _, failed, repaired = self.children(mid)
        receipt = failed["prototype"]
        self.assertEqual(failed["status"], "failed")
        self.assertTrue(receipt["repairable"])
        self.assertFalse(receipt["ready"])
        self.assertEqual(receipt["artifacts"], {})
        self.assertIn("syntax check", receipt["error"])
        self.assertEqual(repaired["prototype_source_attempt_id"], receipt["attempt_id"])
        self.assertEqual(len(self.browser.calls), 1)
        original_bytes = {p.name: p.read_bytes() for p in self.worker.workspaces[0].iterdir()}
        self.assertEqual(self.worker.development_inputs[1], original_bytes)
        self.assertIn(b"Sector fixture", self.worker.development_inputs[1]["index.html"])
        self.assertIn(b"const unfinished = ;", original_bytes["model.js"])
        with self.assertRaises(KeyError):
            self.engine.prototype_preview(receipt["attempt_id"])
        self.assertTrue(repaired["prototype"]["browser_verified"])
        self.assertEqual(final["mission"]["prototype"]["attempt_id"], repaired["prototype"]["attempt_id"])

    def test_invalid_report_still_preserves_safe_source_without_claiming_completion(self):
        self.worker.failure = "report"
        mid = self.submit(project_profile=NETWORK_PROFILE)
        final = self.until(mid)
        self.assertEqual(final["mission"]["status"], "accepted", final)
        _, failed, repaired = self.children(mid)
        self.assertIsNone(failed["result"])
        self.assertFalse(failed["prototype"]["ready"])
        self.assertTrue(failed["prototype"]["repairable"])
        self.assertEqual(self.worker.development_inputs[1]["index.html"], self.worker.workspaces[0].joinpath("index.html").read_bytes())
        self.assertEqual(repaired["supersedes"], failed["id"])

    def test_modified_failed_source_is_rejected_before_next_worker_execution(self):
        self.worker.decisions[-1] = "blocked"
        def mutate_source(context, output):
            if output["action"] == "revise":
                path = self.worker.workspaces[0] / "model.js"
                path.write_bytes(path.read_bytes() + b"\n// changed after receipt\n")
        self.worker.on_decision = mutate_source
        final = self.until(self.submit(project_profile=NETWORK_PROFILE))
        self.assertEqual(final["mission"]["status"], "blocked")
        self.assertEqual(len(self.worker.workspaces), 1)
        self.assertEqual(self.browser.calls, [])
        self.assertEqual(self.children(final["mission"]["id"])[-1]["status"], "failed")

    def test_extra_file_never_becomes_a_repair_source(self):
        self.worker.failure = "extra"
        self.worker.decisions = ["research", "develop", "blocked"]
        final = self.until(self.submit(project_profile=NETWORK_PROFILE))
        self.assertEqual(final["mission"]["status"], "blocked")
        failed = self.children(final["mission"]["id"])[-1]
        self.assertFalse(failed["prototype"].get("repairable", False))
        self.assertFalse(failed["prototype"]["ready"])
        self.assertEqual(self.browser.calls, [])

    def test_project_timeout_is_bounded_and_only_overrides_project_development(self):
        self.engine.projects.policy["prototype_timeout_seconds"] = 10
        final = self.until(self.submit(project_profile=NETWORK_PROFILE))
        self.assertEqual(final["mission"]["status"], "accepted", final)
        self.assertTrue(all(call["seconds"] == 10 for call in self.worker.timeouts))
        self.worker.timeouts.clear()
        self.worker.decisions = ["research", "develop", "blocked"]
        self.engine.projects.policy["prototype_timeout_seconds"] = 900
        self.until(self.submit(project_profile=NETWORK_PROFILE))
        self.assertEqual(self.worker.timeouts, [{"prototype": False, "seconds": 10}, {"prototype": True, "seconds": 900}])
        path = self.root / "config/projects.json"
        original = json.loads(path.read_text(encoding="utf-8"))
        for value in (0, 1801, True, "900"):
            path.write_text(json.dumps({**original, "prototype_timeout_seconds": value}), encoding="utf-8")
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "1800"):
                ProjectWorkflow(self.engine)
        del original["prototype_timeout_seconds"]
        path.write_text(json.dumps(original), encoding="utf-8")
        self.engine.config["timeout_seconds"] = 3600
        self.assertNotIn("prototype_timeout_seconds", ProjectWorkflow(self.engine).policy)


if __name__ == "__main__":
    unittest.main()
