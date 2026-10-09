"""Cookie authentication for Twitter/X.

Credentials come only from an explicit source, checked in this order:

1. ``TWITTER_CREDENTIALS_FILE``: a JSON file that must be a regular file owned
   by the current user with mode 0600. When this variable is set, the
   environment-variable source is never consulted.
2. Environment variables: ``TWITTER_AUTH_TOKEN`` + ``TWITTER_CT0`` and an
   optional full ``TWITTER_COOKIE_STRING``.

Modified in the SummonLav fork (see FORK.md): automatic extraction from local
browser cookie stores was removed. Upstream decrypted every installed
browser's cookie database whenever explicit credentials were missing or were
rejected by X, which silently replaced the credentials the user chose.
"""

from __future__ import annotations

import json
import logging
import os
import stat
import tempfile
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from .constants import BEARER_TOKEN, get_user_agent
from .exceptions import AuthenticationError

logger = logging.getLogger(__name__)

CREDENTIALS_FILE_ENV = "TWITTER_CREDENTIALS_FILE"
MAX_CREDENTIALS_BYTES = 64 * 1024
_MAX_COOKIE_VALUE_CHARS = 8192


def _validate_cookie_value(name: str, value: Any, strict: bool = True) -> str:
    """Return a cookie value that is safe to place in a Cookie header.

    ``strict`` is used for auth_token/ct0 (opaque tokens). Other cookies may be
    empty or quoted (X sets ``personalization_id="v1_..."``), but every value
    must stay printable ASCII without ``;`` so it cannot inject header lines or
    extra cookies.
    """
    if not isinstance(value, str) or (strict and not value):
        raise AuthenticationError("%s is missing or empty" % name)
    if len(value) > _MAX_COOKIE_VALUE_CHARS:
        raise AuthenticationError("%s is too long" % name)
    if strict:
        bad = any(ord(ch) <= 0x20 or ord(ch) >= 0x7F or ch in ';,"\\' for ch in value)
    else:
        bad = any(ord(ch) < 0x20 or ord(ch) >= 0x7F or ch == ";" for ch in value)
    if bad:
        raise AuthenticationError("%s contains characters not allowed in a cookie value" % name)
    return value


def parse_cookie_header(cookie_string: str) -> Dict[str, str]:
    """Parse a ``name=value; name2=value2`` Cookie header into a dict."""
    if not isinstance(cookie_string, str) or not cookie_string.strip():
        raise AuthenticationError("cookie_string is empty")
    if len(cookie_string) > MAX_CREDENTIALS_BYTES:
        raise AuthenticationError("cookie_string is too long")
    if any(ch in cookie_string for ch in "\r\n\x00"):
        raise AuthenticationError("cookie_string contains line breaks")

    cookies: Dict[str, str] = {}
    for part in cookie_string.split(";"):
        part = part.strip()
        if not part:
            continue
        name, sep, value = part.partition("=")
        name = name.strip()
        if not sep or not name:
            raise AuthenticationError("cookie_string is not a 'name=value; ...' header")
        cookies[name] = value.strip()
    return cookies


def build_credentials(
    auth_token: Optional[str] = None,
    ct0: Optional[str] = None,
    cookie_string: Optional[str] = None,
) -> Dict[str, str]:
    """Validate credentials and return the dict consumed by TwitterClient.

    A full ``cookie_string`` must contain ``auth_token`` and ``ct0``; explicit
    values, when also given, must match the ones inside it.
    """
    result: Dict[str, str] = {}
    if cookie_string is not None:
        parsed = parse_cookie_header(cookie_string)
        for name, value in parsed.items():
            if any(ord(ch) <= 0x20 or ord(ch) >= 0x7F or ch in '=,"' for ch in name):
                raise AuthenticationError("cookie_string contains an invalid cookie name")
            _validate_cookie_value("cookie '%s'" % name, value, strict=False)
        for name, explicit in (("auth_token", auth_token), ("ct0", ct0)):
            if name not in parsed:
                raise AuthenticationError("cookie_string does not contain %s" % name)
            if explicit is not None and explicit != parsed[name]:
                raise AuthenticationError("%s does not match the value in cookie_string" % name)
        auth_token = parsed["auth_token"]
        ct0 = parsed["ct0"]
        result["cookie_string"] = "; ".join("%s=%s" % item for item in parsed.items())

    result["auth_token"] = _validate_cookie_value("auth_token", auth_token)
    result["ct0"] = _validate_cookie_value("ct0", ct0)
    return result


