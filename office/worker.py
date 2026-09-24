"""Official installed Codex CLI adapter; no API client or credential file access."""
import ctypes
import json
import os
import re
import shutil
import signal
import subprocess
import threading
import time
from pathlib import Path


def _object(properties):
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


TEXT = {"type": "string"}
TEXTS = {"type": "array", "items": TEXT}
RESULT_SCHEMA = _object({
    "summary": TEXT,
    "confirmed_problems": {"type": "array", "items": _object({"statement": TEXT, "source": TEXT})},
    "assumptions": TEXTS,
    "mvp": _object({"name": TEXT, "rationale": TEXT}),
    "required_inputs": TEXTS, "expected_outputs": TEXTS, "validation_plan": TEXTS,
    "completion_criteria": TEXTS, "next_tasks": TEXTS,
    "evidence": {"type": "array", "items": _object({"criterion": TEXT, "artifact_section": TEXT, "explanation": TEXT})},
    "limitations": TEXTS,
})


def clean_env():
    allowed = {"PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "USERPROFILE", "HOMEDRIVE", "HOMEPATH",
               "APPDATA", "LOCALAPPDATA", "PROGRAMDATA", "PROGRAMFILES", "PROGRAMFILES(X86)", "TEMP", "TMP",
               "COMSPEC", "CODEX_HOME", "HOME", "LANG", "LC_ALL", "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY",
               "SSL_CERT_FILE", "SSL_CERT_DIR", "CURL_CA_BUNDLE"}
    return {k: v for k, v in os.environ.items() if k.upper() in allowed}


def redact(text):
    text = re.sub(r"\bsk-[A-Za-z0-9_-]{8,}", "[REDACTED]", text)
    text = re.sub(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", "[REDACTED]", text)
    text = re.sub(r"(?i)(bearer\s+)[A-Za-z0-9._~+/-]+=*", r"\1[REDACTED]", text)
    text = re.sub(r'(?i)(["\']?(?:access_token|refresh_token|id_token|api_key|authorization)["\']?\s*[:=]\s*)["\']?[^\s,}\r\n]+', r'\1"[REDACTED]"', text)
    return text


class _WindowsJob:
    """Kill the CLI and its descendants when the office process exits, even abruptly."""
    def __init__(self, process):
        from ctypes import wintypes as w

        class Basic(ctypes.Structure):
            _fields_ = [("ProcessUserTime", ctypes.c_int64), ("JobUserTime", ctypes.c_int64),
                        ("LimitFlags", w.DWORD), ("MinWorking", ctypes.c_size_t), ("MaxWorking", ctypes.c_size_t),
                        ("ActiveProcessLimit", w.DWORD), ("Affinity", ctypes.c_size_t),
                        ("PriorityClass", w.DWORD), ("SchedulingClass", w.DWORD)]

        class IO(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in ("ReadOp", "WriteOp", "OtherOp", "ReadBytes", "WriteBytes", "OtherBytes")]

        class Extended(ctypes.Structure):
            _fields_ = [("Basic", Basic), ("IO", IO), ("ProcessMemory", ctypes.c_size_t),
                        ("JobMemory", ctypes.c_size_t), ("PeakProcess", ctypes.c_size_t), ("PeakJob", ctypes.c_size_t)]

        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, w.LPCWSTR]
        self.kernel.CreateJobObjectW.restype = w.HANDLE
        self.kernel.SetInformationJobObject.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD]
        self.kernel.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
        self.kernel.CloseHandle.argtypes = [w.HANDLE]
        self.handle = self.kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise OSError("Windows Job 생성 실패")
        limits = Extended()
        limits.Basic.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)) or not self.kernel.AssignProcessToJobObject(self.handle, int(process._handle)):
            self.close()
            raise OSError("Windows Job 연결 실패")

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def render_report(result):
    parts = ["# 업무 산출물", "", result["summary"], "", "## 확인된 문제 · 제공된 배경 기준"]
    parts.extend(f"- {p['statement']}\n  - 출처: {p['source']}" for p in result["confirmed_problems"])
    sections = [("확인이 필요한 가정", "assumptions"), ("필요한 입력 데이터", "required_inputs"),
                ("기대 산출물", "expected_outputs"), ("실제 고객 업무 검증 계획 · 아직 미실시", "validation_plan"),
                ("완료 기준", "completion_criteria"), ("실행 가능한 후속 작업", "next_tasks"), ("한계와 미확인 사항", "limitations")]
    parts += ["", "## 선정한 첫 MVP 기능", "", "### " + result["mvp"]["name"], "", result["mvp"]["rationale"]]
    for title, field in sections:
        parts += ["", "## " + title, ""] + ["- " + item for item in result[field]]
    parts += ["", "## 완료 기준별 제출 근거", ""]
    for item in result["evidence"]:
        parts += ["- 기준: " + item["criterion"], "  - 위치: " + item["artifact_section"], "  - 근거: " + item["explanation"]]
    parts += ["", "---", "이 보고서는 AI 실행 산출물입니다. 기본 검증 후에도 대표의 내용 검토와 실제 고객 검증이 필요합니다.", ""]
    return "\n".join(parts)


