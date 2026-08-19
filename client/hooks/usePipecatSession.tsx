// client/hooks/usePipecatSession.tsx
// Updated to use OpenAI's audio handling

import { useCallback, useRef, useState, useEffect } from 'react';
import { Alert } from 'react-native';
import React, { createContext, useContext } from 'react';
import { useOpenAIAudio } from './useOpenAIAudio'; // ← New import

/* ── types ────────────────────────────────────────────── */
type SessionConfig = { systemPrompt: string; voice: string };

/* ── env helpers ──────────────────────────────────────── */
const API_BASE = process.env.EXPO_PUBLIC_API_URL ?? 'http://localhost:8100';

/* ── main hook ────────────────────────────────────────── */
export function usePipecatSession() {
  const [isPlaying, setPlaying] = useState(false);
  const [config, setConfigState] = useState<SessionConfig>({
    systemPrompt: 'You are a helpful and friendly companion. Keep your responses conversational and engaging.',
    voice: 'alloy',
  });

  // Replace old audio hooks with OpenAI's implementation
  const {
    initializeAudio,
    startRecording,
    stopRecording,
    playAudio,
    cleanup: cleanupAudio,
    isInitialized: audioInitialized
  } = useOpenAIAudio();

  const wsRef = useRef<WebSocket | null>(null);
  const sessionIdRef = useRef<string | null>(null);

  const setConfig = (partial: Partial<SessionConfig>) =>
    setConfigState(prev => ({ ...prev, ...partial }));

  /* Start session with OpenAI audio handling */
  const startSession = useCallback(
    async (override?: Partial<SessionConfig>) => {
      const effectiveConfig = { ...config, ...override };

      try {
        console.log('[SESSION] Starting with OpenAI audio...', effectiveConfig.systemPrompt);

        // 1. Initialize audio first (following OpenAI's pattern)
        if (!audioInitialized) {
          await initializeAudio();
        }

        // 2. Create session with your backend (no changes here)
        const response = await fetch(`${API_BASE}/sessions/`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            systemPrompt: effectiveConfig.systemPrompt,
            voice: effectiveConfig.voice,
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

        // 3. Connect to WebSocket (your backend, no changes)
        const ws = new WebSocket(ws_url);
        ws.binaryType = 'arraybuffer';
        wsRef.current = ws;

        ws.onopen = async () => {
          console.log('[WS] Connected to Pipecat server');

          // 4. Start recording using OpenAI's proven method
          try {
            await startRecording((pcm: Int16Array) => {
              //console.log('[DEBUG] onAudioData got →', pcm);
              if (ws.readyState !== WebSocket.OPEN || !pcm?.length) {
                console.log('[DEBUG] sending', pcm.length);     // <-- C
                ws.send(pcm.buffer);
              }
              ws.send(pcm.buffer);                      // raw PCM16 → Pipecat
              console.log(`[WS] sent ${pcm.length} samples (${pcm.byteLength} bytes)`);
            });

            setPlaying(true);
            console.log('[AUDIO] Recording started with OpenAI audio tools');
          } catch (audioError) {
            console.error('[AUDIO] Failed to start recording:', audioError);
            Alert.alert('Audio Error', 'Failed to start microphone recording');
          }
        };

        ws.onmessage = (event) => {
            // binary = audio you should play
            if (event.data instanceof ArrayBuffer) {
              playAudio(new Int16Array(event.data));
              if (__DEV__) {
                console.log('[WS] received', event.data.byteLength, 'bytes');
              }
              return;
            }

            // text = optional debug / status messages from server
            console.log('[WS] text message:', event.data);
        };

        ws.onerror = (error) => {
          console.error('[WS] WebSocket error:', error);
          Alert.alert('Connection Error', 'Failed to connect to voice session');
          setPlaying(false);
        };

        ws.onclose = (event) => {
          console.log('[WS] WebSocket closed:', event.code, event.reason);
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
    },
    [config, initializeAudio, startRecording, playAudio, audioInitialized]
  );

  /* Stop session and cleanup */
  const stopSession = useCallback(async () => {
    console.log('[SESSION] Stopping session');

    try {
      // Stop recording using OpenAI method
      stopRecording();

      if (wsRef.current) {
        wsRef.current.close(1000, 'User stopped session');
      }

    } finally {
      wsRef.current = null;
      sessionIdRef.current = null;
      setPlaying(false);
    }
  }, [stopRecording]);

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
      // Cleanup audio and WebSocket
      stopRecording();
      cleanupAudio();

      if (wsRef.current) {
        wsRef.current.close(1000, 'Component unmounting');
      }
      wsRef.current = null;
      sessionIdRef.current = null;
      setPlaying(false);
    };
  }, [stopRecording, cleanupAudio]);

  return {
    isPlaying,
    config,
    setConfig,
    togglePlay,
    startSession,
    stopSession,
    sessionId: sessionIdRef.current
  };
}

/* ── context wrapper (unchanged) ──────────────────────── */
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
