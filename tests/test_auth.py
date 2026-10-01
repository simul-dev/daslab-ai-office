"""Offline OAuth transport fixtures; no GitHub account or credential is used."""
import base64
import hashlib
import json
import threading
import unittest
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode, urlsplit
from unittest.mock import Mock, patch

from office.auth import (AuthError, OfficeAuth, SESSION_COOKIE, STATE_COOKIE, TOKEN_URL,
                         USER_URL, GITHUB_ISSUER, AUTH_FAILURE_REASONS, auth_failure_reason, _github_json, _NoRedirect)


CONFIG = {"mode": "github", "public_origin": "https://office.example.test", "allowed_github_ids": [12345]}
ENV = {"DAS_OFFICE_GITHUB_CLIENT_ID": "offline-client", "DAS_OFFICE_GITHUB_CLIENT_SECRET": "offline-secret"}


def pair(cookie):
    return cookie.split(";", 1)[0]


class OfficeAuthTests(unittest.TestCase):
    def setUp(self):
        self.time = [1000.0]
        self.calls = []
        self.identity = {"id": 12345, "login": "fixture-owner", "type": "User"}
        self.token = {"access_token": "offline-access-fixture", "token_type": "bearer", "scope": "read:user"}
        self.auth = OfficeAuth(CONFIG, environ=ENV, http=self.http, clock=lambda: self.time[0])

    def http(self, method, url, data=None, headers=None):
        self.calls.append((method, url, data, headers))
        return dict(self.token if url == TOKEN_URL else self.identity)

    def begin(self):
        response = self.auth.begin_login()
        query = parse_qs(urlsplit(response["location"]).query)
        return query, pair(response["cookies"][0])

    def finish(self, query, cookies, **extra):
        return self.auth.finish_login(urlencode({"state": query["state"][0], "code": "offline-code", "iss": GITHUB_ISSUER, **extra}), cookies)

    def test_local_mode_remains_loopback_only_and_preserves_existing_write_headers(self):
        local = OfficeAuth()
        local.validate_bind("127.0.0.1")
        local.validate_bind("::1")
        local.check_request("127.0.0.1:8772", None, "127.0.0.1", 8772, write=True)
        self.assertTrue(local.status("")["authenticated"])
        self.assertFalse(local.status("")["required"])
        for host in ("0.0.0.0", "192.168.1.1", "example.test"):
            with self.subTest(host=host), self.assertRaises(ValueError):
                local.validate_bind(host)
        for host, origin, peer in (("attacker.test", None, "127.0.0.1"),
                                   ("127.0.0.1:8772", None, "192.168.1.2"),
                                   ("127.0.0.1:8772", "https://evil.test", "127.0.0.1")):
            with self.subTest(host=host, origin=origin, peer=peer), self.assertRaises(AuthError):
                local.check_request(host, origin, peer, 8772)
        with self.assertRaises(AuthError):
            local.begin_login()

    def test_github_configuration_fails_closed_without_https_secrets_and_one_owner_id(self):
        cases = [{}, {"mode": "disabled"}, {"mode": "local", "public_origin": "https://example.test"},
                 {**CONFIG, "allowed_github_ids": []}, {**CONFIG, "allowed_github_ids": [True]},
                 {**CONFIG, "allowed_github_ids": ["12345"]}, {**CONFIG, "allowed_github_ids": [12345, 222]},
                 {**CONFIG, "client_secret": "no-inline-credentials"}, {**CONFIG, "session_seconds": True}]
        cases += [{**CONFIG, "public_origin": value} for value in (
            "http://office.example.test", "https://office.example.test/", "https://office.example.test?",
            "https://office.example.test#", "https://office.example.test:", "https://office.example.test/path",
            "https://user@office.example.test", "https://office.example.test\\evil", "https://OFFICE.example.test")]
        for config in cases:
            with self.subTest(config=config), self.assertRaises(ValueError):
                OfficeAuth(config, environ=ENV)
        with self.assertRaises(ValueError):
            OfficeAuth(CONFIG, environ={})

    def test_canonical_host_and_write_origin_are_enforced_with_loopback_proxy(self):
        self.auth.validate_bind("127.0.0.1")
        self.auth.check_request("office.example.test", None, "127.0.0.1", 8772)
        self.auth.check_request("office.example.test", CONFIG["public_origin"], "127.0.0.1", 8772, write=True)
        for host, origin in (("localhost:8772", None), ("evil.test", CONFIG["public_origin"]),
                             ("office.example.test", "http://office.example.test"),
                             ("office.example.test", "https://evil.test"), ("office.example.test", "null")):
            with self.subTest(host=host, origin=origin), self.assertRaises(AuthError):
                self.auth.check_request(host, origin, "127.0.0.1", 8772)
        with self.assertRaises(AuthError):
            self.auth.check_request("office.example.test", None, "127.0.0.1", 8772, write=True)

    def test_login_uses_pkce_browser_binding_and_only_basic_profile_scope(self):
        query, cookies = self.begin()
        self.assertEqual(query["redirect_uri"], [CONFIG["public_origin"] + "/auth/callback"])
        self.assertEqual(query["scope"], ["read:user"])
        self.assertEqual(query["code_challenge_method"], ["S256"])
        self.assertEqual(query["allow_signup"], ["false"])
        self.assertNotIn("client_secret", query)
        response = self.finish(query, cookies)
        exchanged = parse_qs(self.calls[0][2].decode("ascii"))
        expected = base64.urlsafe_b64encode(hashlib.sha256(exchanged["code_verifier"][0].encode("ascii")).digest()).rstrip(b"=").decode("ascii")
        self.assertEqual(query["code_challenge"], [expected])
        self.assertEqual(exchanged["redirect_uri"], query["redirect_uri"])
        self.assertEqual([(call[0], call[1]) for call in self.calls], [("POST", TOKEN_URL), ("GET", USER_URL)])
        self.assertEqual(response["location"], "/")
        self.assertEqual(self.auth.current_user(pair(response["cookies"][0])), {"id": 12345, "login": "fixture-owner"})
        for cookie in response["cookies"]:
            for attribute in ("HttpOnly", "Secure", "SameSite=Lax", "Path=/"):
                self.assertIn(attribute, cookie)
            self.assertNotIn("Domain=", cookie)
        self.assertEqual(self.auth._pending, {})
        stored = json.dumps(self.auth._sessions)
        self.assertNotIn("offline-access-fixture", stored)
        self.assertNotIn("offline-secret", stored)
        self.assertNotIn(pair(response["cookies"][0]).split("=", 1)[1], stored)

    def test_other_github_identity_denied_even_with_same_username(self):
        for identity in ({"id": 54321, "login": "fixture-owner", "type": "User"},
                         {"id": "12345", "login": "fixture-owner", "type": "User"},
                         {"id": 12345, "login": "fixture-owner", "type": "Organization"}):
            self.identity = identity
            query, cookies = self.begin()
            with self.subTest(identity=identity), self.assertRaises(AuthError):
                self.finish(query, cookies)
            self.assertEqual(self.auth._sessions, {})
        self.identity = {"id": 12345, "login": "renamed-owner", "type": "User"}
        query, cookies = self.begin()
        self.assertEqual(self.finish(query, cookies)["user"]["login"], "renamed-owner")

    def test_wrong_browser_state_expiry_and_replay_never_exchange_tokens(self):
        query, cookies = self.begin()
        other_query, other_cookie = self.begin()
        with self.assertRaises(AuthError):
            self.finish(query, other_cookie)
        with self.assertRaises(AuthError):
            self.finish(query, cookies)
        self.assertEqual(self.calls, [])
        self.time[0] += 601
        with self.assertRaises(AuthError):
            self.finish(other_query, other_cookie)
        self.assertEqual(self.calls, [])
        query, cookies = self.begin()
        self.finish(query, cookies)
        calls = len(self.calls)
        with self.assertRaises(AuthError):
            self.finish(query, cookies)
        self.assertEqual(len(self.calls), calls)

    def test_callback_denial_duplicates_and_redirect_injection_are_rejected(self):
        for extra in ("&state=duplicate", "&code=second", "&next=https://evil.test", "&error=access_denied"):
            query, cookies = self.begin()
            raw = urlencode({"state": query["state"][0], "code": "offline-code", "iss": GITHUB_ISSUER}) + extra
            with self.subTest(extra=extra), self.assertRaises(AuthError):
                self.auth.finish_login(raw, cookies)
        query, cookies = self.begin()
        with self.assertRaises(AuthError):
            self.auth.finish_login(urlencode({"state": query["state"][0], "error": "access_denied", "iss": GITHUB_ISSUER}), cookies)
        self.assertEqual(self.calls, [])

    def test_issuer_is_required_exact_and_unique_before_state_consumption(self):
        query, cookie = self.begin()
        base = urlencode({"state": query["state"][0], "code": "offline-code"})
        variants = [("", "issuer_missing")]
        variants += [("&" + urlencode({"iss": value}), "issuer_mismatch") for value in (
            "", "https://github.com", "https://github.com/login/oauth/", "https://GITHUB.com/login/oauth",
            "http://github.com/login/oauth", "https://github.com/login/oauth?", "https://github.com/login/oauth#",
            "https://github.com/login/oauth.evil", "https://github.com.evil/login/oauth", " https://github.com/login/oauth")]
        variants += [("&" + urlencode([("iss", GITHUB_ISSUER), ("iss", GITHUB_ISSUER)]), "issuer_mismatch")]
        for extra, expected in variants:
            with self.subTest(expected=expected), self.assertRaises(AuthError) as failure:
                self.auth.finish_login(base + extra, cookie)
            self.assertEqual(auth_failure_reason(failure.exception), expected)
            self.assertEqual(self.calls, [])
            self.assertEqual(self.auth._sessions, {})
        # Invalid issuer responses must not consume a valid browser challenge.
        self.assertEqual(self.finish(query, cookie)["user"]["id"], 12345)

    def test_callback_is_consumed_before_inflight_exchange_completes(self):
        query, cookies = self.begin()
        entered, release = threading.Event(), threading.Event()
        result = []
        def delayed(method, url, **kwargs):
            if url == TOKEN_URL:
                entered.set()
                if not release.wait(3):
                    raise RuntimeError("offline fixture timeout")
            return self.http(method, url, **kwargs)
        self.auth._http = delayed
        thread = threading.Thread(target=lambda: result.append(self.finish(query, cookies)))
        thread.start()
        try:
            self.assertTrue(entered.wait(2))
            with self.assertRaises(AuthError):
                self.finish(query, cookies)
        finally:
            release.set()
            thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(result), 1)
        self.assertEqual(len(self.calls), 2)

    def test_logout_revokes_inflight_callback_and_its_late_session(self):
        query, cookies = self.begin()
        entered, release = threading.Event(), threading.Event()
        results, errors = [], []
        def delayed(method, url, **kwargs):
            if url == TOKEN_URL:
                entered.set()
                if not release.wait(3):
                    raise RuntimeError("offline fixture timeout")
            return self.http(method, url, **kwargs)
        def callback():
            try:
                results.append(self.finish(query, cookies))
            except AuthError as error:
                errors.append(error)
        self.auth._http = delayed
        thread = threading.Thread(target=callback)
        thread.start()
        try:
            self.assertTrue(entered.wait(2))
            self.assertEqual(len(self.auth._inflight), 1)
            self.auth.logout(cookies)
        finally:
            release.set()
            thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(results, [])
        self.assertEqual(len(errors), 1)
        self.assertEqual(self.auth._sessions, {})
        self.assertEqual(self.auth._inflight, {})
        # A logout sent just after issuance can have only the old binding cookie:
        # the callback's new session response has not reached that browser yet.
        self.auth._http = self.http
        query, cookies = self.begin()
        late_response = self.finish(query, cookies)
        session = pair(late_response["cookies"][0])
        self.assertIsNotNone(self.auth.current_user(session))
        self.auth.logout(cookies)
        self.assertIsNone(self.auth.current_user(session))

    def test_expiry_while_github_is_answering_cannot_issue_session(self):
        query, cookies = self.begin()
        def expired(method, url, **kwargs):
            if url == USER_URL:
                self.time[0] += 601
            return self.http(method, url, **kwargs)
        self.auth._http = expired
        with self.assertRaises(AuthError):
            self.finish(query, cookies)
        self.assertEqual(self.auth._inflight, {})
        self.assertEqual(self.auth._sessions, {})

    def test_browser_binding_survives_login_and_cancels_other_tab_challenge(self):
        first, binding = self.begin()
        second_response = self.auth.begin_login(binding)
        second = parse_qs(urlsplit(second_response["location"]).query)
        self.assertEqual(pair(second_response["cookies"][0]), binding)
        response = self.finish(first, binding)
        self.assertEqual(pair(response["cookies"][1]), binding)
        # The session still identifies the browser if its binding cookie is absent.
        self.auth.logout(pair(response["cookies"][0]))
        with self.assertRaises(AuthError):
            self.finish(second, binding)
        self.assertEqual(self.auth._sessions, {})

    def test_provider_errors_and_broader_scopes_do_not_create_sessions(self):
        for update in ({"error": "bad_verification_code"}, {"scope": "read:user,repo"},
                       {"access_token": "bad\r\nheader"}, {"token_type": "unsupported"}):
            self.token = {"access_token": "offline-access-fixture", "token_type": "bearer", "scope": "read:user", **update}
            query, cookies = self.begin()
            with self.subTest(update=update), self.assertRaises(AuthError):
                self.finish(query, cookies)
            self.assertEqual(self.auth._sessions, {})
        query, cookies = self.begin()
        self.auth._http = Mock(side_effect=RuntimeError("offline-sensitive-provider-body"))
        with self.assertRaises(AuthError) as failure:
            self.finish(query, cookies)
        self.assertNotIn("offline-sensitive-provider-body", str(failure.exception))

    def test_callback_reasons_distinguish_browser_binding_and_lost_state(self):
        for cookie_kind, expected in (("missing", "binding_missing"), ("malformed", "binding_invalid"),
                                      ("other_browser", "binding_mismatch"), ("expired", "state_unavailable")):
            query, cookie = self.begin()
            if cookie_kind == "missing":
                cookie = ""
            elif cookie_kind == "malformed":
                cookie = STATE_COOKIE + "=offline-malformed-value"
            elif cookie_kind == "other_browser":
                _, cookie = self.begin()
            else:
                self.time[0] += 601
            with self.subTest(kind=cookie_kind), self.assertRaises(AuthError) as failure:
                self.finish(query, cookie)
            self.assertEqual(auth_failure_reason(failure.exception), expected)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.auth._sessions, {})
        query, cookie = self.begin()
        self.auth = OfficeAuth(CONFIG, environ=ENV, http=self.http, clock=lambda: self.time[0])
        with self.assertRaises(AuthError) as failure:
            self.finish(query, cookie)
        self.assertEqual(auth_failure_reason(failure.exception), "state_unavailable")

    def test_provider_authorization_and_callback_shape_have_safe_fixed_reasons(self):
        secret = "offline-sensitive-code-cookie-state-provider-text"
        for extra, expected in (({"error": secret}, "provider_authorization_failed"),
                                ({"next": secret}, "callback_invalid")):
            query, cookie = self.begin()
            with self.subTest(expected=expected), self.assertRaises(AuthError) as failure:
                self.finish(query, cookie, **extra)
            self.assertEqual(auth_failure_reason(failure.exception), expected)
            self.assertNotIn(secret, str(failure.exception))
        self.assertEqual(self.calls, [])
        self.assertEqual(self.auth._sessions, {})

    def test_token_and_user_reasons_do_not_expose_provider_data_or_relax_validation(self):
        secret = "offline-sensitive-provider-text"
        valid = {"access_token": "offline-access-fixture", "token_type": "bearer", "scope": "read:user"}
        cases = [
            ([RuntimeError(secret)], "token_exchange_failed"),
            ([{"error": "bad_verification_code", "error_description": secret}], "token_bad_verification_code"),
            ([{"error": "incorrect_client_credentials", "error_description": secret}], "token_incorrect_client_credentials"),
            ([{"error": "redirect_uri_mismatch", "error_description": secret}], "token_redirect_uri_mismatch"),
            ([{"error": secret}], "token_rejected"),
            ([[]], "token_response_invalid"),
            ([{**valid, "token_type": secret}], "token_response_invalid"),
            ([{**valid, "access_token": "bad\r\n" + secret}], "token_response_invalid"),
            ([{**valid, "scope": "read:user,repo"}], "token_scope_rejected"),
            ([{**valid, "scope": ["read:user"]}], "token_scope_rejected"),
            ([valid, RuntimeError(secret)], "user_lookup_failed"),
            ([valid, []], "user_response_invalid"),
            ([valid, {"id": 54321, "login": secret, "type": "User"}], "owner_mismatch"),
            ([valid, {"id": 12345, "login": "bad\r\n" + secret, "type": "User"}], "user_response_invalid"),
        ]
        for responses, expected in cases:
            self.auth._http = Mock(side_effect=responses)
            query, cookie = self.begin()
            with self.subTest(expected=expected), self.assertRaises(AuthError) as failure:
                self.finish(query, cookie)
            self.assertEqual(auth_failure_reason(failure.exception), expected)
            self.assertNotIn(secret, str(failure.exception))
            self.assertEqual(self.auth._sessions, {})
            self.assertEqual(self.auth._inflight, {})

    def test_reason_projection_never_accepts_arbitrary_exception_attributes(self):
        for reason in ("offline-sensitive-text", "owner_mismatch&secret=offline", None, []):
            error = AuthError("offline-sensitive-message", reason=reason)
            self.assertEqual(auth_failure_reason(error), "auth_error")
            error.reason = reason
            self.assertEqual(auth_failure_reason(error), "auth_error")
        generic = PermissionError("offline-sensitive-message")
        generic.reason = "owner_mismatch"
        self.assertEqual(auth_failure_reason(generic), "auth_error")
        for reason in AUTH_FAILURE_REASONS:
            self.assertRegex(reason, r"^[a-z_]+$")

    def test_sessions_expire_logout_and_restart_revoke_and_login_rotates(self):
        query, cookies = self.begin()
        response = self.finish(query, cookies)
        session = pair(response["cookies"][0])
        self.assertTrue(self.auth.status(session)["authenticated"])
        self.assertFalse(OfficeAuth(CONFIG, environ=ENV).status(session)["authenticated"])
        query, binding = self.begin()
        rotated = pair(self.finish(query, binding + "; " + session)["cookies"][0])
        self.assertIsNone(self.auth.current_user(session))
        self.assertIsNotNone(self.auth.current_user(rotated))
        cleared = self.auth.logout(rotated)
        self.assertIsNone(self.auth.current_user(rotated))
        self.assertTrue(all("Max-Age=0" in cookie for cookie in cleared["cookies"]))
        query, binding = self.begin()
        session = pair(self.finish(query, binding)["cookies"][0])
        self.time[0] += 28_800
        self.assertIsNone(self.auth.current_user(session))

    def test_missing_malformed_or_duplicate_session_cookies_are_denied(self):
        query, cookies = self.begin()
        session = pair(self.finish(query, cookies)["cookies"][0])
        spaced = session.replace("=", " =", 1)
        for value in (None, "", session + "; " + session, session + "; " + spaced, spaced + "; " + session,
                      session + ", " + session, "other=x, " + session, session + "; other=x, " + session,
                      SESSION_COOKIE + "=guess", session + "\r\n", "x" * 9000):
            with self.subTest(case=type(value).__name__):
                self.assertIsNone(self.auth.current_user(value))
        self.assertIsNotNone(self.auth.current_user(session))

    def test_pending_challenges_are_bounded_and_expire(self):
        first, first_cookie = self.begin()
        for _ in range(127):
            self.auth.begin_login()
        latest, latest_cookie = self.begin()
        self.assertEqual(len(self.auth._pending), 128)
        with self.assertRaises(AuthError):
            self.finish(first, first_cookie)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.finish(latest, latest_cookie)["user"]["id"], 12345)
        self.time[0] += 601
        self.auth.begin_login()
        self.assertEqual(len(self.auth._pending), 1)

    def test_default_transport_never_follows_redirect_or_returns_provider_error_body(self):
        self.assertIsNone(_NoRedirect().redirect_request(None, None, 302, "redirect", {}, "https://evil.test"))
        with patch("office.auth.build_opener") as opener:
            opener.return_value.open.side_effect = HTTPError(TOKEN_URL, 302, "offline-sensitive-body", {}, None)
            with self.assertRaises(AuthError) as failure:
                _github_json("POST", TOKEN_URL, b"offline-data")
            self.assertNotIn("offline-sensitive-body", str(failure.exception))
            self.assertEqual(opener.return_value.open.call_args.kwargs["timeout"], 10)
        with self.assertRaises(AuthError):
            _github_json("GET", "https://evil.test")


if __name__ == "__main__":
    unittest.main()
