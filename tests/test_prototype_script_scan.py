"""Regex literals cannot hide executable forbidden prototype operations."""
import subprocess
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from office.prototypes import PrototypeWorkspace, _mask_regex_literals


class PrototypeScriptScanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.proto = PrototypeWorkspace(self.root, self.root / "data")
        self.addCleanup(self.proto.close)
        self.folder = self.root / "data/run"
        self.proto.prepare(self.folder)
        self.script = self.folder / "workspace/model.test.cjs"

    def finish(self, source):
        self.script.write_text(source, encoding="utf-8")
        return self.proto.finish(self.folder)

    def test_require_pattern_in_matchall_is_data_and_host_only_checks_syntax(self):
        source = r'''const source = "fixture";
const calls=[...source.matchAll(/require\(([^)]*)\)/g)].map(x=>x[1]);
throw new Error("Never execute this generated fixture on the host");'''
        real = subprocess.run
        with patch("office.prototypes.subprocess.run", wraps=real) as run:
            result = self.finish(source)
        self.assertTrue(result["ready"], result)
        self.assertFalse(result["model_verified"])
        self.assertEqual(len(run.call_args_list), 3)
        self.assertTrue(all(call.args[0][1] == "--check" for call in run.call_args_list))
        self.assertEqual(self.script.read_text(encoding="utf-8"), source)

    def test_escaped_slashes_classes_and_nested_template_regexes_are_recognized(self):
        sources = [r'''const a=/[\/]require\("x"\)/g; const b=/[/*]fetch/;''',
                   r'''const a="slash / and quote '\""; const b=/* / */ /require/;''',
                   r'''const x = `${`${(/require/).source}`}`;''',
                   r'''const x = [ /fetch/, /[\]\\/]require/ ];''']
        for source in sources:
            with self.subTest(source=source):
                result = self.finish(source)
                self.assertTrue(result["ready"], result)

    def test_require_pattern_and_readme_execution_marker_string_work_together(self):
        source = r'''const source = "fixture";
const calls=[...source.matchAll(/require\(([^)]*)\)/g)].map(x=>x[1]);
const marker='\n<!-- EXECUTION_RECORD -->';
throw new Error("Never execute this generated fixture on the host");'''
        result = self.finish(source)
        self.assertTrue(result["ready"], result)
        self.assertEqual(self.script.read_text(encoding="utf-8"), source)
        for marker in (r'''const marker="<!-- quote ' --> #!";''',
                       r'''const marker=`<!-- raw ' --> #! ${/safe/.source}`;''',
                       r'''const marker=/<!-- ' --> #!/;'''):
            with self.subTest(marker=marker):
                result = self.finish(source + "\n" + marker.replace("const marker", "const extra"))
                self.assertTrue(result["ready"], result)

    def test_regex_followed_by_actual_forbidden_calls_is_rejected_before_node(self):
        sources = [r'''const pattern=/require\(([^)]*)\)/g; require("node:crypto");''',
                   r'''const pattern=/fetch/; fetch("https://example.invalid");''',
                   r'''const pattern=/import/; import("node:crypto");''',
                   r'''const pattern=/WebSocket/; new WebSocket("wss://example.invalid");''',
                   r'''const pattern=/require/; const moduleName="node:fs"; require(moduleName);''']
        for source in sources:
            with self.subTest(source=source), patch("office.prototypes.subprocess.run") as run:
                result = self.finish(source)
                self.assertFalse(result["ready"])
                self.assertIn("imports", result["error"])
                run.assert_not_called()

    def test_division_and_allowed_require_operands_cannot_mask_real_calls(self):
        sources = [r'''a / require("node:crypto") / b;''',
                   r'''a / /* note */ require("node:crypto") / b;''',
                   r'''a /= require("node:crypto");''',
                   r'''const x = require("node:fs") / require("node:crypto") / b;''',
                   r'''const x = /safe/.test("a") / fetch("https://example.invalid") / b;''',
                   r'''let i=1; i++ / require("node:crypto") / b;''',
                   r'''const o={return:1}; o.return / require("node:crypto") / b;''']
        for source in sources:
            with self.subTest(source=source), patch("office.prototypes.subprocess.run") as run:
                self.assertFalse(self.finish(source)["ready"])
                run.assert_not_called()

    def test_strings_comments_and_template_expressions_do_not_hide_actual_calls(self):
        sources = [r'''const text=" = / "; require("node:crypto"); const end="/";''',
                   r'''/* = / */ require("node:crypto");''',
                   'const s=1; // = /\nrequire("node:crypto");',
                   r'''const text = `${require("node:crypto")}`;''',
                   r'''const text = `${`${require("node:crypto")}`}`;''',
                   r'''const text = `${/safe/.source} ${fetch("https://example.invalid")}`;''']
        for source in sources:
            with self.subTest(source=source), patch("office.prototypes.subprocess.run") as run:
                self.assertFalse(self.finish(source)["ready"])
                run.assert_not_called()

    def test_ambiguous_slashes_and_invalid_regex_stay_visible(self):
        for source in ('value / require("node:crypto") / divisor', r'const pattern=/require',
                       'function f() { return /require/; }'):
            with self.subTest(source=source):
                self.assertIn("require", _mask_regex_literals(source))
        source = 'const text="/require/"; /* /fetch/ */'
        self.assertEqual(_mask_regex_literals(source), source)

    def test_html_line_comments_and_hashbang_cannot_confuse_later_quote_boundaries(self):
        node = shutil.which("node")
        self.assertIsNotNone(node)
        for prefix in ("<!-- '", "--> '", "#! /usr/bin/node '"):
            source = prefix + "\nconst s='= /'; require(\"node:crypto\"); const t='/';"
            with self.subTest(prefix=prefix):
                self.script.write_text(source, encoding="utf-8")
                syntax = subprocess.run([node, "--check", str(self.script)], capture_output=True, text=True,
                                        timeout=20, shell=False)
                self.assertEqual(syntax.returncode, 0, syntax.stderr)
                self.assertEqual(_mask_regex_literals(source), source)
                with patch("office.prototypes.subprocess.run") as run:
                    self.assertFalse(self.proto.finish(self.folder)["ready"])
                    run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
