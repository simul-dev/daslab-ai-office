"""Real loopback HTTP and persistent pairing, with an isolated gateway double."""
import http.client
import json
import tempfile
import threading
import unittest
from http.cookies import SimpleCookie
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.parse import urlsplit

from office.auth import OfficeAuth, SESSION_COOKIE
from server import handler_for


class NextAuthHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        (root / 'static').mkdir()
        (root / 'static' / 'office.html').write_text('preserved office')
        (root / 'static' / 'brand').mkdir()
        for name in ('daslab-dark.png', 'daslab-light.png', 'private.png'):
            (root / 'static' / 'brand' / name).write_bytes(b'brand fixture')
        self.root_patch = patch('server.ROOT', root)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)
        self.config = {'mode': 'pairing', 'public_origin': 'https://office.example',
                       'session_db': str(root / 'sessions.sqlite3')}
        self.auth = OfficeAuth(self.config)
        token = urlsplit(self.auth.start_pairing()['url']).fragment.removeprefix('pair=')
        cookies = self.auth.pair_login(token)['cookies']
        self.cookie = SESSION_COOKIE + '=' + SimpleCookie(cookies[0])[SESSION_COOKIE].value
        self.gateway = Mock()
        self.gateway.handles.side_effect = lambda method, path: path in (
            '/', '/assets/office.js', '/api/office/snapshot', '/api/office/issues', '/demo/supply-chain')
        self.gateway.request.return_value = SimpleNamespace(
            status=200, body=b'new office', headers={'Content-Type': 'text/html',
            'Content-Security-Policy': "sandbox allow-scripts; default-src 'none'"})
        self.organization = Mock()
        self.organization.snapshot.return_value = {'old_missions': 23}
        self.start(self.auth)
        self.addCleanup(self.stop)

    def start(self, auth, *, gateway=True):
        self.server = ThreadingHTTPServer(('127.0.0.1', 0),
            handler_for(Mock(), self.organization, auth, self.gateway if gateway else None))
        self.server.daemon_threads = True
        self.host = 'office.example' if auth.mode != 'local' else f'127.0.0.1:{self.server.server_port}'
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={'poll_interval': .01}, daemon=True)
        self.thread.start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(3)

    def request(self, path, *, method='GET', authenticated=True, headers=None, body=None):
        supplied = {'Host': self.host}
        if authenticated:
            supplied['Cookie'] = self.cookie
        if method == 'POST':
            supplied.update({'Origin': 'https://office.example', 'Content-Type': 'application/json',
                             'X-DAS-Office': '1', 'X-Office-Next': '1'})
        for key, value in (headers or {}).items():
            if value is None:
                supplied.pop(key, None)
            else:
                supplied[key] = value
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        try:
            encoded = json.dumps(body or {}).encode() if method == 'POST' else None
            connection.request(method, path, body=encoded, headers=supplied)
            response = connection.getresponse()
            return response.status, response.headers, response.read()
        finally:
            connection.close()

    def test_private_page_assets_api_and_demo_require_the_existing_session(self):
        for path in ('/', '/office.html', '/assets/office.js', '/api/office/snapshot', '/demo/supply-chain'):
            with self.subTest(path=path):
                status, _, _ = self.request(path, authenticated=False)
                self.assertEqual(status, 303 if path in ('/', '/office.html') else 401)
        self.gateway.request.assert_not_called()

    def test_paired_root_uses_gateway_and_preserves_demo_sandbox_policy(self):
        status, headers, body = self.request('/')
        self.assertEqual((status, body), (200, b'new office'))
        self.assertEqual(headers.get_all('Content-Security-Policy'),
                         ["sandbox allow-scripts; default-src 'none'"])
        self.gateway.request.assert_called_once_with('GET', '/', b'', mutation_authorized=False)

    def test_old_office_and_staff_api_still_use_the_original_engine(self):
        self.assertEqual(self.request('/office.html')[2], b'preserved office')
        self.assertEqual(json.loads(self.request('/api/org')[2]), {'old_missions': 23})
        self.gateway.request.assert_not_called()

    def test_query_bearing_bookmarks_redirect_to_the_new_root(self):
        for path in ('/?refresh=1', '/index.html?from=phone'):
            status, headers, _ = self.request(path)
            self.assertEqual((status, headers['Location']), (303, '/'))
        self.gateway.request.assert_not_called()

    def test_new_assignment_requires_origin_both_headers_and_current_session(self):
        for headers in ({'Origin': 'https://other.example'}, {'Origin': None},
                        {'X-DAS-Office': None}, {'X-Office-Next': None}):
            with self.subTest(headers=headers):
                self.assertEqual(self.request('/api/office/issues', method='POST', headers=headers)[0], 403)
        self.gateway.request.assert_not_called()
        payload = {'instruction': 'bounded test', 'requestId': 'test-request'}
        self.assertEqual(self.request('/api/office/issues', method='POST', body=payload)[0], 200)
        self.assertEqual(json.loads(self.gateway.request.call_args.args[2]), payload)
        self.assertTrue(self.gateway.request.call_args.kwargs['mutation_authorized'])

    def test_logout_blocks_the_new_api_without_touching_other_sessions(self):
        self.assertEqual(self.request('/auth/logout', method='POST')[0], 303)
        self.assertEqual(self.request('/api/office/snapshot')[0], 401)
        self.gateway.request.assert_not_called()

    def test_existing_phone_session_survives_server_reconstruction(self):
        self.stop()
        self.start(OfficeAuth(self.config))
        self.assertEqual(self.request('/')[0], 200)

    def test_local_listener_shows_new_root_and_preserves_original_engine(self):
        self.stop()
        self.start(OfficeAuth())
        self.assertEqual(self.request('/')[2], b'new office')
        self.assertEqual(self.request('/api/office/snapshot')[0], 200)
        self.assertEqual(self.request('/office.html')[2], b'preserved office')
        self.assertEqual(json.loads(self.request('/api/org')[2]), {'old_missions': 23})

    def test_local_assignment_requires_exact_origin_and_both_headers(self):
        self.stop()
        self.start(OfficeAuth())
        origin = 'http://' + self.host
        for headers in ({'Origin': None}, {'Origin': 'https://other.example'},
                        {'Origin': origin, 'X-DAS-Office': None},
                        {'Origin': origin, 'X-Office-Next': None}):
            self.assertEqual(self.request('/api/office/issues', method='POST', headers=headers)[0], 403)
        self.gateway.request.assert_not_called()
        self.assertEqual(self.request('/api/office/issues', method='POST', headers={'Origin': origin})[0], 200)

    def test_only_trusted_local_listener_can_issue_and_cancel_pairing(self):
        self.assertEqual(self.request('/api/owner/pair/start', method='POST')[0], 404)
        self.stop()
        local = OfficeAuth()
        local.pairing_auth = self.auth
        self.start(local)
        self.assertTrue(json.loads(self.request('/api/auth')[2])['pairing_available'])
        self.assertEqual(self.request('/api/owner/pair/start', method='POST', headers={'Origin': None})[0], 403)
        headers = {'Origin': 'http://' + self.host}
        response = self.request('/api/owner/pair/start', method='POST', headers=headers)
        self.assertEqual(response[0], 200)
        token = urlsplit(json.loads(response[2])['url']).fragment.removeprefix('pair=')
        self.assertEqual(self.request('/api/owner/pair/cancel', method='POST', headers=headers)[0], 200)
        with self.assertRaises(PermissionError):
            self.auth.pair_login(token)

    def test_without_opt_in_local_root_remains_original(self):
        self.stop()
        self.start(OfficeAuth(), gateway=False)
        self.assertEqual(self.request('/')[2], b'preserved office')
        self.gateway.request.assert_not_called()

    def test_login_logos_are_public_but_other_brand_files_are_private(self):
        for path in ('/brand/daslab-dark.png', '/brand/daslab-light.png'):
            self.assertEqual(self.request(path, authenticated=False)[0], 200)
        for path in ('/brand/private.png', '/brand/%64aslab-dark.png', '/brand/'):
            self.assertEqual(self.request(path, authenticated=False)[0], 401)
        self.assertEqual(self.request('/qr-code.js', authenticated=False)[0], 401)

    def test_wrong_host_is_rejected_before_gateway(self):
        self.assertEqual(self.request('/', headers={'Host': 'other.example'})[0], 403)
        self.gateway.request.assert_not_called()


if __name__ == '__main__':
    unittest.main()
