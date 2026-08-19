# Emotion Machine SDK

Python SDK for building AI companions with persistent relationships.

## Installation

```bash
pip install emotion-machine
```

The client depends on `httpx` and `websockets`, targeting Python 3.10+.

## Quick Start

```python
from emotion_machine import EmotionMachine

async with EmotionMachine(api_key="...") as em:
    # Get a relationship handle (no network call)
    rel = em.relationship(companion_id, user_id)

    # Send a message
    response = await rel.send("Hello!")
    print(response["message"]["content"])
```

## Progressive Disclosure

The SDK provides four levels of interaction complexity:

### Level 1: Simple Send

```python
rel = em.relationship(companion_id, user_id)
response = await rel.send("Hello!")
print(response["message"]["content"])
```

### Level 2: Streaming

```python
async for chunk in rel.stream("Tell me a story"):
    data = chunk.get("data", {})
    if data.get("type") == "delta":
        print(data["data"]["content"], end="")
```

### Level 3: WebSocket (Real-time)

```python
async with rel.connect() as ws:
    await ws.send("Hello!")
    async for event in ws:
        if event["type"] == "delta":
            print(event["data"]["content"], end="")
        elif event["type"] == "proactive":
            print(f"[Companion]: {event['data']['content']}")
```

**Authentication:** By default, the JWT token is sent in the `Authorization` header
(recommended for mobile/native clients). For browser clients that cannot set custom
WebSocket headers, use query parameter auth:

```python
async with rel.connect(use_header_auth=False) as ws:
    ...
```

### Level 4: Voice

```python
async with rel.voice(config={"voice_name": "alloy"}) as voice:
    async for event in voice:
        handle(event)
```

**Authentication:** Same as WebSocket - header auth by default, use `use_header_auth=False`
for browser clients.

## Companion Management

```python
# Create a companion
companion = await em.companions.create(
    name="Coach",
    config={
        "system_prompt": {"full_system_prompt": "You are a helpful coach."},
        "inference": {
            "model": "openai-gpt4o-mini",  # Default model for this companion
            "temperature": 0.7,            # Default temperature
        },
        "memory": {"enabled": True},
        "knowledge": {"enabled": True},
    }
)

# List companions
companions = await em.companions.list()

# Get a companion
companion = await em.companions.get(companion_id)

# Update a companion
await em.companions.update(companion_id, name="New Name")

# Delete a companion
await em.companions.delete(companion_id)
```

## Knowledge Management

```python
# Ingest a file
job = await em.knowledge.ingest(companion_id, file_path="data.jsonl")

# Wait for completion
await em.knowledge.wait(job["id"])

# Search knowledge
results = await em.knowledge.search(companion_id, query="menstrual cycle")
```

## Profile Management

```python
# Get profile
profile = await rel.profile_get()

# Set profile (replaces entirely)
await rel.profile_set({
    "user": {"name": "Sarah", "age": 28},
    "preferences": {"tone": "friendly"}
})

# Patch profile (merges changes)
await rel.profile_patch({"user": {"mood": "happy"}})

# Clear profile
await rel.profile_clear()
```

## Sessions

```python
# Start a bounded session
session = await rel.session_start(type="coaching")

# Send messages within the session
await session.send("Let's begin our coaching session")

# End session and get summary
summary = await session.end()
print(summary["summary"])
```

## Inbox (Proactive Messages)

```python
# Check for proactive messages
messages = await rel.inbox_check()

# Acknowledge messages
await rel.inbox_ack([m["id"] for m in messages])
```

## Behaviors

### Decorator Syntax

