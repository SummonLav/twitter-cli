from __future__ import annotations

import inspect
import json
import os
import stat
import sys

import pytest

from twitter_cli import auth
from twitter_cli.exceptions import AuthenticationError

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX ownership/mode checks")

TOKEN = "a" * 40
CSRF = "b" * 160


def _write_private(path, payload, mode=0o600) -> str:
    path.write_text(json.dumps(payload), encoding="utf-8")
    os.chmod(path, mode)
    return str(path)


def _no_network_verify(monkeypatch, seen=None):
    def _verify(auth_token, ct0, cookie_string=None):
        if seen is not None:
            seen.append((auth_token, ct0, cookie_string))
        return {}

    monkeypatch.setattr(auth, "verify_cookies", _verify)


def test_get_cookies_prefers_env(monkeypatch) -> None:
    monkeypatch.delenv(auth.CREDENTIALS_FILE_ENV, raising=False)
    monkeypatch.setenv("TWITTER_AUTH_TOKEN", TOKEN)
    monkeypatch.setenv("TWITTER_CT0", CSRF)
    monkeypatch.delenv("TWITTER_COOKIE_STRING", raising=False)
    seen = []
    _no_network_verify(monkeypatch, seen)

    cookies = auth.get_cookies()

    assert cookies == {"auth_token": TOKEN, "ct0": CSRF}
    assert seen == [(TOKEN, CSRF, None)]


@posix_only
def test_get_cookies_uses_only_credentials_file_when_configured(monkeypatch, tmp_path) -> None:
    path = _write_private(tmp_path / "credentials.json", {"auth_token": TOKEN, "ct0": CSRF})
    monkeypatch.setenv(auth.CREDENTIALS_FILE_ENV, path)
    # Environment credentials must be ignored once a credentials file is configured.
    monkeypatch.setenv("TWITTER_AUTH_TOKEN", "c" * 40)
    monkeypatch.setenv("TWITTER_CT0", "d" * 32)
    _no_network_verify(monkeypatch)

    assert auth.get_cookies() == {"auth_token": TOKEN, "ct0": CSRF}


def test_get_cookies_without_credentials_fails_instead_of_reading_browsers(monkeypatch) -> None:
    for name in (auth.CREDENTIALS_FILE_ENV, "TWITTER_AUTH_TOKEN", "TWITTER_CT0", "TWITTER_COOKIE_STRING"):
        monkeypatch.delenv(name, raising=False)
    _no_network_verify(monkeypatch)

    with pytest.raises(AuthenticationError, match="Browser cookie extraction is disabled"):
        auth.get_cookies()


def test_get_cookies_does_not_fall_back_after_verify_failure(monkeypatch) -> None:
    monkeypatch.delenv(auth.CREDENTIALS_FILE_ENV, raising=False)
    monkeypatch.setenv("TWITTER_AUTH_TOKEN", TOKEN)
    monkeypatch.setenv("TWITTER_CT0", CSRF)
    calls = []

    def _verify(auth_token, ct0, cookie_string=None):
        calls.append(auth_token)
        raise AuthenticationError("Cookie expired or invalid (HTTP 401).")

    monkeypatch.setattr(auth, "verify_cookies", _verify)

    with pytest.raises(AuthenticationError, match="HTTP 401"):
        auth.get_cookies()
    assert calls == [TOKEN]


def test_auth_module_has_no_browser_extraction() -> None:
    source = inspect.getsource(auth)
    assert "browser_cookie3" not in source
    assert "subprocess" not in source
    assert not hasattr(auth, "extract_from_browser")


def test_load_from_env_logs_incomplete_env(monkeypatch, caplog) -> None:
    monkeypatch.setenv("TWITTER_AUTH_TOKEN", "token")
    monkeypatch.delenv("TWITTER_CT0", raising=False)
    monkeypatch.delenv("TWITTER_COOKIE_STRING", raising=False)

    with caplog.at_level("DEBUG"):
        cookies = auth.load_from_env()

    assert cookies is None
    assert "Environment cookies incomplete" in caplog.text


