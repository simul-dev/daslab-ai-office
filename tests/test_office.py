"""Domain regression checks; all data is temporary and no AI is invoked."""
import copy
import hashlib
import http.client
import json
import shutil
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from office.planner import CodePlanner
from office.service import Office
from office.store import Conflict, Store, ident, now


METADATA = {"source", "created_at", "project_id", "verification_status"}


class OfficeStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "office.sqlite3"
        self.store = Store(self.path)
        self.config = {"max_attempts": 3, "daily_runs": 10, "worker_provider": "fake"}

    def task(self, title="Example task"):
        task = {
            "id": ident(), "title": title, "project_id": "franchise",
            "goal": "Choose one first MVP feature.",
            "inputs": "The owner described time spent comparing quotations.",
            "acceptance_criteria": ["Separate reported problems and assumptions.", "Choose one MVP."],
            "priority": "high", "status": "queued", "created_at": now(),
            "updated_at": now(), "error": None, "source": "owner",
            "verification_status": "pending",
        }
        task["pm_spec"] = CodePlanner().plan(task, {"project": {"name": "Franchise"}})
        return self.store.create(task)["task"]

    def claim(self, task, store=None):
        return (store or self.store).claim(task["id"], self.config, self.root / "runs")[1]

    def finish(self, task, run, status="review"):
        self.store.finish(
            task["id"], run["id"], status,
            "Worker exited unsuccessfully." if status == "failed" else None,
            {"passed": status == "review", "source": "code_reviewer", "created_at": now(),
             "project_id": task["project_id"], "verification_status": "basic_checks"},
            [{"name": "report.md", "sha256": "a" * 64, "size": 120}],
        )

    def test_planner_preserves_owner_requirements_and_provenance(self):
        task = self.task()
        plan = task["pm_spec"]
        for field in ("goal", "inputs", "acceptance_criteria", "project_id"):
            self.assertEqual(plan[field], task[field])
        self.assertEqual(plan["source"], "task:" + task["id"])
        self.assertEqual(plan["provider"], "code")
        self.assertTrue(plan["steps"])
        self.assertTrue(METADATA <= plan.keys())

    def test_creation_and_records_survive_new_store_instance(self):
        task = self.task()
        run = self.claim(task)
        self.finish(task, run)
        before = self.store.detail(task["id"])
        after = Store(self.path).detail(task["id"])
        self.assertEqual(after, before)
        self.assertEqual(after["task"]["status"], "review")
        self.assertEqual(after["runs"][0]["artifacts"][0]["name"], "report.md")
        self.assertTrue(all(METADATA <= event.keys() for event in after["events"]))
        self.assertTrue(METADATA <= after["runs"][0].keys())

    def test_restart_marks_interrupted_work_failed_without_automatic_retry(self):
        task = self.task()
        run = self.claim(task)
        run_dir = Path(run["run_dir"])
        run_dir.mkdir(parents=True)
        evidence = run_dir / "events.jsonl"
        evidence.write_text('{"type":"partial"}\n', encoding="utf-8")
        restarted = Store(self.path)
        restarted.recover()
        detail = restarted.detail(task["id"])
        self.assertEqual(detail["task"]["status"], "failed")
        self.assertEqual(detail["runs"][0]["status"], "failed")
        self.assertEqual(detail["runs"][0]["verification_status"], "interrupted")
        self.assertTrue(detail["task"]["error"])
        self.assertEqual(restarted.usage()["active_runs"], 0)
        self.assertEqual(evidence.read_text(encoding="utf-8"), '{"type":"partial"}\n')
        restarted.recover()
        self.assertEqual(len(restarted.detail(task["id"])["runs"]), 1)
        self.assertEqual(len(restarted.detail(task["id"])["events"]), len(detail["events"]))
        retry = self.claim(task, restarted)
        self.assertEqual(retry["attempt"], 2)
        self.assertNotEqual(retry["run_dir"], run["run_dir"])

    def test_claim_is_atomic_across_store_connections_and_allows_only_one_worker(self):
        tasks = [self.task("A"), self.task("B")]
        barrier = threading.Barrier(2)
        outcomes = []

        def attempt(task):
            store = Store(self.path)
            barrier.wait(timeout=5)
            try:
                outcomes.append(("running", self.claim(task, store)))
            except Conflict as exc:
                outcomes.append(("conflict", str(exc)))

        threads = [threading.Thread(target=attempt, args=(task,)) for task in tasks]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
            self.assertFalse(thread.is_alive())
        self.assertCountEqual([item[0] for item in outcomes], ["running", "conflict"])
        self.assertEqual(self.store.usage()["active_runs"], 1)
        self.assertEqual(sum(len(self.store.detail(task["id"])["runs"]) for task in tasks), 1)

    def test_attempt_limit_counts_failed_runs_and_does_not_add_rejected_attempt(self):
        task = self.task()
        self.config["max_attempts"] = 2
        for attempt in (1, 2):
            run = self.claim(task)
            self.assertEqual(run["attempt"], attempt)
            self.finish(task, run, "failed")
        with self.assertRaises(Conflict):
            self.claim(task)
        self.assertEqual(len(self.store.detail(task["id"])["runs"]), 2)
        self.assertEqual(self.store.detail(task["id"])["task"]["status"], "failed")

    def test_daily_limit_applies_across_tasks(self):
        self.config["daily_runs"] = 1
        first, second = self.task("A"), self.task("B")
        self.finish(first, self.claim(first), "failed")
        with self.assertRaises(Conflict):
            self.claim(second)
        self.assertEqual(self.store.detail(second["id"])["task"]["status"], "queued")
        self.assertEqual(self.store.detail(second["id"])["runs"], [])

    def test_daily_usage_rolls_over_at_korean_midnight(self):
        class FixedClock(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime(2026, 9, 24, 16, tzinfo=timezone.utc).astimezone(tz)

        task = self.task()
        for started_at in ("2026-09-24T14:59:59+00:00", "2026-09-24T15:00:00+00:00"):
            run = self.claim(task)
            self.finish(task, run, "failed")
            with self.store.connect() as db:
                saved = self.store.read(db, "runs", run["id"])
                saved["started_at"] = started_at
                self.store.save(db, "runs", saved)
        with patch("office.store.datetime", FixedClock):
            self.assertEqual(self.store.usage(), {"runs_today": 1, "active_runs": 0})

    def test_approval_requires_review_and_human_decision_is_persistent(self):
        task = self.task()
        with self.assertRaises(Conflict):
            self.store.review(task["id"], "approve", "")
        run = self.claim(task)
        with self.assertRaises(Conflict):
            self.store.review(task["id"], "approve", "")
        self.finish(task, run)
        self.assertEqual(self.store.detail(task["id"])["task"]["status"], "review")
        self.store.review(task["id"], "approve", "Read report and criterion evidence.")
        detail = Store(self.path).detail(task["id"])
        self.assertEqual(detail["task"]["status"], "completed")
        self.assertEqual(detail["task"]["verification_status"], "human_approved")
        self.assertEqual(detail["decisions"][0]["source"], "owner")
        self.assertTrue(METADATA <= detail["decisions"][0].keys())
        with self.assertRaises(Conflict):
            self.store.review(task["id"], "approve", "Again")
        with self.assertRaises(Conflict):
            self.claim(task)

    def test_rejection_requires_explicit_retry_and_preserves_prior_evidence(self):
        task = self.task()
        first = self.claim(task)
        self.finish(task, first)
        evidence = copy.deepcopy(self.store.detail(task["id"])["runs"][0])
        self.store.review(task["id"], "reject", "Clarify customer validation criteria.")
        rejected = self.store.detail(task["id"])
        self.assertEqual(rejected["task"]["status"], "failed")
        self.assertEqual(len(rejected["runs"]), 1)
        self.assertEqual(rejected["runs"][0], evidence)
        self.assertIn("Clarify", rejected["task"]["error"])
        retry = self.claim(task)
        self.assertEqual(retry["attempt"], 2)
        detail = self.store.detail(task["id"])
        self.assertEqual(detail["runs"][0], evidence)
        self.assertEqual(detail["decisions"][0]["decision"], "reject")
        self.assertNotEqual(first["run_dir"], retry["run_dir"])

    def test_review_result_cannot_be_reexecuted_without_rejection(self):
        task = self.task()
        self.finish(task, self.claim(task))
        with self.assertRaises(Conflict):
            self.claim(task)
        self.assertEqual(len(self.store.detail(task["id"])["runs"]), 1)

    def test_idle_cancellation_is_persistent_and_retry_is_explicit(self):
        task = self.task()
        self.store.cancel_idle(task["id"])
        detail = Store(self.path).detail(task["id"])
        self.assertEqual(detail["task"]["status"], "cancelled")
        self.assertEqual(detail["runs"], [])
        run = self.claim(task)
        self.assertEqual(run["attempt"], 1)
        with self.assertRaises(Conflict):
            self.store.cancel_idle(task["id"])

    def test_invalid_review_decision_does_not_mutate_task(self):
        task = self.task()
        self.finish(task, self.claim(task))
        with self.assertRaises(ValueError):
            self.store.review(task["id"], "not-a-decision", "")
        self.assertEqual(self.store.detail(task["id"])["task"]["status"], "review")
        self.assertEqual(self.store.detail(task["id"])["decisions"], [])

    def test_finish_rejects_wrong_task_and_stale_completion(self):
        task, other = self.task(), self.task("Other")
        run = self.claim(task)
        with self.assertRaises(Conflict):
            self.store.finish(other["id"], run["id"], "review", None, {}, [])
        with self.assertRaises(Conflict):
            self.store.finish(task["id"], run["id"], "completed", None, {}, [])
        self.finish(task, run, "failed")
        with self.assertRaises(Conflict):
            self.finish(task, run)
        self.assertEqual(self.store.detail(task["id"])["task"]["status"], "failed")


class RecordingPlanner(CodePlanner):
    provider = "fake_planner"

    def __init__(self):
        self.calls = []

    def plan(self, task, context):
        self.calls.append((copy.deepcopy(task), copy.deepcopy(context)))
        return super().plan(task, context)


class FakeWorker:
    """An explicit test double, never used by the runnable application."""

    def __init__(self):
        self.calls = []
        self.available = True
        self.block = False
        self.fail = False

    def probe(self):
        return {"provider": "fake", "available": self.available, "auth_mode": "test-only",
                "message": "Test worker availability"}

    def execute(self, run_dir, prompt, timeout_seconds, cancel_event):
        self.calls.append((run_dir, prompt, timeout_seconds))
        if self.fail:
            raise RuntimeError("sensitive exception value must not be exposed")
        if self.block:
            cancel_event.wait(timeout=5)
            return {"exit_code": -1, "cancelled": cancel_event.is_set()}
        (run_dir / "prompt.txt").write_text(prompt, encoding="utf-8")
        (run_dir / "result.json").write_text('{"summary":"Test report only"}', encoding="utf-8")
        (run_dir / "report.md").write_text("# Test report\nFixture content, not real AI output.\n", encoding="utf-8")
        return {"exit_code": 0, "completed": True}


class FakeReviewer:
    provider = "fake_reviewer"

    def __init__(self):
        self.calls = []
        self.passed = True

    def review(self, run_dir, execution, criteria, project_id):
        self.calls.append((run_dir, execution, criteria, project_id))
        return {"passed": self.passed and (run_dir / "report.md").is_file(),
                "checks": [{"name": "test fixture file exists", "passed": (run_dir / "report.md").is_file()}],
                "source": "fake_reviewer", "created_at": now(), "project_id": project_id,
                "verification_status": "test_only"}


class OfficeServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        source_root = Path(__file__).resolve().parents[1]
        for relative in ("config/office.json", "config/roles.json", "knowledge/projects.json"):
            destination = self.root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_root / relative, destination)
        self.payload = json.loads((source_root / "examples/franchise-task.json").read_text(encoding="utf-8"))
        self.planner, self.worker, self.reviewer = RecordingPlanner(), FakeWorker(), FakeReviewer()
        self.office = Office(self.root, self.root / "data", (self.planner, self.worker, self.reviewer))
        self.office.config["worker_provider"] = "fake"
        self.addCleanup(self.office.close)

    def create(self):
        return self.office.create(self.payload)["task"]

    def wait_run(self, task):
        active = self.office.active.get(task["id"])
        if active:
            active[1].join(timeout=5)
            self.assertFalse(active[1].is_alive(), "Fake worker did not finish")
        detail = self.office.store.detail(task["id"])
        self.assertNotEqual(detail["task"]["status"], "running")
        return detail

    def test_injected_provider_workflow_writes_evidence_and_stops_for_review(self):
        task = self.create()
        self.assertEqual(task["status"], "queued")
        self.assertEqual(task["pm_spec"]["provider"], "fake_planner")
        self.assertEqual(len(self.planner.calls), 1)
        self.assertEqual(self.planner.calls[0][1]["project"]["id"], task["project_id"])
        started = self.office.run(task["id"])
        detail = self.wait_run(task)
        self.assertEqual(detail["task"]["status"], "review")
        self.assertEqual(detail["decisions"], [])
        self.assertEqual(len(self.worker.calls), 1)
        self.assertEqual(len(self.reviewer.calls), 1)
        run = detail["runs"][0]
        self.assertEqual(run["id"], started["run_id"])
        self.assertEqual(run["provider"], "fake")
        self.assertTrue(run["verification"]["passed"])
        artifacts = {item["name"]: item for item in run["artifacts"]}
        self.assertTrue({"report.md", "result.json", "input.json", "plan.json", "execution.json", "verification.json"} <= artifacts.keys())
        for artifact in artifacts.values():
            raw = self.office.file(run["id"], artifact["name"]).read_bytes()
            self.assertEqual(hashlib.sha256(raw).hexdigest(), artifact["sha256"])
            self.assertEqual(len(raw), artifact["size"])
            self.assertTrue(METADATA <= artifact.keys())
        approved = self.office.review(task["id"], {"decision": "approve", "note": "Verified test fixture."})
        self.assertEqual(approved["task"]["status"], "completed")

    def test_modified_artifact_blocks_approval(self):
        task = self.create()
        self.office.run(task["id"])
        detail = self.wait_run(task)
        self.office.file(detail["runs"][0]["id"], "report.md").write_text("changed after verification", encoding="utf-8")
        with self.assertRaises(Conflict):
            self.office.review(task["id"], {"decision": "approve", "note": "Approve"})
        self.assertEqual(self.office.store.detail(task["id"])["task"]["status"], "review")
        self.assertEqual(self.office.store.detail(task["id"])["decisions"], [])
        with self.assertRaises(KeyError):
            self.office.file(detail["runs"][0]["id"], "../../config/office.json")

    def test_failed_review_cannot_be_approved_despite_successful_worker(self):
        self.reviewer.passed = False
        task = self.create()
        self.office.run(task["id"])
        detail = self.wait_run(task)
        self.assertEqual(detail["task"]["status"], "failed")
        self.assertFalse(detail["runs"][0]["verification"]["passed"])
        with self.assertRaises(Conflict):
            self.office.review(task["id"], {"decision": "approve", "note": "Worker says done"})

    def test_unavailable_provider_creates_no_run_and_exposes_no_fake_success(self):
        self.worker.available = False
        task = self.create()
        with self.assertRaises(Conflict):
            self.office.run(task["id"])
        self.assertEqual(self.office.store.detail(task["id"])["runs"], [])
        self.assertEqual(self.worker.calls, [])

    def test_worker_exception_is_recorded_without_disclosing_exception_contents(self):
        self.worker.fail = True
        task = self.create()
        self.office.run(task["id"])
        detail = self.wait_run(task)
        self.assertEqual(detail["task"]["status"], "failed")
        self.assertIn("RuntimeError", detail["task"]["error"])
        self.assertNotIn("sensitive", json.dumps(detail))
        self.assertEqual(self.office.store.usage()["active_runs"], 0)

    def test_running_cancel_stops_worker_and_releases_slot(self):
        self.worker.block = True
        task = self.create()
        self.office.run(task["id"])
        self.office.cancel(task["id"])
        detail = self.wait_run(task)
        self.assertEqual(detail["task"]["status"], "cancelled")
        self.assertEqual(self.office.store.usage()["active_runs"], 0)
        self.assertEqual(self.reviewer.calls, [])

    def test_cancellation_during_reviewer_does_not_advance_to_review(self):
        entered, release = threading.Event(), threading.Event()
        original = self.reviewer.review

        def slow_review(*args):
            entered.set()
            release.wait(timeout=3)
            return original(*args)

        task = self.create()
        with patch.object(self.reviewer, "review", side_effect=slow_review):
            self.office.run(task["id"])
            try:
                self.assertTrue(entered.wait(timeout=3))
                self.office.cancel(task["id"])
            finally:
                release.set()
            detail = self.wait_run(task)
        self.assertEqual(detail["task"]["status"], "cancelled")
        self.assertEqual(self.office.store.usage()["active_runs"], 0)

    def test_unreadable_artifact_records_failure_and_releases_worker_slot(self):
        task = self.create()
        original = Path.read_bytes

        def read_bytes(path):
            if path.name == "report.md":
                raise PermissionError("sensitive locked path")
            return original(path)

        with patch.object(Path, "read_bytes", read_bytes):
            self.office.run(task["id"])
            detail = self.wait_run(task)
        self.assertEqual(detail["task"]["status"], "failed")
        self.assertTrue(detail["task"]["error"])
        self.assertNotIn("sensitive", json.dumps(detail))
        self.assertEqual(self.office.store.usage()["active_runs"], 0)
        with self.assertRaises(Conflict):
            self.office.review(task["id"], {"decision": "approve", "note": "Cannot approve missing evidence"})

    def test_restart_can_recover_when_an_interrupted_artifact_is_unreadable(self):
        task = self.create()
        _, run = self.office.store.claim(task["id"], self.office.config, self.office.data_dir / "runs")
        folder = Path(run["run_dir"])
        folder.mkdir(parents=True)
        (folder / "events.jsonl").write_text('{"type":"partial"}\n', encoding="utf-8")
        original = Path.read_bytes

        def read_bytes(path):
            if path.name == "events.jsonl":
                raise PermissionError("sensitive locked path")
            return original(path)

        with patch.object(Path, "read_bytes", read_bytes):
            recovered = Office(self.root, self.office.data_dir, (self.planner, self.worker, self.reviewer))
        self.addCleanup(recovered.close)
        detail = recovered.store.detail(task["id"])
        self.assertEqual(detail["task"]["status"], "failed")
        self.assertEqual(detail["runs"][0]["verification_status"], "interrupted")
        self.assertEqual(recovered.store.usage()["active_runs"], 0)

    def test_previous_run_cleanup_cannot_remove_retry_cancellation_handle(self):
        saved, release = threading.Event(), threading.Event()
        original = self.office.store.finish

        def delayed_finish(*args):
            original(*args)
            if args[2] == "failed":
                saved.set()
                release.wait(timeout=3)

        task = self.create()
        self.worker.fail = True
        with patch.object(self.office.store, "finish", side_effect=delayed_finish):
            self.office.run(task["id"])
            try:
                self.assertTrue(saved.wait(timeout=3))
                previous = self.office.active[task["id"]][1]
                self.worker.fail, self.worker.block = False, True
                self.office.run(task["id"])
            finally:
                release.set()
            previous.join(timeout=3)
            self.assertIn(task["id"], self.office.active)
            self.office.cancel(task["id"])
            detail = self.wait_run(task)
        self.assertEqual(detail["task"]["status"], "cancelled")
        self.assertEqual(len(detail["runs"]), 2)

    def test_retry_prompt_contains_owner_rejection_and_previous_run_stays_intact(self):
        task = self.create()
        self.office.run(task["id"])
        detail = self.wait_run(task)
        original_run = copy.deepcopy(detail["runs"][0])
        rejection = "Need explicit field-level quotation comparison criteria."
        self.office.review(task["id"], {"decision": "reject", "note": rejection})
        self.office.run(task["id"])
        detail = self.wait_run(task)
        self.assertEqual(detail["task"]["status"], "review")
        self.assertIn(rejection, self.worker.calls[-1][1])
        self.assertEqual(detail["runs"][0], original_run)
        snapshot = json.loads(self.office.file(detail["runs"][-1]["id"], "input.json").read_text(encoding="utf-8"))
        self.assertEqual(snapshot["review_decisions"][0]["note"], rejection)

    def test_invalid_inputs_are_rejected_before_planning_or_persistence(self):
        bad_payloads = [None, [], {}, {**self.payload, "goal": " "},
                        {**self.payload, "project_id": "missing"},
                        {**self.payload, "priority": "urgent"},
                        {**self.payload, "acceptance_criteria": []},
                        {**self.payload, "acceptance_criteria": ["same", " same "]},
                        {**self.payload, "acceptance_criteria": [42]}]
        for payload in bad_payloads:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                self.office.create(payload)
        self.assertEqual(self.planner.calls, [])
        self.assertEqual(self.office.store.tasks(), [])

    def test_http_rejects_external_host_origin_and_unprotected_or_invalid_post(self):
        from http.server import ThreadingHTTPServer
        from server import handler_for

        server = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(self.office))
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()

        def request(method, path, body=None, headers=None):
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
            try:
                connection.request(method, path, body=body, headers=headers or {})
                response = connection.getresponse()
                return response.status, json.loads(response.read())
            finally:
                connection.close()

        try:
            protected = {"Content-Type": "application/json", "X-DAS-Office": "1"}
            cases = [
                ("GET", "/api/health", None, {"Host": "attacker.example"}, 403),
                ("GET", "/api/health", None, {"Origin": "https://attacker.example"}, 403),
                ("POST", "/api/tasks", "{}", {"Content-Type": "application/json"}, 403),
                ("POST", "/api/tasks", "{}", {"Content-Type": "text/plain", "X-DAS-Office": "1"}, 403),
                ("POST", "/api/tasks", "[]", protected, 400),
                ("POST", "/api/tasks", "{invalid", protected, 400),
            ]
            for method, path, body, headers, expected in cases:
                with self.subTest(headers=headers, body=body):
                    status, content = request(method, path, body, headers)
                    self.assertEqual(status, expected)
                    self.assertIn("error", content)
            self.assertEqual(self.office.store.tasks(), [])
            status, content = request("POST", "/api/tasks", json.dumps(self.payload), protected)
            self.assertEqual(status, 201)
            self.assertEqual(content["task"]["status"], "queued")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