```python
from emotion_machine import behavior

@behavior(triggers=["always"], priority=True)
async def mood_tracker(ctx):
    """Runs on every message, injects context before LLM."""
    if "anxious" in ctx.last_user_message.lower():
        ctx.profile.set("user.mood", "anxious")
        return "User seems anxious."

@behavior(triggers=["idle:30"])
async def idle_checkin(ctx):
    """Runs after 30 minutes of inactivity."""
    ctx.send_message("Hey! Just checking in.")

@behavior(triggers=["every:5"], priority=True)
async def summarize_validate(ctx):
    """Runs every 5th message."""
    return "# REMINDER\nReflect back what the user is experiencing."

@behavior(triggers=["cron:0 0 * * 0"])
async def weekly_analysis(ctx):
    """Runs weekly on Sunday midnight."""
    ctx.profile.set("meta.last_weekly_analysis", datetime.now().isoformat())
```

### Deploy Behaviors

```python
# Deploy all decorated behaviors to a companion
await em.behaviors.deploy(companion_id)
```

### Create Behaviors Programmatically

```python
await em.behaviors.create(
    companion_id,
    behavior_key="my_behavior",
    source_code='''
async def execute(ctx):
    return "Hello from behavior!"
''',
    triggers=["always"],
    priority=True,
)
```

### Trigger Behaviors via API

```python
result = await rel.behavior_trigger("my_behavior", context={"key": "value"})
```

### LLM Access in Behaviors

Behaviors can call LLMs directly using `ctx.llm.run()`:

```python
@behavior(triggers=["api"])
async def analyze_mood(ctx):
    """Use LLM to analyze user's mood from recent messages."""
    response = await ctx.llm.run(
        prompt=f"Analyze the mood in this message: {ctx.last_user_message}",
        system="You are a mood analyst. Respond with one word: happy, sad, anxious, or neutral.",
        model="google/gemini-2.0-flash-001:google-vertex",  # optional, this is default
        temperature=0.3,  # optional, default 0.7
        max_tokens=50,  # optional, default 1000
    )
    ctx.profile.set("user.current_mood", response.strip().lower())
    ctx.send_message(f"I sense you're feeling {response.strip().lower()} today.")
```

**Note:** LLM access is available to ALL behaviors, including isolated ones. The `ctx.llm.run()` call routes through a dedicated Modal function (`run_llm_node`) that has network access, allowing even network-blocked behaviors to use LLMs.

### Test Behaviors

```python
result = await em.behaviors.test(
    companion_id,
    "mood_tracker",
    message="I'm feeling anxious today",
)
```

## Trigger Types

| Trigger | Description | Example |
|---------|-------------|---------|
| `always` | Every message | `["always"]` |
| `every:N` | Every Nth message | `["every:5"]` |
| `turn:N,M` | Specific turn numbers | `["turn:1,5,10"]` |
| `keyword:X,Y` | Keywords detected | `["keyword:help,urgent"]` |
| `cron:...` | Cron schedule | `["cron:0 0 * * 0"]` |
| `idle:N` | N minutes of inactivity | `["idle:30"]` |

## Configuration

```python
# Via constructor
em = EmotionMachine(
    api_key="...",
    base_url="https://api.emotionmachine.ai",  # Optional, this is the default
    timeout=30.0,
)

# Via environment variables
# EM_API_KEY - API key
# EM_BASE_URL - Base URL (default: https://api.emotionmachine.ai)
```

## Companion Config Structure

Companion configuration uses nested blocks for different capabilities:

```python
config = {
    "system_prompt": {
        "full_system_prompt": "You are a helpful assistant."
    },
    "inference": {
        "model": "openai-gpt4o-mini",   # LLM model to use
        "temperature": 0.7,              # Response creativity (0-1)
    },
    "memory": {"enabled": True},         # Long-term memory
    "knowledge": {"enabled": True},      # RAG knowledge base
    "behaviors": {"enabled": True},      # Automated behaviors
    "voice": {
        "provider": "elevenlabs",
        "voice_name": "Sarah",
    },
}
```

> **Note:** For backward compatibility, top-level `model` and `temperature` fields
> are accepted and automatically migrated to the `inference` block. We recommend
> using the `inference` block directly for new code.

## Error Handling

