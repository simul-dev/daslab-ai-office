"""Bounded local HTTP fixtures; no production listener or credential is used."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time
import unittest
from unittest.mock import patch

from office.next_gateway import NextOfficeGateway, MAX_REQUEST_BYTES, MAX_RESPONSE_BYTES


ISSUE_ID = "11111111-2222-4333-8444-555555555555"
DEMO_CSP = "sandbox allow-scripts; default-src 'none'; connect-src 'none'; frame-ancestors 'none'"


class NextGatewayTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.status = 200
        self.content = b'{"ok":true}'
        self.response_headers = {"Content-Type": "application/json; charset=utf-8"}
        self.delay = 0
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                self.handle_request()

            def do_POST(self):
                self.handle_request()

            def handle_request(self):
                data = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                owner.calls.append((self.command, self.path, dict(self.headers), data))
                if owner.delay:
                    time.sleep(owner.delay)
                try:
                    self.send_response(owner.status)
                    for key, value in owner.response_headers.items():
                        self.send_header(key, value)
                    self.end_headers()
                    self.wfile.write(owner.content)
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
        self.thread.start()
        self.gateway = NextOfficeGateway(port=self.server.server_port)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def test_allowlist_is_exact_and_caller_cannot_supply_a_destination_or_headers(self):
        allowed = ["/", "/index.html", "/assets/index-ab_1.js", "/pixel-assets.json",
                   "/PIXEL-AGENTS-LICENSE.txt", "/THIRD-PARTY-NOTICES.txt",
                   "/api/office/snapshot", f"/api/office/issues/{ISSUE_ID}", "/demo/supply-chain"]
        self.assertTrue(all(self.gateway.handles("GET", path) for path in allowed))
        forbidden = ["http://evil.test/", "//evil.test/", "/api/companies", "/api/office/issues",
                     "/assets/../auth.json", "/assets/%2e%2e/auth.json", "/assets/a%252fb.js",
                     "/assets/sub/file.js", "/assets/..js", "/assets/a.js?url=http://evil.test",
                     "/?", "/#x", "/assets/a.js\\x", "/assets/a.js\r\nCookie: fixture",
                     f"/api/office/issues/{'a' * 36}", "/demo/other"]
        for path in forbidden:
            with self.subTest(path=path):
                self.assertFalse(self.gateway.handles("GET", path))
                self.assertEqual(self.gateway.request("GET", path).status, 404)
        self.assertFalse(self.gateway.handles("DELETE", f"/api/office/issues/{ISSUE_ID}"))
        with self.assertRaises(TypeError):
            NextOfficeGateway(host="evil.test")
        with self.assertRaises(TypeError):
            self.gateway.request("GET", "/", headers={"Cookie": "fixture"})
        self.assertEqual(self.calls, [])

    def test_index_alias_and_only_fixed_local_headers_reach_upstream(self):
        result = self.gateway.request("GET", "/index.html")
        self.assertEqual(result.status, 200)
        method, path, headers, body = self.calls[0]
        self.assertEqual((method, path, body), ("GET", "/", b""))
        self.assertEqual(headers["Host"], f"127.0.0.1:{self.server.server_port}")
        self.assertEqual(headers["Origin"], f"http://127.0.0.1:{self.server.server_port}")
        for name in ("Cookie", "Authorization", "Forwarded", "X-Forwarded-Host", "X-Forwarded-For", "X-Office-Next"):
            self.assertNotIn(name, headers)

    def test_mutation_requires_explicit_caller_authorization_and_valid_bounded_utf8_json(self):
        body = json.dumps({"instruction": "진행 상황 보고", "requestId": ISSUE_ID}, ensure_ascii=False).encode()
        self.assertEqual(self.gateway.request("POST", "/api/office/issues", body).status, 403)
        self.assertEqual(self.calls, [])
        result = self.gateway.request("POST", "/api/office/issues", body, mutation_authorized=True)
        self.assertEqual(result.status, 200)
        self.assertEqual(self.calls[0][2]["X-Office-Next"], "1")
        self.assertEqual(self.calls[0][3], body)
        for bad in (b"\xff", b"[]", b"not json"):
            self.assertEqual(self.gateway.request("POST", "/api/office/issues", bad, mutation_authorized=True).status, 400)
        self.assertEqual(self.gateway.request("POST", "/api/office/issues", b"x" * (MAX_REQUEST_BYTES + 1), mutation_authorized=True).status, 413)
        self.assertEqual(len(self.calls), 1)

    def test_snapshot_removes_management_url_and_local_report_links(self):
        self.content = json.dumps({"paperclipUrl": "http://127.0.0.1:3101", "legacyUrl": "http://localhost:8772/",
            "agents": [{"lastResult": "결과 http://127.0.0.1:8790/demo/supply-chain", "currentIssue": {"url": "http://[::1]:3101/issue"}}],
            "issues": [{"description": "[보기](http://localhost:3101/task) 공개 https://example.com/report"}]}).encode()
        result = self.gateway.request("GET", "/api/office/snapshot")
        value = json.loads(result.body)
        self.assertEqual(result.status, 200)
        self.assertNotIn("paperclipUrl", value)
        self.assertEqual(value["legacyUrl"], "/office.html")
        for local in (b"127.0.0.1", b"localhost", b"[::1]"):
            self.assertNotIn(local, result.body)
        self.assertIn(b"https://example.com/report", result.body)

    def test_response_preserves_demo_csp_but_never_cookies_or_internal_headers(self):
        self.content = "<html>검수 데모</html>".encode()
        self.response_headers = {"Content-Type": "text/html", "Content-Security-Policy": DEMO_CSP,
                                 "Set-Cookie": "fixture=hidden", "Server": "private-runtime", "Location": "http://localhost:3101/",
                                 "X-Internal-Token": "fixture-only"}
        result = self.gateway.request("GET", "/demo/supply-chain")
        self.assertEqual(result.status, 200)
        self.assertEqual(result.headers["Content-Security-Policy"], DEMO_CSP)
        self.assertEqual(result.headers["Content-Type"], "text/html; charset=utf-8")
        self.assertTrue(set(result.headers) <= {"Content-Type", "Content-Security-Policy", "Cache-Control", "X-Content-Type-Options", "Referrer-Policy"})
        for csp in ("default-src 'none'", "sandbox allow-same-origin allow-scripts",
                    "sandbox ALLOW-SAME-ORIGIN", "sandbox allow-same-origin; sandbox allow-scripts"):
            self.response_headers["Content-Security-Policy"] = csp
            self.assertEqual(self.gateway.request("GET", "/demo/supply-chain").status, 502)

    def test_redirects_are_not_followed_and_do_not_relay_location(self):
        self.status = 302
        self.response_headers["Location"] = "http://evil.test/private"
        result = self.gateway.request("GET", "/")
        self.assertEqual(result.status, 502)
        self.assertEqual(len(self.calls), 1)
        self.assertNotIn("Location", result.headers)
        self.assertNotIn(b"evil.test", result.body)

    def test_response_limit_covers_declared_and_streamed_sizes_and_allows_pixel_assets(self):
        self.assertGreater(MAX_RESPONSE_BYTES, 860739)
        gateway = NextOfficeGateway(port=self.server.server_port, max_response_bytes=100)
        self.content = b"x" * 101
        self.assertEqual(gateway.request("GET", "/pixel-assets.json").status, 502)
        self.content = b"small"
        self.response_headers["Content-Length"] = "101"
        self.assertEqual(gateway.request("GET", "/pixel-assets.json").status, 502)

    def test_invalid_utf8_compression_or_api_shape_is_not_relayed(self):
        self.content = b"\xff"
        self.assertEqual(self.gateway.request("GET", "/assets/app.js").status, 502)
        self.content = b"{}"
        self.response_headers["Content-Encoding"] = "gzip"
        self.assertEqual(self.gateway.request("GET", "/api/office/snapshot").status, 502)
        del self.response_headers["Content-Encoding"]
        self.response_headers["Content-Type"] = "text/html"
        self.assertEqual(self.gateway.request("GET", "/api/office/snapshot").status, 502)

    def test_connection_failure_is_sanitized(self):
        with patch("office.next_gateway.http.client.HTTPConnection.connect", side_effect=OSError("fixture-sensitive-diagnostic")):
            result = self.gateway.request("GET", "/")
        self.assertEqual(result.status, 502)
        self.assertNotIn(b"fixture-sensitive", result.body)
        self.assertNotIn(b"127.0.0.1", result.body)

    def test_total_timeout_interrupts_waiting_for_response_headers(self):
        self.delay = .4
        gateway = NextOfficeGateway(port=self.server.server_port, timeout=.08)
        started = time.monotonic()
        result = gateway.request("GET", "/")
        self.assertEqual(result.status, 502)
        self.assertLess(time.monotonic() - started, .35)


if __name__ == "__main__":
    unittest.main()
