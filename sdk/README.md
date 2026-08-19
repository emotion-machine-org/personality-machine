# Voice Chat SDK (reference implementation)

A modular, TypeScript-first SDK for integrating real-time voice conversations with companions.

This package is a **reference implementation and demo app** (Vite + React) — it is not currently published to npm. Copy the `src/lib` code into your project, or run the demo directly:

```bash
npm install
# create .env with VITE_EM_API_KEY and VITE_EM_COMPANION_ID
npm run dev
```

It provides a robust foundation for handling WebSocket audio streaming, bidirectional voice communication, and session management. It handles the heavy lifting of AudioContexts and buffering while leaving the UI and higher-level logic completely in your control.

## 🚀 Quick Start

The entry point is the CompanionClient. This Facade handles authentication and orchestrates the underlying audio engines. The [EmotionVoiceChat](src/features/EmotionVoiceChat/container/VoiceChat) is the UI implementation using React for the CompanionClient.

```js
import { CompanionClient } from './lib/voice/CompanionClient';

// 1. Initialize with your API Key
const client = new CompanionClient({
  apiKey: 'emk_live_xxxxxxxx',
  // baseUrl: '[https://api.emotionmachine.ai](https://api.emotionmachine.ai)', // Optional override
});

// 2. Listen to state changes
client.addEventListener('companion:state_change', (event) => {
  const { newState } = event.detail;
  console.log(`Status: ${newState}`); 
  // 'init' -> 'connecting' -> 'connected' -> 'closed' and 'error'
});

// 3. Start a new conversation
const startButton = document.getElementById('start-btn');
startButton.onclick = async () => {
  try {
    const conversationId = await client.startConversation('companion-id-123', {
      voiceConfig: {
        pipeline_type: 'openai-realtime',
        voice_name: 'alloy',
        temperature: 0.7,
        realtimeModel: 'gpt-realtime-mini-2025-10-06'
      }
    });
    console.log(`Started conversation: ${conversationId}`);
  } catch (err) {
    console.error('Failed to start:', err);
  }
};
```

## 📚 CompanionClient API
The CompanionClient class is the primary interface for managing voice sessions. It extends TypedEventTarget, allowing for type-safe event listening.

### Constructor

```typescript
new CompanionClient(options: CompanionClientOptions)
```

### CompanionClientOptions

| Property   |  Type  |                                                        Description |
|:-----------|:------:|-------------------------------------------------------------------:|
| apiKey     | string |                              Required. Your EmotionMachine api key |
| baseUrl    | string | Optional API endpoint (defaults to https://api.emotionmachine.ai). |
| sampleRate | number |                 Optional audio sampling rate (defaults to 24000Hz) |

### Audio Sample Rates

The SDK uses a single `sampleRate` for both input and output (defaults to 24kHz).

| Pipeline | Input Rate | Output Rate | SDK Support |
|----------|------------|-------------|-------------|
| `openai-realtime` | 24kHz | 24kHz | ✅ Full support |
| `stt-llm-tts` | 16kHz | 24kHz | ⚠️ See note below |

> **Note for STT-LLM-TTS:** The server expects 16kHz input but sends 24kHz output. The SDK currently uses a single sample rate for both directions. For full STT-LLM-TTS support, either:
> - Set `sampleRate: 16000` (input will be correct, but playback needs manual resampling from 24kHz)
> - Use the lower-level [API directly](../server/API_V1_REFERENCE.md#client-audio-implementation-guide) with separate input/output handling

### Methods

`startConversation`

Creates a new session with the specified companion and immediately connects.
```typescript
startConversation(
  companionId: string, 
  conversationOptions: ConversationOptions
): Promise<string>
```

- Returns: A Promise resolving to the conversation_id.
- conversationOptions: Configuration for the session (see below).

`joinConversation`

Joins an existing active conversation.
```typescript
startConversation(
  companionId: string, 
  conversationOptions: ConversationOptions
): Promise<string>
```
`disconnect`

Closes the WebSocket connection and releases the microphone.

### Configuration Types

`ConversationOptions` Used to configure the voice session.

```typescript
interface ConversationOptions {
  conversationId?: string; // Optional: resume an existing conversation
  voiceConfig?: {
    pipeline_type: string; // e.g., "openai-realtime"
    voice_name: string;    // e.g., "alloy"
    temperature: number;   // 0.0 to 1.0
    realtimeModel: string; // e.g., "gpt-realtime-mini-2025-10-06"
  };
}
```

> **Note:** The system prompt is configured on the companion itself via the dashboard or API, not passed per-session.

### Events

The client dispatches the following events. You can listen to them using addEventListener.

`companion:state_change`
Emitted whenever the connection status changes.
- Detail: { newState: CompanionClientStatus }
- Statuses:
  - **init**: Client initialized.
  - **ready**: Authenticated and ready to connect.
  - **connecting**: Establishing WebSocket connection.
  - **connected**: Audio stream is active.
  - **closed**: Connection terminated.
  - **error**: An error occurred.

`error`
Emitted when an exception occurs (e.g., authentication failure).
- Detail: { error: Error }
