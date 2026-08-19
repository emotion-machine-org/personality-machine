# AGENTS.md - Personality Machine Server

> Developer reference for AI assistants and engineers working on this codebase.

## Project Overview

**Personality Machine** (Emotion Machine) is a FastAPI backend for building AI companions with:
- Persistent relationships (per-user profile, memory, message history)
- Memory persistence (Memory v2 scratchpad + pgvector search)
- Knowledge management (document ingestion + retrieval via OpenAI vector stores)
- Tool integration (OpenAPI spec indexing + execution)
- Behaviors (developer-defined automations executed in Modal sandboxes)
- Multi-modal conversations (voice WebSocket + text REST/WS)

**Stack**: Python 3.12+, FastAPI, AsyncPG, PostgreSQL + pgvector, Modal (serverless), Pipecat (voice)

---

## Project Structure

```
server/
├── app/
│   ├── main.py                    # FastAPI entry point, lifespan, CORS, router registration
│   ├── auth.py                    # Clerk JWT + project API key authentication
│   ├── db.py                      # AsyncPG connection pool (JSONB codecs — see Key Patterns)
│   ├── logging.py, tracing.py     # Logging + OpenTelemetry setup
│   │
│   ├── models/                    # Pydantic data models (user, companion, project, state,
│   │   └── v2/                    #   memory, media, job, share) + v2 API models
│   ├── schemas/                   # Request/response schemas (knowledge, ...)
│   │
│   ├── repositories/              # Data access layer: companion, conversation, project,
│   │                              #   user, memory, memory_v2_repository, relationship_repository,
│   │                              #   behavior_repository, session_repository, summary_repository,
│   │                              #   state_repository, tool_*, job_repository, share, voice, ...
│   │
│   ├── routers/
│   │   ├── api.py                 # Primary /api routes (Clerk auth, dashboard)
│   │   ├── client_api.py          # /v1 public SDK routes (project API key auth)
│   │   ├── v2/                    # /v2 API: relationships, messages, sessions, behaviors,
│   │   │                          #   memory, summaries, inbox, websockets
│   │   ├── voice/                 # Voice implementation: v1/v2 sessions, pipeline, providers,
│   │   │                          #   twilio, fast_brain, openclaw (optional), workspace
│   │   ├── sessions.py            # Deprecated shim → routers/voice/v1.py
│   │   ├── conversations.py       # Text conversation endpoints
│   │   ├── tools.py               # Tool spec management (/api/tools)
│   │   ├── analytics.py           # Analytics + labeling jobs (/api/analytics)
│   │   ├── memories.py            # Memory management (/api)
│   │   ├── companion_shares.py, public_shares.py, public_companions.py
│   │   ├── memory_v2_testing.py, context_engine_testing.py, dialogmachine.py  # debug UIs
│   │   └── oauth_cli.py           # OAuth PKCE flow for CLI clients
│   │
│   ├── services/                  # Business logic: llm, llm_resolver, openai_clients,
│   │                              #   message_processor, memory_service, memory_v2_service,
│   │                              #   knowledge_service, knowledge_assets, openai_vector_store,
│   │                              #   context_assembly, cache_manager, api_keys, encryption,
│   │                              #   share_tokens, voice_presets, media_assets, intro_context, ...
│   │
│   ├── context/                   # Context engine: orchestrator, layers, behavior_runtime,
│   │                              #   behavior_context, intent_classifier, memory_runtime,
│   │                              #   memory_v2_layer, knowledge_runtime, tools_runtime,
│   │                              #   core_prompt_layer, post_turn_executor,
│   │                              #   modal_behavior_executor, chat_helpers, hydration/
│   │
│   ├── database/                  # SQLAlchemy engine/pool, middleware, tracking
│   │
│   ├── modals/                    # Modal serverless functions
│   │   ├── workers/               # tools, memory_ingest, memory_v2_ingest,
│   │   │                          #   summarize_conversation, redact_conversation,
│   │   │                          #   label_conversations
│   │   └── services/db_gateway.py # Database gateway ("em-db") for workers
│   │
│   ├── prompts/                   # Prompt templates
│   ├── utils/                     # Shared helpers (profile normalization, ...)
│   ├── data/knowledge/            # Bundled knowledge fixtures
│   │
│   ├── supabase/migrations/       # 63 raw SQL migrations (applied in filename order)
│   └── scripts/                   # migrate.sh / migrate_latest.sh / migrate_range.sh,
│                                  #   modal_deploy.py, create_project_api_key.py,
│                                  #   seed_onboarding_companion.py, clone_companion.py,
│                                  #   export_v1_openapi.py, backfill_*.py
│
├── tests/                         # Pytest suite (see Running Tests)
├── notebooks/                     # Jupyter notebooks
├── examples/                      # cycle_companion_setup.py + sample data
├── loadtests/                     # Locust load tests
├── pyproject.toml                 # Dependencies (uv) + ruff + pytest config
├── Dockerfile
└── .env                           # Environment variables (never commit)
```

