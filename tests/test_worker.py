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

from office.worker import CodexWorker, clean_env


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


if __name__ == "__main__":
    unittest.main()
