from __future__ import annotations

import json
import os
import stat
import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="twitter-safe is POSIX only")

from twitter_cli import safe  # noqa: E402
from twitter_cli.cli import cli  # noqa: E402

TOKEN = "a" * 40
CSRF = "b" * 160


@pytest.mark.parametrize(
    "args",
    [
        ["article", "123", "-o", "/tmp/out.md"],
        ["article", "123", "--output=/tmp/out.md"],
        ["article", "123", "-o/tmp/out.md"],
        ["-v", "article", "123", "--output", "x"],
        ["search", "q", "-n", "5", "-o", "x.json"],
        ["feed", "--input", "credentials.json"],
        ["feed", "-i", "credentials.json"],
        ["bookmarks", "folders", "42", "-o", "x"],
        ["user-posts", "jack", "--output", "x"],
    ],
)
def test_check_arguments_rejects_local_file_options(args) -> None:
    with pytest.raises(safe.SafeError, match="disabled"):
        safe.check_arguments(cli, args)


def test_check_arguments_rejects_image_upload(tmp_path) -> None:
    image = tmp_path / "secret.png"
    image.write_bytes(b"\x89PNG")
    with pytest.raises(safe.SafeError, match="disabled"):
        safe.check_arguments(cli, ["post", "hello", "--image", str(image)])
    with pytest.raises(safe.SafeError):
        safe.check_arguments(cli, ["reply", "1", "hi", "-i", str(image)])


@pytest.mark.parametrize(
    "args",
    [
        [],
        ["--help"],
        ["--version"],
        ["feed", "-n", "5", "--json"],
        ["-c", "search", "openai", "--from", "sama", "--since", "2026-01-01", "--yaml"],
        ["article", "https://x.com/i/status/1", "--markdown"],
        ["tweet", "1", "--full-text"],
        ["bookmarks"],
        ["bookmarks", "folders", "42"],
        ["post", "hello world"],
        ["post", "--", "-o is just text"],
        ["delete", "1", "--yes"],
        ["whoami"],
    ],
)
def test_check_arguments_allows_regular_use(args) -> None:
    safe.check_arguments(cli, args)


def test_check_arguments_rejects_unknown_command() -> None:
    with pytest.raises(safe.SafeError, match="unknown command"):
        safe.check_arguments(cli, ["exec-something"])


def test_every_file_option_is_covered() -> None:
    """New upstream options that take a local path must be caught by the policy."""
    import click

    def walk(group):
        ctx = click.Context(group)
        for name in group.list_commands(ctx):
            cmd = group.get_command(ctx, name)
            yield cmd
            if isinstance(cmd, click.Group):
                yield from walk(cmd)

    risky = {
        (cmd.name, param.name)
        for cmd in walk(cli)
        for param in cmd.params
        if param.name in ("output_file", "input_file", "images")
    }
    assert risky, "expected the known file options to exist"
    for cmd in walk(cli):
        for param in cmd.params:
            if (cmd.name, param.name) in risky:
                assert safe._forbidden(param)


def _site_config(tmp_path, payload, mode=0o644):
    path = tmp_path / "twitter-safe.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    os.chmod(path, mode)
    return path


def test_load_site_config_missing_is_empty(tmp_path) -> None:
    assert safe.load_site_config(tmp_path / "missing.json") == {}


def test_load_site_config_accepts_proxy_and_lang(tmp_path) -> None:
    path = _site_config(tmp_path, {"proxy": "http://127.0.0.1:7890", "lang": "zh_CN.UTF-8"})
    config = safe.load_site_config(path, required_owner=os.getuid())
    assert config == {"proxy": "http://127.0.0.1:7890", "lang": "zh_CN.UTF-8"}


def test_load_site_config_requires_owner(tmp_path) -> None:
    path = _site_config(tmp_path, {"proxy": "http://127.0.0.1:7890"})
    with pytest.raises(safe.SafeError, match="owned by uid"):
        safe.load_site_config(path, required_owner=os.getuid() + 1)


def test_load_site_config_rejects_writable_file(tmp_path) -> None:
    path = _site_config(tmp_path, {"proxy": "http://127.0.0.1:7890"}, mode=0o666)
    with pytest.raises(safe.SafeError, match="not group/world writable"):
        safe.load_site_config(path, required_owner=os.getuid())


def test_load_site_config_rejects_symlink(tmp_path) -> None:
    real = _site_config(tmp_path, {"proxy": "http://127.0.0.1:7890"})
    link = tmp_path / "link.json"
    link.symlink_to(real)
    with pytest.raises(safe.SafeError, match="regular file"):
        safe.load_site_config(link, required_owner=os.getuid())


@pytest.mark.parametrize(
    "payload",
    [
        {"proxy": "file:///etc/passwd"},
        {"proxy": "http://"},
        {"proxy": "http://a b"},
        {"lang": "en_US.UTF-8; rm"},
        {"verify": False},
    ],
)
def test_load_site_config_rejects_bad_values(tmp_path, payload) -> None:
    path = _site_config(tmp_path, payload)
    with pytest.raises(safe.SafeError):
        safe.load_site_config(path, required_owner=os.getuid())


