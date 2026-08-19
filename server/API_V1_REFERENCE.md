# Emotion Machine API v1 Reference

Base URL: `https://api.emotionmachine.ai/v1`

## Authentication

All `/v1` endpoints require API key authentication via Bearer token:

```bash
Authorization: Bearer em_your_api_key_here
```

API keys are scoped to a project. All resources (companions, conversations, sessions) are automatically associated with the project tied to your API key.

---

## Audio Format Specifications

### PCM Audio Format

All audio data transmitted over WebSocket uses raw PCM format:

| Property | Value |
|----------|-------|
| Encoding | Signed 16-bit Integer (Int16) |
| Byte Order | Little-endian |
| Channels | Mono (1 channel) |

### Sample Rates by Pipeline

| Pipeline | Direction | Sample Rate | Notes |
|----------|-----------|-------------|-------|
| **STT-LLM-TTS** | Client → Server | 16kHz | Required for Silero VAD |
| **STT-LLM-TTS** | Server → Client | 24kHz | TTS output |
| **OpenAI Realtime** | Client → Server | 24kHz | |
| **OpenAI Realtime** | Server → Client | 24kHz | |

**Important:** The input and output sample rates differ for the STT-LLM-TTS pipeline. Clients must:
- Send audio at **16kHz**
- Receive and play audio at **24kHz**

---

## WebSocket Protocol

### Binary Message Format

Audio is transmitted as raw binary frames (not JSON-wrapped).

**Client → Server:**
```
[Int16 PCM samples as ArrayBuffer]
```

**Server → Client:**
```
[Int16 PCM samples as ArrayBuffer]
```

### Recommended Buffer Sizes

| Setting | Recommended Value | Notes |
|---------|------------------|-------|
| Capture buffer | 4096 samples | Balance between latency and efficiency |
| Jitter buffer | 150ms | Handles network variance |

### Connection Lifecycle

```
1. POST /v1/sessions → {id, ws_url, conversation_id}
2. Connect to ws_url (one-time token is consumed on connect)
3. Set WebSocket.binaryType = 'arraybuffer'
4. Send/receive raw PCM binary frames
5. Close WebSocket to end session
```

### Initial Behavior by Pipeline

| Pipeline | On Connect |
|----------|------------|
| **OpenAI Realtime** | AI speaks first (greeting) |
| **STT-LLM-TTS** | Waits for user to speak first |

---

## Available Models

### Text Chat LLM Models

Used in `/v1/companions/{id}/chat` requests via the `model` parameter, and in voice sessions with `stt-llm-tts` pipeline via `voiceConfig.llm_provider`.

| Model ID | Provider | Underlying Model | Notes |
|----------|----------|------------------|-------|
| `openai-gpt4o-mini` | OpenAI | gpt-4o-mini | **Default** - Fast and cost-effective |
| `openai-gpt4o` | OpenAI | gpt-4o | Higher quality, slower |
| `openai-gpt5.1` | OpenAI | gpt-5.1 | Latest OpenAI model |
| `claude-sonnet-4` | OpenRouter | anthropic/claude-sonnet-4 | Anthropic Claude |
| `claude-sonnet-4.5` | OpenRouter | anthropic/claude-sonnet-4.5 | Latest Claude |
| `claude-sonnet-3.7` | OpenRouter | anthropic/claude-3.7-sonnet | Previous Claude |
| `gemini-2.5-flash` | OpenRouter | google/gemini-2.5-flash | Google Gemini |
| `gemini` | OpenRouter | google/gemini-2.5-flash | Alias for gemini-2.5-flash |
| `moonshot-kimi-k2` | OpenRouter | moonshotai/kimi-k2-0905 | Moonshot Kimi |
| `local-vllm-qwen` | Self-hosted | Qwen/QwQ-32B-AWQ | Requires vLLM setup |

### OpenAI Realtime Models

Used in voice sessions with `openai-realtime` pipeline via `voiceConfig.realtimeModel`.

| Model ID | Notes |
|----------|-------|
| `gpt-realtime-mini-2025-10-06` | **Default** - Used when `realtimeModel` is not specified |

### Voice Providers

#### STT (Speech-to-Text) Providers
Used in `voiceConfig.stt_provider` for `stt-llm-tts` pipeline.

| Provider ID | Service |
|-------------|---------|
| `openai` | OpenAI Whisper |
| `deepgram` | Deepgram |
| `ultravox` | Ultravox |
| `cartesia` | Cartesia |

#### TTS (Text-to-Speech) Providers
Used in `voiceConfig.tts_provider` for `stt-llm-tts` pipeline.

| Provider ID | Service | Available Voices |
|-------------|---------|------------------|
| `openai` | OpenAI TTS | alloy, ash, ballad, coral, echo, sage, shimmer, verse |
| `elevenlabs` | ElevenLabs | Sarah, George, Callum, Charlotte, Matilda, Will |
| `cartesia` | Cartesia | Sophie, Savannah, Brooke, Griffin, Zia, Carson, Wise Lady, Ethan |

---

## Companions

### List Companions

Returns all companions in your project.

```bash
curl -X GET "https://api.emotionmachine.ai/v1/companions" \
  -H "Authorization: Bearer em_your_api_key"
```

**Response** `200 OK`
```json
[
  {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "name": "Customer Support Agent",
    "last_updated": "2 hours ago",
    "project_id": "123e4567-e89b-12d3-a456-426614174000"
  }
]
```

---

### Create Companion

```bash
curl -X POST "https://api.emotionmachine.ai/v1/companions" \
  -H "Authorization: Bearer em_your_api_key" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Customer Support Agent",
    "description": "Handles customer inquiries",
    "config": {
      "system_prompt": {
        "full_system_prompt": "You are a helpful customer support agent..."
      },
      "memory": {
        "enabled": true
      }
    }
  }'
```

**Request Body**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| name | string | Yes | Companion name (1-100 chars) |
| description | string | No | Description (max 500 chars) |
| config | object | No | Companion configuration |

**Response** `201 Created`
```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "name": "Customer Support Agent",
  "description": "Handles customer inquiries",
  "config": {
    "system_prompt": {
      "full_system_prompt": "You are a helpful customer support agent..."
    },
    "memory": {
      "enabled": true
    }
  },
  "created_at": "2024-01-15T10:30:00Z",
  "project_id": "123e4567-e89b-12d3-a456-426614174000"
}
```

