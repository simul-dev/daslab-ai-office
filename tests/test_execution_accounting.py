"""Pre-generation provider rejection must not erase work or retry limits."""
import json
import unittest

from office.store import Conflict
import tests.test_organization as organization_tests


class RejectedRequestWorker(organization_tests.RecordingWorker):
    def __init__(self, generated=False, error_code="invalid_json_schema"):
        super().__init__()
        self.generated = generated
        self.error_code = error_code
        self.reject = True

    def execute(self, run_dir, prompt, timeout_seconds, cancel_event, on_event=None):
        if not self.reject:
            return super().execute(run_dir, prompt, timeout_seconds, cancel_event, on_event)
        self.calls.append(prompt)
        events = [{"type": "thread.started"}, {"type": "turn.started"}]
        if self.generated:
            events.append({"type": "item.completed", "item": {"type": "agent_message", "text": "Work occurred."}})
        message = json.dumps({"error": {"type": "invalid_request_error", "code": self.error_code}})
        events.extend([{"type": "error", "message": message}, {"type": "turn.failed"}])
        (run_dir / "events.jsonl").write_text("\n".join(json.dumps(event) for event in events), encoding="utf-8")
        return {"completed": False, "exit_code": 1, "error": "Fixture provider rejection"}


class ExecutionAccountingTests(unittest.TestCase):
    setUp = organization_tests.OrganizationTests.setUp
    cleanup = organization_tests.OrganizationTests.cleanup
    submit = organization_tests.OrganizationTests.submit
    await_status = organization_tests.OrganizationTests.await_status

    def replace_worker(self, **kwargs):
        self.worker = RejectedRequestWorker(**kwargs)
        self.engine.worker = self.worker
        self.engine.config["daily_runs"] = 1

    def test_only_pre_generation_schema_rejection_is_excluded_and_history_survives(self):
        self.replace_worker()
        mission_id = self.submit()
        detail = self.await_status(mission_id, {"failed"})
        self.assertTrue(detail["attempts"][0]["request_rejected"])
        self.assertEqual(self.engine._daily_used(), 0)
        # Existing failures from before this accounting fix are rechecked from raw events.
        with self.engine.changed, self.engine.db:
            attempt = self.engine._attempts(mission_id)[0]
            attempt.pop("request_rejected")
            self.engine._save("attempts", attempt)
        self.assertEqual(self.engine._daily_used(), 1)
        self.worker.reject = False
        self.engine.action(mission_id, {"action": "resume"})
        detail = self.await_status(mission_id, {"review"})
        self.assertEqual([a["status"] for a in detail["attempts"]], ["failed", "completed"])
        self.assertTrue(detail["attempts"][0]["request_rejected"])
        self.assertEqual(self.engine._daily_used(), 1)
        self.engine.config["max_attempts"] = 2
        with self.assertRaises(Conflict):
            self.engine.action(mission_id, {"action": "resume"})

    def test_schema_error_after_generation_still_consumes_daily_allowance(self):
        self.replace_worker(generated=True)
        mission_id = self.submit()
        detail = self.await_status(mission_id, {"failed"})
        self.assertFalse(detail["attempts"][0].get("request_rejected", False))
        self.assertEqual(self.engine._daily_used(), 1)
        self.worker.reject = False
        self.engine.action(mission_id, {"action": "resume"})
        self.await_status(mission_id, {"deferred"})
        self.assertEqual(len(self.worker.calls), 1)

    def test_other_provider_failure_still_consumes_daily_allowance(self):
        self.replace_worker(error_code="invalid_request_error")
        mission_id = self.submit()
        detail = self.await_status(mission_id, {"failed"})
        self.assertFalse(detail["attempts"][0].get("request_rejected", False))
        self.assertEqual(self.engine._daily_used(), 1)


if __name__ == "__main__":
    unittest.main()
