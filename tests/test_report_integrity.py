"""Completion decisions must use the bytes that passed review, not later edits."""
import hashlib
import json
import unittest
from unittest.mock import patch

from tests import test_missions as mission_fixtures


class ReportIntegrityTests(unittest.TestCase):
    setUp = mission_fixtures.MissionTests.setUp
    wait_run = mission_fixtures.MissionTests.wait_run
    report_worker = mission_fixtures.MissionTests.report_worker

    def after_review(self, change):
        reviewer = self.office.reviewer

        class InterceptedReviewer:
            provider = "code"

            def review(self, folder, execution, criteria, project_id):
                result = reviewer.review(folder, execution, criteria, project_id)
                change(folder, result)
                return result

        self.office.reviewer = InterceptedReviewer()

    def run_report(self):
        task = self.office.submit_mission({"mission": "Summarize the supplied business notes."})["task"]
        self.wait_run(task)
        return self.office.detail(task["id"])

    def assert_integrity_failure(self, detail):
        self.assertEqual(detail["task"]["status"], "failed")
        self.assertNotEqual(detail["task"]["overview"]["outcome"], "achieved")
        self.assertIsNone(detail["task"]["overview"]["progress_percent"])
        self.assertIsNone(detail["report"])
        run = detail["runs"][-1]
        self.assertFalse(run["verification"]["passed"])
        self.assertEqual(run["verification"]["verification_status"], "artifact_changed")
        self.assertNotIn("report", run)
        persisted = json.loads(self.office.file(run["id"], "verification.json").read_bytes())
        self.assertEqual(persisted, run["verification"])
        for artifact in run["artifacts"]:
            self.assertEqual(hashlib.sha256(self.office.file(run["id"], artifact["name"]).read_bytes()).hexdigest(),
                             artifact["sha256"])

    def test_result_changed_after_review_cannot_upgrade_partial_to_achieved(self):
        self.report_worker("partial")

        def change(folder, verification):
            self.assertTrue(verification["passed"])
            self.assertEqual(verification["outcome"]["status"], "partial")
            document = json.loads((folder / "result.json").read_bytes())
            document["outcome"].update(status="achieved", progress_percent=100)
            document["remaining"] = []
            for item in document["evidence"]:
                item.update(status="met", artifact_section="report")
            (folder / "result.json").write_text(json.dumps(document), encoding="utf-8")

        self.after_review(change)
        self.assert_integrity_failure(self.run_report())

    def test_every_reviewed_file_is_checked_before_completion(self):
        for name in ("report.md", "events.jsonl"):
            with self.subTest(name=name):
                self.report_worker()
                self.after_review(lambda folder, verification: (folder / name).write_text("changed after review", encoding="utf-8"))
                self.assert_integrity_failure(self.run_report())

    def test_final_snapshot_rechecks_changes_made_after_assessment(self):
        self.report_worker()
        original = self.office._artifacts
        calls = 0

        def snapshot(run, folder):
            nonlocal calls
            calls += 1
            if calls == 2:
                (folder / "report.md").write_text("changed before finish", encoding="utf-8")
            return original(run, folder)

        with patch.object(self.office, "_artifacts", side_effect=snapshot):
            self.assert_integrity_failure(self.run_report())
        self.assertGreaterEqual(calls, 2)

    def test_deleted_reviewed_artifact_is_not_silently_dropped(self):
        self.report_worker()
        self.after_review(lambda folder, verification: (folder / "events.jsonl").unlink())
        self.assert_integrity_failure(self.run_report())

    def test_generic_completion_requires_reviewer_artifact_hashes(self):
        self.report_worker()
        self.after_review(lambda folder, verification: verification.pop("artifacts"))
        self.assert_integrity_failure(self.run_report())

    def test_legacy_result_with_an_outcome_field_does_not_auto_complete(self):
        original = self.worker.execute

        def legacy_execute(folder, *args):
            execution = original(folder, *args)
            task = json.loads((folder / "input.json").read_bytes())
            document = {"summary": "Legacy output with a completion claim.",
                        "outcome": {"status": "achieved", "progress_percent": 100},
                        "evidence": [{"criterion": criterion, "status": "met"} for criterion in task["acceptance_criteria"]]}
            (folder / "result.json").write_text(json.dumps(document), encoding="utf-8")
            return execution

        self.worker.execute = legacy_execute
        detail = self.run_report()
        self.assertEqual(detail["task"]["status"], "review")

    def test_unchanged_report_snapshot_matches_reviewed_files(self):
        self.report_worker()
        detail = self.run_report()
        self.assertEqual(detail["task"]["status"], "completed")
        run = detail["runs"][-1]
        final_hashes = {item["name"]: item["sha256"] for item in run["artifacts"]}
        for artifact in run["verification"]["artifacts"]:
            self.assertEqual(final_hashes[artifact["name"]], artifact["sha256"])
        self.assertEqual(run["report"], json.loads(self.office.file(run["id"], "result.json").read_bytes()))


if __name__ == "__main__":
    unittest.main()
