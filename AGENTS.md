# AGENTS.md — Agent Developer Guide for twitter-cli

This file provides context for AI agents working in this repository.

## Project Overview

- **Project**: twitter-cli — A CLI for Twitter/X (read timelines, bookmarks, search, post, reply, etc.)
- **Language**: Python 3.10+
- **Package Manager**: uv (recommended) / pip
- **Repository**: https://github.com/SummonLav/twitter-cli (fork of jackwener/twitter-cli at 7c634e0; see FORK.md)

## Build, Lint, and Test Commands

```bash
# Install all dependencies (including dev)
uv sync --extra dev

# Run ruff linter
uv run ruff check .

# Run mypy type checker
uv run mypy twitter_cli

# Run all tests (excludes smoke tests by default)
uv run pytest -q

# Run a single test
uv run pytest tests/test_cli.py::test_feed_command -v

# Run tests matching pattern
uv run pytest -k "test_parse" -v
```

## Code Style

- **Line length**: 100 characters
- **Python version**: 3.10+
- Use `from __future__ import annotations` at top of all .py files
- **Functions/variables**: `snake_case`, **Classes**: `PascalCase`, **Constants**: `UPPER_SNAKE_CASE`
- Private functions: prefix with `_`
- Use `@dataclass` for data models (in `models.py`)
- Use Click framework for CLI commands
- Custom exceptions in `exceptions.py`, base: `TwitterError(RuntimeError)`

## Project Structure

```
twitter_cli/
├── cli.py               # Click CLI entry point
├── client.py            # Twitter API client (HTTP)
├── auth.py              # Credentials from a private file or env (no browser extraction)
├── safe.py              # twitter-safe / twitter-safe-setup (service-account wrapper)
├── graphql.py           # GraphQL query IDs
├── parser.py            # Tweet/User parsing
├── models.py            # Dataclass models
├── formatter.py         # Rich table formatting
├── serialization.py     # YAML/JSON output
├── output.py            # Structured output helpers
├── config.py            # Config loading
├── filter.py            # Tweet ranking/scoring
├── constants.py         # Constants
├── exceptions.py        # Custom exceptions
├── cache.py             # Tweet caching
├── search.py            # Search utilities
└── timeutil.py          # Time utilities
```

## Fork Security Invariants

Changes must keep these true (tests enforce them):

- `auth.py` never reads browser cookie stores and never spawns subprocesses; a rejected cookie is an
  error, not a trigger to look for another one.
- The account Cookie header is only built for `_COOKIE_HOSTS` in `client.py` (HTTPS X endpoints).
- Any new option that takes a local path must be named `*_file`/`*_path`/`*_dir` (or use
  `click.Path`/`click.File`) so `safe.check_arguments` refuses it; see `tests/test_safe.py`.
- Never print credential values in messages, logs or exceptions.
- Dependency changes: edit `pyproject.toml`, run `uv lock`, then `deploy/update-locks.sh`, and review the
  hash diff. Never point docs or scripts at `uv tool install twitter-cli` / `pipx install twitter-cli`
  (that is upstream from PyPI).

## CI

- GitHub Actions: Python 3.10, 3.11, 3.12
- CI validates: ruff check + mypy + pytest
