"""Postprocessing recovery tests use a synthetic browser receipt, never a real run."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from office.development import _hash
from office.prototype_recovery import recover_prototype
from office.prototypes import NETWORK_PROFILE, PrototypeWorkspace
from office.providers import CodeReviewer
from office.worker import render_report
from tests.test_report_recovery import FixtureEngine, MISSION_ID, ATTEMPT_ID


class BrowserFixture:
    def __init__(self):
        self.calls = []
        self.status = "passed"
        self.policy = {"present": True, "status": "passed"}
        self.on_verify = None

    def verify(self, url, artifacts, output_dir):
        self.calls.append(output_dir)
        (output_dir / "prototype-browser").mkdir()
        (output_dir / "prototype-browser/fixture.txt").write_text("Synthetic receipt only", encoding="utf-8")
        if self.on_verify:
            self.on_verify()
        return {"status": self.status, "artifacts": dict(artifacts), "business_acceptance": False,
                "checks": [{"name": "Synthetic fixture", "status": self.status}],
                "policy_comparison": self.policy, "errors": []}


class PrototypeRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.engine = FixtureEngine(self.root)
        self.addCleanup(self.engine.db.close)
        self.engine._require_prototyper = lambda employee_id: None
        self.engine.prototypes = PrototypeWorkspace(self.root, self.root)
        self.addCleanup(self.engine.prototypes.close)
        self.browser = BrowserFixture()
        self.engine.prototype_verifier = self.browser
        self.folder = self.root / "organization-runs" / ATTEMPT_ID
        self.folder.mkdir(parents=True)
        self.engine.prototypes.prepare(self.folder)
        model = self.folder / "workspace/model.js"
        model.write_text(model.read_text(encoding="utf-8") + "\n// Synthetic employee fixture change.\n", encoding="utf-8")
        self.criteria = ["합성 모델 초안과 근거 보고"]
        self.document = {
            "summary": "합성 데모 초안을 작성했습니다.",
            "analysis": {"priority": "normal", "complexity": "simple", "rationale": "테스트", "process": ["모델 구현"]},
            "outcome": {"status": "achieved", "progress_percent": 100, "basis": "합성 자체 판단"},
            "report": [{"title": "데모 초안", "content": "합성 테스트 문서이며 실제 고객 효과를 확인하지 않았습니다."}],
            "accomplishments": ["초안 작성"], "remaining": [], "limitations": ["산업적 검증 없음"], "milestones": [],
            "evidence": [{"criterion": self.criteria[0], "status": "met", "artifact_section": "report[0].content", "explanation": "합성 보고"}]}
        self.execution = {"completed": True, "exit_code": 0, "error": None}
        self.write_original()

    def write_original(self):
        (self.folder / "result.json").write_text(json.dumps(self.document, ensure_ascii=False), encoding="utf-8")
        (self.folder / "report.md").write_text(render_report(self.document), encoding="utf-8")
        (self.folder / "events.jsonl").write_text('{"type":"turn.completed"}\n', encoding="utf-8")
        (self.folder / "execution.json").write_text(json.dumps(self.execution), encoding="utf-8")
        (self.folder / "prototype-browser.json").write_text('{"status":"failed","summary":"Original failure"}', encoding="utf-8")
        verification = CodeReviewer().review(self.folder, self.execution, self.criteria, "daslab")
        self.assertTrue(verification["passed"], verification)
        failure = {"ready": False, "artifacts": {}, "error": "Original static failure"}
        mission = {"id": MISSION_ID, "employee_id": "das-rd", "execution_mode": "prototype", "status": "failed",
                   "acceptance_criteria": self.criteria, "result": None, "verification": "not_verified",
                   "error": "Original static failure", "prototype": copy.deepcopy(failure)}
        attempt = {"id": ATTEMPT_ID, "mission_id": MISSION_ID, "execution_mode": "prototype", "status": "failed",
                   "verification": verification, "error": "Original static failure", "number": 1,
                   "prototype": copy.deepcopy(failure), "duration_seconds": 12.5}
        with self.engine.db:
            self.engine._save("missions", mission)
            self.engine._save("attempts", attempt)

    def recover(self, **changes):
        arguments = {"engine": self.engine, "mission_id": MISSION_ID, "review_note": "서버 검사 오류 수정 후 동일 산출물만 다시 검수합니다."}
        arguments.update(changes)
        return recover_prototype(**arguments)

    def originals(self):
        return {str(path.relative_to(self.folder)): path.read_bytes() for path in self.folder.rglob("*")
                if path.is_file() and "prototype-rechecks" not in path.parts}

    def change(self, table, **changes):
        key = MISSION_ID if table == "missions" else ATTEMPT_ID
        with self.engine.db:
            value = self.engine._get(table, key)
            value.update(changes)
            self.engine._save(table, value)

    def test_success_preserves_run_files_failed_attempt_claims_and_counter(self):
        before, attempt = self.originals(), self.engine._get("attempts", ATTEMPT_ID)
        result = self.recover()
        self.assertTrue(result["passed"])
        self.assertEqual(result["status"], "review")
        self.assertEqual(self.originals(), before)
        after = self.engine._get("attempts", ATTEMPT_ID)
        proof = after.pop("prototype_reverification")
        after.pop("prototype_rechecks")
        self.assertEqual(after, attempt)
        self.assertEqual(len(self.engine._attempts(MISSION_ID)), 1)
        mission = self.engine._get("missions", MISSION_ID)
        self.assertEqual(mission["result"], self.document)
        self.assertEqual(mission["verification"], "structural_only")
        self.assertTrue(mission["prototype"]["browser_verified"])
        self.assertEqual(mission["prototype"]["preview_url"], f"/prototypes/{ATTEMPT_ID}/")
        self.assertEqual(Path(proof["path"]), self.browser.calls[0])
        self.assertTrue((Path(proof["path"]) / "prototype-browser/fixture.txt").is_file())
        self.assertEqual(proof["source_pin_basis"], "recheck_start_snapshot")
        with self.assertRaises(ValueError):
            self.recover()

    def test_existing_report_hash_change_is_rejected_before_browser(self):
        (self.folder / "report.md").write_text("changed", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "최초 검증"):
            self.recover()
        self.assertEqual(self.browser.calls, [])
        self.assertFalse((self.folder / "prototype-rechecks").exists())

    def test_active_wrong_mode_nonterminal_and_latest_unverified_are_rejected(self):
        for change in ({"status": "queued"}, {"status": "pausing"}, {"execution_mode": "analysis"}, {"pending_action": "cancel"}):
            self.write_original()
            self.change("missions", **change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.recover()
        self.write_original()
        self.engine._active = {"mission_id": "c" * 32}
        with self.assertRaises(ValueError):
            self.recover()
        self.engine._active = None
        self.change("attempts", verification={"passed": False})
        with self.assertRaises(ValueError):
            self.recover()
        self.assertEqual(self.browser.calls, [])

    def test_execution_must_have_completed_and_inputs_are_bounded(self):
        for changes in ({"review_note": " "}, {"review_note": "a" * 4001}, {"mission_id": "../bad"}, {"require_policy_comparison": 1}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.recover(**changes)
        (self.folder / "execution.json").write_text(json.dumps({"completed": False, "exit_code": 0}), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "정상 종료"):
            self.recover()
        self.assertEqual(self.browser.calls, [])

    def test_required_missing_policy_and_browser_failure_remain_blocked(self):
        for status, policy in (("passed", {"present": False, "status": "not_present"}),
                               ("unavailable", {"present": True, "status": "passed"})):
            self.write_original()
            self.browser.status, self.browser.policy = status, policy
            before = self.originals()
            result = self.recover()
            self.assertFalse(result["passed"])
            self.assertEqual(result["status"], "blocked")
            self.assertFalse(result["prototype"]["ready"])
            self.assertFalse(result["prototype"]["browser_verified"])
            self.assertEqual(self.originals(), before)
            self.assertEqual(self.engine._get("attempts", ATTEMPT_ID)["status"], "failed")

    def test_explicit_basic_check_scope_allows_absent_policy_but_preserves_partial(self):
        self.document["outcome"] = {"status": "partial", "progress_percent": None, "basis": "고객 검증 미완료"}
        self.document["remaining"] = ["고객 데이터로 검증"]
        self.document["evidence"][0]["status"] = "unknown"
        self.write_original()
        self.browser.policy = {"present": False, "status": "not_present"}
        result = self.recover(require_policy_comparison=False)
        self.assertTrue(result["passed"])
        self.assertEqual(self.engine._get("missions", MISSION_ID)["result"], self.document)

    def test_source_or_report_change_during_verification_blocks_promotion(self):
        for filename in ("workspace/model.js", "report.md", "prototype-baseline.json"):
            self.write_original()
            path = self.folder / filename
            before = path.read_bytes()
            self.browser.on_verify = lambda: path.write_bytes(before + b"\n ")
            result = self.recover()
            self.assertFalse(result["passed"], filename)
            self.assertEqual(result["status"], "blocked")
            self.assertFalse(result["prototype"]["model_verified"])
            path.write_bytes(before)

    def test_prior_source_pin_is_enforced_and_unchanged_seed_is_blocked(self):
        self.change("attempts", prototype={"ready": True, "artifacts": {"model.js": "0" * 64}})
        with self.assertRaisesRegex(ValueError, "기존 데모"):
            self.recover()
        self.write_original()
        from office.prototypes import SEED
        (self.folder / "workspace/model.js").write_bytes(SEED["model.js"].encode("utf-8"))
        result = self.recover()
        self.assertFalse(result["passed"])
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(self.browser.calls, [])

    def test_failed_recheck_pins_sources_execution_and_baseline_for_retries(self):
        self.browser.status = "failed"
        first = self.recover()
        self.assertFalse(first["passed"])
        self.browser.status = "passed"
        for filename in ("workspace/model.js", "execution.json", "prototype-baseline.json"):
            path = self.folder / filename
            before = path.read_bytes()
            path.write_bytes(before + b"\n ")
            with self.subTest(filename=filename), self.assertRaisesRegex(ValueError, "첫 재검수"):
                self.recover()
            path.write_bytes(before)
        self.assertEqual(len(self.browser.calls), 1)
        self.assertEqual(len(list((self.folder / "prototype-rechecks").iterdir())), 1)
        second = self.recover()
        self.assertTrue(second["passed"])
        proof = second["prototype_reverification"]
        self.assertEqual(proof["source_pin_basis"], "previous_recheck")
        self.assertEqual(proof["source_artifacts"], first["prototype_reverification"]["source_artifacts"])
        self.assertTrue((Path(first["prototype_reverification"]["path"]) / "provenance.json").is_file())


class NetworkBrowserFixture(BrowserFixture):
    def verify(self, url, artifacts, output_dir):
        receipt = super().verify(url, artifacts, output_dir)
        receipt.pop("policy_comparison")
        receipt.update(profile=NETWORK_PROFILE, kind="supply_network_gis_checks")
        return receipt


class NetworkPrototypeRecoveryTests(unittest.TestCase):
    write_original = PrototypeRecoveryTests.write_original
    originals = PrototypeRecoveryTests.originals
    change = PrototypeRecoveryTests.change
    recover = PrototypeRecoveryTests.recover

    def setUp(self):
        PrototypeRecoveryTests.setUp(self)
        self.network_browser = NetworkBrowserFixture()
        self.verifier = patch("office.prototype_recovery.NetworkVerifier", return_value=self.network_browser)
        self.verifier.start()
        self.addCleanup(self.verifier.stop)
        self.configure_network()

    def configure_network(self, *, code_change=True):
        from office.network_prototypes import SEED

        self.write_original()
        self.parent_id = "c" * 32
        self.identity = {"project_id": self.parent_id, "profile": NETWORK_PROFILE}
        files = {name: value.encode("utf-8") for name, value in SEED.items()}
        for name, raw in files.items():
            (self.folder / "workspace" / name).write_bytes(raw)
        baseline = {**self.identity, "files": {name: _hash(raw) for name, raw in files.items()},
                    "source": "built-in synthetic network GIS seed", "seed": True}
        (self.folder / "prototype-baseline.json").write_text(json.dumps(baseline), encoding="utf-8")
        changed = self.folder / "workspace" / ("model.js" if code_change else "README.md")
        changed.write_bytes(changed.read_bytes() + b"\n// Synthetic employee fixture change.\n")
        self.pin = self.engine.prototypes.repair_snapshot(self.folder, **self.identity)
        failure = {**self.pin, "attempt_id": ATTEMPT_ID, "ready": False, "artifacts": {},
                   "error": "Original failed source scanner"}
        self.change("missions", parent_mission_id=self.parent_id, prototype_project_id=self.parent_id,
                    prototype_profile=NETWORK_PROFILE, prototype=copy.deepcopy(failure))
        self.change("attempts", prototype=copy.deepcopy(failure))
        self.parent = {"id": self.parent_id, "status": "paused", "pending_action": None,
                       "workflow": {"kind": "business", "profile": NETWORK_PROFILE, "child_ids": [MISSION_ID]}}
        with self.engine.db:
            self.engine._save("missions", self.parent)

    def test_network_recheck_uses_registered_verifier_without_inventory_policy_and_preserves_failure(self):
        from office.organization import OrganizationEngine

        before, original = self.originals(), self.engine._get("attempts", ATTEMPT_ID)
        result = self.recover()  # Same API default that requires inventory policy evidence.
        self.assertTrue(result["passed"], result)
        self.assertEqual(result["status"], "review")
        self.assertEqual(self.browser.calls, [])
        self.assertEqual(len(self.network_browser.calls), 1)
        self.assertEqual(self.originals(), before)
        self.assertEqual(self.engine._get("missions", self.parent_id), self.parent)
        stored = self.engine._get("attempts", ATTEMPT_ID)
        self.assertEqual(OrganizationEngine._prototype_receipt(stored), result["prototype"])
        proof = stored.pop("prototype_reverification")
        stored.pop("prototype_rechecks")
        self.assertEqual(stored, original)
        self.assertEqual(proof["source_pin_basis"], "original_repair_snapshot")
        self.assertEqual(proof["profile"], NETWORK_PROFILE)
        self.assertFalse(proof["require_policy_comparison"])
        self.assertTrue(proof["requested_policy_comparison"])
        self.assertEqual(result["prototype"]["browser"]["artifacts"], self.pin["repair_artifacts"])
        self.assertEqual(self.engine._get("missions", MISSION_ID)["result"], self.document)
        self.engine._prototype_receipt = OrganizationEngine._prototype_receipt
        self.assertTrue(OrganizationEngine.prototype_preview(self.engine, ATTEMPT_ID).startswith("http://127.0.0.1:"))
        inherited = self.root / "organization-runs" / ("d" * 32)
        self.engine.prototypes.prepare(inherited, self.folder, **self.identity)
        inherited_baseline = json.loads((inherited / "prototype-baseline.json").read_text(encoding="utf-8"))
        self.assertEqual(inherited_baseline["files"], result["prototype"]["artifacts"])
        self.assertEqual(inherited_baseline["project_id"], self.parent_id)
        self.assertEqual(inherited_baseline["profile"], NETWORK_PROFILE)
        self.assertEqual(self.originals(), before)

    def test_network_recheck_rejects_missing_or_changed_repair_pins_before_browser(self):
        for defect in ("source", "baseline", "missing_pin", "wrong_attempt", "wrong_pin_profile"):
            with self.subTest(defect=defect):
                self.configure_network()
                attempt = self.engine._get("attempts", ATTEMPT_ID)
                if defect in ("source", "baseline"):
                    path = self.folder / ("workspace/model.js" if defect == "source" else "prototype-baseline.json")
                    path.write_bytes(path.read_bytes() + b"\n ")
                elif defect == "missing_pin":
                    attempt["prototype"] = {"ready": False, "artifacts": {}}
                elif defect == "wrong_attempt":
                    attempt["prototype"]["attempt_id"] = "d" * 32
                else:
                    attempt["prototype"]["profile"] = "inventory-policy-v1"
                with self.engine.db:
                    self.engine._save("attempts", attempt)
                with self.assertRaises(ValueError):
                    self.recover()
                self.assertEqual(self.network_browser.calls, [])

    def test_network_recheck_requires_same_paused_parent_and_workspace_profile(self):
        for defect in ("parent_running", "parent_pending", "wrong_parent_profile", "unowned_child", "wrong_mission_profile", "wrong_project"):
            with self.subTest(defect=defect):
                self.configure_network()
                parent = copy.deepcopy(self.parent)
                if defect == "parent_running":
                    parent["status"] = "queued"
                elif defect == "parent_pending":
                    parent["pending_action"] = "cancelled"
                elif defect == "wrong_parent_profile":
                    parent["workflow"]["profile"] = "inventory-policy-v1"
                elif defect == "unowned_child":
                    parent["workflow"]["child_ids"] = []
                elif defect == "wrong_mission_profile":
                    self.change("missions", prototype_profile="inventory-policy-v1")
                else:
                    self.change("missions", prototype_project_id="d" * 32)
                with self.engine.db:
                    self.engine._save("missions", parent)
                with self.assertRaises(ValueError):
                    self.recover()
                self.assertEqual(self.network_browser.calls, [])

    def test_network_document_only_change_cannot_be_promoted(self):
        self.configure_network(code_change=False)
        result = self.recover()
        self.assertFalse(result["passed"])
        self.assertIn("모델·화면", result["prototype_reverification"]["error"])
        self.assertEqual(self.network_browser.calls, [])

    def test_network_wrong_browser_profile_kind_or_failure_cannot_be_promoted(self):
        original_verify = self.network_browser.verify
        for changes in ({"profile": "inventory-policy-v1"}, {"kind": "inventory_checks"},
                        {"status": "unavailable"}, {"artifacts": {}}, {"checks": []},
                        {"errors": ["Browser failure"]}, {"business_acceptance": True}):
            with self.subTest(changes=changes):
                self.configure_network()
                def invalid_receipt(*args):
                    return {**original_verify(*args), **changes}
                with patch.object(self.network_browser, "verify", side_effect=invalid_receipt):
                    result = self.recover()
                self.assertFalse(result["passed"])
                self.assertEqual(result["status"], "blocked")
                self.assertFalse(result["prototype"]["browser_verified"])
                self.assertEqual(self.engine._get("attempts", ATTEMPT_ID)["status"], "failed")

    def test_network_capture_pins_are_checked_and_passed_recheck_takes_precedence(self):
        from office.organization import OrganizationEngine

        attempt = self.engine._get("attempts", ATTEMPT_ID)
        capture = copy.deepcopy(attempt["prototype"])
        attempt["prototype"] = {"ready": False, "artifacts": {}, "error": "Original scanner failure"}
        attempt["prototype_repair_capture"] = capture
        with self.engine.db:
            self.engine._save("attempts", attempt)
        result = self.recover()
        self.assertTrue(result["passed"])
        self.assertEqual(result["prototype_reverification"]["source_pin_basis"], "captured_repair_snapshot")
        stored = self.engine._get("attempts", ATTEMPT_ID)
        self.assertEqual(stored["prototype_repair_capture"], capture)
        self.assertEqual(OrganizationEngine._prototype_receipt(stored), result["prototype"])


class PrototypeRecoveryIntegrationTests(unittest.TestCase):
    # Reuse the bounded worker fixture, while running only this new integration.
    from tests.test_prototype_organization import PrototypeOrganizationTests as _Fixture
    setUp = _Fixture.setUp
    cleanup = _Fixture.cleanup
    payload = _Fixture.payload
    submit = _Fixture.submit
    until = _Fixture.until

    def test_recovered_preview_and_next_worker_inherit_original_workspace(self):
        with patch.object(self.engine.prototypes, "finish", return_value={
                "ready": False, "artifacts": {}, "error": "Synthetic static postprocessing failure"}):
            first = self.until(self.submit(), {"failed"})
        mission_id = first["mission"]["id"]
        attempt_id = first["attempts"][0]["id"]
        original_attempt = copy.deepcopy(self.engine._get("attempts", attempt_id))
        recovered = recover_prototype(self.engine, mission_id, "합성 후처리 복구 통합 검사", require_policy_comparison=False)
        self.assertTrue(recovered["passed"], recovered)
        preview = self.engine.prototype_preview(attempt_id)
        self.assertTrue(preview.startswith("http://127.0.0.1:"))
        original_folder = self.engine.data_dir / "organization-runs" / attempt_id
        original_model = (original_folder / "workspace/model.js").read_bytes()
        with patch.object(self.engine.prototypes, "prepare", wraps=self.engine.prototypes.prepare) as prepare:
            second = self.until(self.submit(), {"review", "failed"})
        self.assertEqual(second["mission"]["status"], "review", second)
        self.assertEqual(prepare.call_args.args[1], original_folder)
        next_folder = self.engine.data_dir / "organization-runs" / second["attempts"][0]["id"]
        baseline = json.loads((next_folder / "prototype-baseline.json").read_text(encoding="utf-8"))
        self.assertEqual(baseline["source"], str(original_folder))
        self.assertEqual(baseline["files"], recovered["prototype"]["artifacts"])
        self.assertEqual((original_folder / "workspace/model.js").read_bytes(), original_model)
        after = self.engine._get("attempts", attempt_id)
        after.pop("prototype_reverification")
        after.pop("prototype_rechecks")
        self.assertEqual(after, original_attempt)


if __name__ == "__main__":
    unittest.main()
