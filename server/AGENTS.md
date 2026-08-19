# AGENTS.md - Emotion Machine Server

> Developer reference for AI assistants and engineers working on this codebase.

## Project Overview

**Emotion Machine** is a FastAPI backend for building AI companions with:
- Memory persistence (vector search via pgvector)
- Knowledge management (document ingestion + retrieval)
- Tool integration (OpenAPI spec indexing + execution)
- Multi-modal conversations (voice WebSocket + text REST)
- State management (per-user + per-conversation)

**Stack**: Python 3.12+, FastAPI, AsyncPG, Supabase (PostgreSQL), Modal (serverless), Pipecat (voice)

---

## Project Structure

```
server/
├── app/
│   ├── main.py                    # FastAPI entry point, lifespan, CORS
│   ├── auth.py                    # Clerk JWT + API key authentication
│   ├── db.py                      # AsyncPG connection pool
│   ├── constants.py               # Application constants
│   │
│   ├── models/                    # Pydantic data models
│   │   ├── user.py                # User model
│   │   ├── companion.py           # Companion config, voices, memory
│   │   ├── project.py             # Projects, API keys, knowledge assets
│   │   ├── state.py               # User/conversation state
│   │   ├── memory.py              # Memory data model
│   │   ├── media.py               # Media/image models
│   │   └── job.py                 # Background job model
│   │
│   ├── repositories/              # Data access layer (DAL)
│   │   ├── companion.py           # Companion CRUD
│   │   ├── conversation.py        # Conversation & message persistence
│   │   ├── project.py             # Project & knowledge operations
│   │   ├── state_repository.py    # State persistence
│   │   ├── memory.py              # Memory retrieval & search (pgvector)
│   │   ├── tool_index_repository.py  # Tool spec indexing
│   │   ├── tool_repository.py     # Tool runtime operations
│   │   ├── job_repository.py      # Background job tracking
│   │   ├── action_repository.py   # Action persistence
│   │   ├── user.py                # User CRUD
│   │   ├── share.py               # Share token operations
│   │   └── project_secrets.py     # Encrypted project secrets
│   │
│   ├── routers/                   # API route handlers
│   │   ├── api.py                 # Primary /api routes (authenticated)
│   │   ├── client_api.py          # /v1 public SDK routes (API key auth)
│   │   ├── sessions.py            # WebSocket voice sessions
│   │   ├── conversations.py       # Text conversation endpoints
│   │   ├── tools.py               # Tool spec management
│   │   ├── analytics.py           # Analytics endpoints
│   │   ├── memories.py            # Memory management
│   │   ├── action_testing.py      # Action testing endpoints
│   │   ├── context_engine_testing.py  # Context engine debug UI
│   │   ├── companion_shares.py    # Share link management
│   │   ├── public_companions.py   # Public companion listing
│   │   └── public_shares.py       # Public share access
│   │
│   ├── services/                  # Business logic layer
│   │   ├── llm.py                 # LLM response generation
│   │   ├── llm_resolver.py        # Provider resolution (OpenAI/OpenRouter)
│   │   ├── openai_clients.py      # OpenAI async client factory
│   │   ├── context_assembly.py    # System prompt + core memory composition
│   │   ├── context_builder.py     # Message history assembly
│   │   ├── knowledge_service.py   # Knowledge asset ingestion
│   │   ├── knowledge_assets.py    # S3 asset storage
│   │   ├── openai_vector_store.py # OpenAI vector store (embeddings)
│   │   ├── memory_service.py      # Memory ingestion & retrieval
│   │   ├── memory_runtime.py      # Memory retrieval heuristics
│   │   ├── memory_prompts.py      # Memory system prompts
│   │   ├── modal_gateway.py       # Modal serverless interface
│   │   ├── cache_manager.py       # In-memory caching
│   │   ├── api_keys.py            # API key generation/validation
│   │   ├── encryption.py          # AES-256-GCM encryption
│   │   ├── share_tokens.py        # Share token generation
│   │   ├── voice_presets.py       # Voice configuration templates
│   │   ├── media_assets.py        # Image upload & presigned URLs
│   │   └── image_description.py   # Image description extraction
│   │
│   ├── context/                   # Context engine (prompt orchestration)
│   │   ├── orchestrator.py        # Main context orchestration
│   │   ├── schemas.py             # Context schema definitions
│   │   ├── resolved_config.py     # Runtime config resolution
│   │   ├── action_runtime.py      # Action execution runtime
│   │   ├── action_registry.py     # Action registration/discovery
│   │   ├── action_sdk.py          # Action SDK for developers
│   │   ├── action_context.py      # Action context builder
│   │   ├── intent_classifier.py   # Intent classification for layers
│   │   ├── memory_runtime.py      # Memory layer runtime
│   │   ├── knowledge_runtime.py   # Knowledge layer runtime
│   │   ├── tools_runtime.py       # Tools layer runtime
│   │   ├── layers.py              # Layer abstraction
│   │   ├── context_hydrator.py    # State hydration
│   │   ├── post_turn_executor.py  # Post-turn effect execution
│   │   ├── modal_action_executor.py  # Modal action execution
│   │   ├── core_prompt_layer.py   # Core system prompt management
│   │   ├── dependency_detector.py # Dependency resolution
│   │   ├── chat_helpers.py        # Chat utility functions
│   │   └── deploy_modal.py        # Modal deployment utilities
│   │
│   ├── modals/                    # Modal serverless functions
│   │   ├── workers/
│   │   │   ├── tools.py           # Tool execution & indexing
│   │   │   ├── memory_ingest.py   # Memory ingestion worker
│   │   │   ├── summarize_conversation.py
│   │   │   ├── redact_conversation.py
│   │   │   └── label_conversations.py
│   │   └── services/
│   │       └── db_gateway.py      # Database gateway for Modal
│   │
│   ├── schemas/                   # Request/response schemas
│   │   └── knowledge.py           # Knowledge ingestion schemas
│   │
│   ├── supabase/
│   │   └── migrations/            # 44+ SQL migrations
│   │
│   └── scripts/                   # Utility scripts
│       ├── create_project_api_key.py
│       ├── modal_deploy.py
│       └── export_v1_openapi.py
│
├── tests/                         # Pytest test suite
├── notebooks/                     # Jupyter notebooks
├── requirements.txt
├── pyproject.toml
├── Dockerfile
└── .env                           # Environment variables
```

