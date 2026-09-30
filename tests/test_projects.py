"""Business PM lifecycle with disposable SQLite and scripted offline workers."""
import copy
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from office.organization import OrganizationEngine
from office.store import Conflict
from office.worker import render_report
from tests.test_project_decisions import decision
from tests.test_research_organization import ResearchRecordingWorker


ROOT = Path(__file__).resolve().parents[1]


class ProjectWorker(ResearchRecordingWorker):
    def __init__(self):
        super().__init__()
        self.decisions = ["research", "accept"]
        self.decision_calls = []
        self.child_contexts = []
        self.outcomes = []
        self.on_decision = None
        self.decision_entered = threading.Event()
        self.decision_release = threading.Event()
        self.decision_release.set()

    def execute_project_decision(self, run_dir, context, timeout_seconds, cancel_event, on_event=None):
        self.decision_calls.append(copy.deepcopy(context))
        self.decision_entered.set()
        self.decision_release.wait(5)
        if not self.decisions:
            raise AssertionError("Unexpected extra PM execution")
        item = self.decisions.pop(0)
        output = decision(item) if isinstance(item, str) else copy.deepcopy(item)
        if output["action"] == "accept":
            output["criteria"] = list(context["goal_criteria"])
        if output["action"] == "revise" and output["target_child_id"] == "a" * 32:
            output["target_child_id"] = context["mission"]["workflow"]["current_child_id"]
        if self.on_decision:
            self.on_decision(context, output)
        (run_dir / "result.json").write_text(json.dumps(output, ensure_ascii=False), encoding="utf-8")
        (run_dir / "events.jsonl").write_text('{"type":"turn.completed"}\n', encoding="utf-8")
        return {"completed": True, "exit_code": 0, "error": None}

    def execute(self, run_dir, prompt, timeout_seconds, cancel_event, on_event=None, research=False):
        self.child_contexts.append(json.loads((run_dir / "input.json").read_text(encoding="utf-8")))
        execution = super().execute(run_dir, prompt, timeout_seconds, cancel_event, on_event, research=research)
        outcome = self.outcomes.pop(0) if self.outcomes else "achieved"
        if outcome != "achieved":
            result_path = run_dir / "result.json"
            document = json.loads(result_path.read_text(encoding="utf-8"))
            document["outcome"] = {"status": outcome, "progress_percent": None, "basis": "Synthetic missing evidence"}
            document["remaining"] = ["Missing fixture evidence"]
            for evidence in document["evidence"]:
                evidence["status"] = "unknown"
            result_path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
            (run_dir / "report.md").write_text(render_report(document), encoding="utf-8")
        return execution


class ProjectWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "config").mkdir()
        (self.root / "knowledge").mkdir()
        for name in ("projects.json", "research.json"):
            (self.root / "config" / name).write_bytes((ROOT / "config" / name).read_bytes())
        (self.root / "config/office.json").write_text(
            json.dumps({"daily_runs": 100, "max_attempts": 3, "timeout_seconds": 10}), encoding="utf-8")
        (self.root / "knowledge/company-charter.md").write_text("Preserve the original owner goal.", encoding="utf-8")
        (self.root / "knowledge/daslab-team.md").write_text("Bounded PM and specialist responsibilities.", encoding="utf-8")
        self.env = patch.dict(os.environ, {}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.worker = ProjectWorker()
        self.engine = OrganizationEngine(self.root, self.root / "data", worker=self.worker)
        self.addCleanup(self.cleanup)
        self.sequence = 0

    def cleanup(self):
        self.worker.release.set()
        self.worker.decision_release.set()
        self.engine.close()

    def payload(self, **changes):
        self.sequence += 1
        result = {"request_id": f"project-{self.sequence}", "employee_id": "das-pm", "execution_mode": "project",
                  "project_profile": "research-deliverable-v1", "context": {"project_id": "daslab-growth"},
                  "text": "시장조사 근거로 의사결정에 쓸 비교 보고와 제안 초안을 완성해 주세요."}
        result.update(changes)
        return result

    def submit(self, **changes):
        return self.engine.submit(self.payload(**changes))["mission"]["id"]

    def until(self, mission_id, states=("accepted", "blocked", "failed", "deferred")):
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            detail = self.engine.detail(mission_id)
            if detail["mission"]["status"] in states:
                return detail
            self.engine.wait_revision(detail["revision"], .02)
        self.fail(f"Project did not reach {states}: {self.engine.detail(mission_id)}")

    def children(self, mission_id):
        with self.engine.lock:
            return self.engine.projects.children(self.engine._get("missions", mission_id))

    def test_ordinary_pm_and_assistant_intake_selects_projects_without_a_form(self):
        for requested in ("das-pm", "assistant", None):
            with self.subTest(requested=requested), self.engine.lock:
                payload = self.payload(text="최근 방법론을 비교하고 적용안을 만들어줘",
                                       context={"project_id": "office-ui"})
                payload.pop("execution_mode")
                payload.pop("project_profile")
                if requested is None:
                    payload.pop("employee_id")
                else:
                    payload["employee_id"] = requested
                before = copy.deepcopy(payload)
                mission = self.engine.submit(payload)["mission"]
                self.assertEqual(mission["execution_mode"], "project")
                self.assertEqual(mission["employee_id"], "das-pm")
                self.assertEqual(mission["requested_employee_id"], requested or "assistant")
                self.assertEqual(mission["workflow"]["profile"], "research-deliverable-v1")
                self.assertEqual(mission["project_context"]["project_id"], "daslab-growth")
                self.assertEqual(mission["text"], payload["text"])
                self.assertEqual(payload, before)
                self.engine.action(mission["id"], {"action": "cancel"})
        self.assertEqual(self.worker.decision_calls, [])

    def test_network_topics_select_the_registered_profile_despite_ambient_office_context(self):
        for text in ("공급망 GIS 화면 개선해", "입지 후보를 비교하고 적용안을 만들어줘",
                     "네트워크 대안을 비교하고 적용안을 만들어줘", "GIS로 의사결정 대안을 만들어줘"):
            with self.subTest(text=text), self.engine.lock:
                payload = self.payload(text=text, context={"project_id": "office-ui"})
                payload.pop("execution_mode")
                payload.pop("project_profile")
                mission = self.engine.submit(payload)["mission"]
                self.assertEqual(mission["execution_mode"], "project")
                self.assertEqual(mission["workflow"]["profile"], "supply-network-gis-v1")
                self.assertEqual(mission["project_context"]["project_id"], "daslab-growth")
                self.assertEqual(mission["text"], text)
                self.assertTrue(set(mission["acceptance_criteria"]) <= set(mission["workflow"]["goal_criteria"]))
                self.engine.action(mission["id"], {"action": "cancel"})

    def test_organization_ui_and_pinned_preview_keep_the_existing_delivery_route(self):
        text = "조직 화면 디자인 개선해"
        payload = {"employee_id": "das-pm", "text": text, "context": {"project_id": "office-ui"}}
        self.assertFalse(self.engine.projects.wants(payload, text, "das-pm"))
        context = self.engine._project_context(payload)
        self.assertTrue(self.engine._is_development(text, context))
        with patch.dict(self.engine.development_policy, {"managed_pm": {"enabled": True}}):
            self.assertTrue(self.engine.management.wants(payload, "development", "das-pm"))
        for extra in ({"source_attempt_id": "b" * 32}, {"delivery_operation": "push"}):
            pinned = {**payload, "text": "이 작업을 이어서 진행해", **extra}
            before = copy.deepcopy(pinned)
            self.assertFalse(self.engine.projects.wants(pinned, pinned["text"], "das-pm"))
            self.assertEqual(pinned, before)

    def test_explicit_research_and_direct_specialists_remain_single_staff_work(self):
        cases = [("das-pm", "research", "최근 방법론의 공식 자료를 조사해줘"),
                 ("das-mkt", None, "최근 방법론을 비교하고 적용안을 만들어줘"),
                 ("das-sales", None, "제공한 근거로 내부 제안서를 만들어줘"),
                 ("das-rd", None, "공급망 적용 방법을 비교해줘")]
        for employee, mode, text in cases:
            with self.subTest(employee=employee, mode=mode), self.engine.lock:
                payload = self.payload(employee_id=employee, text=text)
                payload.pop("project_profile")
                if mode is None:
                    payload.pop("execution_mode")
                else:
                    payload["execution_mode"] = mode
                mission = self.engine.submit(payload)["mission"]
                self.assertEqual(mission["employee_id"], employee)
                self.assertEqual(mission["execution_mode"], mode or "analysis")
                self.assertNotIn("workflow", mission)
                self.assertEqual(mission["project_context"]["project_id"], "daslab-growth")
                self.engine.action(mission["id"], {"action": "cancel"})
        self.assertEqual(self.worker.decision_calls, [])

    def test_research_draft_accept_preserves_goal_and_uses_actual_child_evidence(self):
        self.worker.decisions = ["research", "draft", "accept"]
        payload = self.payload()
        mid = self.engine.submit(payload)["mission"]["id"]
        detail = self.until(mid)
        self.assertEqual(detail["mission"]["status"], "accepted", detail)
        self.assertEqual(detail["mission"]["text"], payload["text"])
        self.assertEqual([c["execution_mode"] for c in self.children(mid)], ["research", "analysis"])
        self.assertEqual(self.worker.research_calls, [True, False])
        self.assertEqual([c["stage"] for c in self.worker.decision_calls], ["plan", "review", "review"])
        self.assertEqual(self.worker.decision_calls[-1]["sources"][0]["web_search"]["completed_count"], 2)
        self.assertEqual(self.worker.child_contexts[-1]["owner_goal"]["owner_goal"], payload["text"])
        self.assertEqual(len(self.worker.child_contexts[-1]["owner_goal"]["prior_work"]), 2)
        self.assertEqual(self.engine._daily_used(), 5)
        self.assertTrue(self.engine.submit(payload)["duplicate"])
        self.assertEqual(len(self.children(mid)), 2)
        with self.assertRaises(Conflict):
            self.engine.submit({**payload, "project_profile": "supply-network-gis-v1"})

    def test_partial_work_is_revised_in_place_without_erasing_prior_claims(self):
        first_decision = {**decision("research"), "criteria": ["공식 출처 조사", "아직 배정하지 않은 GIS 개발까지 완료"]}
        corrected_decision = {**decision("revise"), "criteria": ["공식 출처 조사", "개발에 넘길 문제·방법·입력 조건 명세"]}
        self.worker.decisions = [first_decision, corrected_decision, "accept"]
        self.worker.outcomes = ["partial", "achieved"]
        mid = self.submit()
        detail = self.until(mid)
        self.assertEqual(detail["mission"]["status"], "accepted", detail)
        first, revised = self.children(mid)
        self.assertEqual(first["result"]["outcome"]["status"], "partial")
        self.assertEqual(revised["supersedes"], first["id"])
        self.assertEqual(revised["employee_id"], first["employee_id"])
        self.assertEqual(revised["execution_mode"], first["execution_mode"])
        self.assertEqual(first["acceptance_criteria"], first_decision["criteria"])
        self.assertEqual(revised["acceptance_criteria"], corrected_decision["criteria"])
        self.assertEqual(detail["mission"]["workflow"]["goal_criteria"], self.worker.decision_calls[0]["goal_criteria"])
        self.assertEqual(detail["mission"]["workflow"]["round"], 1)
        with self.assertRaises(Conflict):
            self.engine.action(revised["id"], {"action": "resume"})

    def test_past_partial_child_can_be_revised_after_an_independent_step(self):
        self.worker.decisions = ["research", "draft", "accept", "revise", "accept"]
        self.worker.outcomes = ["partial", "achieved", "achieved"]

        def select_first_child(context, output):
            if output["action"] == "revise":
                output["target_child_id"] = context["sources"][0]["id"]

        self.worker.on_decision = select_first_child
        mid = self.submit()
        final = self.until(mid)
        self.assertEqual(final["mission"]["status"], "accepted", final)
        first, draft, revised = self.children(mid)
        self.assertEqual([c["execution_mode"] for c in (first, draft, revised)], ["research", "analysis", "research"])
        self.assertEqual(first["result"]["outcome"]["status"], "partial")
        self.assertEqual(draft["result"]["outcome"]["status"], "achieved")
        self.assertEqual(revised["supersedes"], first["id"])
        self.assertEqual(revised["employee_id"], first["employee_id"])
        self.assertEqual(final["attempts"][2]["status"], "failed")
        self.assertIn("미완료", self.worker.decision_calls[3]["decision_feedback"])
        self.assertEqual(final["mission"]["workflow"]["round"], 1)

    def test_superseded_blocked_work_stays_in_history_without_current_staff_attention(self):
        self.worker.decisions = ["research", "revise", "request_input", "accept"]
        self.worker.outcomes = ["blocked", "achieved"]
        mid = self.submit()
        waiting = self.until(mid)
        first, revised = self.children(mid)
        original_detail = self.engine.detail(first["id"])
        snapshot = self.engine.snapshot()
        staff = next(e for e in snapshot["employees"] if e["id"] == "das-rd")
        old = next(m for m in snapshot["missions"] if m["id"] == first["id"])
        self.assertEqual(old["status"], "blocked")
        self.assertEqual(old["superseded_by"], revised["id"])
        self.assertFalse(old["current_work"])
        self.assertEqual(staff["status"], "review")
        self.assertEqual(staff["current_mission"]["id"], revised["id"])
        self.assertEqual(staff["metrics"]["assigned"], 2)
        self.assertEqual(staff["metrics"]["reports"], 2)
        self.assertEqual(snapshot["metrics"]["attention"], 1)  # Only the parent's input request.
        self.assertEqual(waiting["children"][0]["superseded_by"], revised["id"])

        self.engine.projects.answer(mid, {"input_round": 1, "answers": {"capacity_data": "추가 자료 없음"}})
        self.assertEqual(self.until(mid)["mission"]["status"], "accepted")
        final = self.engine.snapshot()
        staff = next(e for e in final["employees"] if e["id"] == "das-rd")
        self.assertEqual(staff["status"], "idle")
        self.assertIsNone(staff["current_mission"])
        self.assertEqual(staff["current_mission_ids"], [])
        self.assertEqual(final["metrics"]["attention"], 0)
        self.assertEqual(self.children(mid)[0], first)
        preserved = self.engine.detail(first["id"])
        self.assertEqual(preserved["attempts"], original_detail["attempts"])
        self.assertEqual(preserved["mission"]["report"], original_detail["mission"]["report"])
        self.assertEqual(preserved["mission"]["remaining"], original_detail["mission"]["remaining"])

    def test_foreign_or_broken_successor_does_not_hide_an_unresolved_task(self):
        self.worker.decisions = ["research", "draft", "blocked"]
        self.worker.outcomes = ["blocked", "achieved"]
        mid = self.submit()
        self.until(mid)
        first, independent = self.children(mid)
        for bad_parent in ("f" * 32, mid):
            with self.subTest(bad_parent=bad_parent), self.engine.changed, self.engine.db:
                fake_successor = {**independent, "supersedes": first["id"], "parent_mission_id": bad_parent}
                # The same-parent case is still an invalid research→analysis lineage.
                self.engine._save("missions", fake_successor)
                snapshot = self.engine.snapshot()
                old = next(m for m in snapshot["missions"] if m["id"] == first["id"])
                self.assertIsNone(old["superseded_by"])
                self.assertTrue(old["current_work"])
                staff = next(e for e in snapshot["employees"] if e["id"] == "das-rd")
                self.assertEqual(staff["current_mission"]["id"], first["id"])
                self.assertEqual(snapshot["metrics"]["attention"], 2)
                self.engine._save("missions", independent)

    def test_revision_rejects_foreign_or_missing_target_without_touching_other_project(self):
        other_mid = self.submit()
        self.assertEqual(self.until(other_mid)["mission"]["status"], "accepted")
        other_child = copy.deepcopy(self.children(other_mid)[0])
        for target in (other_child["id"], "f" * 32):
            with self.subTest(target_kind="foreign" if target == other_child["id"] else "missing"):
                start = len(self.worker.decision_calls)
                self.worker.decisions = ["research", decision("revise", target), "revise", "accept"]
                self.worker.outcomes = ["partial", "achieved"]
                mid = self.submit()
                final = self.until(mid)
                self.assertEqual(final["mission"]["status"], "accepted", final)
                first, revised = self.children(mid)
                self.assertEqual(revised["supersedes"], first["id"])
                self.assertEqual(final["attempts"][1]["status"], "failed")
                self.assertTrue(self.worker.decision_calls[start + 2]["decision_feedback"])
                self.assertEqual(self.children(other_mid)[0], other_child)

    def test_already_superseded_child_cannot_be_revised_twice(self):
        self.worker.decisions = ["research", "revise", "revise", "accept"]
        self.worker.outcomes = ["partial", "achieved"]

        def select_first_child(context, output):
            if output["action"] == "revise":
                output["target_child_id"] = context["sources"][0]["id"]

        self.worker.on_decision = select_first_child
        mid = self.submit()
        final = self.until(mid)
        self.assertEqual(final["mission"]["status"], "accepted", final)
        first, revised = self.children(mid)
        self.assertEqual(revised["supersedes"], first["id"])
        self.assertEqual(final["attempts"][2]["status"], "failed")
        self.assertTrue(self.worker.decision_calls[3]["decision_feedback"])
        self.assertEqual(final["mission"]["workflow"]["round"], 1)

    def test_input_answers_resume_automatically_and_are_idempotent_and_round_bound(self):
        self.worker.decisions = ["request_input", "request_input", "research", "accept"]
        mid = self.submit()
        first = self.until(mid)
        self.assertEqual(first["mission"]["workflow"]["stage"], "awaiting_input")
        with self.assertRaises(Conflict):
            self.engine.action(mid, {"action": "resume"})
        first_answer = {"input_round": 1, "answers": {"capacity_data": "현재 내부 자료가 없으므로 명시한 합성 가정으로 검토"}}
        self.engine.projects.answer(mid, first_answer)
        second = self.until(mid)
        self.assertEqual(second["mission"]["workflow"]["input_round"], 2)
        self.engine.projects.answer(mid, first_answer)
        self.assertEqual(self.engine.detail(mid)["mission"]["status"], "blocked")
        self.assertEqual(len(self.worker.decision_calls), 2)
        for answer in ({"input_round": 0, "answers": first_answer["answers"]},
                       {"input_round": 1, "answers": {"capacity_data": "changed prior answer"}}):
            with self.assertRaises(Conflict):
                self.engine.projects.answer(mid, answer)
        with self.assertRaises(ValueError):
            self.engine.projects.answer(mid, {"input_round": 2, "answers": {"wrong_id": "answer"}})
        second_answer = {"input_round": 2, "answers": {"capacity_data": "추가자료 없이 검증 한계를 명시"}}
        self.engine.projects.answer(mid, second_answer)
        final = self.until(mid)
        self.assertEqual(final["mission"]["status"], "accepted", final)
        self.assertEqual([a["input_round"] for a in self.worker.decision_calls[-1]["owner_answers"]], [1, 2])
        self.engine.projects.answer(mid, second_answer)
        self.assertEqual(len(self.worker.decision_calls), 4)

    def prototype_projection_fixture(self):
        self.worker.decisions = ["blocked"]
        mid = self.submit(project_profile="supply-network-gis-v1")
        self.until(mid)
        with self.engine.changed, self.engine.db:
            parent = self.engine._get("missions", mid)
            child_id, attempt_id = "e" * 32, "f" * 32
            failed = {"ready": False, "artifacts": {}, "error": "Original static scanner failure"}
            passed = {"ready": True, "attempt_id": attempt_id, "project_id": mid,
                      "profile": "supply-network-gis-v1", "artifacts": {"model.js": "a" * 64},
                      "browser_verified": True, "model_verified": True,
                      "browser": {"status": "passed", "profile": "supply-network-gis-v1"}}
            child = {**copy.deepcopy(parent), "id": child_id, "request_id": "projection-child",
                     "parent_mission_id": mid, "prototype_project_id": mid,
                     "prototype_profile": "supply-network-gis-v1", "execution_mode": "prototype",
                     "employee_id": "das-rd", "status": "review", "error": None,
                     "prototype": passed, "result": {"summary": "Synthetic recheck result",
                         "outcome": {"status": "achieved", "progress_percent": 100}}}
            child.pop("workflow")
            attempt = {**copy.deepcopy(self.engine._attempts(mid)[0]), "id": attempt_id,
                       "mission_id": child_id, "employee_id": "das-rd", "execution_mode": "prototype",
                       "status": "failed", "error": failed["error"], "prototype": failed,
                       "prototype_reverification": {"passed": True, "prototype": passed}}
            parent["workflow"].update(child_ids=[child_id], current_child_id=child_id, stage="reviewing")
            parent["prototype"] = failed
            self.engine._save("missions", child)
            self.engine._save("attempts", attempt)
            self.engine._save("missions", parent)
        return mid, child_id, attempt_id, passed

    def test_pm_context_projects_current_child_recheck_without_rewriting_failed_parent(self):
        mid, child_id, attempt_id, passed = self.prototype_projection_fixture()
        with self.engine.lock:
            parent = self.engine._get("missions", mid)
            before = copy.deepcopy(parent)
            original_attempt = self.engine._get("attempts", attempt_id)
            context = self.engine.projects.context(parent)
            self.assertEqual(context["mission"]["prototype"], passed)
            self.assertEqual(context["sources"][0]["prototype"], passed)
            self.assertEqual(context["mission"]["status"], "blocked")
            self.assertEqual(context["mission"]["workflow"], before["workflow"])
            public_view = self.engine._mission_view(parent)
            self.assertEqual(public_view["prototype"], passed)
            self.assertEqual(public_view["status"], "blocked")
            public_snapshot = next(m for m in self.engine.snapshot()["missions"] if m["id"] == mid)
            self.assertEqual(public_snapshot["prototype"], passed)
            self.assertEqual(public_snapshot["status"], "blocked")
            self.assertEqual(parent, before)
            self.assertEqual(self.engine._get("missions", mid), before)
            self.assertEqual(self.engine._get("attempts", attempt_id), original_attempt)
            self.assertEqual(original_attempt["status"], "failed")
            self.assertFalse(original_attempt["prototype"]["ready"])
            self.assertEqual(len(self.worker.decision_calls), 1)

    def test_projection_never_inherits_other_project_or_profile_receipt(self):
        mid, child_id, _, _ = self.prototype_projection_fixture()
        with self.engine.changed, self.engine.db:
            parent = self.engine._get("missions", mid)
            original_child = self.engine._get("missions", child_id)
            for changes in ({"parent_mission_id": "d" * 32}, {"prototype_project_id": "d" * 32},
                            {"prototype_profile": "inventory-policy-v1"},
                            {"prototype": {**original_child["prototype"], "project_id": "d" * 32}},
                            {"prototype": None}):
                with self.subTest(changes=changes):
                    self.engine._save("missions", {**original_child, **changes})
                    projected = self.engine.projects.mission_projection(parent)
                    self.assertNotIn("prototype", projected)
                    self.assertEqual(projected["status"], "blocked")
            self.engine._save("missions", original_child)

    def test_projection_shows_latest_failed_development_instead_of_older_pass(self):
        mid, child_id, _, _ = self.prototype_projection_fixture()
        with self.engine.changed, self.engine.db:
            parent = self.engine._get("missions", mid)
            newest = copy.deepcopy(self.engine._get("missions", child_id))
            failed = {"ready": False, "artifacts": {}, "error": "New development remains unverified"}
            newest.update(id="d" * 32, request_id="projection-successor", supersedes=child_id,
                          status="failed", prototype=failed)
            parent["workflow"]["child_ids"].append(newest["id"])
            parent["workflow"]["current_child_id"] = newest["id"]
            self.engine._save("missions", newest)
            self.engine._save("missions", parent)
            context = self.engine.projects.context(parent)
            self.assertEqual(context["mission"]["prototype"], failed)
            self.assertEqual(context["sources"][-1]["prototype"], failed)
            self.assertEqual(context["mission"]["status"], "blocked")

    def test_network_contract_reaches_saved_research_child_input(self):
        from office.network_prototypes import README

        self.worker.decisions = ["research", "blocked"]
        mid = self.submit(project_profile="supply-network-gis-v1")
        final = self.until(mid)
        self.assertEqual(final["mission"]["status"], "blocked", final)
        child = self.children(mid)[0]
        self.assertEqual(child["execution_mode"], "research")
        attempt_id = self.engine.detail(child["id"])["attempts"][0]["id"]
        stored_input = json.loads((self.root / "data" / "organization-runs" / attempt_id / "input.json").read_text(encoding="utf-8"))
        pm_context = self.worker.decision_calls[0]
        child_context = stored_input["owner_goal"]
        self.assertEqual(pm_context["profile"], "supply-network-gis-v1")
        self.assertEqual(child_context["profile"], pm_context["profile"])
        self.assertEqual(pm_context["development_contract"], README)
        self.assertEqual(child_context["development_contract"], pm_context["development_contract"])
        self.assertEqual(self.worker.child_contexts[0]["owner_goal"], child_context)
        self.assertNotIn("prototype", stored_input)
        self.assertNotIn("development", stored_input)

    def test_input_wait_instruction_preserves_questions_and_replans_without_stale_answer(self):
        for prior_child in (False, True):
            with self.subTest(prior_child=prior_child):
                self.worker.decisions = (["research"] if prior_child else []) + ["request_input", "draft", "accept"]
                mid = self.submit()
                waiting = self.until(mid)
                flow = waiting["mission"]["workflow"]
                questions = copy.deepcopy(flow["input_requests"])
                original_criteria = list(flow["goal_criteria"])
                calls_before = len(self.worker.decision_calls)
                instruction = "내부 자료는 없습니다. 공개 근거와 명시한 합성 가정으로 가능한 범위를 다시 정해 주세요."
                paused = self.engine.action(mid, {"action": "instruct", "text": instruction})
                self.assertEqual(paused["mission"]["status"], "paused")
                expected_stage = "reviewing" if prior_child else "planning"
                self.assertEqual(paused["mission"]["workflow"]["stage"], expected_stage)
                self.assertEqual(paused["mission"]["workflow"]["input_requests"], [])
                preserved = [h for h in paused["mission"]["workflow"]["history"] if h["stage"] == "input_superseded"]
                self.assertEqual(preserved[-1]["input_requests"], questions)
                self.assertEqual(preserved[-1]["input_round"], flow["input_round"])
                self.assertEqual(len(self.worker.decision_calls), calls_before)
                with self.assertRaises(Conflict):
                    self.engine.projects.answer(mid, {"input_round": flow["input_round"], "answers": {"capacity_data": "늦게 도착한 답변"}})
                self.engine.action(mid, {"action": "resume"})
                final = self.until(mid)
                self.assertEqual(final["mission"]["status"], "accepted", final)
                resumed_context = self.worker.decision_calls[calls_before]
                self.assertEqual(resumed_context["stage"], "review" if prior_child else "plan")
                self.assertEqual(resumed_context["mission"]["instructions"][-1]["text"], instruction)
                self.assertEqual(resumed_context["owner_answers"], [])
                self.assertEqual(final["mission"]["workflow"]["goal_criteria"], original_criteria)

    def test_paused_input_wait_resumes_same_questions_before_answering(self):
        self.worker.decisions = ["request_input", "research", "accept"]
        mid = self.submit()
        waiting = self.until(mid)
        flow = waiting["mission"]["workflow"]
        answer = {"input_round": flow["input_round"], "answers": {"capacity_data": "현재 자료가 없으므로 합성 가정을 명시해 주세요."}}
        paused = self.engine.action(mid, {"action": "pause"})
        self.assertEqual(paused["mission"]["status"], "paused")
        self.assertEqual(paused["mission"]["workflow"]["stage"], "awaiting_input")
        with self.assertRaises(Conflict):
            self.engine.projects.answer(mid, answer)
        resumed = self.engine.action(mid, {"action": "resume"})
        self.assertEqual(resumed["mission"]["status"], "blocked")
        self.assertEqual(resumed["mission"]["workflow"]["input_round"], flow["input_round"])
        self.assertEqual(resumed["mission"]["workflow"]["input_requests"], flow["input_requests"])
        self.assertEqual(len(self.worker.decision_calls), 1)
        self.assertEqual(self.children(mid), [])
        self.engine.projects.answer(mid, answer)
        final = self.until(mid)
        self.assertEqual(final["mission"]["status"], "accepted", final)
        self.assertEqual(self.worker.decision_calls[1]["owner_answers"][0]["answers"], answer["answers"])
        self.assertEqual(final["mission"]["workflow"]["input_requests"], [])
        self.assertEqual(len(final["mission"]["workflow"]["answers"]), 1)

    def test_child_pause_discards_late_result_and_restart_resumes_same_child(self):
        self.worker.release.clear()
        mid = self.submit()
        self.assertTrue(self.worker.entered.wait(3))
        child_id = self.children(mid)[0]["id"]
        self.engine.action(mid, {"action": "pause"})
        self.worker.release.set()
        self.until(mid, {"paused"})
        self.assertIsNone(self.engine.detail(child_id)["mission"]["summary"])
        self.assertEqual(self.engine.detail(child_id)["attempts"][0]["status"], "cancelled")
        self.engine.close()
        self.engine = OrganizationEngine(self.root, self.root / "data", worker=self.worker)
        self.engine.action(mid, {"action": "resume"})
        final = self.until(mid)
        self.assertEqual(final["mission"]["status"], "accepted", final)
        self.assertEqual([c["id"] for c in self.children(mid)], [child_id])
        self.assertEqual(len(self.engine.detail(child_id)["attempts"]), 2)
        self.assertEqual(len(self.worker.decision_calls), 2)

    def test_cancel_cascades_to_child_and_late_pm_decision_cannot_spawn_work(self):
        self.worker.release.clear()
        mid = self.submit()
        self.assertTrue(self.worker.entered.wait(3))
        child_id = self.children(mid)[0]["id"]
        self.engine.action(mid, {"action": "cancel"})
        self.worker.release.set()
        self.until(mid, {"cancelled"})
        self.assertEqual(self.engine.detail(child_id)["mission"]["status"], "cancelled")
        self.assertIsNone(self.engine.detail(child_id)["mission"]["summary"])
        with self.assertRaises(Conflict):
            self.engine.action(mid, {"action": "resume"})
        self.worker.decisions = ["research"]
        self.worker.decision_entered.clear()
        self.worker.decision_release.clear()
        next_mid = self.submit()
        self.assertTrue(self.worker.decision_entered.wait(3))
        self.engine.action(next_mid, {"action": "cancel"})
        self.worker.decision_release.set()
        final = self.until(next_mid, {"cancelled"})
        self.assertEqual(final["attempts"][0]["status"], "cancelled")
        self.assertEqual(self.children(next_mid), [])

    def test_daily_limit_defers_child_and_resume_keeps_plan_and_execution_history(self):
        self.engine.config["daily_runs"] = 1
        mid = self.submit()
        paused = self.until(mid)
        self.assertEqual(paused["mission"]["status"], "deferred", paused)
        child_id = self.children(mid)[0]["id"]
        self.assertEqual(self.worker.calls, [])
        self.assertEqual(self.engine._daily_used(), 1)
        self.engine.config["daily_runs"] = 8
        self.engine.action(mid, {"action": "resume"})
        final = self.until(mid)
        self.assertEqual(final["mission"]["status"], "accepted", final)
        self.assertEqual([c["id"] for c in self.children(mid)], [child_id])
        self.assertEqual(len(self.worker.decision_calls), 2)
        self.assertEqual(self.engine._daily_used(), 3)

    def test_revision_limit_stops_repeated_partial_work_without_extra_child(self):
        self.engine.projects.policy["max_revisions"] = 1
        self.worker.decisions = ["research", "revise", "revise"]
        self.worker.outcomes = ["partial", "partial"]
        mid = self.submit()
        final = self.until(mid)
        self.assertEqual(final["mission"]["status"], "blocked", final)
        self.assertIn("재작업 한도", final["mission"]["error"])
        self.assertEqual(len(self.children(mid)), 2)
        self.assertEqual(final["mission"]["workflow"]["round"], 1)

    def test_two_research_revisions_do_not_spend_the_next_tasks_revision_allowance(self):
        self.worker.decisions = ["research", "revise", "revise", "draft", "revise", "accept"]
        self.worker.outcomes = ["partial", "partial", "achieved", "partial", "achieved"]
        mid = self.submit()
        final = self.until(mid)
        self.assertEqual(final["mission"]["status"], "accepted", final)
        first, first_fix, second_fix, draft, draft_fix = self.children(mid)
        self.assertEqual(first_fix["supersedes"], first["id"])
        self.assertEqual(second_fix["supersedes"], first_fix["id"])
        self.assertEqual(draft_fix["supersedes"], draft["id"])
        self.assertEqual(final["mission"]["workflow"]["round"], 3)
        self.assertEqual(final["mission"]["workflow"]["max_revisions"], 2)
        context = self.worker.decision_calls[-1]
        self.assertEqual([s["revision_count"] for s in context["sources"]], [0, 1, 2, 0, 1])
        self.assertEqual(context["revision_policy"], {"scope": "task_lineage", "maximum": 2, "total_so_far": 3})
        self.assertEqual(final["mission"]["workflow"]["goal_criteria"], self.worker.decision_calls[0]["goal_criteria"])
        self.assertEqual(self.engine._daily_used(), 11)

    def test_second_lineage_keeps_its_own_cap_and_partial_work_still_blocks_acceptance(self):
        self.worker.decisions = ["research", "revise", "revise", "draft", "revise", "revise", "accept", "revise"]
        self.worker.outcomes = ["partial", "partial", "achieved", "partial", "partial", "partial"]
        mid = self.submit()
        final = self.until(mid)
        self.assertEqual(final["mission"]["status"], "blocked", final)
        self.assertIn("업무 계보의 재작업 한도", final["mission"]["error"])
        self.assertEqual(len(self.children(mid)), 6)
        self.assertEqual(final["mission"]["workflow"]["round"], 4)
        self.assertEqual(final["attempts"][-2]["decision"]["action"], "accept")
        self.assertIn("미완료", final["attempts"][-2]["error"])
        self.assertEqual(final["attempts"][-1]["status"], "failed")
        self.assertEqual(self.worker.decision_calls[-1]["sources"][-1]["remaining_revisions"], 0)
        self.assertEqual(self.engine._daily_used(), 14)

    def test_independent_lineages_cannot_bypass_the_project_assignment_limit(self):
        self.worker.decisions = ["research"] + ["draft"] * 8
        mid = self.submit()
        final = self.until(mid)
        self.assertEqual(final["mission"]["status"], "blocked", final)
        self.assertIn("프로젝트 배정 한도", final["mission"]["error"])
        self.assertEqual(len(self.children(mid)), 8)
        self.assertEqual(final["mission"]["workflow"]["round"], 0)
        self.assertEqual(len(self.worker.decision_calls), 9)

    def test_invalid_revision_ancestry_cannot_reset_allowance_or_accept(self):
        self.worker.decisions = ["research", "revise", "blocked"]
        self.worker.outcomes = ["partial", "achieved"]
        mid = self.submit()
        self.assertEqual(self.until(mid)["mission"]["status"], "blocked")
        first, revised = self.children(mid)
        with self.engine.lock:
            original = copy.deepcopy(self.engine._get("missions", mid))
        mutations = [
            ("cycle", first["id"], {"supersedes": revised["id"]}),
            ("other_project", first["id"], {"parent_mission_id": "f" * 32}),
            ("missing_ancestor", revised["id"], {"supersedes": "f" * 32}),
            ("different_mode", first["id"], {"execution_mode": "analysis"}),
        ]
        for name, target_id, changes in mutations:
            with self.subTest(mutation=name), self.engine.changed, self.engine.db:
                target = copy.deepcopy(first if target_id == first["id"] else revised)
                self.engine._save("missions", {**target, **changes})
                parent = copy.deepcopy(original)
                accept = decision("accept")
                accept["criteria"] = parent["workflow"]["goal_criteria"]
                with self.assertRaisesRegex(ValueError, "계보"):
                    self.engine.projects.child(parent, decision("revise", revised["id"]))
                with self.assertRaisesRegex(ValueError, "계보"):
                    self.engine.projects.accept(parent, accept)
                self.assertEqual(len(parent["workflow"]["child_ids"]), 2)
                self.assertEqual(parent["workflow"]["round"], 1)
                self.assertEqual(self.engine._get("missions", mid)["status"], "blocked")
                self.engine._save("missions", target)

    def test_invalid_decision_retries_stop_at_the_pm_limit(self):
        self.engine.projects.policy["max_decisions"] = 3
        self.worker.decisions = ["research", "accept", "accept"]
        self.worker.outcomes = ["partial"]
        mid = self.submit()
        final = self.until(mid)
        self.assertEqual(final["mission"]["status"], "blocked", final)
        self.assertIn("PM 판단 한도", final["mission"]["error"])
        self.assertEqual(len(self.worker.decision_calls), 3)
        self.assertEqual(len(self.children(mid)), 1)
        self.assertEqual([a["status"] for a in final["attempts"]], ["completed", "failed", "failed"])
        self.assertEqual(self.engine._daily_used(), 4)
        self.assertEqual(final["mission"]["workflow"]["goal_criteria"], self.worker.decision_calls[0]["goal_criteria"])

    def test_accept_rejects_changed_disk_reports_missing_pins_and_unverified_metrics(self):
        def change_report(context, output):
            attempt_id = context["sources"][0]["attempt_id"]
            path = self.engine.data_dir / "organization-runs" / attempt_id / "report.md"
            path.write_bytes(path.read_bytes() + b"\nchanged after review\n")

        def remove_pins(context, output):
            attempt_id = context["sources"][0]["attempt_id"]
            with self.engine.changed, self.engine.db:
                attempt = self.engine._get("attempts", attempt_id)
                attempt["verification"]["artifacts"] = []
                self.engine._save("attempts", attempt)

        def unverified_metric(context, output):
            output["metrics"][0]["status"] = "unverified"

        def changed_goal(context, output):
            output["criteria"] = ["Only an easy subtask"]

        for mutation in (change_report, remove_pins, unverified_metric, changed_goal):
            self.worker.decisions = ["research", "accept", "blocked"]
            self.worker.on_decision = lambda context, output: mutation(context, output) if output["action"] == "accept" else None
            with self.subTest(mutation=mutation.__name__):
                final = self.until(self.submit())
                self.assertEqual(final["mission"]["status"], "blocked", final)
                self.assertEqual(final["attempts"][1]["status"], "failed")
                self.assertNotIn("Unexpected extra PM execution", final["mission"]["error"])

    def test_accept_requires_real_search_and_network_profile_cannot_finish_with_reports(self):
        self.worker.receipt = {"enabled": True, "observed": False, "call_count": 0, "completed_count": 0}
        self.worker.decisions = ["research", "accept", "blocked"]
        first = self.until(self.submit())
        self.assertEqual(first["mission"]["status"], "blocked", first)
        self.assertEqual(first["children"][0]["status"], "blocked")
        self.worker.receipt = {"enabled": True, "observed": True, "call_count": 1, "completed_count": 1}
        self.worker.decisions = ["research", "accept", "blocked"]
        second = self.until(self.submit(project_profile="supply-network-gis-v1"))
        self.assertEqual(second["mission"]["status"], "blocked", second)
        self.assertIn("실제 개발 결과", second["attempts"][1]["error"])
        self.assertIn("실제 개발 결과", self.worker.decision_calls[-1]["decision_feedback"])


if __name__ == "__main__":
    unittest.main()
