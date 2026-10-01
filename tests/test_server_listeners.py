"""Two real loopback sockets, one stateful engine double, offline OAuth only."""
import http.client
import json
import os
import socket
import tempfile
import threading
import unittest
from http.cookies import SimpleCookie
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlencode, urlsplit

from office.auth import OfficeAuth, SESSION_COOKIE, STATE_COOKIE, GITHUB_ISSUER
from server import _bind_listeners, _listener_configuration, _serve_listeners, main


def available_ports():
    with socket.socket() as first, socket.socket() as second:
        first.bind(('127.0.0.1', 0))
        second.bind(('127.0.0.1', 0))
        return first.getsockname()[1], second.getsockname()[1]


def github_auth():
    return OfficeAuth(
        {'mode': 'github', 'public_origin': 'https://office.example', 'allowed_github_ids': [1234]},
        environ={'DAS_OFFICE_GITHUB_CLIENT_ID': 'offline-client',
                 'DAS_OFFICE_GITHUB_CLIENT_SECRET': 'offline-secret'},
        http=Mock(side_effect=[
            {'access_token': 'offline-provider-token', 'token_type': 'bearer', 'scope': 'read:user'},
            {'id': 1234, 'login': 'office-owner', 'type': 'User'},
        ]))


class ListenerConfigurationTests(unittest.TestCase):
    def test_single_listener_preserves_the_explicit_auth_mode(self):
        for auth in (OfficeAuth(), github_auth()):
            self.assertEqual(_listener_configuration(8772, auth), [(8772, auth)])

    def test_public_listener_requires_distinct_ports_and_complete_github_auth(self):
        with self.assertRaisesRegex(ValueError, 'GitHub'):
            _listener_configuration(8772, OfficeAuth(), 8774)
        for local, public in ((8772, 8772), (0, 8774), (8772, 0), (8772, 65536), (8772, True)):
            with self.subTest(local=local, public=public), self.assertRaises(ValueError):
                _listener_configuration(local, github_auth(), public)
        with self.assertRaises(ValueError):
            OfficeAuth({'mode': 'github', 'public_origin': 'https://office.example',
                        'allowed_github_ids': [1234]}, environ={})

    def test_failed_second_bind_closes_the_first_socket_without_serving(self):
        local, _ = available_ports()
        with socket.socket() as occupied:
            occupied.bind(('127.0.0.1', 0))
            occupied.listen(1)
            configuration = _listener_configuration(local, github_auth(), occupied.getsockname()[1])
            with self.assertRaises(OSError):
                _bind_listeners(Mock(), Mock(), configuration)
            with socket.socket() as retry:
                retry.bind(('127.0.0.1', local))

    def test_missing_public_auth_settings_fail_before_engine_or_sockets_start(self):
        with tempfile.TemporaryDirectory() as temporary:
            missing = Path(temporary) / 'missing-auth.json'
            with patch('sys.argv', ['server.py', '--port', '8772', '--public-port', '8774',
                                    '--auth-config', str(missing)]), \
                    patch('server.clear_server_secrets'), \
                    patch('server.InstanceLock') as lock, patch('server.Office') as office, \
                    patch('office.organization.OrganizationEngine') as engine, \
                    patch('server._bind_listeners') as bind:
                with self.assertRaisesRegex(ValueError, 'GitHub'):
                    main()
                lock.assert_not_called()
                office.assert_not_called()
                engine.assert_not_called()
                bind.assert_not_called()

    def test_stopped_listener_stops_the_other_serving_loop(self):
        stopped = threading.Event()
        first = Mock(serve_forever=Mock())
        second = Mock(serve_forever=Mock(side_effect=lambda **kwargs: stopped.wait(3)),
                      shutdown=Mock(side_effect=stopped.set))
        with self.assertRaisesRegex(RuntimeError, '종료'):
            _serve_listeners([first, second])
        self.assertTrue(stopped.is_set())
        second.shutdown.assert_called_once()

    def test_main_builds_one_engine_and_cleans_both_listeners(self):
        auth = github_auth()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            settings = root / 'auth.json'
            settings.write_text('{"mode":"github"}')
            office, engine, lock = Mock(), Mock(), Mock()
            startup_order = []
            servers = [Mock(), Mock()]
            for server, port in zip(servers, (8772, 8774)):
                server.server_port = port
            with patch('sys.argv', ['server.py', '--port', '8772', '--public-port', '8774',
                                    '--auth-config', str(settings), '--data-dir', str(root / 'data')]), \
                    patch('server.OfficeAuth', side_effect=[auth, OfficeAuth()]), \
                    patch('server.clear_server_secrets', side_effect=lambda config: startup_order.append('clear')) as clear, \
                    patch('server.InstanceLock', return_value=lock), \
                    patch('server.Office', side_effect=lambda *a, **kw: startup_order.append('office') or office) as create_office, \
                    patch('office.organization.OrganizationEngine', side_effect=lambda *a, **kw: startup_order.append('engine') or engine) as create_engine, \
                    patch('server._bind_listeners', return_value=servers) as bind, \
                    patch('server._serve_listeners', side_effect=KeyboardInterrupt), patch('builtins.print'):
                main()
            create_office.assert_called_once()
            create_engine.assert_called_once()
            clear.assert_called_once_with({'mode': 'github'})
            self.assertEqual(startup_order, ['clear', 'office', 'engine'])
            self.assertIs(bind.call_args.args[0], office)
            self.assertIs(bind.call_args.args[1], engine)
            self.assertEqual([item[1].mode for item in bind.call_args.args[2]], ['local', 'github'])
            for server in servers:
                server.server_close.assert_called_once()
            office.close.assert_called_once()
            engine.close.assert_called_once()
            lock.close.assert_called_once()