---

## API Routes

### Primary API (`/api`) - Clerk JWT Auth

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/me` | Current user profile |
| POST | `/api/me/complete-onboarding` | Mark onboarding done |
| GET | `/api/companions` | List companions (paginated) |
| POST | `/api/companions` | Create companion |
| GET | `/api/companions/{id}` | Get companion detail |
| PUT | `/api/companions/{id}` | Update companion |
| DELETE | `/api/companions/{id}` | Delete companion |
| POST | `/api/companions/{id}/versions` | Create version |
| GET | `/api/companions/{id}/versions` | List versions |
| POST | `/api/projects/{project_id}/api-keys` | Create API key |
| GET | `/api/projects/{project_id}/api-keys` | List API keys |

### Client SDK (`/v1`) - Project API Key Auth

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/v1/companions` | List companions |
| POST | `/v1/companions` | Create companion |
| GET | `/v1/companions/{id}` | Get companion |
| PUT | `/v1/companions/{id}` | Update companion |
| DELETE | `/v1/companions/{id}` | Delete companion |
| POST | `/v1/companions/{id}/chat` | Send message (streaming/direct) |
| POST | `/v1/companions/{id}/sessions` | Create voice session |
| GET | `/v1/companions/{id}/profile-schema` | Get profile schema |
| PUT | `/v1/companions/{id}/profile-schema` | Update profile schema |
| POST | `/v1/companions/{id}/knowledge` | Ingest knowledge |
| GET | `/v1/companions/{id}/knowledge` | List knowledge assets |
| DELETE | `/v1/companions/{id}/knowledge/{asset_id}` | Delete knowledge |
| POST | `/v1/companions/{id}/knowledge/search` | Search knowledge |
| POST | `/v1/companions/{id}/core-memories` | Update core memories |
| POST | `/v1/companions/{id}/tools` | Index OpenAPI spec for tools |
| GET | `/v1/companions/{id}/tools` | List tool specs |
| GET | `/v1/companions/{id}/tools/{spec_id}` | Get tool spec details |
| PATCH | `/v1/companions/{id}/tools/{spec_id}` | Update tool secrets config |
| DELETE | `/v1/companions/{id}/tools/{spec_id}` | Delete tool spec |
| POST | `/v1/secrets` | Create or update a secret |
| GET | `/v1/secrets` | List secrets (metadata only) |
| DELETE | `/v1/secrets/{secret_name}` | Delete a secret |

