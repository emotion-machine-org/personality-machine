'use client';

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import { useAuth } from '@clerk/nextjs';
import { useWebSocketAudio } from './useWebSocketAudio';
import { API_CONFIG } from '@/lib/config';

// Types matching the server-side configuration
// Note: 'openai-realtime' is kept for backwards compatibility with legacy configs
// but the server will silently convert it to 'stt-llm-tts'
export type PipelineType = 'openai-realtime' | 'stt-llm-tts';
export type STTProvider = 'openai' | 'elevenlabs' | 'cartesia' | 'ultravox' | 'deepgram';
export type LLMProvider =
  | 'openai-gpt4o'
  | 'openai-gpt4o-mini'
  | 'openai-gpt5.1'
  | 'claude-sonnet-3.7'
  | 'claude-sonnet-3.5'
  | 'claude-haiku-4.5'
  | 'claude-sonnet-4'
  | 'claude-sonnet-4.5'
  | 'claude-sonnet-4.6'
  | 'claude-opus-4'
  | 'claude-opus-4.5'
  | 'claude-opus-4.6'
  | 'gemini'
  | 'gemini-2.5-flash'
  | 'gemini-3-flash'
  | 'gemini-3.1-flash-lite-preview'
  | 'fast-brain'
  | 'local-vllm-qwen'
  | 'moonshot-kimi-k2'
  | 'moonshot-kimi-k2.5';
export type TTSProvider = 'openai' | 'elevenlabs' | 'cartesia';

export interface VoiceConfig {
  pipeline_type: PipelineType;
  voice_name?: string; // Voice name only - server handles ID mapping
  stt_provider?: STTProvider;
  llm_provider?: LLMProvider;
  tts_provider?: TTSProvider;
  tts_voice_id?: string;
  elevenlabs_model_id?: string;
  elevenlabs_stability?: number;
  elevenlabs_similarity_boost?: number;
  elevenlabs_style?: number;
  elevenlabs_speed?: number;
  elevenlabs_use_speaker_boost?: boolean;
  elevenlabs_language_code?: string | null;
  temperature?: number;
  background_noise_enabled?: boolean;
  background_noise_type?: string;
  background_noise_volume?: number;
  fast_brain_delegate_enabled?: boolean;
  fast_brain_end_call_enabled?: boolean;
}

export interface SessionConfig {
  systemPrompt: string;
  companionId: string;
  voiceConfig: VoiceConfig;
  // Optional stable builder test user id to scope memories across refresh
  clientExternalUserId?: string;
  // Optional existing conversation ID to continue (for text → voice transitions)
  conversationId?: string;
  // Use V2 relationship-based voice endpoints (default: true)
  useV2?: boolean;
  // Use DialogMachine simulate-token endpoint for voice testing
  useDialogmachine?: boolean;
  // Clear relationship profile before starting (useful for onboarding)
  clearProfile?: boolean;
  // Clear all conversation history before starting (useful for onboarding)
  clearMessages?: boolean;
}

const API_BASE = API_CONFIG.BASE_URL;

const DEFAULT_CONFIG: SessionConfig = {
  systemPrompt:
    'You are a helpful and friendly companion. Keep your responses conversational and engaging.',
  companionId: '',
  voiceConfig: {
    pipeline_type: 'stt-llm-tts',
    voice_name: 'Sarah',
    stt_provider: 'cartesia',
    llm_provider: 'claude-haiku-4.5',
    tts_provider: 'elevenlabs',
    temperature: 0.7,
  },
  useV2: true, // Default to V2 relationship-based endpoints
};

