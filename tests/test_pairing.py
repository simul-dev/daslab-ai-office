"""One-time trusted-PC pairing and persistent owner sessions, all offline."""
import http.client
import json
import socket
import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing
from http.cookies import SimpleCookie
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

from office.auth import AuthError, OfficeAuth, SESSION_COOKIE
from server import _bind_listeners, _listener_configuration


def config(root):
    return {"mode": "pairing", "public_origin": "https://office.example",
            "session_seconds": 2_592_000, "pairing_seconds": 600,
            "session_db": str(root / "data" / "owner-sessions.sqlite3")}


def available_ports():
    with socket.socket() as first, socket.socket() as second:
        first.bind(("127.0.0.1", 0))
        second.bind(("127.0.0.1", 0))
        return first.getsockname()[1], second.getsockname()[1]


def paired_token(result):
    parsed = urlsplit(result["url"])
    assert not parsed.query and parsed.fragment.startswith("pair=")
    return parsed.fragment[len("pair="):]


def session_cookie(cookies):
    parsed = SimpleCookie(cookies[0])
    return SESSION_COOKIE + "=" + parsed[SESSION_COOKIE].value


class PairingAuthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.clock = [1_000_000]
        self.auth = OfficeAuth(config(self.root), clock=lambda: self.clock[0])

    def test_one_time_pairing_persists_only_session_hash_across_restart(self):
        pair = self.auth.start_pairing()
        self.assertEqual((pair["expires_at"], pair["expires_in"]), (1_000_600, 600))
        token = paired_token(pair)
        result = self.auth.pair_login(token)
        cookie = session_cookie(result["cookies"])
        self.assertEqual(self.auth.current_user(cookie), {"id": "owner", "login": "owner"})
        with self.assertRaises(AuthError):
            self.auth.pair_login(token)
        with closing(sqlite3.connect(config(self.root)["session_db"])) as connection:
            rows = connection.execute("SELECT token_hash, expires_at FROM owner_sessions").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(rows[0][0]), 64)
        self.assertEqual(rows[0][1], 3_592_000)
        self.assertNotIn(token.encode("ascii"), Path(config(self.root)["session_db"]).read_bytes())
        self.assertNotIn(cookie.split("=", 1)[1].encode("ascii"), Path(config(self.root)["session_db"]).read_bytes())
        restarted = OfficeAuth(config(self.root), clock=lambda: self.clock[0])
        self.assertTrue(restarted.status(cookie)["authenticated"])
        restarted.logout(cookie)
        self.assertFalse(self.auth.status(cookie)["authenticated"])
        self.assertFalse(OfficeAuth(config(self.root), clock=lambda: self.clock[0]).status(cookie)["authenticated"])
        second = self.auth.start_pairing()
        second_cookie = session_cookie(self.auth.pair_login(paired_token(second))["cookies"])
        pending = self.auth.start_pairing()
        self.assertEqual(self.auth.revoke_paired_sessions(), {"revoked_count": 1})
        self.assertFalse(restarted.status(second_cookie)["authenticated"])
        with self.assertRaises(AuthError):
            self.auth.pair_login(paired_token(pending))

    def test_expiry_replacement_and_invalid_guess_limits(self):
        first = self.auth.start_pairing()
        second = self.auth.start_pairing()
        with self.assertRaises(AuthError):
            self.auth.pair_login(paired_token(first))
        for _ in range(24):
            with self.assertRaises(AuthError):
                self.auth.pair_login("A" * 43)
        self.assertIsNotNone(self.auth.pair_login(paired_token(second))["cookies"])
        expired = self.auth.start_pairing()
        self.clock[0] += 600
        with self.assertRaises(AuthError):
            self.auth.pair_login(paired_token(expired))

    def test_cancel_pending_pair_does_not_revoke_existing_device(self):
        first = self.auth.start_pairing()
        cookie = session_cookie(self.auth.pair_login(paired_token(first))["cookies"])
        pending = self.auth.start_pairing()
        self.assertEqual(self.auth.cancel_pairing(), {"cancelled": True})
        with self.assertRaises(AuthError):
            self.auth.pair_login(paired_token(pending))
        self.assertEqual(self.auth.current_user(cookie), {"id": "owner", "login": "owner"})

    def test_configuration_is_bounded_and_has_no_oauth_credentials(self):
        for invalid in ({"session_seconds": 2_592_001}, {"pairing_seconds": 601},
                        {"allowed_github_ids": [1234]}, {"session_db": "relative.db"},
                        {"public_origin": "http://office.example"}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                OfficeAuth({**config(self.root), **invalid})
        self.assertEqual(self.auth.status("")["login_url"], "/login")
        self.assertTrue(self.auth.status("")["required"])


class PairingHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.auth = OfficeAuth(config(Path(self.temp.name)))
        self.engine = SimpleNamespace(snapshot=lambda: {"revision": 1, "missions": []})
        ports = available_ports()
        self.servers = _bind_listeners(object(), self.engine, _listener_configuration(ports[0], self.auth, ports[1]))
        self.threads = [threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
                        for server in self.servers]
        for thread in self.threads:
            thread.start()

    def tearDown(self):
        for server in self.servers:
            server.shutdown()
            server.server_close()
        for thread in self.threads:
            thread.join(3)
            self.assertFalse(thread.is_alive())

    def request(self, public, path, method="GET", body=None, cookie=None, origin=True, host=None):
        port = self.servers[int(public)].server_port
        address = "office.example" if public else f"127.0.0.1:{port}"
        headers = {"Host": address if host is None else host}
        if cookie:
            headers["Cookie"] = cookie
        if method == "POST":
            headers.update({"Content-Type": "application/json", "X-DAS-Office": "1"})
            if origin:
                headers["Origin"] = "https://office.example" if public else "http://" + address
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
        try:
            encoded = json.dumps({} if body is None else body).encode("utf-8") if method == "POST" else None
            connection.request(method, path, body=encoded, headers=headers)
            response = connection.getresponse()
            return response.status, response.headers, response.read()
        finally:
            connection.close()

    def denied(self, status, *args, **kwargs):
        try:
            result = self.request(*args, **kwargs)
        except (ConnectionAbortedError, ConnectionResetError) as exc:
            if getattr(exc, "winerror", None) in (10053, 10054):
                return
            raise
        self.assertEqual(result[0], status, result[2])

    def test_local_pairing_public_private_boundary_and_logout(self):
        local_status = json.loads(self.request(False, "/api/auth")[2])
        self.assertTrue(local_status["pairing_available"])
        self.assertEqual(local_status["public_origin"], "https://office.example")
        public_status = json.loads(self.request(True, "/api/auth")[2])
        self.assertNotIn("pairing_available", public_status)
        self.assertEqual(self.request(True, "/api/org")[0], 401)
        self.denied(401, True, "/api/owner/pair/start", "POST")
        self.denied(403, False, "/api/owner/pair/start", "POST", origin=False)
        self.denied(403, False, "/api/owner/pair/start", "POST", host="office.example")
        status, _, raw = self.request(False, "/api/owner/pair/start", "POST")
        self.assertEqual(status, 200)
        token = paired_token(json.loads(raw))
        self.denied(403, True, "/auth/pair", "POST", {"token": token}, origin=False)
        self.denied(403, True, "/auth/pair", "POST", {"token": token}, host="127.0.0.1:8774")
        self.assertEqual(self.request(True, "/api/org")[0], 401)
        status, headers, raw = self.request(True, "/auth/pair", "POST", {"token": token})
        self.assertEqual(status, 200, raw)
        self.assertEqual(json.loads(raw)["user"]["login"], "owner")
        self.assertIn("Secure", headers["Set-Cookie"])
        self.assertIn("HttpOnly", headers["Set-Cookie"])
        self.assertIn("SameSite=Lax", headers["Set-Cookie"])
        cookie = session_cookie([headers["Set-Cookie"]])
        self.assertEqual(self.request(True, "/api/org", cookie=cookie)[0], 200)
        self.assertEqual(self.request(True, "/qr-code.js")[0], 401)
        self.assertEqual(self.request(True, "/auth/pair", "POST", {"token": token})[0], 403)
        self.assertEqual(self.request(True, "/auth/logout", "POST", cookie=cookie)[0], 303)
        self.assertEqual(self.request(True, "/api/org", cookie=cookie)[0], 401)
        second = json.loads(self.request(False, "/api/owner/pair/start", "POST")[2])
        second_status, second_headers, _ = self.request(True, "/auth/pair", "POST", {"token": paired_token(second)})
        self.assertEqual(second_status, 200)
        second_cookie = session_cookie([second_headers["Set-Cookie"]])
        self.denied(404, True, "/api/owner/pair/revoke-all", "POST", cookie=second_cookie)
        self.denied(403, False, "/api/owner/pair/revoke-all", "POST", origin=False)
        revoked = self.request(False, "/api/owner/pair/revoke-all", "POST")
        self.assertEqual((revoked[0], json.loads(revoked[2])), (200, {"revoked_count": 1}))
        self.assertEqual(self.request(True, "/api/org", cookie=second_cookie)[0], 401)
        pending = json.loads(self.request(False, "/api/owner/pair/start", "POST")[2])
        self.denied(401, True, "/api/owner/pair/cancel", "POST", cookie=second_cookie)
        self.denied(403, False, "/api/owner/pair/cancel", "POST", origin=False)
        self.assertEqual(self.request(False, "/api/owner/pair/cancel", "POST")[0], 200)
        self.denied(403, True, "/auth/pair", "POST", {"token": paired_token(pending)})
        self.assertEqual(self.request(False, "/api/org")[0], 200)


if __name__ == "__main__":
    unittest.main()