class CodexWorker:
    provider = "codex"

    def __init__(self):
        self._cached_probe = None
        self._probe_time = 0.0
        self._probe_lock = threading.Lock()

    def executable(self):
        custom = os.environ.get("DAS_CODEX_PATH")
        candidate = custom or shutil.which("codex.exe" if os.name == "nt" else "codex")
        if candidate and Path(candidate).is_file():
            return str(Path(candidate).resolve())
        if custom:
            return None
        candidates = list((Path.home() / ".vscode/extensions").glob("openai.chatgpt-*/bin/windows-x86_64/codex.exe"))
        return str(max(candidates, key=lambda p: p.stat().st_mtime)) if candidates else None

    def probe(self, force=False):
        with self._probe_lock:
            if not force and self._cached_probe and time.monotonic() - self._probe_time < 20:
                return dict(self._cached_probe)
            result = {"provider": self.provider, "available": False, "auth_mode": "unknown", "version": None,
                      "message": "Codex 설치를 찾을 수 없습니다. 설치된 codex.exe 경로를 DAS_CODEX_PATH로 지정하세요."}
            executable = self.executable()
            if executable:
                try:
                    options = {"capture_output": True, "text": True, "encoding": "utf-8", "errors": "replace",
                               "timeout": 15, "env": clean_env(), "creationflags": subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0}
                    version = subprocess.run([executable, "--version"], **options)
                    auth = subprocess.run([executable, "login", "status"], **options)
                    auth_text = (auth.stdout + auth.stderr).lower()
                    is_chatgpt = auth.returncode == 0 and "logged in using chatgpt" in auth_text
                    result.update(version=redact(version.stdout.strip())[:100], available=is_chatgpt,
                                  auth_mode="chatgpt" if is_chatgpt else "not_chatgpt",
                                  message="실제 Codex · 기존 ChatGPT 로그인 사용" if is_chatgpt else
                                  "ChatGPT 로그인이 필요합니다. 터미널에서 codex login 후 브라우저 인증을 완료하세요. API 인증으로 전환하지 않습니다.")
                except (OSError, subprocess.TimeoutExpired):
                    result["message"] = "설치된 Codex를 실행할 수 없습니다. 로컬 codex --version 및 codex login status를 확인하세요."
            self._cached_probe, self._probe_time = result, time.monotonic()
            return dict(result)

    def execute(self, run_dir: Path, prompt: str, timeout_seconds: int, cancel_event: threading.Event):
        run_dir = run_dir.resolve()
        run_dir.mkdir(parents=True, exist_ok=True)
        prompt += "\n추가 출력 규칙: evidence.artifact_section에는 관련 JSON 최상위 필드명을 정확히 쓰세요(여러 개면 쉼표로 구분). 각 목록은 구체적인 내용을 최소 한 개 포함해야 합니다."
        (run_dir / "prompt.txt").write_text(prompt, encoding="utf-8")
        (run_dir / "schema.json").write_text(json.dumps(RESULT_SCHEMA, ensure_ascii=False, indent=2), encoding="utf-8")
        result = {"provider": "codex", "exit_code": -1, "completed": False, "error": None, "auth_mode": "chatgpt"}
        probe = self.probe(force=True)
        if not probe["available"]:
            result["error"] = probe["message"]
            return result
        result["cli_version"] = probe["version"]
        flags = ["shell_tool", "unified_exec", "apps", "plugins", "multi_agent", "multi_agent_v2", "hooks",
                 "browser_use", "browser_use_external", "computer_use", "in_app_browser", "image_generation",
                 "code_mode", "code_mode_host", "skill_search", "skill_mcp_dependency_install", "sleep_tool",
                 "view_image", "unbounded_connection_retries", "memories"]
        command = [self.executable(), "--no-daemon", "-a", "never", "exec", "--ignore-user-config", "--ignore-rules",
                   "--skip-git-repo-check", "--ephemeral", "--sandbox", "read-only", "--json", "--color", "never",
                   "--cd", str(run_dir), "--output-schema", str(run_dir / "schema.json"),
                   "--output-last-message", str(run_dir / "result.json"),
                   "-c", 'forced_login_method="chatgpt"', "-c", 'model_provider="openai"',
                   "-c", 'web_search="disabled"', "-c", "project_doc_max_bytes=0"]
        for flag in flags:
            command += ["-c", f"features.{flag}=false"]
        command += ["-"]
        process, job = None, None
        threads = []
        seen = {"completed": False, "failed": False, "too_large": False}
        started = time.monotonic()

        def consume(stream, path, is_events):
            total = 0
            with path.open("w", encoding="utf-8") as handle:
                while True:
                    line = stream.readline(2_000_001)
                    if not line:
                        break
                    total += len(line)
                    if total > 8_000_000 or len(line) > 2_000_000:
                        seen["too_large"] = True
                        continue
                    safe = redact(line.decode("utf-8", errors="replace"))
                    if is_events:
                        try:
                            event = json.loads(safe)
                            seen["completed"] |= event.get("type") == "turn.completed"
                            seen["failed"] |= event.get("type") in ("turn.failed", "error")
                        except json.JSONDecodeError:
                            safe = json.dumps({"type": "cli.notice", "message": safe}, ensure_ascii=False) + "\n"
                    handle.write(safe)
                    handle.flush()

        try:
            process = subprocess.Popen(command, cwd=run_dir, env=clean_env(), stdin=subprocess.PIPE,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                                       start_new_session=os.name != "nt")
            if os.name == "nt":
                job = _WindowsJob(process)
            for stream, filename, events in ((process.stdout, "events.jsonl", True), (process.stderr, "stderr.log", False)):
                thread = threading.Thread(target=consume, args=(stream, run_dir / filename, events), daemon=True)
                thread.start()
                threads.append(thread)
            def send_prompt():
                try:
                    process.stdin.write(prompt.encode("utf-8"))
                except (BrokenPipeError, OSError, ValueError):
                    pass
                finally:
                    if not process.stdin.closed:
                        try:
                            process.stdin.close()
                        except OSError:
                            pass

            writer = threading.Thread(target=send_prompt, daemon=True)
            writer.start()
            threads.append(writer)
            while process.poll() is None:
                if cancel_event.is_set() or time.monotonic() - started >= timeout_seconds or seen["too_large"]:
                    result["error"] = ("실행이 취소되었습니다." if cancel_event.is_set() else
                                       "실행 기록 크기 제한에 도달했습니다." if seen["too_large"] else "실행 시간 제한에 도달했습니다.")
                    if job:
                        job.close()
                    elif os.name != "nt":
                        os.killpg(process.pid, signal.SIGKILL)
                    else:
                        process.kill()
                    break
                cancel_event.wait(0.15)
            result["exit_code"] = process.wait(timeout=10)
            for thread in threads:
                thread.join(timeout=5)
            result["completed"] = result["exit_code"] == 0 and seen["completed"] and not seen["failed"] and not result["error"]
            if not result["completed"] and not result["error"]:
                result["error"] = f"Codex 실행 실패 (종료 코드 {result['exit_code']}). stderr.log와 events.jsonl을 확인하세요."
            output = run_dir / "result.json"
            if output.is_file():
                if output.stat().st_size > 2_000_000:
                    raise ValueError("응답 크기 제한 초과")
                safe = redact(output.read_text(encoding="utf-8"))
                output.write_text(safe, encoding="utf-8")
                report = render_report(json.loads(safe))
                (run_dir / "report.md").write_text(report, encoding="utf-8")
        except Exception as exc:
            result["completed"] = False
            result["error"] = f"Codex 실행/산출물 처리 오류 ({type(exc).__name__}). 실행 기록을 확인하세요."
        finally:
            if job:
                job.close()
            if process:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=10)
                for thread in threads:
                    thread.join(timeout=2)
                for stream in (process.stdin, process.stdout, process.stderr):
                    if stream and not stream.closed:
                        try:
                            stream.close()
                        except OSError:
                            pass
            result["duration_seconds"] = round(time.monotonic() - started, 2)
        return result
