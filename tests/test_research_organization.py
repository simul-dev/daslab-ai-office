"""Research dispatch, evidence and owner learning; no real provider execution."""
import copy
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import tests.test_organization as organization_tests
from office.organization import OrganizationEngine
from office.store import Conflict


class ResearchRecordingWorker(organization_tests.RecordingWorker):
    def __init__(self):
        super().__init__()
        self.research_calls = []
        self.receipt = {"enabled": True, "observed": True, "call_count": 2, "completed_count": 2}

    def execute(self, run_dir, prompt, timeout_seconds, cancel_event, on_event=None, research=False):
        self.research_calls.append(research)
        result = super().execute(run_dir, prompt, timeout_seconds, cancel_event, on_event)
        if research and self.receipt is not None:
            result["web_search"] = copy.deepcopy(self.receipt)
        return result


class ResearchOrganizationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "config").mkdir()
        (self.root / "knowledge").mkdir()
        (self.root / "config/office.json").write_text(
            json.dumps({"daily_runs": 20, "max_attempts": 3, "timeout_seconds": 10}), encoding="utf-8")
        (self.root / "config/research.json").write_text(json.dumps({
            "enabled": True, "allowed_employee_ids": ["das-pm", "das-rd", "das-mkt", "das-sales"],
        }), encoding="utf-8")
        (self.root / "knowledge/company-charter.md").write_text("DAS Lab is the current priority.", encoding="utf-8")
        (self.root / "knowledge/daslab-team.md").write_text("Evidence-based research and bounded execution.", encoding="utf-8")
        self.env = patch.dict(os.environ, {}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.worker = ResearchRecordingWorker()
        self.engine = OrganizationEngine(self.root, self.root / "data", worker=self.worker)
        self.addCleanup(self.cleanup)
        self.request_number = 0

    def cleanup(self):
        self.worker.release.set()
        self.engine.close()

    def payload(self, **changes):
        self.request_number += 1
        result = {"request_id": f"research-{self.request_number}", "text": "공개 기술 자료의 현재 근거를 조사해 주세요.",
                  "employee_id": "das-rd", "execution_mode": "research", "context": {"project_id": "daslab-growth"}}
        result.update(changes)
        return result

    def submit(self, **changes):
        return self.engine.submit(self.payload(**changes))["mission"]["id"]

    def await_status(self, mission_id, states):
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            detail = self.engine.detail(mission_id)
            if detail["mission"]["status"] in states:
                return detail
            self.engine.wait_revision(detail["revision"], .02)
        self.fail(f"Mission did not reach {states}: {self.engine.detail(mission_id)}")

    def owner_memories(self, text):
        return [memory for employee in self.engine.snapshot()["employees"] for memory in employee["memories"]
                if text in memory["text"] and memory["verification"] == "owner_statement"]

    def test_explicit_research_uses_worker_and_retains_observed_search_receipt(self):
        detail = self.await_status(self.submit(), {"review", "failed", "blocked"})
        self.assertEqual(detail["mission"]["status"], "review", detail)
        self.assertEqual(self.worker.research_calls, [True])
        self.assertEqual(detail["mission"]["execution_mode"], "research")
        self.assertEqual(detail["mission"]["project_context"]["project_id"], "daslab-growth")
        self.assertEqual(detail["mission"]["web_search"], self.worker.receipt)
        self.assertEqual(detail["attempts"][0]["web_search"], self.worker.receipt)
        self.assertEqual(detail["mission"]["verification"], "structural_only")
        self.assertEqual(self.engine.snapshot()["metrics"]["verified_outcomes"], 0)
        self.assertNotIn("외부 조회, 파일 탐색", self.worker.calls[0])

    def test_research_requires_policy_context_and_authorized_employee_before_queueing(self):
        denied = [
            {"context": None}, {"context": {"project_id": "office-ui"}},
            {"employee_id": "owner"}, {"source_attempt_id": "0" * 32},
            {"delivery_operation": "push"},
        ]
        for changes in denied:
            with self.subTest(changes=changes), self.assertRaises((ValueError, PermissionError)):
                self.engine.submit(self.payload(**changes))
        self.assertEqual(self.engine.snapshot()["missions"], [])
        self.assertEqual(self.worker.calls, [])
        self.assertEqual(self.worker.probes, 0)

    def test_disallowed_research_employee_is_rejected_before_queueing(self):
        self.engine.research_policy["allowed_employee_ids"] = ["das-pm"]
        with self.assertRaises(PermissionError):
            self.submit(employee_id="das-rd")
        self.assertEqual(self.engine.snapshot()["missions"], [])
        self.assertEqual(self.worker.probes, 0)

    def test_assistant_request_routes_to_an_authorized_worker(self):
        detail = self.await_status(self.submit(employee_id="assistant"), {"review"})
        self.assertEqual(detail["mission"]["requested_employee_id"], "assistant")
        self.assertEqual(detail["mission"]["employee_id"], "das-pm")
        self.assertEqual(self.worker.research_calls, [True])

    def test_missing_policy_denies_research_without_provider_use(self):
        self.engine.close()
        (self.root / "config/research.json").unlink()
        self.engine = OrganizationEngine(self.root, self.root / "data", worker=self.worker)
        with self.assertRaises(PermissionError):
            self.submit()
        self.assertEqual(self.worker.calls, [])
        self.assertEqual(self.engine.snapshot()["missions"], [])

    def test_project_context_does_not_enable_research_implicitly(self):
        payload = self.payload(text="자료를 요약해 주세요.")
        del payload["execution_mode"]
        mission_id = self.engine.submit(payload)["mission"]["id"]
        detail = self.await_status(mission_id, {"review"})
        self.assertEqual(detail["mission"]["execution_mode"], "analysis")
        self.assertEqual(self.worker.research_calls, [False])

    def test_growth_pm_analysis_bypasses_specialist_keyword_routing(self):
        mission_id = self.submit(employee_id="das-pm", execution_mode="analysis",
                                 text="시뮬레이션 모델 개발 데모의 주간 계획을 검토해 주세요.")
        detail = self.await_status(mission_id, {"review"})
        self.assertEqual(detail["mission"]["employee_id"], "das-pm")
        self.assertEqual(detail["mission"]["execution_mode"], "analysis")
        self.assertFalse(detail["mission"].get("workflow"))
        self.assertEqual(self.worker.research_calls, [False])

    def test_reusing_request_cannot_upgrade_analysis_to_research(self):
        self.worker.available = False
        payload = self.payload(execution_mode="analysis")
        mission_id = self.engine.submit(payload)["mission"]["id"]
        self.await_status(mission_id, {"deferred"})
        with self.assertRaises(Conflict):
            self.engine.submit({**payload, "execution_mode": "research"})
        self.assertEqual(len(self.engine.snapshot()["missions"]), 1)
        self.assertEqual(self.worker.calls, [])

    def test_missing_or_uncompleted_search_blocks_even_achieved_report(self):
        for receipt in (None, {"enabled": True, "observed": False, "call_count": 0, "completed_count": 0},
                        {"enabled": True, "observed": True, "call_count": 1},
                        {"enabled": True, "observed": True, "call_count": 1, "completed_count": 0}):
            with self.subTest(receipt=receipt):
                self.worker.receipt = receipt
                detail = self.await_status(self.submit(), {"review", "blocked", "failed"})
                self.assertEqual(detail["mission"]["status"], "blocked", detail)
                self.assertNotEqual(detail["mission"]["outcome"], "achieved")
                self.assertNotEqual(detail["mission"]["progress_percent"], 100)

    def test_research_reassignment_cannot_grant_an_unlisted_employee_access(self):
        mission_id = self.submit()
        self.await_status(mission_id, {"review"})
        with self.assertRaises(PermissionError):
            self.engine.action(mission_id, {"action": "reassign", "employee_id": "assistant"})
        self.assertEqual(self.engine.detail(mission_id)["mission"]["employee_id"], "das-rd")
        self.assertEqual(self.worker.research_calls, [True])

    def test_malformed_search_counts_are_not_successful_research(self):
        for count in (True, -1, "1"):
            with self.subTest(completed_count=count):
                self.worker.receipt = {"enabled": True, "observed": True, "call_count": 1, "completed_count": count}
                detail = self.await_status(self.submit(), {"review", "blocked", "failed"})
                self.assertEqual(detail["mission"]["status"], "blocked", detail)

    def test_owner_instruction_is_preserved_for_employee_and_pm_with_scope(self):
        mission_id = self.submit()
        self.await_status(mission_id, {"review"})
        instruction = "모델 검증은 동일 입력의 반복 실험과 비교 근거를 보여 주세요."
        self.engine.action(mission_id, {"action": "instruct", "text": instruction})
        memories = self.owner_memories(instruction)
        self.assertEqual({memory["employee_id"] for memory in memories}, {"das-rd", "das-pm"})
        self.assertTrue(all(memory["source"] == "owner_feedback:" + mission_id for memory in memories))
        title = self.engine.detail(mission_id)["mission"]["title"]
        self.assertTrue(all(title in memory["text"] for memory in memories))
        self.engine.action(mission_id, {"action": "resume"})
        self.await_status(mission_id, {"review"})
        self.assertIn(instruction, self.worker.calls[-1])
        self.assertIn("owner_statement", self.worker.calls[-1])

    def test_rejected_instruction_does_not_create_learned_memory(self):
        mission_id = self.submit()
        self.await_status(mission_id, {"review"})
        self.engine.action(mission_id, {"action": "cancel"})
        instruction = "거부된 지시는 승인된 기대치로 저장하지 마세요."
        with self.assertRaises(Conflict):
            self.engine.action(mission_id, {"action": "instruct", "text": instruction})
        self.assertEqual(self.owner_memories(instruction), [])

    def test_policy_is_rechecked_before_resuming_research(self):
        mission_id = self.submit()
        self.await_status(mission_id, {"review"})
        self.engine.research_policy["enabled"] = False
        with self.assertRaises(PermissionError):
            self.engine.action(mission_id, {"action": "resume"})
        self.assertEqual(self.worker.research_calls, [True])


if __name__ == "__main__":
    unittest.main()