```python
from emotion_machine import APIError, KnowledgeJobFailed, WebSocketError

try:
    response = await rel.send("Hello!")
except APIError as e:
    print(f"API error: {e.status_code} - {e.message}")

try:
    await em.knowledge.wait(job_id)
except KnowledgeJobFailed as e:
    print(f"Knowledge job failed: {e.error}")

try:
    async with rel.connect() as ws:
        async for event in ws:
            pass
except WebSocketError as e:
    print(f"WebSocket error: {e.message}")
```

## API Coverage

| Resource | Endpoint | SDK Method |
|----------|----------|------------|
| **Companions** | | |
| | `GET /v1/companions` | `em.companions.list()` |
| | `POST /v1/companions` | `em.companions.create(...)` |
| | `GET /v1/companions/{id}` | `em.companions.get(id)` |
| | `PATCH /v1/companions/{id}` | `em.companions.update(...)` |
| | `DELETE /v1/companions/{id}` | `em.companions.delete(id)` |
| **Knowledge** | | |
| | `POST /v1/companions/{id}/knowledge` | `em.knowledge.ingest(...)` |
| | `GET /v1/knowledge-jobs/{id}` | `em.knowledge.get_job(id)` |
| | `POST /v1/companions/{id}/knowledge/search` | `em.knowledge.search(...)` |
| **Relationships** | | |
| | `PUT /v2/companions/{cid}/relationships/{uid}` | `rel.ensure()` |
| | `POST /v2/companions/{cid}/relationships/{uid}/messages` | `rel.send(...)` |
| | Streaming | `rel.stream(...)` |
| | WebSocket | `rel.connect()` |
| | Voice | `rel.voice()` |
| **Profile** | | |
| | `GET /v2/relationships/{id}/profile` | `rel.profile_get()` |
| | `PUT /v2/relationships/{id}/profile` | `rel.profile_set(...)` |
| | `PATCH /v2/relationships/{id}/profile` | `rel.profile_patch(...)` |
| | `DELETE /v2/relationships/{id}/profile` | `rel.profile_clear()` |
| **Sessions** | | |
| | `POST /v2/relationships/{id}/sessions` | `rel.session_start(...)` |
| | `POST /v2/sessions/{id}/end` | `session.end()` |
| **Inbox** | | |
| | `GET /v2/relationships/{id}/inbox` | `rel.inbox_check()` |
| | `POST /v2/relationships/{id}/inbox/ack` | `rel.inbox_ack(...)` |
| **Config** | | |
| | `GET /v2/relationships/{id}/config` | `rel.config_get()` |
| | `PATCH /v2/relationships/{id}/config` | `rel.config_patch(...)` |
| | `GET /v2/relationships/{id}/config/resolved` | `rel.config_resolved()` |
| **Behaviors** | | |
| | `POST /v2/companions/{id}/behaviors` | `em.behaviors.create(...)` |
| | `GET /v2/companions/{id}/behaviors` | `em.behaviors.list(...)` |
| | `DELETE /v2/companions/{id}/behaviors/{key}` | `em.behaviors.delete(...)` |
| | Deploy decorated | `em.behaviors.deploy(...)` |
| | `POST /v2/relationships/{id}/behaviors/{key}/trigger` | `rel.behavior_trigger(...)` |
| **Memory** | | |
| | `GET /v2/relationships/{id}/memory` | *use HTTP client* |
| | `POST /v2/relationships/{id}/memory` | *use HTTP client* |
| | `PATCH /v2/relationships/{id}/memory/{entry_id}` | *use HTTP client* |
| | `DELETE /v2/relationships/{id}/memory/{entry_id}` | *use HTTP client* |
| | `DELETE /v2/relationships/{id}/memory` | *use HTTP client* |

## Context Manager

Always use the context manager or call `close()` to properly clean up:

```python
# Recommended: context manager
async with EmotionMachine(api_key="...") as em:
    ...

# Alternative: manual cleanup
em = EmotionMachine(api_key="...")
try:
    ...
finally:
    await em.close()
```

## Development

```bash
cd packages/pip-emotion-machine
pip install -e .
```

The package ships from `src/emotion_machine`. Update `pyproject.toml` to bump versions.
