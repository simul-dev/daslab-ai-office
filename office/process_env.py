"""Keep server login/tunnel credentials out of child process environments.

Call clear_server_secrets once after OfficeAuth reads its configuration and
before starting any engines. This also protects library-managed driver
processes which do not expose an environment argument. Values are never logged
or returned; the parent launcher and the OfficeAuth object's values are intact.
"""
import os


_server_names = frozenset({"DAS_OFFICE_GITHUB_CLIENT_ID", "DAS_OFFICE_GITHUB_CLIENT_SECRET", "TUNNEL_TOKEN"})


def _server_only(name):
    upper = name.upper()
    return upper in _server_names or upper.startswith("DAS_OFFICE_GITHUB_")


def child_env():
    """Preserve ordinary runtime/transport settings, excluding server secrets."""
    return {name: value for name, value in os.environ.items() if not _server_only(name)}


def clear_server_secrets(auth_config=None):
    """Remove configured OAuth names and tunnel credentials from this process."""
    global _server_names
    config = auth_config or {}
    names = [config.get("client_id_env"), config.get("client_secret_env")]
    _server_names = _server_names | frozenset(name.upper() for name in names if isinstance(name, str))
    for name in list(os.environ):
        if _server_only(name):
            del os.environ[name]


def require_clean_process_env():
    """Fail before a library spawns its driver if startup cleanup was omitted."""
    if any(_server_only(name) for name in os.environ):
        raise RuntimeError("서버 인증 환경 정제가 필요합니다. 브라우저 검증을 시작하지 않았습니다.")
