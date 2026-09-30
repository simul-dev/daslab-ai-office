"""Verifier boundary tests. No real browser or external network is launched."""
import asyncio
import copy
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call, patch

from office.browser_verification import (
    BrowserUnavailable, BrowserVerifier, _allowed_request, _artifacts,
    _origin, _preview_url, _route_request, _keyboard_skip_link,
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
        # Inert synthetic credentials: only URL parsing is exercised, never a request.
        # Keep the loopback-looking username case to reject misleading host prefixes.
        for url in ('https://127.0.0.1:54321/', 'http://localhost:54321/',
                    'http://example.com:54321/', 'http://127.0.0.1/',
                    'http://127.0.0.1:80/', 'http://127.0.0.1:54321/path',
                    URL + '?target=x', URL + '#fragment',
                    'http://test-user:test-pass@127.0.0.1:54321/', 'file:///tmp/index.html',
                    'http://127.0.0.1:99999/', 'http://127.0.0.1:test-pass@evil.example/'):
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

    def keyboard_page(self, **state):
        focused = dict(focused=True, focusVisible=True, visible=True, inViewport=True,
                       unobscured=True, active='a')
        focused.update(state)
        link = SimpleNamespace(count=AsyncMock(return_value=1), evaluate=AsyncMock(return_value=focused))
        return SimpleNamespace(
            locator=Mock(return_value=link), keyboard=SimpleNamespace(press=AsyncMock()),
            evaluate=AsyncMock(), wait_for_function=AsyncMock(), screenshot=AsyncMock(),
        )

    def keyboard_check(self, page, label='desktop'):
        self.output.mkdir(exist_ok=True)
        receipt = {'screenshots': []}
        detail = asyncio.run(_keyboard_skip_link(page, self.output, receipt, label))
        return detail, receipt

    def test_keyboard_uses_native_tab_enter_and_saves_focus_evidence_for_both_sizes(self):
        for label in ('desktop', 'mobile'):
            page = self.keyboard_page()
            detail, receipt = self.keyboard_check(page, label)
            self.assertEqual(page.keyboard.press.await_args_list, [call('Tab'), call('Enter')])
            page.wait_for_function.assert_awaited_once_with(
                'document.activeElement === document.querySelector("main#workspace")')
            page.screenshot.assert_awaited_once_with(
                path=str(self.output / (label + '-keyboard.png')), full_page=False, timeout=5000)
            self.assertEqual(receipt['screenshots'], [str(self.output / (label + '-keyboard.png'))])
            self.assertIn('document.activeElement to main#workspace', detail)
            self.assertIn(':focus-visible=true', detail)
            self.assertIn(label, detail)

    def test_keyboard_missing_link_fails_before_any_keypress(self):
        page = self.keyboard_page()
        page.locator.return_value.count.return_value = 0
        with self.assertRaisesRegex(ValueError, 'expected one'):
            self.keyboard_check(page)
        page.keyboard.press.assert_not_called()

    def test_keyboard_focus_on_other_element_is_not_a_pass(self):
        page = self.keyboard_page(focused=False, active='button#tab-work')
        with self.assertRaisesRegex(ValueError, 'document.activeElement=button#tab-work'):
            self.keyboard_check(page)
        page.keyboard.press.assert_awaited_once_with('Tab')
        page.screenshot.assert_awaited_once()

    def test_keyboard_hidden_offscreen_covered_or_nonvisible_focus_fails_before_enter(self):
        for key, reason in (('focusVisible', ':focus-visible'), ('visible', 'not visible'),
                            ('inViewport', 'not fully inside'), ('unobscured', 'not unobscured')):
            with self.subTest(key=key):
                page = self.keyboard_page(**{key: False})
                with self.assertRaisesRegex(ValueError, reason):
                    self.keyboard_check(page)
                page.keyboard.press.assert_awaited_once_with('Tab')

    def test_keyboard_enter_must_move_actual_focus_not_just_fragment(self):
        page = self.keyboard_page()
        page.wait_for_function.side_effect = TimeoutError('fragment changed but active element stayed')
        page.evaluate.side_effect = [None, 'body']
        with self.assertRaisesRegex(ValueError, 'actual focus to main#workspace; document.activeElement=body'):
            self.keyboard_check(page)
        self.assertEqual(page.keyboard.press.await_args_list, [call('Tab'), call('Enter')])

    def test_keyboard_focus_wait_cancellation_is_not_rewritten_as_failure(self):
        page = self.keyboard_page()
        page.wait_for_function.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            self.keyboard_check(page)

    def test_keyboard_evidence_never_overwrites_an_existing_file(self):
        self.output.mkdir()
        target = self.output / 'desktop-keyboard.png'
        target.write_bytes(b'existing evidence')
        page = self.keyboard_page()
        with self.assertRaisesRegex(ValueError, 'evidence file already exists'):
            self.keyboard_check(page)
        page.screenshot.assert_not_called()
        self.assertEqual(target.read_bytes(), b'existing evidence')

    def test_failed_keyboard_check_is_recorded_as_failed_verification(self):
        async def runner(url, directory, receipt):
            page = self.keyboard_page(inViewport=False)
            try:
                detail = await _keyboard_skip_link(page, directory, receipt, 'mobile')
                receipt['checks'].append({'name': 'keyboard_mobile', 'status': 'passed', 'detail': detail})
            except ValueError as exc:
                receipt['checks'].append({'name': 'keyboard_mobile', 'status': 'failed', 'detail': str(exc)})
        result = self.verify(runner)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['checks'][0]['name'], 'keyboard_mobile')
        self.assertIn('not fully inside the viewport', result['checks'][0]['detail'])
        self.assertEqual(len(result['screenshots']), 1)


if __name__ == '__main__':
    unittest.main()