interface ConnectionState {
  isConnected: boolean;
  isConnecting: boolean;
  isPaused: boolean;
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export type JsonMessageHandler = (message: Record<string, any>) => void;

interface WebSocketSessionContextValue {
  isConnected: boolean;
  isConnecting: boolean;
  isPaused: boolean;
  isRecording: boolean;
  userAmplitude: number;
  companionAmplitude: number;
  isCompanionSpeaking: boolean;
  config: SessionConfig;
  setConfig: (partial: Partial<SessionConfig>) => void;
  toggleSession: () => void;
  startSession: (override?: Partial<SessionConfig>) => Promise<void>;
  stopSession: () => Promise<void>;
  pauseSession: () => Promise<void>;
  resumeSession: () => Promise<void>;
  sessionId: string | null;
  lastSessionId: string | null;
  // Register a callback for JSON messages from the WebSocket
  setOnJsonMessage: (handler: JsonMessageHandler | null) => void;
}

interface WebSocketSessionCoreContextValue {
  isConnected: boolean;
  isConnecting: boolean;
  isPaused: boolean;
  config: SessionConfig;
  setConfig: (partial: Partial<SessionConfig>) => void;
  toggleSession: () => void;
  startSession: (override?: Partial<SessionConfig>) => Promise<void>;
  stopSession: () => Promise<void>;
  pauseSession: () => Promise<void>;
  resumeSession: () => Promise<void>;
  sessionId: string | null;
  lastSessionId: string | null;
  // Register a callback for JSON messages from the WebSocket
  setOnJsonMessage: (handler: JsonMessageHandler | null) => void;
}

interface WebSocketSessionAudioContextValue {
  isRecording: boolean;
  userAmplitude: number;
  companionAmplitude: number;
  isCompanionSpeaking: boolean;
}

const WebSocketSessionCoreContext = createContext<WebSocketSessionCoreContextValue | null>(null);
const WebSocketSessionAudioContext = createContext<WebSocketSessionAudioContextValue | null>(null);

function useConnectionState(initial: ConnectionState) {
  const [state, setState] = useState<ConnectionState>(initial);
  const stateRef = useRef(state);

  useEffect(() => {
    stateRef.current = state;
  }, [state]);

  const update = useCallback((updates: Partial<ConnectionState>) => {
    setState(prev => {
      const next: ConnectionState = {
        isConnected: updates.isConnected ?? prev.isConnected,
        isConnecting: updates.isConnecting ?? prev.isConnecting,
        isPaused: updates.isPaused ?? prev.isPaused,
      };
      if (
        next.isConnected === prev.isConnected &&
        next.isConnecting === prev.isConnecting &&
        next.isPaused === prev.isPaused
      ) {
        return prev;
      }
      return next;
    });
  }, []);

  return { state, stateRef, update };
}

export function WebSocketSessionProvider({ children }: { children: ReactNode }) {
  const [config, setConfigState] = useState<SessionConfig>(DEFAULT_CONFIG);
  const configRef = useRef(config);
  useEffect(() => {
    configRef.current = config;
  }, [config]);

  const { state: connectionState, stateRef: connectionStateRef, update: updateConnectionState } =
    useConnectionState({ isConnected: false, isConnecting: false, isPaused: false });

  const {
    initializeAudio,
    startRecording,
    stopRecording,
    playAudio,
    cleanup: cleanupAudio,
    isInitialized: audioInitialized,
    isRecording,
    userAmplitude,
    companionAmplitude,
    isCompanionSpeaking,
  } = useWebSocketAudio();

  const [sessionId, setSessionId] = useState<string | null>(null);
  const [lastSessionId, setLastSessionId] = useState<string | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const jsonMessageHandlerRef = useRef<JsonMessageHandler | null>(null);
  const { getToken } = useAuth();

  const setOnJsonMessage = useCallback((handler: JsonMessageHandler | null) => {
    jsonMessageHandlerRef.current = handler;
  }, []);

  const setConfig = useCallback((partial: Partial<SessionConfig>) => {
    setConfigState(prev => {
      const nextVoice = partial.voiceConfig
        ? { ...prev.voiceConfig, ...partial.voiceConfig }
        : prev.voiceConfig;
      const next: SessionConfig = {
        ...prev,
        ...partial,
        voiceConfig: nextVoice,
      };
      const unchanged =
        next.systemPrompt === prev.systemPrompt &&
        next.companionId === prev.companionId &&
        next.clientExternalUserId === prev.clientExternalUserId &&
        next.conversationId === prev.conversationId &&
        next.useV2 === prev.useV2 &&
        next.useDialogmachine === prev.useDialogmachine &&
        next.clearProfile === prev.clearProfile &&
        next.clearMessages === prev.clearMessages &&
        JSON.stringify(next.voiceConfig) === JSON.stringify(prev.voiceConfig);
      if (unchanged) {
        return prev;
      }
      return next;
    });
  }, []);

  const startWebSocketSession = useCallback(
    async (effectiveConfig: SessionConfig) => {
      if (!effectiveConfig.companionId) {
        throw new Error('Companion ID is required to start a session');
      }

      const token = await getToken(
        process.env.NEXT_PUBLIC_CLERK_JWT_TEMPLATE
          ? { template: process.env.NEXT_PUBLIC_CLERK_JWT_TEMPLATE }
          : undefined
      );

      let ws_url: string;
      let session_id: string;

      // Use V2 relationship-based endpoints by default
      if (effectiveConfig.useV2 !== false && effectiveConfig.clientExternalUserId) {
        const tokenPath = effectiveConfig.useDialogmachine
          ? `/api/dialogmachine/companions/${effectiveConfig.companionId}/test-users/${encodeURIComponent(effectiveConfig.clientExternalUserId)}/simulate-token`
          : `/api/companions/${effectiveConfig.companionId}/test-users/${encodeURIComponent(effectiveConfig.clientExternalUserId)}/voice-token`;

        const requestBody = effectiveConfig.useDialogmachine
          ? { voice_config: effectiveConfig.voiceConfig }
          : {
              voice_config: effectiveConfig.voiceConfig,
              clearProfile: effectiveConfig.clearProfile,
              clearMessages: effectiveConfig.clearMessages,
            };

        const voiceTokenResponse = await fetch(`${API_BASE}${tokenPath}`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            ...(token ? { Authorization: `Bearer ${token}` } : {}),
          },
          body: JSON.stringify(requestBody),
        });

        if (!voiceTokenResponse.ok) {
          const errorText = await voiceTokenResponse.text();
          throw new Error(`Failed to get voice token: ${voiceTokenResponse.status} - ${errorText}`);
        }

        const voiceToken = await voiceTokenResponse.json();
        session_id = voiceToken.relationship_id;

        // Build full WebSocket URL with token
        const wsProtocol = API_BASE.startsWith('https') ? 'wss' : 'ws';
        const wsHost = API_BASE.replace(/^https?:\/\//, '');
        ws_url = `${wsProtocol}://${wsHost}${voiceToken.ws_url}?token=${voiceToken.token}`;
      } else {
        // V1 fallback: Use legacy /sessions/ endpoint

        const voiceName = effectiveConfig.voiceConfig.voice_name || 'alloy';
        const payload: Record<string, unknown> = {
          systemPrompt: effectiveConfig.systemPrompt,
          companionId: effectiveConfig.companionId,
          voiceConfig: effectiveConfig.voiceConfig,
          voice: voiceName,
        };
        if (effectiveConfig.clientExternalUserId) {
          payload.clientExternalUserId = effectiveConfig.clientExternalUserId;
        }
        if (effectiveConfig.conversationId) {
          payload.conversationId = effectiveConfig.conversationId;
        }

        const response = await fetch(`${API_BASE}/sessions/`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            ...(token ? { Authorization: `Bearer ${token}` } : {}),
          },
          body: JSON.stringify(payload),
        });

        if (!response.ok) {
          const errorText = await response.text();
          throw new Error(`Failed to create WebSocket session: ${response.status} - ${errorText}`);
        }

        const sessionData = await response.json();
        session_id = sessionData.id;
        ws_url = sessionData.ws_url;
      }

