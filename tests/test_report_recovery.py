"""Explicit report classification repair uses fixture evidence, never a real run."""
import copy
import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from office.providers import CodeReviewer
from office.report_recovery import recover_future_actions, recover_report_references, restore_unstarted_report
from office.worker import render_report


MISSION_ID = "a" * 32
ATTEMPT_ID = "b" * 32
FUTURE = ["다음 개발 단계의 모델 구현", "개발 후 데모 검증"]


class FixtureEngine:
    def __init__(self, root):
        self.data_dir = root
        self.lock = threading.RLock()
        self.changed = threading.Condition(self.lock)
        self.db = sqlite3.connect(root / "organization.sqlite3")
        self.db.executescript("CREATE TABLE missions(id TEXT PRIMARY KEY,data TEXT); CREATE TABLE attempts(id TEXT PRIMARY KEY,data TEXT);")
        self.closed, self._active = False, None
        self.events = []

    def _get(self, table, record_id):
        return json.loads(self.db.execute(f"SELECT data FROM {table} WHERE id=?", (record_id,)).fetchone()[0])

    def _save(self, table, value):
        self.db.execute(f"INSERT INTO {table}(id,data) VALUES(?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data", (value["id"], json.dumps(value)))

    def _attempts(self, mission_id):
        return [json.loads(row[0]) for row in self.db.execute("SELECT data FROM attempts ORDER BY id") if json.loads(row[0])["mission_id"] == mission_id]

    def _event(self, *event):
        self.events.append(event)


class ReportRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.engine = FixtureEngine(self.root)
        self.addCleanup(self.engine.db.close)
        self.folder = self.root / "organization-runs" / ATTEMPT_ID
        self.folder.mkdir(parents=True)
        self.criteria = ["공식 자료 조사와 문제 정의"]
        self.document = {
            "summary": "합성 연구 보고. 실제 시장 검증 아님.",
            "analysis": {"priority": "normal", "complexity": "simple", "rationale": "테스트 문서", "process": ["근거 정리"]},
            "outcome": {"status": "achieved", "progress_percent": 100, "basis": "현재 조사 범위의 기준 충족이라는 합성 자체 판단"},
            "report": [{"title": "연구 산출물", "content": "합성 출처와 문제 정의 문서; 사업 성과 아님."}],
            "accomplishments": ["조사 초안 작성"], "remaining": FUTURE,
            "limitations": ["현장 데이터와 개발 결과를 검증하지 않음"], "milestones": [],
            "evidence": [{"criterion": self.criteria[0], "status": "met", "artifact_section": "report[0].content", "explanation": "합성 보고 본문"}]
        }
        self.execution = {"completed": True, "exit_code": 0, "error": None,
                          "web_search": {"enabled": True, "observed": True, "completed_count": 1}}
        self.write_original()

    def write_original(self):
        (self.folder / "result.json").write_text(json.dumps(self.document, ensure_ascii=False), encoding="utf-8")
        (self.folder / "report.md").write_text(render_report(self.document), encoding="utf-8")
        (self.folder / "events.jsonl").write_text('{"type":"turn.completed"}\n', encoding="utf-8")
        (self.folder / "execution.json").write_text(json.dumps(self.execution), encoding="utf-8")
        verification = CodeReviewer().review(self.folder, self.execution, self.criteria, "daslab")
        mission = {"id": MISSION_ID, "employee_id": "das-rd", "execution_mode": "research", "status": "failed",
                   "acceptance_criteria": self.criteria, "result": None, "verification": "not_verified", "error": "Original failure"}
        attempt = {"id": ATTEMPT_ID, "mission_id": MISSION_ID, "execution_mode": "research", "status": "failed",
                   "error": "Original failure", "verification": verification, "duration_seconds": 17.5,
                   "web_search": self.execution["web_search"], "number": 1}
        with self.engine.db:
            self.engine._save("missions", mission)
            self.engine._save("attempts", attempt)

    def recover(self, **changes):
        args = {"engine": self.engine, "mission_id": MISSION_ID, "expected_remaining": FUTURE,
                "review_note": "직접 본 두 항목은 조사 완료 뒤 다음 개발 단계이며 현재 조사 산출물 미완료가 아닙니다."}
        args.update(changes)
        return recover_future_actions(**args)

    def originals(self):
        return {path.name: path.read_bytes() for path in self.folder.iterdir() if path.is_file()}

    def test_explicit_field_reclassification_preserves_original_and_failure(self):
        before = self.originals()
        original_attempt = self.engine._get("attempts", ATTEMPT_ID)
        result = self.recover()
        self.assertTrue(result["verification"]["passed"])
        self.assertEqual(before, self.originals())
        mission = self.engine._get("missions", MISSION_ID)
        self.assertEqual(mission["status"], "review")
        self.assertEqual(mission["verification"], "structural_only")
        expected = copy.deepcopy(self.document)
        expected.update(remaining=[], next_actions=FUTURE)
        self.assertEqual(mission["result"], expected)
        corrected_attempt = self.engine._get("attempts", ATTEMPT_ID)
        provenance = corrected_attempt.pop("report_correction")
        self.assertEqual(original_attempt, corrected_attempt)
        self.assertEqual(provenance["operator"], "supervisor_review")
        corrected = Path(provenance["path"])
        self.assertEqual((corrected / "events.jsonl").read_bytes(), before["events.jsonl"])
        self.assertEqual((corrected / "execution.json").read_bytes(), before["execution.json"])
        self.assertEqual(json.loads((corrected / "result.json").read_text(encoding="utf-8")), expected)
        rendered = (corrected / "report.md").read_text(encoding="utf-8")
        self.assertIn("## 다음 단계", rendered)
        self.assertTrue(all(action in rendered for action in FUTURE))
        self.assertTrue((corrected / "provenance.json").is_file())
        self.assertEqual(len(self.engine._attempts(MISSION_ID)), 1)
        self.assertEqual(self.engine.events[-1][0], "report.corrected")

    def test_changed_original_hash_denies_before_creating_correction(self):
        (self.folder / "report.md").write_text("Changed after original verification", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "최초 검증 이후 변경"):
            self.recover()
        self.assertFalse((self.folder / "report-corrections").exists())
        self.assertEqual(self.engine._get("missions", MISSION_ID)["status"], "failed")

    def test_expected_remaining_must_match_exactly(self):
        with self.assertRaisesRegex(ValueError, "원문 목록이 일치"):
            self.recover(expected_remaining=["다른 다음 단계"])
        self.assertFalse((self.folder / "report-corrections").exists())

    def test_any_other_failed_check_denies(self):
        self.document["evidence"][0]["artifact_section"] = "missing"
        self.write_original()
        with self.assertRaisesRegex(ValueError, "유일한 실패"):
            self.recover()
        self.assertFalse((self.folder / "report-corrections").exists())

    def test_target_active_or_nonfailed_or_development_denied(self):
        for change in ({"status": "queued"}, {"status": "pausing"}, {"execution_mode": "development"}):
            self.write_original()
            with self.engine.db:
                mission = self.engine._get("missions", MISSION_ID)
                mission.update(change)
                self.engine._save("missions", mission)
            with self.assertRaises(ValueError):
                self.recover()
        self.write_original()
        self.engine._active = {"mission_id": MISSION_ID}
        with self.assertRaises(ValueError):
            self.recover()
        self.assertFalse((self.folder / "report-corrections").exists())

    def test_research_without_real_search_receipt_denied(self):
        self.execution["web_search"]["completed_count"] = 0
        self.write_original()
        with self.assertRaisesRegex(ValueError, "실제 웹 검색"):
            self.recover()

    def test_does_not_overwrite_existing_distinct_next_actions(self):
        self.document["next_actions"] = ["다른 기존 후속 계획"]
        self.write_original()
        with self.assertRaises(ValueError):
            self.recover()

    def test_invalid_input_and_missing_review_note_denied(self):
        for change in ({"mission_id": "../bad"}, {"expected_remaining": []}, {"expected_remaining": "bad"}, {"review_note": " "}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.recover(**change)
        self.assertFalse((self.folder / "report-corrections").exists())

    def test_correction_cannot_repeat_or_reset_attempt_count(self):
        self.recover()
        with self.assertRaises(ValueError):
            self.recover()
        self.assertEqual(self.engine._attempts(MISSION_ID)[0]["number"], 1)


class ReportReferenceRecoveryTests(unittest.TestCase):
    write_original = ReportRecoveryTests.write_original
    originals = ReportRecoveryTests.originals

    def setUp(self):
        ReportRecoveryTests.setUp(self)
        snippets = ["채널 조사", "콘텐츠 초안", "영상 기획", "검토 기준", "측정 계획"]
        self.criteria = [", ".join(snippets)]
        self.document["report"] = [{"title": f"결과 {index}", "content": f"합성 산출물 {snippet}"} for index, snippet in enumerate(snippets)]
        self.document["outcome"] = {"status": "partial", "progress_percent": None, "basis": "미확인 조건이 있어 산정하지 않았습니다."}
        self.document["remaining"] = ["실제 데모 화면 검증과 미확인 조건 해소"]
        self.document["evidence"][0].update(criterion=self.criteria[0], status="unknown")
        self.document["milestones"] = [
            {"criterion": snippet, "deliverable": snippet + " 결과", "status": "met" if index < 2 else "unknown",
             "artifact_section": f"report[{index}],report[0]", "explanation": "기존 자체 판단을 보존"}
            for index, snippet in enumerate(snippets)]
        self.expected = copy.deepcopy(self.document["milestones"])
        self.replacements = [{"criterion": self.criteria[0], "artifact_section": f"report[{index}].content"} for index in range(5)]
        self.write_original()

    def recover(self, **changes):
        args = {"engine": self.engine, "mission_id": MISSION_ID, "expected_milestones": self.expected,
                "replacements": self.replacements, "review_note": "다섯 원문·근거 본문을 직접 검토했으며 상태와 불확실성은 유지합니다."}
        args.update(changes)
        return recover_report_references(**args)

    def test_reference_repair_preserves_partial_unknown_claims_and_originals(self):
        before = self.originals()
        original_attempt = self.engine._get("attempts", ATTEMPT_ID)
        self.assertEqual({c["name"] for c in original_attempt["verification"]["checks"] if not c["passed"]},
                         {"세부 결과와 원문 기준 대응", "세부 결과 근거 위치", "달성 상태와 진행률"})
        result = self.recover()
        self.assertTrue(result["verification"]["passed"])
        corrected = self.engine._get("missions", MISSION_ID)["result"]
        self.assertEqual(corrected["outcome"], self.document["outcome"])
        self.assertEqual(corrected["remaining"], self.document["remaining"])
        self.assertEqual(corrected["report"], self.document["report"])
        expected = copy.deepcopy(self.document)
        for item, replacement in zip(expected["milestones"], self.replacements):
            item.update(replacement)
        self.assertEqual(corrected, expected)
        self.assertEqual(before, self.originals())
        after = self.engine._get("attempts", ATTEMPT_ID)
        after.pop("report_correction")
        self.assertEqual(after, original_attempt)

    def test_changed_expected_snapshot_denied(self):
        changed = copy.deepcopy(self.expected)
        changed[0]["explanation"] = "Different old text"
        with self.assertRaisesRegex(ValueError, "정확히 일치"):
            self.recover(expected_milestones=changed)
        self.assertFalse((self.folder / "report-corrections").exists())

    def test_status_escalation_or_extra_replacement_fields_denied(self):
        changed = copy.deepcopy(self.replacements)
        changed[2]["status"] = "met"
        with self.assertRaises(ValueError):
            self.recover(replacements=changed)
        self.assertFalse((self.folder / "report-corrections").exists())

    def test_new_criterion_or_noncontent_reference_denied(self):
        for field, value in (("criterion", "새 기준"), ("artifact_section", "report[0]"),
                             ("artifact_section", "report[99].content"), ("artifact_section", "remaining[0]")):
            with self.subTest(field=field, value=value):
                changed = copy.deepcopy(self.replacements)
                changed[0][field] = value
                with self.assertRaises(ValueError):
                    self.recover(replacements=changed)
        self.assertFalse((self.folder / "report-corrections").exists())

    def test_unrelated_error_and_modified_artifacts_denied(self):
        self.document["summary"] = ""
        self.write_original()
        with self.assertRaises(ValueError):
            self.recover()
        self.document["summary"] = "합성 보고"
        self.write_original()
        (self.folder / "result.json").write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "최초 검증 이후 변경"):
            self.recover()


