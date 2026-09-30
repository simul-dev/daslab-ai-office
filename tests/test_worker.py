"""Adapter regressions use Python sleep processes only; never execute real Codex."""
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from office.worker import CodexWorker, RESULT_SCHEMA, clean_env, render_report


class CodexAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.worker = CodexWorker()
        self.processes = []
        self.original_popen = subprocess.Popen
        self.addCleanup(self.stop_children)

    def stop_children(self):
        for process in self.processes:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream and not stream.closed:
                    stream.close()

    def launch_sleep(self, command, **kwargs):
        self.assertIn('forced_login_method="chatgpt"', command)
        self.assertIn("--ignore-user-config", command)
        self.assertIn("--sandbox", command)
        self.assertIn("read-only", command)
        self.assertNotIn("OPENAI_API_KEY", kwargs["env"])
        self.assertNotIn("CODEX_API_KEY", kwargs["env"])
        process = self.original_popen([sys.executable, "-c", "import time; time.sleep(60)"], **kwargs)
        self.processes.append(process)
        return process

    def test_clean_environment_excludes_api_credentials_and_provider_overrides(self):
        supplied = {
            "PATH": "fixture-runtime-path", "SYSTEMROOT": "fixture-system-root",
            "CODEX_HOME": "fixture-home", "OPENAI_API_KEY": "fixture-key-one",
            "CODEX_API_KEY": "fixture-key-two", "ANTHROPIC_API_KEY": "fixture-key-three",
            "OPENAI_BASE_URL": "fixture-override", "OPENAI_ORG_ID": "fixture-organization",
            "UNRELATED_SECRET": "fixture-secret",
        }
        with patch.dict(os.environ, supplied, clear=True):
            result = clean_env()
        self.assertEqual(set(result), {"PATH", "SYSTEMROOT", "CODEX_HOME"})

    def test_unavailable_authentication_does_not_start_an_execution_process(self):
        with patch.object(self.worker, "probe", return_value={"available": False, "message": "ChatGPT login required"}), \
             patch("office.worker.subprocess.Popen") as popen:
            result = self.worker.execute(self.folder, "A fixture task", 1, threading.Event())
        popen.assert_not_called()
        self.assertFalse(result["completed"])
        self.assertTrue(result["error"])
        prompt = (self.folder / "prompt.txt").read_text(encoding="utf-8")
        self.assertIn("unknown이 하나라도 있으면 null", prompt)
        self.assertIn("제안서나 실행 계획만 작성했다면 원래 미션을 달성했다고 하지 마세요", prompt)
        self.assertIn("실제 요청한 결과만 간결하게 나누세요", prompt)
        self.assertIn("품질 규칙을 그 자체가 요청한 결과가 아닌데 진행 항목으로 세지 마세요", prompt)
        self.assertIn("remaining에는 이번 미션 범위에서 아직 미완료한 일만", prompt)
        self.assertIn("다음 단계는 next_actions", prompt)
        self.assertIn("임의로 다음 단계로 옮겨 완료 처리하지 마세요", prompt)

    def test_development_enables_only_scoped_workspace_tools(self):
        workspace = self.folder / "workspace"
        workspace.mkdir()
        def launch(command, **kwargs):
            self.assertIn('default_permissions="office-development"', command)
            self.assertIn('permissions.office-development.extends=":workspace"', command)
            self.assertIn('permissions.office-development.network.enabled=false', command)
            self.assertNotIn('--sandbox', command)
            self.assertNotIn('danger-full-access', command)
            self.assertIn('features.code_mode_host=true', command)
            self.assertIn('features.shell_tool=true', command)
            if os.name == 'nt':
                self.assertIn('windows.sandbox="elevated"', command)
            self.assertIn('features.browser_use=false', command)
            self.assertEqual(kwargs['cwd'], workspace)
            process = self.original_popen([sys.executable, '-c', 'import time; time.sleep(60)'], **kwargs)
            self.processes.append(process)
            return process
        with patch.object(self.worker, 'probe', return_value={'available': True, 'version': 'test'}), \
             patch.object(self.worker, 'executable', return_value='never-executed-codex'), \
             patch('office.worker.subprocess.Popen', side_effect=launch):
            self.worker.execute(self.folder, 'Fixture', 1, threading.Event(), workspace_dir=workspace)

    def test_development_rejects_unrelated_workspace(self):
        with self.assertRaises(ValueError):
            self.worker.execute(self.folder, 'Fixture', 1, threading.Event(), workspace_dir=self.folder)

    def test_api_key_login_is_not_accepted_as_chatgpt_authentication(self):
        responses = [subprocess.CompletedProcess([], 0, stdout="codex-cli fixture", stderr=""),
                     subprocess.CompletedProcess([], 0, stdout="", stderr="Logged in using an API key")]
        with patch.object(self.worker, "executable", return_value="unused-codex"), \
             patch("office.worker.subprocess.run", side_effect=responses) as run:
            result = self.worker.probe(force=True)
        self.assertFalse(result["available"])
        self.assertEqual(result["auth_mode"], "not_chatgpt")
        self.assertEqual(run.call_args_list[1].args[0], ["unused-codex", "login", "status"])
        self.assertNotIn("OPENAI_API_KEY", run.call_args_list[1].kwargs["env"])

    def execute_sleep(self, prompt, timeout, cancel):
        with patch.object(self.worker, "probe", return_value={"available": True, "version": "test-only"}), \
             patch.object(self.worker, "executable", return_value="never-executed-codex"), \
             patch("office.worker.subprocess.Popen", side_effect=self.launch_sleep):
            return self.worker.execute(self.folder, prompt, timeout, cancel)

    def test_timeout_terminates_the_actual_stub_process(self):
        started = time.monotonic()
        result = self.execute_sleep("Small fixture input", 1, threading.Event())
        self.assertLess(time.monotonic() - started, 6)
        self.assertFalse(result["completed"])
        # Windows kill-on-job-close may report exit code 0; explicit timeout must win.
        self.assertIn("시간 제한", result["error"])
        self.assertEqual(len(self.processes), 1)
        self.assertIsNotNone(self.processes[0].poll())

    def test_cancellation_terminates_the_actual_stub_process(self):
        cancelled = threading.Event()
        timer = threading.Timer(0.4, cancelled.set)
        timer.start()
        self.addCleanup(timer.cancel)
        started = time.monotonic()
        result = self.execute_sleep("Small cancellation fixture", 20, cancelled)
        self.assertLess(time.monotonic() - started, 6)
        self.assertFalse(result["completed"])
        self.assertTrue(result["error"])
        self.assertIsNotNone(self.processes[0].poll())

    def test_large_blocked_stdin_cannot_bypass_timeout(self):
        result = []

        def execute():
            result.append(self.execute_sleep("Large fixture input. " * 5000, 1, threading.Event()))

        thread = threading.Thread(target=execute, daemon=True)
        thread.start()
        thread.join(timeout=6)
        if thread.is_alive():
            self.stop_children()
            thread.join(timeout=3)
            self.fail("Blocked stdin write prevented the execution timeout")
        self.assertEqual(len(result), 1)
        self.assertFalse(result[0]["completed"])
        self.assertIsNotNone(self.processes[0].poll())

    def test_large_blocked_stdin_cannot_bypass_cancellation(self):
        cancelled = threading.Event()
        timer = threading.Timer(0.4, cancelled.set)
        timer.start()
        self.addCleanup(timer.cancel)
        result = []

        def execute():
            result.append(self.execute_sleep("Large cancellation fixture. " * 5000, 20, cancelled))

        thread = threading.Thread(target=execute, daemon=True)
        thread.start()
        thread.join(timeout=6)
        if thread.is_alive():
            self.stop_children()
            thread.join(timeout=3)
            self.fail("Blocked stdin write prevented cancellation")
        self.assertEqual(len(result), 1)
        self.assertFalse(result[0]["completed"])
        self.assertIsNotNone(self.processes[0].poll())