---

## API Routes

Canonical, exhaustive references: `API_V1_REFERENCE.md` and `API_V2_REFERENCE.md`.
Quick orientation:

### Primary API (`/api`) - Clerk JWT Auth

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/me` | Current user profile |
| POST | `/api/me/complete-onboarding` | Mark onboarding done |
| GET/POST | `/api/companions` | List / create companions |
| GET/PUT/DELETE | `/api/companions/{id}` | Get / update / delete companion |
| GET | `/api/companions/{id}/versions` | List versions (`/{version_id}` for one) |
| POST/GET | `/api/projects/default/keys` | Create / list API keys |
| DELETE | `/api/projects/default/keys/{key_id}` | Revoke API key |
| POST/GET | `/api/companions/{id}/knowledge-assets` | Upload / list knowledge files |
| GET | `/api/auth/dev-token` | Dev token (only with `DISABLE_AUTH_FOR_TESTING=true`) |

### Client SDK (`/v1`) - Project API Key Auth

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET/POST | `/v1/companions` | List / create companions |
| GET/PATCH/DELETE | `/v1/companions/{id}` | Get / update / delete companion |
| POST | `/v1/companions/{id}/chat` | Send message (`/chat/stream` for SSE) |
| POST | `/v1/sessions` | Create voice session (`PATCH /v1/sessions/{id}` to update) |
| GET/PUT | `/v1/companions/{id}/profile-schema` | Get / update profile schema |
| POST | `/v1/companions/{id}/knowledge` | Ingest knowledge (`/knowledge/search` to query) |
| GET | `/v1/knowledge-jobs/{job_id}` | Poll ingestion job |
| POST | `/v1/companions/{id}/core-memories` | Seed core memories |
| POST/GET | `/v1/companions/{id}/tools` | Index / list tool specs (`/{spec_id}`: GET/PATCH/DELETE) |
| POST/GET | `/v1/secrets` | Create / list secrets (`DELETE /v1/secrets/{name}`) |
| GET | `/v1/voice-mappings` | Voice name catalog (unauthenticated) |

### v2 API (`/v2`) - Project API Key Auth

Relationships, messages (REST + SSE + WebSocket), sessions, behaviors (+ relationship-scoped
overrides + `/definition` for source code), memory, summaries, inbox, voice. See
`API_V2_REFERENCE.md` §4 — it is accurate and complete.

### Voice Sessions - WebSocket

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/sessions/` | Create v1 session (returns WS URL + token) |
| WS | `/sessions/ws/{session_id}` | Audio stream (binary PCM) |
| POST | `/v2/.../voice/token` + WS `/v2/.../voice/connect` | v2 voice (companion- or relationship-scoped) |

### Analytics (`/api/analytics`) - Clerk Auth

Conversations listing/detail/messages/system-prompt, per-user summaries, labeling
(`/companions/{id}/label-conversations`, `/labels`), jobs (`/jobs/{id}`, `/jobs/{id}/events`),
plus `privacy/*` and `share/*` families. See `app/routers/analytics.py`.

### Memory (`/api`) - Clerk Auth

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET/POST | `/api/companions/{companion_id}/memories` | List / add memories (`/search` to query) |
| GET/DELETE | `/api/memories/{memory_id}` | Get / delete memory (`/api/memories/stats` for stats) |

