"""Partial mission progress counts requested deliverables, with original scope intact."""
import copy
import json
import unittest

from office.outcomes import assessment, overview, report_view
from tests import test_missions as mission_fixtures
from tests import test_validation as validation_fixtures


class MilestoneProgressTests(unittest.TestCase):
    setUp = validation_fixtures.MissionReportTests.setUp
    review = validation_fixtures.MissionReportTests.review

    def add_milestones(self):
        self.criteria = ["Compare prices, compare exclusions, and recommend the next step."]
        self.document["evidence"] = [{"criterion": self.criteria[0], "status": "unmet", "artifact_section": "remaining",
                                      "explanation": "Both comparisons are done; the recommendation is still missing."}]
        self.document["milestones"] = [
            {"criterion": self.criteria[0], "deliverable": title, "status": state,
             "artifact_section": "report" if state == "met" else "remaining", "explanation": explanation}
            for title, state, explanation in (("Price comparison", "met", "Prices were compared."),
                                               ("Exclusion comparison", "met", "The exclusions were compared."),
                                               ("Next-step recommendation", "unmet", "A recommendation is still missing."))]
        self.document["remaining"] = ["Write the recommendation using the compared prices and exclusions."]
        self.document["outcome"].update(status="partial", progress_percent=66, basis="Two of the three requested results are complete.")

    def test_one_original_goal_can_report_honest_partial_deliverables(self):
        self.add_milestones()
        verification = self.review()
        self.assertTrue(verification["passed"])
        result = assessment(self.document, self.criteria, verification["passed"])
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["progress_percent"], 66)
        self.assertIn("3개 중 2개 완료", result["basis"])
        self.assertEqual(report_view(self.document, result)["milestones"][0]["deliverable"], "Price comparison")

    def test_every_original_criterion_must_be_covered(self):
        self.add_milestones()
        self.criteria.append("Write a customer-facing summary.")
        self.document["evidence"].append({"criterion": self.criteria[-1], "status": "met", "artifact_section": "report",
                                          "explanation": "A summary was written."})
        self.assertFalse(self.review()["passed"])
        self.assertEqual(assessment(self.document, self.criteria, True)["status"], "unknown")

    def test_unknown_deliverable_has_unknown_total_and_original_criterion(self):
        self.add_milestones()
        self.document["milestones"][-1]["status"] = "unknown"
        self.document["evidence"][0]["status"] = "unknown"
        self.document["outcome"]["progress_percent"] = None
        self.assertTrue(self.review()["passed"])
        self.assertIsNone(assessment(self.document, self.criteria, True)["progress_percent"])
        self.document["outcome"]["progress_percent"] = 66
        self.assertFalse(self.review()["passed"])

    def test_duplicate_extra_and_empty_milestones_do_not_inflate_counts(self):
        self.add_milestones()
        original = copy.deepcopy(self.document)
        cases = [
            [{**original["milestones"][0], "deliverable": " PRICE COMPARISON "}, *original["milestones"]],
            [{**original["milestones"][0], "criterion": "Unrequested work"}, *original["milestones"][1:]],
            [{**original["milestones"][0], "deliverable": " "}, *original["milestones"][1:]],
            [{**original["milestones"][0], "status": "done"}, *original["milestones"][1:]],
            None,
        ]
        for milestones in cases:
            with self.subTest(milestones=milestones):
                self.document = copy.deepcopy(original)
                self.document["milestones"] = milestones
                self.assertFalse(self.review()["passed"])

    def test_milestone_reference_must_contain_actual_result_content(self):
        self.add_milestones()
        for reference in ("milestones[0]", "outcome", "report[99]", "remaining"):
            with self.subTest(reference=reference):
                self.document["milestones"][0]["artifact_section"] = reference
                self.assertFalse(self.review()["passed"])

    def test_parent_and_child_statuses_cannot_contradict_each_other(self):
        self.add_milestones()
        for state in ("met", "unknown"):
            with self.subTest(state=state):
                self.document["evidence"][0]["status"] = state
                self.assertFalse(self.review()["passed"])
        self.document["evidence"][0]["status"] = "unmet"
        self.document["milestones"][-1].update(status="met", artifact_section="report")
        self.document["outcome"].update(status="achieved", progress_percent=100)
        self.document["remaining"] = []
        self.assertFalse(self.review()["passed"])
        self.document["evidence"][0].update(status="met", artifact_section="report")
        self.assertTrue(self.review()["passed"])
        self.assertEqual(assessment(self.document, self.criteria, True)["status"], "achieved")

    def test_empty_or_absent_milestones_preserve_existing_criterion_calculation(self):
        self.document["milestones"] = []
        self.assertTrue(self.review()["passed"])
        self.assertEqual(assessment(self.document, self.criteria, True)["progress_percent"], 100)
        del self.document["milestones"]
        self.assertTrue(self.review()["passed"])

    def test_running_mission_does_not_show_a_final_report_as_live_progress(self):
        self.add_milestones()
        task = {"status": "running", "acceptance_criteria": self.criteria, "priority": "normal"}
        result = overview(task, [{"verification": {"passed": True}}], self.document)
        self.assertEqual(result["outcome"], "running")
        self.assertIsNone(result["progress_percent"])


class MilestoneServiceTests(unittest.TestCase):
    setUp = mission_fixtures.MissionTests.setUp
    wait_run = mission_fixtures.MissionTests.wait_run
    report_worker = mission_fixtures.MissionTests.report_worker

    def test_single_text_mission_keeps_partial_report_and_completed_piece_count(self):
        self.report_worker("partial")
        original = self.worker.execute

        def execute(folder, *args):
            execution = original(folder, *args)
            document = json.loads((folder / "result.json").read_bytes())
            self.assertEqual(len(document["evidence"]), 1)
            criterion = document["evidence"][0]["criterion"]
            document["milestones"] = [
                {"criterion": criterion, "deliverable": title, "status": state,
                 "artifact_section": "report" if state == "met" else "remaining", "explanation": title}
                for title, state in (("Price comparison", "met"), ("Exclusion comparison", "met"), ("Recommendation", "unmet"))]
            document["outcome"]["progress_percent"] = 66
            (folder / "result.json").write_text(json.dumps(document), encoding="utf-8")
            return execution

        self.worker.execute = execute
        task = self.office.submit_mission({"mission": "Compare prices and exclusions and recommend the next step."})["task"]
        self.wait_run(task)
        detail = self.office.detail(task["id"])
        self.assertEqual(detail["task"]["status"], "review")
        self.assertEqual(detail["task"]["overview"]["progress_percent"], 66)
        self.assertIn("3개 중 2개 완료", detail["task"]["overview"]["progress_basis"])
        self.assertEqual(len(detail["report"]["milestones"]), 3)


if __name__ == "__main__":
    unittest.main()
