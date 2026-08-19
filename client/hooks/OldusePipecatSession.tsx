import { useCallback, useRef, useState, useEffect } from 'react';
import { Alert } from 'react-native';
import React, { createContext, useContext } from 'react';
import { useMicStream } from './useMicStream';
import { useAudioPlayer } from './useAudioPlayer';

/* ── types ────────────────────────────────────────────── */
type SessionConfig = { systemPrompt: string; voice: string };

/* ── env helpers ──────────────────────────────────────── */
const API_BASE = process.env.EXPO_PUBLIC_API_URL ?? 'http://localhost:8101';

/* ── main hook ────────────────────────────────────────── */
export function usePipecatSession() {
  const [isPlaying, setPlaying] = useState(false);
  const [config, setConfigState] = useState<SessionConfig>({
    systemPrompt: 'You are a helpful and friendly companion. Keep your responses conversational and engaging.',
    voice: 'alloy',
  });
  const { playAudio, clearQueue } = useAudioPlayer();

  const wsRef = useRef<WebSocket | null>(null);
  const sessionIdRef = useRef<string | null>(null);

  // Initialize microphone streaming with smaller chunks for better quality
  useMicStream(wsRef.current, isPlaying, {
    sampleRate: 24000,
    chunkSize: 2048  // Reduced from 4096 for lower latency and better quality
  });

  const setConfig = (partial: Partial<SessionConfig>) =>
    setConfigState(prev => ({ ...prev, ...partial }));

  /* Start session with proper WebSocket handling for protobuf */
  const startSession = useCallback(
      async (override?: Partial<SessionConfig>) => {
        const effectiveConfig = { ...config, ...override };

        try {
          console.log('[SESSION] Starting with', effectiveConfig.systemPrompt);

          const response = await fetch(`${API_BASE}/sessions/`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              systemPrompt: effectiveConfig.systemPrompt,
              voice:        effectiveConfig.voice,
            }),
          });

      if (!response.ok) {
        const errorText = await response.text();
        throw new Error(`Failed to create session: ${response.status} - ${errorText}`);
      }

      const { id, ws_url } = await response.json();
      sessionIdRef.current = id;
      console.log('[SESSION] Created session:', id);
      console.log('[SESSION] Connecting to WebSocket:', ws_url);

      // 2. Connect to WebSocket with binary data support
      const ws = new WebSocket(ws_url);
      ws.binaryType = 'arraybuffer'; // Important for protobuf binary data
      wsRef.current = ws;

      ws.onopen = () => {
        console.log('[WS] Connected to Pipecat server (protobuf mode)');
        setPlaying(true);
      };

      ws.onmessage = (event) => {
        if (event.data instanceof ArrayBuffer) {
          console.log('[WS] Received binary data:', event.data.byteLength, 'bytes');
          playAudio(event.data);
        } else {
          console.log('[WS] Received text message:', event.data);
          try {
            const parsed = JSON.parse(event.data);
            console.log('[WS] Parsed message:', parsed);
          } catch (e) {
            console.log('[WS] Non-JSON text message:', event.data);
          }
        }
      };

      ws.onerror = (error) => {
        console.error('[WS] WebSocket error:', error);
        Alert.alert('Connection Error', 'Failed to connect to voice session');
        setPlaying(false);
      };

      ws.onclose = (event) => {
        console.log('[WS] WebSocket closed:', event.code, event.reason);
        // Only set playing to false if this wasn't a user-initiated close
        if (event.code !== 1000) {
          setPlaying(false);
        }
        wsRef.current = null;
        sessionIdRef.current = null;
      };

    } catch (err) {
      console.error('[SESSION] Error starting session:', err);
      Alert.alert('Session Error', String(err));
      setPlaying(false);
    }
  }, [config, playAudio]);

  /* Stop session and cleanup */
  const stopSession = useCallback(async () => {
    console.log('[SESSION] Stopping session');

    try {
      if (wsRef.current) {
        wsRef.current.close(1000, 'User stopped session');
      }

      clearQueue(); // Clear any pending audio

    } finally {
      wsRef.current = null;
      sessionIdRef.current = null;
      setPlaying(false);
    }
  }, [clearQueue]);

  const togglePlay = () => {
    if (isPlaying) {
      stopSession();
    } else {
      startSession();
    }
  };

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      // Direct cleanup without depending on stopSession function
      if (wsRef.current) {
        wsRef.current.close(1000, 'Component unmounting');
      }
      clearQueue();
      wsRef.current = null;
      sessionIdRef.current = null;
      setPlaying(false);
    };
  }, []);

  return {
    isPlaying,
    config,
    setConfig,
    togglePlay,
    startSession,          //  ←  add this
    stopSession,
    sessionId: sessionIdRef.current
  };
}

/* ── context wrapper ──────────────────────────────────── */
const PipecatSessionContext =
  createContext<ReturnType<typeof usePipecatSession> | null>(null);

export function PipecatSessionProvider({ children }: { children: React.ReactNode }) {
  const value = usePipecatSession();
  return (
    <PipecatSessionContext.Provider value={value}>
      {children}
    </PipecatSessionContext.Provider>
  );
}

export function usePipecatSessionCtx() {
  const ctx = useContext(PipecatSessionContext);
  if (!ctx) throw new Error('must be inside PipecatSessionProvider');
  return ctx;
}