---

## Database Schema (Key Tables)

### `users`
- `id` (UUID, PK), `clerk_user_id`, `email`, `username`, `display_name`, `avatar_url`
- `onboarding_completed`, `created_at`, `updated_at`

### `projects`
- `id` (UUID, PK), `owner_id` (FK users), `name`, `slug` (unique), `is_default`

### `companions`
- `id` (UUID, PK), `owner_id`, `project_id`, `name`, `description`, `metadata` (JSONB)

### `companion_versions`
- `id` (UUID, PK), `companion_id`, `version_number`, `config` (JSONB — system_prompt,
  inference, memory, voice, layers, classifier, ...), `status` (DRAFT/DEPLOYED/ARCHIVED)
- `system_prompt`, `voice_id`, `memory_enabled` are legacy columns kept for backfill;
  new writes go through `config` (migration `0053_rename_system_prompt_to_config.sql`)

### `relationships`
- `id` (UUID, PK), `companion_id`, `external_user_id`, `profile` (JSONB), `config` (JSONB),
  `version`, timestamps — the core v2 user↔companion state

### `conversations` / `messages`
- Conversations: `id`, `companion_id`, `external_user_id`, `share_id`, counters
- Messages: `id`, `relationship_id`/`conversation_id`, `role`, `content`, `seq`,
  `input_modality` (text/voice/image), `metadata` (JSONB), `is_proactive`, `created_at`

### `memories` (pgvector) / `memory_v2_entries`
- `memories`: `companion_id`, `content`, `embedding` (vector, HNSW), `importance`, `is_core`
- `memory_v2_entries`: `relationship_id`, `content`, `type` — flat scratchpad injected into prompts

### `project_api_keys`
- `id`, `project_id`, `prefix` (unique, `emk_<stage>_<tag>`), `secret_hash`, `salt`,
  `status`, `scopes`, `expires_at`

### `behaviors` / `companion_behavior_links`
- `behaviors`: `id`, `project_id`, `key`, `name`, `description`, `source_code`, `version`,
  `dependencies`, `timeout_seconds`, `block_network` (renamed from `actions`, migration 0049/0050)
- `companion_behavior_links`: `companion_id`, `behavior_id`, `relationship_id` (NULL = default),
  `triggers` (JSONB), `priority`, `enabled`, `webhook_url`, `webhook_secret`, `params`

### `tool_specs` / `tool_operations` / `project_secrets`
- OpenAPI spec storage, parsed operations, encrypted secrets

### `jobs`
- Unified async job queue: `job_type`, `status`, `run_at`, `attempts`, `companion_id`,
  `behavior_key`, `params`, `result`, counters

---

## Authentication

### Clerk JWT (Dashboard/Frontend)
```python
# In auth.py
def verify_clerk_token(request) -> Dict  # Returns token payload
def get_current_user(request, conn) -> User  # FastAPI dependency
def get_current_user_optional() -> Optional[User]  # Non-failing
```

### Project API Keys (SDK/External)
```python
# Format: emk_<stage>_<12charTag>.<secret>   e.g. emk_prod_a1b2c3d4e5f6.…
# The whole "emk_<stage>_<tag>" part is the stored `prefix`; stage comes from
# API_KEY_ENV (falls back to ENV, default "dev"). See app/services/api_keys.py.
def get_project_api_subject(request, conn) -> ProjectApiKeySubject
```

### Development Bypass
Set `DISABLE_AUTH_FOR_TESTING=true` to skip authentication. This also exposes
`GET /api/auth/dev-token` and enables the `mock-dev-token` bearer. Local use only.

---

## Key Services

### Context Engine (`context/orchestrator.py`)
Builds context plans by executing parallel layers:
1. **Core Prompt Layer**: System prompt + core memories
2. **Memory Layer**: Retrieved memories (gated by classifier/heuristic)
3. **Knowledge Layer**: Vector search results
4. **Tools Layer**: Available tool definitions
5. **Behaviors Layer**: Triggered behaviors (renamed from "actions")

