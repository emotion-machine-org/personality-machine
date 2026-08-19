/**
 * Hook for V2 relationship-based text chat via WebSocket.
 *
 * Provides real-time bidirectional text communication with:
 * - Token streaming (delta events)
 * - Proactive messages from behaviors
 * - Message persistence
 * - Reconnection with event replay
 */

import { useAuth } from '@clerk/nextjs';
import { useCallback, useEffect, useRef, useState } from 'react';
import { apiClient, isApiError } from '@/lib/api';
import { API_CONFIG } from '@/lib/config';

// WebSocket event types from server
export interface WsEvent {
  seq: number | null;
  timestamp: string;
  turn_id: string | null;
  type: string;
  data: Record<string, unknown>;
}

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  seq?: number;
  created_at?: string;
  is_proactive?: boolean;
}

export interface StatusEvent {
  id: string;
  stage: string;
  phase: 'start' | 'end';
  meta?: Record<string, unknown>;
  timestamp: number;
}

export interface TraceEvent {
  id: string;
  name: string;
  phase: 'start' | 'end' | 'info' | 'error';
  ts_ms: number;
  meta?: Record<string, unknown>;
  turn_id?: string;
}

interface UseRelationshipChatOptions {
  companionId: string | null;
  userId: string | null;
  debugMode?: boolean;
  onMessage?: (message: ChatMessage) => void;
  onProactiveMessage?: (message: ChatMessage) => void;
  onStatusChange?: (stage: string, phase: 'start' | 'end') => void;
  onTraceEvent?: (event: TraceEvent) => void;
  onError?: (error: Error) => void;
}

interface UseRelationshipChatReturn {
  // State
  isConnected: boolean;
  isConnecting: boolean;
  isStreaming: boolean;
  isAuthError: boolean;
  messages: ChatMessage[];
  streamingContent: string;
  statusEvents: StatusEvent[];
  traceEvents: TraceEvent[];
  error: Error | null;

  // Actions
  connect: () => Promise<void>;
  disconnect: () => void;
  sendMessage: (content: string) => Promise<void>;
  clearMessages: () => void;
}