class ReportRenderingTests(unittest.TestCase):
    def test_generic_report_leads_with_outcome_and_keeps_internal_paths_out_of_prose(self):
        document = {
            "summary": "자료를 비교해 첫 실행안을 정리했습니다.",
            "analysis": {"priority": "normal", "complexity": "simple", "rationale": "제공된 자료 두 건의 비교입니다.", "process": ["자료 비교", "실행안 작성"]},
            "outcome": {"status": "partial", "progress_percent": 50, "basis": "2개 기준 중 1개 충족. 실제 발송은 수행하지 않았습니다."},
            "report": [{"title": "권고안", "content": "A안을 우선 검토하세요. 초기 비용이 더 낮습니다."}],
            "accomplishments": ["자료 비교"], "remaining": ["고객에게 발송"], "limitations": ["발송 기능 없음"],
            "evidence": [{"criterion": "자료 비교", "status": "met", "artifact_section": "report[0].content", "explanation": "두 안의 비용을 비교했습니다."}],
        }
        report = render_report(document, 12.34)
        self.assertIn("일부 달성 · 진행률 50%", report)
        self.assertIn("처리 시간: 12.3초", report)
        self.assertIn("## 권고안\n\nA안을", report)
        self.assertIn("<summary>처리 판단과 근거 보기</summary>", report)
        self.assertNotIn("report[0].content", report)
        self.assertNotIn("대표의 내용 검토", report)
        self.assertNotIn("## 다음 단계", report)
        document["next_actions"] = ["다음 개발 회차에서 비교 데모를 구현합니다."]
        updated = render_report(document)
        self.assertIn("## 다음 단계\n\n- 다음 개발 회차", updated)
        self.assertEqual(document["remaining"], ["고객에게 발송"])
        document["milestones"] = [{"criterion": "자료 비교", "deliverable": "견적 가격 비교", "status": "met",
                                   "artifact_section": "report", "explanation": "견적 가격을 비교했습니다."}]
        self.assertIn("견적 가격 비교 — 충족", render_report(document))
        document["outcome"].update(status="blocked", progress_percent=None)
        self.assertIn("진행 막힘 · 진행률 산정 불가", render_report(document))

    def test_schema_does_not_require_a_specific_business_mvp_for_every_mission(self):
        fields = RESULT_SCHEMA["properties"]
        self.assertIn("analysis", fields)
        self.assertIn("report", fields)
        self.assertIn("outcome", fields)
        self.assertIn("milestones", fields)
        self.assertIn("next_actions", fields)
        self.assertEqual(fields["next_actions"], {"type": "array", "items": {"type": "string"}})
        self.assertIn("next_actions", RESULT_SCHEMA["required"])
        self.assertIn("milestones", RESULT_SCHEMA["required"])
        self.assertNotIn("mvp", fields)
        self.assertNotIn("required_inputs", fields)
        self.assertEqual(fields["outcome"]["properties"]["progress_percent"]["type"], ["integer", "null"])


if __name__ == "__main__":
    unittest.main()
