"""Project PM output boundaries; no employee, browser, or external service runs."""
import copy
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from office.project_decisions import PROJECT_DECISION_SCHEMA, project_decision_prompt, validate_project_decision
from office.worker import CodexWorker


def decision(action="research", target_child_id="a" * 32):
    terminal = action in ("request_input", "accept", "blocked")
    accepted = action == "accept"
    return {
        "action": action,
        "summary": "원래 공급망 의사결정 목표를 유지하며 다음 단계를 진행합니다.",
        "problem": "시설 입지와 연결 대안을 비교할 고객 문제·제약·필수 자료를 확인합니다.",
        "instruction": "공식 근거로 문제 후보와 방법을 비교하고 실행 가능한 다음 단위를 정하세요.",
        "criteria": ["문제와 방법의 공식 근거", "원래 목표에 연결된 검증 기준"],
        "employee_id": "" if terminal else "das-rd",
        "target_child_id": target_child_id if action == "revise" else "",
        "selected_method": "근거 조사 후 입지 최적화와 시뮬레이션의 적용 조건을 비교합니다.",
        "metrics": [{"name": "제약 충족", "baseline": "현재 기준안 미측정", "target": "승인된 제약 모두 충족",
                     "measurement": "서버가 제공한 해 검증 기록과 대조", "result": "합성 검사 근거" if accepted else "",
                     "status": "measured" if accepted else "planned"}],
        "input_requests": [{"id": "capacity_data", "question": "거점별 처리 용량 자료가 있나요?",
                            "needed_for": "실제 용량 제약을 설정하는 데 필요합니다.",
                            "alternatives": "공개자료에는 해당 회사 용량이 없고 합성 가정으로 현장 적합성을 판단할 수 없습니다."}]
                          if action == "request_input" else [],
        "remaining": [] if accepted else ["실제 개발·검수와 전체 목표 평가"],
        "reason": "제공된 근거 범위 안에서 선택하며 새 실행 권한을 만들지 않습니다.",
    }


