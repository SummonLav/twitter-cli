#!/bin/sh
# Regenerate the hash-locked requirement files used by deploy/install.sh.
# Run after changing dependencies in pyproject.toml (and `uv lock`), then
# review the diff before committing: these hashes are what root will trust.
set -eu
cd "$(dirname "$0")/.."

uv lock --check
uv export --frozen --no-dev --no-emit-project --format requirements-txt --no-header \
    -o deploy/requirements.lock >/dev/null
# Keep the build backend on releases at least two weeks old.
CUTOFF=$(python3 -c 'import datetime; print((datetime.date.today() - datetime.timedelta(days=14)).isoformat())')
uv pip compile --quiet --universal --python-version 3.10 --generate-hashes \
    --exclude-newer "$CUTOFF" --no-header deploy/build-requirements.in \
    -o deploy/build-requirements.lock
git diff --stat -- deploy/requirements.lock deploy/build-requirements.lock