---

### Get Companion

```bash
curl -X GET "https://api.emotionmachine.ai/v1/companions/{companion_id}" \
  -H "Authorization: Bearer em_your_api_key"
```

**Response** `200 OK`
```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "name": "Customer Support Agent",
  "description": "Handles customer inquiries",
  "config": {
    "system_prompt": {
      "full_system_prompt": "You are a helpful customer support agent..."
    },
    "memory": {
      "enabled": true
    }
  },
  "created_at": "2024-01-15T10:30:00Z",
  "project_id": "123e4567-e89b-12d3-a456-426614174000"
}
```

---

### Update Companion

```bash
curl -X PATCH "https://api.emotionmachine.ai/v1/companions/{companion_id}" \
  -H "Authorization: Bearer em_your_api_key" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Updated Agent Name",
    "config": {
      "system_prompt": {
        "full_system_prompt": "Updated system prompt..."
      }
    }
  }'
```

**Request Body**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| name | string | No | New name (1-100 chars) |
| description | string | No | New description (max 500 chars) |
| config | object | No | Updated configuration |

**Response** `200 OK`
```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "name": "Updated Agent Name",
  "description": "Handles customer inquiries",
  "config": {
    "system_prompt": {
      "full_system_prompt": "Updated system prompt..."
    }
  },
  "created_at": "2024-01-15T10:30:00Z",
  "project_id": "123e4567-e89b-12d3-a456-426614174000"
}
```

---

### Delete Companion

```bash
curl -X DELETE "https://api.emotionmachine.ai/v1/companions/{companion_id}" \
  -H "Authorization: Bearer em_your_api_key"
```

**Response** `204 No Content`

No response body is returned on successful deletion.

---

## Chat

### Send Message (Non-Streaming)

```bash
curl -X POST "https://api.emotionmachine.ai/v1/companions/{companion_id}/chat" \
  -H "Authorization: Bearer em_your_api_key" \
  -H "Content-Type: application/json" \
  -d '{
    "external_user_id": "user-123",
    "message": "Hello, I need help with my order",
    "conversation_id": null,
    "model": "openai-gpt4o-mini",
    "temperature": 0.7
  }'
```

**Request Body**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| external_user_id | string | Yes | Your user identifier (max 255 chars) |
| message | string | Yes | User message (1-4000 chars) |
| conversation_id | uuid | No | Continue existing conversation |
| profile | object | No | User profile data for personalization |
| model | string | No | LLM model (default: `openai-gpt4o-mini`) |
| temperature | float | No | 0.0-2.0 (default: 0.7) |
| image_ids | uuid[] | No | Image IDs to include as context (see Images in Chat) |

**Available Models**
- `openai-gpt4o` - GPT-4o
- `openai-gpt4o-mini` - GPT-4o Mini (default)
- `openai-gpt5.1` - GPT-5.1
- `claude-sonnet-4` - Claude Sonnet 4
- `claude-sonnet-4.5` - Claude Sonnet 4.5
- `gemini-2.5-flash` - Gemini 2.5 Flash

**Response** `200 OK`
```json
{
  "id": "chatcmpl-abc123",
  "object": "chat.completion",
  "created": 1705312200,
  "model": "openai-gpt4o-mini",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "Hello! I'd be happy to help you with your order. Could you please provide your order number?"
      },
      "finish_reason": "stop",
      "emotion_machine": {
        "metadata": {
          "conversation_id": "660e8400-e29b-41d4-a716-446655440000",
          "project_id": "123e4567-e89b-12d3-a456-426614174000"
        }
      }
    }
  ],
  "usage": {
    "prompt_tokens": 0,
    "completion_tokens": 0,
    "total_tokens": 0
  }
}
```

---

### Send Message (Streaming)

```bash
curl -X POST "https://api.emotionmachine.ai/v1/companions/{companion_id}/chat/stream" \
  -H "Authorization: Bearer em_your_api_key" \
  -H "Content-Type: application/json" \
  -d '{
    "external_user_id": "user-123",
    "message": "Tell me about your return policy"
  }'
```

**Request Body**

Same as non-streaming endpoint.

**Response** `200 OK` (Server-Sent Events)

```
event: ack
id: 1
data: {"conversation_id": "660e8400-...", "message": {"id": "...", "role": "user", "content": "Tell me about...", "created_at": "..."}}

event: status
id: 2
data: {"stage": "retrieving", "phase": "start"}

event: status
id: 3
data: {"stage": "retrieving", "phase": "end", "meta": {"retrieval_items": 3, "retrieval_ms": 45.2}}

event: status
id: 4
data: {"stage": "thinking", "phase": "start", "meta": {"model": "gpt-4o-mini"}}

event: delta
id: 5
data: {"id": "chatcmpl-...", "object": "chat.completion.chunk", "created": 1705312200, "model": "gpt-4o-mini", "choices": [{"index": 0, "delta": {"role": "assistant", "content": "Our"}, "finish_reason": null}]}

event: delta
id: 6
data: {"id": "chatcmpl-...", "object": "chat.completion.chunk", "created": 1705312200, "model": "gpt-4o-mini", "choices": [{"index": 0, "delta": {"role": "assistant", "content": " return"}, "finish_reason": null}]}

event: status
id: 7
data: {"stage": "thinking", "phase": "end"}

event: message
id: 8
data: {"id": "chatcmpl-...", "object": "chat.completion", "created": 1705312200, "model": "gpt-4o-mini", "choices": [{"index": 0, "message": {"role": "assistant", "content": "Our return policy allows..."}, "finish_reason": "stop", "emotion_machine": {"metadata": {"conversation_id": "...", "project_id": "..."}}}], "usage": {...}}

event: done
id: 9
data: {"conversation_id": "660e8400-...", "assistant_message_id": "..."}
```

---

## Voice Sessions

### Voice Session Lifecycle

The voice session API supports start, pause, continue, and restart flows:

