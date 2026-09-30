"""Real HTTP standing controls against a temporary DB and offline worker."""
import unittest
from datetime import date
from unittest.mock import patch

import tests.test_organization_http as organization_http


class StandingHTTPTests(unittest.TestCase):
    def setUp(self):
        self.fixture = organization_http.OrganizationHTTPTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        clock = patch.object(self.fixture.organization.standing, "_date", return_value=date(2026, 9, 30))
        clock.start()
        self.addCleanup(clock.stop)
        self.request = self.fixture.request

    def standing(self):
        status, headers, body = self.request("/api/org/standing")
        self.assertEqual(status, 200, body)
        self.assertEqual(headers["Cache-Control"], "no-store")
        return body

    def test_get_is_read_only_and_does_not_claim_a_registered_schedule(self):
        before = self.fixture.snapshot()
        result = self.standing()
        self.assertTrue(result["enabled"])
        self.assertIsNone(result["current_cycle"])
        self.assertFalse(result["recurring_enabled"])
        self.assertFalse(result["publication_enabled"])
        self.assertFalse(result["outreach_enabled"])
        self.assertEqual(self.fixture.snapshot()["revision"], before["revision"])
        self.assertEqual(self.fixture.worker.calls, [])

    def test_tick_posts_dispatch_fixed_duties_once_and_never_call_real_provider(self):
        status, _, result = self.request("/api/org/standing/tick", "POST", {})
        self.assertEqual(status, 200, result)
        self.assertEqual(len(result["submitted"]), 3)
        ids = {slot["mission_id"] for slot in result["current_cycle"]["duties"].values()}
        self.assertEqual(len(ids), 3)
        for mission_id in ids:
            detail = self.fixture.until(
                lambda mid=mission_id: (value if (value := self.fixture.detail(mid))["mission"]["status"] == "deferred" else None),
                "Offline research mission did not defer")
            self.assertEqual(detail["mission"]["execution_mode"], "research")
            self.assertEqual(detail["mission"]["project_context"]["project_id"], "daslab-growth")
            self.assertEqual(detail["attempts"], [])
        status, _, replay = self.request("/api/org/standing/tick", "POST", {})
        self.assertEqual(status, 200, replay)
        self.assertEqual({slot["mission_id"] for slot in replay["current_cycle"]["duties"].values()}, ids)
        research_missions = [m for m in self.fixture.snapshot()["missions"] if m["execution_mode"] == "research"]
        self.assertEqual(len(research_missions), 3)
        self.assertEqual(self.fixture.worker.calls, [])

    def test_pause_resume_controls_new_assignment(self):
        status, _, paused = self.request("/api/org/standing/pause", "POST", {"paused": True})
        self.assertEqual(status, 200, paused)
        self.assertTrue(paused["paused"])
        status, _, tick = self.request("/api/org/standing/tick", "POST", {})
        self.assertEqual(status, 200)
        self.assertEqual(tick["submitted"], [])
        self.assertIsNone(tick["current_cycle"])
        self.assertEqual(self.fixture.snapshot()["missions"], [])
        status, _, resumed = self.request("/api/org/standing/pause", "POST", {"paused": False})
        self.assertEqual(status, 200, resumed)
        self.assertFalse(resumed["paused"])
        self.assertFalse(self.standing()["paused"])
        self.assertEqual(self.fixture.worker.calls, [])

    def test_schedule_endpoint_records_receipt_without_starting_work(self):
        status, _, result = self.request("/api/org/standing/schedule", "POST", {"automation_id": "offline-confirmed-schedule-fixture"})
        self.assertEqual(status, 200, result)
        receipt = result["schedule_registration"]
        self.assertEqual(receipt["id"], "offline-confirmed-schedule-fixture")
        self.assertTrue(receipt["enabled"])
        self.assertIn("실시간 확인한 값은 아닙니다", receipt["note"])
        before = self.fixture.snapshot()["revision"]
        status, _, replay = self.request("/api/org/standing/schedule", "POST", {"automation_id": receipt["id"]})
        self.assertEqual(status, 200, replay)
        self.assertEqual(replay["schedule_registration"], receipt)
        self.assertEqual(self.fixture.snapshot()["revision"], before)
        self.assertEqual(self.fixture.snapshot()["missions"], [])
        self.assertEqual(self.fixture.worker.calls, [])

    def test_standing_mutations_reject_cross_origin_and_missing_csrf_headers(self):
        for path, payload in (("tick", {}), ("pause", {"paused": True}), ("schedule", {"automation_id": "fixture"})):
            for headers in ({"Host": "attacker.example"}, {"Origin": "https://attacker.example"},
                            {"X-DAS-Office": ""}, {"Content-Type": "text/plain"}):
                with self.subTest(path=path, headers=headers):
                    # These headers are rejected before the server reads a body.
                    # Avoid an unread-body TCP reset masking the HTTP 403 on Windows.
                    status, _, body = self.request("/api/org/standing/" + path, "POST", headers=headers)
                    self.assertEqual(status, 403, body)
        standing = self.standing()
        self.assertFalse(standing["paused"])
        self.assertFalse(standing["recurring_enabled"])
        self.assertIsNone(standing["current_cycle"])
        self.assertEqual(self.fixture.worker.calls, [])

    def test_invalid_payloads_and_get_requests_cannot_mutate_standing_controls(self):
        for path in ("tick", "pause", "schedule"):
            for payload in (b"null", b"[]", b"{broken"):
                with self.subTest(path=path, payload=payload):
                    status, _, body = self.request("/api/org/standing/" + path, "POST", payload, raw=True)
                    self.assertEqual(status, 400, body)
            status, _, body = self.request("/api/org/standing/" + path)
            self.assertEqual(status, 404, body)
        for payload in ({}, {"paused": "false"}, {"paused": 1}, {"paused": None}):
            status, _, body = self.request("/api/org/standing/pause", "POST", payload)
            self.assertEqual(status, 400, body)
        for payload in ({}, {"automation_id": True}, {"automation_id": ""}, {"automation_id": "x" * 201}):
            status, _, body = self.request("/api/org/standing/schedule", "POST", payload)
            self.assertEqual(status, 400, body)
        self.assertIsNone(self.standing()["current_cycle"])
        self.assertEqual(self.fixture.snapshot()["missions"], [])
        self.assertEqual(self.fixture.worker.calls, [])

    def test_ordinary_composer_research_request_reaches_growth_scope(self):
        status, _, result = self.request("/api/org/missions", "POST", {
            "request_id": "ordinary-research", "text": "최근 시뮬레이션 기술 동향을 조사해",
            "employee_id": "das-rd", "context": {"project_id": "office-ui"},
        })
        self.assertEqual(status, 201, result)
        self.assertEqual(result["mission"]["execution_mode"], "research")
        self.assertEqual(result["mission"]["project_context"]["project_id"], "daslab-growth")
        self.assertFalse(result["mission"].get("workflow"))
        self.assertEqual(self.fixture.worker.calls, [])

    def test_ordinary_research_request_does_not_enable_missing_policy(self):
        self.fixture.organization.research_policy = {}
        status, _, result = self.request("/api/org/missions", "POST", {
            "request_id": "no-research-policy", "text": "최근 시뮬레이션 기술 동향을 조사해",
            "employee_id": "das-rd", "context": {"project_id": "office-ui"},
        })
        self.assertEqual(status, 201, result)
        self.assertEqual(result["mission"]["execution_mode"], "analysis")
        self.assertEqual(self.fixture.worker.calls, [])

    def test_explicit_ui_change_remains_development_despite_research_words(self):
        status, _, result = self.request("/api/org/missions", "POST", {
            "request_id": "explicit-ui-change", "text": "기술 동향 표시를 위한 조직 UI 화면의 버튼 디자인을 수정해",
            "employee_id": "das-rd", "context": {"project_id": "office-ui"},
        })
        self.assertEqual(status, 201, result)
        self.assertEqual(result["mission"]["execution_mode"], "development")
        self.assertEqual(result["mission"]["project_context"]["project_id"], "office-ui")
        self.assertEqual(self.fixture.worker.calls, [])

    def test_composer_feedback_cannot_change_research_into_organization_ui_work(self):
        for employee in ("das-rd", "das-mkt"):
            with self.subTest(employee=employee):
                status, _, result = self.request("/api/org/missions", "POST", {
                    "request_id": "research-feedback-" + employee, "text": "공개된 데모 자료를 조사해 주세요.",
                    "employee_id": employee, "execution_mode": "research", "context": {"project_id": "daslab-growth"},
                })
                self.assertEqual(status, 201, result)
                mission_id = result["mission"]["id"]
                self.fixture.until(
                    lambda: (detail if (detail := self.fixture.detail(mission_id))["mission"]["status"] == "deferred" else None),
                    "Offline mission did not defer")
                status, _, changed = self.request(f"/api/org/missions/{mission_id}/action", "POST", {
                    "action": "instruct", "text": "고객이 이해하기 쉬운 데모 디자인도 조사해 주세요.",
                    "context": {"project_id": "office-ui"},
                })
                self.assertEqual(status, 200, changed)
                self.assertEqual(changed["mission"]["execution_mode"], "research")
                self.assertEqual(changed["mission"]["project_context"]["project_id"], "daslab-growth")
                self.assertEqual(changed["mission"]["status"], "paused")
        self.assertEqual(self.fixture.worker.calls, [])


if __name__ == "__main__":
    unittest.main()
