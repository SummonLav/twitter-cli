# Modified in the SummonLav fork; see FORK.md.
"""Consistency checks for the deploy/ files used to install twitter-safe as root."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
DEPLOY = ROOT / "deploy"
_PIN_RE = re.compile(r"^([A-Za-z0-9_.-]+)==([^ ;\\]+)")


def _pins(path: Path) -> dict:
    pins = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = _PIN_RE.match(line)
        if match:
            pins[match.group(1).lower()] = match.group(2)
    return pins


def _hash_blocks(path: Path):
    """Yield (requirement line, number of --hash lines) for each pinned requirement."""
    current = None
    count = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if _PIN_RE.match(line):
            if current is not None:
                yield current, count
            current, count = line, 0
        elif line.strip().startswith("--hash=sha256:"):
            count += 1
    if current is not None:
        yield current, count


@pytest.mark.parametrize("name", ["requirements.lock", "build-requirements.lock"])
def test_every_locked_requirement_is_hashed(name) -> None:
    blocks = list(_hash_blocks(DEPLOY / name))
    assert blocks
    for requirement, hashes in blocks:
        assert hashes > 0, requirement


def test_runtime_lock_has_no_browser_cookie_extraction() -> None:
    pins = _pins(DEPLOY / "requirements.lock")
    assert "browser-cookie3" not in pins
    assert "twitter-cli" not in pins  # the project itself is built offline from source


@pytest.mark.skipif(sys.version_info < (3, 11), reason="tomllib")
def test_runtime_lock_matches_uv_lock() -> None:
    import tomllib

    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    uv_versions = {pkg["name"].lower(): pkg["version"] for pkg in lock["package"]}
    for name, version in _pins(DEPLOY / "requirements.lock").items():
        assert uv_versions.get(name) == version, "%s: run deploy/update-locks.sh" % name


def test_build_lock_pins_hatchling() -> None:
    assert "hatchling" in _pins(DEPLOY / "build-requirements.lock")


@pytest.mark.skipif(shutil.which("sh") is None, reason="needs sh")
@pytest.mark.parametrize(
    "script", ["install.sh", "uninstall.sh", "update-locks.sh", "path-security.sh"],
)
def test_deploy_scripts_parse(script) -> None:
    result = subprocess.run(["sh", "-n", str(DEPLOY / script)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def _path_check(tmp_path, target, *, untrusted_owner="", execute=False, preflight=False):
    """Run shipped checks against real files, simulating UIDs without root access."""
    source = (DEPLOY / "install.sh").read_text()
    helpers = source.split("# ── helpers", 1)[1].split("# ── 1.", 1)[0].split("\n", 1)[1]
    action = 'resolved=$(resolve_root_owned_path "$TARGET"); printf "%s\\n" "$resolved"'
    if execute:
        action += '\n"$resolved"'
    if preflight:
        action = source.split("# ── 2.", 1)[1].split("if [ \"$CHECK_ONLY\" = 1 ]", 1)[0]
        action = action.split("\n", 1)[1]
    script = "set -eu\ndie() { echo \"$*\" >&2; exit 1; }\n" + helpers + """