class ReportRestorationTests(unittest.TestCase):
    write_original = ReportRecoveryTests.write_original
    originals = ReportRecoveryTests.originals
    daily_reason = "내부 일일 실행 제한에 도달했습니다. 한도를 확인한 뒤 직접 재개하세요. 유료 API로 전환하지 않습니다."

    def setUp(self):
        ReportRecoveryTests.setUp(self)
        self.engine.db.execute("CREATE TABLE events(id INTEGER PRIMARY KEY, data TEXT)")
        recover_future_actions(self.engine, MISSION_ID, FUTURE, "합성 후속 단계 분류 교정")
        self.defer_report(corrected=True)

    def defer_report(self, corrected):
        from office.standing import StandingOperations
        with self.engine.db:
            attempt = self.engine._get("attempts", ATTEMPT_ID)
            attempt.update(started_at="2026-09-30T17:00:00+09:00", ended_at="2026-09-30T17:05:00+09:00")
            if not corrected:
                attempt["status"] = "completed"
            self.engine._save("attempts", attempt)
            mission = self.engine._get("missions", MISSION_ID)
            if not corrected:
                mission.update(result=copy.deepcopy(self.document), status="review", verification="structural_only", error=None)
            mission["instructions"] = []
            self.engine._save("missions", mission)
            self.standing = object.__new__(StandingOperations)
            self.standing.engine = self.engine
            self.cycle = {"duties": {"simulation-research": {"mission_id": MISSION_ID}}}
            self.original_basis = self.standing._basis(self.cycle)
            self.expected = copy.deepcopy(mission["result"])
            mission.update(previous_result=mission.pop("result"), result=None, status="deferred", error=self.daily_reason,
                           verification="not_verified", ended_at=None)
            self.engine._save("missions", mission)
            self.engine.db.execute("DELETE FROM events")
            for index, (kind, stamp, message) in enumerate([
                    ("report.corrected" if corrected else "execution.review", "17:06:00", "Original report ready"),
                    ("owner.resume", "17:10:00", "Resume"), ("execution.deferred", "17:10:01", self.daily_reason)], 1):
                event = {"id": index, "mission_id": MISSION_ID, "type": kind,
                         "created_at": "2026-09-30T" + stamp + "+09:00", "message": message}
                self.engine.db.execute("INSERT INTO events VALUES(?,?)", (index, json.dumps(event)))

    def restore(self, **changes):
        args = {"engine": self.engine, "mission_id": MISSION_ID, "expected_attempt_id": ATTEMPT_ID,
                "review_note": "시작하지 못한 중복 실행 요청을 철회하고 동일한 검증 보고서를 복원합니다."}
        args.update(changes)
        return restore_unstarted_report(**args)

    def test_corrected_report_restores_identical_basis_without_attempt_or_file_changes(self):
        files, attempt = self.originals(), self.engine._get("attempts", ATTEMPT_ID)
        self.assertNotEqual(self.standing._basis(self.cycle), self.original_basis)
        result = self.restore()
        mission = self.engine._get("missions", MISSION_ID)
        self.assertEqual(mission["result"], self.expected)
        self.assertEqual(result["status"], "review")
        self.assertEqual(mission["verification"], "structural_only")
        self.assertEqual(self.standing._basis(self.cycle), self.original_basis)
        self.assertEqual(self.engine._get("attempts", ATTEMPT_ID), attempt)
        self.assertEqual(self.originals(), files)
        self.assertEqual(result["report_restoration"]["withdrawn_event_ids"], [2, 3])
        self.assertEqual(self.engine.events[-1][0], "report.restored")
        with self.assertRaises(ValueError):
            self.restore()

    def test_uncorrected_passed_report_and_partial_claims_are_preserved(self):
        self.document["outcome"] = {"status": "partial", "progress_percent": None, "basis": "불확실한 고객 효과"}
        self.document["evidence"][0]["status"] = "unknown"
        self.document["remaining"] = ["고객 효과 검증"]
        self.write_original()
        self.defer_report(corrected=False)
        self.restore()
        self.assertEqual(self.engine._get("missions", MISSION_ID)["result"], self.document)
        self.assertEqual(self.standing._basis(self.cycle), self.original_basis)

    def test_changed_previous_result_instruction_and_wrong_latest_attempt_are_denied(self):
        original = self.engine._get("missions", MISSION_ID)
        for changes in ({"previous_result": {"summary": "changed"}}, {"instructions": [{"text": "추가 업무"}]},
                        {"status": "queued"}, {"error": "다른 사유"}):
            with self.engine.db:
                self.engine._save("missions", {**original, **changes})
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.restore()
        with self.engine.db:
            self.engine._save("missions", original)
            latest = self.engine._get("attempts", ATTEMPT_ID)
            latest["id"] = "c" * 32
            self.engine._save("attempts", latest)
        with self.assertRaisesRegex(ValueError, "새 실행"):
            self.restore()

    def test_new_execution_or_other_intervention_events_are_denied(self):
        for kind in ("execution.started", "owner.instruct", "owner.reassign", "owner.cancel"):
            event = {"id": 4, "mission_id": MISSION_ID, "type": kind, "created_at": "2026-09-30T17:11:00+09:00"}
            with self.engine.db:
                self.engine.db.execute("INSERT OR REPLACE INTO events VALUES(4,?)", (json.dumps(event),))
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                self.restore()

    def test_modified_original_or_corrected_artifact_is_denied(self):
        correction = self.engine._get("attempts", ATTEMPT_ID)["report_correction"]
        for path in (self.folder / "report.md", Path(correction["path"]) / "report.md"):
            before = path.read_bytes()
            path.write_bytes(before + b"changed")
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, "변경"):
                self.restore()
            path.write_bytes(before)

    def test_corrected_execution_changed_during_validation_is_denied(self):
        correction = self.engine._get("attempts", ATTEMPT_ID)["report_correction"]
        target = Path(correction["path"]) / "execution.json"
        original_review = CodeReviewer.review
        def mutate(reviewer, *args):
            result = original_review(reviewer, *args)
            target.write_bytes(target.read_bytes() + b"\n ")
            return result
        with patch.object(CodeReviewer, "review", mutate):
            with self.assertRaisesRegex(ValueError, "검사 도중"):
                self.restore()
        self.assertEqual(self.engine._get("missions", MISSION_ID)["status"], "deferred")


if __name__ == "__main__":
    unittest.main()
