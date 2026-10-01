"""Bounded transport to the local Office Next bridge, behind existing owner auth.

The caller must authenticate every request and check its existing Host/Origin and
CSRF rules before using this module. It never accepts a destination URL or client
headers. Paperclip's unauthenticated management API is not exposed by this proxy.
"""
from dataclasses import dataclass
import http.client
import ipaddress
import json
import re
import socket
import threading
import time
from urllib.parse import urlsplit


MAX_RESPONSE_BYTES = 4 * 1024 * 1024  # Current pixel-assets.json is 860,739 bytes.
MAX_REQUEST_BYTES = 20_000  # The bridge enforces the same request limit.
_UUID = r"[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12}"
_ISSUE_PATH = re.compile(r"/api/office/issues/" + _UUID + r"\Z")
_ASSET_PATH = re.compile(r"/assets/[A-Za-z0-9][A-Za-z0-9_.-]{0,199}\Z")
_URL = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
_GET_PATHS = frozenset({"/", "/index.html", "/pixel-assets.json", "/PIXEL-AGENTS-LICENSE.txt",
                        "/THIRD-PARTY-NOTICES.txt", "/api/office/snapshot", "/demo/supply-chain"})


@dataclass(frozen=True)
class GatewayResponse:
    status: int
    body: bytes
    headers: dict[str, str]


def _json_response(status, message):
    return GatewayResponse(status, json.dumps({"error": message}, ensure_ascii=False).encode("utf-8"), {
        "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer",
    })


def _local_link(match):
    value = match.group(0)
    try:
        hostname = urlsplit(value).hostname
        if hostname and (hostname.lower() == "localhost" or hostname.lower().endswith(".localhost")):
            return "[로컬 연결]"
        if hostname and ipaddress.ip_address(hostname).is_loopback:
            return "[로컬 연결]"
    except ValueError:
        pass
    return value


def _public_value(value):
    if isinstance(value, dict):
        return {key: _public_value(item) for key, item in value.items() if key != "paperclipUrl"}
    if isinstance(value, list):
        return [_public_value(item) for item in value]
    if isinstance(value, str):
        return _URL.sub(_local_link, value)
    return value


