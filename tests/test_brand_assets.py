"""HTTP coverage for live brand assets using temporary files only."""
import http.client
import os
import stat
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import server as server_module


class BrandAssetTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.brand = self.root / "static" / "brand"
        self.brand.mkdir(parents=True)
        self.patch = patch.object(server_module, "ROOT", self.root)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.office = Mock()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), server_module.handler_for(self.office))
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={"poll_interval": 0.01}, daemon=True)
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

    def asset(self, relative, data=b"brand fixture"):
        path = self.brand / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def test_allowlisted_assets_use_explicit_mime_and_existing_security_headers(self):
        for suffix, mime in server_module.BRAND_TYPES.items():
            self.asset("logos/daslab" + suffix)
            with self.subTest(suffix=suffix):
                status, headers, body = self.request("/brand/logos/daslab" + suffix)
                self.assertEqual(status, 200)
                self.assertEqual(headers["Content-Type"], mime)
                self.assertEqual(body, b"brand fixture")
                self.assertEqual(headers["Content-Length"], str(len(body)))
                self.assertEqual(headers["Cache-Control"], "no-store")
                self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
                self.assertIn("img-src 'self' data:", headers["Content-Security-Policy"])
                self.assertNotIn("wasm-unsafe-eval", headers["Content-Security-Policy"])

    def test_encoded_names_and_query_strings_deliver_assets(self):
        self.asset("daslab dark.png")
        status, _, body = self.request("/brand/daslab%20dark.png?v=1")
        self.assertEqual(status, 200)
        self.assertEqual(body, b"brand fixture")

    def test_traversal_hidden_files_and_unapproved_extensions_are_rejected(self):
        (self.root / "static" / "outside.png").write_bytes(b"PRIVATE")
        for name in (".secret.png", ".private/logo.png", "secrets.json", "code.js", "page.html"):
            self.asset(name, b"PRIVATE")
        attempts = ("../outside.png", "%2e%2e/outside.png", "%2e%2e%2foutside.png",
                    "logos/%2e%2e/%2e%2e/outside.png", "%5c..%5coutside.png",
                    "%2f../outside.png", "C%3a/outside.png", "logo.png%00",
                    ".secret.png", "%2esecret.png", ".private/logo.png",
                    "logo.png%3asecret", "logo.png.", "logo.png%20",
                    "secrets.json", "code.js", "page.html", "%252e%252e/outside.png")
        for relative in attempts:
            with self.subTest(relative=relative):
                status, _, body = self.request("/brand/" + relative)
                self.assertEqual(status, 404)
                self.assertNotIn(b"PRIVATE", body)

    def test_directories_and_missing_files_have_no_listing(self):
        self.asset("logos/daslab.png")
        for path in ("/brand", "/brand/", "/brand/logos", "/brand/logos/", "/brand/missing.png"):
            with self.subTest(path=path):
                status, _, body = self.request(path)
                self.assertEqual(status, 404)
                self.assertNotIn(b"daslab.png", body)

    def test_file_and_directory_symlinks_cannot_expose_private_assets(self):
        outside = self.root / "private"
        outside.mkdir()
        (outside / "logo.png").write_bytes(b"PRIVATE")
        try:
            (self.brand / "escape.png").symlink_to(outside / "logo.png")
            (self.brand / "linked").symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("This account cannot create symbolic links")
        for path in ("/brand/escape.png", "/brand/linked/logo.png"):
            with self.subTest(path=path):
                status, _, body = self.request(path)
                self.assertEqual(status, 404)
                self.assertNotIn(b"PRIVATE", body)

    def test_cloud_reparse_files_work_but_junction_tags_and_hidden_attributes_do_not(self):
        logo = self.asset("daslab-dark.png")
        original_lstat = Path.lstat

        def tagged(tag, attributes):
            def lstat_with_tag(path, *args, **kwargs):
                info = original_lstat(path, *args, **kwargs)
                if path == logo:
                    return SimpleNamespace(st_mode=info.st_mode, st_reparse_tag=tag,
                                           st_file_attributes=attributes)
                return info
            return lstat_with_tag

        # Simulates file attributes, not a real OneDrive hydration operation.
        for tag, attributes, expected in ((0x9000001A, 0x400, 200),
                                          (0xA0000003, 0x400, 404),
                                          (0xA000000C, 0x400, 404),
                                          (0, stat.FILE_ATTRIBUTE_HIDDEN, 404)):
            with self.subTest(tag=tag, attributes=attributes), patch.object(
                    Path, "lstat", tagged(tag, attributes)):
                status, _, _ = self.request("/brand/daslab-dark.png")
                self.assertEqual(status, expected)

    @unittest.skipUnless(os.name == "nt", "Windows junction behavior")
    def test_directory_and_brand_root_junctions_cannot_expose_private_assets(self):
        import _winapi

        outside = self.root / "private"
        outside.mkdir()
        (outside / "logo.png").write_bytes(b"PRIVATE")
        linked = self.brand / "linked"
        _winapi.CreateJunction(str(outside), str(linked))
        try:
            status, _, body = self.request("/brand/linked/logo.png")
            self.assertEqual(status, 404)
            self.assertNotIn(b"PRIVATE", body)
        finally:
            linked.rmdir()  # Removes this temporary junction, not its target.
        self.brand.rmdir()
        _winapi.CreateJunction(str(outside), str(self.brand))
        try:
            status, _, body = self.request("/brand/logo.png")
            self.assertEqual(status, 404)
            self.assertNotIn(b"PRIVATE", body)
        finally:
            self.brand.rmdir()
        self.assertEqual((outside / "logo.png").read_bytes(), b"PRIVATE")

    def test_external_hosts_and_origins_remain_blocked(self):
        self.asset("daslab-dark.png")
        for headers in ({"Host": "attacker.example"}, {"Origin": "https://attacker.example"}):
            with self.subTest(headers=headers):
                self.assertEqual(self.request("/brand/daslab-dark.png", headers=headers)[0], 403)

    def test_brand_delivery_is_read_only(self):
        logo = self.asset("daslab-dark.png")
        status, _, _ = self.request("/brand/daslab-dark.png", "POST", "{}",
                                    {"Content-Type": "application/json", "X-DAS-Office": "1"})
        self.assertEqual(status, 404)
        self.assertEqual(logo.read_bytes(), b"brand fixture")
        self.office.submit_mission.assert_not_called()


if __name__ == "__main__":
    unittest.main()
