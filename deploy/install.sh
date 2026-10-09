#!/bin/sh
# Install twitter-safe from this checkout into a root-owned prefix.
#
# Run it from a root-owned clone so nothing your normal account can modify is
# ever executed as root (see FORK.md):
#
#   sudo /usr/bin/git clone https://github.com/SummonLav/twitter-cli /opt/twitter-safe-src
#   sudo /opt/twitter-safe-src/deploy/install.sh --agent-user "$USER"
#
# Result:
#   /opt/twitter-safe/venv              root-owned venv (hash-locked wheels only)
#   /opt/twitter-safe/bin/twitter-safe  runs twitter-cli as the service user
#   /opt/twitter-safe/bin/tw            what the agent calls: sudo -n -u <service> twitter-safe
#   /etc/sudoers.d/twitter-safe         lets --agent-user run only twitter-safe as the service user
#   ~<service>/.config/twitter-safe/    credentials (written later by twitter-safe-setup)
set -eu
umask 022
PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH
unset PYTHONPATH PYTHONHOME PIP_CONFIG_FILE PIP_INDEX_URL PIP_EXTRA_INDEX_URL 2>/dev/null || true

PREFIX=/opt/twitter-safe
AGENT_USER=
SERVICE_USER=
PYTHON=
PROXY=
LANG_SETTING=
KEEP_SUDO_CACHE=0
CHECK_ONLY=0

usage() {
    cat <<'EOF'
usage: install.sh --agent-user USER [options]
       install.sh --check [--python PATH]

  --check              preflight only: verify this checkout and the Python
                       are root-owned and not writable by others; change nothing
  --agent-user USER    account your AI agent runs as (gets permission to run tw)
  --service-user USER  account that owns the credentials (default: xtwitter on
                       Linux, _xtwitter on macOS; created if missing)
  --python PATH        root-owned Python >= 3.10 to build the venv from
  --proxy URL          proxy for x.com traffic, e.g. http://127.0.0.1:7890
  --lang LANG          locale sent as Accept-Language, e.g. zh_CN.UTF-8
  --keep-sudo-cache    do not set timestamp_timeout=0 for the agent user
                       (see FORK.md: a cached sudo password lets the agent become root)
EOF
}

die() {
    echo "install.sh: $*" >&2
    exit 1
}

while [ $# -gt 0 ]; do
    case "$1" in
        --agent-user) AGENT_USER=${2:?}; shift 2 ;;
        --service-user) SERVICE_USER=${2:?}; shift 2 ;;
        --python) PYTHON=${2:?}; shift 2 ;;
        --proxy) PROXY=${2:?}; shift 2 ;;
        --lang) LANG_SETTING=${2:?}; shift 2 ;;
        --keep-sudo-cache) KEEP_SUDO_CACHE=1; shift ;;
        --check) CHECK_ONLY=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) usage >&2; die "unknown argument: $1" ;;
    esac
done

[ "$(id -u)" = 0 ] || die "must run as root (sudo)"
OS=$(uname -s)
case "$OS" in
    Linux) : "${SERVICE_USER:=xtwitter}" ;;
    Darwin) : "${SERVICE_USER:=_xtwitter}" ;;
    *) die "unsupported OS: $OS" ;;
esac
case "$AGENT_USER$SERVICE_USER" in
    *[!A-Za-z0-9._-]*) die "user names may only contain letters, digits, '.', '_' and '-'" ;;
esac
if [ -n "$AGENT_USER" ]; then
    id "$AGENT_USER" >/dev/null 2>&1 || die "agent user does not exist: $AGENT_USER"
    [ "$AGENT_USER" != root ] || die "the agent user must not be root"
    [ "$AGENT_USER" != "$SERVICE_USER" ] || die "agent and service user must differ"
elif [ "$CHECK_ONLY" = 0 ]; then
    usage >&2
    die "--agent-user is required"
fi

# ── helpers ──────────────────────────────────────────────────────────────
owner_uid() { stat -c %u "$1" 2>/dev/null || stat -f %u "$1"; }
mode_bits() { stat -c %a "$1" 2>/dev/null || stat -f %Lp "$1"; }