owner_uid() {
    if [ "$1" = "$UNTRUSTED_OWNER" ]; then echo 501; else echo 0; fi
}
mode_bits() {
    case "$1" in
        "$FIXTURE_ROOT"|"$FIXTURE_ROOT"/*)
            stat -c %a "$1" 2>/dev/null || stat -f %Lp "$1" ;;
        *) echo 755 ;;
    esac
}
. "$PATH_HELPER"
""" + action
    return subprocess.run(
        ["sh", "-c", script], capture_output=True, text=True,
        env={
            **os.environ, "TARGET": str(target), "PYTHON": str(target),
            "FIXTURE_ROOT": str(tmp_path), "UNTRUSTED_OWNER": str(untrusted_owner),
            "PATH_HELPER": str(DEPLOY / "path-security.sh"),
        },
    )


def _fake_python(tmp_path):
    binary = tmp_path / "trusted" / "python3"
    binary.parent.mkdir()
    marker = tmp_path / "executed"
    binary.write_text("#!/bin/sh\nprintf executed > " + shlex.quote(str(marker)) + "\n")
    binary.chmod(0o755)
    return binary, marker


def test_python_path_resolves_trusted_relative_and_absolute_links(tmp_path) -> None:
    binary, marker = _fake_python(tmp_path)
    aliases = tmp_path / "aliases"
    aliases.mkdir()
    first = aliases / "python3"
    first.symlink_to("../trusted/python3")
    outer = tmp_path / "current-python"
    outer.symlink_to(first)
    result = _path_check(tmp_path, outer, execute=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(binary)
    assert marker.read_text() == "executed"


@pytest.mark.parametrize("mode", [0o775, 0o777])
def test_python_preflight_rejects_writable_alias_parent_before_execution(tmp_path, mode) -> None:
    binary, marker = _fake_python(tmp_path)
    aliases = tmp_path / "agent-bin"
    aliases.mkdir(mode=mode)
    aliases.chmod(mode)
    alias = aliases / "python3"
    alias.symlink_to(binary)
    result = _path_check(tmp_path, alias, preflight=True)
    assert result.returncode != 0
    assert "writable by group or others" in result.stderr
    assert not marker.exists()


@pytest.mark.parametrize("untrusted", ["alias", "parent", "target"])
def test_python_preflight_rejects_nonroot_path_before_execution(tmp_path, untrusted) -> None:
    binary, marker = _fake_python(tmp_path)
    aliases = tmp_path / "agent-bin"
    aliases.mkdir()
    alias = aliases / "python3"
    alias.symlink_to(binary)
    owner_path = {"alias": alias, "parent": aliases, "target": binary}[untrusted]
    result = _path_check(tmp_path, alias, untrusted_owner=owner_path, preflight=True)
    assert result.returncode != 0
    assert "must be owned by root" in result.stderr
    assert not marker.exists()


def test_python_path_checks_intermediate_symlink_target(tmp_path) -> None:
    binary, marker = _fake_python(tmp_path)
    mutable = tmp_path / "mutable"
    mutable.mkdir()
    inner = mutable / "python3"
    inner.symlink_to(binary)
    outer = tmp_path / "python3"
    outer.symlink_to(inner)
    result = _path_check(tmp_path, outer, untrusted_owner=mutable, execute=True)
    assert result.returncode != 0
    assert "must be owned by root" in result.stderr
    assert not marker.exists()


def test_installed_venv_link_cannot_escape_through_mutable_alias(tmp_path) -> None:
    binary, marker = _fake_python(tmp_path)
    alias = tmp_path / "agent-alias"
    alias.symlink_to(binary)
    venv_bin = tmp_path / "venv" / "bin"
    venv_bin.mkdir(parents=True)
    (venv_bin / "python").symlink_to(alias)
    result = _path_check(tmp_path, venv_bin / "python", untrusted_owner=alias, execute=True)
    assert result.returncode != 0
    assert not marker.exists()


def test_python_path_rejects_symlink_loop(tmp_path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.symlink_to(second)
    second.symlink_to(first)
    result = _path_check(tmp_path, first)
    assert result.returncode != 0
    assert "too many symlinks" in result.stderr


@pytest.fixture
def private_config():
    spec = importlib.util.spec_from_file_location("private_config", DEPLOY / "private-config.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_site_config_is_private_before_secret_is_written(tmp_path, monkeypatch, private_config):
    target = tmp_path / "twitter-safe.json"
    original_dump = private_config.json.dump

    def checked_dump(value, handle):
        info = os.fstat(handle.fileno())
        assert stat.S_IMODE(info.st_mode) == 0o640
        assert info.st_gid == os.getgid()
        original_dump(value, handle)

    monkeypatch.setattr(private_config.json, "dump", checked_dump)
    private_config.write_site_config(
        target, os.getgid(), "http://user:fake-secret@localhost:8080", "en_US.UTF-8",
        owner_uid=os.getuid(),
    )
    assert stat.S_IMODE(target.stat().st_mode) == 0o640
    assert target.stat().st_uid == os.getuid()
    assert json.loads(target.read_text())["proxy"] == "http://user:fake-secret@localhost:8080"


def test_site_config_migrates_public_file_without_new_options(tmp_path, private_config):
    target = tmp_path / "twitter-safe.json"
    settings = {"proxy": "http://user:fake-secret@localhost:8080", "lang": "en_US.UTF-8"}
    target.write_text(json.dumps(settings))
    target.chmod(0o644)
    private_config.write_site_config(target, os.getgid(), "", "", owner_uid=os.getuid())
    assert stat.S_IMODE(target.stat().st_mode) == 0o640
    assert json.loads(target.read_text()) == settings


def test_site_config_update_preserves_unspecified_settings(tmp_path, private_config):
    target = tmp_path / "twitter-safe.json"
    target.write_text(json.dumps({"proxy": "http://localhost:8080", "lang": "en_US.UTF-8"}))
    target.chmod(0o640)
    private_config.write_site_config(target, os.getgid(), "", "zh_CN.UTF-8", owner_uid=os.getuid())
    assert json.loads(target.read_text()) == {
        "proxy": "http://localhost:8080", "lang": "zh_CN.UTF-8",
    }


def test_site_config_rejects_symlink(tmp_path, private_config):
    original = tmp_path / "original"
    original.write_text("{}")
    target = tmp_path / "twitter-safe.json"
    target.symlink_to(original)
    with pytest.raises(OSError):
        private_config.write_site_config(target, os.getgid(), "", "", owner_uid=os.getuid())
    assert original.read_text() == "{}"


def test_site_config_failed_update_preserves_original(tmp_path, monkeypatch, private_config):
    target = tmp_path / "twitter-safe.json"
    target.write_text('{"proxy": "http://localhost:8080"}')
    target.chmod(0o640)
    original = target.read_bytes()

    def fail_replace(source, destination):
        raise OSError("simulated replacement failure")

    monkeypatch.setattr(private_config.os, "replace", fail_replace)
    with pytest.raises(OSError):
        private_config.write_site_config(
            target, os.getgid(), "http://localhost:9000", "", owner_uid=os.getuid(),
        )
    assert target.read_bytes() == original
    assert list(tmp_path.glob(".twitter-safe-*")) == []


@pytest.mark.parametrize("member_source", ["explicit", "primary", "same_gid_alias"])
def test_private_group_rejects_other_accounts(monkeypatch, private_config, member_source):
    service = SimpleNamespace(pw_name="service", pw_uid=501, pw_gid=20)
    other = SimpleNamespace(pw_name="agent", pw_uid=502, pw_gid=20)
    group = SimpleNamespace(gr_name="twitter-safe", gr_gid=499, gr_mem=[])
    groups = [group]
    if member_source == "explicit":
        group.gr_mem.append("agent")
    elif member_source == "primary":
        other.pw_gid = group.gr_gid
    else:
        groups.append(SimpleNamespace(gr_name="shared-alias", gr_gid=499, gr_mem=["agent"]))
    monkeypatch.setattr(private_config.pwd, "getpwnam", lambda name: service)
    monkeypatch.setattr(private_config.pwd, "getpwall", lambda: [service, other])
    monkeypatch.setattr(private_config.grp, "getgrnam", lambda name: group)
    monkeypatch.setattr(private_config.grp, "getgrall", lambda: groups)
    with pytest.raises(private_config.ConfigError, match="shared"):
        private_config.validate_private_group("service", "twitter-safe")


@pytest.mark.parametrize("extra", ["NestedGroups", "GroupMembers", "GroupMembership", None])
def test_macos_private_group_checks_directory_members(monkeypatch, private_config, extra):
    service = SimpleNamespace(pw_name="service", pw_uid=501, pw_gid=20)
    group = SimpleNamespace(gr_name="_twitter_safe", gr_gid=499, gr_mem=["service"])
    monkeypatch.setattr(private_config.pwd, "getpwnam", lambda name: service)
    monkeypatch.setattr(private_config.pwd, "getpwall", lambda: [service])
    monkeypatch.setattr(private_config.grp, "getgrnam", lambda name: group)
    monkeypatch.setattr(private_config.grp, "getgrall", lambda: [group])
    monkeypatch.setattr(private_config.sys, "platform", "darwin")
    record = {"dsAttrTypeStandard:GroupMembers": ["service-uuid"]}
    if extra:
        record["dsAttrTypeStandard:" + extra] = ["another-account"]
    monkeypatch.setattr(
        private_config, "_directory_record",
        lambda path: record if path.startswith("/Groups/")
        else {"dsAttrTypeStandard:GeneratedUID": ["service-uuid"]},
    )
    if extra:
        with pytest.raises(private_config.ConfigError):
            private_config.validate_private_group("service", "_twitter_safe")
    else:
        assert private_config.validate_private_group("service", "_twitter_safe") == 499