class NextOfficeGateway:
    def __init__(self, *, port=8790, timeout=8.0, max_response_bytes=MAX_RESPONSE_BYTES):
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
            raise ValueError("Invalid bridge port")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 30:
            raise ValueError("Invalid bridge timeout")
        if isinstance(max_response_bytes, bool) or not isinstance(max_response_bytes, int) or max_response_bytes < 1:
            raise ValueError("Invalid response limit")
        self.port = port
        self.timeout = float(timeout)
        self.max_response_bytes = max_response_bytes

    @staticmethod
    def handles(method, raw_path):
        if not isinstance(raw_path, str) or len(raw_path) > 2048 or not raw_path.isascii():
            return False
        # Reject ambiguity before any URL decoding: no encoded paths, queries,
        # fragments, controls, backslashes, dot segments or absolute URLs.
        if any(char in raw_path for char in "%?#\\") or any(ord(char) <= 32 or ord(char) == 127 for char in raw_path):
            return False
        if ".." in raw_path:
            return False
        if method == "POST":
            return raw_path == "/api/office/issues"
        if method != "GET":
            return False
        return raw_path in _GET_PATHS or bool(_ISSUE_PATH.fullmatch(raw_path) or _ASSET_PATH.fullmatch(raw_path))

    def request(self, method, raw_path, body=b"", *, mutation_authorized=False):
        if not self.handles(method, raw_path):
            return _json_response(404, "허용되지 않은 새 사무실 경로입니다.")
        if method == "POST" and mutation_authorized is not True:
            return _json_response(403, "로그인과 요청 확인 후 업무를 맡겨 주세요.")
        if not isinstance(body, bytes):
            return _json_response(400, "올바른 요청 본문이 필요합니다.")
        if len(body) > MAX_REQUEST_BYTES:
            return _json_response(413, "입력이 너무 깁니다.")
        if method == "GET" and body:
            return _json_response(400, "조회 요청에는 본문을 보낼 수 없습니다.")
        if method == "POST":
            try:
                if not isinstance(json.loads(body.decode("utf-8")), dict):
                    raise ValueError()
            except (ValueError, UnicodeError):
                return _json_response(400, "올바른 UTF-8 JSON이 필요합니다.")

        path = "/" if raw_path == "/index.html" else raw_path
        origin = f"http://127.0.0.1:{self.port}"
        headers = {"Host": f"127.0.0.1:{self.port}", "Origin": origin,
                   "Accept": "*/*", "Accept-Encoding": "identity", "Connection": "close"}
        if method == "POST":
            headers.update({"Content-Type": "application/json; charset=utf-8", "X-Office-Next": "1"})

        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=self.timeout)
        timer = None
        deadline = time.monotonic() + self.timeout
        try:
            connection.connect()
            transport = connection.sock

            def expire():
                # Socket shutdown interrupts a trickling response, not just an
                # idle socket; close() alone may leave HTTPResponse's fd open.
                try:
                    transport.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError()
            timer = threading.Timer(remaining, expire)
            timer.daemon = True
            timer.start()
            connection.request(method, path, body=body if method == "POST" else None, headers=headers)
            response = connection.getresponse()
            with response:
                if 300 <= response.status < 400:
                    raise ValueError("Redirects are not supported")
                if response.status >= 500:
                    raise ValueError("Upstream unavailable")
                if response.getheader("Content-Encoding", "identity").lower() != "identity":
                    raise ValueError("Unexpected content encoding")
                length = response.getheader("Content-Length")
                if length is not None and (int(length) < 0 or int(length) > self.max_response_bytes):
                    raise ValueError("Response limit exceeded")
                content = response.read(self.max_response_bytes + 1)
                if len(content) > self.max_response_bytes or time.monotonic() >= deadline:
                    raise ValueError("Response limit exceeded")
                if length is not None and len(content) != int(length):
                    raise ValueError("Incomplete response")
                content_type = response.getheader("Content-Type", "application/octet-stream")
                if "\r" in content_type or "\n" in content_type:
                    raise ValueError("Invalid response header")
                media_type = content_type.partition(";")[0].strip().lower()
                is_text = media_type.startswith("text/") or media_type in {
                    "application/json", "application/javascript", "image/svg+xml"}
                if is_text:
                    content.decode("utf-8")
                    content_type = media_type + "; charset=utf-8"
                if path.startswith("/api/office/"):
                    if media_type != "application/json":
                        raise ValueError("Invalid API response")
                    value = _public_value(json.loads(content.decode("utf-8")))
                    if path == "/api/office/snapshot" and response.status == 200:
                        if not isinstance(value, dict):
                            raise ValueError("Invalid snapshot")
                        value["legacyUrl"] = "/office.html"
                    content = json.dumps(value, ensure_ascii=False).encode("utf-8")
                    if len(content) > self.max_response_bytes:
                        raise ValueError("Response limit exceeded")
                safe_headers = {"Content-Type": content_type, "Cache-Control": "no-store",
                                "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer"}
                csp = response.getheader("Content-Security-Policy")
                if path == "/demo/supply-chain":
                    sandbox = [part.strip().lower().split()[1:] for part in (csp or "").split(";")
                               if part.strip() and part.strip().lower().split()[0] == "sandbox"]
                    # Reject duplicate directives rather than validating the last
                    # one while a browser might enforce a different occurrence.
                    if len(sandbox) != 1 or not set(sandbox[0]) <= {"allow-scripts"}:
                        raise ValueError("Demo isolation missing")
                if csp:
                    if "\r" in csp or "\n" in csp:
                        raise ValueError("Invalid response header")
                    safe_headers["Content-Security-Policy"] = csp
                return GatewayResponse(response.status, content, safe_headers)
        except (OSError, http.client.HTTPException, ValueError, UnicodeError, RecursionError):
            return _json_response(502, "새 사무실에 연결하지 못했습니다. 잠시 뒤 다시 시도해 주세요.")
        finally:
            if timer is not None:
                timer.cancel()
            connection.close()