def load_from_env() -> Optional[Dict[str, str]]:
    """Load cookies from environment variables."""
    auth_token = os.environ.get("TWITTER_AUTH_TOKEN", "")
    ct0 = os.environ.get("TWITTER_CT0", "")
    cookie_string = os.environ.get("TWITTER_COOKIE_STRING", "")
    if cookie_string:
        return build_credentials(auth_token or None, ct0 or None, cookie_string)
    if auth_token and ct0:
        return build_credentials(auth_token, ct0)
    if auth_token or ct0:
        logger.debug(
            "Environment cookies incomplete: auth_token=%s ct0=%s",
            bool(auth_token),
            bool(ct0),
        )
    return None


def _check_private_file(fd: int, path: str) -> None:
    """Require a regular file owned by the current user and closed to others."""
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode):
        raise AuthenticationError("Credentials file is not a regular file: %s" % path)
    if info.st_size > MAX_CREDENTIALS_BYTES:
        raise AuthenticationError("Credentials file is too large: %s" % path)
    if hasattr(os, "getuid"):
        if info.st_uid != os.getuid():
            raise AuthenticationError("Credentials file is not owned by the current user: %s" % path)
        if info.st_mode & 0o077:
            raise AuthenticationError(
                "Credentials file must not be accessible by group or others "
                "(run: chmod 600 %s)" % path
            )


def load_from_file(path: str) -> Dict[str, str]:
    """Load credentials from a private JSON file.

    Format: ``{"auth_token": "...", "ct0": "..."}`` and/or
    ``{"cookie_string": "auth_token=...; ct0=...; ..."}``.
    """
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(path, flags)
    except FileNotFoundError:
        raise AuthenticationError("Credentials file not found: %s" % path) from None
    except OSError as exc:
        # O_NOFOLLOW makes a symlink fail here (ELOOP) instead of being followed.
        raise AuthenticationError("Cannot open credentials file %s: %s" % (path, exc.strerror)) from None

    try:
        _check_private_file(fd, path)
        raw = b""
        while len(raw) <= MAX_CREDENTIALS_BYTES:
            chunk = os.read(fd, MAX_CREDENTIALS_BYTES + 1 - len(raw))
            if not chunk:
                break
            raw += chunk
    finally:
        os.close(fd)

    if len(raw) > MAX_CREDENTIALS_BYTES:
        raise AuthenticationError("Credentials file is too large: %s" % path)
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        # Never echo the decoder message: it can quote file content.
        raise AuthenticationError("Credentials file is not valid JSON: %s" % path) from None
    if not isinstance(data, dict):
        raise AuthenticationError("Credentials file must contain a JSON object: %s" % path)

    unknown = set(data) - {"auth_token", "ct0", "cookie_string"}
    if unknown:
        raise AuthenticationError(
            "Credentials file has unknown keys: %s" % ", ".join(sorted(unknown))
        )
    return build_credentials(data.get("auth_token"), data.get("ct0"), data.get("cookie_string"))


