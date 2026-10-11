#!/bin/sh
# Modified in the SummonLav fork; see FORK.md.
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
# Preserve the fork modification notice when regenerating derived lock files.
python3 - <<'PY'
from pathlib import Path

notice = "# Modified in the SummonLav fork; see FORK.md.\n"
for name in ("uv.lock", "deploy/requirements.lock", "deploy/build-requirements.lock"):
    path = Path(name)
    content = path.read_text(encoding="utf-8")
    if not content.startswith(notice):
        path.write_text(notice + content, encoding="utf-8")
PY
git diff --stat -- deploy/requirements.lock deploy/build-requirements.lock
