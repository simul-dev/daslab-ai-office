"""PM decisions and CLI boundary, using Python fixtures instead of real AI calls."""
import copy
import json
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from office.pm import PM_DECISION_SCHEMA, decision_prompt, validate_decision
from office.worker import CodexWorker


def decision(action="delegate"):
    return {
        "action": action,
        "summary": "R&D가 승인된 화면 변경을 수행합니다.",
        "instruction": "기존 조직 UI에서 요청한 버튼을 수정하고 실제 변경과 검증 근거를 제출하세요.",
        "criteria": ["승인된 버튼 수정", "실제 동작 검증 근거"],
        "reason": "원래 목표를 수행할 파일 변경이 필요합니다.",
    }


class DecisionValidationTests(unittest.TestCase):
    def test_actions_are_stage_specific(self):
        for stage, allowed in (("plan", {"delegate", "accept", "blocked"}),
                               ("review", {"accept", "revise", "blocked"})):
            for action in ("delegate", "accept", "revise", "blocked"):
                with self.subTest(stage=stage, action=action):
                    item = decision(action)
                    if action in allowed:
                        self.assertIs(validate_decision(item, stage), item)
                    else:
                        with self.assertRaises(ValueError):
                            validate_decision(item, stage)

    def test_rejects_wrong_shape_and_types_without_coercion(self):
        invalid = [None, [], "{}", {**decision(), "authority": "push"}]
        for field in PM_DECISION_SCHEMA["required"]:
            missing = decision()
            del missing[field]
            invalid.append(missing)
            for value in (None, True, 1, {}, []):
                invalid.append({**decision(), field: value})
        for item in invalid:
            with self.subTest(item=item), self.assertRaises(ValueError):
                validate_decision(item, "plan")
        for stage in (None, [], "execute"):
            with self.subTest(stage=stage), self.assertRaises(ValueError):
                validate_decision(decision(), stage)

    def test_rejects_blank_and_oversized_content(self):
        for field, size in (("summary", 2000), ("instruction", 12000), ("reason", 6000)):
            for value in ("", "  \n ", "a" * (size + 1)):
                with self.subTest(field=field, size=len(value)), self.assertRaises(ValueError):
                    validate_decision({**decision(), field: value}, "plan")
        for criteria in ([], [" ", "valid"], [123], ["x"] * 21, ["x" * 1001], ("valid",)):
            with self.subTest(criteria=criteria), self.assertRaises(ValueError):
                validate_decision({**decision(), "criteria": criteria}, "plan")

    def test_prompt_preserves_input_and_separates_judgment_from_authority(self):
        context = {"stage": "review", "mission": {"text": "버튼 수정 후 실제 동작 확인"},
                   "evidence": {"browser": {"status": "unverified"}}}
        original = copy.deepcopy(context)
        prompt = decision_prompt(context)
        self.assertEqual(context, original)
        self.assertIn(json.dumps(context, ensure_ascii=False, indent=2), prompt)
        self.assertIn("accept / revise / blocked", prompt)
        self.assertIn("판단은 실행 권한이 아닙니다", prompt)
        self.assertIn("브라우저 확인이나 성공 결과를 꾸며 내지 마세요", prompt)
        self.assertIn("대표가 지시하지 않은 공개·발행·푸시 권한", prompt)
        self.assertIn("DAS-RD뿐", prompt)
        for invalid in (None, [], {}, {"stage": []}, {"stage": "publish"}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                decision_prompt(invalid)


class DecisionAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.worker = CodexWorker()
        self.original_popen = subprocess.Popen
        self.commands = []

    def execute_fixture(self, output, stage="plan"):
        def launch(command, **kwargs):
            self.commands.append(command)
            self.assertEqual(kwargs["cwd"], self.folder)
            self.assertNotIn("OPENAI_API_KEY", kwargs["env"])
            self.assertNotIn("CODEX_API_KEY", kwargs["env"])
            script = "import pathlib,sys,time; sys.stdin.buffer.read(); "
            if output is not None:
                script += f"pathlib.Path('result.json').write_text({json.dumps(output, ensure_ascii=True)!r}, encoding='utf-8'); "
            script += "print('{\"type\":\"turn.completed\"}', flush=True); time.sleep(0.3)"
            return self.original_popen([sys.executable, "-c", script], **kwargs)
        events = []
        with patch.object(self.worker, "probe", return_value={"available": True, "version": "fixture"}), \
             patch.object(self.worker, "executable", return_value="never-executed-codex"), \
             patch("office.worker.subprocess.Popen", side_effect=launch):
            result = self.worker.execute_decision(self.folder, {"stage": stage, "mission": {"text": "버튼 수정"}},
                                                  5, threading.Event(), on_event=events.append)
        return result, events

    def test_fixed_decision_schema_read_only_tools_disabled_subscription_only(self):
        result, events = self.execute_fixture(decision())
        self.assertTrue(result["completed"], result)
        self.assertEqual(result["auth_mode"], "chatgpt")
        self.assertEqual(events, [{"type": "turn.completed"}])
        command = self.commands[0]
        self.assertIn('--ignore-user-config', command)
        self.assertIn('--ignore-rules', command)
        self.assertEqual(command[command.index('--sandbox') + 1], "read-only")
        self.assertIn('forced_login_method="chatgpt"', command)
        self.assertIn('web_search="disabled"', command)
        self.assertIn('features.shell_tool=false', command)
        self.assertIn('features.browser_use=false', command)
        self.assertTrue(all(item.endswith("=false") for item in command if item.startswith("features.")))
        self.assertNotIn('--image', command)
        self.assertEqual(json.loads((self.folder / 'schema.json').read_text(encoding='utf-8')), PM_DECISION_SCHEMA)
        self.assertEqual(json.loads((self.folder / 'result.json').read_text(encoding='utf-8')), decision())
        self.assertFalse((self.folder / 'report.md').exists())
        prompt = (self.folder / 'prompt.txt').read_text(encoding='utf-8')
        self.assertNotIn("추가 출력 규칙", prompt)
        self.assertNotIn("현재 기능은 제공된 자료의 분석과 문서 작성까지", prompt)

    def test_review_attaches_only_fixed_server_capture_filenames(self):
        captures = [self.folder / name for name in ('review-desktop.png', 'review-mobile.png')]
        for capture in captures:
            capture.write_bytes(b'fixture PNG; decoded only by the mocked provider')
        (self.folder / 'other-image.png').write_bytes(b'not a server capture')
        result, _ = self.execute_fixture(decision('accept'), stage='review')
        self.assertTrue(result['completed'], result)
        command = self.commands[0]
        images = [command[index + 1] for index, item in enumerate(command) if item == '--image']
        self.assertEqual(images, [str(path) for path in captures])
        self.assertIn('features.view_image=false', command)
        self.assertIn('features.shell_tool=false', command)

    def test_review_without_captures_keeps_command_without_images(self):
        result, _ = self.execute_fixture(decision('accept'), stage='review')
        self.assertTrue(result['completed'], result)
        self.assertNotIn('--image', self.commands[0])

    def test_planning_does_not_attach_leftover_review_capture(self):
        (self.folder / 'review-desktop.png').write_bytes(b'fixture')
        result, _ = self.execute_fixture(decision(), stage='plan')
        self.assertTrue(result['completed'], result)
        self.assertNotIn('--image', self.commands[0])

    def test_review_rejects_capture_marked_as_symlink_before_launch(self):
        # Model the filesystem link flag without requiring Windows link privileges.
        capture = self.folder / 'review-desktop.png'
        capture.write_bytes(b'fixture')
        with patch.object(Path, 'is_symlink', lambda path: path == capture):
            with self.assertRaisesRegex(ValueError, '이미지'):
                self.execute_fixture(decision('accept'), stage='review')
        self.assertEqual(self.commands, [])

    def test_invalid_decision_is_execution_failure_despite_turn_completed(self):
        result, _ = self.execute_fixture(decision("delegate"), stage="review")
        self.assertFalse(result["completed"])
        self.assertIn("ValueError", result["error"])

    def test_missing_decision_is_execution_failure_despite_turn_completed(self):
        result, _ = self.execute_fixture(None)
        self.assertFalse(result["completed"])
        self.assertIn("ValueError", result["error"])

    def test_unavailable_auth_does_not_launch_a_decision(self):
        with patch.object(self.worker, "probe", return_value={"available": False, "message": "로그인 필요"}), \
             patch("office.worker.subprocess.Popen") as launch:
            result = self.worker.execute_decision(self.folder, {"stage": "plan"}, 5, threading.Event())
        launch.assert_not_called()
        self.assertFalse(result["completed"])
        self.assertEqual(result["error"], "로그인 필요")


if __name__ == "__main__":
    unittest.main()