def test_build_child_env_inherits_nothing(tmp_path) -> None:
    env = safe.build_child_env(tmp_path, {"proxy": "http://127.0.0.1:7890"})
    assert env == {
        "HOME": str(tmp_path),
        "PATH": "/usr/bin:/bin",
        "LANG": "en_US.UTF-8",
        "TWITTER_CREDENTIALS_FILE": str(tmp_path / ".config" / "twitter-safe" / "credentials.json"),
        "TWITTER_PROXY": "http://127.0.0.1:7890",
    }


@pytest.fixture
def service_account(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    os.chmod(home, 0o700)
    monkeypatch.setattr(safe, "service_home", lambda: home)
    monkeypatch.setattr(safe.os, "geteuid", lambda: 4242)
    monkeypatch.setattr(safe, "site_config_path", lambda: tmp_path / "no-site-config.json")
    monkeypatch.setattr(os, "environ", dict(os.environ))
    monkeypatch.chdir(tmp_path)
    old_umask = os.umask(0o022)
    yield home
    os.umask(old_umask)


def test_main_discards_caller_environment(service_account, monkeypatch, capsys) -> None:
    os.environ.update(
        {
            "TWITTER_AUTH_TOKEN": TOKEN,
            "TWITTER_CT0": CSRF,
            "TWITTER_PROXY": "http://attacker.example:8080",
            "HTTPS_PROXY": "http://attacker.example:8080",
            "PYTHONPATH": "/tmp/evil",
        }
    )

    with pytest.raises(SystemExit) as excinfo:
        safe.main(["whoami", "--json"])

    assert excinfo.value.code != 0
    assert set(os.environ) == {"HOME", "PATH", "LANG", "TWITTER_CREDENTIALS_FILE"}
    assert os.getcwd() == str(service_account)
    out = capsys.readouterr()
    # The env token was discarded; only the (missing) credentials file is used.
    assert "Credentials file not found" in out.out + out.err
    assert TOKEN not in out.out + out.err


def test_main_rejects_file_option_before_running(service_account, capsys) -> None:
    with pytest.raises(SystemExit) as excinfo:
        safe.main(["feed", "--input", str(service_account / ".config/twitter-safe/credentials.json")])
    assert excinfo.value.code == 2
    assert "disabled" in capsys.readouterr().err


def test_main_refuses_root(monkeypatch) -> None:
    monkeypatch.setattr(safe.os, "geteuid", lambda: 0)
    with pytest.raises(SystemExit):
        safe.main(["whoami"])


def test_setup_writes_private_credentials(service_account, monkeypatch, capsys) -> None:
    header = "auth_token=%s; ct0=%s; lang=en" % (TOKEN, CSRF)
    monkeypatch.setattr(safe.sys.stdin, "isatty", lambda: True, raising=False)
    monkeypatch.setattr(safe.getpass, "getpass", lambda prompt="": header)

    safe.setup_main([])

    target = service_account / ".config" / "twitter-safe" / "credentials.json"
    assert stat.S_IMODE(os.stat(target).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(target.parent).st_mode) == 0o700
    assert json.loads(target.read_text(encoding="utf-8"))["cookie_string"] == header
    out = capsys.readouterr().out
    assert "Saved 3 cookies" in out
    assert TOKEN not in out and CSRF not in out


def test_setup_accepts_two_values(service_account, monkeypatch) -> None:
    monkeypatch.setattr(safe.sys.stdin, "isatty", lambda: True, raising=False)
    monkeypatch.setattr(safe.getpass, "getpass", lambda prompt="": "%s %s" % (TOKEN, CSRF))

    safe.setup_main([])

    target = service_account / ".config" / "twitter-safe" / "credentials.json"
    assert json.loads(target.read_text(encoding="utf-8")) == {"auth_token": TOKEN, "ct0": CSRF}


def test_setup_requires_interactive_terminal(service_account, monkeypatch, capsys) -> None:
    monkeypatch.setattr(safe.sys.stdin, "isatty", lambda: False, raising=False)
    monkeypatch.setattr(safe.getpass, "getpass", lambda prompt="": pytest.fail("must not prompt"))

    with pytest.raises(SystemExit):
        safe.setup_main([])
    assert "interactive terminal" in capsys.readouterr().err
    assert not (service_account / ".config" / "twitter-safe" / "credentials.json").exists()


def test_setup_rejects_invalid_input_without_echo(service_account, monkeypatch, capsys) -> None:
    monkeypatch.setattr(safe.sys.stdin, "isatty", lambda: True, raising=False)
    monkeypatch.setattr(safe.getpass, "getpass", lambda prompt="": "auth_token=%s" % TOKEN)

    with pytest.raises(SystemExit):
        safe.setup_main([])
    captured = capsys.readouterr()
    assert TOKEN not in captured.out + captured.err


def test_setup_remove(service_account, monkeypatch, capsys) -> None:
    target = service_account / ".config" / "twitter-safe" / "credentials.json"
    target.parent.mkdir(parents=True)
    target.write_text("{}", encoding="utf-8")

    safe.setup_main(["--remove"])

    assert not target.exists()
    assert "log out this session" in capsys.readouterr().out
