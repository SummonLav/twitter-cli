#!/bin/sh
# Remove twitter-safe (sudoers rule and /opt/twitter-safe).
# With --purge, also delete the service user, its home and the stored credentials.
set -eu
PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH

PREFIX=/opt/twitter-safe
SERVICE_USER=
PURGE=0

while [ $# -gt 0 ]; do
    case "$1" in
        --service-user) SERVICE_USER=${2:?}; shift 2 ;;
        --purge) PURGE=1; shift ;;
        -h|--help) echo "usage: uninstall.sh [--service-user USER] [--purge]"; exit 0 ;;
        *) echo "uninstall.sh: unknown argument: $1" >&2; exit 1 ;;
    esac
done

[ "$(id -u)" = 0 ] || { echo "uninstall.sh: must run as root (sudo)" >&2; exit 1; }
OS=$(uname -s)
case "$OS" in
    Linux) : "${SERVICE_USER:=xtwitter}" ;;
    Darwin) : "${SERVICE_USER:=_xtwitter}" ;;
    *) echo "uninstall.sh: unsupported OS: $OS" >&2; exit 1 ;;
esac

rm -f /etc/sudoers.d/twitter-safe
rm -rf "$PREFIX"
echo "Removed /etc/sudoers.d/twitter-safe and $PREFIX"

if [ "$PURGE" = 1 ] && id "$SERVICE_USER" >/dev/null 2>&1; then
    case "$OS" in
        Linux)
            userdel -r "$SERVICE_USER" 2>/dev/null || userdel "$SERVICE_USER"
            ;;
        Darwin)
            HOME_DIR=$(dscl . -read "/Users/$SERVICE_USER" NFSHomeDirectory | awk '{print $2}')
            dscl . -delete "/Users/$SERVICE_USER"
            case "$HOME_DIR" in
                /var/*|/private/var/*) rm -rf "$HOME_DIR" ;;
            esac
            ;;
    esac
    echo "Deleted service user $SERVICE_USER and its stored credentials"
fi

echo "To invalidate the cookies themselves, log out that session on x.com:"
echo "  Settings > Security and account access > Apps and sessions > Sessions"
