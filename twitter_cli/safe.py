"""Run twitter-cli under a dedicated OS account without exposing its cookies.

SummonLav fork addition (see FORK.md and deploy/). Intended invocation::

    sudo -n -u <service-user> /opt/twitter-safe/bin/twitter-safe <twitter args>

``twitter-safe`` discards the caller's environment, reads credentials only from
the service account's private credentials file, and refuses options that make
twitter-cli read or write local files. ``twitter-safe-setup`` is the only way
to store credentials and requires an interactive terminal, so an agent that
can run ``twitter-safe`` can neither read nor replace them.
"""

from __future__ import annotations

import getpass
import json
import os
import re
import stat
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence
from urllib.parse import urlsplit

PROG = "twitter-safe"
_ALLOWED_PROXY_SCHEMES = {"http", "https", "socks5", "socks5h"}
_LANG_RE = re.compile(r"^[A-Za-z]{2,3}(_[A-Za-z]{2})?\.UTF-8$")
_MAX_SITE_CONFIG_BYTES = 16 * 1024
# Parameters that would let a caller pick a local path for twitter-cli to read
# or write. Matching by name suffix keeps new upstream options closed by default.
_FORBIDDEN_PARAM_NAMES = {"images"}
_FORBIDDEN_PARAM_SUFFIXES = ("_file", "_path", "_dir")


class SafeError(Exception):
    """A refusal or misconfiguration reported to the caller without a traceback."""


def _fail(message: str, code: int = 2) -> None:
    sys.stderr.write("%s: %s\n" % (PROG, message))
    raise SystemExit(code)


def service_home() -> Path:
    """Home directory of the effective user, from the account database (not $HOME)."""
    import pwd

    return Path(pwd.getpwuid(os.geteuid()).pw_dir)


def credentials_path(home: Path) -> Path:
    return home / ".config" / "twitter-safe" / "credentials.json"


def site_config_path() -> Path:
    """``<install root>/etc/twitter-safe.json`` for a venv at ``<install root>/venv``."""
    return Path(sys.prefix).parent / "etc" / "twitter-safe.json"