export function useRelationshipChat({
  companionId,
  userId,
  debugMode = false,
  onMessage,
  onProactiveMessage,
  onStatusChange,
  onTraceEvent,
  onError,
}: UseRelationshipChatOptions): UseRelationshipChatReturn {
  const { getToken } = useAuth();

  // Connection state
  const [isConnected, setIsConnected] = useState(false);
  const [isConnecting, setIsConnecting] = useState(false);
  const [isStreaming, setIsStreaming] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const [isAuthError, setIsAuthError] = useState(false);

  // Message state
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [streamingContent, setStreamingContent] = useState('');
  const [statusEvents, setStatusEvents] = useState<StatusEvent[]>([]);
  const [traceEvents, setTraceEvents] = useState<TraceEvent[]>([]);

  // Refs for WebSocket and reconnection
  const wsRef = useRef<WebSocket | null>(null);
  const lastSeqRef = useRef<number>(0);
  const pendingMessageRef = useRef<string | null>(null);
  const reconnectTimeoutRef = useRef<NodeJS.Timeout | null>(null);
  const tokenRefreshTimeoutRef = useRef<NodeJS.Timeout | null>(null);
  const prevConnectionParamsRef = useRef<{ companionId: string | null; userId: string | null; debugMode: boolean } | null>(null);

  // Token expires in 1 hour (3600s), refresh 5 minutes before expiry
  const TOKEN_REFRESH_BUFFER_MS = 5 * 60 * 1000; // 5 minutes
  const TOKEN_EXPIRY_MS = 60 * 60 * 1000; // 1 hour

  // Helper to get auth token
  const getAuthToken = useCallback(async () => {
    const isAuthDisabled = process.env.NEXT_PUBLIC_DISABLE_AUTH === 'true';
    if (isAuthDisabled) return 'mock-dev-token';
    return getToken(
      process.env.NEXT_PUBLIC_CLERK_JWT_TEMPLATE
        ? { template: process.env.NEXT_PUBLIC_CLERK_JWT_TEMPLATE }
        : undefined
    );
  }, [getToken]);

  // Clean up on unmount
  useEffect(() => {
    return () => {
      if (wsRef.current) {
        wsRef.current.close(1000, 'Component unmounted');
        wsRef.current = null;
      }
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current);
      }
      if (tokenRefreshTimeoutRef.current) {
        clearTimeout(tokenRefreshTimeoutRef.current);
      }
    };
  }, []);

  // Reset when companion, user, or debug mode changes (but only if they actually changed)
  useEffect(() => {
    const prev = prevConnectionParamsRef.current;
    const hasChanged = prev && (
      prev.companionId !== companionId ||
      prev.userId !== userId ||
      prev.debugMode !== debugMode
    );

    // Only disconnect if params actually changed (not on initial mount)
    if (hasChanged && wsRef.current) {
      wsRef.current.close(1000, 'Connection parameters changed');
      wsRef.current = null;
      setIsConnected(false);
    }

    // Update the ref with current values
    prevConnectionParamsRef.current = { companionId, userId, debugMode };
  }, [companionId, userId, debugMode]);

  // Clear messages only when companion or user changes
  useEffect(() => {
    setMessages([]);
    setStreamingContent('');
    lastSeqRef.current = 0;
  }, [companionId, userId]);

  // Connect to WebSocket
  const connect = useCallback(async () => {
    if (!companionId || !userId) {
      setError(new Error('Companion and user ID required'));
      return;
    }

    // Don't connect if already connected or connecting
    if (wsRef.current?.readyState === WebSocket.OPEN ||
        wsRef.current?.readyState === WebSocket.CONNECTING) {
      return;
    }

    setIsConnecting(true);
    setError(null);
    setIsAuthError(false);

    try {
      const authToken = await getAuthToken();

      // Load message history first
      try {
        const history = await apiClient.getTestUserMessages(companionId, userId, 50, authToken);
        if (history.length > 0) {
          const historyMessages: ChatMessage[] = history.map((msg) => ({
            id: msg.id,
            role: msg.role as 'user' | 'assistant' | 'system',
            content: msg.content,
            created_at: msg.created_at,
          }));
          setMessages(historyMessages);
        }
      } catch (historyErr) {
        // Auth errors should stop connection entirely
        if (isApiError(historyErr) && historyErr.isAuthError) {
          setIsConnecting(false);
          setIsAuthError(true);
          setError(historyErr);
          onError?.(historyErr);
          return;
        }
        console.warn('[RELATIONSHIP_CHAT] Failed to load message history:', historyErr);
        // Continue anyway - history is optional for non-auth errors
      }

      // Get WebSocket token
      const tokenResponse = await apiClient.getTextWsToken(companionId, userId, authToken);

      // Build WebSocket URL
      const wsProtocol = API_CONFIG.BASE_URL.startsWith('https') ? 'wss' : 'ws';
      const wsHost = API_CONFIG.BASE_URL.replace(/^https?:\/\//, '');
      let wsUrl = `${wsProtocol}://${wsHost}${tokenResponse.ws_url}?token=${tokenResponse.token}`;
      if (lastSeqRef.current > 0) {
        wsUrl += `&since_seq=${lastSeqRef.current}`;
      }
      if (debugMode) {
        wsUrl += `&debug=true`;
      }

      const ws = new WebSocket(wsUrl);
      wsRef.current = ws;

      ws.onopen = () => {
        setIsConnected(true);
        setIsConnecting(false);
        setError(null);

        // Schedule token refresh before expiry
        if (tokenRefreshTimeoutRef.current) {
          clearTimeout(tokenRefreshTimeoutRef.current);
        }
        tokenRefreshTimeoutRef.current = setTimeout(() => {
          // Close current connection and reconnect with fresh token
          if (wsRef.current?.readyState === WebSocket.OPEN) {
            wsRef.current.close(1000, 'Token refresh');
            // Reconnection will happen in onclose handler
          }
        }, TOKEN_EXPIRY_MS - TOKEN_REFRESH_BUFFER_MS);
      };

      ws.onclose = (event) => {
        setIsConnected(false);
        setIsConnecting(false);
        wsRef.current = null;

        // Clear token refresh timer on close
        if (tokenRefreshTimeoutRef.current) {
          clearTimeout(tokenRefreshTimeoutRef.current);
          tokenRefreshTimeoutRef.current = null;
        }

        // Check if this was a token refresh close
        const isTokenRefresh = event.code === 1000 && event.reason === 'Token refresh';

        // Don't reconnect on normal close (unless token refresh) or if component is unmounting
        if ((event.code === 1000 && !isTokenRefresh) || event.code === 1001) {
          return;
        }

        // Reconnect for token refresh or unexpected disconnects
        // For token errors (4001, 4002), also reconnect to get fresh token
        const shouldReconnect = isTokenRefresh || event.code === 4001 || event.code === 4002 ||
          (event.code !== 1000 && event.code !== 1001);

        if (shouldReconnect) {
          // Immediate reconnect for token refresh, delay for other cases
          const delay = isTokenRefresh ? 0 : 3000;
          reconnectTimeoutRef.current = setTimeout(() => {
            connect();
          }, delay);
        }
      };

      ws.onerror = () => {
        setError(new Error('WebSocket connection failed'));
        onError?.(new Error('WebSocket connection failed'));
      };

      ws.onmessage = (event) => {
        try {
          const wsEvent: WsEvent = JSON.parse(event.data);
          handleWsEvent(wsEvent);
        } catch {
          console.error('Failed to parse WebSocket message:', event.data);
        }
      };
    } catch (err) {
      setIsConnecting(false);
      // Track auth errors to prevent reconnection loops
      if (isApiError(err) && err.isAuthError) {
        setIsAuthError(true);
      }
      setError(err instanceof Error ? err : new Error('Failed to connect'));
      onError?.(err instanceof Error ? err : new Error('Failed to connect'));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- handleWsEvent is stable and only used in ws.onmessage callback
  }, [companionId, userId, debugMode, getAuthToken, onError]);

  // Handle WebSocket events
  const handleWsEvent = useCallback(
    (event: WsEvent) => {
      // Track sequence for reconnection
      if (event.seq !== null) {
        lastSeqRef.current = Math.max(lastSeqRef.current, event.seq);
      }

      switch (event.type) {
        case 'connected':
          // Connection confirmed
          break;

        case 'ack': {
          // User message acknowledged - update the optimistic message with server ID
          const ackData = event.data as { message_id: string; client_message_id: string };
          if (pendingMessageRef.current === ackData.client_message_id) {
            // Update the optimistic message with the server's message_id and seq
            setMessages((prev) =>
              prev.map((msg) =>
                msg.id === ackData.client_message_id
                  ? { ...msg, id: ackData.message_id, seq: event.seq ?? undefined }
                  : msg
              )
            );
            pendingMessageRef.current = null;
          }
          break;
        }

        case 'status': {
          const statusData = event.data as { stage: string; phase: 'start' | 'end'; meta?: Record<string, unknown> };
          const statusEvent: StatusEvent = {
            id: `${statusData.stage}-${Date.now()}`,
            stage: statusData.stage,
            phase: statusData.phase,
            meta: statusData.meta,
            timestamp: Date.now(),
          };
          setStatusEvents((prev) => [...prev, statusEvent]);
          onStatusChange?.(statusData.stage, statusData.phase);
          break;
        }

        case 'delta': {
          // Streaming token
          const deltaData = event.data as { content: string };
          setStreamingContent((prev) => prev + deltaData.content);
          break;
        }

        case 'message': {
          // Complete assistant message
          const msgData = event.data as {
            id: string;
            role: 'assistant';
            content: string;
            created_at?: string;
          };
          const assistantMessage: ChatMessage = {
            id: msgData.id,
            role: 'assistant',
            content: msgData.content,
            seq: event.seq ?? undefined,
            created_at: msgData.created_at,
          };
          setMessages((prev) => [...prev, assistantMessage]);
          setStreamingContent('');
          setStatusEvents([]); // Clear status events when message is complete
          // Note: We keep trace events for debug display (cleared manually or on next send)
          setIsStreaming(false);
          onMessage?.(assistantMessage);
          break;
        }

        case 'proactive': {
          // Proactive message from behavior
          const proactiveData = event.data as {
            id: string;
            content: string;
            source_behavior_key?: string;
          };
          const proactiveMessage: ChatMessage = {
            id: proactiveData.id,
            role: 'assistant',
            content: proactiveData.content,
            seq: event.seq ?? undefined,
            is_proactive: true,
          };
          setMessages((prev) => [...prev, proactiveMessage]);
          onProactiveMessage?.(proactiveMessage);
          break;
        }

        case 'error': {
          const errorData = event.data as { message?: string; detail?: string };
          const errorMessage = errorData.message || errorData.detail || 'Unknown error';
          setError(new Error(errorMessage));
          setIsStreaming(false);
          setStreamingContent('');
          onError?.(new Error(errorMessage));
          break;
        }

        case 'trace': {
          // Debug trace event from context engine
          const traceData = event.data as {
            name: string;
            phase: 'start' | 'end' | 'info' | 'error';
            ts_ms: number;
            meta?: Record<string, unknown>;
          };
          const traceEvent: TraceEvent = {
            id: `${traceData.name}-${traceData.ts_ms}`,
            name: traceData.name,
            phase: traceData.phase,
            ts_ms: traceData.ts_ms,
            meta: traceData.meta,
            turn_id: event.turn_id ?? undefined,
          };
          setTraceEvents((prev) => [...prev, traceEvent]);
          onTraceEvent?.(traceEvent);
          break;
        }

        case 'heartbeat':
        case 'pong':
          // Keep-alive, no action needed
          break;

        default:
          console.log('Unhandled WebSocket event:', event.type, event);
      }
    },
    [onMessage, onProactiveMessage, onStatusChange, onTraceEvent, onError]
  );

  // Disconnect from WebSocket
  const disconnect = useCallback(() => {
    if (wsRef.current) {
      wsRef.current.close(1000, 'User disconnected');
      wsRef.current = null;
    }
    if (reconnectTimeoutRef.current) {
      clearTimeout(reconnectTimeoutRef.current);
      reconnectTimeoutRef.current = null;
    }
    if (tokenRefreshTimeoutRef.current) {
      clearTimeout(tokenRefreshTimeoutRef.current);
      tokenRefreshTimeoutRef.current = null;
    }
    setIsConnected(false);
    setIsConnecting(false);
  }, []);

  // Send a message
  const sendMessage = useCallback(
    async (content: string) => {
      if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) {
        throw new Error('WebSocket not connected');
      }

      const clientMessageId = crypto.randomUUID();
      pendingMessageRef.current = clientMessageId; // Store the client ID for ack matching

      // Optimistic UI: add user message immediately
      const optimisticMessage: ChatMessage = {
        id: clientMessageId,
        role: 'user',
        content,
      };
      setMessages((prev) => [...prev, optimisticMessage]);
      setTraceEvents([]); // Clear previous trace events for new turn
      setIsStreaming(true);

      const message = {
        type: 'user_message',
        client_message_id: clientMessageId,
        content,
      };

      wsRef.current.send(JSON.stringify(message));
    },
    []
  );

  // Clear all messages
  const clearMessages = useCallback(() => {
    setMessages([]);
    setStreamingContent('');
    setStatusEvents([]);
    setTraceEvents([]);
    lastSeqRef.current = 0;
  }, []);

  return {
    isConnected,
    isConnecting,
    isStreaming,
    isAuthError,
    messages,
    streamingContent,
    statusEvents,
    traceEvents,
    error,

    connect,
    disconnect,
    sendMessage,
    clearMessages,
  };
}