```python
async def build_context_plan(
    *, conn, companion_config, relationship_id, session_id,
    include_memory=..., include_knowledge=..., include_tools=..., include_behaviors=..., ...
) -> ContextPlan
```

### Memory System
- **Ingestion**: `memory_service.py` → Modal worker (`em-memory-v2`)
- **Retrieval**: `context/memory_runtime.py` (gated vector search), `memory_v2_layer.py`
- **Storage**: pgvector with HNSW index; Memory v2 rows in `memory_v2_entries`

```python
async def should_retrieve_memories(message, history) -> bool
async def retrieve_regular_memories(companion_id, query, top_k) -> List[Memory]
```

### Tool Integration
- **Indexing**: Parse OpenAPI spec, store operations
- **Execution**: Build HTTP request, inject secrets, call endpoint

```python
# Modal methods in modals/workers/tools.py ("em-tools")
index_tools, retrieve_best_tool, choose_and_parametrize_tool, use_api_tool
```

### LLM Resolution
```python
# In llm_resolver.py
resolve_llm_client(provider_string, default_model=...) -> tuple[client, model, used_default]

# Client keys: openai, openrouter, vllm — alias registry in _PROVIDER_REGISTRY
```

---

## Modal Workers

Deployed with `uv run python app/scripts/modal_deploy.py`:

- **`em-tools`** — tool indexing/execution (`index_tools`, `retrieve_best_tool`,
  `choose_and_parametrize_tool`, `use_api_tool`)
- **`em-db`** — database gateway for workers (`start_labeling_job`, `start_summary_job`,
  `start_privacy_job`, memory batch writes, message reads)
- **`em-memory-v2`** — memory ingestion + relationship summarization
- Label/summarize/redact conversation workers

Without deployed workers, text chat works but memory ingestion, summaries, and
behavior execution are no-ops.

---

## Environment Variables

See `.env.example` for the full annotated list. Highlights:

### Database
```
DATABASE_DSN=postgresql://...          # what the app reads
DATABASE_TRANSACTION_DSN=...           # read by migrate scripts; falls back to DATABASE_DSN
```

### Authentication
```
CLERK_SECRET_KEY=sk_test_...
CLERK_JWT_KEY=-----BEGIN PUBLIC KEY-----...
CLERK_AUTHORIZED_PARTIES=http://localhost:3000,...
DISABLE_AUTH_FOR_TESTING=false
SEED_ENDPOINT_ENABLED=false
```

### LLM Providers
```
OPENAI_API_KEY=sk-proj-...             # OpenAI models, embeddings, vector stores
OPENROUTER_API_KEY=sk-or-v1-...        # Claude/Gemini/... aliases
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1   # optional override
```

### Voice Services
```
ELEVEN_API_KEY=...
CARTESIA_API_KEY=...
DEEPGRAM_API_KEY=...
```

### Memory / Modal / S3 / Security
```
MEMORY_RETRIEVAL_ENABLED=true
MEMORY_TOP_K_DEFAULT=12
MEMORY_RETRIEVAL_TIMEOUT_MS=2500
MODAL_ENVIRONMENT=... MODAL_TOKEN_ID=... MODAL_TOKEN_SECRET=...
AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=... KNOWLEDGE_S3_BUCKET=your-bucket
ENCRYPTION_KEY=...                      # AES-256 key for project secrets
INTERNAL_API_KEY=... WS_TOKEN_SECRET=...
```

Observability (`OTEL_*`): see `OBSERVABILITY.md`.

---

## Running Tests

```bash
# Offline suite (default — no DB, network, or credentials needed)
uv run pytest

# Single file / test
uv run pytest tests/test_share_tokens.py -vv
uv run pytest tests/test_file.py::test_name

# Integration tiers (marker assignment in tests/conftest.py)
uv run pytest -m live    # needs a running server + EM_BASE_URL/TEST_EM_API_KEY + seeded DB
uv run pytest -m modal   # needs deployed Modal workers + credentials
```

`pyproject.toml` sets `addopts = "-ra -m 'not live and not modal'"`, so the default
run excludes both integration tiers. There is no coverage plugin installed.

---

## Development Commands

