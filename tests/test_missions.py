"""Mission intake, queued execution and honest dashboard outcome regressions."""
import http.client
import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import patch

from office.outcomes import assessment, elapsed_seconds
from office.providers import CodeReviewer
from office.service import Office
from server import handler_for
from tests import test_office as fixtures


class MissionTests(unittest.TestCase):
    setUp = fixtures.OfficeServiceTests.setUp
    wait_run = fixtures.OfficeServiceTests.wait_run

    def report_worker(self, status="achieved", unknown=False):
        def execute(folder, prompt, timeout, cancel):
            task = json.loads((folder / "input.json").read_text(encoding="utf-8"))
            criteria = task["acceptance_criteria"]
            states = ["met"] * len(criteria)
            if status != "achieved":
                states[-1] = "unknown" if unknown else "unmet"
            document = {"summary": "업무 핵심을 세 문장으로 정리했습니다.",
                        "analysis": {"priority": "normal", "complexity": "moderate", "rationale": "검토할 자료가 있습니다.", "process": ["자료 확인", "요약"]},
                        "outcome": {"status": status, "progress_percent": None if unknown else states.count("met") * 100 // len(states), "basis": "완료 기준을 확인했습니다."},
                        "report": [{"title": "결과", "content": "첫째, 핵심 업무는 견적 비교입니다. 둘째, 필요한 자료는 견적서입니다."}],
                        "accomplishments": ["제공된 내용의 핵심을 정리했습니다."],
                        "remaining": [] if status == "achieved" else ["추가 자료가 필요합니다."], "limitations": [],
                        "evidence": [{"criterion": criterion, "status": state, "artifact_section": "report" if state == "met" else "remaining", "explanation": "보고서에 대응 내용이 있습니다."}
                                     for criterion, state in zip(criteria, states)]}
            (folder / "result.json").write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
            (folder / "report.md").write_text("# 결과\n" + document["summary"], encoding="utf-8")
            (folder / "events.jsonl").write_text('{"type":"turn.completed"}\n', encoding="utf-8")
            return {"exit_code": 0, "completed": True}
        self.worker.execute = execute
        self.office.reviewer = CodeReviewer()

    def test_one_sentence_infers_brief_without_owner_metadata(self):
        task = self.office.create({"mission": "오늘 프랜차이즈 출점 견적 비교 방식을 정리해 줘"})["task"]
        self.assertEqual(task["goal"], task["mission"])
        self.assertEqual(task["project_id"], "franchise")
        self.assertEqual(task["priority"], "high")
        self.assertTrue(task["acceptance_criteria"])
        self.assertTrue(task["mission_analysis"]["process"])
        self.assertEqual(self.worker.calls, [])

    def test_mission_input_validation_never_creates_bad_task(self):
        for payload in ({}, {"mission": " "}, {"mission": 8}, {"mission": "요약", "inputs": []},
                        {"mission": "요약", "project_id": "missing"}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                self.office.submit_mission(payload)
        self.assertEqual(self.office.store.tasks(), [])

    def test_unavailable_worker_persists_queued_mission_and_reason(self):
        self.worker.available = False
        result = self.office.submit_mission({"mission": "자료를 요약해 줘"})
        self.assertFalse(result["started"])
        self.assertTrue(result["start_error"])
        detail = self.office.detail(result["task"]["id"])
        self.assertEqual(detail["task"]["status"], "queued")
        self.assertEqual(detail["task"]["overview"]["outcome"], "pending")
        self.assertIsNone(detail["task"]["overview"]["progress_percent"])
        self.assertEqual(detail["runs"], [])

    def test_verified_achievement_completes_without_owner_approval(self):
        self.report_worker()
        result = self.office.submit_mission({"mission": "견적 업무 내용을 요약해 줘"})
        self.assertTrue(result["started"])
        self.wait_run(result["task"])
        detail = self.office.detail(result["task"]["id"])
        self.assertEqual(detail["task"]["status"], "completed")
        self.assertEqual(detail["task"]["overview"]["outcome"], "achieved")
        self.assertEqual(detail["task"]["overview"]["progress_percent"], 100)
        self.assertEqual(detail["task"]["overview"]["complexity"], "standard")
        self.assertEqual(detail["decisions"], [])
        self.assertEqual(detail["report"]["sections"][0]["title"], "결과")
        self.assertEqual(detail["report"]["assessment_source"], "ai_self_assessment")
        self.assertIsNotNone(detail["task"]["overview"]["duration_seconds"])
        changed = self.office.review(result["task"]["id"], {"decision": "reject", "note": "세 문장 대신 한 문장으로 정리해 줘"})
        self.assertEqual(changed["task"]["status"], "failed")
        self.assertEqual(changed["runs"][0]["status"], "completed")

    def test_partial_report_preserves_earned_progress_and_unknown_never_fakes_it(self):
        for unknown in (False, True):
            self.report_worker("partial", unknown)
            # Two real deliverables: finishing one earns 50%; reporting honestly earns none.
            task = self.office.create({**self.payload, "goal": "견적 내용을 요약하고 업체별 비교표를 만들어 줘",
                                       "acceptance_criteria": ["제공된 견적 내용을 요약한다.", "업체별 가격 비교표를 작성한다."]})["task"]
            self.office.run(task["id"])
            result = {"task": task}
            self.wait_run(result["task"])
            detail = self.office.detail(result["task"]["id"])
            self.assertEqual(detail["task"]["status"], "review")
            self.assertEqual(detail["task"]["overview"]["outcome"], "partial")
            if unknown:
                self.assertIsNone(detail["task"]["overview"]["progress_percent"])
            else:
                self.assertEqual(detail["task"]["overview"]["progress_percent"], 50)

    def test_blocked_real_work_gets_no_credit_for_honest_reporting(self):
        self.report_worker("blocked")
        mission = "고객 세 명에게 안내 이메일을 보내 줘"
        result = self.office.submit_mission({"mission": mission})
        self.wait_run(result["task"])
        detail = self.office.detail(result["task"]["id"])
        self.assertEqual(len(detail["task"]["acceptance_criteria"]), 1)
        self.assertIn(mission, detail["task"]["acceptance_criteria"][0])
        self.assertEqual(detail["task"]["overview"]["outcome"], "blocked")
        self.assertEqual(detail["task"]["overview"]["progress_percent"], 0)

    def restart_office(self):
        self.office.close()
        self.office = Office(self.root, self.root / "data", (self.planner, self.worker, self.reviewer))
        self.office.config["worker_provider"] = "fake"
        self.addCleanup(self.office.close)

    def signal_worker_start(self):
        entered = threading.Event()
        execute = self.worker.execute

        def signalled(*args):
            entered.set()
            return execute(*args)

        self.worker.execute = signalled
        return entered

    def test_scheduler_resumes_queue_after_worker_recovers_and_stops_on_close(self):
        with patch.object(Office, "QUEUE_POLL_SECONDS", 0.02):
            self.worker.available = False
            self.restart_office()
            result = self.office.submit_mission({"mission": "자료를 요약해 줘"})
            entered = self.signal_worker_start()
            self.worker.available = True
            self.assertTrue(entered.wait(timeout=3), "Queue did not resume after worker recovered")
            self.wait_run(result["task"])
            self.assertEqual(len(self.worker.calls), 1)
            self.office.close()
            self.assertFalse(self.office.scheduler.is_alive())

    def test_scheduler_resumes_persisted_queue_after_restart(self):
        self.worker.available = False
        result = self.office.submit_mission({"mission": "저장한 미션을 요약해 줘"})
        entered = self.signal_worker_start()
        self.worker.available = True
        with patch.object(Office, "QUEUE_POLL_SECONDS", 0.02):
            self.restart_office()
            self.assertTrue(entered.wait(timeout=3), "Persisted queue was not scheduled")
            self.wait_run(result["task"])
            self.assertEqual(len(self.worker.calls), 1)

    def test_scheduler_resumes_daily_limited_queue_without_retrying_failed_runs(self):
        with patch.object(Office, "QUEUE_POLL_SECONDS", 0.02):
            self.restart_office()
            self.office.config["daily_runs"] = 1
            self.worker.fail = True
            first = self.office.submit_mission({"mission": "첫 미션"})
            self.wait_run(first["task"])
            second = self.office.submit_mission({"mission": "둘째 미션"})
            self.assertFalse(second["started"])
            self.assertIn("오늘", second["start_error"])
            self.worker.fail = False
            entered = self.signal_worker_start()
            with self.office.store.connect() as db:
                # Shift the completed attempt out of today's allowance, as midnight does.
                row = db.execute("SELECT id FROM runs WHERE task_id=?", (first["task"]["id"],)).fetchone()
                first_run = self.office.store.read(db, "runs", row[0])
                first_run["started_at"] = "2020-01-01T00:00:00+00:00"
                self.office.store.save(db, "runs", first_run)
            self.assertTrue(entered.wait(timeout=3), "Queue did not resume when daily allowance became available")
            self.wait_run(second["task"])
            self.assertEqual(len(self.office.store.detail(first["task"]["id"])["runs"]), 1)
            self.assertEqual(self.office.store.detail(first["task"]["id"])["task"]["status"], "failed")
            self.assertEqual(len(self.worker.calls), 2)

    def test_queued_mission_starts_after_active_mission_finishes(self):
        entered, release = threading.Event(), threading.Event()
        self.report_worker()
        actual_execute = self.worker.execute
        calls = []

        def blocking_execute(*args):
            calls.append(args)
            if len(calls) == 1:
                entered.set()
                release.wait(timeout=5)
            return actual_execute(*args)

        self.worker.execute = blocking_execute
        first = self.office.submit_mission({"mission": "첫 자료를 요약해 줘"})
        self.assertTrue(entered.wait(timeout=3))
        second = self.office.submit_mission({"mission": "둘째 자료를 요약해 줘"})
        self.assertFalse(second["started"])
        self.assertEqual(second["task"]["status"], "queued")
        release.set()
        self.wait_run(first["task"])
        self.wait_run(second["task"])
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.office.detail(second["task"]["id"])["task"]["status"], "completed")

    def test_revision_queues_behind_busy_worker_and_preserves_history(self):
        self.report_worker()
        original = self.office.submit_mission({"mission": "첫 요약을 만들어 줘"})
        self.wait_run(original["task"])
        original_run = self.office.store.detail(original["task"]["id"])["runs"][0]
        entered, release = threading.Event(), threading.Event()
        execute = self.worker.execute

        def block_other_task(folder, *args):
            task = json.loads((folder / "input.json").read_text(encoding="utf-8"))
            if task["id"] != original["task"]["id"]:
                entered.set()
                release.wait(timeout=5)
            return execute(folder, *args)

        self.worker.execute = block_other_task
        other = self.office.submit_mission({"mission": "다른 자료를 정리해 줘"})
        self.assertTrue(entered.wait(timeout=3))
        try:
            revised = self.office.revise(original["task"]["id"], {"note": "첫 문장을 더 짧게 써 줘"})
            self.assertFalse(revised["started"])
            self.assertTrue(revised["queued"])
            self.assertEqual(revised["task"]["status"], "queued")
            detail = self.office.store.detail(original["task"]["id"])
            self.assertEqual(detail["runs"], [original_run])
            self.assertEqual(detail["decisions"][-1]["note"], "첫 문장을 더 짧게 써 줘")
        finally:
            release.set()
        self.wait_run(other["task"])
        detail = self.wait_run(original["task"])
        self.assertEqual(detail["task"]["status"], "completed")
        self.assertEqual(len(detail["runs"]), 2)
        snapshot = json.loads(self.office.file(detail["runs"][-1]["id"], "input.json").read_text(encoding="utf-8"))
        self.assertEqual(snapshot["review_decisions"][-1]["note"], "첫 문장을 더 짧게 써 줘")

    def test_revision_waits_for_availability_but_never_queues_permanent_attempt_limit(self):
        self.report_worker()
        original = self.office.submit_mission({"mission": "요약해 줘"})
        self.wait_run(original["task"])
        self.worker.available = False
        revised = self.office.revise(original["task"]["id"], {"note": "제목을 짧게 써 줘"})
        self.assertTrue(revised["queued"])
        self.assertEqual(revised["task"]["status"], "queued")
        self.worker.available = True
        self.office._start_next()
        self.wait_run(original["task"])
        self.office.config["max_attempts"] = 2
        self.worker.available = False
        capped = self.office.revise(original["task"]["id"], {"note": "설명을 더 줄여 줘"})
        self.assertFalse(capped["started"])
        self.assertFalse(capped["queued"])
        self.assertIn("최대 실행 횟수", capped["start_error"])
        self.assertEqual(capped["task"]["status"], "failed")
        self.assertFalse(capped["task"].get("auto_start"))
        self.assertEqual(len(self.office.store.detail(original["task"]["id"])["runs"]), 2)

    def test_revision_requires_a_change_request_and_ordinary_failures_cannot_auto_requeue(self):
        self.report_worker()
        original = self.office.submit_mission({"mission": "요약해 줘"})
        self.wait_run(original["task"])
        with self.assertRaises(ValueError):
            self.office.revise(original["task"]["id"], {"note": " "})
        self.assertEqual(self.office.store.detail(original["task"]["id"])["decisions"], [])
        self.office.review(original["task"]["id"], {"decision": "reject", "note": "한 문장으로 써 줘"})
        self.worker.execute = lambda *args: (_ for _ in ()).throw(RuntimeError("test failure"))
        self.office.run(original["task"]["id"])
        self.wait_run(original["task"])
        queued, _ = self.office.store.requeue_requested_revision(original["task"]["id"], "retry", 3)
        self.assertFalse(queued)
        self.assertEqual(self.office.store.detail(original["task"]["id"])["task"]["status"], "failed")

    def test_legacy_report_renders_but_basic_checks_do_not_mean_achievement(self):
        task = self.office.create(self.payload)["task"]
        self.office.run(task["id"])
        self.wait_run(task)
        detail = self.office.detail(task["id"])
        self.assertEqual(detail["report"]["summary"], "Test report only")
        self.assertEqual(detail["task"]["overview"]["outcome"], "unknown")
        self.assertIsNone(detail["task"]["overview"]["progress_percent"])
        approved = self.office.review(task["id"], {"decision": "approve"})
        self.assertEqual(approved["task"]["status"], "completed")
        self.assertEqual(approved["task"]["overview"]["outcome"], "unknown")

    def test_http_mission_create_and_dashboard_share_human_summary(self):
        self.worker.available = False
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(self.office))
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
        try:
            connection.request("POST", "/api/missions", json.dumps({"mission": "요약해 줘"}),
                               {"Content-Type": "application/json", "X-DAS-Office": "1"})
            response = connection.getresponse()
            created = json.loads(response.read())
            self.assertEqual(response.status, 201)
            self.assertFalse(created["started"])
            connection.request("GET", "/api/tasks")
            response = connection.getresponse()
            tasks = json.loads(response.read())["tasks"]
            self.assertEqual(tasks[0]["id"], created["task"]["id"])
            self.assertIn("summary", tasks[0]["overview"])
        finally:
            connection.close()
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

    def test_http_revision_records_note_and_queues_when_daily_allowance_is_used(self):
        self.report_worker()
        original = self.office.submit_mission({"mission": "요약해 줘"})
        self.wait_run(original["task"])
        self.office.config["daily_runs"] = 1
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(self.office))
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
        try:
            connection.request("POST", f"/api/tasks/{original['task']['id']}/revise", json.dumps({"note": "두 문장으로 줄여 줘"}),
                               {"Content-Type": "application/json", "X-DAS-Office": "1"})
            response = connection.getresponse()
            result = json.loads(response.read())
            self.assertEqual(response.status, 200)
            self.assertFalse(result["started"])
            self.assertTrue(result["queued"])
            self.assertIn("오늘", result["start_error"])
            self.assertEqual(result["task"]["status"], "queued")
            detail = self.office.store.detail(original["task"]["id"])
            self.assertEqual(detail["decisions"][-1]["note"], "두 문장으로 줄여 줘")
            self.assertEqual(len(detail["runs"]), 1)
        finally:
            connection.close()
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

    def test_elapsed_time_is_actual_run_total_and_never_progress(self):
        runs = [{"started_at": "2026-09-24T01:00:00+00:00", "finished_at": "2026-09-24T01:00:10+00:00"},
                {"started_at": "2026-09-24T02:00:00+00:00", "finished_at": "2026-09-24T02:00:25+00:00"}]
        self.assertEqual(elapsed_seconds(runs), 35)
        runs[0]["verification_status"] = "interrupted"
        self.assertIsNone(elapsed_seconds(runs))
        self.assertIsNone(assessment({"summary": "done"}, ["goal"], True)["progress_percent"])


if __name__ == "__main__":
    unittest.main()
