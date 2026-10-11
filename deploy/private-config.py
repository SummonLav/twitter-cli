# Modified in the SummonLav fork; see FORK.md.
"""Install a root-owned site config readable only by its private service group."""

from __future__ import annotations

import grp
import json
import os
import plistlib
import pwd
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

MAX_CONFIG_BYTES = 16 * 1024


class ConfigError(ValueError):
    """A diagnostic containing no configuration values."""


def _directory_record(path: str) -> dict:
    result = subprocess.run(
        ["/usr/bin/dscl", "-plist", ".", "-read", path],
        check=True, capture_output=True,
    )
    return plistlib.loads(result.stdout)


def validate_private_group(service_user: str, group_name: str) -> int:
    """Refuse existing shared groups instead of changing their other members."""
    service = pwd.getpwnam(service_user)
    group = grp.getgrnam(group_name)
    if group.gr_gid == 0:
        raise ConfigError("the site config group must not be a root group")
    # Other group names may refer to the same numeric GID.
    aliases = [entry for entry in grp.getgrall() if entry.gr_gid == group.gr_gid]
    members = {member for entry in aliases for member in entry.gr_mem}
    members.update(entry.pw_name for entry in pwd.getpwall() if entry.pw_gid == group.gr_gid)
    if members - {service_user}:
        raise ConfigError("the site config group is shared with another account")
    if sys.platform == "darwin":
        for name in {group_name, *(entry.gr_name for entry in aliases)}:
            record = _directory_record("/Groups/" + name)
            if record.get("dsAttrTypeStandard:NestedGroups"):
                raise ConfigError("the site config group contains nested groups")
            if set(record.get("dsAttrTypeStandard:GroupMembership", [])) - {service_user}:
                raise ConfigError("the site config group is shared with another account")
            member_ids = set(record.get("dsAttrTypeStandard:GroupMembers", []))
            if member_ids:
                service_record = _directory_record("/Users/" + service.pw_name)
                service_ids = set(service_record.get("dsAttrTypeStandard:GeneratedUID", []))
                if member_ids - service_ids:
                    raise ConfigError("the site config group contains another account's UUID")
    return group.gr_gid


def write_site_config(
    path: Path, gid: int, proxy: str, lang: str, *, owner_uid: int = 0,
) -> None:
    """Preserve existing settings and atomically migrate old public files to 0640."""
    config = {}
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        if not proxy and not lang:
            return
    else:
        with os.fdopen(fd, "r", encoding="utf-8") as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != owner_uid:
                raise ConfigError("existing site config must be a root-owned regular file")
            if info.st_mode & 0o022 or info.st_size > MAX_CONFIG_BYTES:
                raise ConfigError("existing site config has unsafe permissions or size")
            config = json.load(handle)
        if not isinstance(config, dict) or set(config) - {"proxy", "lang"}:
            raise ConfigError("existing site config has an invalid format")
    if proxy:
        config["proxy"] = proxy
    if lang:
        config["lang"] = lang

    fd, temporary = tempfile.mkstemp(prefix=".twitter-safe-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            # Apply ownership and permissions before any secret bytes are written.
            os.fchown(handle.fileno(), owner_uid, gid)
            os.fchmod(handle.fileno(), 0o640)
            json.dump(config, handle)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main() -> None:
    if os.geteuid() != 0:
        raise ConfigError("must run as root")
    command, *args = sys.argv[1:]
    if command == "validate-group" and len(args) == 2:
        validate_private_group(*args)
    elif command == "write" and len(args) == 2:
        # The proxy can contain a password; do not put it in child-process argv.
        raw = sys.stdin.buffer.read(MAX_CONFIG_BYTES + 1)
        if len(raw) > MAX_CONFIG_BYTES:
            raise ConfigError("site config settings are too large")
        proxy, lang, end = raw.decode("utf-8").split("\0")
        if end:
            raise ConfigError("invalid site config input")
        write_site_config(Path(args[0]), grp.getgrnam(args[1]).gr_gid, proxy, lang)
    else:
        raise ConfigError("invalid private-config command")


if __name__ == "__main__":
    try:
        main()
    except ConfigError as exc:
        sys.exit("install.sh: " + str(exc))
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        # JSON/parser/OS errors can echo input; never print configuration values.
        sys.exit("install.sh: private site config validation or update failed")
