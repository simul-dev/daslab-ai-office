"""Run a single-user local office: python server.py"""
import argparse
import json
import os
import re
import shutil
import stat
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from office.service import Office
from office.store import Conflict
from office.report_recovery import recover_future_actions, recover_report_references, restore_unstarted_report
from office.prototype_recovery import recover_prototype
from office.prototype_capture import capture_prototype_repair_source
from office.auth import AuthError, OfficeAuth, auth_failure_reason
from office.process_env import clear_server_secrets
from office.intake import route_intake

ROOT = Path(__file__).resolve().parent

VOICE_TYPES = {
    ".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8", ".mjs": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8", ".wasm": "application/wasm",
    ".onnx": "application/octet-stream", ".txt": "text/plain; charset=utf-8",
    ".md": "text/plain; charset=utf-8", ".wav": "audio/wav",
}

BRAND_TYPES = {
    ".svg": "image/svg+xml", ".png": "image/png", ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg", ".webp": "image/webp", ".gif": "image/gif",
    ".ico": "image/x-icon", ".woff2": "font/woff2",
}


def brand_asset(request_path):
    """Resolve public brand assets without following links out of the static tree."""
    try:
        relative = unquote(request_path[len("/brand/"):], errors="strict")
        parts = relative.split("/")
        if any(not part or part.startswith(".") or part.endswith((".", " ")) for part in parts):
            return None
        if re.search(r'[\\\x00-\x1f<>:"|?*]', relative):
            return None
        project = ROOT.resolve(strict=True)
        root = project / "static" / "brand"
        path = root.joinpath(*parts)
        resolved_root, resolved = root.resolve(strict=True), path.resolve(strict=True)
        if (not resolved_root.is_relative_to(project) or not resolved.is_relative_to(resolved_root)
                or resolved.suffix.lower() not in BRAND_TYPES or not resolved.is_file()):
            return None
        current = project
        for part in path.relative_to(project).parts:
            current = current / part
            info = current.lstat()
            # OneDrive cloud placeholders are reparse points too; only redirection
            # tags are links. Do not reject ordinary cloud-backed brand files.
            if (stat.S_ISLNK(info.st_mode)
                    or getattr(info, "st_reparse_tag", 0) in (0xA0000003, 0xA000000C)
                    or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_HIDDEN):
                return None
        return resolved, BRAND_TYPES[resolved.suffix.lower()]
    except (OSError, UnicodeError, ValueError):
        return None