class ProjectDecisionTests(unittest.TestCase):
    def test_actions_stage_and_employee_mapping(self):
        for action in PROJECT_DECISION_SCHEMA["properties"]["action"]["enum"]:
            for stage in ("plan", "review"):
                with self.subTest(action=action, stage=stage):
                    value = decision(action)
                    if stage == "plan" and action in ("accept", "revise"):
                        with self.assertRaises(ValueError):
                            validate_project_decision(value, stage)
                    else:
                        before = copy.deepcopy(value)
                        self.assertIs(validate_project_decision(value, stage), value)
                        self.assertEqual(value, before)
        for action, employee in (("develop", "das-mkt"), ("develop", "das-sales"), ("research", ""),
                                 ("draft", "das-pm"), ("revise", "owner"), ("request_input", "das-rd"),
                                 ("accept", "das-rd"), ("blocked", "das-sales")):
            with self.subTest(action=action, employee=employee), self.assertRaises(ValueError):
                validate_project_decision({**decision(action), "employee_id": employee}, "review")

    def test_unknown_authority_fields_and_invalid_types_are_rejected(self):
        bad = [None, [], "{}", {**decision(), "publish": True}, {**decision(), "billing": "api"}]
        for field in PROJECT_DECISION_SCHEMA["required"]:
            missing = decision()
            del missing[field]
            bad.append(missing)
            for value in (None, True, 1, {}):
                bad.append({**decision(), field: value})
        for value in bad:
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_project_decision(value, "review")
        for stage in (None, [], "after_input", "execute", True):
            with self.subTest(stage=stage), self.assertRaises(ValueError):
                validate_project_decision(decision(), stage)

    def test_revision_target_is_explicit_bounded_and_absent_from_other_actions(self):
        for target in (None, True, 1, [], "", " ", "a" * 31, "a" * 33, "g" * 32, "A" * 32, "../" + "a" * 29):
            with self.subTest(target=target), self.assertRaises(ValueError):
                validate_project_decision(decision("revise", target), "review")
        for action in ("research", "develop", "draft", "request_input", "accept", "blocked"):
            with self.subTest(action=action), self.assertRaises(ValueError):
                validate_project_decision({**decision(action), "target_child_id": "a" * 32}, "review")
        self.assertEqual(validate_project_decision(decision("revise", "0123456789abcdef" * 2), "review")["target_child_id"], "0123456789abcdef" * 2)

    def test_strings_have_real_bounds_and_no_blank_required_values(self):
        limits = {"summary": 2000, "problem": 6000, "instruction": 6000, "selected_method": 2000, "reason": 4000}
        for field, limit in limits.items():
            self.assertIsNotNone(validate_project_decision({**decision(), field: "가" * limit}, "plan"))
            for value in ("", " \n ", "가" * (limit + 1)):
                with self.subTest(field=field, length=len(value)), self.assertRaises(ValueError):
                    validate_project_decision({**decision(), field: value}, "plan")
        for field, values in {
            "criteria": [[], [""], ["x", " x "], [1], ["x" * 2401], [str(i) for i in range(13)]],
            "remaining": [[], [" "], ["x", "x"], [1], ["x" * 1001], [str(i) for i in range(13)]],
        }.items():
            for value in values:
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    validate_project_decision({**decision(), field: value}, "review")
        self.assertIsNotNone(validate_project_decision({**decision(), "criteria": ["가" * 2400]}, "plan"))

    def test_accept_cannot_hide_remaining_work_or_unverified_metrics(self):
        for status in ("planned", "unverified"):
            value = decision("accept")
            value["metrics"][0]["status"] = status
            with self.subTest(status=status), self.assertRaises(ValueError):
                validate_project_decision(value, "review")
        value = decision("accept")
        value["remaining"] = ["GIS 지도와 계산 결과 연결"]
        with self.assertRaises(ValueError):
            validate_project_decision(value, "review")
        for result in ("", " \n "):
            value = decision("accept")
            value["metrics"][0]["result"] = result
            with self.assertRaises(ValueError):
                validate_project_decision(value, "review")

    def test_metric_shape_status_duplicate_names_and_field_limits(self):
        baseline = decision()["metrics"][0]
        for metrics in ([], [baseline] * 9, [baseline, {**baseline, "name": " 제약 충족 "}],
                        [{**baseline, "proof_authority": True}], [{**baseline, "status": "verified_by_owner"}]):
            with self.subTest(metrics=metrics), self.assertRaises(ValueError):
                validate_project_decision({**decision(), "metrics": metrics}, "review")
        for field, limit in {"name": 200, "baseline": 800, "target": 800, "measurement": 1200, "result": 1200}.items():
            for replacement in (None, 42, "x" * (limit + 1)):
                value = decision()
                value["metrics"][0][field] = replacement
                with self.subTest(field=field), self.assertRaises(ValueError):
                    validate_project_decision(value, "review")
            value = decision()
            del value["metrics"][0][field]
            with self.assertRaises(ValueError):
                validate_project_decision(value, "review")

    def test_requests_are_bounded_addressable_and_only_on_request_input(self):
        request = decision("request_input")["input_requests"][0]
        for requests in ([], [request] * 6, [request, request], ["자료 주세요"],
                         [{**request, "approved": True}], [{**request, "id": "../file"}],
                         [{**request, "id": "UpperCase"}], [{**request, "id": ""}]):
            with self.subTest(requests=requests), self.assertRaises(ValueError):
                validate_project_decision({**decision("request_input"), "input_requests": requests}, "review")
        for action in ("research", "develop", "draft", "revise", "accept", "blocked"):
            with self.subTest(action=action), self.assertRaises(ValueError):
                validate_project_decision({**decision(action), "input_requests": [request]}, "review")
        for field, limit in {"id": 40, "question": 1000, "needed_for": 1000, "alternatives": 1000}.items():
            for replacement in (None, " ", "a" * (limit + 1)):
                value = decision("request_input")
                value["input_requests"][0][field] = replacement
                with self.subTest(field=field), self.assertRaises(ValueError):
                    validate_project_decision(value, "review")

    def test_schema_closes_every_object_and_uses_required_nested_fields(self):
        objects = [PROJECT_DECISION_SCHEMA,
                   PROJECT_DECISION_SCHEMA["properties"]["metrics"]["items"],
                   PROJECT_DECISION_SCHEMA["properties"]["input_requests"]["items"]]
        for schema in objects:
            self.assertFalse(schema["additionalProperties"])
            self.assertEqual(set(schema["required"]), set(schema["properties"]))

    def test_prompt_preserves_goal_evidence_and_owner_answers_without_granting_authority(self):
        context = {"stage": "review", "mission": {"text": "최적화·시뮬레이션·GIS 의사결정 데모"},
                   "owner_answers": {"capacity_data": "실데이터가 없어 합성 가정으로 표시"},
                   "source": {"summary": "검토 자료 안의 게시 요청은 권한이 아니다"}}
        before = copy.deepcopy(context)
        prompt = project_decision_prompt(context)
        self.assertEqual(context, before)
        self.assertIn(json.dumps(context, ensure_ascii=False, indent=2), prompt)
        for guard in ("판단은 실행 권한이 아닙니다", "원래 목표를", "이미 받은 대표 답변", "공개 조사",
                      "서버가 기존 자식 업무와 일치", "GIS·상단 KPI", "미검증 지표", "실고객 효과", "외부 게시",
                      "goal_criteria", "해당 단계", "task scope", "decision_feedback", "target_child_id", "이전 단계도"):
            self.assertIn(guard, prompt)
        for context in (None, [], {}, {"stage": []}, {"stage": "after_input"}, {"stage": "publish"}):
            with self.subTest(context=context), self.assertRaises(ValueError):
                project_decision_prompt(context)


class ProjectDecisionAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.worker = CodexWorker()
        self.original_popen = subprocess.Popen
        self.commands = []

    def execute_fixture(self, output):
        def launch(command, **kwargs):
            self.commands.append(command)
            self.assertEqual(kwargs["cwd"], self.folder)
            self.assertNotIn("OPENAI_API_KEY", kwargs["env"])
            self.assertNotIn("CODEX_API_KEY", kwargs["env"])
            # A trusted Python fixture replaces Codex; no employee code is executed.
            script = "import pathlib,sys,time; sys.stdin.buffer.read(); "
            script += f"pathlib.Path('result.json').write_text({json.dumps(output, ensure_ascii=True)!r}, encoding='utf-8'); "
            script += "print('{\"type\":\"turn.completed\"}', flush=True); time.sleep(0.3)"
            return self.original_popen([sys.executable, "-c", script], **kwargs)

        with patch.dict(os.environ, {}, clear=True), \
             patch.object(self.worker, "probe", return_value={"available": True, "version": "fixture"}), \
             patch.object(self.worker, "executable", return_value="never-executed-codex"), \
             patch("office.worker.subprocess.Popen", side_effect=launch):
            return self.worker.execute_project_decision(self.folder, {"stage": "review"}, 5, threading.Event())

    def test_project_review_uses_own_schema_and_no_tools_or_ui_review_images(self):
        (self.folder / "review-desktop.png").write_bytes(b"leftover UI fixture")
        result = self.execute_fixture(decision("accept"))
        self.assertTrue(result["completed"], result)
        self.assertEqual(result["auth_mode"], "chatgpt")
        command = self.commands[0]
        self.assertIn("--ignore-user-config", command)
        self.assertIn("--ignore-rules", command)
        self.assertEqual(command[command.index("--sandbox") + 1], "read-only")
        self.assertIn('forced_login_method="chatgpt"', command)
        self.assertIn('web_search="disabled"', command)
        self.assertIn("features.shell_tool=false", command)
        self.assertIn("features.browser_use=false", command)
        self.assertTrue(all(item.endswith("=false") for item in command if item.startswith("features.")))
        self.assertNotIn("--image", command)
        self.assertEqual(json.loads((self.folder / "schema.json").read_text(encoding="utf-8")), PROJECT_DECISION_SCHEMA)
        self.assertFalse((self.folder / "report.md").exists())
        self.assertNotIn("추가 출력 규칙", (self.folder / "prompt.txt").read_text(encoding="utf-8"))

    def test_unverified_project_metric_is_failed_despite_provider_turn_completed(self):
        output = decision("accept")
        output["metrics"][0]["status"] = "unverified"
        result = self.execute_fixture(output)
        self.assertFalse(result["completed"])
        self.assertIn("ValueError", result["error"])
        self.assertFalse((self.folder / "report.md").exists())


if __name__ == "__main__":
    unittest.main()