| Action | Request | Result |
|--------|---------|--------|
| **Start** | `POST /v1/sessions` with `companionId` only | New conversation created, returns `conversation_id` |
| **Pause** | Client closes WebSocket | Session ends, `ws_url` invalidated |
| **Continue** | `POST /v1/sessions` with `companionId` + `conversationId` | Same conversation, new `ws_url` |
| **Restart** | `POST /v1/sessions` with `companionId` only (no `conversationId`) | New conversation created |

**SDK Flow Example:**
```
1. User clicks START → POST /v1/sessions {companionId}
   ← Response: {id, ws_url, conversation_id}
   → SDK stores conversation_id

2. User clicks PAUSE → SDK closes WebSocket
   → ws_url is now invalid

3. User clicks CONTINUE → POST /v1/sessions {companionId, conversationId}
   ← Response: {id, ws_url, conversation_id} (same conversation_id)
   → SDK connects to new ws_url

4. User clicks RESTART → POST /v1/sessions {companionId}
   ← Response: {id, ws_url, conversation_id} (NEW conversation_id)
   → SDK updates stored conversation_id
```

---

### Create Voice Session

Creates a voice session and returns a WebSocket URL with a one-time authentication token.

- If `conversationId` is omitted: creates a **new conversation**
- If `conversationId` is provided: **continues existing conversation**

```bash
curl -X POST "https://api.emotionmachine.ai/v1/sessions" \
  -H "Authorization: Bearer em_your_api_key" \
  -H "Content-Type: application/json" \
  -d '{
    "companionId": "550e8400-e29b-41d4-a716-446655440000"
  }'
```

**Request Body**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| companionId | uuid | Yes | Companion to use for the session |
| conversationId | uuid | No | Continue existing conversation (omit to create new) |
| externalUserId | string | No | Your user identifier (max 255 chars) |
| voiceConfig | object | No | Voice pipeline configuration |

**Voice Config Options**
| Field | Type | Default | Description |
|-------|------|---------|-------------|
| pipeline_type | string | `openai-realtime` | `openai-realtime` or `stt-llm-tts` |
| voice_name | string | `alloy` | Voice name (see available voices below) |
| temperature | float | 0.7 | LLM temperature |
| stt_provider | string | — | Required for `stt-llm-tts`: `openai`, `deepgram`, `ultravox`, `cartesia` |
| llm_provider | string | — | Required for `stt-llm-tts`: see available models above |
| tts_provider | string | — | Required for `stt-llm-tts`: `openai`, `elevenlabs`, `cartesia` |

**Available Voices**

*OpenAI:* `alloy`, `ash`, `ballad`, `coral`, `echo`, `sage`, `shimmer`, `verse`

*ElevenLabs:* `Sarah`, `George`, `Callum`, `Charlotte`, `Matilda`, `Will`

*Cartesia:* `Sophie`, `Savannah`, `Brooke`, `Griffin`, `Zia`, `Carson`, `Wise Lady`, `Ethan`

**Response** `201 Created`
```json
{
  "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "ws_url": "wss://api.emotionmachine.ai/sessions/ws/a1b2c3d4-e5f6-7890-abcd-ef1234567890?t=one_time_token_here",
  "conversation_id": "660e8400-e29b-41d4-a716-446655440000"
}
```

**Example: Start New Conversation (Simplest)**
```bash
curl -X POST "https://api.emotionmachine.ai/v1/sessions" \
  -H "Authorization: Bearer em_your_api_key" \
  -H "Content-Type: application/json" \
  -d '{
    "companionId": "550e8400-e29b-41d4-a716-446655440000"
  }'
```

**Example: Continue Existing Conversation**

Only `companionId` and `conversationId` are needed - the conversation already has the user association.

```bash
curl -X POST "https://api.emotionmachine.ai/v1/sessions" \
  -H "Authorization: Bearer em_your_api_key" \
  -H "Content-Type: application/json" \
  -d '{
    "companionId": "550e8400-e29b-41d4-a716-446655440000",
    "conversationId": "660e8400-e29b-41d4-a716-446655440000"
  }'
```

**Example: With Custom Voice**
```bash
curl -X POST "https://api.emotionmachine.ai/v1/sessions" \
  -H "Authorization: Bearer em_your_api_key" \
  -H "Content-Type: application/json" \
  -d '{
    "companionId": "550e8400-e29b-41d4-a716-446655440000",
    "externalUserId": "user-123",
    "voiceConfig": {
      "pipeline_type": "openai-realtime",
      "voice_name": "sage"
    }
  }'
```

**Example: STT-LLM-TTS Pipeline**
```bash
curl -X POST "https://api.emotionmachine.ai/v1/sessions" \
  -H "Authorization: Bearer em_your_api_key" \
  -H "Content-Type: application/json" \
  -d '{
    "companionId": "550e8400-e29b-41d4-a716-446655440000",
    "externalUserId": "user-123",
    "voiceConfig": {
      "pipeline_type": "stt-llm-tts",
      "stt_provider": "deepgram",
      "llm_provider": "claude-sonnet-4",
      "tts_provider": "elevenlabs",
      "voice_name": "Sarah",
      "temperature": 0.8
    }
  }'
```

---

### Update Voice Session

Update a session's configuration before the WebSocket connects. Cannot update active sessions.

```bash
curl -X PATCH "https://api.emotionmachine.ai/v1/sessions/{session_id}" \
  -H "Authorization: Bearer em_your_api_key" \
  -H "Content-Type: application/json" \
  -d '{
    "companionId": "550e8400-e29b-41d4-a716-446655440000",
    "voiceConfig": {
      "voice_name": "coral"
    }
  }'
```

**Request Body**

Same as Create Voice Session.

**Response** `200 OK`
```json
{
  "status": "updated"
}
```

**Error** `409 Conflict`
```json
{
  "detail": "Cannot update an active session. Please stop the session first."
}
```

---

### WebSocket Connection

After creating a session, connect to the WebSocket URL returned in the response. The URL includes a one-time token that is consumed on connection.

```javascript
const ws = new WebSocket(response.ws_url);

// Send raw PCM audio (16-bit, mono)
// Sample rate: 24kHz for OpenAI Realtime, 16kHz for STT-LLM-TTS
ws.send(audioBuffer);

// Receive audio frames back
ws.onmessage = (event) => {
  // event.data contains raw PCM audio
  playAudio(event.data);
};
```