      setSessionId(session_id);
      setLastSessionId(session_id);

      const ws = new WebSocket(ws_url);
      ws.binaryType = 'arraybuffer';
      wsRef.current = ws;

      ws.onopen = async () => {
        try {
          await initializeAudio(effectiveConfig.voiceConfig.pipeline_type);
          await startRecording((pcm: Int16Array) => {
            if (ws.readyState === WebSocket.OPEN && pcm?.length) {
              ws.send(pcm.buffer);
            }
          });

          // Small delay to let first audio frame reach server before UI shows "Listening"
          // This helps VAD initialize but keeps UX snappy (companion-simulator works without delay)
          await new Promise(resolve => setTimeout(resolve, 100));

          updateConnectionState({ isConnected: true, isConnecting: false, isPaused: false });
        } catch {
          ws.close();
          updateConnectionState({ isConnected: false, isConnecting: false, isPaused: false });
        }
      };

      ws.onmessage = event => {
        if (event.data instanceof ArrayBuffer) {
          // Binary audio data
          playAudio(new Int16Array(event.data));
        } else if (typeof event.data === 'string') {
          // JSON message (tool_result, etc.)
          try {
            const message = JSON.parse(event.data);
            // Forward to custom handler (for tool_result, etc.)
            if (jsonMessageHandlerRef.current) {
              jsonMessageHandlerRef.current(message);
            }
          } catch {
            // Ignore malformed JSON messages
          }
        }
      };

      ws.onerror = () => {
        updateConnectionState({ isConnected: false, isConnecting: false, isPaused: false });
      };

