# Contributing

Thanks for your interest in improving Personality Machine!

## Development setup

- **Server**: Python 3.12+ with [uv](https://docs.astral.sh/uv/). `cd server && uv sync`.
- **Web**: Node 20+. `cd web && npm install`.
- **Hooks**: `pre-commit install` (runs ruff on `server/` and eslint on `web/src/`).

A full local stack (Postgres + migrations + API) is one command: `docker compose up --build`.

## Tests

Server tests come in tiers:

| Command | What runs |
|---|---|
| `uv run pytest` | Offline unit tests — no DB, network, or credentials needed. **Must pass on every PR.** |
| `uv run pytest -m live` | Integration tests against a running server (`EM_BASE_URL`, `TEST_EM_API_KEY`, seeded DB) |
| `uv run pytest -m modal` | Tests requiring deployed Modal workers |

The tier assignment lives in `server/tests/conftest.py`. Web tests: `cd web && npm run test` (Vitest).

## Code style

- Python: Ruff (format + lint), 100-char lines, double quotes — enforced by pre-commit.
- TypeScript: ESLint with `--max-warnings=0` on `web/`.

## Docs

`server/API_V2_REFERENCE.md` is the canonical v2 API reference. The copy served by the dashboard at `web/public/API_V2_REFERENCE.md` must be kept in sync (it's a plain copy).

## Licensing

By contributing, you agree that your contributions are licensed under the [MIT License](LICENSE) (inbound = outbound). No CLA required.