---

## Voice SDK (JavaScript/TypeScript)

The Emotion Machine Voice SDK provides a high-level TypeScript client for integrating real-time voice conversations into web applications. It handles WebSocket audio streaming, microphone capture, and audio playback.

> **Note:** The SDK is currently available as source code. Contact us for access or integration support.

### Quick Start

```typescript
import { CompanionClient } from './lib/voice/CompanionClient';

// 1. Initialize with your API key
const client = new CompanionClient({
  apiKey: 'em_live_xxxxxxxx',
});

// 2. Listen to state changes
client.addEventListener('companion:state_change', (event) => {
  const { newState } = event.detail;
  console.log(`Status: ${newState}`);
  // States: 'init' -> 'connecting' -> 'connected' -> 'closed' | 'error'
});

// 3. Start a new conversation
const conversationId = await client.startConversation('companion-id-here', {
  voiceConfig: {
    pipeline_type: 'openai-realtime',
    voice_name: 'alloy',
    temperature: 0.7,
    realtimeModel: 'gpt-realtime-mini-2025-10-06'
  }
});

// 4. Later, disconnect
client.disconnect();
```

### CompanionClient API

#### Constructor

```typescript
new CompanionClient(options: CompanionClientOptions)
```

| Option | Type | Description |
|--------|------|-------------|
| apiKey | string | Required. Your Emotion Machine API key |
| baseUrl | string | Optional. API endpoint (defaults to `https://api.emotionmachine.ai`) |
| sampleRate | number | Optional. Audio sample rate (defaults to 24000Hz) |

#### Methods

**startConversation(companionId, options)**

Creates a new voice session and immediately connects.

```typescript
const conversationId = await client.startConversation(
  'companion-uuid',
  {
    voiceConfig: {
      pipeline_type: 'openai-realtime',
      voice_name: 'sage',
      temperature: 0.7,
      realtimeModel: 'gpt-realtime-mini-2025-10-06'
    }
  }
);
```

Returns the `conversation_id` for the new session.

**joinConversation(companionId, conversationId, options)**

Resumes an existing conversation (e.g., after pause/disconnect).

```typescript
await client.joinConversation(
  'companion-uuid',
  'existing-conversation-uuid'
);
```

**disconnect()**

Closes the WebSocket connection and releases the microphone.

```typescript
client.disconnect();
```

#### Events

Listen to events using `addEventListener`:

```typescript
client.addEventListener('companion:state_change', (event) => {
  const { newState } = event.detail;
  // Handle state change
});

client.addEventListener('error', (event) => {
  const { error } = event.detail;
  console.error('Client error:', error);
});
```

**State Machine**

| State | Description |
|-------|-------------|
| `init` | Client initialized, not yet authenticated |
| `ready` | Authenticated and ready to connect |
| `connecting` | Establishing WebSocket connection |
| `connected` | Audio stream active, conversation in progress |
| `closed` | Connection terminated |
| `error` | An error occurred |

### React Integration

The SDK includes a ready-to-use React component:

```tsx
import { VoiceChat } from './features/EmotionVoiceChat';

function App() {
  return (
    <VoiceChat apiKey="em_live_xxxxxxxx" />
  );
}
```

Or build your own UI using the `CompanionClient`:

```tsx
import { useEffect, useRef, useState } from 'react';
import { CompanionClient, CompanionClientStatus } from './lib/voice/CompanionClient';

function VoiceButton({ apiKey, companionId }: { apiKey: string; companionId: string }) {
  const [status, setStatus] = useState<CompanionClientStatus>('init');
  const [conversationId, setConversationId] = useState<string | null>(null);
  const clientRef = useRef<CompanionClient>(new CompanionClient({ apiKey }));

  useEffect(() => {
    const client = clientRef.current;
    const handleStateChange = (e: CustomEvent) => setStatus(e.detail.newState);
    client.addEventListener('companion:state_change', handleStateChange);
    return () => client.removeEventListener('companion:state_change', handleStateChange);
  }, []);

  const handleClick = async () => {
    const client = clientRef.current;

    if (status === 'connected') {
      client.disconnect();
    } else if (conversationId) {
      // Resume existing conversation
      await client.joinConversation(companionId, conversationId);
    } else {
      // Start new conversation
      const id = await client.startConversation(companionId, {
        voiceConfig: {
          pipeline_type: 'openai-realtime',
          voice_name: 'alloy',
          temperature: 0.7,
          realtimeModel: 'gpt-realtime-mini-2025-10-06'
        }
      });
      setConversationId(id);
    }
  };

  return (
    <button onClick={handleClick}>
      {status === 'connected' ? 'Stop' : 'Start'} Voice Chat
    </button>
  );
}
```

### Session Lifecycle

```
┌─────────────────────────────────────────────────────────────┐
│  User clicks START                                          │
│    ↓                                                        │
│  client.startConversation(companionId)                      │
│    → POST /v1/sessions { companionId }                      │
│    ← { id, ws_url, conversation_id }                        │
│    → WebSocket connects, microphone starts                  │
│    → Status: 'connected'                                    │
├─────────────────────────────────────────────────────────────┤
│  User clicks PAUSE                                          │
│    ↓                                                        │
│  client.disconnect()                                        │
│    → WebSocket closes, microphone stops                     │
│    → Status: 'closed'                                       │
│    → conversation_id is preserved in state                  │
├─────────────────────────────────────────────────────────────┤
│  User clicks RESUME                                         │
│    ↓                                                        │
│  client.joinConversation(companionId, conversationId)       │
│    → POST /v1/sessions { companionId, conversationId }      │
│    ← { id, ws_url, conversation_id } (same conversation)    │
│    → New WebSocket connects with conversation history       │
│    → Status: 'connected'                                    │
├─────────────────────────────────────────────────────────────┤
│  User clicks RESTART                                        │
│    ↓                                                        │
│  client.disconnect() then client.startConversation(...)     │
│    → Creates NEW conversation_id                            │
│    → Fresh conversation, no history                         │
└─────────────────────────────────────────────────────────────┘
```

---

## Conversations

### Create Conversation

Create a new conversation for a companion. This is useful when you need to upload images before sending any messages.

