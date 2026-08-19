# Personality Machine

An open-source platform for building AI companions with persistent relationships: long-term vector memory, configurable behaviors, real-time voice, and a builder dashboard.

Create a companion via API or the web dashboard, deploy it, and every user who talks to it gets a persistent **relationship** — profile, memory, and message history that carry across conversations, text and voice alike.

## Features

- **Companions & relationships** — user–companion pairs with persistent profile, memory, and history
- **Vector memory** — semantic memory on Postgres + pgvector, injected into prompts at the right time
- **Behaviors** — developer-defined automations (webhooks, scheduled/triggered actions) run in Modal sandboxes
- **Context assembly** — dynamic prompt building from memory, profile, knowledge, and conversation history
- **Voice sessions** — real-time WebSocket audio (Deepgram STT, Cartesia/ElevenLabs TTS, OpenAI Realtime)
- **Knowledge base** — file/JSON ingestion with hybrid semantic search
- **Builder dashboard** — Next.js app for creating companions, editing prompts, and debugging conversations

## Repository layout

| Directory | What it is |
|---|---|
| [`server/`](server/) | Python FastAPI backend — the core API (v1 + v2), memory, behaviors, voice |
| [`web/`](web/) | Next.js 15 builder UI & dashboard (Clerk auth) |
| [`sdk/`](sdk/) | TypeScript voice-chat SDK reference implementation + demo app |
| [`client/`](client/) | Expo React Native mobile app |
| [`packages/pip-emotion-machine/`](packages/pip-emotion-machine/) | Python client library (`emotion-machine` on pip) |

## Quickstart

Requires Docker (Postgres with pgvector is provided by compose) and an [OpenRouter](https://openrouter.ai) API key for LLM calls.

```bash
export OPENROUTER_API_KEY=sk-or-...
DISABLE_AUTH_FOR_TESTING=true docker compose up --build
```

This starts Postgres, applies migrations, and runs the API on **http://localhost:8100** (interactive docs at `/docs`).

> ⚠️ **`DISABLE_AUTH_FOR_TESTING=true` disables all authentication** and exposes an unauthenticated `GET /api/auth/dev-token` endpoint. It exists so you can explore locally without setting up Clerk. **Never enable it on anything reachable from the internet.**

Talk to it:

```bash
# health
curl http://localhost:8100/healthz

# get a dev token (only exists in dev mode)
TOKEN=$(curl -s http://localhost:8100/api/auth/dev-token | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')

# create a companion
curl -X POST http://localhost:8100/api/companions \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"name": "Luna", "description": "A supportive companion"}'
```

For a complete worked example (companion + behaviors + knowledge base + a test user), see [`server/examples/cycle_companion_setup.py`](server/examples/cycle_companion_setup.py).

## Configuration

The server is configured entirely via environment variables — see [`server/.env.example`](server/.env.example). The important ones:

| Variable | Purpose |
|---|---|
| `DATABASE_DSN` | Postgres connection string (needs the `vector` extension). Migration scripts read `DATABASE_TRANSACTION_DSN` and fall back to this. |
| `OPENROUTER_API_KEY` / `LLM_BASE_URL` | LLM access (OpenAI-compatible; defaults to OpenRouter) |
| `CLERK_SECRET_KEY`, `CLERK_JWT_KEY`, ... | [Clerk](https://clerk.com) auth for the dashboard & user-facing APIs |
| `DEEPGRAM_API_KEY`, `CARTESIA_API_KEY`, `ELEVEN_API_KEY` | Optional — voice pipelines |
| `MODAL_TOKEN_ID` / `MODAL_TOKEN_SECRET` | Optional — behavior execution and memory ingestion workers |

> **Modal caveat:** text chat works with just a database and an LLM key. **Long-term memory ingestion and conversation summarization run as [Modal](https://modal.com) functions** — without deploying them (`server/app/scripts/modal_deploy.py`) those features are no-ops. This decoupling is on the roadmap.

## Development

```bash
# Server (Python 3.12+, uv)
cd server
uv sync
uv run pytest            # offline test suite (no DB or network needed)
uv run pytest -m live    # integration tests — needs a running server + env vars

# Web
cd web && npm install && npm run dev

# Migrations against your own Postgres
DATABASE_DSN=postgresql://... bash server/app/scripts/migrate.sh
```

API references: [v1](server/API_V1_REFERENCE.md) · [v2](server/API_V2_REFERENCE.md). Observability (Jaeger/OTel): [server/OBSERVABILITY.md](server/OBSERVABILITY.md).

See [CONTRIBUTING.md](CONTRIBUTING.md) for development conventions and [SECURITY.md](SECURITY.md) for reporting vulnerabilities.

## License

[Apache-2.0](LICENSE) © Emotion Machine
