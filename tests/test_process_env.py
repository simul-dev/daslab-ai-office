"""Synthetic environment markers only; never inspect or print real secrets."""
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from office.auth import OfficeAuth
from office.browser_verification import BrowserVerifier
from office.delivery import GitDelivery
from office.development import DevelopmentWorkspace
from office.network_checks import NetworkVerifier
from office.process_env import child_env, clear_server_secrets, require_clean_process_env
from office.prototype_checks import PrototypeVerifier
from office.prototypes import PrototypeWorkspace
from office.worker import CodexWorker, clean_env


MARKERS = {
    "DAS_OFFICE_GITHUB_CLIENT_ID": "synthetic-client-only",
    "DAS_OFFICE_GITHUB_CLIENT_SECRET": "synthetic-secret-only",
    "das_office_github_future_token": "synthetic-extra-only",
    "TUNNEL_TOKEN": "synthetic-tunnel-only",
}
CUSTOM = {"OFFICE_TEST_LOGIN_ID": "synthetic-custom-client", "OFFICE_TEST_LOGIN_SECRET": "synthetic-custom-secret"}
CUSTOM_CONFIG = {"mode": "github", "public_origin": "https://office.example",
                 "allowed_github_ids": [1234], "client_id_env": "OFFICE_TEST_LOGIN_ID",
                 "client_secret_env": "OFFICE_TEST_LOGIN_SECRET"}


def probe_script(names, *, nested=False):
    """Emit only a boolean: no environment name/value is sent to logs."""
    script = "import json,os; print(json.dumps(bool(set(k.upper() for k in os.environ) & " + repr({k.upper() for k in names}) + ")))"
    if nested:
        script = "import subprocess,sys; subprocess.run([sys.executable,'-c'," + repr(script) + "],check=True)"
    return script


class ProcessEnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.real_run = subprocess.run
        self.real_popen = subprocess.Popen

    def test_copy_filters_case_insensitively_without_changing_parent_runtime(self):
        with patch.dict(os.environ, {**MARKERS, "OFFICE_TEST_ORDINARY": "kept"}):
            result = child_env()
            self.assertFalse({key.upper() for key in MARKERS} & {key.upper() for key in result})
            self.assertEqual(result["OFFICE_TEST_ORDINARY"], "kept")
            self.assertTrue(all(key in os.environ for key in MARKERS))
            self.assertFalse({key.upper() for key in MARKERS} & {key.upper() for key in clean_env()})

    def test_startup_cleanup_keeps_auth_object_and_removes_custom_names(self):
        with patch.dict(os.environ, {**MARKERS, **CUSTOM, "OFFICE_TEST_ORDINARY": "kept"}):
            auth = OfficeAuth(CUSTOM_CONFIG, http=Mock())
            clear_server_secrets(CUSTOM_CONFIG)
            self.assertFalse({key.upper() for key in (*MARKERS, *CUSTOM)} & {key.upper() for key in os.environ})
            self.assertEqual(os.environ["OFFICE_TEST_ORDINARY"], "kept")
            # OAuth still has its initialization values; no provider request.
            self.assertTrue(auth.begin_login()["location"].startswith("https://github.com/login/oauth/authorize?"))
            require_clean_process_env()
            with patch.dict(os.environ, CUSTOM):
                self.assertFalse(set(CUSTOM) & set(child_env()))

    def test_real_child_and_grandchild_receive_no_server_markers(self):
        with patch.dict(os.environ, MARKERS):
            control = self.real_run([sys.executable, "-c", probe_script(MARKERS)], capture_output=True, text=True, check=True)
            self.assertTrue(json.loads(control.stdout), "Probe must detect the deliberately inherited synthetic markers")
            for environment in (child_env(), clean_env()):
                for nested in (False, True):
                    with self.subTest(nested=nested):
                        result = self.real_run([sys.executable, "-c", probe_script(MARKERS, nested=nested)],
                                               env=environment, capture_output=True, text=True, check=True)
                        self.assertFalse(json.loads(result.stdout))

    def test_startup_cleanup_prevents_implicit_library_driver_inheritance(self):
        with patch.dict(os.environ, {**MARKERS, **CUSTOM}):
            clear_server_secrets(CUSTOM_CONFIG)
            result = self.real_run([sys.executable, "-c", probe_script((*MARKERS, *CUSTOM), nested=True)],
                                   capture_output=True, text=True, check=True)
            self.assertFalse(json.loads(result.stdout))

    def test_git_delivery_uses_filtered_env_before_actual_child_launch(self):
        checked = []
        def run(command, **kwargs):
            self.assertEqual(command[0], "git")
            self.assertEqual(kwargs["env"]["GIT_TERMINAL_PROMPT"], "0")
            self.assertNotIn("GIT_CONFIG_COUNT", kwargs["env"])
            result = self.real_run([sys.executable, "-c", probe_script(MARKERS, nested=True)], **kwargs)
            checked.append(json.loads(result.stdout))
            return result
        with patch.dict(os.environ, {**MARKERS, "GIT_CONFIG_COUNT": "1"}), \
             patch("office.delivery.subprocess.run", side_effect=run):
            GitDelivery(self.root)._git("status")
        self.assertEqual(checked, [False])

    def test_development_and_prototype_syntax_children_exclude_server_markers(self):
        static = self.root / "static"
        static.mkdir()
        (static / "office.html").write_text('<html><head><script src="/office.js" defer></script><link rel="stylesheet" href="/office.css"></head><body><main id="workspace"></main></body></html>')
        (static / "office.css").write_text("body{color:black}")
        (static / "office.js").write_text('"use strict";')
        development = DevelopmentWorkspace(self.root, self.root / "data")
        prototype = PrototypeWorkspace(self.root, self.root / "data")
        self.addCleanup(development.close)
        self.addCleanup(prototype.close)
        dev_folder, proto_folder = self.root / "data/runs/dev", self.root / "data/runs/proto"
        development.prepare(dev_folder)
        prototype.prepare(proto_folder)
        (dev_folder / "workspace/static/office.css").write_text("body{color:navy}")
        checked = []
        def run(command, **kwargs):
            self.assertEqual(command[1], "--check")
            # Execute a trusted Python probe, never the generated JS files.
            result = self.real_run([sys.executable, "-c", probe_script(MARKERS, nested=True)], **kwargs)
            checked.append(json.loads(result.stdout))
            return result
        with patch.dict(os.environ, MARKERS), \
             patch("office.development.shutil.which", return_value=sys.executable), \
             patch("office.development.subprocess.run", side_effect=run):
            self.assertTrue(development.finish(dev_folder)["ready"])
            self.assertTrue(prototype.finish(proto_folder)["ready"])
        self.assertEqual(checked, [False] * 4)

    def test_worker_pm_and_project_pm_keep_existing_whitelist_for_actual_children(self):
        marker = self.root / "observed.json"
        script = ("import pathlib,subprocess,sys,time; sys.stdin.buffer.read(); "
                  "r=subprocess.run([sys.executable,'-c'," + repr(probe_script(MARKERS)) + "],capture_output=True,text=True,check=True); "
                  "pathlib.Path(" + repr(str(marker)) + ").write_text(r.stdout); "
                  "print('{\"type\":\"turn.completed\"}',flush=True); time.sleep(.3)")
        def launch(command, **kwargs):
            return self.real_popen([sys.executable, "-c", script], **kwargs)
        worker = CodexWorker()
        with patch.dict(os.environ, MARKERS), \
             patch.object(worker, "probe", return_value={"available": True, "version": "offline"}), \
             patch.object(worker, "executable", return_value="never-executed-codex"), \
             patch("office.worker.subprocess.Popen", side_effect=launch):
            for mode in ("worker", "pm", "project"):
                with self.subTest(mode=mode):
                    if marker.exists():
                        marker.unlink()
                    folder = self.root / mode
                    if mode == "worker":
                        result = worker.execute(folder, "Offline environment check", 5, threading.Event())
                    elif mode == "pm":
                        result = worker.execute_decision(folder, {"stage": "plan", "mission": {"text": "offline"}}, 5, threading.Event())
                    else:
                        result = worker.execute_project_decision(folder, {"stage": "plan"}, 5, threading.Event())
                    self.assertEqual(result["exit_code"], 0)
                    self.assertFalse(json.loads(marker.read_text()))

    @unittest.skipUnless(importlib.util.find_spec("playwright"), "Optional browser runtime is not installed")
    def test_actual_playwright_driver_starts_with_clean_environment(self):
        from playwright.async_api import async_playwright
        real_create = asyncio.create_subprocess_exec
        observed = []
        async def create(*args, **kwargs):
            environment = kwargs.get("env", os.environ)
            observed.append(bool({key.upper() for key in (*MARKERS, *CUSTOM)} &
                                 {key.upper() for key in environment}))
            return await real_create(*args, **kwargs)
        async def start_driver():
            async with async_playwright() as runtime:
                self.assertEqual(runtime.chromium.name, "chromium")
        with patch.dict(os.environ, {**MARKERS, **CUSTOM}):
            clear_server_secrets(CUSTOM_CONFIG)
            with patch("asyncio.create_subprocess_exec", side_effect=create):
                asyncio.run(start_driver())
        self.assertEqual(observed, [False])

    @unittest.skipUnless(importlib.util.find_spec("playwright"), "Optional browser runtime is not installed")
    def test_all_verifiers_refuse_dirty_driver_environment_before_start(self):
        with patch.dict(os.environ, MARKERS), patch("playwright.async_api.async_playwright") as start:
            for verifier in (BrowserVerifier(), PrototypeVerifier(), NetworkVerifier()):
                with self.subTest(verifier=type(verifier).__name__), self.assertRaisesRegex(RuntimeError, "서버 인증 환경 정제"):
                    asyncio.run(verifier._run_browser("http://127.0.0.1:9001/", self.root, {}))
            start.assert_not_called()


if __name__ == "__main__":
    unittest.main()