def voice_asset(request_path):
    """Resolve only public, allowlisted files beneath the local voice app."""
    try:
        relative = "index.html" if request_path in ("/voice", "/voice/") else unquote(
            request_path[len("/voice/"):], errors="strict")
        parts = relative.split("/")
        if any(not part or part.startswith(".") or part.endswith((".", " ")) for part in parts):
            return None
        if re.search(r'[\\\x00-\x1f<>:"|?*]', relative):
            return None
        root = (ROOT / "static" / "voice").resolve()
        path = root.joinpath(*parts).resolve()
        if not path.is_relative_to(root) or path.suffix.lower() not in VOICE_TYPES or not path.is_file():
            return None
        current = root
        for part in path.relative_to(root).parts:
            current = current / part
            if part.startswith(".") or getattr(current.stat(), "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_HIDDEN:
                return None
        return path, VOICE_TYPES[path.suffix.lower()]
    except (OSError, UnicodeError, ValueError):
        return None


class InstanceLock:
    def __init__(self, data_dir):
        data_dir.mkdir(parents=True, exist_ok=True)
        self.file = (data_dir / "server.lock").open("a+b")
        self.file.seek(0)
        self.file.write(b"0")
        self.file.flush()
        self.file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            raise RuntimeError("이 데이터 폴더의 DAS Lab AI Office가 이미 실행 중입니다.") from None

    def close(self):
        self.file.close()


class OfficeHTTPServer(ThreadingHTTPServer):
    """Bound slow or excessive browser connections on either loopback listener."""

    daemon_threads = True
    request_queue_size = 64
    max_active_requests = 48

    def __init__(self, *args, **kwargs):
        self._request_slots = threading.BoundedSemaphore(self.max_active_requests)
        super().__init__(*args, **kwargs)

    def process_request(self, request, client_address):
        if not self._request_slots.acquire(blocking=False):
            try:
                request.sendall(b"HTTP/1.1 503 Service Unavailable\r\nConnection: close\r\nContent-Length: 0\r\n\r\n")
            except OSError:
                pass
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._request_slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._request_slots.release()


def handler_for(office, organization=None, auth=None):
    auth = auth or OfficeAuth()
    stream_slots = threading.BoundedSemaphore(12)

    class Handler(BaseHTTPRequestHandler):
        server_version = "DASLabOffice/0.1"

        def setup(self):
            self.request.settimeout(15)
            super().setup()

        def log_message(self, fmt, *args):
            # No request bodies, auth values or sensitive query strings in server log.
            pass

        def security_headers(self, voice=False):
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            script = "script-src 'self' 'wasm-unsafe-eval'" if voice else "script-src 'self'"
            worker = "; worker-src 'self' blob:" if voice else ""
            self.send_header("Content-Security-Policy", "default-src 'self'; " + script + "; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'" + worker)
            if voice:
                self.send_header("Permissions-Policy", "microphone=(self), camera=()")

        def respond(self, status, data, content_type="application/json; charset=utf-8", *, voice=False, cookies=()):
            if not isinstance(data, bytes):
                data = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            for cookie in cookies:
                self.send_header("Set-Cookie", cookie)
            self.security_headers(voice=voice)
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

        def respond_voice_file(self, path, content_type):
            # Model downloads can be much larger than ordinary HTML assets.
            with path.open("rb") as stream:
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(os.fstat(stream.fileno()).st_size))
                self.security_headers(voice=True)
                self.end_headers()
                try:
                    shutil.copyfileobj(stream, self.wfile, length=128 * 1024)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass

        def redirect(self, location, cookies=()):
            self.send_response(303)
            self.send_header("Location", location)
            self.send_header("Content-Length", "0")
            for cookie in cookies:
                self.send_header("Set-Cookie", cookie)
            self.security_headers()
            self.end_headers()

        def stream_organization(self):
            if not stream_slots.acquire(blocking=False):
                self.respond(503, {"error": "열린 조직 화면이 많습니다. 다른 탭을 닫고 다시 연결해 주세요."})
                return
            try:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.security_headers()
                self.end_headers()
                previous = None
                while not organization.closed:
                    if auth.current_user(self.headers.get("Cookie", "")) is None:
                        self.wfile.write(b"event: auth-required\ndata: {}\n\n")
                        self.wfile.flush()
                        break
                    snapshot = organization.snapshot()
                    revision = snapshot["revision"]
                    if revision != previous:
                        message = "event: snapshot\nid: " + str(revision) + "\ndata: " + json.dumps(snapshot, ensure_ascii=False) + "\n\n"
                        self.wfile.write(message.encode("utf-8"))
                        previous = revision
                    else:
                        self.wfile.write(b": keep-alive\n\n")
                    self.wfile.flush()
                    organization.wait_revision(previous, timeout=20)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
                pass
            finally:
                stream_slots.release()
                self.close_connection = True

        def do_GET(self):
            self.dispatch(False)

        def do_POST(self):
            self.dispatch(True)

        def json_body(self):
            if self.headers.get("X-DAS-Office") != "1" or self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                raise PermissionError("JSON 및 X-DAS-Office 헤더가 필요합니다.")
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 131072:
                raise ValueError("요청 크기가 올바르지 않습니다.")
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError("JSON 객체가 필요합니다.")
            return body

        def dispatch(self, write):
            try:
                auth.check_request(self.headers.get("Host", ""), self.headers.get("Origin"),
                                   self.client_address[0], self.server.server_port, write=write)
                path = urlsplit(self.path).path
                cookie = self.headers.get("Cookie", "")
                if not write and path == "/api/auth":
                    self.respond(200, auth.status(cookie))
                    return
                if not write and path == "/auth/login" and auth.mode == "github":
                    result = auth.begin_login(cookie)
                    self.redirect(result["location"], result.get("cookies", ()))
                    return
                if not write and path == "/auth/callback" and auth.mode == "github":
                    try:
                        result = auth.finish_login(urlsplit(self.path).query, cookie)
                        self.redirect(result["location"], result.get("cookies", ()))
                    except (PermissionError, ValueError) as exc:
                        self.redirect("/login?failed=1&reason=" + auth_failure_reason(exc))
                    return
                if not write and path in ("/login", "/login.js", "/office.css"):
                    if path == "/login" and auth.current_user(cookie) is not None:
                        self.redirect("/")
                    else:
                        name = "login.html" if path == "/login" else path[1:]
                        mime = {".html": "text/html", ".js": "text/javascript", ".css": "text/css"}[Path(name).suffix]
                        self.respond(200, (ROOT / "static" / name).read_bytes(), mime + "; charset=utf-8")
                    return
                if write and path == "/auth/pair" and auth.mode == "pairing":
                    result = auth.pair_login(self.json_body().get("token"), cookie)
                    self.respond(200, {"authenticated": result["authenticated"], "user": result["user"]},
                                 cookies=result["cookies"])
                    return
                if auth.current_user(cookie) is None:
                    if not write and path in ("/", "/index.html", "/legacy", "/voice", "/voice/"):
                        self.redirect("/login")
                    else:
                        self.respond(401, {"error": "로그인이 필요합니다.", "login_url": "/login"})
                    return
                body = self.json_body() if write else {}
                if write and path == "/auth/logout":
                    result = auth.logout(cookie)
                    self.redirect("/login", result.get("cookies", ()))
                elif (write and path == "/api/owner/pair/start" and auth.mode == "local"
                      and auth.pairing_auth is not None):
                    if self.headers.get("Origin") != "http://" + self.headers.get("Host", ""):
                        raise PermissionError("PC의 같은 웹 화면에서 연결해 주세요.")
                    if body:
                        raise ValueError("연결 시작 요청은 빈 JSON 객체만 허용합니다.")
                    self.respond(200, auth.pairing_auth.start_pairing())
                elif (write and path == "/api/owner/pair/revoke-all" and auth.mode == "local"
                      and auth.pairing_auth is not None):
                    if self.headers.get("Origin") != "http://" + self.headers.get("Host", ""):
                        raise PermissionError("PC의 같은 웹 화면에서 연결해 주세요.")
                    if body:
                        raise ValueError("연결 해제 요청은 빈 JSON 객체만 허용합니다.")
                    self.respond(200, auth.pairing_auth.revoke_paired_sessions())
                elif (write and path == "/api/owner/pair/cancel" and auth.mode == "local"
                      and auth.pairing_auth is not None):
                    if self.headers.get("Origin") != "http://" + self.headers.get("Host", ""):
                        raise PermissionError("PC의 같은 웹 화면에서 연결해 주세요.")
                    if body:
                        raise ValueError("연결 취소 요청은 빈 JSON 객체만 허용합니다.")
                    self.respond(200, auth.pairing_auth.cancel_pairing())
                elif organization is not None and path == "/api/org/route" and write:
                    self.respond(200, route_intake(body.get("text"), employee_id=body.get("employee_id"), auto=body.get("auto", True)))
                elif organization is not None and path == "/api/org" and not write:
                    self.respond(200, organization.snapshot())
                elif organization is not None and not write and (m := re.fullmatch(r"/previews/([a-f0-9]{32})/", path)):
                    if auth.mode in ("github", "pairing"):
                        raise Conflict("이 미리보기는 PC의 별도 로컬 화면에서 열어 주세요. 원격 미리보기는 아직 연결되지 않았습니다.")
                    location = organization.preview(m[1])
                    self.send_response(302)
                    self.send_header("Location", location)
                    self.send_header("Content-Length", "0")
                    self.security_headers()
                    self.end_headers()
                elif organization is not None and not write and (m := re.fullmatch(r"/prototypes/([a-f0-9]{32})/", path)):
                    if auth.mode in ("github", "pairing"):
                        raise Conflict("이 데모는 PC의 별도 로컬 화면에서 열어 주세요. 원격 데모 미리보기는 아직 연결되지 않았습니다.")
                    location = organization.prototype_preview(m[1])
                    self.send_response(302)
                    self.send_header("Location", location)
                    self.send_header("Content-Length", "0")
                    self.security_headers()
                    self.end_headers()
                elif organization is not None and path == "/api/org/events" and not write:
                    self.stream_organization()
                elif organization is not None and path == "/api/org/connection" and write:
                    self.respond(200, organization.refresh_connection())
                elif organization is not None and path == "/api/org/standing" and not write:
                    self.respond(200, organization.standing.snapshot())
                elif organization is not None and path == "/api/org/standing/tick" and write:
                    self.respond(200, organization.standing.tick())
                elif organization is not None and path == "/api/org/standing/pause" and write:
                    self.respond(200, organization.standing.set_paused(body.get("paused")))
                elif organization is not None and path == "/api/org/standing/schedule" and write:
                    self.respond(200, organization.standing.register_schedule(body.get("automation_id")))
                elif organization is not None and path == "/api/org/missions" and write:
                    if body.get("auto_recipient") is True:
                        routed = route_intake(body.get("text"), auto=True)
                        if routed["needs_clarification"]:
                            raise ValueError("여러 직원을 부르셨습니다. 받는 직원 한 명을 선택해 주세요.")
                        body = {**body, "employee_id": routed["employee_id"]}
                    self.respond(201, organization.submit(body))
                elif organization is not None and write and (m := re.fullmatch(r"/api/org/missions/([a-f0-9]{32})/input", path)):
                    self.respond(200, organization.projects.answer(m[1], body))
                elif organization is not None and write and (m := re.fullmatch(r"/api/org/missions/([a-f0-9]{32})/recheck-prototype", path)):
                    self.respond(200, recover_prototype(organization, m[1], body.get("review_note"),
                                                        require_policy_comparison=True))
                elif organization is not None and write and (m := re.fullmatch(r"/api/org/missions/([a-f0-9]{32})/capture-prototype-repair", path)):
                    self.respond(200, capture_prototype_repair_source(organization, m[1], body.get("attempt_id"),
                                 body.get("expected_artifacts"), body.get("expected_baseline_sha256")))
                elif organization is not None and write and (m := re.fullmatch(r"/api/org/missions/([a-f0-9]{32})/recover-report", path)):
                    if body.get("kind") == "restore_unstarted":
                        result = restore_unstarted_report(organization, m[1], body.get("expected_attempt_id"), body.get("review_note"))
                    elif body.get("kind") == "references":
                        result = recover_report_references(organization, m[1], body.get("expected_milestones"), body.get("replacements"), body.get("review_note"))
                    elif body.get("kind", "future_actions") == "future_actions":
                        result = recover_future_actions(organization, m[1], body.get("expected_remaining"), body.get("review_note"))
                    else:
                        raise ValueError("지원하는 보고서 분류·참조 교정만 사용할 수 있습니다.")
                    self.respond(200, result)
                elif organization is not None and (m := re.fullmatch(r"/api/org/missions/([a-f0-9]{32})(?:/(action))?", path)):
                    mission_id, action = m.groups()
                    if write and action:
                        self.respond(200, organization.action(mission_id, body))
                    elif not write and action is None:
                        self.respond(200, organization.detail(mission_id))
                    else:
                        self.respond(405, {"error": "허용되지 않는 요청입니다."})
                elif organization is not None and write and (m := re.fullmatch(r"/api/org/employees/([a-z-]+)/memory", path)):
                    self.respond(201, organization.remember(m[1], body))
                elif organization is not None and write and (path in ("/api/missions", "/api/tasks") or path.startswith("/api/tasks/")):
                    self.respond(409, {"error": "이전 미션은 조회 전용입니다. 조직 오피스에서 새 업무를 맡겨 주세요."})
                elif not write and path == "/api/health":
                    self.respond(200, office.health())
                elif write and path == "/api/missions":
                    self.respond(201, office.submit_mission(body))
                elif path == "/api/tasks":
                    self.respond(201 if write else 200, office.create(body) if write else {"tasks": office.tasks()})
                elif not write and path == "/api/sample":
                    self.respond(200, (ROOT / "examples/franchise-task.json").read_bytes())
                elif (m := re.fullmatch(r"/api/tasks/([a-f0-9]{32})(?:/(run|cancel|review|revise))?", path)):
                    task_id, action = m.groups()
                    if write and action == "run":
                        self.respond(202, office.run(task_id))
                    elif write and action == "cancel":
                        self.respond(200, office.cancel(task_id))
                    elif write and action == "review":
                        self.respond(200, office.review(task_id, body))
                    elif write and action == "revise":
                        self.respond(200, office.revise(task_id, body))
                    elif not write and action is None:
                        self.respond(200, office.detail(task_id))
                    else:
                        self.respond(405, {"error": "허용되지 않는 요청입니다."})
                elif not write and (m := re.fullmatch(r"/api/runs/([a-f0-9]{32})/files/([a-z_.]+)", path)):
                    self.respond(200, office.file(*m.groups()).read_bytes(), "text/plain; charset=utf-8")
                elif not write and (path in ("/voice", "/voice/") or path.startswith("/voice/")):
                    asset = voice_asset(path)
                    if asset is None:
                        self.respond(404, {"error": "찾을 수 없습니다."})
                    else:
                        self.respond_voice_file(*asset)
                elif not write and path.startswith("/brand/"):
                    asset = brand_asset(path)
                    if asset is None:
                        self.respond(404, {"error": "찾을 수 없습니다."})
                    else:
                        asset_path, content_type = asset
                        self.respond(200, asset_path.read_bytes(), content_type)
                elif not write and path in ("/", "/index.html", "/legacy", "/styles.css", "/app.js", "/office.css", "/office.js", "/voice-input.js", "/qr-code.js"):
                    name = "office.html" if organization is not None and path in ("/", "/index.html") else "index.html" if path in ("/", "/legacy") else path[1:]
                    mime = {".html": "text/html", ".css": "text/css", ".js": "text/javascript"}[Path(name).suffix]
                    self.respond(200, (ROOT / "static" / name).read_bytes(), mime + "; charset=utf-8", voice=name == "office.html")
                else:
                    self.respond(404, {"error": "찾을 수 없습니다."})
            except KeyError:
                self.respond(404, {"error": "업무 또는 파일을 찾을 수 없습니다."})
            except PermissionError as exc:
                self.respond(429 if isinstance(exc, AuthError) and exc.reason == "pair_rate_limited" else 403,
                             {"error": str(exc)})
            except Conflict as exc:
                self.respond(409, {"error": str(exc)})
            except (ValueError, UnicodeError) as exc:
                self.respond(400, {"error": str(exc)})
            except Exception:
                self.respond(500, {"error": "내부 처리 오류가 발생했습니다. 로컬 파일과 실행 기록을 확인하세요."})
    return Handler


