"""Real HTTP integration against an isolated database and injected fake worker.

These tests never invoke a model, read production data, or establish business success.
"""
import http.client
import json
import shutil
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock

from office.organization import OrganizationEngine
from server import handler_for


class ControlledWorker:
    """Offline provider whose completion can intentionally arrive after cancellation."""

    def __init__(self):
        self.available = False
        self.calls = []
        self.entered = threading.Event()
        self.release = threading.Event()
        self.cancel_seen = threading.Event()

    def probe(self, force=False):
        return {"available": self.available, "auth_mode": "chatgpt", "version": "offline-test",
                "message": "Offline injected worker; no actual AI connection."}

    def execute(self, run_dir, prompt, timeout_seconds, cancel_event):
        self.calls.append((run_dir, prompt))
        self.entered.set()
        deadline = time.monotonic() + 5
        while not self.release.wait(0.005):
            if cancel_event.is_set():
                self.cancel_seen.set()
            if time.monotonic() >= deadline:
                raise RuntimeError("Controlled test worker was not released")
        if cancel_event.is_set():
            self.cancel_seen.set()
        # Deliberately return after cancellation: the engine must discard this result.
        return {"completed": True, "exit_code": 0, "error": None}


class OrganizationHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "project"
        self.data = self.root / "data"
        source = Path(__file__).resolve().parents[1]
        for folder in ("knowledge", "config"):
            shutil.copytree(source / folder, self.root / folder)
        self.worker = ControlledWorker()
        self.organization = OrganizationEngine(self.root, self.data, worker=self.worker)
        self.legacy = Mock()
        self.start_server()
        self.addCleanup(self.cleanup_runtime)

    def start_server(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(self.legacy, self.organization))
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={"poll_interval": 0.01}, daemon=True)
        self.thread.start()

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.assertFalse(self.thread.is_alive())

    def cleanup_runtime(self):
        self.worker.release.set()
        self.stop_server()
        self.organization.close()

    def request(self, path, method="GET", body=None, headers=None, raw=False):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=4)
        request_headers = {}
        if method == "POST":
            request_headers = {"Content-Type": "application/json", "X-DAS-Office": "1"}
        request_headers.update(headers or {})
        if body is not None and not raw:
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        try:
            connection.request(method, path, body=body, headers=request_headers)
            response = connection.getresponse()
            payload = response.read()
            return response.status, dict(response.getheaders()), json.loads(payload)
        finally:
            connection.close()

    def snapshot(self):
        status, _, result = self.request("/api/org")
        self.assertEqual(status, 200, result)
        return result

    def create(self, request_id="test-request", text="공급망 데모의 개선 방향을 요약해 줘", employee_id="assistant"):
        status, _, result = self.request("/api/org/missions", "POST",
                                        {"request_id": request_id, "text": text, "employee_id": employee_id})
        self.assertIn(status, (200, 201), result)
        return result

    def detail(self, mission_id):
        status, _, result = self.request(f"/api/org/missions/{mission_id}")
        self.assertEqual(status, 200, result)
        return result

    def action(self, mission_id, action, **extra):
        status, _, result = self.request(f"/api/org/missions/{mission_id}/action", "POST",
                                        {"action": action, **extra})
        self.assertEqual(status, 200, result)
        return result

    def until(self, callback, message):
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            result = callback()
            if result:
                return result
            time.sleep(0.01)
        self.fail(message)

    def test_snapshot_connects_persistent_staff_and_exposes_no_invented_results(self):
        snapshot = self.snapshot()
        staff = {employee["id"]: employee for employee in snapshot["employees"]}
        self.assertEqual(set(staff), {"owner", "assistant", "das-pm", "das-rd", "das-mkt", "das-sales"})
        for employee_id, parent_id in (("assistant", "owner"), ("das-pm", "owner"),
                                       ("das-rd", "das-pm"), ("das-mkt", "das-pm"), ("das-sales", "das-pm")):
            self.assertEqual(staff[employee_id]["parent_id"], parent_id)
        self.assertTrue(snapshot["standing"]["enabled"])
        self.assertEqual(snapshot["primary_contact_id"], "das-pm")
        self.assertTrue(staff["assistant"]["reserved"])
        self.assertGreater(staff["assistant"]["memory_count"], 0)
        self.assertEqual(snapshot["missions"], [])
        self.assertEqual(snapshot["metrics"]["verified_outcomes"], 0)
        self.assertIsNone(snapshot["execution"]["remaining_quota"])
        self.assertFalse(snapshot["execution"]["paid_fallback"])
        self.assertFalse(snapshot["execution"]["recurring_enabled"])
        self.assertFalse(snapshot["execution"]["temporary_agents_enabled"])
        self.assertEqual(self.worker.calls, [])
        self.legacy.submit_mission.assert_not_called()

    def test_request_replay_is_idempotent_and_payload_conflict_is_rejected(self):
        first = self.create()
        replay = self.create()
        self.assertFalse(first["duplicate"])
        self.assertTrue(replay["duplicate"])
        self.assertEqual(first["mission"]["id"], replay["mission"]["id"])
        status, _, _ = self.request("/api/org/missions", "POST",
                                    {"request_id": "test-request", "text": "다른 미션을 수행해 줘"})
        self.assertEqual(status, 409)
        self.assertEqual(len(self.snapshot()["missions"]), 1)
        self.assertEqual(self.worker.calls, [])

    def test_concurrent_identical_posts_cannot_create_two_missions(self):
        barrier = threading.Barrier(2)
        results = []
        errors = []

        def submit():
            try:
                barrier.wait(timeout=3)
                results.append(self.create(request_id="simultaneous"))
            except BaseException as exc:
                errors.append(exc)

        threads = [threading.Thread(target=submit) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 2)
        self.assertEqual(len({item["mission"]["id"] for item in results}), 1)
        self.assertCountEqual([item["duplicate"] for item in results], [True, False])

    def test_unavailable_provider_has_reason_and_no_attempt_or_percentage(self):
        mission_id = self.create()["mission"]["id"]
        detail = self.until(lambda: (d if (d := self.detail(mission_id))["mission"]["status"] == "deferred" else None),
                            "Unavailable provider did not defer the mission")
        self.assertTrue(detail["mission"]["error"])
        self.assertEqual(detail["attempts"], [])
        self.assertIsNone(detail["mission"]["progress_percent"])
        self.assertEqual(detail["mission"]["outcome"], "unknown")
        self.assertEqual(self.snapshot()["execution"]["daily_used"], 0)

    def test_owner_interventions_and_employee_memory_survive_restart(self):
        mission_id = self.create()["mission"]["id"]
        self.action(mission_id, "instruct", text="실제 배포 대신 개선안 초안부터 정리해 줘")
        changed = self.action(mission_id, "reassign", employee_id="das-mkt")
        self.assertEqual(changed["mission"]["employee_id"], "das-mkt")
        self.assertEqual(changed["mission"]["status"], "paused")
        status, _, saved = self.request("/api/org/employees/das-mkt/memory", "POST",
                                        {"text": "과장된 성과 표현을 사용하지 않는다.", "source": "trusted_external",
                                         "verification": "independently_verified"})
        self.assertEqual(status, 201, saved)
        self.assertEqual(saved["memory"]["source"], "owner")
        self.assertEqual(saved["memory"]["verification"], "owner_statement")
        before = self.detail(mission_id)
        self.stop_server()
        self.organization.close()
        self.organization = OrganizationEngine(self.root, self.data, worker=self.worker)
        self.start_server()
        after = self.detail(mission_id)
        self.assertEqual(after["mission"], before["mission"])
        self.assertEqual(after["attempts"], before["attempts"])
        staff = {employee["id"]: employee for employee in self.snapshot()["employees"]}
        self.assertIn(saved["memory"], staff["das-mkt"]["memories"])
        self.assertEqual(after["mission"]["intervention_count"], 2)
        self.assertEqual(self.worker.calls, [])

    def test_pause_acknowledgement_waits_for_worker_and_discards_late_completion(self):
        self.worker.available = True
        mission_id = self.create()["mission"]["id"]
        self.assertTrue(self.worker.entered.wait(timeout=3))
        pausing = self.action(mission_id, "pause")
        self.assertEqual(pausing["mission"]["status"], "pausing")
        self.assertTrue(self.worker.cancel_seen.wait(timeout=3))
        self.assertEqual(self.snapshot()["execution"]["active_count"], 1)
        self.worker.release.set()
        paused = self.until(lambda: (d if (d := self.detail(mission_id))["mission"]["status"] == "paused" else None),
                            "Mission was not paused after worker returned")
        self.assertEqual(paused["mission"]["outcome"], "unknown")
        self.assertIsNone(paused["mission"]["progress_percent"])
        self.assertFalse(paused["mission"]["report"])
        self.assertEqual(self.snapshot()["metrics"]["reports"], 0)
        self.assertEqual(self.snapshot()["metrics"]["verified_outcomes"], 0)

    def test_reassign_keeps_old_attempt_identity_and_requires_explicit_resume(self):
        self.worker.available = True
        mission_id = self.create(employee_id="das-rd")["mission"]["id"]
        self.assertTrue(self.worker.entered.wait(timeout=3))
        self.until(lambda: self.detail(mission_id)["mission"]["elapsed_seconds"] >= 0.1,
                   "Controlled execution did not accumulate measurable time")
        self.action(mission_id, "reassign", employee_id="das-mkt")
        during = {item["id"]: item for item in self.snapshot()["employees"]}
        self.assertIn(during["das-rd"]["status"], ("running", "pausing"),
                      "Original employee must remain visible until its old execution stops")
        self.worker.release.set()
        changed = self.until(lambda: (d if (d := self.detail(mission_id))["mission"]["status"] == "paused" else None),
                             "Reassigned mission was not held for a fresh attempt")
        self.assertEqual(changed["mission"]["employee_id"], "das-mkt")
        self.assertEqual(changed["attempts"][0]["employee_id"], "das-rd")
        self.assertEqual(len(self.worker.calls), 1)
        self.assertEqual(changed["mission"]["attempts_count"], 1)
        staff = {item["id"]: item for item in self.snapshot()["employees"]}
        self.assertEqual(staff["das-rd"]["metrics"]["execution_seconds"],
                         round(changed["attempts"][0]["duration_seconds"], 1))
        self.assertEqual(staff["das-mkt"]["metrics"]["execution_seconds"], 0)

    def test_structured_fixture_report_never_becomes_verified_business_success(self):
        from tests.org_fixture_server import FixtureWorker

        fixture = FixtureWorker(delay=0)
        self.worker.available = True
        self.worker.execute = fixture.execute
        mission_id = self.create(employee_id="das-rd")["mission"]["id"]
        detail = self.until(lambda: (d if (d := self.detail(mission_id))["mission"]["status"] == "blocked" else None),
                            "Synthetic report was not structurally reviewed")
        self.assertEqual(detail["mission"]["verification"], "structural_only")
        self.assertEqual(detail["mission"]["outcome"], "blocked")
        self.assertIsNone(detail["mission"]["progress_percent"])
        self.assertIn("합성", detail["mission"]["summary"])
        snapshot = self.snapshot()
        self.assertEqual(snapshot["metrics"]["reports"], 1)
        self.assertEqual(snapshot["metrics"]["verified_outcomes"], 0)
        employee = next(item for item in snapshot["employees"] if item["id"] == "das-rd")
        derived = next(item for item in employee["memories"] if item["source"] == "mission:" + mission_id)
        self.assertEqual(derived["verification"], "ai_unverified")

    def test_external_host_origin_and_missing_csrf_header_cannot_mutate(self):
        for headers in ({"Host": "attacker.example"}, {"Origin": "https://attacker.example"},
                        {"X-DAS-Office": ""}, {"Content-Type": "text/plain"}):
            with self.subTest(headers=headers):
                status, _, _ = self.request("/api/org/missions", "POST",
                                            {"request_id": "rejected", "text": "이 요청을 실행해 줘"}, headers)
                self.assertEqual(status, 403)
        self.assertEqual(self.snapshot()["missions"], [])

    def test_invalid_payloads_and_unknown_employee_do_not_persist(self):
        invalid = ({}, {"text": ""}, {"text": "업무를 요약해 줘", "request_id": ""},
                   {"text": 5, "request_id": "bad"}, {"text": "업무를 요약해 줘", "request_id": "x" * 161},
                   {"text": "업무를 요약해 줘", "request_id": "bad", "employee_id": "missing"})
        for body in invalid:
            with self.subTest(body=body):
                status, _, _ = self.request("/api/org/missions", "POST", body)
                self.assertIn(status, (400, 404))
        for raw in (b"[]", b"null", b"{broken"):
            with self.subTest(raw=raw):
                status, _, _ = self.request("/api/org/missions", "POST", raw, raw=True)
                self.assertEqual(status, 400)
        self.assertEqual(self.snapshot()["missions"], [])

    def test_legacy_mutations_are_blocked_when_organization_owns_execution(self):
        for path in ("/api/missions", "/api/tasks", "/api/tasks/" + "a" * 32 + "/run"):
            with self.subTest(path=path):
                status, _, payload = self.request(path, "POST", {"mission": "이전 실행기를 시작해 줘"})
                self.assertEqual(status, 409, payload)
        self.legacy.submit_mission.assert_not_called()
        self.legacy.create.assert_not_called()
        self.legacy.run.assert_not_called()
        self.assertEqual(self.snapshot()["execution"]["active_count"], 0)

    def test_sse_stream_delivers_initial_state_and_actual_memory_revision(self):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=4)
        try:
            connection.request("GET", "/api/org/events")
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            self.assertTrue(response.getheader("Content-Type").startswith("text/event-stream"))
            self.assertEqual(response.getheader("Cache-Control"), "no-store")

            def next_snapshot():
                frame = {}
                for _ in range(20):
                    line = response.readline().decode("utf-8").rstrip("\r\n")
                    if line == "":
                        if frame.get("event") == "snapshot":
                            payload = json.loads(frame["data"])
                            self.assertEqual(int(frame["id"]), payload["revision"])
                            return payload
                        frame = {}
                    elif ":" in line and not line.startswith(":"):
                        field, value = line.split(":", 1)
                        frame[field] = value.lstrip()
                self.fail("Stream did not emit a complete snapshot frame")

            initial = next_snapshot()
            self.assertEqual(initial["missions"], [])
            status, _, memory = self.request("/api/org/employees/das-rd/memory", "POST",
                                             {"text": "실제 장면의 측정 없이 품질 개선을 확정하지 않는다."})
            self.assertEqual(status, 201)
            changed = next_snapshot()
            self.assertGreater(changed["revision"], initial["revision"])
            employee = next(item for item in changed["employees"] if item["id"] == "das-rd")
            self.assertIn(memory["memory"], employee["memories"])
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