### Conversations (`/conversations`) - Clerk Auth

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/conversations` | Create conversation |
| GET | `/conversations/{id}` | Get conversation |
| GET | `/conversations/{id}/messages` | Get messages (paginated) |
| POST | `/conversations/{id}/messages` | Send message with context plan |
| POST | `/conversations/{id}/messages/stream` | Stream message response |

### Voice Sessions (`/sessions`) - WebSocket

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/sessions` | Create session (returns WS URL + token) |
| WS | `/sessions/{session_id}` | Audio stream (binary PCM) |

### Tools (`/api/tools`) - Clerk Auth

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/tools/index` | Index OpenAPI spec |
| GET | `/api/tools` | List tool specs |
| PATCH | `/api/tools/{spec_id}/secrets-config` | Update secrets mapping |
| DELETE | `/api/tools/{spec_id}` | Delete tool spec |

### Analytics (`/api/analytics`) - Clerk Auth

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/analytics/conversations/metrics` | Conversation metrics |
| GET | `/api/analytics/conversations/time-series` | Time series data |
| POST | `/api/analytics/jobs` | Create labeling/summarization job |
| GET | `/api/analytics/jobs/{id}` | Get job status |

### Memory (`/api/memories`) - Clerk Auth

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/memories/{companion_id}` | List memories |
| POST | `/api/memories/{companion_id}` | Add memory |
| DELETE | `/api/memories/{id}` | Delete memory |

---

## Database Schema (Key Tables)

### `users`
- `id` (UUID, PK), `clerk_user_id`, `email`, `username`, `display_name`, `avatar_url`
- `onboarding_completed`, `created_at`, `updated_at`

### `projects`
- `id` (UUID, PK), `owner_id` (FK users), `name`, `slug` (unique), `is_default`

### `companions`
- `id` (UUID, PK), `owner_id`, `project_id`, `name`, `description`, `metadata` (JSONB config)

### `companion_versions`
- `id` (UUID, PK), `companion_id`, `version_number`, `system_prompt`, `voice_id`
- `memory_enabled`, `config` (JSONB), `status` (DRAFT/DEPLOYED/ARCHIVED)

### `conversations`
- `id` (UUID, PK), `companion_id`, `external_user_id`, `share_id`
- `started_at`, `ended_at`, `message_count`, `context_engine`

### `messages`
- `id` (UUID, PK), `conversation_id`, `role` (user/assistant/system), `content`
- `input_modality` (text/voice/image), `pii_spans` (JSONB), `created_at`

### `memories` (pgvector)
- `id` (UUID, PK), `companion_id`, `content`, `embedding` (vector)
- `importance` (0-1), `modality`, `external_user_id`, `is_core`

### `project_api_keys`
- `id` (UUID, PK), `project_id`, `prefix` (unique), `secret_hash`, `salt`
- `status` (active/revoked), `scopes`, `expires_at`

### `tool_specs`
- `id` (UUID, PK), `project_id`, `companion_id`, `spec_name`
- `json_content` (JSONB - OpenAPI spec), `secrets_config`

### `tool_operations`
- `id` (UUID, PK), `tool_spec_id`, `operation_id`, `method`, `path`
- `parameters` (JSONB), `request_body_schema`, `response_schema`

### `project_secrets`
- `id` (UUID, PK), `project_id`, `name`, `encrypted_value`

### `actions`
- `id` (UUID, PK), `companion_id`, `key`, `action_type` (webhook/modal/scheduled)
- `config` (JSONB), `triggers` (JSONB), `enabled`

### `background_jobs`
- `id` (UUID, PK), `companion_id`, `type` (labeling/summarization/privacy)
- `status`, `total_items`, `processed_count`, `error_count`

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
# Format: emk_dev_<prefix>.<secret>
def get_project_api_subject(request, conn) -> ProjectApiKeySubject
```

### Development Bypass
Set `DISABLE_AUTH_FOR_TESTING=true` to skip authentication.

---

## Key Services

### Context Engine (`context/orchestrator.py`)
Builds context plans by executing parallel layers:
1. **Core Prompt Layer**: System prompt + core memories
2. **Memory Layer**: Retrieved memories (gated by heuristic)
3. **Knowledge Layer**: Vector search results
4. **Tools Layer**: Available tool definitions
5. **Actions Layer**: Triggered actions

```python
async def build_context_plan(
    companion_id, conversation_id, user_message, ...
) -> ContextPlan
```

### Memory System
- **Ingestion**: `memory_service.py` + Modal worker
- **Retrieval**: `memory_runtime.py` (heuristic-gated vector search)
- **Storage**: pgvector with HNSW index

