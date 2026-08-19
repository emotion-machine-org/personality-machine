# AGENTS.md

This file provides guidance to coding agents working in this repository.

## Project Overview

Personality Machine (Emotion Machine) is an AI companion platform with persistent relationships, real-time voice interactions, configurable behaviors, and vector-based memory. Users create companions via a builder UI or the API, deploy them, and debug via a dashboard.

## Monorepo Structure

- **server/** - Python FastAPI backend (core API v1/v2, companions, memory, behaviors, voice sessions)
- **web/** - Next.js 15 web app (builder UI, dashboard, Clerk auth)
- **sdk/** - TypeScript voice chat SDK reference implementation (CompanionClient facade, WebSocket audio streaming)
- **client/** - Expo React Native mobile app
- **packages/pip-emotion-machine/** - Python client library (`emotion-machine` on pip)

## Common Commands

### Full local stack

```bash
docker compose up --build   # Postgres (pgvector) + migrations + API on :8100
```

### Server (Python with uv)

```bash
cd server

# Setup environment
uv sync
# In sandboxed environments:
UV_CACHE_DIR=$(pwd)/.uv-cache uv sync

# Run tests — offline suite by default (no DB/network needed)
uv run pytest
uv run pytest tests/test_file.py -vv          # Single file
uv run pytest tests/test_file.py::test_name   # Single test
uv run pytest -m live                         # Integration tests (running server + TEST_EM_API_KEY)
uv run pytest -m modal                        # Tests needing deployed Modal workers
# Marker assignment lives in tests/conftest.py

# Migrations (raw SQL, applied in filename order; no state table)
DATABASE_DSN=postgresql://... bash app/scripts/migrate.sh

# Load tests (requires EM_API_KEY, EM_TEST_COMPANION_ID env vars)
cd loadtests
python run_test.py --quick                    # Quick smoke test
python run_test.py --users 100 --duration 5m  # Full load test
locust                                        # Interactive web UI

# Format and lint (repo-wide clean; also runs via pre-commit)
uv run ruff format .
uv run ruff check --fix .
```

### Web (Next.js)

```bash
cd web
npm install
npm run dev      # Development server
npm run build    # Production build (a dummy Clerk publishable key suffices)
npm run lint     # ESLint
npm run test     # Vitest
```

### SDK

```bash
cd sdk
npm install
npm run dev      # Vite dev server (needs VITE_EM_API_KEY, VITE_EM_COMPANION_ID in .env)
npm run build    # TypeScript compile + Vite build
npm run lint
```

### Client (Expo)

```bash
cd client
npm install
npm start        # Expo dev server
npm run ios      # iOS simulator
npm run android  # Android emulator
npm run web      # Web target
```

### Pre-commit Hooks

```bash
pre-commit install           # Setup hooks
pre-commit run --all-files   # Run manually
```

## Architecture

### Server Layers

- **Routers** (`app/routers/`) - HTTP endpoints, V1 and V2 APIs
- **Services** (`app/services/`) - Business logic (LLM, memory, knowledge, context assembly)
- **Repositories** (`app/repositories/`) - Data access with asyncpg
- **Context** (`app/context/`) - Behavior runtime and execution context
- **Schemas** (`app/schemas/`) - Pydantic request/response models
- **Modal workers** (`app/modals/`) - Behavior execution, memory ingestion, summaries. Deployed via `app/scripts/modal_deploy.py`; without them, chat works but memory ingestion and behaviors are no-ops.

### Core Concepts

- **Relationships** - User-Companion persistent state (profile, memory, messages)
- **Behaviors** - Automated actions triggered by patterns (webhooks, Modal execution)
- **Memory** - Vector-based semantic memory with pgvector embeddings (Memory v2: flat scratchpad entries injected into the system prompt)
- **Context Assembly** - Dynamic prompt building from memory, profile, knowledge, conversation history ("layered" mode)
- **Voice Sessions** - WebSocket real-time audio streaming via Daily.co, Deepgram STT, Cartesia/ElevenLabs TTS

### Web Structure

- `app/(authenticated)/` - Protected routes (dashboard root, api-keys, relationships, share)
- `app/companion/` - Public companion interface
- `components/` - UI components (auth, dashboard, memory-explorer, voice, ...)
- `hooks/` - Custom React hooks
- CodeMirror integration for prompt editing

### SDK Pattern

`CompanionClient` is the main facade abstracting WebSocket connections, audio I/O, and authentication. Event-driven API with typed events (`companion:state_change`, `error`).

## Tech Stack

**Server**: FastAPI, asyncpg, pgvector, OpenAI SDK (OpenRouter-compatible), pipecat-ai (Deepgram), Cartesia TTS, Daily.co, Modal (serverless)

**Web**: Next.js 15, React 19, Clerk, Radix UI, Tailwind CSS v4, React Query, CodeMirror

**SDK**: Vite, React 19, TypeScript, Tailwind CSS, WebSocket audio streaming

**Client**: Expo, React Native, WebRTC

## Code Style

**Python** (Ruff configured):

- Python 3.12+, 100 char line length
- Double quotes, space indentation
- B008 ignored for FastAPI `Depends`

**TypeScript/JavaScript**:

- ESLint on web/ (`npm run lint`); pre-commit enforces max-warnings=0 on `web/src/`
- Pre-commit runs on `web/src/` files

## Gotchas

- There is already a JSON encoder/decoder at the asyncpg level in `server/app/db.py`. Do not add your own JSON encoding when persisting objects to the DB.
- `DATABASE_DSN` is what the app reads; the migrate scripts read `DATABASE_TRANSACTION_DSN` and fall back to `DATABASE_DSN`.
- `DISABLE_AUTH_FOR_TESTING=true` disables all auth and exposes `GET /api/auth/dev-token`. Local use only.
- `server/API_V2_REFERENCE.md` is canonical; `web/public/API_V2_REFERENCE.md` is a served copy — keep in sync.

## Shell Tool Preferences

When using shell tools, prefer:

- `fd` for finding files
- `rg` (ripgrep) for text search
- `ast-grep` for code structure (TS/TSX)
- `jq` for JSON, `yq` for YAML/XML
