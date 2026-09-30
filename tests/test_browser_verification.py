"""Verifier boundary tests. No real browser or external network is launched."""
import asyncio
import copy
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from office.browser_verification import (
    BrowserUnavailable, BrowserVerifier, _allowed_request, _artifacts,
    _origin, _preview_url, _route_request,
)


HASHES = {'office.html': 'a' * 64, 'office.css': 'b' * 64, 'office.js': 'c' * 64}
URL = 'http://127.0.0.1:54321/'


class BrowserVerificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name) / 'evidence'

    def verify(self, runner, **kwargs):
        return BrowserVerifier(runner=runner, **kwargs).verify(URL, HASHES, self.output)

    def test_only_explicit_loopback_preview_roots_are_accepted(self):
        for url in (URL, 'http://[::1]:54321/'):
            self.assertEqual(_preview_url(url), url)
        for url in ('https://127.0.0.1:54321/', 'http://localhost:54321/',
                    'http://example.com:54321/', 'http://127.0.0.1/',
                    'http://127.0.0.1:80/', 'http://127.0.0.1:54321/path',
                    URL + '?target=x', URL + '#fragment',
                    'http://user:secret@127.0.0.1:54321/', 'file:///tmp/index.html',
                    'http://127.0.0.1:99999/', 'http://127.0.0.1:54321@evil.example/'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                _preview_url(url)

    def test_request_boundary_rejects_other_origins_and_mutation_methods(self):
        origin = _origin(URL)
        for path in ('office.js', 'brand/logo.svg', 'api/org', 'api/org/events?cursor=1'):
            self.assertTrue(_allowed_request(URL + path, 'GET', origin))
        self.assertTrue(_allowed_request(URL, 'HEAD', origin))
        for url in ('http://127.0.0.1:54322/', 'https://127.0.0.1:54321/',
                    'http://localhost:54321/', 'http://example.com:54321/',
                    'ws://127.0.0.1:54321/', 'file:///c:/private', 'data:text/html,x'):
            self.assertFalse(_allowed_request(url, 'GET', origin))
        for method in ('POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS', 'CONNECT'):
            self.assertFalse(_allowed_request(URL, method, origin))

    def test_pinned_artifact_validation_and_copy(self):
        pins = copy.deepcopy(HASHES)
        result = _artifacts(pins)
        pins['office.js'] = 'd' * 64
        self.assertEqual(result, HASHES)
        for name in ('../secret', '/office.html', 'brand/../secret.png',
                     'brand/a.png:payload', 'brand/a.png ', 'brand\\a.png',
                     'brand/nested//logo.svg', 'brand/a.js'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                _artifacts({**HASHES, name: 'a' * 64})
        for value in ({}, {'office.html': 'a' * 64}, {**HASHES, 'office.js': 'claim'}, []):
            with self.assertRaises(ValueError):
                _artifacts(value)

    def test_pass_has_pins_and_explicit_smoke_scope_without_quality_acceptance(self):
        async def runner(url, directory, receipt):
            self.assertEqual(url, URL)
            self.assertTrue(directory.is_dir())
            receipt['checks'].append({'name': 'organization_render', 'status': 'passed'})
        result = self.verify(runner)
        self.assertEqual(result['status'], 'passed', result)
        self.assertEqual(result['artifacts'], HASHES)
        self.assertFalse(result['aesthetic_acceptance'])
        self.assertFalse(result['business_acceptance'])
        self.assertIn('읽기 전용', result['scope'])
        self.assertGreaterEqual(result['elapsed_seconds'], 0)

    def test_failed_check_and_browser_error_cannot_be_reported_as_pass(self):
        async def failed_check(url, directory, receipt):
            receipt['checks'].extend([{'status': 'passed'}, {'status': 'failed'}])
        async def page_error(url, directory, receipt):
            receipt['checks'].append({'status': 'passed'})
            receipt['errors'].append('ReferenceError')
        async def no_checks(url, directory, receipt):
            pass
        for runner in (failed_check, page_error, no_checks):
            self.assertEqual(self.verify(runner)['status'], 'failed')

    def test_missing_runtime_is_unavailable_and_preserves_pins(self):
        with patch('office.browser_verification.importlib.util.find_spec', return_value=None):
            result = BrowserVerifier().verify(URL, HASHES, self.output)
        self.assertEqual(result['status'], 'unavailable', result)
        self.assertEqual(result['artifacts'], HASHES)
        self.assertIn('requirements-browser.txt', result['errors'][0])
        self.assertEqual(result['checks'], [])

    def test_no_browser_error_is_unavailable(self):
        async def runner(url, directory, receipt):
            raise BrowserUnavailable('No isolated browser installed')
        result = self.verify(runner)
        self.assertEqual(result['status'], 'unavailable')
        self.assertIn('No isolated browser', result['errors'][0])

    def test_cancelled_before_execution_does_not_call_runner(self):
        runner = AsyncMock()
        cancel = threading.Event()
        cancel.set()
        result = BrowserVerifier(runner=runner).verify(URL, HASHES, self.output, cancel)
        self.assertEqual(result['status'], 'failed')
        self.assertIn('cancelled', result['errors'][0])
        runner.assert_not_called()

    def test_running_cancellation_waits_for_runner_cleanup(self):
        cancel = threading.Event()
        finished = []
        async def runner(url, directory, receipt):
            cancel.set()
            try:
                await asyncio.sleep(30)
            finally:
                finished.append(True)
        result = BrowserVerifier(runner=runner).verify(URL, HASHES, self.output, cancel)
        self.assertEqual(result['status'], 'failed')
        self.assertIn('cancelled', result['errors'][0])
        self.assertEqual(finished, [True])

    def test_timeout_cannot_become_a_pass(self):
        async def runner(url, directory, receipt):
            receipt['checks'].append({'name': 'first_check', 'status': 'passed'})
            await asyncio.sleep(30)
        result = self.verify(runner, timeout_seconds=1)
        self.assertEqual(result['status'], 'failed')
        self.assertIn('time limit', result['errors'][0])

    def test_bad_inputs_never_launch_a_browser(self):
        runner = AsyncMock()
        verifier = BrowserVerifier(runner=runner)
        for url, pins in (('https://external.example/', HASHES), (URL, {})):
            result = verifier.verify(url, pins, self.output)
            self.assertEqual(result['status'], 'failed')
        runner.assert_not_called()

    def test_repeated_runs_use_separate_evidence_directories(self):
        directories = []
        async def runner(url, directory, receipt):
            directories.append(directory)
            (directory / 'desktop.png').write_bytes(b'evidence')
            receipt['checks'].append({'status': 'passed'})
        self.assertEqual(self.verify(runner)['status'], 'passed')
        self.assertEqual(self.verify(runner)['status'], 'passed')
        self.assertNotEqual(directories[0], directories[1])

    def route(self, url, method='GET', resource_type='eventsource', status=200):
        route = SimpleNamespace(
            request=SimpleNamespace(url=url, method=method, resource_type=resource_type),
            abort=AsyncMock(), continue_=AsyncMock(), fulfill=AsyncMock(),
            fetch=AsyncMock(return_value=SimpleNamespace(status=status)),
        )
        blocked, errors = [], []
        asyncio.run(_route_request(route, _origin(URL), blocked, errors))
        return route, blocked, errors

    def test_trusted_preview_events_keep_the_real_stream_open(self):
        route, blocked, errors = self.route(URL + 'api/org/events')
        route.continue_.assert_awaited_once_with()
        route.fetch.assert_not_called()
        route.fulfill.assert_not_called()
        self.assertEqual(blocked + errors, [])

    def test_event_stream_exception_cannot_escape_the_preview_boundary(self):
        for url, method in (('http://external.example:54321/api/org/events', 'GET'),
                            ('http://127.0.0.1:54322/api/org/events', 'GET'),
                            (URL + 'api/org/events', 'POST')):
            route, blocked, errors = self.route(url, method)
            route.continue_.assert_not_called()
            route.fetch.assert_not_called()
            route.abort.assert_awaited_once_with('blockedbyclient')
            self.assertEqual(len(blocked), 1)
        for url, kind in ((URL + 'api/org/events?redirect=elsewhere', 'eventsource'),
                          (URL + 'api/org/events/other', 'eventsource'),
                          (URL + 'api/org/events', 'fetch')):
            route, blocked, errors = self.route(url, resource_type=kind)
            route.continue_.assert_not_called()
            route.fetch.assert_awaited_once_with(max_redirects=0, timeout=5000)

    def test_other_redirects_are_rejected_without_following(self):
        route, blocked, errors = self.route(URL + 'office.js', resource_type='script', status=302)
        route.fetch.assert_awaited_once_with(max_redirects=0, timeout=5000)
        route.abort.assert_awaited_once_with('blockedbyclient')
        route.continue_.assert_not_called()
        route.fulfill.assert_not_called()
        self.assertEqual(len(blocked), 1)


if __name__ == '__main__':
    unittest.main()
