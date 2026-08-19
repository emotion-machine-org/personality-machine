# Emotion Machine Server – Testing Guide

This backend uses [uv](https://docs.astral.sh/uv/) to manage Python runtimes, dependencies, and test execution. The commands below assume you run them from the `server/` directory.

## Prerequisites

- uv installed and available on your `PATH` (see https://docs.astral.sh/uv/ for installer snippets).
- macOS/Linux: no additional setup is required. On Windows, prefer PowerShell when running the examples.

## Syncing the virtual environment

```bash
# Create or refresh .venv using the lockfile
uv sync
```

If you are in a sandboxed or restricted environment (e.g., CI) that blocks the default cache path, set a writable cache directory before syncing:

```bash
UV_CACHE_DIR=$(pwd)/.uv-cache uv sync
```

This keeps all uv artifacts inside the repo so Git/Sandbox policies don’t interfere with `~/.cache` or `~/.local/share`.

## Running the test suite

By default `uv run pytest` runs the **offline** suite only — integration tests are
excluded via markers (`-m 'not live and not modal'` in `pyproject.toml`). Run them
explicitly with `uv run pytest -m live` (needs a running server) or `uv run pytest -m modal`
(needs deployed Modal workers). Marker assignment lives in `tests/conftest.py`.

Use `uv run` so tests execute inside the managed environment without needing to activate `.venv` manually:

```bash
uv run pytest
```

For deterministic local runs (especially in macOS sandboxed shells), pin the cache directory:

```bash
UV_CACHE_DIR=$(pwd)/.uv-cache uv run pytest -q
```

`uv run` will bootstrap the interpreter on the first invocation. Subsequent runs reuse the environment unless you remove `.venv/`.

### Running a single test file

```bash
uv run pytest tests/test_share_tokens.py -vv
```

### Disabling third-party pytest plugins

If you encounter plugin-related issues, use the standard `pytest` flag:

```bash
UV_CACHE_DIR=$(pwd)/.uv-cache PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -q
```

## Cleaning / rebuilding

- Remove `.venv/` to force a clean environment on the next `uv sync`.
- Clear the uv cache with `uv cache clean` (add `--all` to drop downloaded wheels).

Keeping `UV_CACHE_DIR` inside the repo makes these clean-up steps self-contained and avoids permissions problems with shared user caches.
