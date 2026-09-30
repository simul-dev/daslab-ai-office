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

ROOT = Path(__file__).resolve().parent

VOICE_TYPES = {
    ".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8", ".mjs": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8", ".wasm": "application/wasm",
    ".onnx": "application/octet-stream", ".txt": "text/plain; charset=utf-8",
    ".md": "text/plain; charset=utf-8", ".wav": "audio/wav",
}


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


def handler_for(office, organization=None):
    stream_slots = threading.BoundedSemaphore(12)

    class Handler(BaseHTTPRequestHandler):
        server_version = "DASLabOffice/0.1"

        def log_message(self, fmt, *args):
            # No request bodies, auth values or sensitive query strings in server log.
            pass

        def security_headers(self, voice=False):
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            script = "script-src 'self' 'wasm-unsafe-eval'" if voice else "script-src 'self'"
            worker = "; worker-src 'self' blob:" if voice else ""
            self.send_header("Content-Security-Policy", "default-src 'self'; " + script + "; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'" + worker)
            if voice:
                self.send_header("Permissions-Policy", "microphone=(self), camera=()")

        def respond(self, status, data, content_type="application/json; charset=utf-8"):
            if not isinstance(data, bytes):
                data = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.security_headers()
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

        def local_request(self):
            host = self.headers.get("Host", "")
            allowed = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
            if host not in allowed:
                raise PermissionError("로컬 주소로 접속해 주세요.")
            origin = self.headers.get("Origin")
            if origin and origin != "http://" + host:
                raise PermissionError("동일한 로컬 웹에서만 요청할 수 있습니다.")

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

        def dispatch(self, write):
            try:
                self.local_request()
                path = urlsplit(self.path).path
                body = {}
                if write:
                    if self.headers.get("X-DAS-Office") != "1" or self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                        raise PermissionError("JSON 및 X-DAS-Office 헤더가 필요합니다.")
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= 131072:
                        raise ValueError("요청 크기가 올바르지 않습니다.")
                    body = json.loads(self.rfile.read(length))
                    if not isinstance(body, dict):
                        raise ValueError("JSON 객체가 필요합니다.")
                if organization is not None and path == "/api/org" and not write:
                    self.respond(200, organization.snapshot())
                elif organization is not None and not write and (m := re.fullmatch(r"/previews/([a-f0-9]{32})/", path)):
                    location = organization.preview(m[1])
                    self.send_response(302)
                    self.send_header("Location", location)
                    self.send_header("Content-Length", "0")
                    self.security_headers()
                    self.end_headers()
                elif organization is not None and path == "/api/org/events" and not write:
                    self.stream_organization()
                elif organization is not None and path == "/api/org/connection" and write:
                    self.respond(200, organization.refresh_connection())
                elif organization is not None and path == "/api/org/missions" and write:
                    self.respond(201, organization.submit(body))
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
                elif not write and path in ("/", "/index.html", "/legacy", "/styles.css", "/app.js", "/office.css", "/office.js"):
                    name = "office.html" if organization is not None and path in ("/", "/index.html") else "index.html" if path in ("/", "/legacy") else path[1:]
                    mime = {".html": "text/html", ".css": "text/css", ".js": "text/javascript"}[Path(name).suffix]
                    self.respond(200, (ROOT / "static" / name).read_bytes(), mime + "; charset=utf-8")
                else:
                    self.respond(404, {"error": "찾을 수 없습니다."})
            except KeyError:
                self.respond(404, {"error": "업무 또는 파일을 찾을 수 없습니다."})
            except PermissionError as exc:
                self.respond(403, {"error": str(exc)})
            except Conflict as exc:
                self.respond(409, {"error": str(exc)})
            except (ValueError, UnicodeError) as exc:
                self.respond(400, {"error": str(exc)})
            except Exception:
                self.respond(500, {"error": "내부 처리 오류가 발생했습니다. 로컬 파일과 실행 기록을 확인하세요."})
    return Handler


def main():
    parser = argparse.ArgumentParser(description="DAS Lab AI Office — 로컬 관리자 웹")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    args = parser.parse_args()
    data_dir = args.data_dir.resolve()
    lock = InstanceLock(data_dir)
    office = None
    organization = None
    server = None
    try:
        from office.organization import OrganizationEngine
        office = Office(ROOT, data_dir, start_scheduler=False)
        organization = OrganizationEngine(ROOT, data_dir)
        server = ThreadingHTTPServer(("127.0.0.1", args.port), handler_for(office, organization))
        server.daemon_threads = True
        print(f"DAS Lab AI Office: http://127.0.0.1:{args.port}", flush=True)
        print("조직 운영 엔진 · 구독 실행 · 음성은 선택 입력. 종료: Ctrl+C", flush=True)
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        if server:
            server.server_close()
        if office:
            office.close()
        if organization:
            organization.close()
        lock.close()


if __name__ == "__main__":
    main()
