"""Voice static delivery only: no microphone, AI, model download, or production data."""
import http.client
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock, patch

import server as server_module


class VoiceStaticServerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.voice = self.root / "static" / "voice"
        self.voice.mkdir(parents=True)
        (self.voice / "index.html").write_text("<title>Local voice fixture</title>", encoding="utf-8")
        (self.root / "static" / "index.html").write_text("Main UI fixture", encoding="utf-8")
        self.root_patch = patch.object(server_module, "ROOT", self.root)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)
        self.office = Mock()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), server_module.handler_for(self.office))
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)

    def request(self, path, method="GET", body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        try:
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def asset(self, relative, content=b"local fixture"):
        path = self.voice / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def test_voice_aliases_deliver_the_same_local_index(self):
        for path in ("/voice", "/voice/", "/voice/index.html", "/voice/?lang=ko"):
            with self.subTest(path=path):
                status, headers, body = self.request(path)
                self.assertEqual(status, 200)
                self.assertIn(b"Local voice fixture", body)
                self.assertEqual(headers["Content-Type"], "text/html; charset=utf-8")
                self.assertEqual(headers["Content-Length"], str(len(body)))
                self.assertEqual(headers["Cache-Control"], "no-store")
                self.assertEqual(headers["X-Content-Type-Options"], "nosniff")

    def test_allowlisted_nested_assets_have_explicit_mime_types(self):
        for suffix, mime in server_module.VOICE_TYPES.items():
            relative = "vendor/model" + suffix
            self.asset(relative)
            with self.subTest(suffix=suffix):
                status, headers, body = self.request("/voice/" + relative)
                self.assertEqual(status, 200)
                self.assertEqual(headers["Content-Type"], mime)
                self.assertEqual(body, b"local fixture")

    def test_percent_encoded_names_are_decoded_before_file_lookup(self):
        self.asset("models/test model.onnx")
        status, _, body = self.request("/voice/models/test%20model.onnx")
        self.assertEqual(status, 200)
        self.assertEqual(body, b"local fixture")

    def test_large_models_are_streamed_without_read_bytes(self):
        payload = bytes(range(256)) * 8193
        self.asset("models/speaker.onnx", payload)
        with patch.object(Path, "read_bytes", side_effect=AssertionError("Do not load a model into memory")):
            status, headers, body = self.request("/voice/models/speaker.onnx")
        self.assertEqual(status, 200)
        self.assertEqual(int(headers["Content-Length"]), len(payload))
        self.assertEqual(body, payload)

    def test_voice_permissions_do_not_relax_the_main_ui_csp(self):
        _, voice_headers, _ = self.request("/voice")
        _, main_headers, _ = self.request("/")
        policy = voice_headers["Content-Security-Policy"]
        self.assertIn("script-src 'self' 'wasm-unsafe-eval'", policy)
        self.assertIn("worker-src 'self' blob:", policy)
        self.assertIn("connect-src 'self'", policy)
        self.assertIn("frame-ancestors 'none'", policy)
        self.assertNotIn("'unsafe-eval'", policy)
        self.assertNotIn("https:", policy)
        self.assertEqual(voice_headers["Permissions-Policy"], "microphone=(self), camera=()")
        self.assertNotIn("wasm-unsafe-eval", main_headers["Content-Security-Policy"])
        self.assertNotIn("worker-src", main_headers["Content-Security-Policy"])

    def test_hidden_paths_and_private_extensions_are_not_served(self):
        for relative in (".secret.json", ".private/report.txt", "models/.weights.onnx", "secrets.py", "office.sqlite3"):
            self.asset(relative, b"PRIVATE")
            with self.subTest(relative=relative):
                status, _, body = self.request("/voice/" + relative)
                self.assertEqual(status, 404)
                self.assertNotIn(b"PRIVATE", body)

    def test_traversal_and_windows_path_syntax_cannot_escape_voice(self):
        (self.root / "static" / "outside.json").write_text("PRIVATE", encoding="utf-8")
        attempts = ("../outside.json", "%2e%2e/outside.json", "%2E%2E%2Foutside.json",
                    "models/%2e%2e/%2e%2e/outside.json", "%5c..%5coutside.json",
                    "%2f../outside.json", "C%3a/outside.json", "index.html%00",
                    "%2esecret.json", "index.html%3asecret", "index.html.", "index.html%20",
                    "%252e%252e/outside.json")
        for relative in attempts:
            with self.subTest(relative=relative):
                status, _, body = self.request("/voice/" + relative)
                self.assertEqual(status, 404)
                self.assertNotIn(b"PRIVATE", body)

    def test_directories_and_missing_files_have_no_listing(self):
        self.asset("models/weights.onnx")
        for path in ("/voice/models", "/voice/models/", "/voice/missing.js"):
            with self.subTest(path=path):
                status, _, body = self.request(path)
                self.assertEqual(status, 404)
                self.assertNotIn(b"weights.onnx", body)

    def test_symlinks_cannot_serve_files_outside_voice(self):
        outside = self.root / "private.json"
        outside.write_text("PRIVATE", encoding="utf-8")
        try:
            (self.voice / "escape.json").symlink_to(outside)
        except OSError:
            self.skipTest("This Windows account cannot create symbolic links")
        status, _, body = self.request("/voice/escape.json")
        self.assertEqual(status, 404)
        self.assertNotIn(b"PRIVATE", body)

    def test_external_host_and_origin_remain_blocked(self):
        for headers in ({"Host": "attacker.example"}, {"Origin": "https://attacker.example"}):
            with self.subTest(headers=headers):
                status, _, _ = self.request("/voice", headers=headers)
                self.assertEqual(status, 403)

    def test_audio_posts_and_voice_api_routes_do_not_exist(self):
        protected = {"Content-Type": "application/json", "X-DAS-Office": "1"}
        for path, body, headers, expected in (
                ("/voice", b"RIFFaudio", {"Content-Type": "audio/wav", "X-DAS-Office": "1"}, 403),
                ("/voice/transcribe", json.dumps({"audio": "fixture"}), protected, 404),
                ("/api/voice/transcribe", json.dumps({"audio": "fixture"}), protected, 404),
                ("/voice/index.html", "{}", protected, 404)):
            with self.subTest(path=path):
                status, _, _ = self.request(path, "POST", body, headers)
                self.assertEqual(status, expected)
        self.office.submit_mission.assert_not_called()
        self.office.run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