```bash
curl -X POST "https://api.emotionmachine.ai/v1/companions/{companion_id}/conversations" \
  -H "Authorization: Bearer em_your_api_key" \
  -H "Content-Type: application/json" \
  -d '{
    "external_user_id": "user-123"
  }'
```

**Request Body**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| external_user_id | string | Yes | Your user identifier (max 255 chars) |

**Response** `201 Created`
```json
{
  "conversation_id": "660e8400-e29b-41d4-a716-446655440000"
}
```

---

### Get Conversation

Retrieve a conversation with all messages.

```bash
curl -X GET "https://api.emotionmachine.ai/v1/conversations/{conversation_id}" \
  -H "Authorization: Bearer em_your_api_key"
```

**Response** `200 OK`
```json
{
  "id": "660e8400-e29b-41d4-a716-446655440000",
  "companion_id": "550e8400-e29b-41d4-a716-446655440000",
  "external_user_id": "user-123",
  "started_at": "2024-01-15T10:30:00Z",
  "messages": [
    {
      "id": "770e8400-e29b-41d4-a716-446655440000",
      "role": "user",
      "content": "Hello, I need help",
      "created_at": "2024-01-15T10:30:00Z"
    },
    {
      "id": "880e8400-e29b-41d4-a716-446655440000",
      "role": "assistant",
      "content": "Hello! How can I assist you today?",
      "created_at": "2024-01-15T10:30:05Z"
    }
  ]
}
```

---

### List Conversations for Companion

```bash
curl -X GET "https://api.emotionmachine.ai/v1/companions/{companion_id}/conversations?limit=50&offset=0" \
  -H "Authorization: Bearer em_your_api_key"
```

**Query Parameters**
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| limit | int | 50 | Results per page (1-200) |
| offset | int | 0 | Pagination offset |
| external_user_id | string | — | Filter by exact user ID |
| external_user_prefix | string | — | Filter by user ID prefix (e.g., `user-`) |

**Response** `200 OK`
```json
[
  {
    "id": "660e8400-e29b-41d4-a716-446655440000",
    "companion_id": "550e8400-e29b-41d4-a716-446655440000",
    "external_user_id": "user-123",
    "started_at": "2024-01-15T10:30:00Z",
    "last_message_at": "2024-01-15T10:35:00Z",
    "message_count": 12
  }
]
```

---

## Profile Schema

### Set Profile Schema

Define a JSON schema for user profiles associated with a companion.

```bash
curl -X PUT "https://api.emotionmachine.ai/v1/companions/{companion_id}/profile-schema" \
  -H "Authorization: Bearer em_your_api_key" \
  -H "Content-Type: application/json" \
  -d '{
    "schema": {
      "type": "object",
      "properties": {
        "name": {"type": "string"},
        "subscription_tier": {"type": "string", "enum": ["free", "pro", "enterprise"]},
        "preferences": {
          "type": "object",
          "properties": {
            "language": {"type": "string"},
            "timezone": {"type": "string"}
          }
        }
      }
    }
  }'
```

**Response** `200 OK`
```json
{
  "companion_id": "550e8400-e29b-41d4-a716-446655440000",
  "project_id": "123e4567-e89b-12d3-a456-426614174000",
  "schema": {
    "type": "object",
    "properties": {
      "name": {"type": "string"},
      "subscription_tier": {"type": "string", "enum": ["free", "pro", "enterprise"]}
    }
  },
  "updated_at": "2024-01-15T10:30:00Z",
  "updated_by": null
}
```

---

### Get Profile Schema

```bash
curl -X GET "https://api.emotionmachine.ai/v1/companions/{companion_id}/profile-schema" \
  -H "Authorization: Bearer em_your_api_key"
```

**Response** `200 OK`
```json
{
  "companion_id": "550e8400-e29b-41d4-a716-446655440000",
  "project_id": "123e4567-e89b-12d3-a456-426614174000",
  "schema": {
    "type": "object",
    "properties": {
      "name": {"type": "string"}
    }
  },
  "updated_at": "2024-01-15T10:30:00Z",
  "updated_by": null
}
```

---

## Knowledge Base

### Upload Knowledge Asset

Upload a file to the companion's knowledge base.

```bash
curl -X POST "https://api.emotionmachine.ai/v1/companions/{companion_id}/knowledge-assets" \
  -H "Authorization: Bearer em_your_api_key" \
  -F "file=@document.pdf"
```

**Response** `201 Created`
```json
{
  "id": "990e8400-e29b-41d4-a716-446655440000",
  "project_id": "123e4567-e89b-12d3-a456-426614174000",
  "companion_id": "550e8400-e29b-41d4-a716-446655440000",
  "filename": "document.pdf",
  "content_type": "application/pdf",
  "size_bytes": 1048576,
  "status": "pending",
  "created_at": "2024-01-15T10:30:00Z"
}
```

---

### List Knowledge Assets

```bash
curl -X GET "https://api.emotionmachine.ai/v1/companions/{companion_id}/knowledge-assets?limit=50" \
  -H "Authorization: Bearer em_your_api_key"
```

**Response** `200 OK`
```json
[
  {
    "id": "990e8400-e29b-41d4-a716-446655440000",
    "project_id": "123e4567-e89b-12d3-a456-426614174000",
    "companion_id": "550e8400-e29b-41d4-a716-446655440000",
    "filename": "document.pdf",
    "content_type": "application/pdf",
    "size_bytes": 1048576,
    "status": "processed",
    "created_at": "2024-01-15T10:30:00Z"
  }
]
```

---

### Ingest Knowledge

Ingest text content or trigger processing of an uploaded asset.

```bash
curl -X POST "https://api.emotionmachine.ai/v1/companions/{companion_id}/knowledge" \
  -H "Authorization: Bearer em_your_api_key" \
  -H "Content-Type: application/json" \
  -d '{
    "type": "text",
    "content": "This is the knowledge content to ingest...",
    "key": "product-faq"
  }'
```

**Request Body**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| type | string | Yes | `text`, `url`, or `asset` |
| content | string | Conditional | Required for `text` type |
| key | string | No | Identifier for the knowledge item |
| asset_id | uuid | Conditional | Required for `asset` type |

