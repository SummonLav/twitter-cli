"""Consistency checks for the deploy/ files used to install twitter-safe as root."""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

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
@pytest.mark.parametrize("script", ["install.sh", "uninstall.sh", "update-locks.sh"])
def test_deploy_scripts_parse(script) -> None:
    result = subprocess.run(["sh", "-n", str(DEPLOY / script)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