      ws.onclose = () => {
        updateConnectionState({ isConnected: false, isConnecting: false, isPaused: false });
      };
    },
    [getToken, initializeAudio, startRecording, playAudio, updateConnectionState]
  );

  const startSession = useCallback(
    async (override?: Partial<SessionConfig>) => {
      // Prevent double connections
      const current = connectionStateRef.current;
      if (current.isConnected || current.isConnecting) {
        return;
      }

      const currentConfig = configRef.current;
      const effectiveConfig = {
        ...currentConfig,
        ...override,
        voiceConfig: {
          ...currentConfig.voiceConfig,
          ...(override?.voiceConfig || {}),
        },
      };

      updateConnectionState({ isConnecting: true, isPaused: false });

      try {
        if (!audioInitialized) {
          await initializeAudio(effectiveConfig.voiceConfig.pipeline_type);
        }

        await startWebSocketSession(effectiveConfig);
      } catch (err) {
        updateConnectionState({ isConnecting: false });
        throw err;
      }
    },
    [audioInitialized, initializeAudio, startWebSocketSession, updateConnectionState, connectionStateRef]
  );

  const stopSession = useCallback(async () => {
    updateConnectionState({ isConnecting: false, isPaused: false });

    try {
      stopRecording();

      if (wsRef.current) {
        wsRef.current.close();
        wsRef.current = null;
      }

      try {
        cleanupAudio();
      } catch {
        // noop
      }
    } finally {
      setSessionId(null);
      updateConnectionState({ isConnected: false, isPaused: false });
    }
  }, [cleanupAudio, stopRecording, updateConnectionState]);

  const pauseSession = useCallback(async () => {
    const current = connectionStateRef.current;
    if (current.isConnected && !current.isPaused) {
      stopRecording();
      updateConnectionState({ isPaused: true });
    }
  }, [stopRecording, updateConnectionState, connectionStateRef]);

  const resumeSession = useCallback(async () => {
    const current = connectionStateRef.current;
    if (current.isConnected && current.isPaused && wsRef.current) {
      try {
        if (!audioInitialized) {
          await initializeAudio(configRef.current.voiceConfig.pipeline_type);
        }
        await startRecording((pcm: Int16Array) => {
          if (wsRef.current?.readyState === WebSocket.OPEN && pcm?.length) {
            wsRef.current.send(pcm.buffer);
          }
        });
        updateConnectionState({ isPaused: false });
      } catch {
        // Resume failed - connection may be stale
      }
    }
  }, [audioInitialized, initializeAudio, startRecording, updateConnectionState, connectionStateRef]);

  const toggleSession = useCallback(() => {
    const current = connectionStateRef.current;
    if (current.isConnected || current.isConnecting) {
      void stopSession();
    } else {
      void startSession();
    }
  }, [startSession, stopSession, connectionStateRef]);

  useEffect(() => {
    return () => {
      stopRecording();
      cleanupAudio();

      if (wsRef.current) {
        wsRef.current.close();
        wsRef.current = null;
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const coreValue = useMemo<WebSocketSessionCoreContextValue>(
    () => ({
      isConnected: connectionState.isConnected,
      isConnecting: connectionState.isConnecting,
      isPaused: connectionState.isPaused,
      config,
      setConfig,
      toggleSession,
      startSession,
      stopSession,
      pauseSession,
      resumeSession,
      sessionId,
      lastSessionId,
      setOnJsonMessage,
    }),
    [
      connectionState.isConnected,
      connectionState.isConnecting,
      connectionState.isPaused,
      config,
      setConfig,
      toggleSession,
      startSession,
      stopSession,
      pauseSession,
      resumeSession,
      sessionId,
      lastSessionId,
      setOnJsonMessage,
    ]
  );

  const audioValue = useMemo<WebSocketSessionAudioContextValue>(
    () => ({
      isRecording,
      userAmplitude,
      companionAmplitude,
      isCompanionSpeaking,
    }),
    [isRecording, userAmplitude, companionAmplitude, isCompanionSpeaking]
  );

  return (
    <WebSocketSessionCoreContext.Provider value={coreValue}>
      <WebSocketSessionAudioContext.Provider value={audioValue}>
        {children}
      </WebSocketSessionAudioContext.Provider>
    </WebSocketSessionCoreContext.Provider>
  );
}

export function useWebSocketSessionCore(): WebSocketSessionCoreContextValue {
  const context = useContext(WebSocketSessionCoreContext);
  if (!context) {
    throw new Error('useWebSocketSessionCore must be used within a WebSocketSessionProvider');
  }
  return context;
}

export function useWebSocketSession(): WebSocketSessionContextValue {
  const core = useWebSocketSessionCore();
  const audio = useContext(WebSocketSessionAudioContext);

  if (!audio) {
    throw new Error('useWebSocketSession must be used within a WebSocketSessionProvider');
  }

  return useMemo(
    () => ({
      ...core,
      ...audio,
    }),
    [core, audio]
  );
}