# Path and every parent must be root-owned and not writable by group/others.
require_root_owned() {
    p=$1
    while :; do
        [ -e "$p" ] || die "missing: $p"
        [ ! -L "$p" ] || die "unexpected symlink: $p"
        [ "$(owner_uid "$p")" = 0 ] || die "$p must be owned by root"
        m=$(mode_bits "$p")
        [ $((0$m & 022)) -eq 0 ] || die "$p is writable by group or others"
        [ "$p" = / ] && break
        p=$(dirname "$p")
    done
}

# Every file below a directory must be root-owned and not group/other writable.
require_root_owned_tree() {
    bad=$(find "$1" \( ! -user 0 -o -perm -020 -o -perm -002 \) ! -type l -print 2>/dev/null | head -n 5)
    [ -z "$bad" ] || die "not root-owned or writable by others under $1:
$bad"
}

# ── 1. this checkout must be root-owned ─────────────────────────────────
SRC=$(cd -P "$(dirname "$0")/.." && pwd -P)
require_root_owned "$SRC"
require_root_owned_tree "$SRC"
for f in deploy/requirements.lock deploy/build-requirements.lock pyproject.toml twitter_cli/safe.py; do
    [ -f "$SRC/$f" ] || die "missing $SRC/$f"
done
COMMIT=$(git -C "$SRC" rev-parse HEAD 2>/dev/null || echo unknown)

# ── 2. root-owned Python ────────────────────────────────────────────────
if [ -z "$PYTHON" ]; then
    case "$OS" in
        Linux) PYTHON=/usr/bin/python3 ;;
        Darwin) PYTHON=/Library/Frameworks/Python.framework/Versions/Current/bin/python3 ;;
    esac
fi
[ -x "$PYTHON" ] || die "Python not found at $PYTHON (macOS: install from python.org; Homebrew is user-writable)"
# macOS python.org installs may be admin-group writable; FORK.md shows the chmod that fixes it.
"$PYTHON" -I -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' \
    || die "$PYTHON is older than 3.10"
PY_REAL=$("$PYTHON" -I -c 'import os, sys; print(os.path.realpath(sys.executable))')
PY_STDLIB=$("$PYTHON" -I -c 'import os, sysconfig; print(os.path.realpath(sysconfig.get_paths()["stdlib"]))')
require_root_owned "$PY_REAL"
require_root_owned "$PY_STDLIB"
require_root_owned_tree "$PY_STDLIB"
"$PYTHON" -I -c 'import venv, ensurepip' 2>/dev/null \
    || die "$PYTHON lacks venv/ensurepip (Debian/Ubuntu: apt install python3-venv)"

if [ "$CHECK_ONLY" = 1 ]; then
    echo "preflight OK: $SRC ($COMMIT) and $PY_REAL are root-owned and not writable by others"
    exit 0
fi

# ── 3. service user and its private home ────────────────────────────────
if ! id "$SERVICE_USER" >/dev/null 2>&1; then
    echo "Creating service user $SERVICE_USER"
    case "$OS" in
        Linux)
            command -v useradd >/dev/null 2>&1 || die "useradd not found; create $SERVICE_USER manually"
            NOLOGIN=$(command -v nologin 2>/dev/null || echo /bin/false)
            useradd --system --user-group --create-home --home-dir "/var/lib/$SERVICE_USER" \
                --shell "$NOLOGIN" "$SERVICE_USER"
            ;;
        Darwin)
            NEW_UID=
            for candidate in $(seq 401 499); do
                if ! dscl . -list /Users UniqueID | awk '{print $2}' | grep -qx "$candidate"; then
                    NEW_UID=$candidate
                    break
                fi
            done
            [ -n "$NEW_UID" ] || die "no free UID in 401-499 for $SERVICE_USER"
            dscl . -create "/Users/$SERVICE_USER"
            dscl . -create "/Users/$SERVICE_USER" UniqueID "$NEW_UID"
            dscl . -create "/Users/$SERVICE_USER" PrimaryGroupID 20
            dscl . -create "/Users/$SERVICE_USER" UserShell /usr/bin/false
            dscl . -create "/Users/$SERVICE_USER" NFSHomeDirectory "/var/$SERVICE_USER"
            dscl . -create "/Users/$SERVICE_USER" RealName "twitter-safe service account"
            dscl . -create "/Users/$SERVICE_USER" IsHidden 1
            dscl . -create "/Users/$SERVICE_USER" Password '*'
            ;;
    esac
