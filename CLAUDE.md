# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Emotion Machine is an AI companion platform with persistent relationships, real-time voice interactions, configurable behaviors, and vector-based memory. Users create companions via a builder UI, deploy them to custom URLs, and debug via a dashboard.

## Monorepo Structure

- **server/** - Python FastAPI backend (core API, companions, memory, voice sessions)
- **web/** - Next.js 15 web app (builder UI, dashboard, Clerk auth)
- **sdk/** - TypeScript voice chat SDK (CompanionClient facade, WebSocket audio streaming)
- **client/** - Expo React Native mobile app

## Common Commands

### Server (Python with uv)

```bash
cd server

# Setup environment
uv sync
# In sandboxed environments:
UV_CACHE_DIR=$(pwd)/.uv-cache uv sync

# Run tests
uv run pytest
uv run pytest tests/test_file.py -vv          # Single file
uv run pytest tests/test_file.py::test_name   # Single test

# Load tests (requires EM_API_KEY, EM_TEST_COMPANION_ID env vars)
cd loadtests
python run_test.py --quick                    # Quick smoke test
python run_test.py --users 100 --duration 5m  # Full load test
locust                                        # Interactive web UI

# Format and lint (also runs via pre-commit)
uv run ruff format .
uv run ruff check --fix .
```

### Web (Next.js)

```bash
cd web
npm install
npm run dev      # Development server
npm run build    # Production build
npm run lint     # ESLint
npm run test     # Vitest
```

### SDK

```bash
cd sdk
npm install
npm run dev      # Vite dev server
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

### Core Concepts

- **Relationships** - User-Companion persistent state (profile, memory, messages)
- **Behaviors** - Automated actions triggered by patterns (webhooks, Modal execution)
- **Memory** - Vector-based semantic memory with pgvector embeddings
- **Context Assembly** - Dynamic prompt building from memory, profile, conversation history
- **Voice Sessions** - WebSocket real-time audio streaming via Daily.co, Deepgram STT, Cartesia TTS

### Web Structure

- `app/(authenticated)/` - Protected routes (companions, dashboard)
- `app/companion/` - Public companion interface
- `components/` - UI components (analytics, auth, dashboard, voice)
- `hooks/` - Custom React hooks
- CodeMirror integration for prompt editing

### SDK Pattern

`CompanionClient` is the main facade abstracting WebSocket connections, audio I/O, and authentication. Event-driven API with typed events (`companion:state_change`, `error`).

## Tech Stack

**Server**: FastAPI, asyncpg, pgvector, OpenAI SDK, pipecat-ai (Deepgram), Cartesia TTS, Daily.co, Modal (serverless)

**Web**: Next.js 15, React 19, Clerk, Radix UI, Tailwind CSS v4, React Query, CodeMirror

**SDK**: Vite, React 19, TypeScript, Tailwind CSS, WebSocket audio streaming

**Client**: Expo, React Native, WebRTC

## Code Style

**Python** (Ruff configured):

- Python 3.12+, 100 char line length
- Double quotes, space indentation
- B008 ignored for FastAPI `Depends`

**TypeScript/JavaScript**:

- ESLint with max-warnings=0 on web/
- Pre-commit runs on `web/src/` files

## Shell Tool Preferences

When using shell tools, prefer:

- `fd` for finding files
- `rg` (ripgrep) for text search
- `ast-grep` for code structure (TS/TSX)
- `jq` for JSON, `yq` for YAML/XML

&nbsp;