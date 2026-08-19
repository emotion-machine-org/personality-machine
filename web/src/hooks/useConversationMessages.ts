/**
 * Hook for managing conversation messages and real-time updates.
 * Integrates with the conversation persistence system to load and send messages.
 */

import { useState, useCallback, useRef } from 'react';
import { useAuth } from '@clerk/nextjs';
import { subscribeTextStream, type TextStreamEvent } from '@/lib/sse';
import { API_CONFIG } from '@/lib/config';

const API_BASE = API_CONFIG.BASE_URL;

export interface Message {
  id: string;
  role: 'system' | 'user' | 'assistant';
  content: string;
  created_at: string;
  badges?: StreamingStatusEvent[]; // Persistent badges associated with this message
}

export interface MemoryHighlight {
  messageId: string;
  sender: string;
  preview: string;
  importance: number;
  memoryId?: string;
  state: 'queued' | 'stored' | 'pending';
  updatedAt: number;
}

export interface CompanionConfig {
  system_prompt: string;
  llm_provider: string;
  temperature: number;
}

// Narrowing helper for runtime validation without using 'any'
const isObject = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null;

export type StreamingStatusEvent = {
  stage: 'retrieving' | 'thinking' | 'ruminating' | 'memory_stored';
  phase: 'start' | 'end';
  meta?: Record<string, unknown> | null;
  at: number; // epoch ms
  persistent?: boolean; // If true, badge stays visible after streaming ends
};

interface UseConversationMessagesResult {
  messages: Message[];
  loading: boolean;
  error: string | null;
  sendMessage: (content: string, companionConfig: CompanionConfig) => Promise<void>;
  loadConversation: (conversationId: string) => Promise<boolean>;
  clearMessages: () => void;
  sendMessageToConversation: (conversationId: string, content: string, companionConfig: CompanionConfig, imageIds?: string[]) => Promise<void>;
  sendMessageToConversationStream: (conversationId: string, content: string, companionConfig: CompanionConfig, imageIds?: string[]) => Promise<{ abort: () => void } | void>;
  isStreaming: boolean;
  streamingStatus: string | null;
  stopStreaming: () => void;
  streamingEvents: StreamingStatusEvent[];
}