def test_load_from_env_accepts_full_cookie_string(monkeypatch) -> None:
    monkeypatch.delenv("TWITTER_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("TWITTER_CT0", raising=False)
    monkeypatch.setenv("TWITTER_COOKIE_STRING", "auth_token=%s; ct0=%s; lang=en" % (TOKEN, CSRF))

    cookies = auth.load_from_env()

    assert cookies == {
        "auth_token": TOKEN,
        "ct0": CSRF,
        "cookie_string": "auth_token=%s; ct0=%s; lang=en" % (TOKEN, CSRF),
    }


def test_build_credentials_accepts_quoted_cookie_values() -> None:
    header = 'guest_id=v1%3A1; personalization_id="v1_abc=="; auth_token=' + TOKEN + "; ct0=" + CSRF

    cookies = auth.build_credentials(cookie_string=header)

    assert cookies["auth_token"] == TOKEN
    assert 'personalization_id="v1_abc=="' in cookies["cookie_string"]


@pytest.mark.parametrize(
    "header",
    [
        "auth_token=%s; ct0=%s\r\nX-Injected: 1" % (TOKEN, CSRF),
        "auth_token=%s" % TOKEN,
        "ct0=%s" % CSRF,
        "auth_token=; ct0=%s" % CSRF,
        "not a header",
    ],
)
def test_build_credentials_rejects_bad_cookie_strings(header) -> None:
    with pytest.raises(AuthenticationError):
        auth.build_credentials(cookie_string=header)


def test_build_credentials_rejects_mismatched_explicit_token() -> None:
    header = "auth_token=%s; ct0=%s" % (TOKEN, CSRF)
    with pytest.raises(AuthenticationError, match="does not match"):
        auth.build_credentials(auth_token="e" * 40, cookie_string=header)


@pytest.mark.parametrize("value", ["has space", "semi;colon", "quote\"", "line\nbreak", "ünicode"])
def test_build_credentials_rejects_unsafe_token_values(value) -> None:
    with pytest.raises(AuthenticationError):
        auth.build_credentials(auth_token=value, ct0=CSRF)


@posix_only
def test_load_from_file_reads_cookie_string(tmp_path) -> None:
    header = "auth_token=%s; ct0=%s; twid=u%%3D1" % (TOKEN, CSRF)
    path = _write_private(tmp_path / "credentials.json", {"cookie_string": header})

    cookies = auth.load_from_file(path)

    assert cookies["auth_token"] == TOKEN
    assert cookies["ct0"] == CSRF
    assert cookies["cookie_string"] == header


@posix_only
def test_load_from_file_rejects_group_or_world_readable_file(tmp_path) -> None:
    path = _write_private(tmp_path / "credentials.json", {"auth_token": TOKEN, "ct0": CSRF}, 0o640)

    with pytest.raises(AuthenticationError, match="chmod 600"):
        auth.load_from_file(path)


@posix_only
def test_load_from_file_rejects_file_owned_by_another_user(monkeypatch, tmp_path) -> None:
    path = _write_private(tmp_path / "credentials.json", {"auth_token": TOKEN, "ct0": CSRF})
    monkeypatch.setattr(auth.os, "getuid", lambda: os.stat(path).st_uid + 1)

    with pytest.raises(AuthenticationError, match="not owned by the current user"):
        auth.load_from_file(path)


@posix_only
def test_load_from_file_rejects_symlink(tmp_path) -> None:
    real = _write_private(tmp_path / "real.json", {"auth_token": TOKEN, "ct0": CSRF})
    link = tmp_path / "credentials.json"
    link.symlink_to(real)

    with pytest.raises(AuthenticationError, match="Cannot open credentials file"):
        auth.load_from_file(str(link))


@posix_only
def test_load_from_file_invalid_json_does_not_echo_content(tmp_path) -> None:
    path = tmp_path / "credentials.json"
    path.write_text('{"auth_token": "%s", ' % TOKEN, encoding="utf-8")
    os.chmod(path, 0o600)

    with pytest.raises(AuthenticationError) as excinfo:
        auth.load_from_file(str(path))

    assert TOKEN not in str(excinfo.value)
    assert excinfo.value.__cause__ is None


@posix_only
def test_load_from_file_rejects_unknown_keys(tmp_path) -> None:
    path = _write_private(tmp_path / "credentials.json", {"auth_token": TOKEN, "ct0": CSRF, "proxy": "x"})

    with pytest.raises(AuthenticationError, match="unknown keys: proxy"):
        auth.load_from_file(path)


def test_load_from_file_missing(tmp_path) -> None:
    with pytest.raises(AuthenticationError, match="not found"):
        auth.load_from_file(str(tmp_path / "missing.json"))


@posix_only
def test_write_credentials_file_is_private_and_round_trips(tmp_path) -> None:
    target = tmp_path / "conf" / "twitter-safe" / "credentials.json"
    credentials = auth.build_credentials(TOKEN, CSRF)

    auth.write_credentials_file(str(target), credentials)

    assert stat.S_IMODE(os.stat(target).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(target.parent).st_mode) == 0o700
    assert auth.load_from_file(str(target)) == credentials
    assert [p.name for p in target.parent.iterdir()] == ["credentials.json"]


@posix_only
def test_write_credentials_file_refuses_symlink_target(tmp_path) -> None:
    elsewhere = tmp_path / "elsewhere.json"
    elsewhere.write_text("keep", encoding="utf-8")
    target = tmp_path / "credentials.json"
    target.symlink_to(elsewhere)

    with pytest.raises(AuthenticationError, match="symlink"):
        auth.write_credentials_file(str(target), auth.build_credentials(TOKEN, CSRF))
    assert elsewhere.read_text(encoding="utf-8") == "keep"


def test_verify_cookies_logs_attempt_summary_on_non_auth_failures(monkeypatch, caplog) -> None:
    class Response:
        def __init__(self, status_code: int, payload=None) -> None:
            self.status_code = status_code
            self._payload = payload or {}

        def json(self):
            return self._payload

    class Session:
        def __init__(self) -> None:
            self.calls = 0

        def get(self, url, headers=None, timeout=5):
            self.calls += 1
            if self.calls == 1:
                return Response(404)
            raise Exception("network")

    monkeypatch.setattr("twitter_cli.client._get_cffi_session", lambda: Session())

    with caplog.at_level("INFO"):
        result = auth.verify_cookies("token", "csrf")

    assert result == {}
    assert "verify_credentials.json=404" in caplog.text
    assert "settings.json=Exception" in caplog.text


def test_verify_cookies_raises_on_401(monkeypatch) -> None:
    class Response:
        status_code = 401

    class Session:
        def get(self, url, headers=None, timeout=5):
            return Response()

    monkeypatch.setattr("twitter_cli.client._get_cffi_session", lambda: Session())

    with pytest.raises(AuthenticationError) as excinfo:
        auth.verify_cookies(TOKEN, CSRF)
    assert TOKEN not in str(excinfo.value)
