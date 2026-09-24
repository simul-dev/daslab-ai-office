"""Run a single-user local office: python server.py"""
import argparse
import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from office.service import Office
from office.store import Conflict

ROOT = Path(__file__).resolve().parent


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


def handler_for(office):
    class Handler(BaseHTTPRequestHandler):
        server_version = "DASLabOffice/0.1"

        def log_message(self, fmt, *args):
            # No request bodies, auth values or sensitive query strings in server log.
            pass

        def respond(self, status, data, content_type="application/json; charset=utf-8"):
            if not isinstance(data, bytes):
                data = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            self.end_headers()
            try:
                self.wfile.write(data)
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
                if not write and path == "/api/health":
                    self.respond(200, office.health())
                elif path == "/api/tasks":
                    self.respond(201 if write else 200, office.create(body) if write else {"tasks": office.store.tasks()})
                elif not write and path == "/api/sample":
                    self.respond(200, (ROOT / "examples/franchise-task.json").read_bytes())
                elif (m := re.fullmatch(r"/api/tasks/([a-f0-9]{32})(?:/(run|cancel|review))?", path)):
                    task_id, action = m.groups()
                    if write and action == "run":
                        self.respond(202, office.run(task_id))
                    elif write and action == "cancel":
                        self.respond(200, office.cancel(task_id))
                    elif write and action == "review":
                        self.respond(200, office.review(task_id, body))
                    elif not write and action is None:
                        self.respond(200, office.store.detail(task_id))
                    else:
                        self.respond(405, {"error": "허용되지 않는 요청입니다."})
                elif not write and (m := re.fullmatch(r"/api/runs/([a-f0-9]{32})/files/([a-z_.]+)", path)):
                    self.respond(200, office.file(*m.groups()).read_bytes(), "text/plain; charset=utf-8")
                elif not write and path in ("/", "/index.html", "/styles.css", "/app.js"):
                    name = "index.html" if path == "/" else path[1:]
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
    server = None
    try:
        office = Office(ROOT, data_dir)
        server = ThreadingHTTPServer(("127.0.0.1", args.port), handler_for(office))
        server.daemon_threads = True
        print(f"DAS Lab AI Office: http://127.0.0.1:{args.port}", flush=True)
        print("실행은 웹의 실행 버튼으로만 시작합니다. 종료: Ctrl+C", flush=True)
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        if server:
            server.server_close()
        if office:
            office.close()
        lock.close()


if __name__ == "__main__":
    main()
