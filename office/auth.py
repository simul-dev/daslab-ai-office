"""Single-owner GitHub or locally paired device sign-in.

The HTTP adapter must apply check_request before every route, require a current
user for private resources, and keep its JSON/custom-header checks for writes.
GitHub sessions and OAuth challenges exist only in this server process. Paired
device sessions persist as token hashes, never plaintext bearer credentials.
"""
import base64
import hashlib
import ipaddress
import json
import os
import re
import secrets
import sqlite3
import threading
import time
from collections import deque
from contextlib import closing
from http.cookies import CookieError, SimpleCookie
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
TOKEN_URL = "https://github.com/login/oauth/access_token"
USER_URL = "https://api.github.com/user"
GITHUB_ISSUER = "https://github.com/login/oauth"
SESSION_COOKIE = "__Host-das-office-session"
STATE_COOKIE = "__Host-das-office-oauth"
TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{43}\Z")
AUTH_FAILURE_REASONS = frozenset({
    "auth_error", "callback_invalid", "binding_missing", "binding_invalid", "binding_mismatch",
    "state_unavailable", "provider_authorization_failed", "callback_busy", "session_cancelled",
    "token_exchange_failed", "token_response_invalid", "token_scope_rejected", "token_rejected",
    "token_bad_verification_code", "token_incorrect_client_credentials", "token_redirect_uri_mismatch",
    "user_lookup_failed", "user_response_invalid", "owner_mismatch", "issuer_missing", "issuer_mismatch",
    "pair_rate_limited",
})


class AuthError(PermissionError):
    """A safe, user-readable failure without provider bodies or credentials."""

    def __init__(self, message, *, reason="auth_error"):
        super().__init__(message)
        self.reason = reason if isinstance(reason, str) and reason in AUTH_FAILURE_REASONS else "auth_error"


def auth_failure_reason(error):
    """Return only a fixed diagnostic label, never exception/provider text."""
    reason = getattr(error, "reason", None) if isinstance(error, AuthError) else None
    return reason if isinstance(reason, str) and reason in AUTH_FAILURE_REASONS else "auth_error"


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _github_json(method, url, data=None, headers=None):
    # Call sites select these endpoints, never a callback/request parameter.
    if (method, url) not in (("POST", TOKEN_URL), ("GET", USER_URL)):
        raise AuthError("등록된 GitHub 로그인 경로만 사용할 수 있습니다.")
    request = Request(url, data=data, method=method,
                      headers={"Accept": "application/json", "User-Agent": "DAS-Lab-Office-Login", **(headers or {})})
    try:
        with build_opener(_NoRedirect()).open(request, timeout=10) as response:
            if response.status != 200 or response.geturl() != url:
                raise ValueError("unexpected response")
            raw = response.read(65_537)
            if len(raw) > 65_536:
                raise ValueError("response too large")
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise ValueError("invalid response")
            return result
    except (HTTPError, URLError, OSError, ValueError, UnicodeError):
        raise AuthError("GitHub 로그인 응답을 확인하지 못했습니다. 다시 시도해 주세요.") from None


def _digest(value):
    return hashlib.sha256(value.encode("ascii")).hexdigest()


def _loopback(value):
    if value == "localhost":
        return True
    try:
        return ipaddress.ip_address(value).is_loopback
    except (ValueError, TypeError):
        return False


def _cookie_value(header, name):
    if (not isinstance(header, str) or len(header) > 8192 or "," in header
            or any(ord(c) < 32 or ord(c) == 127 for c in header)):
        return None
    # Reject duplicate cookie names instead of allowing an order-dependent choice.
    if sum(part.strip().split("=", 1)[0].strip() == name for part in header.split(";")) != 1:
        return None
    try:
        parsed = SimpleCookie()
        parsed.load(header)
        value = parsed[name].value if name in parsed else ""
    except (CookieError, ValueError):
        return None
    return value if TOKEN_PATTERN.fullmatch(value) else None


