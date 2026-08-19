/**
 * Centralized configuration for the application.
 * Consolidates API endpoints, timeouts, and other constants.
 */

const resolveApiBaseUrl = () => {
  return process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8100';
};

export const API_CONFIG = {
  BASE_URL: resolveApiBaseUrl(),

  // Timeout configurations in milliseconds
  TIMEOUTS: {
    // WebSocket connection timeouts
    WEBSOCKET_CONNECT: 5000,

    // Audio system timeouts
    AUDIO_INIT: 200,

    // UI refresh intervals
    MESSAGE_REFRESH: 100,

    // User interaction debouncing
    VOICE_TOGGLE_DEBOUNCE: 500,

    // Session cleanup timeouts
    SESSION_CLEANUP_CHECK_INTERVAL: 50,
    SESSION_CLEANUP_MAX_WAIT: 2000, // 40 attempts * 50ms
    SESSION_CLEANUP_MAX_ATTEMPTS: 40,

    // State settling delays
    STATE_SETTLE_DELAY: 200,

    // Text mode refresh delay
    TEXT_MODE_REFRESH_DELAY: 200
  },

  // API endpoints
  ENDPOINTS: {
    SESSIONS: '/sessions',
    CONVERSATIONS: '/conversations',
    VOICE_MAPPINGS: '/sessions/voice-mappings'
  }
} as const;

// Voice configuration constants
export const VOICE_CONFIG = {
  DEFAULT_PIPELINE_TYPE: 'stt-llm-tts' as const,
  DEFAULT_VOICE: 'alloy' as const,
  DEFAULT_TEMPERATURE: 0.7,
  DEFAULT_STT_PROVIDER: 'openai' as const,
  DEFAULT_LLM_PROVIDER: 'openai-gpt4o' as const,
  DEFAULT_TTS_PROVIDER: 'openai' as const,
  DEFAULT_SYSTEM_PROMPT: 'You are a helpful and friendly companion. Keep your responses conversational and engaging.'
} as const;

// WebSocket configuration
export const WEBSOCKET_CONFIG = {
  BINARY_TYPE: 'arraybuffer' as const,
  READY_STATE: {
    CONNECTING: 0,
    OPEN: 1,
    CLOSING: 2,
    CLOSED: 3
  }
} as const;
