"""Pre-generation provider rejection must not erase work or retry limits."""
import io
import json
import unittest
from contextlib import redirect_stderr
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from office.organization import OrganizationEngine
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


class DailyAllowanceTests(unittest.TestCase):
    setUp = organization_tests.OrganizationTests.setUp
    cleanup = organization_tests.OrganizationTests.cleanup
    submit = organization_tests.OrganizationTests.submit
    await_status = organization_tests.OrganizationTests.await_status

    def restart(self, extra_runs_today=0):
        self.engine.close()
        self.engine = OrganizationEngine(self.root, self.root / "data", worker=self.worker,
                                         extra_runs_today=extra_runs_today)

    def test_allowance_preserves_history_config_and_expires_on_restart(self):
        mission_id = self.submit()
        before = self.await_status(mission_id, {"review"})["attempts"]
        config_bytes = (self.root / "config/office.json").read_bytes()
        self.restart(6)
        self.assertEqual(self.engine.snapshot()["execution"]["daily_limit"], 16)
        self.assertEqual(self.engine.config["daily_runs"], 10)
        self.assertEqual(self.engine.config["max_attempts"], 3)
        self.assertEqual(self.engine._daily_used(), 1)
        self.assertEqual(self.engine.detail(mission_id)["attempts"], before)
        self.assertTrue(any(e["type"] == "execution.daily_allowance" for e in self.engine.snapshot()["events"]))
        self.restart()
        self.assertEqual(self.engine._daily_limit(), 10)
        self.assertEqual(self.engine._daily_used(), 1)
        self.assertEqual((self.root / "config/office.json").read_bytes(), config_bytes)

    def test_allowance_expires_at_korean_midnight_in_same_process(self):
        current = [datetime(2026, 9, 30, 14, 59, 59, tzinfo=timezone.utc)]
        with patch("office.organization.datetime", wraps=datetime) as clock:
            clock.now.side_effect = lambda tz: current[0].astimezone(tz)
            self.restart(6)
            self.assertEqual(self.engine._extra_runs_date, "2026-09-30")
            self.assertEqual(self.engine.snapshot()["execution"]["daily_limit"], 16)
            current[0] += timedelta(seconds=1)
            self.assertEqual(self.engine._daily_limit(), 10)
            self.assertEqual(self.engine.snapshot()["execution"]["daily_limit"], 10)

    def test_execution_uses_effective_limit_without_bypassing_other_limits(self):
        self.restart(6)
        self.engine.config["max_attempts"] = 1
        with patch.object(self.engine, "_daily_used", return_value=15):
            allowed = self.submit("allowed")
            self.await_status(allowed, {"review"})
        with self.assertRaises(Conflict):
            self.engine.action(allowed, {"action": "resume"})
        with patch.object(self.engine, "_daily_used", return_value=16):
            self.await_status(self.submit("at-limit"), {"deferred"})
        with self.engine.changed, self.engine.db:
            self.engine._set_quota_blocked(True)
        with patch.object(self.engine, "_daily_used", return_value=10):
            self.await_status(self.submit("provider-quota"), {"deferred"})
        self.assertTrue(self.engine._quota_blocked)
        self.assertEqual(len(self.worker.calls), 1)
        self.assertEqual(self.engine._daily_used(), 1)

    def test_allowance_rejects_invalid_values_before_opening_storage(self):
        for value in (-1, 7, True, 1.5, "6", None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                OrganizationEngine(self.root, self.root / "unused", extra_runs_today=value)
        self.assertFalse((self.root / "unused").exists())

    def test_cli_rejects_nonpositive_or_unbounded_allowance(self):
        import server
        for value in ("0", "-1", "7", "1.5"):
            with self.subTest(value=value), patch("sys.argv", ["server.py", "--extra-runs-today", value]), \
                    patch("server.InstanceLock") as instance_lock, redirect_stderr(io.StringIO()), \
                    self.assertRaises(SystemExit) as failure:
                server.main()
            self.assertEqual(failure.exception.code, 2)
            instance_lock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