def _cookie(name, value="", max_age=0):
    return f"{name}={value}; Path=/; Max-Age={max_age}; HttpOnly; Secure; SameSite=Lax"


class OfficeAuth:
    """Explicit local mode, GitHub OAuth, or trusted-PC device pairing."""

    def __init__(self, config=None, *, environ=None, http=None, clock=None):
        config = {"mode": "local"} if config is None else config
        allowed = {"mode", "public_origin", "allowed_github_ids", "client_id_env", "client_secret_env",
                   "session_seconds", "session_db", "pairing_seconds"}
        if not isinstance(config, dict) or set(config) - allowed or config.get("mode") not in ("local", "github", "pairing"):
            raise ValueError("인증 설정은 local, github 또는 pairing 모드를 명시해야 합니다.")
        self.mode = config["mode"]
        self.public_origin = None
        self._http = http or _github_json
        self._clock = clock or (time.time if self.mode == "pairing" else time.monotonic)
        self._lock = threading.RLock()
        self._pending, self._inflight, self._sessions = {}, {}, {}
        self.pairing_auth = None  # Set only on the trusted local listener.
        self._state_seconds = 600
        self._session_seconds = config.get("session_seconds", 2_592_000 if self.mode == "pairing" else 28_800)
        if (type(self._session_seconds) is not int
                or not 300 <= self._session_seconds <= (2_592_000 if self.mode == "pairing" else 43_200)):
            raise ValueError("로그인 세션 기간이 올바르지 않습니다.")
        if self.mode == "local":
            if set(config) != {"mode"}:
                raise ValueError("로컬 모드에는 GitHub 인증 설정을 함께 둘 수 없습니다.")
            return
        origin = config.get("public_origin")
        if not isinstance(origin, str) or len(origin) > 2048 or not origin.isascii() or any(c.isspace() for c in origin):
            raise ValueError("공개 로그인에는 정확한 HTTPS 주소가 필요합니다.")
        try:
            parsed = urlsplit(origin)
            port = parsed.port
            if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                    or parsed.path or parsed.query or parsed.fragment or "\\" in origin
                    or origin != "https://" + parsed.netloc or parsed.netloc.endswith(":")
                    or parsed.netloc.lower() != parsed.netloc or (port is not None and not 1 <= port <= 65535)
                    or not re.fullmatch(r"[a-z0-9.\-\[\]:]+", parsed.netloc)):
                raise ValueError()
        except ValueError:
            raise ValueError("공개 로그인에는 경로 없는 HTTPS 주소가 필요합니다.") from None
        self.public_origin, self._host = origin, parsed.netloc
        if self.mode == "pairing":
            if set(config) - {"mode", "public_origin", "session_seconds", "session_db", "pairing_seconds"}:
                raise ValueError("기기 연결 모드에는 OAuth 설정을 함께 둘 수 없습니다.")
            self._pairing_seconds = config.get("pairing_seconds", 600)
            if type(self._pairing_seconds) is not int or not 300 <= self._pairing_seconds <= 600:
                raise ValueError("기기 연결 코드는 5~10분으로 설정해야 합니다.")
            database = config.get("session_db")
            if not isinstance(database, str) or not Path(database).is_absolute():
                raise ValueError("기기 연결 세션 저장소는 절대 경로로 지정해야 합니다.")
            self._db_path = Path(database)
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            self._pair_pending = {}
            self._pair_failures = deque()
            self._init_pair_store()
            return
        ids = config.get("allowed_github_ids")
        if not isinstance(ids, list) or len(ids) != 1 or type(ids[0]) is not int or not 0 < ids[0] < 2 ** 63:
            raise ValueError("허용할 대표의 GitHub 숫자 ID 한 개를 지정해야 합니다.")
        self._owner_id = ids[0]
        self.callback_url = origin + "/auth/callback"
        env = os.environ if environ is None else environ
        credentials = []
        for field, default in (("client_id_env", "DAS_OFFICE_GITHUB_CLIENT_ID"),
                               ("client_secret_env", "DAS_OFFICE_GITHUB_CLIENT_SECRET")):
            name = config.get(field, default)
            if not isinstance(name, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{2,79}", name):
                raise ValueError("GitHub 로그인 환경변수 이름이 올바르지 않습니다.")
            value = env.get(name)
            if not isinstance(value, str) or not 1 <= len(value) <= 512 or not re.fullmatch(r"[A-Za-z0-9_.-]+", value):
                raise ValueError("GitHub 로그인 Client ID와 Secret을 환경변수에 설정해야 합니다.")
            credentials.append(value)
        self._client_id, self._client_secret = credentials

    def validate_bind(self, host):
        if self.mode == "local" and not _loopback(host):
            raise ValueError("로그인 없는 로컬 모드는 loopback 주소에서만 열 수 있습니다.")

    def check_request(self, host, origin, peer, port, *, write=False):
        if self.mode == "local":
            allowed = {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"}
            if not _loopback(peer) or host not in allowed:
                raise AuthError("로컬 주소로 접속해 주세요.")
            expected = "http://" + host
        else:
            if host != self._host:
                raise AuthError("설정된 공개 주소로 접속해 주세요.")
            expected = self.public_origin
        if (origin is not None and origin != expected) or (write and self.mode in ("github", "pairing") and origin != expected):
            raise AuthError("같은 웹 화면에서 보낸 요청만 사용할 수 있습니다.")

    def _pair_connection(self):
        return sqlite3.connect(str(self._db_path), timeout=5)

    def _init_pair_store(self):
        with closing(self._pair_connection()) as connection, connection:
            connection.execute("CREATE TABLE IF NOT EXISTS owner_sessions ("
                               "token_hash TEXT PRIMARY KEY, expires_at INTEGER NOT NULL, created_at INTEGER NOT NULL)")
            connection.execute("CREATE INDEX IF NOT EXISTS owner_sessions_expiry ON owner_sessions(expires_at)")

    def start_pairing(self):
        """Only the trusted local HTTP listener may call this method."""
        if self.mode != "pairing":
            raise AuthError("기기 연결을 사용할 수 없습니다.")
        token = secrets.token_urlsafe(32)
        expires_at = int(self._clock()) + self._pairing_seconds
        with self._lock:
            self._pair_pending = {_digest(token): expires_at}
        return {"url": self.public_origin + "/login#pair=" + token,
                "expires_at": expires_at, "expires_in": self._pairing_seconds}

    def pair_login(self, token, cookie_header=""):
        if self.mode != "pairing":
            raise AuthError("기기 연결을 사용할 수 없습니다.")
        if not isinstance(token, str) or not TOKEN_PATTERN.fullmatch(token):
            raise AuthError("연결 코드가 올바르지 않거나 만료됐습니다. PC에서 다시 연결해 주세요.")
        now = int(self._clock())
        digest = _digest(token)
        with self._lock:
            while self._pair_failures and self._pair_failures[0] <= now - 60:
                self._pair_failures.popleft()
            expires = self._pair_pending.pop(digest, None)
            if expires is None or expires <= now:
                # A public attacker cannot turn random guesses into a denial
                # of service for the actual 256-bit token displayed on the PC.
                if len(self._pair_failures) >= 20:
                    raise AuthError("요청이 많습니다. 잠시 뒤 다시 시도해 주세요.", reason="pair_rate_limited")
                self._pair_failures.append(now)
                raise AuthError("연결 코드가 올바르지 않거나 만료됐습니다. PC에서 다시 연결해 주세요.")
            session = secrets.token_urlsafe(32)
            old = _cookie_value(cookie_header, SESSION_COOKIE)
            with closing(self._pair_connection()) as connection, connection:
                connection.execute("DELETE FROM owner_sessions WHERE expires_at <= ?", (now,))
                if old:
                    connection.execute("DELETE FROM owner_sessions WHERE token_hash = ?", (_digest(old),))
                connection.execute("INSERT INTO owner_sessions(token_hash, expires_at, created_at) VALUES(?,?,?)",
                                   (_digest(session), now + self._session_seconds, now))
                connection.execute("DELETE FROM owner_sessions WHERE token_hash NOT IN ("
                                   "SELECT token_hash FROM owner_sessions ORDER BY created_at DESC, rowid DESC LIMIT 4)")
        user = {"id": "owner", "login": "owner"}
        return {"authenticated": True, "user": user,
                "cookies": [_cookie(SESSION_COOKIE, session, self._session_seconds)]}

    def revoke_paired_sessions(self):
        """Trusted-PC recovery for a lost phone; never exposed on public auth."""
        if self.mode != "pairing":
            raise AuthError("기기 연결을 사용할 수 없습니다.")
        with self._lock:
            self._pair_pending.clear()
            with closing(self._pair_connection()) as connection, connection:
                removed = connection.execute("DELETE FROM owner_sessions").rowcount
        return {"revoked_count": removed}

    def cancel_pairing(self):
        """Invalidate any unredeemed QR without logging out paired devices."""
        if self.mode != "pairing":
            raise AuthError("기기 연결을 사용할 수 없습니다.")
        with self._lock:
            cancelled = len(self._pair_pending)
            self._pair_pending.clear()
        return {"cancelled": bool(cancelled)}

    def _prune(self):
        stamp = self._clock()
        for table in (self._pending, self._inflight, self._sessions):
            for key in list(table):
                if table[key]["expires"] <= stamp:
                    del table[key]

    def begin_login(self, cookie_header=""):
        if self.mode != "github":
            raise AuthError("현재 서버는 로컬 전용 모드입니다.")
        state, verifier = (secrets.token_urlsafe(32) for _ in range(2))
        # A second login tab belongs to the same browser. Keeping its binding
        # lets a later logout cancel both callbacks, including late responses.
        binding = _cookie_value(cookie_header, STATE_COOKIE) or secrets.token_urlsafe(32)
        with self._lock:
            self._prune()
            while len(self._pending) >= 128:
                # Bounded memory without making every new owner wait ten minutes
                # after abandoned login starts fill the pool. Proxy rate limiting
                # is still necessary to control sustained anonymous traffic.
                del self._pending[next(iter(self._pending))]
            self._pending[_digest(state)] = {"binding": _digest(binding), "verifier": verifier,
                                             "expires": self._clock() + self._state_seconds}
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
        query = {"client_id": self._client_id, "redirect_uri": self.callback_url, "scope": "read:user",
                 "state": state, "code_challenge": challenge, "code_challenge_method": "S256", "allow_signup": "false"}
        return {"location": AUTHORIZE_URL + "?" + urlencode(query),
                "cookies": [_cookie(STATE_COOKIE, binding, self._state_seconds)]}

    def finish_login(self, raw_query, cookie_header):
        if self.mode != "github":
            raise AuthError("현재 서버는 로컬 전용 모드입니다.")
        if not isinstance(raw_query, str) or len(raw_query) > 4096:
            raise AuthError("로그인 응답이 올바르지 않습니다.", reason="callback_invalid")
        try:
            query = parse_qs(raw_query, keep_blank_values=True, strict_parsing=True, max_num_fields=8)
        except ValueError:
            raise AuthError("로그인 응답이 올바르지 않습니다.", reason="callback_invalid") from None
        # GitHub advertises RFC 9207 support. Validate the decoded issuer before
        # consuming state or sending the authorization code to any endpoint.
        issuer = query.get("iss", [])
        if not issuer:
            raise AuthError("로그인 응답의 발급자를 확인할 수 없습니다.", reason="issuer_missing")
        if len(issuer) != 1 or issuer[0] != GITHUB_ISSUER:
            raise AuthError("로그인 응답의 발급자를 확인할 수 없습니다.", reason="issuer_mismatch")
        state = query.get("state", [])
        binding = _cookie_value(cookie_header, STATE_COOKIE)
        if len(state) != 1 or not TOKEN_PATTERN.fullmatch(state[0]):
            raise AuthError("로그인 요청을 확인할 수 없습니다. 다시 로그인해 주세요.", reason="callback_invalid")
        if binding is None:
            reason = "binding_missing" if isinstance(cookie_header, str) and STATE_COOKIE not in cookie_header else "binding_invalid"
            raise AuthError("로그인 요청을 확인할 수 없습니다. 다시 로그인해 주세요.", reason=reason)
        with self._lock:
            self._prune()
            state_key = _digest(state[0])
            challenge = self._pending.pop(state_key, None)
            if not challenge:
                raise AuthError("로그인 요청이 만료되었거나 이미 사용됐습니다. 다시 로그인해 주세요.", reason="state_unavailable")
            if not secrets.compare_digest(challenge["binding"], _digest(binding)):
                raise AuthError("로그인 요청이 만료되었거나 이미 사용됐습니다. 다시 로그인해 주세요.", reason="binding_mismatch")
            code = query.get("code", [])
            if "error" in query:
                raise AuthError("GitHub 로그인이 승인되지 않았습니다. 다시 로그인해 주세요.", reason="provider_authorization_failed")
            if set(query) != {"code", "state", "iss"} or len(code) != 1 or not re.fullmatch(r"[A-Za-z0-9_.-]{1,512}", code[0]):
                raise AuthError("GitHub 로그인이 승인되지 않았습니다. 다시 로그인해 주세요.", reason="callback_invalid")
            if len(self._inflight) >= 16:
                raise AuthError("로그인 확인 요청이 많습니다. 잠시 뒤 다시 시도해 주세요.", reason="callback_busy")
            self._inflight[state_key] = challenge
        payload = urlencode({"client_id": self._client_id, "client_secret": self._client_secret,
                             "code": code[0], "redirect_uri": self.callback_url,
                             "code_verifier": challenge["verifier"]}).encode("ascii")
        try:
            try:
                token = self._http("POST", TOKEN_URL, data=payload,
                                   headers={"Content-Type": "application/x-www-form-urlencoded"})
            except Exception:
                raise AuthError("GitHub 로그인 응답을 확인하지 못했습니다. 다시 시도해 주세요.", reason="token_exchange_failed") from None
            if not isinstance(token, dict):
                raise AuthError("GitHub 로그인 응답을 확인하지 못했습니다. 다시 시도해 주세요.", reason="token_response_invalid")
            if token.get("error"):
                provider_error = token["error"]
                reason = ("token_" + provider_error if isinstance(provider_error, str) and provider_error in
                          ("bad_verification_code", "incorrect_client_credentials", "redirect_uri_mismatch") else "token_rejected")
                raise AuthError("GitHub 로그인 응답을 확인하지 못했습니다. 다시 시도해 주세요.", reason=reason)
            access = token.get("access_token")
            scopes = token.get("scope", "")
            if (str(token.get("token_type", "")).lower() != "bearer"
                    or not isinstance(access, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,1024}", access)):
                raise AuthError("GitHub 로그인 응답을 확인하지 못했습니다. 다시 시도해 주세요.", reason="token_response_invalid")
            if not isinstance(scopes, str) or set(filter(None, re.split(r"[ ,]+", scopes))) - {"read:user"}:
                raise AuthError("GitHub 로그인 응답을 확인하지 못했습니다. 다시 시도해 주세요.", reason="token_scope_rejected")
            try:
                user = self._http("GET", USER_URL, headers={"Authorization": "Bearer " + access})
            except Exception:
                raise AuthError("GitHub 로그인 응답을 확인하지 못했습니다. 다시 시도해 주세요.", reason="user_lookup_failed") from None
            if not isinstance(user, dict):
                raise AuthError("GitHub 로그인 응답을 확인하지 못했습니다. 다시 시도해 주세요.", reason="user_response_invalid")
            identity, login = user.get("id"), user.get("login")
            if type(identity) is not int or identity != self._owner_id or user.get("type") != "User":
                raise AuthError("허용된 대표 GitHub 계정만 이 사무실에 접속할 수 있습니다.", reason="owner_mismatch")
            if not isinstance(login, str) or not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})", login):
                raise AuthError("GitHub 로그인 응답을 확인하지 못했습니다. 다시 시도해 주세요.", reason="user_response_invalid")
            session = secrets.token_urlsafe(32)
            with self._lock:
                self._prune()
                if self._inflight.pop(state_key, None) is not challenge:
                    raise AuthError("로그인이 취소되었거나 만료됐습니다. 다시 로그인해 주세요.", reason="session_cancelled")
                old = _cookie_value(cookie_header, SESSION_COOKIE)
                if old:
                    self._sessions.pop(_digest(old), None)
                while len(self._sessions) >= 64:
                    del self._sessions[next(iter(self._sessions))]
                self._sessions[_digest(session)] = {"user": {"id": identity, "login": login},
                                                   "binding": challenge["binding"],
                                                   "expires": self._clock() + self._session_seconds}
        except AuthError:
            raise
        except Exception:
            raise AuthError("GitHub 로그인 응답을 확인하지 못했습니다. 다시 시도해 주세요.") from None
        finally:
            with self._lock:
                self._inflight.pop(state_key, None)
        return {"location": "/", "user": {"id": identity, "login": login},
                "cookies": [_cookie(SESSION_COOKIE, session, self._session_seconds),
                            _cookie(STATE_COOKIE, binding, self._session_seconds)]}

    def current_user(self, cookie_header):
        if self.mode == "local":
            return {"id": None, "login": "local", "mode": "local"}
        session = _cookie_value(cookie_header, SESSION_COOKIE)
        if session is None:
            return None
        if self.mode == "pairing":
            now = int(self._clock())
            with self._lock, closing(self._pair_connection()) as connection:
                stored = connection.execute("SELECT 1 FROM owner_sessions WHERE token_hash = ? AND expires_at > ?",
                                            (_digest(session), now)).fetchone()
            return {"id": "owner", "login": "owner"} if stored else None
        with self._lock:
            self._prune()
            stored = self._sessions.get(_digest(session))
            return dict(stored["user"]) if stored else None

    def logout(self, cookie_header):
        if self.mode == "pairing":
            session = _cookie_value(cookie_header, SESSION_COOKIE)
            if session:
                with self._lock, closing(self._pair_connection()) as connection, connection:
                    connection.execute("DELETE FROM owner_sessions WHERE token_hash = ?", (_digest(session),))
            return {"cookies": [_cookie(SESSION_COOKIE)]}
        with self._lock:
            bindings = set()
            session = _cookie_value(cookie_header, SESSION_COOKIE)
            if session:
                stored = self._sessions.pop(_digest(session), None)
                if stored:
                    bindings.add(stored["binding"])
            binding = _cookie_value(cookie_header, STATE_COOKIE)
            if binding:
                bindings.add(_digest(binding))
            for digest in bindings:
                # Keep revocation effective while a token exchange is running,
                # and after issuance if its response has not reached the browser.
                for table in (self._pending, self._inflight, self._sessions):
                    for key in list(table):
                        if secrets.compare_digest(table[key]["binding"], digest):
                            del table[key]
        return {"cookies": [_cookie(SESSION_COOKIE), _cookie(STATE_COOKIE)]}

    def status(self, cookie_header):
        user = self.current_user(cookie_header)
        result = {"mode": self.mode, "required": self.mode in ("github", "pairing"),
                  "authenticated": user is not None, "user": user,
                  "login_url": "/auth/login" if self.mode == "github" else "/login" if self.mode == "pairing" else None}
        if self.mode == "local":
            result["pairing_available"] = self.pairing_auth is not None
            if self.pairing_auth is not None:
                result["public_origin"] = self.pairing_auth.public_origin
        return result
