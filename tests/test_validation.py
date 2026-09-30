"""Reviewer checks saved evidence independently of a worker's completion claim."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from office.validation import validate_run


class CodeReviewerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.criteria = ["Separate facts and assumptions.", "Choose one first MVP."]
        self.execution = {"exit_code": 0, "completed": True, "error": None, "provider": "fake"}
        self.document = {
            "summary": "A fixture proposal for testing the reviewer.",
            "confirmed_problems": [{"statement": "Owner reports quotation comparison takes time.", "source": "Owner background"}],
            "assumptions": ["Comparable scopes can be collected; customer validation not yet conducted."],
            "mvp": {"name": "Quotation comparison", "rationale": "Uses one bounded existing workflow."},
            "required_inputs": ["Two existing quotations for the same site."],
            "expected_outputs": ["Comparable scope and exclusions table."],
            "validation_plan": ["Have the user check one actual customer project."],
            "completion_criteria": ["Each quotation value is traceable to a source row."],
            "next_tasks": ["Obtain an anonymized real quotation pair."],
            "limitations": ["No customer interview or savings estimate is claimed."],
            "evidence": [
                {"criterion": self.criteria[0], "artifact_section": "confirmed_problems, assumptions", "explanation": "Provided background and untested assumptions are separate."},
                {"criterion": self.criteria[1], "artifact_section": "mvp.name", "explanation": "One named MVP and its rationale."},
            ],
        }
        self.save_document()
        (self.folder / "report.md").write_text("# Fixture report\nA test proposal, not an actual AI run.\n", encoding="utf-8")
        (self.folder / "events.jsonl").write_text('{"type":"thread.started"}\n{"type":"turn.completed"}\n', encoding="utf-8")

    def save_document(self):
        (self.folder / "result.json").write_text(json.dumps(self.document), encoding="utf-8")

    def review(self):
        return validate_run(self.folder, self.execution, self.criteria, "franchise")

    def test_complete_structural_evidence_passes_but_always_requires_human_review(self):
        result = self.review()
        self.assertTrue(result["passed"])
        self.assertTrue(result["review_required"])
        self.assertTrue(all(check["passed"] for check in result["checks"]))
        self.assertEqual(result["project_id"], "franchise")
        self.assertEqual(result["source"], "reviewer:code")
        self.assertEqual({item["name"] for item in result["artifacts"]}, {"result.json", "report.md", "events.jsonl"})
        self.assertTrue(all(len(item["sha256"]) == 64 for item in result["artifacts"]))

    def test_missing_actual_completion_event_blocks_worker_success_claim(self):
        for contents in (None, '{"type":"thread.started"}\n', "invalid JSON\n"):
            with self.subTest(contents=contents):
                event_file = self.folder / "events.jsonl"
                if contents is None:
                    event_file.unlink()
                else:
                    event_file.write_text(contents, encoding="utf-8")
                self.assertFalse(self.review()["passed"])

    def test_empty_report_is_not_an_artifact_success(self):
        (self.folder / "report.md").write_text(" \n", encoding="utf-8")
        self.assertFalse(self.review()["passed"])

    def test_done_claim_without_substantive_structured_result_fails(self):
        self.document = {"summary": "Completed successfully."}
        self.save_document()
        (self.folder / "report.md").write_text("Completed successfully.", encoding="utf-8")
        self.assertFalse(self.review()["passed"])

    def test_missing_duplicate_or_changed_criterion_fails(self):
        original = copy.deepcopy(self.document["evidence"])
        cases = [original[:1], [original[0], original[0]],
                 [original[0], {**original[1], "criterion": "A different criterion"}]]
        for evidence in cases:
            with self.subTest(evidence=evidence):
                self.document["evidence"] = evidence
                self.save_document()
                self.assertFalse(self.review()["passed"])

    def test_nonexistent_section_reference_and_empty_required_strings_fail(self):
        original = copy.deepcopy(self.document)
        variants = [
            {**original, "summary": " "},
            {**original, "assumptions": []},
            {**original, "mvp": {"name": "MVP", "rationale": ""}},
            {**original, "confirmed_problems": [{"statement": "Reported problem", "source": ""}]},
            {**original, "next_tasks": [42]},
            {**original, "evidence": [{**original["evidence"][0], "artifact_section": "mvp.missing"}, original["evidence"][1]]},
        ]
        for document in variants:
            with self.subTest(document=document):
                self.document = document
                self.save_document()
                self.assertFalse(self.review()["passed"])

    def test_nonzero_exit_or_failed_cli_event_blocks_valid_files(self):
        for change in ({"exit_code": 1}, {"completed": False}, {"error": "Worker timed out"}):
            with self.subTest(change=change):
                original = self.execution
                self.execution = {**original, **change}
                self.assertFalse(self.review()["passed"])
                self.execution = original
        (self.folder / "events.jsonl").write_text('{"type":"turn.failed"}\n{"type":"turn.completed"}\n', encoding="utf-8")
        self.assertFalse(self.review()["passed"])


class MissionReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.criteria = ["Compare the supplied options.", "Recommend the first next step."]
        self.execution = {"exit_code": 0, "completed": True, "error": None}
        self.document = {
            "summary": "The supplied options favor the lower-cost pilot; begin with one existing customer.",
            "analysis": {"priority": "normal", "complexity": "simple", "rationale": "A bounded comparison of supplied information.",
                         "process": ["Compare supplied options", "Recommend one next step"]},
            "outcome": {"status": "achieved", "progress_percent": 100, "basis": "Both of the two comparison criteria are met."},
            "report": [{"title": "Comparison", "content": "Option A costs less in the supplied figures. Option B requires a larger initial team."},
                       {"title": "Next step", "content": "Ask one existing customer to try the Option A pilot."}],
            "accomplishments": ["Compared the two supplied options", "Recommended a pilot"],
            "remaining": [], "limitations": ["This is a document recommendation; no customer was contacted."],
            "evidence": [
                {"criterion": self.criteria[0], "status": "met", "artifact_section": "report[0].content", "explanation": "The comparison names each option and a tradeoff."},
                {"criterion": self.criteria[1], "status": "met", "artifact_section": "report[1].content", "explanation": "A concrete next step is included."},
            ],
        }
        (self.folder / "report.md").write_text("# Mission result\nA comparison and recommendation.\n", encoding="utf-8")
        (self.folder / "events.jsonl").write_text('{"type":"turn.completed"}\n', encoding="utf-8")

    def review(self):
        (self.folder / "result.json").write_text(json.dumps(self.document), encoding="utf-8")
        return validate_run(self.folder, self.execution, self.criteria, "franchise")

    def make_partial(self, unknown=False):
        self.document["evidence"][1].update(status="unknown" if unknown else "unmet", artifact_section="remaining")
        self.document["remaining"] = ["The next step requires information that was not supplied."]
        self.document["outcome"].update(status="partial", progress_percent=None if unknown else 50,
                                        basis="One of two criteria met; the other is not completed.")

    def test_generic_document_result_passes_without_an_owner_approval_gate(self):
        result = self.review()
        self.assertTrue(result["passed"])
        self.assertFalse(result["review_required"])
        self.assertEqual(result["outcome"]["status"], "achieved")
        self.assertEqual(result["assessment_source"], "ai_self_assessment")

    def test_partial_completion_is_valid_report_not_full_achievement(self):
        self.make_partial()
        result = self.review()
        self.assertTrue(result["passed"])
        self.assertEqual(result["outcome"], self.document["outcome"])

    def test_external_execution_cannot_be_replaced_by_a_plan_claim(self):
        self.criteria[1] = "Send the chosen proposal to the customer."
        self.document["evidence"][1]["criterion"] = self.criteria[1]
        self.make_partial()
        self.document["remaining"] = ["Sending is unavailable in this worker; the customer has not received a proposal."]
        self.document["outcome"]["status"] = "blocked"
        self.assertTrue(self.review()["passed"])
        self.document["outcome"].update(status="achieved", progress_percent=100)
        self.assertFalse(self.review()["passed"])

    def test_unknown_criterion_requires_unknown_progress(self):
        self.make_partial(unknown=True)
        self.assertTrue(self.review()["passed"])
        for progress in (0, 50, 90, 100):
            with self.subTest(progress=progress):
                self.document["outcome"]["progress_percent"] = progress
                self.assertFalse(self.review()["passed"])

    def test_progress_comes_from_criteria_not_an_arbitrary_estimate(self):
        self.make_partial()
        for progress in (0, 49, 51, 100, True, 50.0, "50", None):
            with self.subTest(progress=progress):
                self.document["outcome"]["progress_percent"] = progress
                self.assertFalse(self.review()["passed"])

    def test_partial_and_full_claims_must_match_the_criterion_states(self):
        self.document["outcome"]["status"] = "partial"
        self.assertFalse(self.review()["passed"])
        self.make_partial()
        self.document["outcome"]["status"] = "achieved"
        self.assertFalse(self.review()["passed"])

    def test_remaining_work_is_required_for_incomplete_reports(self):
        self.document["remaining"] = ["Still need to make the recommendation."]
        self.assertFalse(self.review()["passed"])
        self.make_partial()
        self.document["remaining"] = []
        self.assertFalse(self.review()["passed"])

    def test_empty_report_and_circular_evidence_do_not_pass(self):
        original = copy.deepcopy(self.document)
        for reference in ("outcome", "evidence[0]", "analysis", "report[99]", "report[0].missing", "limitations"):
            with self.subTest(reference=reference):
                self.document = copy.deepcopy(original)
                self.document["evidence"][0]["artifact_section"] = reference
                self.assertFalse(self.review()["passed"])
        self.document = copy.deepcopy(original)
        self.document["report"] = []
        self.assertFalse(self.review()["passed"])

    def test_duplicate_missing_and_unrecognized_evidence_status_fail(self):
        original = copy.deepcopy(self.document["evidence"])
        for evidence in (original[:1], [original[0], original[0]],
                         [original[0], {**original[1], "status": "done"}]):
            with self.subTest(evidence=evidence):
                self.document["evidence"] = evidence
                self.assertFalse(self.review()["passed"])

    def test_failed_execution_never_releases_a_success_outcome(self):
        self.execution["completed"] = False
        result = self.review()
        self.assertFalse(result["passed"])
        self.assertIsNone(result["outcome"])


if __name__ == "__main__":
    unittest.main()