fi
SERVICE_UID=$(id -u "$SERVICE_USER")
[ "$SERVICE_UID" != 0 ] || die "service user must not be root"
SERVICE_HOME=$("$PYTHON" -I -c 'import pwd, sys; print(pwd.getpwnam(sys.argv[1]).pw_dir)' "$SERVICE_USER")
case "$SERVICE_HOME" in
    /var/*|/private/var/*) ;;
    *) die "unexpected home for $SERVICE_USER: $SERVICE_HOME (expected under /var)" ;;
esac
mkdir -p "$SERVICE_HOME"
SERVICE_HOME_REAL=$(cd -P "$SERVICE_HOME" && pwd -P)
require_root_owned "$(dirname "$SERVICE_HOME_REAL")"
[ ! -L "$SERVICE_HOME" ] || die "$SERVICE_HOME must not be a symlink"
chown "$SERVICE_USER" "$SERVICE_HOME_REAL"
chmod 700 "$SERVICE_HOME_REAL"

# ── 4. build: hash-locked wheels only, no network for the project itself ─
STAGE=$(mktemp -d /tmp/twitter-safe-build.XXXXXX)
trap 'rm -rf "$STAGE"' EXIT INT TERM
PIP_LOCKED="--isolated --no-input --disable-pip-version-check --require-hashes --only-binary=:all: --no-deps"
PIP_OFFLINE="--isolated --no-input --disable-pip-version-check --no-index --no-deps"

echo "Building twitter-cli $COMMIT with $PY_REAL"
"$PYTHON" -I -m venv "$STAGE/build-venv"
# shellcheck disable=SC2086
"$STAGE/build-venv/bin/python" -I -m pip install $PIP_LOCKED -r "$SRC/deploy/build-requirements.lock"
cp -R "$SRC" "$STAGE/src"
rm -rf "$STAGE/src/.git" "$STAGE/src/.venv" "$STAGE/src/dist" "$STAGE/src/build"
# shellcheck disable=SC2086
"$STAGE/build-venv/bin/python" -I -m pip wheel $PIP_OFFLINE --no-build-isolation -w "$STAGE/wheels" "$STAGE/src"

mkdir -p "$PREFIX"
require_root_owned "$PREFIX"
NEW_VENV="$PREFIX/venv.new"
rm -rf "$NEW_VENV"
"$PYTHON" -I -m venv "$NEW_VENV"
# shellcheck disable=SC2086
"$NEW_VENV/bin/python" -I -m pip install $PIP_LOCKED -r "$SRC/deploy/requirements.lock"
# shellcheck disable=SC2086
"$NEW_VENV/bin/python" -I -m pip install $PIP_OFFLINE "$STAGE"/wheels/twitter_cli-*.whl
"$NEW_VENV/bin/python" -I -c 'import twitter_cli.safe, twitter_cli.cli' \
    || die "installed package does not import"
if "$NEW_VENV/bin/python" -I -c 'import browser_cookie3' 2>/dev/null; then
    die "browser_cookie3 must not be installed"
fi
rm -rf "$PREFIX/venv.old"
[ ! -d "$PREFIX/venv" ] || mv "$PREFIX/venv" "$PREFIX/venv.old"
mv "$NEW_VENV" "$PREFIX/venv"
rm -rf "$PREFIX/venv.old"

# ── 5. launchers ─────────────────────────────────────────────────────────
mkdir -p "$PREFIX/bin" "$PREFIX/etc"
cat > "$PREFIX/bin/twitter-safe" <<EOF
#!/bin/sh
exec $PREFIX/venv/bin/python -I -m twitter_cli.safe "\$@"
EOF
cat > "$PREFIX/bin/twitter-safe-setup" <<EOF
#!/bin/sh
exec $PREFIX/venv/bin/python -I -c 'from twitter_cli.safe import setup_main; setup_main()' "\$@"
EOF
cat > "$PREFIX/bin/tw" <<EOF
#!/bin/sh
# Runs twitter-cli as $SERVICE_USER; the caller never sees the credentials.
exec /usr/bin/sudo -n -u $SERVICE_USER $PREFIX/bin/twitter-safe "\$@"
EOF
chmod 755 "$PREFIX/bin/twitter-safe" "$PREFIX/bin/twitter-safe-setup" "$PREFIX/bin/tw"
printf '%s\n' "$COMMIT" > "$PREFIX/INSTALLED_COMMIT"

SITE_CONFIG="$PREFIX/etc/twitter-safe.json"
if [ -n "$PROXY$LANG_SETTING" ]; then
    "$PYTHON" -I -c '
import json, sys
config = {}
if sys.argv[2]:
    config["proxy"] = sys.argv[2]
if sys.argv[3]:
    config["lang"] = sys.argv[3]
with open(sys.argv[1], "w", encoding="utf-8") as handle:
    json.dump(config, handle)
' "$SITE_CONFIG" "$PROXY" "$LANG_SETTING"
    chmod 644 "$SITE_CONFIG"
fi
chown -R root "$PREFIX"
chmod -R go-w "$PREFIX"
require_root_owned_tree "$PREFIX"

# ── 6. sudoers ───────────────────────────────────────────────────────────
SUDOERS_TMP=$(mktemp /tmp/twitter-safe-sudoers.XXXXXX)
{
    echo "# Managed by twitter-safe deploy/install.sh ($COMMIT)."
    echo "# $AGENT_USER may run only twitter-safe as $SERVICE_USER, with a reset environment."
    echo "Defaults!$PREFIX/bin/twitter-safe env_reset"
    if [ "$KEEP_SUDO_CACHE" = 0 ]; then
        echo "# A cached sudo password would let the agent run anything as root."
        echo "Defaults:$AGENT_USER timestamp_timeout=0"
    fi
    echo "$AGENT_USER ALL=($SERVICE_USER) NOPASSWD: $PREFIX/bin/twitter-safe"
} > "$SUDOERS_TMP"
visudo -cf "$SUDOERS_TMP" >/dev/null || { rm -f "$SUDOERS_TMP"; die "generated sudoers rule is invalid"; }
SUDOERS_DIR=/etc/sudoers.d
[ -d "$SUDOERS_DIR" ] || die "$SUDOERS_DIR does not exist"
chown root "$SUDOERS_TMP"
chmod 440 "$SUDOERS_TMP"
mv "$SUDOERS_TMP" "$SUDOERS_DIR/twitter-safe"
visudo -c >/dev/null || die "sudoers is invalid after installing the rule"

# ── 7. self-test and warnings ────────────────────────────────────────────
if ! sudo -u "$AGENT_USER" "$PREFIX/bin/tw" --version >/dev/null 2>&1; then
    die "self-test failed: $AGENT_USER cannot run $PREFIX/bin/tw (is $SUDOERS_DIR included by /etc/sudoers?)"
fi

AGENT_SUDO=$(sudo -l -U "$AGENT_USER" 2>/dev/null || true)
if printf '%s\n' "$AGENT_SUDO" | grep -Eq 'NOPASSWD: *ALL|NOPASSWD:.*\(ALL'; then
    echo "WARNING: $AGENT_USER has passwordless root via sudo; the agent can read the credentials." >&2
fi
if [ "$OS" = Linux ] && id -nG "$AGENT_USER" | tr ' ' '\n' | grep -Eqx 'docker|lxd|libvirt|disk'; then
    echo "WARNING: $AGENT_USER is in a root-equivalent group (docker/lxd/libvirt/disk)." >&2
fi

cat <<EOF

twitter-safe installed from commit $COMMIT.

Next, store the credentials yourself (not through the agent):
  sudo -u $SERVICE_USER $PREFIX/bin/twitter-safe-setup

Then the agent uses:
  $PREFIX/bin/tw whoami
  $PREFIX/bin/tw search "query" -n 20 --json
EOF