export function useConversationMessages(): UseConversationMessagesResult {
  const [messages, setMessages] = useState<Message[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { getToken } = useAuth();
  const [isStreaming, setIsStreaming] = useState(false);
  const [streamingStatus, setStreamingStatus] = useState<string | null>(null);
  const streamAbortRef = useRef<AbortController | null>(null);
  const [streamingEvents, setStreamingEvents] = useState<StreamingStatusEvent[]>([]);
  // Keep a ref in sync with streamingEvents for access in callbacks
  const streamingEventsRef = useRef<StreamingStatusEvent[]>([]);
  // Track seen SSE event ids to avoid duplicate status pills if a proxy or parser replays chunks
  const seenStatusIdsRef = useRef<Set<string>>(new Set());

  const loadConversation = useCallback(async (conversationId: string) => {
    if (!conversationId) {
      setMessages([]);
      return false;
    }

    setLoading(true);
    setError(null);
    let success = false;

    try {
      const token = await getToken(
        process.env.NEXT_PUBLIC_CLERK_JWT_TEMPLATE
          ? { template: process.env.NEXT_PUBLIC_CLERK_JWT_TEMPLATE }
          : undefined
      );
      const response = await fetch(`${API_BASE}/conversations/${conversationId}/messages`, {
        headers: {
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
      });

      if (!response.ok) {
        if (response.status === 404) {
          // Conversation doesn't exist yet or has no messages
          setMessages([]);
          return false;
        }
        throw new Error(`Failed to load conversation: ${response.statusText}`);
      }

      const data = await response.json();
      const isValid = (m: unknown): m is Message => {
        if (!isObject(m)) return false;
        return (
          typeof m['id'] === 'string' &&
          typeof m['role'] === 'string' &&
          typeof m['content'] === 'string' &&
          typeof m['created_at'] === 'string'
        );
      };
      const normalized = Array.isArray(data) ? (data as unknown[]).filter(isValid) : [];
      setMessages(normalized);
      success = true;
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'Failed to load conversation';
      setError(errorMessage);
      console.error('Error loading conversation:', err);
    } finally {
      setLoading(false);
    }
    return success;
  }, [getToken]);

    // eslint-disable-next-line @typescript-eslint/no-unused-vars
  const sendMessage = useCallback(async (content: string, _companionConfig: CompanionConfig) => {
    if (!content.trim()) return;

    // We need a conversation ID to send a message
    // This should be provided by the parent component that manages the current conversation
    throw new Error('sendMessage requires a conversation ID. Use sendMessageToConversation instead.');
  }, []);

  const sendMessageToConversation = useCallback(async (
    conversationId: string,
    content: string,
    companionConfig: CompanionConfig,
    imageIds?: string[]
  ) => {
    if ((!content.trim() && (!imageIds || imageIds.length === 0)) || !conversationId) return;

    // Add user message optimistically
    const tempUserMessage: Message = {
      id: `temp-${Date.now()}`,
      role: 'user',
      content,
      created_at: new Date().toISOString()
    };

    setMessages(prev => [...prev, tempUserMessage]);
    setError(null);

    try {
      const token = await getToken(
        process.env.NEXT_PUBLIC_CLERK_JWT_TEMPLATE
          ? { template: process.env.NEXT_PUBLIC_CLERK_JWT_TEMPLATE }
          : undefined
      );
      const response = await fetch(`${API_BASE}/conversations/${conversationId}/messages`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({
          content,
          system_prompt: companionConfig.system_prompt,
          llm_provider: companionConfig.llm_provider,
          temperature: companionConfig.temperature,
          image_ids: imageIds && imageIds.length > 0 ? imageIds : undefined,
        })
      });

      if (!response.ok) {
        throw new Error(`Failed to send message: ${response.statusText}`);
      }

      const data = await response.json();
      const isValid = (m: unknown): m is Message => {
        if (!isObject(m)) return false;
        return (
          typeof m['id'] === 'string' &&
          typeof m['role'] === 'string' &&
          typeof m['content'] === 'string' &&
          typeof m['created_at'] === 'string'
        );
      };

      const toAppend: Message[] = [data?.user_message, data?.assistant_message].filter(isValid);

      // Replace temp message with real messages from server, ignoring invalid payloads
      setMessages(prev => {
        if (toAppend.length > 0) {
          const filtered = prev.filter(msg => msg.id !== tempUserMessage.id);
          return [...filtered, ...toAppend];
        }
        // If nothing to append, keep optimistic user message
        return prev;
      });

    } catch (err) {
      // Remove optimistic message on error
      setMessages(prev => prev.filter(msg => msg.id !== tempUserMessage.id));

      const errorMessage = err instanceof Error ? err.message : 'Failed to send message';
      setError(errorMessage);
      console.error('Error sending message:', err);
    }
  }, [getToken]);

  const stopStreaming = useCallback(() => {
    try {
      streamAbortRef.current?.abort();
    } catch {}
    setIsStreaming(false);
    setStreamingStatus(null);
    // Clear all streaming events when manually stopped
    streamingEventsRef.current = [];
    setStreamingEvents([]);
    seenStatusIdsRef.current.clear();
    // Remove empty draft bubble (keep partial text if any)
    setMessages(prev => prev.filter(m => !(m.id.startsWith('draft-') && (!m.content || m.content.trim().length === 0))));
  }, []);

  const sendMessageToConversationStream = useCallback(async (
    conversationId: string,
    content: string,
    companionConfig: CompanionConfig,
    imageIds?: string[]
  ) => {
    if ((!content.trim() && (!imageIds || imageIds.length === 0)) || !conversationId) return;

    // Optimistic user message
    const tempUser: Message = {
      id: `temp-${Date.now()}`,
      role: 'user',
      content,
      created_at: new Date().toISOString(),
    };
    // Assistant draft message
    const draftId = `draft-${Date.now()}`;
    const draftAssistant: Message = {
      id: draftId,
      role: 'assistant',
      content: '',
      created_at: new Date().toISOString(),
    };

    setMessages(prev => [...prev, tempUser, draftAssistant]);
    setError(null);
    setIsStreaming(true);
    setStreamingStatus(null);
    // Clear previous turn's events (including persistent ones from last message)
    streamingEventsRef.current = [];
    setStreamingEvents([]);
    seenStatusIdsRef.current.clear();

    const controller = new AbortController();
    streamAbortRef.current = controller;

    try {
      const token = await getToken(
        process.env.NEXT_PUBLIC_CLERK_JWT_TEMPLATE
          ? { template: process.env.NEXT_PUBLIC_CLERK_JWT_TEMPLATE }
          : undefined
      );

      const body = {
        content,
        system_prompt: companionConfig.system_prompt,
        llm_provider: companionConfig.llm_provider,
        temperature: companionConfig.temperature,
        image_ids: imageIds && imageIds.length > 0 ? imageIds : undefined,
      };

      await subscribeTextStream(
        `${API_BASE}/conversations/${conversationId}/messages/stream`,
        token || null,
        body,
        (evt: TextStreamEvent) => {
          if (evt.type === 'ack') {
            // Replace temp user with persisted one
            const u = evt.data?.user_message as unknown as Message | undefined;
            if (u && typeof u.id === 'string') {
              setMessages(prev => prev.map(m => (m.id === tempUser.id ? u : m)));
            }
          } else if (evt.type === 'status') {
            const { stage, phase, meta } = evt.data;
            if (phase === 'start') setStreamingStatus(stage);
            if (phase === 'end') setStreamingStatus(null);

            // Dedupe: if server/event-source resent the same status event id, ignore it
            if (evt.id && seenStatusIdsRef.current.has(evt.id)) {
              return;
            }
            if (evt.id) seenStatusIdsRef.current.add(evt.id);

            // Determine if this event should be persistent (stay visible after streaming)
            const isPersistent = stage === 'memory_stored';

            // For persistent events at 'end' phase, add to streamingEvents
            // Both user and assistant memory_stored badges will show on the AI response
            if (isPersistent && phase === 'end') {
              const newEvent = {
                stage: stage as StreamingStatusEvent['stage'],
                phase: phase as StreamingStatusEvent['phase'],
                meta: meta ?? null,
                at: Date.now(),
                persistent: true,
              };
              streamingEventsRef.current = [...streamingEventsRef.current, newEvent];
              setStreamingEvents(streamingEventsRef.current);
            } else {
              // For non-persistent events (fleeting badges), add to streaming events
              const s = (stage || 'thinking') as StreamingStatusEvent['stage'];
              const p = (phase || 'start') as StreamingStatusEvent['phase'];
              const last = streamingEventsRef.current[streamingEventsRef.current.length - 1];
              if (!(last && last.stage === s && last.phase === p)) {
                const newEvent = {
                  stage: s,
                  phase: p,
                  meta: meta ?? null,
                  at: Date.now(),
                  persistent: false,
                };
                streamingEventsRef.current = [...streamingEventsRef.current, newEvent];
                setStreamingEvents(streamingEventsRef.current);
              }
            }
          } else if (evt.type === 'delta') {
            const piece = evt.data?.content || '';
            if (!piece) return;
            setMessages(prev => prev.map(m => m.id === draftId ? { ...m, content: (m.content || '') + piece } : m));
          } else if (evt.type === 'message') {
            const a = evt.data?.assistant_message as unknown as Message | undefined;
            if (a && typeof a.id === 'string' && typeof a.role === 'string') {
              setMessages(prev => prev.map(m => (m.id === draftId ? a : m)));
            } else {
              // Ignore malformed final message; keep draft content visible
              // The 'done' event or a later valid 'message' will finalize it.
            }
          } else if (evt.type === 'error') {
            const msg = evt.data.detail || evt.data.message || 'Stream error';
            setError(msg);
            setMessages(prev => prev.filter(m => m.id !== tempUser.id && m.id !== draftId));
            setIsStreaming(false);
            setStreamingStatus(null);
            // Add error event to ref and state
            const errorEvent = { stage: 'ruminating' as const, phase: 'end' as const, meta: { error: msg }, at: Date.now(), persistent: false };
            streamingEventsRef.current = [...streamingEventsRef.current, errorEvent];
            setStreamingEvents(streamingEventsRef.current);
          } else if (evt.type === 'done') {
            // Get persistent events from ref (current value, not stale closure)
            const persistentEvents = streamingEventsRef.current.filter(e => e.persistent === true && e.phase === 'end');

            // Clear streaming events FIRST (before setting isStreaming=false)
            // This prevents fleeting badges from briefly showing after stream ends
            streamingEventsRef.current = [];
            setStreamingEvents([]);

            // Move persistent events to message badges
            if (persistentEvents.length > 0) {
              setMessages(prev => prev.map(m =>
                m.id === draftId ? { ...m, badges: persistentEvents } : m
              ));
            }

            // Set streaming to false AFTER clearing events to avoid flicker
            setIsStreaming(false);
            setStreamingStatus(null);
          }
        },
        controller.signal
      );

      return { abort: () => controller.abort() };
    } catch (err) {
      // Clean up optimistic/draft on error
      setMessages(prev => prev.filter(m => m.id !== tempUser.id && m.id !== draftId));
      const errorMessage = err instanceof Error ? err.message : 'Failed to stream message';
      setError(errorMessage);
      setIsStreaming(false);
      setStreamingStatus(null);
      setStreamingEvents([]);
    }
  }, [getToken]);

  const clearMessages = useCallback(() => {
    setMessages([]);
    setError(null);
  }, []);

  return {
    messages,
    loading,
    error,
    sendMessage,
    loadConversation,
    clearMessages,
    sendMessageToConversation,
    sendMessageToConversationStream,
    isStreaming,
    streamingStatus,
    stopStreaming,
    streamingEvents
  };
}