def load_site_config(path: Path, required_owner: int = 0) -> Dict[str, str]:
    """Load the optional root-owned site config (proxy, lang).

    The file must be a regular file owned by ``required_owner`` that nobody
    else can write, so the caller cannot redirect traffic through a proxy of
    its choosing.
    """
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return {}
    if not stat.S_ISREG(info.st_mode):
        raise SafeError("site config is not a regular file: %s" % path)
    if info.st_uid != required_owner or info.st_mode & 0o022:
        raise SafeError("site config must be owned by uid %d and not group/world writable: %s"
                        % (required_owner, path))
    if info.st_size > _MAX_SITE_CONFIG_BYTES:
        raise SafeError("site config is too large: %s" % path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        raise SafeError("site config is not valid JSON: %s" % path) from None
    if not isinstance(data, dict):
        raise SafeError("site config must be a JSON object: %s" % path)
    unknown = set(data) - {"proxy", "lang"}
    if unknown:
        raise SafeError("site config has unknown keys: %s" % ", ".join(sorted(unknown)))

    config: Dict[str, str] = {}
    proxy = data.get("proxy")
    if proxy is not None:
        if not isinstance(proxy, str) or any(ch.isspace() for ch in proxy):
            raise SafeError("site config proxy must be a URL string")
        parsed = urlsplit(proxy)
        if parsed.scheme not in _ALLOWED_PROXY_SCHEMES or not parsed.hostname:
            raise SafeError("site config proxy must use one of: %s"
                            % ", ".join(sorted(_ALLOWED_PROXY_SCHEMES)))
        config["proxy"] = proxy
    lang = data.get("lang")
    if lang is not None:
        if not isinstance(lang, str) or not _LANG_RE.match(lang):
            raise SafeError("site config lang must look like en_US.UTF-8")
        config["lang"] = lang
    return config


def build_child_env(home: Path, site_config: Dict[str, str]) -> Dict[str, str]:
    """The complete environment twitter-cli runs with; nothing is inherited."""
    env = {
        "HOME": str(home),
        "PATH": "/usr/bin:/bin",
        "LANG": site_config.get("lang", "en_US.UTF-8"),
        "TWITTER_CREDENTIALS_FILE": str(credentials_path(home)),
    }
    if "proxy" in site_config:
        env["TWITTER_PROXY"] = site_config["proxy"]
    return env


def _forbidden(param: Any) -> bool:
    import click

    name = param.name or ""
    return (
        name in _FORBIDDEN_PARAM_NAMES
        or name.endswith(_FORBIDDEN_PARAM_SUFFIXES)
        or isinstance(param.type, (click.Path, click.File))
    )


def check_arguments(group: Any, args: Sequence[str]) -> None:
    """Reject arguments that select a local file, using click's own parser.

    Parsing with the same click objects that will run the command means
    attached values (``-ofile``), ``--opt=value`` and option clusters are all
    seen exactly as twitter-cli will see them.
    """
    import click
    from click.core import ParameterSource

    remaining: List[str] = list(args)
    command: Any = group
    parent: Optional[click.Context] = None
    name = "twitter"
    while True:
        try:
            ctx = command.make_context(name, list(remaining), parent=parent, resilient_parsing=True)
        except click.exceptions.Exit:
            return  # --help / --version
        except click.ClickException as exc:
            raise SafeError(exc.format_message()) from None
        for param in command.params:
            if not _forbidden(param):
                continue
            given = ctx.get_parameter_source(param.name) not in (None, ParameterSource.DEFAULT)
            # A non-empty default would also make the command touch a local path.
            if given or ctx.params.get(param.name) not in (None, (), [], ""):
                raise SafeError(
                    "option %s is disabled: it reads or writes local files. "
                    "Read the command output instead." % "/".join(param.opts)
                )
        if not isinstance(command, click.Group):
            return
        # click >= 8.2 keeps the subcommand in _protected_args (the public
        # name is deprecated); older releases expose protected_args directly.
        protected = getattr(ctx, "_protected_args", None)
        if protected is None:
            protected = ctx.protected_args
        rest = list(protected) + list(ctx.args)
        if not rest:
            return
        sub_name, sub_command, sub_args = command.resolve_command(ctx, rest)
        if sub_command is None:
            raise SafeError("unknown command: %s" % rest[0])
        command, parent, name, remaining = sub_command, ctx, sub_name or rest[0], sub_args


def _require_posix_service_account() -> Path:
    if os.name != "posix":
        _fail("only supported on Linux and macOS")
    if os.geteuid() == 0:
        _fail("refusing to run as root; run it as the dedicated service user")
    return service_home()


def main(argv: Optional[Sequence[str]] = None) -> None:
    """Entry point for ``twitter-safe``."""
    args = list(sys.argv[1:] if argv is None else argv)
    home = _require_posix_service_account()
    os.umask(0o077)

    try:
        site_config = load_site_config(site_config_path())
    except SafeError as exc:
        _fail(str(exc))
        return
    os.environ.clear()
    os.environ.update(build_child_env(home, site_config))
    # twitter-cli also reads ./config.yaml; never let the caller's cwd choose it.
    try:
        os.chdir(home)
    except OSError:
        _fail("service account home %s is not accessible" % home)

    from .cli import cli

    try:
        check_arguments(cli, args)
    except SafeError as exc:
        _fail(str(exc))
    cli.main(args=args, prog_name="twitter")


def _read_secret_line(prompt: str) -> str:
    try:
        return getpass.getpass(prompt)
    except (EOFError, KeyboardInterrupt):
        _fail("cancelled", 1)
        return ""


def setup_main(argv: Optional[Sequence[str]] = None) -> None:
    """Entry point for ``twitter-safe-setup``: store or remove credentials."""
    from .auth import build_credentials, write_credentials_file
    from .exceptions import AuthenticationError

    args = list(sys.argv[1:] if argv is None else argv)
    home = _require_posix_service_account()
    target = credentials_path(home)

    if args == ["--remove"]:
        try:
            target.unlink()
        except FileNotFoundError:
            print("No credentials stored at %s" % target)
            return
        print("Removed %s" % target)
        print("Also log out this session at x.com: Settings > Security and account access > Sessions.")
        return
    if args:
        _fail("usage: twitter-safe-setup [--remove]")

    if not sys.stdin.isatty():
        _fail("credentials can only be entered from an interactive terminal")
    if home.stat().st_mode & 0o077:
        _fail("home directory %s must not be accessible by group or others (chmod 700)" % home)

    print("Paste the Cookie header for x.com (Cookie-Editor > Export > Header String),")
    print("or AUTH_TOKEN and CT0 separated by a space. Input is hidden.")
    value = _read_secret_line("Cookie: ").strip()
    try:
        if "=" in value:
            credentials = build_credentials(cookie_string=value)
        else:
            parts = value.split()
            if len(parts) != 2:
                raise AuthenticationError("expected a Cookie header or exactly two values")
            credentials = build_credentials(parts[0], parts[1])
        os.umask(0o077)
        write_credentials_file(str(target), credentials)
    except AuthenticationError as exc:
        _fail(str(exc))
        return

    cookie_count = len(credentials.get("cookie_string", "").split(";")) if "cookie_string" in credentials else 2
    print("Saved %d cookies to %s (mode 600)." % (cookie_count, target))
    print("Check it with: twitter-safe whoami")


if __name__ == "__main__":
    main()