**Response** `202 Accepted`
```json
{
  "id": "aa0e8400-e29b-41d4-a716-446655440000",
  "project_id": "123e4567-e89b-12d3-a456-426614174000",
  "companion_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "pending",
  "created_at": "2024-01-15T10:30:00Z"
}
```

---

### Search Knowledge

Search the companion's knowledge base using vector similarity.

```bash
curl -X POST "https://api.emotionmachine.ai/v1/companions/{companion_id}/knowledge/search" \
  -H "Authorization: Bearer em_your_api_key" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "How do I reset my password?",
    "max_results": 5
  }'
```

**Request Body**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| query | string | Yes | Search query |
| max_results | int | No | Maximum results (default: 5) |
| filters | object | No | Additional filters |
| mode | string | No | Search mode |

**Response** `200 OK`
```json
{
  "results": [
    {
      "content": "To reset your password, go to Settings > Security > Reset Password...",
      "score": 0.92,
      "metadata": {
        "source": "help-center",
        "key": "password-reset"
      }
    }
  ]
}
```

---

### Get Knowledge Job Status

```bash
curl -X GET "https://api.emotionmachine.ai/v1/knowledge-jobs/{job_id}" \
  -H "Authorization: Bearer em_your_api_key"
```

**Response** `200 OK`
```json
{
  "id": "aa0e8400-e29b-41d4-a716-446655440000",
  "project_id": "123e4567-e89b-12d3-a456-426614174000",
  "companion_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "completed",
  "created_at": "2024-01-15T10:30:00Z",
  "completed_at": "2024-01-15T10:31:00Z"
}
```

---

## Images in Chat

The Images API allows users to upload images to conversations. Images are processed by a vision model (Gemini 2.0 Flash) to extract descriptions, which are then injected into the LLM context. This enables any text LLM to "understand" images without native vision capabilities.

### Upload Image

Upload an image to a conversation. The image is stored in S3 and automatically described by a vision model.

```bash
curl -X POST "https://api.emotionmachine.ai/v1/companions/{companion_id}/conversations/{conversation_id}/images" \
  -H "Authorization: Bearer em_your_api_key" \
  -F "file=@photo.jpg"
```

**Supported Formats:** JPEG, PNG, WebP, GIF

**Max File Size:** 10 MB

**Response** `201 Created`
```json
{
  "image_id": "bb0e8400-e29b-41d4-a716-446655440000",
  "description": "A photograph showing a sunset over the ocean with orange and pink clouds...",
  "storage_url": "https://s3.amazonaws.com/...",
  "mime_type": "image/jpeg",
  "width": 1920,
  "height": 1080
}
```

**Response Fields**
| Field | Type | Description |
|-------|------|-------------|
| image_id | uuid | Unique identifier for the image |
| description | string | AI-extracted description of the image content |
| storage_url | string | Presigned URL to view the image (expires in 1 hour) |
| mime_type | string | MIME type of the uploaded image |
| width | int | Image width in pixels |
| height | int | Image height in pixels |

---

### List Conversation Images

Retrieve all images uploaded to a conversation.

```bash
curl -X GET "https://api.emotionmachine.ai/v1/companions/{companion_id}/conversations/{conversation_id}/images" \
  -H "Authorization: Bearer em_your_api_key"
```

**Response** `200 OK`
```json
[
  {
    "image_id": "bb0e8400-e29b-41d4-a716-446655440000",
    "description": "A photograph showing a sunset...",
    "storage_url": "https://s3.amazonaws.com/...",
    "mime_type": "image/jpeg",
    "width": 1920,
    "height": 1080
  }
]
```

---

### Using Images in Chat

After uploading images, include their IDs in your chat request to provide image context to the LLM:

```bash
curl -X POST "https://api.emotionmachine.ai/v1/companions/{companion_id}/chat" \
  -H "Authorization: Bearer em_your_api_key" \
  -H "Content-Type: application/json" \
  -d '{
    "external_user_id": "user-123",
    "conversation_id": "660e8400-e29b-41d4-a716-446655440000",
    "message": "What do you see in this image?",
    "image_ids": ["bb0e8400-e29b-41d4-a716-446655440000"]
  }'
```

The image descriptions are automatically injected into the system prompt, allowing the LLM to respond with awareness of the image content.

**Note:** The `image_ids` parameter is also supported in the streaming chat endpoint (`/chat/stream`).

### Typical Flow

Image upload requires an existing conversation. Use this flow:

```bash
# 1. Create a conversation
curl -X POST ".../v1/companions/{companion_id}/conversations" \
  -H "Authorization: Bearer em_your_api_key" \
  -H "Content-Type: application/json" \
  -d '{"external_user_id": "user-123"}'
# Response: {"conversation_id": "..."}

# 2. Upload image to that conversation
curl -X POST ".../v1/companions/{companion_id}/conversations/{conversation_id}/images" \
  -H "Authorization: Bearer em_your_api_key" \
  -F "file=@photo.jpg"
# Response includes image_id

# 3. Send message referencing the image
curl -X POST ".../v1/companions/{companion_id}/chat" \
  -H "Authorization: Bearer em_your_api_key" \
  -H "Content-Type: application/json" \
  -d '{"external_user_id": "user-123", "conversation_id": "...", "message": "What do you think of this?", "image_ids": ["..."]}'
```

---

## Client Audio Implementation Guide

### Overview

Building a voice client requires handling:
1. **Microphone capture** → resample to target rate → encode to Int16 → send via WebSocket
2. **WebSocket receive** → decode Int16 → resample to browser rate → schedule playback with jitter buffer

### Microphone Capture

```javascript
// 1. Request microphone with preferred settings
const stream = await navigator.mediaDevices.getUserMedia({
  audio: {
    sampleRate: 16000,        // Hint to browser (may not be honored)
    channelCount: 1,
    echoCancellation: true,
    noiseSuppression: true,
    autoGainControl: true,
  }
});

// 2. Create AudioContext and check actual sample rate
const audioContext = new AudioContext();
console.log('Native rate:', audioContext.sampleRate); // Often 44100 or 48000

// 3. If native rate differs from target, resample before sending
// For STT-LLM-TTS: resample from browserRate → 16000Hz
```

### Resampling (Linear Interpolation)

Use synchronous linear interpolation for low-latency resampling:

