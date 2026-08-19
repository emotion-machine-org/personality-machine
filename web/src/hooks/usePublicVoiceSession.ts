'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useWebSocketAudio } from './useWebSocketAudio';
import { API_CONFIG } from '@/lib/config';

const API_BASE = API_CONFIG.BASE_URL;

export type PublicVoiceSessionPayload = {
  share_id: string;
  conversation_id: string;
  visitor_token: string;
  session_id: string;
  ws_url: string;
  pipeline_type: string;
};

interface UsePublicVoiceSessionOptions {
  slug: string;
  visitorToken: string | null;
  conversationId: string | null;
  onSessionReady?: (payload: PublicVoiceSessionPayload) => void;
  onError?: (message: string) => void;
}

interface UsePublicVoiceSessionReturn {
  isConnecting: boolean;
  isConnected: boolean;
  isRecording: boolean;
  isPaused: boolean;
  userAmplitude: number;
  companionAmplitude: number;
  isCompanionSpeaking: boolean;
  start: () => Promise<void>;
  stop: () => Promise<void>;
  pause: () => Promise<void>;
  resume: () => Promise<void>;
  error: string | null;
  sessionId: string | null;
}

export function usePublicVoiceSession(options: UsePublicVoiceSessionOptions): UsePublicVoiceSessionReturn {
  const { slug, visitorToken, conversationId, onSessionReady, onError } = options;
  const visitorTokenRef = useRef<string | null>(visitorToken ?? null);
  const conversationIdRef = useRef<string | null>(conversationId ?? null);
  const sessionIdRef = useRef<string | null>(null);
  const pipelineTypeRef = useRef<string>('stt-llm-tts');
  const websocketRef = useRef<WebSocket | null>(null);
  const chunkHandlerRef = useRef<((pcm: Int16Array) => void) | null>(null);

  const [isConnecting, setIsConnecting] = useState(false);
  const [isConnected, setIsConnected] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isPaused, setIsPaused] = useState(false);

  const {
    initializeAudio,
    startRecording,
    stopRecording,
    playAudio,
    cleanup: cleanupAudio,
    isInitialized,
    isRecording,
    userAmplitude,
    companionAmplitude,
    isCompanionSpeaking,
  } = useWebSocketAudio();

  useEffect(() => {
    visitorTokenRef.current = visitorToken ?? null;
  }, [visitorToken]);

  useEffect(() => {
    conversationIdRef.current = conversationId ?? null;
  }, [conversationId]);

  const handleError = useCallback(
    (message: string) => {
      setError(message);
      onError?.(message);
    },
    [onError]
  );

  const stop = useCallback(async () => {
    try {
      stopRecording();
    } catch {
      // noop
    }

    if (websocketRef.current) {
      try {
        websocketRef.current.close();
      } catch {
        // noop
      }
      websocketRef.current = null;
    }

    try {
      cleanupAudio();
    } catch {
      // noop
    }

    setIsConnecting(false);
    setIsConnected(false);
    setIsPaused(false);
    sessionIdRef.current = null;
  }, [cleanupAudio, stopRecording]);

  const handleAudioChunk = useCallback((pcm: Int16Array) => {
    if (websocketRef.current?.readyState === WebSocket.OPEN && pcm.length) {
      websocketRef.current.send(pcm.buffer);
    }
  }, []);

  useEffect(() => {
    chunkHandlerRef.current = handleAudioChunk;
  }, [handleAudioChunk]);

  const start = useCallback(async () => {
    if (isConnecting || isConnected) {
      return;
    }

    setError(null);
    setIsConnecting(true);

    try {
      const body: Record<string, unknown> = {};
      const currentToken = visitorTokenRef.current;
      const currentConversation = conversationIdRef.current;

      if (currentToken) {
        body.visitor_token = currentToken;
      }
      if (currentConversation) {
        body.conversation_id = currentConversation;
      }

      const response = await fetch(`${API_BASE}/public/companions/${slug}/sessions/voice`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });

      if (!response.ok) {
        const details = await response.text();
        throw new Error(details || `Failed to create voice session (${response.status})`);
      }

      const payload = (await response.json()) as PublicVoiceSessionPayload;
      visitorTokenRef.current = payload.visitor_token;
      conversationIdRef.current = payload.conversation_id;
      sessionIdRef.current = payload.session_id;
      pipelineTypeRef.current = payload.pipeline_type;
      onSessionReady?.(payload);

      if (!isInitialized) {
        await initializeAudio(payload.pipeline_type);
      }

      const ws = new WebSocket(payload.ws_url);
      ws.binaryType = 'arraybuffer';
      websocketRef.current = ws;

      ws.onopen = async () => {
        try {
          if (!isInitialized) {
            await initializeAudio(payload.pipeline_type);
          }
          await startRecording(chunkHandlerRef.current ?? handleAudioChunk);
          setIsConnected(true);
          setIsConnecting(false);
          setIsPaused(false);
        } catch (audioError) {
          handleError(audioError instanceof Error ? audioError.message : 'Audio initialization failed');
          setIsConnecting(false);
          try {
            ws.close();
          } catch {
            // noop
          }
        }
      };

      ws.onmessage = event => {
        if (event.data instanceof ArrayBuffer) {
          playAudio(new Int16Array(event.data));
        }
      };

      ws.onerror = () => {
        handleError('Voice session encountered a connection error.');
        void stop();
      };

      ws.onclose = () => {
        try {
          stopRecording();
        } catch {
          // noop
        }
        setIsConnected(false);
        setIsConnecting(false);
        setIsPaused(false);
      };
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to start voice session';
      handleError(message);
      setIsConnecting(false);
      throw err;
    }
  }, [
    handleError,
    initializeAudio,
    isConnected,
    isConnecting,
    isInitialized,
    onSessionReady,
    playAudio,
    slug,
    startRecording,
    stop,
    stopRecording,
    handleAudioChunk,
  ]);

  const pause = useCallback(async () => {
    if (!isConnected || isPaused) {
      return;
    }
    try {
      stopRecording();
      setIsPaused(true);
    } catch (err) {
      handleError(err instanceof Error ? err.message : 'Failed to pause voice session');
    }
  }, [handleError, isConnected, isPaused, stopRecording]);

  const resume = useCallback(async () => {
    if (!isConnected || !isPaused) {
      return;
    }
    try {
      if (!isInitialized) {
        await initializeAudio(pipelineTypeRef.current);
      }
      await startRecording(chunkHandlerRef.current ?? handleAudioChunk);
      setIsPaused(false);
    } catch (err) {
      handleError(err instanceof Error ? err.message : 'Failed to resume voice session');
    }
  }, [handleError, initializeAudio, isConnected, isInitialized, isPaused, startRecording, handleAudioChunk]);

  useEffect(() => {
    return () => {
      void stop();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const value = useMemo<UsePublicVoiceSessionReturn>(
    () => ({
      isConnecting,
      isConnected,
      isRecording,
      isPaused,
      userAmplitude,
      companionAmplitude,
      isCompanionSpeaking,
      start,
      stop,
      error,
      pause,
      resume,
      sessionId: sessionIdRef.current,
    }),
    [error, isConnected, isConnecting, isPaused, isRecording, userAmplitude, companionAmplitude, isCompanionSpeaking, pause, resume, start, stop]
  );

  return value;
}