```bash
# Start server
uv run uvicorn app.main:app --reload --port 8100

# Run migrations (raw SQL, applied in filename order; no state table)
DATABASE_DSN=postgresql://... bash app/scripts/migrate.sh
bash app/scripts/migrate_latest.sh    # only the newest file
bash app/scripts/migrate_range.sh     # a numbered range

# Deploy Modal workers
uv run python app/scripts/modal_deploy.py

# Generate API key
EM_PROJECT_ID=... EM_PROJECT_OWNER_ID=... uv run python app/scripts/create_project_api_key.py

# Export OpenAPI schema
uv run python app/scripts/export_v1_openapi.py
```

---

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                      FastAPI Application                     │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │ Routers: /api, /v1, /v2, /sessions, /conversations, ... │ │
│  └─────────────────────────────────────────────────────────┘ │
│                              ↓                               │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │ Auth: Clerk JWT | Project API Key                       │ │
│  └─────────────────────────────────────────────────────────┘ │
│                              ↓                               │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │ Services: LLM, Memory, Knowledge, Context, Behaviors    │ │
│  └─────────────────────────────────────────────────────────┘ │
│                              ↓                               │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │ Repositories: Companion, Relationship, Memory, State    │ │
│  └─────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
                               ↓
┌──────────────────────────────────────────────────────────────┐
│  PostgreSQL (pgvector)         │  External Services          │
│  - Core tables                 │  - OpenAI/OpenRouter (LLM)  │
│  - pgvector (embeddings)       │  - ElevenLabs/Cartesia (TTS)│
│  - HNSW index (memory)         │  - AWS S3 (storage)         │
│                                │  - Modal (serverless)       │
└──────────────────────────────────────────────────────────────┘
```

---

## Troubleshooting

### Common Issues

**Auth failures**: Check `CLERK_JWT_KEY` and `CLERK_AUTHORIZED_PARTIES`

**Memory not retrieving**: Verify `MEMORY_RETRIEVAL_ENABLED=true` and check pgvector index

**Memory never ingesting**: Modal workers not deployed — run `modal_deploy.py`

**Tool execution errors**: Check `project_secrets` encryption and `secrets_config` mapping

**Modal worker issues**: Verify `MODAL_TOKEN_ID`/`MODAL_TOKEN_SECRET` and redeploy

### Logging
```python
import logging
logger = logging.getLogger(__name__)
logger.info("message %s", value)
```

---

## Key Patterns

### AsyncPG and JSON/JSONB Handling

**IMPORTANT**: AsyncPG is configured with automatic JSON codecs in `app/db.py`. This means:

1. **Pass dicts directly** - Do NOT use `json.dumps()` when inserting JSONB data
2. **Receive dicts directly** - Do NOT use `json.loads()` when reading JSONB data
3. **Avoid `::jsonb` casts** - The codec handles type conversion automatically

```python
# CORRECT - pass dict directly
await conn.execute(
    "INSERT INTO table (data) VALUES ($1)",
    {"key": "value"}  # Pass dict, asyncpg handles serialization
)

# WRONG - don't json.dumps and cast
await conn.execute(
    "INSERT INTO table (data) VALUES ($1::jsonb)",
    json.dumps({"key": "value"})  # This may cause string/dict issues on read
)

# CORRECT - read dict directly
row = await conn.fetchrow("SELECT data FROM table WHERE id = $1", id)
data = row["data"]  # Already a dict, no json.loads() needed

# WRONG - don't json.loads
data = json.loads(row["data"])  # Unnecessary, may fail if already dict
```

The codec is set up via `init=_setup_jsonb_codec` in the connection pool.

### Adding a New Endpoint
1. Create/modify router in `app/routers/`
2. Add Pydantic models in `app/models/` or `app/schemas/`
3. Implement repository methods in `app/repositories/`
4. Add service logic in `app/services/`
5. Register router in `app/main.py`

### Adding a New Context Layer
1. Create layer runtime in `app/context/`
2. Register in `orchestrator.py`
3. Update intent classifier if needed

### Adding a Modal Worker
1. Create worker class in `app/modals/workers/`
2. Define Modal app with `@app.cls`
3. Deploy with `uv run python app/scripts/modal_deploy.py`