```javascript
function resample(input, fromRate, toRate) {
  if (fromRate === toRate) return input;

  const ratio = fromRate / toRate;
  const outputLength = Math.round(input.length / ratio);
  const output = new Float32Array(outputLength);

  for (let i = 0; i < outputLength; i++) {
    const t = i * ratio;
    const i0 = Math.floor(t);
    const i1 = Math.min(i0 + 1, input.length - 1);
    const w = t - i0;
    output[i] = (1 - w) * input[i0] + w * input[i1];
  }
  return output;
}
```

### PCM Conversion

```javascript
// Float32 (-1 to 1) → Int16 (for sending to server)
function floatToInt16(float32) {
  const int16 = new Int16Array(float32.length);
  for (let i = 0; i < float32.length; i++) {
    const s = Math.max(-1, Math.min(1, float32[i]));
    int16[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
  }
  return int16;
}

// Int16 → Float32 (for playback from server)
function int16ToFloat(int16) {
  const float32 = new Float32Array(int16.length);
  for (let i = 0; i < int16.length; i++) {
    float32[i] = int16[i] / 0x8000;
  }
  return float32;
}
```

### Audio Playback with Jitter Buffer

The jitter buffer ensures smooth playback despite network variance:

```javascript
class AudioPlayback {
  constructor(audioContext, serverSampleRate = 24000) {
    this.ctx = audioContext;
    this.serverRate = serverSampleRate;
    this.playbackTime = audioContext.currentTime;
    this.LEAD_BUFFER = 0.15; // 150ms jitter buffer
  }

  reset() {
    this.playbackTime = this.ctx.currentTime;
  }

  enqueue(pcmBuffer) {
    // 1. Decode Int16 → Float32
    const int16 = new Int16Array(pcmBuffer);
    if (!int16.length) return;

    const float32 = int16ToFloat(int16);

    // 2. Resample: server rate (24kHz) → browser AudioContext rate
    const resampled = (this.serverRate === this.ctx.sampleRate)
      ? float32
      : resample(float32, this.serverRate, this.ctx.sampleRate);

    // 3. Create buffer at browser's native sample rate
    const buffer = this.ctx.createBuffer(1, resampled.length, this.ctx.sampleRate);
    buffer.getChannelData(0).set(resampled);

    // 4. Schedule with jitter buffer logic
    const source = this.ctx.createBufferSource();
    source.buffer = buffer;
    source.connect(this.ctx.destination);

    const now = this.ctx.currentTime;

    // If we fell behind, jump ahead with safety buffer
    if (this.playbackTime < now) {
      this.playbackTime = now + this.LEAD_BUFFER;
    }

    source.start(this.playbackTime);
    this.playbackTime += buffer.duration;
  }
}
```

---

## Turn Detection

The WebSocket currently sends only raw audio. Turn detection must be implemented client-side.

### Recommended Approach

| State | Detection Method |
|-------|-----------------|
| **User speaking** | Voice Activity Detection (VAD) on microphone input amplitude |
| **User stopped** | Silence threshold exceeded (e.g., 800ms below amplitude threshold) |
| **Processing** | Time between user stopped and first server audio received |
| **Companion speaking** | Receiving audio data from WebSocket |
| **Companion stopped** | No audio received for ~300ms |

### Simple VAD Implementation

```javascript
function calculateAmplitude(float32Array) {
  let sum = 0;
  for (let i = 0; i < float32Array.length; i += 8) { // Sample every 8th value
    sum += float32Array[i] * float32Array[i];
  }
  return Math.sqrt(sum / (float32Array.length / 8));
}

const SPEAKING_THRESHOLD = 0.08;
const SILENCE_DURATION = 800; // ms

let silenceTimer = null;
let isSpeaking = false;

function detectVoiceActivity(audioData) {
  const amplitude = calculateAmplitude(audioData);

  if (amplitude > SPEAKING_THRESHOLD) {
    isSpeaking = true;
    if (silenceTimer) {
      clearTimeout(silenceTimer);
      silenceTimer = null;
    }
  } else if (isSpeaking && !silenceTimer) {
    silenceTimer = setTimeout(() => {
      isSpeaking = false;
      // User stopped speaking - trigger processing state
    }, SILENCE_DURATION);
  }
}
```

---

## Quick Start Example

### Minimal Voice Client (Browser)

A complete working example in vanilla JavaScript:

