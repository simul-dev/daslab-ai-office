"""File/syntax/server tests; generated model code never executes on the host."""
import http.client
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

from office.prototypes import FILES, MAX_PREVIEWS, PrototypeWorkspace


class PrototypeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.proto = PrototypeWorkspace(self.root, self.root / "data")
        self.folder = self.root / "data" / "runs" / "first"
        self.context = self.proto.prepare(self.folder)

    def tearDown(self):
        self.proto.close()
        self.temp.cleanup()

    def file(self, name):
        return self.folder / "workspace" / name

    def request(self, path="/", method="GET", host=None):
        url = self.proto.open_preview(self.folder)
        port = urlsplit(url).port
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
        connection.request(method, path, headers={"Host": host or f"127.0.0.1:{port}"})
        response = connection.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        connection.close()
        return result

    def test_seed_is_bounded_and_never_claims_execution(self):
        self.assertEqual({p.name for p in self.file("index.html").parent.iterdir()}, set(FILES))
        self.assertFalse((self.root / "static").exists())
        result = self.proto.finish(self.folder)
        self.assertTrue(result["ready"], result)
        self.assertTrue(result["seed_only"])
        self.assertFalse(result["model_verified"])
        self.assertFalse(result["browser_verified"])
        self.assertFalse(result["applied_to_live"])
        self.assertEqual(result["changed_files"], [])
        self.assertEqual(self.context["project_id"], "daslab-growth")

    def test_finish_only_parses_generated_tests_never_executes_them(self):
        import subprocess
        real = subprocess.run
        with patch("office.prototypes.subprocess.run", wraps=real) as run:
            result = self.proto.finish(self.folder)
        self.assertTrue(result["ready"])
        self.assertEqual(len(run.call_args_list), 3)
        for call in run.call_args_list:
            self.assertEqual(call.args[0][1], "--check")
            self.assertFalse(call.kwargs["shell"])

    def test_revision_copies_only_pinned_prototype(self):
        self.file("README.md").write_text("Synthetic revised problem definition", encoding="utf-8")
        first = self.proto.finish(self.folder)
        self.assertEqual(first["changed_files"], ["README.md"])
        self.assertFalse(first["seed_only"])
        next_folder = self.root / "data" / "runs" / "next"
        self.proto.prepare(next_folder, self.folder)
        second = self.proto.finish(next_folder)
        self.assertTrue(second["ready"])
        self.assertEqual(first["artifacts"], second["artifacts"])

    def test_directory_escape_and_reuse_rejected(self):
        with self.assertRaises(ValueError):
            self.proto.prepare(self.root / "outside")
        with self.assertRaises(ValueError):
            self.proto.prepare(self.folder)

    def test_extra_file_or_directory_rejected(self):
        extra = self.file("unexpected.json")
        extra.write_text("{}")
        self.assertFalse(self.proto.finish(self.folder)["ready"])
        extra.unlink()
        extra.mkdir()
        self.assertFalse(self.proto.finish(self.folder)["ready"])

    def test_missing_and_large_files_rejected(self):
        original = self.file("README.md").read_bytes()
        self.file("README.md").unlink()
        self.assertFalse(self.proto.finish(self.folder)["ready"])
        self.file("README.md").write_bytes(original)
        self.file("README.md").write_bytes(b"x" * 500001)
        self.assertFalse(self.proto.finish(self.folder)["ready"])

    def test_rejects_linked_files_when_supported(self):
        target = self.root / "target.txt"
        target.write_text("outside")
        self.file("README.md").unlink()
        try:
            self.file("README.md").symlink_to(target)
        except OSError:
            self.skipTest("Creating symbolic links is unavailable on this host")
        self.assertFalse(self.proto.finish(self.folder)["ready"])

    def test_external_imports_and_unsafe_html_rejected(self):
        originals = {name: self.file(name).read_text(encoding="utf-8") for name in FILES}
        samples = [("app.js", 'import "https://example.invalid/script.js";'),
                   ("app.js", 'fetch("https://example.invalid/");'),
                   ("model.test.cjs", 'require("node:http");'),
                   ("style.css", '@import "https://example.invalid/style.css";'),
                   ("index.html", originals["index.html"].replace('src="model.js"', 'src="https://example.invalid/model.js"')),
                   ("index.html", originals["index.html"].replace("<body>", '<body onclick="alert(1)">'))]
        for name, value in samples:
            with self.subTest(name=name, value=value[:50]):
                self.file(name).write_text(value, encoding="utf-8")
                self.assertFalse(self.proto.finish(self.folder)["ready"])
                self.file(name).write_text(originals[name], encoding="utf-8")

    def test_sandbox_test_builtin_imports_are_only_syntax_checked(self):
        import subprocess
        real = subprocess.run
        self.file("model.test.cjs").write_text('''"use strict";
const model = require("./model.js");
const assert = require('node:assert/strict');
const fs = require ( "node:fs" );
const vm = require('node:vm');
throw new Error("Host must never execute this test file");
''', encoding="utf-8")
        with patch("office.prototypes.subprocess.run", wraps=real) as run:
            result = self.proto.finish(self.folder)
        self.assertTrue(result["ready"], result)
        self.assertEqual(len(run.call_args_list), 3)
        self.assertTrue(all(call.args[0][1] == "--check" for call in run.call_args_list))
        self.assertFalse(result["model_verified"])
        self.assertEqual(self.request("/model.test.cjs")[0], 404)

    def test_test_builtin_allowlist_does_not_apply_to_browser_code(self):
        for name in ("model.js", "app.js"):
            original = self.file(name).read_bytes()
            for module in ("./model.js", "node:assert/strict", "node:fs", "node:vm"):
                with self.subTest(name=name, module=module):
                    self.file(name).write_text('require("' + module + '");', encoding="utf-8")
                    with patch("office.prototypes.subprocess.run") as run:
                        result = self.proto.finish(self.folder)
                    self.assertFalse(result["ready"])
                    run.assert_not_called()
            self.file(name).write_bytes(original)

    def test_test_imports_reject_dynamic_external_and_network_access(self):
        snippets = [
            'require("node:http");', 'require("node:child_process");',
            'require("node:fs/promises");', 'require("fs");',
            'require("./app.js");', 'require("some-package");',
            'const name="node:fs"; require(name);',
            'require(`node:fs`);', 'require("node:" + "fs");',
            'const load = require; load("node:fs");',
            'require("node:fs", "extra");', 'import fs from "node:fs";',
            'require("node:fs"); fetch("https://example.invalid/");',
        ]
        for script in snippets:
            with self.subTest(script=script):
                self.file("model.test.cjs").write_text(script, encoding="utf-8")
                with patch("office.prototypes.subprocess.run") as run:
                    result = self.proto.finish(self.folder)
                self.assertFalse(result["ready"], result)
                run.assert_not_called()

    def test_invalid_js_and_unavailable_parser_block(self):
        self.file("app.js").write_text("function broken(")
        self.assertFalse(self.proto.finish(self.folder)["ready"])
        with patch("office.prototypes.shutil.which", return_value=None):
            result = self.proto.finish(self.folder)
        self.assertFalse(result["ready"])
        self.assertIn("Node.js unavailable", result["error"])

    def test_preview_is_isolated_and_read_only(self):
        status, headers, body = self.request()
        self.assertEqual(status, 200)
        self.assertIn(b"DAS Lab", body)
        self.assertIn("connect-src 'none'", headers["Content-Security-Policy"])
        self.assertIn("form-action 'none'", headers["Content-Security-Policy"])
        self.assertEqual(self.request("/", "POST")[0], 405)
        self.assertEqual(self.request("/api/org")[0], 404)
        self.assertEqual(self.request("/model.test.cjs")[0], 404)
        self.assertEqual(self.request("/README.md")[0], 404)
        self.assertEqual(self.request("/%2e%2e/index.html")[0], 404)
        self.assertEqual(self.request("/", host="external.invalid")[0], 403)

    def test_preview_is_frozen_and_rejects_mutated_pins(self):
        result = self.proto.finish(self.folder)
        url = self.proto.open_preview(self.folder, result["artifacts"])
        before = self.request("/app.js")[2]
        self.file("app.js").write_bytes(before + b"\n// later edit\n")
        self.assertEqual(self.request("/app.js")[2], before)
        self.assertEqual(self.proto.open_preview(self.folder), url)
        with self.assertRaisesRegex(ValueError, "no longer matches"):
            self.proto.open_preview(self.folder, result["artifacts"])

    def test_preview_lru_closes_oldest_server_and_reopens_preserved_files(self):
        self.assertEqual(MAX_PREVIEWS, 16)
        with patch("office.prototypes.MAX_PREVIEWS", 2):
            first_receipt = self.proto.finish(self.folder)
            self.proto.open_preview(self.folder, first_receipt["artifacts"])
            second = self.root / "data" / "runs" / "second"
            third = self.root / "data" / "runs" / "third"
            self.proto.prepare(second)
            self.proto.prepare(third)
            second_receipt = self.proto.finish(second)
            self.proto.open_preview(second, second_receipt["artifacts"])
            second_entry = self.proto.servers[str(second)]
            before = {path.name: path.read_bytes() for path in (second / "workspace").iterdir()}
            baseline = (second / "prototype-baseline.json").read_bytes()
            # Successful reuse makes the first preview newer than the second.
            self.proto.open_preview(self.folder, first_receipt["artifacts"])
            self.assertEqual(list(self.proto.servers), [str(second), str(self.folder)])
            self.proto.open_preview(second)
            self.assertEqual(list(self.proto.servers), [str(self.folder), str(second)])
            self.proto.open_preview(self.folder)
            self.proto.open_preview(third)
            self.assertEqual(len(self.proto.servers), 2)
            self.assertIn(str(self.folder), self.proto.servers)
            self.assertNotIn(str(second), self.proto.servers)
            self.assertEqual(second_entry[0].socket.fileno(), -1)
            self.assertFalse(second_entry[2].is_alive())
            self.assertEqual(before, {path.name: path.read_bytes() for path in (second / "workspace").iterdir()})
            self.assertEqual(baseline, (second / "prototype-baseline.json").read_bytes())
            reopened = self.proto.open_preview(second, second_receipt["artifacts"])
            port = urlsplit(reopened).port
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
            connection.request("GET", "/", headers={"Host": f"127.0.0.1:{port}"})
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            self.assertEqual(response.read(), before["index.html"])
            connection.close()
            self.assertEqual(len(self.proto.servers), 2)

    def test_evicted_preview_rechecks_artifact_pins_before_cache_mutation(self):
        with patch("office.prototypes.MAX_PREVIEWS", 1):
            pinned = self.proto.finish(self.folder)["artifacts"]
            self.proto.open_preview(self.folder, pinned)
            other = self.root / "data" / "runs" / "other"
            self.proto.prepare(other)
            self.proto.open_preview(other)
            other_server = self.proto.servers[str(other)][0]
            self.file("app.js").write_text(self.file("app.js").read_text(encoding="utf-8") + "\n// changed after eviction\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "no longer matches"):
                self.proto.open_preview(self.folder, pinned)
            self.assertEqual(list(self.proto.servers), [str(other)])
            self.assertNotEqual(other_server.socket.fileno(), -1)


if __name__ == "__main__":
    unittest.main()