class DualListenerHTTPTests(unittest.TestCase):
    def setUp(self):
        self.auth = github_auth()
        self.revision = 0
        self.engine = SimpleNamespace(snapshot=self.snapshot,
                                      standing=SimpleNamespace(snapshot=self.snapshot, tick=self.tick))
        ports = available_ports()
        self.servers = _bind_listeners(Mock(), self.engine,
                                      _listener_configuration(ports[0], self.auth, ports[1]))
        self.threads = [threading.Thread(target=s.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
                        for s in self.servers]
        for thread in self.threads:
            thread.start()

    def tearDown(self):
        for server in self.servers:
            server.shutdown()
            server.server_close()
        for thread in self.threads:
            thread.join(3)
            self.assertFalse(thread.is_alive())

    def snapshot(self):
        return {'revision': self.revision, 'employees': [], 'missions': []}

    def tick(self):
        self.revision += 1
        return self.snapshot()

    def request(self, public, path, method='GET', *, cookie=None, headers=None):
        port = self.servers[int(public)].server_port
        supplied = {'Host': 'office.example' if public else f'127.0.0.1:{port}'}
        if method == 'POST':
            supplied.update({'Content-Type': 'application/json', 'X-DAS-Office': '1'})
            if public:
                supplied['Origin'] = 'https://office.example'
        if cookie:
            supplied['Cookie'] = cookie
        for key, value in (headers or {}).items():
            if value is None:
                supplied.pop(key, None)
            else:
                supplied[key] = value
        connection = http.client.HTTPConnection('127.0.0.1', port, timeout=3)
        try:
            connection.request(method, path, body=b'{}' if method == 'POST' else None, headers=supplied)
            response = connection.getresponse()
            return response.status, response.headers, response.read()
        finally:
            connection.close()

    def denied(self, status, *args, **kwargs):
        try:
            result = self.request(*args, **kwargs)
        except (ConnectionAbortedError, ConnectionResetError) as exc:
            if os.name != 'nt' or getattr(exc, 'winerror', None) not in (10053, 10054):
                raise
            return
        self.assertEqual(result[0], status, result[2])

    @staticmethod
    def cookie(headers, name):
        for value in headers.get_all('Set-Cookie', []):
            parsed = SimpleCookie(value)
            if name in parsed:
                return name + '=' + parsed[name].value
        raise AssertionError('Missing expected cookie')

    def login(self):
        status, headers, _ = self.request(True, '/auth/login')
        self.assertEqual(status, 303)
        state = parse_qs(urlsplit(headers['Location']).query)['state'][0]
        binding = self.cookie(headers, STATE_COOKIE)
        status, headers, _ = self.request(True, '/auth/callback?' + urlencode({'state': state, 'code': 'offline-code', 'iss': GITHUB_ISSUER}), cookie=binding)
        self.assertEqual(status, 303)
        return self.cookie(headers, SESSION_COOKIE)

    def test_local_heartbeat_and_authenticated_public_ui_share_one_engine(self):
        self.assertEqual(self.request(False, '/api/org/standing')[0], 200)
        self.assertEqual(self.request(False, '/api/org/standing/tick', 'POST')[0], 200)
        self.denied(401, True, '/api/org')
        session = self.login()
        public = json.loads(self.request(True, '/api/org', cookie=session)[2])
        self.assertEqual(public['revision'], 1)
        self.assertEqual(self.request(True, '/api/org/standing/tick', 'POST', cookie=session)[0], 200)
        self.assertEqual(json.loads(self.request(False, '/api/org')[2])['revision'], 2)
        self.assertEqual(self.request(True, '/auth/logout', 'POST', cookie=session)[0], 303)
        self.denied(401, True, '/api/org', cookie=session)
        self.assertEqual(self.request(False, '/api/org/standing/tick', 'POST')[0], 200)
        self.assertEqual(self.revision, 3)

    def test_public_host_cannot_use_local_listener_and_local_host_cannot_use_public(self):
        local_host = f'127.0.0.1:{self.servers[0].server_port}'
        self.denied(403, False, '/api/org', headers={'Host': 'office.example'})
        self.denied(403, True, '/api/org', headers={'Host': local_host})
        self.denied(403, False, '/api/org/standing/tick', 'POST', headers={'Origin': 'https://office.example'})
        session = self.login()
        for origin in (None, 'http://' + local_host, 'https://external.example'):
            self.denied(403, True, '/api/org/standing/tick', 'POST', cookie=session, headers={'Origin': origin})
        self.denied(401, True, '/api/org/standing/tick', 'POST')
        self.assertEqual(self.revision, 0, 'denied cross-boundary requests cannot dispatch work')


if __name__ == '__main__':
    unittest.main()