def _listener_configuration(port, auth, public_port=None):
    """Choose explicit per-listener authentication before starting an engine."""
    if type(port) is not int or not 0 <= port <= 65535:
        raise ValueError("서버 포트가 올바르지 않습니다.")
    if public_port is None:
        auth.validate_bind("127.0.0.1")
        return [(port, auth)]
    if (type(public_port) is not int or not 1 <= public_port <= 65535
            or not port or port == public_port):
        raise ValueError("공개 연결 포트는 로컬 포트와 다른 1~65535 값이어야 합니다.")
    if auth.mode not in ("github", "pairing"):
        raise ValueError("공개 연결에는 GitHub 또는 기기 연결 인증 설정이 필요합니다.")
    # Both listeners bind to loopback. Only the second is a tunnel target;
    # neither changes handler_for's Host, Origin, peer or session checks.
    local = OfficeAuth()
    if auth.mode == "pairing":
        local.pairing_auth = auth
    local.validate_bind("127.0.0.1")
    auth.validate_bind("127.0.0.1")
    return [(port, local), (public_port, auth)]


def _bind_listeners(office, organization, configuration):
    """Bind all sockets before serving; roll back a partially bound pair."""
    servers = []
    try:
        for port, auth in configuration:
            server = OfficeHTTPServer(("127.0.0.1", port), handler_for(office, organization, auth))
            server.daemon_threads = True
            servers.append(server)
        return servers
    except BaseException:
        for server in servers:
            server.server_close()
        raise


