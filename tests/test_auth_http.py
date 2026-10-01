"""HTTP authentication boundaries with real sessions and an offline GitHub stub.

Only a temporary loopback listener and tiny static fixtures are used. There is
no organization database, worker, provider connection, or production server.
"""
import http.client
import json
import os
import tempfile
import threading
import unittest
from http.cookies import SimpleCookie
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlencode, urlsplit

from office.auth import AuthError, OfficeAuth, SESSION_COOKIE, STATE_COOKIE, TOKEN_URL, USER_URL, GITHUB_ISSUER
from server import handler_for


class AuthHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        fixtures = {
            "office.html": "private office fixture", "index.html": "legacy fixture",
            "login.html": "public login fixture", "login.js": "public login script",
            "office.css": "public login styles", "office.js": "private office script",
            "voice-input.js": "private voice input script", "voice/index.html": "private voice page",
            "voice/model.onnx": "private model fixture", "voice/app.js": "private voice script",
        }
        for name, content in fixtures.items():
            path = self.root / "static" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        self.root_patch = patch("server.ROOT", self.root)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)
        self.github = Mock(side_effect=[
            {"access_token": "offline-provider-token", "token_type": "bearer", "scope": "read:user"},
            {"id": 1234, "login": "office-owner", "type": "User"},
        ])
        self.auth = OfficeAuth(
            {"mode": "github", "public_origin": "https://office.example", "allowed_github_ids": [1234]},
            environ={"DAS_OFFICE_GITHUB_CLIENT_ID": "offline-client",
                     "DAS_OFFICE_GITHUB_CLIENT_SECRET": "offline-secret"}, http=self.github)
        self.organization = Mock()
        self.organization.closed = False
        self.organization.snapshot.return_value = {"revision": 7, "missions": [{"title": "private mission"}]}
        self.organization.submit.return_value = {"id": "a" * 32, "status": "queued"}
        self.organization.preview.return_value = "http://127.0.0.1:9001/"
        self.organization.prototype_preview.return_value = "http://127.0.0.1:9002/"
        self.legacy = Mock()
        self.stream_waiting = threading.Event()
        self.stream_continue = threading.Event()
        self.organization.wait_revision.side_effect = self.wait_revision
        self.start_server()
        self.addCleanup(self.cleanup_runtime)

    def wait_revision(self, previous, timeout):
        self.stream_waiting.set()
        self.stream_continue.wait(3)

    def start_server(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(self.legacy, self.organization, self.auth))
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={"poll_interval": 0.01}, daemon=True)
        self.thread.start()
        self.host = "office.example" if self.auth.mode == "github" else f"127.0.0.1:{self.server.server_port}"

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.assertFalse(self.thread.is_alive())

    def cleanup_runtime(self):
        self.organization.closed = True
        self.stream_continue.set()
        self.stop_server()

    def request(self, path, method="GET", body=None, *, cookie=None, headers=None):
        supplied = {"Host": self.host}
        if method == "POST":
            supplied.update({"Content-Type": "application/json", "X-DAS-Office": "1"})
            if self.auth.mode == "github":
                supplied["Origin"] = self.auth.public_origin
        if cookie:
            supplied["Cookie"] = cookie
        for key, value in (headers or {}).items():
            if value is None:
                supplied.pop(key, None)
            else:
                supplied[key] = value
        encoded = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=4)
        try:
            connection.request(method, path, body=encoded, headers=supplied)
            response = connection.getresponse()
            return response.status, response.headers, response.read()
        finally:
            connection.close()

    def denied(self, status, path, method="GET", body=None, **kwargs):
        try:
            result = self.request(path, method, body, **kwargs)
        except (ConnectionAbortedError, ConnectionResetError) as exc:
            # Windows can reject an early-denied POST's unread body before its
            # 401/403 reaches the client. Callers still assert no handler ran.
            if os.name != "nt" or getattr(exc, "winerror", None) not in (10053, 10054):
                raise
            return
        self.assertEqual(result[0], status, result[2])
        return result

    @staticmethod
    def cookie(headers, name):
        for value in headers.get_all("Set-Cookie", []):
            parsed = SimpleCookie(value)
            if name in parsed:
                return f"{name}={parsed[name].value}"
        return None

    def begin_login(self):
        status, headers, raw = self.request("/auth/login")
        self.assertEqual(status, 303, raw)
        location = urlsplit(headers["Location"])
        self.assertEqual((location.scheme, location.netloc, location.path),
                         ("https", "github.com", "/login/oauth/authorize"))
        query = parse_qs(location.query)
        self.assertEqual(query["redirect_uri"], ["https://office.example/auth/callback"])
        self.assertEqual(query["scope"], ["read:user"])
        self.assertEqual(query["code_challenge_method"], ["S256"])
        return query["state"][0], self.cookie(headers, STATE_COOKIE)

    def login(self):
        state, cookie = self.begin_login()
        path = "/auth/callback?" + urlencode({"state": state, "code": "offline-code", "iss": GITHUB_ISSUER})
        status, headers, raw = self.request(path, cookie=cookie)
        self.assertEqual((status, headers.get("Location")), (303, "/"), raw)
        session = self.cookie(headers, SESSION_COOKIE)
        self.assertIsNotNone(session)
        return session

    def test_public_login_only_and_private_reads_never_touch_organization(self):
        for path in ("/login", "/login.js", "/office.css", "/api/auth"):
            with self.subTest(public=path):
                status, headers, raw = self.request(path)
                self.assertEqual(status, 200, raw)
                self.assertEqual(headers["Cache-Control"], "no-store")
        for path in ("/", "/index.html", "/legacy", "/voice", "/voice/"):
            with self.subTest(page=path):
                status, headers, raw = self.request(path)
                self.assertEqual((status, headers["Location"]), (303, "/login"), raw)
        for path in ("/api/org", "/api/org/events", "/api/tasks", "/api/health", "/office.js",
                     "/voice-input.js", "/voice/app.js", "/voice/model.onnx",
                     "/previews/" + "a" * 32 + "/", "/prototypes/" + "a" * 32 + "/"):
            with self.subTest(private=path):
                status, _, raw = self.request(path)
                self.assertEqual(status, 401, raw)
                self.assertNotIn(b"private", raw)
        for path in ("/api/org/route", "/api/org/missions", "/auth/logout"):
            with self.subTest(write=path):
                self.denied(401, path, "POST", {"text": "PM, 새 업무를 해줘"})
        self.assertEqual(self.organization.mock_calls, [])
        self.assertEqual(self.legacy.mock_calls, [])
        self.github.assert_not_called()

    def test_callback_creates_owner_session_without_exposing_provider_credentials(self):
        session = self.login()
        status, headers, raw = self.request("/api/auth", cookie=session)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw), {"mode": "github", "required": True, "authenticated": True,
                                          "user": {"id": 1234, "login": "office-owner"}, "login_url": "/auth/login"})
        self.assertEqual(self.request("/api/org", cookie=session)[0], 200)
        status, headers, raw = self.request("/", cookie=session)
        self.assertEqual((status, raw), (200, b"private office fixture"))
        self.assertEqual(headers["Permissions-Policy"], "microphone=(self), camera=()")
        self.assertEqual(self.request("/voice/model.onnx", cookie=session)[0], 200)
        self.assertNotIn(b"offline-secret", raw)
        self.assertNotIn(b"offline-provider-token", raw)
        self.assertEqual(self.github.call_count, 2)
        post, get = self.github.call_args_list
        self.assertEqual(post.args, ("POST", TOKEN_URL))
        self.assertEqual(parse_qs(post.kwargs["data"].decode())["code"], ["offline-code"])
        self.assertEqual(get.args, ("GET", USER_URL))
        self.assertEqual(get.kwargs["headers"]["Authorization"], "Bearer offline-provider-token")
        self.organization.submit.assert_not_called()

    def test_github_issuer_response_reaches_exchange_and_owner_session(self):
        state, binding = self.begin_login()
        path = "/auth/callback?" + urlencode({"state": state, "code": "offline-code",
                                             "iss": "https://github.com/login/oauth"})
        status, headers, _ = self.request(path, cookie=binding)
        self.assertEqual((status, headers.get("Location")), (303, "/"))
        session = self.cookie(headers, SESSION_COOKIE)
        self.assertIsNotNone(session)
        self.assertEqual(self.request("/api/org", cookie=session)[0], 200)
        self.assertEqual(self.github.call_count, 2)

    def test_missing_blank_duplicate_and_wrong_issuer_never_exchange_or_issue_session(self):
        state, binding = self.begin_login()
        base = "/auth/callback?" + urlencode({"state": state, "code": "offline-code"})
        variants = [("", "issuer_missing"),
                    ("&iss=", "issuer_mismatch"),
                    ("&" + urlencode({"iss": "https://attacker.example/login/oauth"}), "issuer_mismatch"),
                    ("&" + urlencode([("iss", GITHUB_ISSUER), ("iss", GITHUB_ISSUER)]), "issuer_mismatch")]
        for extra, reason in variants:
            with self.subTest(reason=reason):
                status, headers, raw = self.request(base + extra, cookie=binding)
                self.assertEqual((status, headers["Location"]), (303, "/login?failed=1&reason=" + reason))
                self.assertIsNone(self.cookie(headers, SESSION_COOKIE))
                self.assertEqual(raw, b"")
        self.github.assert_not_called()
        self.assertEqual(self.auth._sessions, {})
        self.assertEqual(self.organization.mock_calls, [])

    def test_wrong_github_owner_cannot_obtain_private_data(self):
        self.github.side_effect = [
            {"access_token": "offline-other-token", "token_type": "bearer", "scope": "read:user"},
            {"id": 9876, "login": "office-owner", "type": "User"},
        ]
        state, cookie = self.begin_login()
        status, headers, raw = self.request("/auth/callback?" + urlencode({"state": state, "code": "other-code", "iss": GITHUB_ISSUER}), cookie=cookie)
        self.assertEqual((status, headers["Location"]), (303, "/login?failed=1&reason=owner_mismatch"), raw)
        self.assertIsNone(self.cookie(headers, SESSION_COOKIE))
        self.assertEqual(self.request("/api/org", cookie=cookie)[0], 401)
        self.assertEqual(self.organization.mock_calls, [])

    def test_callback_requires_browser_binding_and_is_one_use(self):
        state, cookie = self.begin_login()
        path = "/auth/callback?" + urlencode({"state": state, "code": "offline-code", "iss": GITHUB_ISSUER})
        status, headers, _ = self.request(path)
        self.assertEqual((status, headers["Location"]), (303, "/login?failed=1&reason=binding_missing"))
        self.github.assert_not_called()
        status, headers, _ = self.request(path, cookie=cookie)
        self.assertEqual((status, headers["Location"]), (303, "/"))
        status, headers, _ = self.request(path, cookie=cookie)
        self.assertEqual((status, headers["Location"]), (303, "/login?failed=1&reason=state_unavailable"))
        self.assertEqual(self.github.call_count, 2)
        self.assertEqual(self.organization.mock_calls, [])

    def test_callback_redirect_exposes_only_allowlisted_failure_stage(self):
        sensitive = "offline-sensitive-provider-response"
        token = {"access_token": "offline-provider-token", "token_type": "bearer", "scope": "read:user"}
        cases = [([RuntimeError(sensitive)], "token_exchange_failed"),
                 ([{"error": "bad_verification_code", "error_description": sensitive}], "token_bad_verification_code"),
                 ([{"error": sensitive}], "token_rejected"),
                 ([{**token, "scope": "read:user,repo"}], "token_scope_rejected"),
                 ([token, RuntimeError(sensitive)], "user_lookup_failed")]
        for responses, reason in cases:
            self.github.side_effect = responses
            state, cookie = self.begin_login()
            path = "/auth/callback?" + urlencode({"state": state, "code": "offline-private-code", "iss": GITHUB_ISSUER})
            with self.subTest(reason=reason):
                status, headers, raw = self.request(path, cookie=cookie)
                self.assertEqual((status, headers["Location"]), (303, "/login?failed=1&reason=" + reason))
                self.assertIsNone(self.cookie(headers, SESSION_COOKIE))
                public = str(headers) + raw.decode()
                for value in (sensitive, "offline-private-code", "offline-provider-token", "offline-secret", state, cookie):
                    self.assertNotIn(value, public)
        self.assertEqual(self.organization.mock_calls, [])
        self.assertEqual(self.auth._sessions, {})

    def test_unexpected_callback_exception_cannot_inject_reason_or_provider_text(self):
        sensitive = "offline-sensitive-text&secret=offline"
        error = AuthError(sensitive)
        error.reason = sensitive
        for failure in (error, PermissionError(sensitive), ValueError(sensitive)):
            with patch.object(self.auth, "finish_login", side_effect=failure):
                status, headers, raw = self.request("/auth/callback?code=offline&state=offline")
            self.assertEqual((status, headers["Location"]), (303, "/login?failed=1&reason=auth_error"))
            self.assertNotIn(sensitive, str(headers) + raw.decode())
        self.assertEqual(self.organization.mock_calls, [])

    def test_csrf_host_and_origin_rejections_do_not_submit_or_revoke_session(self):
        session = self.login()
        for overrides in ({"Origin": None}, {"Origin": "https://attacker.example"},
                          {"Host": "attacker.example"}, {"X-DAS-Office": None},
                          {"Content-Type": "text/plain"}):
            with self.subTest(headers=overrides):
                self.denied(403, "/api/org/missions", "POST", {"text": "작업 실행"},
                            cookie=session, headers=overrides)
                self.denied(403, "/auth/logout", "POST", {}, cookie=session, headers=overrides)
                self.assertIsNotNone(self.auth.current_user(session))
        self.denied(403, "/api/org", cookie=session, headers={"Origin": "https://attacker.example"})
        self.denied(403, "/api/org", cookie=session, headers={"Host": "attacker.example"})
        self.assertEqual(self.organization.mock_calls, [])
        self.assertEqual(self.legacy.mock_calls, [])
        self.assertEqual(self.request("/api/org/missions", "POST", {"text": "작업 실행"}, cookie=session)[0], 201)
        self.organization.submit.assert_called_once_with({"text": "작업 실행"})

    def test_logout_revokes_session_and_active_event_stream(self):
        session = self.login()
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=4)
        try:
            connection.request("GET", "/api/org/events", headers={"Host": self.host, "Cookie": session})
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            self.assertEqual(response.readline(), b"event: snapshot\n")
            self.assertEqual(response.readline(), b"id: 7\n")
            self.assertIn(b"private mission", response.readline())
            self.assertEqual(response.readline(), b"\n")
            self.assertTrue(self.stream_waiting.wait(2))
            status, headers, raw = self.request("/auth/logout", "POST", {}, cookie=session)
            self.assertEqual((status, headers["Location"]), (303, "/login"), raw)
            for value in headers.get_all("Set-Cookie", []):
                self.assertIn("Max-Age=0", value)
            self.assertIsNone(self.auth.current_user(session))
            self.stream_continue.set()
            self.assertEqual(response.readline(), b"event: auth-required\n")
            self.assertEqual(response.readline(), b"data: {}\n")
            self.assertEqual(response.readline(), b"\n")
            self.assertEqual(response.read(), b"")
        finally:
            connection.close()
        self.organization.snapshot.assert_called_once()
        self.assertEqual(self.request("/api/org", cookie=session)[0], 401)
        self.assertFalse(json.loads(self.request("/api/auth", cookie=session)[2])["authenticated"])
        self.denied(401, "/api/org/missions", "POST", {"text": "재실행"}, cookie=session)
        self.organization.submit.assert_not_called()

    def test_remote_session_cannot_redirect_to_unauthenticated_loopback_previews(self):
        session = self.login()
        for kind in ("previews", "prototypes"):
            status, headers, raw = self.request(f"/{kind}/" + "a" * 32 + "/", cookie=session)
            self.assertEqual(status, 409, raw)
            self.assertNotIn("Location", headers)
        self.organization.preview.assert_not_called()
        self.organization.prototype_preview.assert_not_called()

    def test_route_preview_and_auto_submit_share_addressing_without_mutating_text(self):
        session = self.login()
        text = "  알앤디, 이번 데모를 검토해 줘. PM에게 보고할 내용도 정리해.  "
        status, _, raw = self.request("/api/org/route", "POST", {"text": text, "auto": True}, cookie=session)
        self.assertEqual(status, 200, raw)
        routed = json.loads(raw)
        self.assertEqual((routed["employee_id"], routed["status"], routed["text"]), ("das-rd", "addressed", text))
        self.assertFalse(routed["needs_clarification"])
        self.organization.submit.assert_not_called()
        body = {"text": text, "employee_id": "das-pm", "auto_recipient": True, "request_id": "offline-draft"}
        self.assertEqual(self.request("/api/org/missions", "POST", body, cookie=session)[0], 201)
        self.organization.submit.assert_called_once_with({**body, "employee_id": "das-rd"})

    def test_ambiguous_auto_submit_is_blocked_until_manual_selection(self):
        session = self.login()
        text = "PM과 R&D, 너희가 이 작업을 해 줘"
        status, _, raw = self.request("/api/org/route", "POST", {"text": text, "auto": True}, cookie=session)
        self.assertEqual(status, 200, raw)
        routed = json.loads(raw)
        self.assertTrue(routed["needs_clarification"])
        self.assertEqual(routed["candidates"], ["das-pm", "das-rd"])
        body = {"text": text, "employee_id": "das-pm", "auto_recipient": True}
        status, _, raw = self.request("/api/org/missions", "POST", body, cookie=session)
        self.assertEqual(status, 400, raw)
        self.organization.submit.assert_not_called()
        manual = {"text": text, "employee_id": "das-rd", "auto": False}
        status, _, raw = self.request("/api/org/route", "POST", manual, cookie=session)
        self.assertEqual(status, 200, raw)
        self.assertEqual(json.loads(raw)["status"], "manual")
        body.update(employee_id="das-rd", auto_recipient=False)
        self.assertEqual(self.request("/api/org/missions", "POST", body, cookie=session)[0], 201)
        self.organization.submit.assert_called_once_with(body)

    def test_local_mode_preserves_loopback_use_without_login(self):
        self.stop_server()
        self.auth = OfficeAuth()
        self.start_server()
        status, _, raw = self.request("/api/auth")
        self.assertEqual(status, 200)
        self.assertFalse(json.loads(raw)["required"])
        self.assertTrue(json.loads(raw)["authenticated"])
        self.assertEqual(self.request("/")[0], 200)
        self.assertEqual(self.request("/api/org")[0], 200)
        self.assertEqual(self.request("/voice/model.onnx")[0], 200)
        body = {"text": "로컬 업무", "employee_id": "das-pm"}
        self.assertEqual(self.request("/api/org/missions", "POST", body)[0], 201)
        self.organization.submit.assert_called_once_with(body)
        for kind, port in (("previews", 9001), ("prototypes", 9002)):
            status, headers, raw = self.request(f"/{kind}/" + "a" * 32 + "/")
            self.assertEqual((status, headers["Location"]), (302, f"http://127.0.0.1:{port}/"), raw)
        self.denied(403, "/api/org", headers={"Host": "attacker.example"})
        self.denied(403, "/api/org/missions", "POST", body, headers={"X-DAS-Office": None})
        self.organization.submit.assert_called_once()
        self.github.assert_not_called()


if __name__ == "__main__":
    unittest.main()