```python
# Heuristic gate
should_retrieve_memories(message, history) -> bool

# Vector search
retrieve_regular_memories(companion_id, query, top_k) -> List[Memory]
```

### Tool Integration
- **Indexing**: Parse OpenAPI spec, store operations
- **Execution**: Build HTTP request, inject secrets, call endpoint

```python
# In modals/workers/tools.py
def index_tools(spec_json, companion_id, project_id)
def call_tool(operation_id, parameters, secrets)
```

### LLM Resolution
```python
# In llm_resolver.py
resolve_llm_client(provider_string) -> AsyncOpenAI

# Supported: openai, openrouter, vllm
```

---

## Modal Workers

### `em-tools` - Tool Execution & Indexing
```python
class ToolsWorker:
    def index_tools(spec_json, ...)  # Parse OpenAPI spec
    def call_tool(operation, params)  # Execute HTTP request
```

### `em-db` - Database Gateway
```python
class DbGateway:
    def start_labeling_job(...)
    def start_summary_job(...)
    def start_privacy_job(...)
    def create_memories_batch(...)
```

### Deploying Workers
```bash
uv run python app/scripts/modal_deploy.py
```

---

## Environment Variables

### Database
```
DATABASE_DSN=postgresql://...
SUPABASE_URL=https://...supabase.co
SUPABASE_ANON_KEY=...
SUPABASE_SERVICE_KEY=...
```

### Authentication
```
CLERK_SECRET_KEY=sk_test_...
CLERK_JWT_KEY=-----BEGIN PUBLIC KEY-----...
CLERK_AUTHORIZED_PARTIES=http://localhost:3000,...
DISABLE_AUTH_FOR_TESTING=false
```

### LLM Providers
```
OPENAI_API_KEY=sk-proj-...
OPENROUTER_API_KEY=sk-or-v1-...
LLM_BASE_URL=https://openrouter.ai/api/v1
```

### Voice Services
```
ELEVEN_API_KEY=sk_...
CARTESIA_API_KEY=sk_car_...
DEEPGRAM_API_KEY=...
DAILY_API_KEY=...
```

### Memory System
```
MEMORY_RETRIEVAL_ENABLED=true
MEMORY_TOP_K_DEFAULT=12
MEMORY_RETRIEVAL_TIMEOUT_MS=2500
```

### Modal
```
MODAL_ENVIRONMENT=staging
MODAL_TOKEN_ID=ak-...
MODAL_TOKEN_SECRET=as-...
```

### AWS S3
```
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
KNOWLEDGE_S3_BUCKET=emotion-machine-knowledge-prod
```

### Security
```
ENCRYPTION_KEY=... (AES-256 key for secrets)
```

---

## Running Tests

```bash
# All tests
uv run pytest

# Single file
uv run pytest tests/test_memory_integration.py -vv

# With coverage
uv run pytest --cov=app
```

### Key Test Files
- `test_api_memory_integration.py` - Memory system
- `test_client_api.py` - Client SDK endpoints
- `test_context_plan.py` - Context engine
- `test_tools_secrets.py` - Tool secret encryption
- `test_encryption.py` - AES encryption

---

## Development Commands

```bash
# Start server
uv run uvicorn app.main:app --reload --port 8100

# Run migrations
uv run alembic upgrade head

# Deploy Modal workers
uv run python app/scripts/modal_deploy.py

# Generate API key
uv run python app/scripts/create_project_api_key.py <project_id>

# Export OpenAPI schema
uv run python app/scripts/export_v1_openapi.py
```

---

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                      FastAPI Application                     │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │ Routers: /api, /v1, /sessions, /conversations, etc.    │ │
│  └─────────────────────────────────────────────────────────┘ │
│                              ↓                               │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │ Auth: Clerk JWT | Project API Key                       │ │
│  └─────────────────────────────────────────────────────────┘ │
│                              ↓                               │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │ Services: LLM, Memory, Knowledge, Context, Tools       │ │
│  └─────────────────────────────────────────────────────────┘ │
│                              ↓                               │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │ Repositories: Companion, Conversation, Memory, State   │ │
│  └─────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
                               ↓
┌──────────────────────────────────────────────────────────────┐
│  PostgreSQL (Supabase)         │  External Services          │
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

**Tool execution errors**: Check `project_secrets` encryption and `secrets_config` mapping

**Modal worker issues**: Verify `MODAL_TOKEN_ID`/`MODAL_TOKEN_SECRET` and run `modal deploy`

### Logging
```python
import structlog
logger = structlog.get_logger()
logger.info("message", key=value)
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
3. Deploy with `modal deploy`
