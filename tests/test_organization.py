"""Durable organization semantics without an AI call or production data."""
import concurrent.futures
import json
import os
import sqlite3
import tempfile
import threading
import time
import unittest
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from office.organization import OrganizationEngine
from office.providers import CodeReviewer
from office.store import Conflict


class RecordingWorker:
    def __init__(self):
        self.available = True
        self.calls = []
        self.probes = 0
        self.entered = threading.Event()
        self.release = threading.Event()
        self.release.set()
        self.quota = False
        self.active = 0
        self.maximum_active = 0

    def probe(self, force=False):
        self.probes += 1
        return {"available": self.available, "message": "offline fixture"}

    def execute(self, run_dir, prompt, timeout_seconds, cancel_event, on_event=None):
        self.calls.append(prompt)
        self.active += 1
        self.maximum_active = max(self.maximum_active, self.active)
        self.entered.set()
        self.release.wait(4)
        if on_event:
            on_event({"type": "thread.started", "secret": "must never enter an event log"})
            on_event({"type": "response.output_text.delta", "delta": "ignore me"})
        if self.quota:
            self.active -= 1
            (run_dir / "events.jsonl").write_text('{"type":"error","code":"usage_limit_reached"}\n', encoding="utf-8")
            return {"completed": False, "exit_code": 1, "error": "Provider failed"}
        context = json.loads((run_dir / "input.json").read_text(encoding="utf-8"))
        criteria = context["mission"]["acceptance_criteria"]
        document = {"summary": "Offline fixture report; no business result asserted.",
                    "analysis": {"priority": "normal", "complexity": "simple", "rationale": "Fixture document.", "process": ["Write a fixture"]},
                    "outcome": {"status": "achieved", "progress_percent": 100, "basis": "Fixture criteria self-assessed met."},
                    "report": [{"title": "Fixture", "content": "Synthetic report for automated validation only."}],
                    "accomplishments": ["Prepared test fixture"], "remaining": [], "limitations": ["No actual AI or business execution."],
                    "milestones": [], "evidence": [{"criterion": c, "status": "met", "artifact_section": "report[0].content", "explanation": "Synthetic evidence."} for c in criteria]}
        (run_dir / "result.json").write_text(json.dumps(document), encoding="utf-8")
        (run_dir / "report.md").write_text("# Synthetic fixture\nNo business execution.", encoding="utf-8")
        (run_dir / "events.jsonl").write_text('{"type":"turn.completed"}\n', encoding="utf-8")
        self.active -= 1
        return {"completed": True, "exit_code": 0, "error": None}


class OrganizationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "config").mkdir()
        (self.root / "knowledge").mkdir()
        (self.root / "config/office.json").write_text(json.dumps({"daily_runs": 10, "max_attempts": 3, "timeout_seconds": 10}), encoding="utf-8")
        (self.root / "knowledge/company-charter.md").write_text("DAS Lab is first priority. Source date: 2026-09-24.", encoding="utf-8")
        (self.root / "knowledge/daslab-team.md").write_text("Standing staff and project accountability.", encoding="utf-8")
        self.env = patch.dict(os.environ, {}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.worker = RecordingWorker()
        self.engine = OrganizationEngine(self.root, self.root / "data", worker=self.worker)
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.worker.release.set()
        self.engine.close()

    def submit(self, request_id="request-1", text="간단한 내용 요약", employee_id="das-rd"):
        return self.engine.submit({"request_id": request_id, "text": text, "employee_id": employee_id})["mission"]["id"]

    def await_status(self, mission_id, states):
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            detail = self.engine.detail(mission_id)
            if detail["mission"]["status"] in states:
                return detail
            self.engine.wait_revision(detail["revision"], .02)
        self.fail(f"Mission did not reach {states}: {self.engine.detail(mission_id)}")

    def test_empty_registry_has_no_execution_and_refresh_does_not_probe(self):
        for _ in range(4):
            snapshot = self.engine.snapshot()
        self.assertEqual(len(snapshot["employees"]), 6)
        self.assertEqual(snapshot["missions"], [])
        self.assertEqual(self.worker.probes, 0)
        self.assertEqual(snapshot["metrics"]["verified_outcomes"], 0)

    def test_idempotency_is_atomic_and_rejects_changed_request(self):
        self.worker.available = False
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            ids = list(pool.map(lambda _: self.submit(), range(8)))
        self.assertEqual(len(set(ids)), 1)
        self.assertEqual(len(self.engine.snapshot()["missions"]), 1)
        with self.assertRaises(Conflict):
            self.submit(text="changed")

    def test_context_and_structural_report_do_not_become_verified_business_success(self):
        self.engine.remember("das-rd", {"text": "Use realistic 3D for industrial demonstrations.", "source": "verified", "verification": "verified"})
        first = self.submit()
        detail = self.await_status(first, {"review"})
        self.assertEqual(detail["mission"]["verification"], "structural_only")
        self.assertEqual(detail["mission"]["outcome"], "achieved")
        self.assertEqual(self.engine.snapshot()["metrics"]["verified_outcomes"], 0)
        self.assertIn("Use realistic 3D", self.worker.calls[0])
        self.assertIn("Source date: 2026-09-24", self.worker.calls[0])
        self.assertIn('"id":"das-rd"', self.worker.calls[0])
        memories = next(e for e in self.engine.snapshot()["employees"] if e["id"] == "das-rd")["memories"]
        self.assertTrue(any(m["source"] == "owner" and m["verification"] == "owner_statement" for m in memories))
        self.assertTrue(any(m["verification"] == "ai_unverified" for m in memories))
        self.await_status(self.submit("request-2"), {"review"})
        self.assertIn("ai_unverified", self.worker.calls[-1])
        events = json.dumps(self.engine.snapshot()["events"])
        self.assertNotIn("must never", events)
        self.assertNotIn("ignore me", events)

    def test_only_one_execution_runs_and_queued_cancel_never_calls_provider(self):
        self.worker.release.clear()
        first = self.submit("one")
        self.assertTrue(self.worker.entered.wait(2))
        second = self.submit("two")
        self.assertEqual(self.engine.detail(second)["mission"]["status"], "queued")
        self.engine.action(second, {"action": "cancel"})
        self.worker.release.set()
        self.await_status(first, {"review"})
        self.assertEqual(len(self.worker.calls), 1)
        self.assertEqual(self.worker.maximum_active, 1)
        self.assertEqual(self.engine.detail(second)["mission"]["status"], "cancelled")

    def test_pause_discards_late_report_and_resume_applies_instruction(self):
        self.worker.release.clear()
        mission = self.submit()
        self.assertTrue(self.worker.entered.wait(2))
        self.engine.action(mission, {"action": "instruct", "text": "Focus the next run on pump production."})
        self.assertEqual(self.engine.detail(mission)["mission"]["status"], "pausing")
        self.worker.release.set()
        paused = self.await_status(mission, {"paused"})
        self.assertIsNone(paused["mission"]["summary"])
        self.assertEqual(self.engine.snapshot()["metrics"]["reports"], 0)
        self.engine.action(mission, {"action": "resume"})
        ready = self.await_status(mission, {"review"})
        self.assertEqual(len(ready["attempts"]), 2)
        self.assertIn("Focus the next run", self.worker.calls[-1])

    def test_reassignment_keeps_prior_employee_time_and_report(self):
        mission = self.submit()
        self.await_status(mission, {"review"})
        self.engine.action(mission, {"action": "reassign", "employee_id": "das-mkt"})
        snapshot = self.engine.snapshot()
        employees = {e["id"]: e for e in snapshot["employees"]}
        self.assertEqual(employees["das-rd"]["metrics"]["reports"], 1)
        self.assertEqual(employees["das-mkt"]["metrics"]["reports"], 0)
        self.assertIsNone(self.engine.detail(mission)["mission"]["summary"])
        self.engine.action(mission, {"action": "resume"})
        self.await_status(mission, {"review"})
        self.assertIn('"id":"das-mkt"', self.worker.calls[-1])
        self.assertIn("previous_result", self.worker.calls[-1])

    def test_quota_failure_defers_batch_and_new_jobs_until_explicit_resume(self):
        self.worker.release.clear()
        self.worker.quota = True
        first = self.submit("one")
        self.assertTrue(self.worker.entered.wait(2))
        second = self.submit("two")
        self.worker.release.set()
        self.await_status(first, {"deferred"})
        self.await_status(second, {"deferred"})
        third = self.submit("three")
        self.assertEqual(self.engine.detail(third)["mission"]["status"], "deferred")
        self.assertEqual(len(self.worker.calls), 1)
        self.assertTrue(self.engine.snapshot()["execution"]["quota_blocked"])
        self.worker.quota = False
        self.engine.action(first, {"action": "resume"})
        self.await_status(first, {"review"})
        self.assertEqual(len(self.worker.calls), 2)
        self.assertEqual(self.engine.detail(second)["mission"]["status"], "deferred")

    def test_daily_cap_and_mission_attempt_cap(self):
        self.engine.config["daily_runs"] = 1
        self.await_status(self.submit("one"), {"review"})
        second = self.submit("two")
        detail = self.await_status(second, {"deferred"})
        self.assertEqual(detail["attempts"], [])
        self.assertEqual(len(self.worker.calls), 1)
        self.engine.config["max_attempts"] = 1
        first = next(m for m in self.engine.snapshot()["missions"] if m["status"] == "review")
        with self.assertRaises(Conflict):
            self.engine.action(first["id"], {"action": "resume"})

    def test_api_environment_blocks_before_probe_or_execution(self):
        with patch.dict(os.environ, {"ANTHROPIC_AUTH_TOKEN": "synthetic-test-only"}):
            mission = self.submit()
            self.await_status(mission, {"deferred"})
            self.assertEqual(self.worker.probes, 0)
            self.assertEqual(self.worker.calls, [])
            self.assertNotIn("synthetic-test-only", json.dumps(self.engine.snapshot()))

    def test_reopen_keeps_memory_and_recovers_unknown_duration_without_reexecution(self):
        self.worker.available = False
        mission_id = self.submit()
        self.await_status(mission_id, {"deferred"})
        self.engine.remember("das-rd", {"text": "Persistent instruction"})
        self.engine.close()
        with closing(sqlite3.connect(self.root / "data/organization.sqlite3")) as db, db:
            mission = json.loads(db.execute("SELECT data FROM missions WHERE id=?", (mission_id,)).fetchone()[0])
            mission["status"] = "running"
            db.execute("UPDATE missions SET data=? WHERE id=?", (json.dumps(mission), mission_id))
            attempt = {"id": "interrupted-fixture", "mission_id": mission_id, "employee_id": "das-rd", "number": 1,
                       "status": "running", "started_at": "2000-01-01T00:00:00+00:00", "ended_at": None,
                       "duration_seconds": 0, "error": None, "summary": None}
            db.execute("INSERT INTO attempts VALUES(?,?,?,?)", (attempt["id"], mission_id, attempt["started_at"], json.dumps(attempt)))
        self.worker.available = True
        self.engine = OrganizationEngine(self.root, self.root / "data", worker=self.worker)
        detail = self.engine.detail(mission_id)
        self.assertEqual(detail["mission"]["status"], "paused")
        self.assertTrue(detail["mission"]["elapsed_unknown"])
        self.assertIsNone(detail["attempts"][0]["duration_seconds"])
        self.assertEqual(detail["mission"]["elapsed_seconds"], 0)
        self.assertEqual(self.worker.calls, [])
        self.assertTrue(any(m["text"] == "Persistent instruction" for e in self.engine.snapshot()["employees"] for m in e["memories"]))

    def test_artifact_changed_after_review_is_not_released(self):
        class MutatingReviewer(CodeReviewer):
            def review(self, folder, execution, criteria, project_id):
                result = super().review(folder, execution, criteria, project_id)
                (folder / "report.md").write_text("Changed after hashing", encoding="utf-8")
                return result
        self.engine.reviewer = MutatingReviewer()
        detail = self.await_status(self.submit(), {"failed"})
        self.assertIsNone(detail["mission"]["summary"])
        self.assertIsNone(detail["mission"]["progress_percent"])
        self.assertEqual(self.engine.snapshot()["metrics"]["reports"], 0)

    def test_daily_window_uses_korean_calendar(self):
        class FixedDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                value = datetime(2026, 9, 29, 16, tzinfo=timezone.utc)
                return value.astimezone(tz) if tz else value.replace(tzinfo=None)
        with self.engine.lock, self.engine.db:
            for key, stamp in (("yesterday", "2026-09-29T14:59:00+00:00"), ("today", "2026-09-29T15:01:00+00:00")):
                self.engine.db.execute("INSERT INTO attempts VALUES(?,?,?,?)", (key, "fixture", stamp, "{}"))
            with patch("office.organization.datetime", FixedDatetime):
                self.assertEqual(self.engine._daily_used(), 1)
            self.engine.db.execute("DELETE FROM attempts WHERE mission_id='fixture'")


if __name__ == "__main__":
    unittest.main()