def _serve_listeners(servers):
    """Keep one process alive for every listener and stop their loops together."""
    threads = []
    try:
        for index, server in enumerate(servers):
            thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .25},
                                      name=f"office-http-{index}", daemon=True)
            thread.start()
            threads.append(thread)
        while threads and all(thread.is_alive() for thread in threads):
            threads[0].join(timeout=.25)
        raise RuntimeError("서버 연결이 종료되어 다른 연결도 함께 정리합니다.")
    finally:
        for server, thread in zip(servers, threads):
            if thread.is_alive():
                server.shutdown()
        for thread in threads:
            thread.join(timeout=3)


def main():
    parser = argparse.ArgumentParser(description="DAS Lab AI Office — 로컬 관리자 웹")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--public-port", type=int,
                        help="선택적 인증 전용 loopback 포트. --port는 기존 로컬 접속에 유지합니다.")
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    parser.add_argument("--auth-config", type=Path, default=ROOT / "config" / "auth.json")
    parser.add_argument("--extra-runs-today", type=int, choices=range(1, 31), default=0,
                        help="이번 서버 실행의 오늘(한국 시간)에만 내부 실행 1~30회 추가")
    args = parser.parse_args()
    data_dir = args.data_dir.resolve()
    auth_config = json.loads(args.auth_config.read_text(encoding="utf-8")) if args.auth_config.exists() else None
    if isinstance(auth_config, dict) and auth_config.get("mode") == "pairing":
        auth_config = {**auth_config, "session_db": str(data_dir / "owner-sessions.sqlite3")}
    auth = OfficeAuth(auth_config)
    clear_server_secrets(auth_config)
    configuration = _listener_configuration(args.port, auth, args.public_port)
    lock = InstanceLock(data_dir)
    office = None
    organization = None
    servers = []
    try:
        from office.organization import OrganizationEngine
        office = Office(ROOT, data_dir, start_scheduler=False)
        organization = OrganizationEngine(ROOT, data_dir, extra_runs_today=args.extra_runs_today)
        servers = _bind_listeners(office, organization, configuration)
        for server, (_, listener_auth) in zip(servers, configuration):
            if listener_auth.mode in ("github", "pairing"):
                print(f"DAS Lab AI Office: {listener_auth.public_origin} · HTTPS 연결 대상 127.0.0.1:{server.server_port}", flush=True)
            else:
                print(f"DAS Lab AI Office: http://127.0.0.1:{server.server_port} · PC 내부 접속", flush=True)
        print("조직 운영 엔진 · 구독 실행 · 음성은 선택 입력. 종료: Ctrl+C", flush=True)
        if args.extra_runs_today:
            print(f"오늘(한국 시간) 내부 실행 {args.extra_runs_today}회 추가 · 총 {organization._daily_limit()}회. "
                  "이번 서버 실행에만 적용하며 다음 날짜에는 기본 한도로 돌아갑니다.", flush=True)
        _serve_listeners(servers)
    except KeyboardInterrupt:
        pass
    finally:
        for server in servers:
            server.server_close()
        if office:
            office.close()
        if organization:
            organization.close()
        lock.close()


if __name__ == "__main__":
    main()
