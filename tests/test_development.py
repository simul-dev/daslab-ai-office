import http.client
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlsplit

from office.development import DevelopmentWorkspace


class DevelopmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / 'static/brand').mkdir(parents=True)
        (self.root / 'static/office.html').write_text('<html><head><link rel="stylesheet" href="/office.css"><script src="/office.js" defer></script></head><body><main id="workspace">Original</main></body></html>', encoding='utf-8')
        (self.root / 'static/office.css').write_text('body { color: black; }', encoding='utf-8')
        (self.root / 'static/office.js').write_text('"use strict";', encoding='utf-8')
        (self.root / 'static/brand/logo.svg').write_text('<svg xmlns="http://www.w3.org/2000/svg"><path d="M0 0"/></svg>', encoding='utf-8')
        (self.root / 'static/secret.txt').write_text('excluded')
        self.dev = DevelopmentWorkspace(self.root, self.root / 'data')
        self.folder = self.root / 'data/runs/one'
        self.dev.prepare(self.folder)

    def tearDown(self):
        self.dev.close()
        self.temp.cleanup()

    def change(self):
        (self.folder / 'workspace/static/office.css').write_text('body { color: navy; }', encoding='utf-8')

    def request(self, path='/', method='GET', host=None):
        url = self.dev.open_preview(self.folder, lambda: {'employees': [], 'missions': [{'id': 'abc', 'title': 'Real mission'}], 'revision': 1})
        port = urlsplit(url).port
        connection = http.client.HTTPConnection('127.0.0.1', port, timeout=3)
        connection.request(method, path, headers={'Host': host or f'127.0.0.1:{port}'})
        response = connection.getresponse()
        status, headers, body = response.status, dict(response.getheaders()), response.read()
        connection.close()
        return status, headers, body

    def test_copies_only_allowed_sources_and_requires_real_change(self):
        self.assertFalse((self.folder / 'workspace/static/secret.txt').exists())
        self.assertFalse(self.dev.finish(self.folder)['ready'])
        self.change()
        result = self.dev.finish(self.folder)
        self.assertTrue(result['ready'], result)
        self.assertEqual(result['changed_files'], ['office.css'])
        self.assertFalse(result['visual_verified'])
        self.assertFalse(result['applied_to_live'])
        self.assertIn('black', (self.root / 'static/office.css').read_text())

    def test_previous_verified_workspace_iteration(self):
        self.change()
        next_folder = self.root / 'data/runs/two'
        self.dev.prepare(next_folder, self.folder)
        self.assertIn('navy', (next_folder / 'workspace/static/office.css').read_text())
        self.assertFalse(self.dev.finish(next_folder)['ready'])

    def test_allow_unchanged_inherits_verified_artifacts_and_original_live_baseline(self):
        original_live = (self.root / 'static/office.css').read_bytes()
        original_baseline = json.loads((self.folder / 'development-baseline.json').read_text(encoding='utf-8'))['live_files']
        self.change()
        verified = self.dev.finish(self.folder)
        self.assertTrue(verified['ready'], verified)
        expected_css = (self.folder / 'workspace/static/office.css').read_bytes()
        # Later live edits must not silently replace the original conflict baseline.
        (self.root / 'static/office.css').write_text('body { color: red; }', encoding='utf-8')
        previous = self.folder
        for name in ('unchanged-review', 'unchanged-recheck'):
            with self.subTest(workspace=name):
                folder = self.root / 'data/runs' / name
                self.dev.prepare(folder, previous, allow_unchanged=True)
                result = self.dev.finish(folder)
                self.assertTrue(result['ready'], result)
                self.assertEqual(result['changed_files'], [])
                self.assertEqual(result['artifacts'], verified['artifacts'])
                files, baseline = self.dev.delivery_files(folder, verified['artifacts'])
                self.assertEqual(files['office.css'], expected_css)
                self.assertEqual(baseline, original_baseline)
                self.assertEqual(baseline['office.css'], hashlib.sha256(original_live).hexdigest())
                self.assertNotEqual(baseline['office.css'], hashlib.sha256(files['office.css']).hexdigest())
                self.assertFalse(result['visual_verified'])
                self.assertFalse(result['applied_to_live'])
                previous = folder

    def test_allow_unchanged_cannot_make_new_workspace_ready_without_real_change(self):
        folder = self.root / 'data/runs/new-without-source'
        self.dev.prepare(folder, allow_unchanged=True)
        result = self.dev.finish(folder)
        self.assertFalse(result['ready'], result)
        self.assertEqual(result['changed_files'], [])
        self.assertIn('No actual HTML or style change', result['error'])

    def test_allow_unchanged_rejects_unverified_previous_workspace(self):
        folder = self.root / 'data/runs/unverified-source'
        with self.assertRaisesRegex(ValueError, 'Previous workspace has not passed verification'):
            self.dev.prepare(folder, self.folder, allow_unchanged=True)
        self.assertFalse((folder / 'workspace/static/office.html').exists())

    def test_rejects_missing_ids_remote_scripts_invalid_js(self):
        self.change()
        html = self.folder / 'workspace/static/office.html'
        original = html.read_text()
        for bad in (original.replace('id="workspace"', ''), original.replace('/office.js', 'https://evil.invalid/a.js'), original.replace('<main', '<iframe></iframe><main')):
            html.write_text(bad)
            self.assertFalse(self.dev.finish(self.folder)['ready'])
        html.write_text(original)
        (self.folder / 'workspace/static/office.js').write_text('function {')
        self.assertFalse(self.dev.finish(self.folder)['ready'])

    def test_unexpected_files_and_svg_rejected(self):
        self.change()
        extra = self.folder / 'workspace/static/secret.py'
        extra.write_text('print(1)')
        self.assertFalse(self.dev.finish(self.folder)['ready'])
        extra.unlink()
        (self.folder / 'workspace/static/brand/logo.svg').write_text('<svg><script>alert(1)</script></svg>')
        self.assertFalse(self.dev.finish(self.folder)['ready'])

    def test_path_escape_and_duplicate_prepare(self):
        with self.assertRaises(ValueError):
            self.dev.prepare(self.root / 'outside')
        with self.assertRaises(ValueError):
            self.dev.prepare(self.folder)
        self.assertFalse(self.dev.finish(self.root / 'outside')['ready'])

    def test_symlink_rejected(self):
        target = self.root / 'outside'
        target.mkdir()
        link = self.folder / 'workspace/static/brand/link'
        try:
            link.symlink_to(target, target_is_directory=True)
        except OSError:
            self.skipTest('OS does not permit creating symlinks')
        self.assertFalse(self.dev.finish(self.folder)['ready'])

    def test_windows_reparse_attribute_rejected(self):
        original = Path.lstat
        target = self.folder / 'workspace/static/office.css'

        def fake_stat(path, *args, **kwargs):
            result = original(path, *args, **kwargs)
            if path == target:
                return SimpleNamespace(st_mode=result.st_mode, st_file_attributes=0x400)
            return result

        with patch.object(Path, 'lstat', fake_stat):
            self.assertFalse(self.dev.finish(self.folder)['ready'])

    def test_sse_initial_snapshot(self):
        self.change()
        url = self.dev.open_preview(self.folder, lambda: {'revision': 12, 'employees': [], 'missions': []})
        connection = http.client.HTTPConnection('127.0.0.1', urlsplit(url).port, timeout=3)
        connection.request('GET', '/api/org/events')
        response = connection.getresponse()
        self.assertEqual(response.status, 200)
        self.assertEqual(response.readline(), b'event: snapshot\n')
        self.assertIn(b'"revision":12', response.readline())
        response.close()
        connection.close()

    def test_http_read_only_allowlist_and_host(self):
        self.change()
        status, headers, body = self.request()
        self.assertEqual(status, 200)
        self.assertIn(b'development-preview-banner', body)
        self.assertIn(b'/preview-mode.js', body)
        self.assertIn("form-action 'none'", headers['Content-Security-Policy'])
        self.assertEqual(self.request('/office.css')[0], 200)
        self.assertEqual(self.request('/brand/logo.svg')[0], 200)
        for path in ('/../development-baseline.json', '/%2e%2e/secret', '/workspace', '/brand/', '/secret.txt', '/api/tasks'):
            self.assertEqual(self.request(path)[0], 404, path)
        self.assertEqual(self.request('/api/org/missions', 'POST')[0], 405)
        self.assertEqual(self.request(host='evil.invalid')[0], 403)
        snapshot = json.loads(self.request('/api/org')[2])
        self.assertEqual(snapshot['missions'][0]['title'], 'Real mission')
        detail = json.loads(self.request('/api/org/missions/abc')[2])
        self.assertEqual(detail['mission']['id'], 'abc')

    def test_preview_bytes_frozen_after_open(self):
        self.change()
        self.assertIn(b'navy', self.request('/office.css')[2])
        (self.folder / 'workspace/static/office.css').write_text('changed later')
        self.assertIn(b'navy', self.request('/office.css')[2])

    def test_pinned_delivery_rejects_post_verification_changes(self):
        self.change()
        expected = self.dev.finish(self.folder)['artifacts']
        self.dev.open_preview(self.folder, lambda: {}, expected_artifacts=expected)
        self.dev.close()
        (self.folder / 'workspace/static/office.css').write_text('body { color: red; }')
        with self.assertRaisesRegex(ValueError, 'no longer match'):
            self.dev.open_preview(self.folder, lambda: {}, expected_artifacts=expected)


if __name__ == '__main__':
    unittest.main()