```html
<!DOCTYPE html>
<html>
<head>
  <title>Emotion Machine Voice Demo</title>
</head>
<body>
  <button id="start">Start Voice Chat</button>
  <button id="stop" disabled>Stop</button>
  <div id="status">Click Start to begin</div>

  <script>
    // Configuration
    const API_KEY = 'em_your_api_key_here';
    const COMPANION_ID = 'your-companion-uuid-here';
    const API_BASE = 'https://api.emotionmachine.ai/v1';

    // Audio utilities
    function resample(input, fromRate, toRate) {
      if (fromRate === toRate) return input;
      const ratio = fromRate / toRate;
      const outputLength = Math.round(input.length / ratio);
      const output = new Float32Array(outputLength);
      for (let i = 0; i < outputLength; i++) {
        const t = i * ratio;
        const i0 = Math.floor(t);
        const i1 = Math.min(i0 + 1, input.length - 1);
        const w = t - i0;
        output[i] = (1 - w) * input[i0] + w * input[i1];
      }
      return output;
    }

    function floatToInt16(float32) {
      const int16 = new Int16Array(float32.length);
      for (let i = 0; i < float32.length; i++) {
        const s = Math.max(-1, Math.min(1, float32[i]));
        int16[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
      }
      return int16;
    }

    function int16ToFloat(int16) {
      const float32 = new Float32Array(int16.length);
      for (let i = 0; i < int16.length; i++) {
        float32[i] = int16[i] / 0x8000;
      }
      return float32;
    }

    // State
    let ws = null;
    let audioCtx = null;
    let stream = null;
    let processor = null;
    let playbackTime = 0;
    const LEAD_BUFFER = 0.15;

    // UI elements
    const startBtn = document.getElementById('start');
    const stopBtn = document.getElementById('stop');
    const status = document.getElementById('status');

    startBtn.onclick = async () => {
      try {
        status.textContent = 'Creating session...';

        // 1. Create session via REST API
        const sessionRes = await fetch(`${API_BASE}/sessions`, {
          method: 'POST',
          headers: {
            'Authorization': `Bearer ${API_KEY}`,
            'Content-Type': 'application/json'
          },
          body: JSON.stringify({
            companionId: COMPANION_ID,
            voiceConfig: {
              pipeline_type: 'stt-llm-tts',
              stt_provider: 'deepgram',
              llm_provider: 'openai-gpt4o-mini',
              tts_provider: 'elevenlabs',
              voice_name: 'Matilda'
            }
          })
        });

        if (!sessionRes.ok) {
          throw new Error(`Session creation failed: ${sessionRes.status}`);
        }

        const { ws_url } = await sessionRes.json();
        status.textContent = 'Connecting...';

        // 2. Setup audio context (use browser's native sample rate)
        audioCtx = new AudioContext();
        await audioCtx.resume();
        playbackTime = audioCtx.currentTime;
        console.log(`AudioContext sample rate: ${audioCtx.sampleRate}Hz`);

        // 3. Connect WebSocket
        ws = new WebSocket(ws_url);
        ws.binaryType = 'arraybuffer';

        ws.onopen = async () => {
          status.textContent = 'Connected! Speak now...';
          startBtn.disabled = true;
          stopBtn.disabled = false;

          // 4. Start microphone capture
          stream = await navigator.mediaDevices.getUserMedia({
            audio: {
              echoCancellation: true,
              noiseSuppression: true,
              autoGainControl: true
            }
          });

          const source = audioCtx.createMediaStreamSource(stream);
          processor = audioCtx.createScriptProcessor(4096, 1, 1);

          processor.onaudioprocess = (e) => {
            if (ws.readyState !== WebSocket.OPEN) return;

            const input = e.inputBuffer.getChannelData(0);
            // Resample to 16kHz for STT
            const resampled = resample(input, audioCtx.sampleRate, 16000);
            const int16 = floatToInt16(resampled);
            ws.send(int16.buffer);
          };

          source.connect(processor);
          processor.connect(audioCtx.destination);
        };

        ws.onmessage = (e) => {
          if (!(e.data instanceof ArrayBuffer)) return;

          // Play received audio (24kHz from server)
          const int16 = new Int16Array(e.data);
          if (!int16.length) return;

          const float32 = int16ToFloat(int16);
          // Resample from 24kHz to browser's sample rate
          const resampled = resample(float32, 24000, audioCtx.sampleRate);

          const buffer = audioCtx.createBuffer(1, resampled.length, audioCtx.sampleRate);
          buffer.getChannelData(0).set(resampled);

          const source = audioCtx.createBufferSource();
          source.buffer = buffer;
          source.connect(audioCtx.destination);

          const now = audioCtx.currentTime;
          if (playbackTime < now) {
            playbackTime = now + LEAD_BUFFER;
          }

          source.start(playbackTime);
          playbackTime += buffer.duration;

          status.textContent = 'Companion speaking...';
        };

        ws.onerror = (e) => {
          console.error('WebSocket error:', e);
          status.textContent = 'Connection error';
        };

        ws.onclose = () => {
          status.textContent = 'Disconnected';
          cleanup();
        };

      } catch (err) {
        console.error('Error:', err);
        status.textContent = `Error: ${err.message}`;
        cleanup();
      }
    };

    stopBtn.onclick = () => {
      cleanup();
      status.textContent = 'Stopped';
    };

    function cleanup() {
      processor?.disconnect();
      stream?.getTracks().forEach(t => t.stop());
      ws?.close();
      audioCtx?.close();
      ws = null;
      audioCtx = null;
      stream = null;
      processor = null;
      startBtn.disabled = false;
      stopBtn.disabled = true;
    }
  </script>
</body>
</html>
```

---

## Troubleshooting

### Audio Playback Issues

| Symptom | Cause | Solution |
|---------|-------|----------|
| Audio plays slow (slo-mo effect) | Playing 24kHz audio at wrong rate | Resample server audio (24kHz) to browser's native rate |
| Audio plays fast (chipmunk effect) | Playing 16kHz audio at higher rate | Check you're using correct sample rate (24kHz for playback) |
| Crackling/popping sounds | Missing jitter buffer | Add 100-200ms lead buffer when scheduling audio |
| Gaps in audio | Packets arriving late | Increase jitter buffer size |
| No audio at all | AudioContext suspended | Call `audioContext.resume()` after user gesture |
| Distorted audio | Incorrect Int16 conversion | Use `/ 0x8000` for decoding, not `/ 32768` |

### WebSocket Issues

| Symptom | Cause | Solution |
|---------|-------|----------|
| Connection refused | Token already used | `ws_url` token is one-time; create new session |
| CORS error on session create | Origin not allowed | Add your domain to API CORS whitelist |
| Connection drops | Inactivity timeout | Send periodic audio (even silence) or implement ping |

### Common Mistakes

1. **Using wrong sample rates:**
   - STT-LLM-TTS input: **16kHz** (not 24kHz)
   - STT-LLM-TTS output: **24kHz** (not 16kHz)

2. **Not resampling:**
   - Browser mic is often 44.1kHz or 48kHz
   - Server expects specific rates
   - Always resample before sending and after receiving

3. **Creating AudioContext at wrong rate:**
   - Don't force `new AudioContext({ sampleRate: 24000 })`
   - Use browser's native rate: `new AudioContext()`
   - Resample to/from server rates as needed

4. **Forgetting to set binaryType:**
   ```javascript
   ws.binaryType = 'arraybuffer'; // Required!
   ```

---

## Error Responses

All endpoints return standard HTTP error codes:

**401 Unauthorized**
```json
{
  "detail": "Missing API key"
}
```

**404 Not Found**
```json
{
  "detail": "Companion not found"
}
```

**409 Conflict**
```json
{
  "detail": "Cannot update an active session. Please stop the session first."
}
```

**422 Unprocessable Entity**
```json
{
  "detail": "STT, LLM, and TTS providers must be specified for stt-llm-tts pipeline"
}
```

**500 Internal Server Error**
```json
{
  "detail": "An unexpected error occurred"
}
```