def write_credentials_file(path: str, credentials: Mapping[str, str]) -> None:
    """Atomically write credentials as a 0600 file inside a 0700 directory.

    Refuses to write through a symlink at the directory or the final path.
    """
    target = Path(path)
    parent = target.parent
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if parent.is_symlink() or target.is_symlink():
        raise AuthenticationError("Refusing to write credentials through a symlink: %s" % path)
    os.chmod(parent, 0o700)

    payload = {
        key: credentials[key] for key in ("auth_token", "ct0", "cookie_string") if key in credentials
    }
    fd, tmp_name = tempfile.mkstemp(dir=str(parent), prefix=".%s." % target.name, suffix=".tmp")
    try:
        os.chmod(tmp_name, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
            handle.flush()
            os.fsync(handle.fileno())
        # os.replace swaps the directory entry; it never follows a symlink
        # that appears at the target after the check above.
        os.replace(tmp_name, str(target))
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def verify_cookies(auth_token: str, ct0: str, cookie_string: Optional[str] = None) -> Dict[str, Any]:
    """Verify cookies by calling a Twitter API endpoint.

    Uses curl_cffi for proper TLS fingerprint.
    Tries multiple endpoints. Only raises on clear auth failures (401/403).
    For other errors (404, network), returns empty dict (proceed without verification).
    """
    from .client import _get_cffi_session

    urls = [
        "https://api.x.com/1.1/account/verify_credentials.json",
        "https://x.com/i/api/1.1/account/settings.json",
    ]

    # Use full cookie string if available, otherwise minimal
    cookie_header = cookie_string or "auth_token=%s; ct0=%s" % (auth_token, ct0)

    headers = {
        "Authorization": "Bearer %s" % BEARER_TOKEN,
        "Cookie": cookie_header,
        "X-Csrf-Token": ct0,
        "X-Twitter-Active-User": "yes",
        "X-Twitter-Auth-Type": "OAuth2Session",
        "User-Agent": get_user_agent(),
    }

    # Reuse the shared curl_cffi session for consistent TLS fingerprint
    session = _get_cffi_session()
    attempts = []

    logger.debug(
        "Verifying Twitter cookies with %s cookie header",
        "full forwarded" if cookie_string else "minimal",
    )

    for url in urls:
        endpoint = url.split("/")[-1]
        try:
            resp = session.get(url, headers=headers, timeout=5)
            if resp.status_code in (401, 403):
                raise AuthenticationError(
                    "Cookie expired or invalid (HTTP %d). Export fresh x.com cookies and "
                    "update your credentials." % resp.status_code
                )
            if resp.status_code == 200:
                data = resp.json()
                attempts.append("%s=200" % endpoint)
                logger.debug("Cookie verification succeeded via %s", endpoint)
                return {"screen_name": data.get("screen_name", "")}
            attempts.append("%s=%d" % (endpoint, resp.status_code))
            logger.debug("Verification endpoint %s returned HTTP %d, trying next...", url, resp.status_code)
            continue
        except RuntimeError:
            raise
        except Exception as e:
            attempts.append("%s=%s" % (endpoint, type(e).__name__))
            logger.debug("Verification endpoint %s failed: %s", url, e)
            continue

    # All endpoints failed with non-auth errors — proceed without verification
    logger.info(
        "Cookie verification skipped (attempts: %s), will verify on first API call",
        ", ".join(attempts) if attempts else "none",
    )
    return {}


def get_cookies() -> Dict[str, str]:
    """Get Twitter cookies from the credentials file or environment variables.

    Raises AuthenticationError when no explicit credentials are available or X
    rejects them. There is no fallback to browser cookie stores.
    """
    credentials_file = os.environ.get(CREDENTIALS_FILE_ENV, "").strip()
    cookies: Optional[Dict[str, str]]
    if credentials_file:
        cookies = load_from_file(credentials_file)
        logger.info("Loaded cookies from credentials file")
    else:
        cookies = load_from_env()
        if cookies:
            logger.info("Loaded cookies from environment variables")

    if not cookies:
        raise AuthenticationError(
            "No Twitter credentials configured.\n"
            "Option 1: Set %s to a private (chmod 600) JSON credentials file\n"
            "Option 2: Set TWITTER_AUTH_TOKEN and TWITTER_CT0 environment variables\n"
            "Browser cookie extraction is disabled in this fork." % CREDENTIALS_FILE_ENV
        )

    verify_cookies(cookies["auth_token"], cookies["ct0"], cookies.get("cookie_string"))
    return cookies
