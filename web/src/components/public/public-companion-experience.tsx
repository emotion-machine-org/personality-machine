'use client';

import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import Icon from '@/components/ui/icon';
import { ChatThread } from '@/components/ui/chat-thread';
import { ChatInput } from '@/components/ui/chat-input';
import { MessageBubble } from '@/components/ui/message-bubble';
import { VoiceOrb } from '@/components/voice/voice-orb';
import { subscribeTextStream, type TextStreamEvent } from '@/lib/sse';
import { SHARE_CONTEXT_PLACEHOLDER, normalizeShareContext } from '@/lib/share';
import type { PublicShareMeta } from '@/lib/api';
import { usePublicVoiceSession } from '@/hooks/usePublicVoiceSession';
import { EventBadgeList, type EventBadgeData } from '@/components/ui/event-badge';
import { API_CONFIG } from '@/lib/config';

interface PublicCompanionExperienceProps {
  slug: string;
  meta: PublicShareMeta;
}

interface TextSessionResponse {
  share_id: string;
  conversation_id: string;
  visitor_token: string;
  allow_text: boolean;
  allow_voice: boolean;
}

interface PublicTurnMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  created_at: string;
  badges?: StreamingStatusEvent[]; // Persistent badges associated with this message
}

interface StreamingStatusEvent {
  id?: string;
  stage: 'retrieving' | 'thinking' | 'memory_stored';
  phase: 'start' | 'end';
  meta?: Record<string, unknown> | null;
  at: number;
  persistent?: boolean;
}

const API_BASE = API_CONFIG.BASE_URL;

const tokenStorageKey = (slug: string) => `companion-share:${slug}:visitor-token`;

const getStoredToken = (slug: string) => {
  if (typeof window === 'undefined') return null;
  return window.localStorage.getItem(tokenStorageKey(slug));
};

const setStoredToken = (slug: string, token: string) => {
  if (typeof window === 'undefined') return;
  window.localStorage.setItem(tokenStorageKey(slug), token);
};

const generateVisitorToken = () => {
  if (typeof window !== 'undefined' && window.crypto?.randomUUID) {
    return window.crypto.randomUUID().replace(/-/g, '');
  }
  return `${Math.random().toString(36).slice(2)}${Math.random().toString(36).slice(2)}`;
};


export function PublicCompanionExperience({ slug, meta }: PublicCompanionExperienceProps) {
  const [visitorToken, setVisitorToken] = useState<string | null>(null);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [status, setStatus] = useState<'idle' | 'loading' | 'ready' | 'error'>('idle');
  const [error, setError] = useState<string | null>(null);
  const [messages, setMessages] = useState<PublicTurnMessage[]>([]);
  const [input, setInput] = useState('');
  const [sendError, setSendError] = useState<string | null>(null);
  const [voiceError, setVoiceError] = useState<string | null>(null);
  const [isStreaming, setIsStreaming] = useState(false);
  const [streamingEvents, setStreamingEvents] = useState<StreamingStatusEvent[]>([]);
  const streamingEventsRef = useRef<StreamingStatusEvent[]>([]);
  const streamAbortRef = useRef<AbortController | null>(null);
  const seenStatusIdsRef = useRef<Set<string>>(new Set());
  const chatInputRef = useRef<HTMLInputElement | null>(null);

  const [sessionStarted, setSessionStarted] = useState(false);
  const [activeMode, setActiveMode] = useState<'text' | 'voice'>('text');
  const [detailsExpanded, setDetailsExpanded] = useState(false);
  const [isRestarting, setIsRestarting] = useState(false);

  const collapseDetailsForMobile = useCallback(() => {
    if (typeof window === 'undefined') return;
    if (window.innerWidth < 768) {
      setDetailsExpanded(false);
    }
  }, []);

  useEffect(() => {
    setVisitorToken(getStoredToken(slug));
  }, [slug]);

  useEffect(() => () => {
    try {
      streamAbortRef.current?.abort();
    } catch {
      /* noop */
    }
  }, []);

  useEffect(() => {
    if (activeMode !== 'text') return;
    if (isStreaming) return;
    chatInputRef.current?.focus();
  }, [activeMode, isStreaming]);

  const description = useMemo(() => {
    const normalized = normalizeShareContext(meta.description);
    return normalized || SHARE_CONTEXT_PLACEHOLDER;
  }, [meta.description]);

  const fetchConversationMessages = useCallback(
    async (conversation: string, token: string) => {
      try {
        const response = await fetch(
          `${API_BASE}/public/companions/conversations/${conversation}/messages?visitor_token=${encodeURIComponent(token)}`,
          { cache: 'no-store' }
        );
        if (!response.ok) {
          if (response.status === 404) {
            setMessages([]);
            return;
          }
          throw new Error(`Failed with status ${response.status}`);
        }
        const payload = (await response.json()) as PublicTurnMessage[];
        setMessages(Array.isArray(payload) ? payload : []);
      } catch (err) {
        console.error('Failed to load conversation history', err);
      }
    },
    []
  );

  const [voiceReadyBanner, setVoiceReadyBanner] = useState<'idle' | 'connecting' | 'active' | 'paused'>('idle');

  const {
    isConnecting: isVoiceConnecting,
    isConnected: isVoiceConnected,
    isRecording: isVoiceRecording,
    isPaused: isVoicePaused,
    userAmplitude,
    companionAmplitude,
    isCompanionSpeaking,
    start: startVoiceSession,
    stop: stopVoiceSession,
    pause: pauseVoiceSession,
    resume: resumeVoiceSession,
    error: hookVoiceError,
  } = usePublicVoiceSession({
    slug,
    visitorToken,
    conversationId,
    onSessionReady: (payload) => {
      setVisitorToken(payload.visitor_token);
      setStoredToken(slug, payload.visitor_token);
      setConversationId(payload.conversation_id);
      setSessionStarted(true);
      setActiveMode('voice');
      setStatus('ready');
      setVoiceReadyBanner('active');
      setDetailsExpanded(false);
    },
    onError: (message) => setVoiceError(message),
  });

  useEffect(() => {
    if (hookVoiceError) {
      setVoiceError(hookVoiceError);
    }
  }, [hookVoiceError]);

  useEffect(() => {
    if (conversationId && visitorToken) {
      void fetchConversationMessages(conversationId, visitorToken);
    }
  }, [conversationId, visitorToken, fetchConversationMessages]);

  const handleCreateTextSession = useCallback(async (overrideToken?: string) => {
    if (!meta.allow_text) return;
    setStatus('loading');
    setError(null);

    try {
      const tokenForRequest = overrideToken ?? visitorToken;
      const response = await fetch(`${API_BASE}/public/companions/${slug}/sessions/text`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ visitor_token: tokenForRequest || undefined }),
      });

      if (!response.ok) {
        const details = await response.text();
        throw new Error(details || `Failed with status ${response.status}`);
      }

      const payload: TextSessionResponse = await response.json();
      setVisitorToken(payload.visitor_token);
      setStoredToken(slug, payload.visitor_token);
      setConversationId(payload.conversation_id);
      setStatus('ready');
      setSessionStarted(true);
      setActiveMode('text');
      setMessages([]);
      setVoiceReadyBanner('paused');
      setDetailsExpanded(false);
      if (payload.visitor_token && payload.conversation_id) {
        void fetchConversationMessages(payload.conversation_id, payload.visitor_token);
      }
    } catch (err) {
      console.error('Failed to create text session', err);
      setStatus('error');
      setError(err instanceof Error ? err.message : 'Unknown error');
    }
  }, [fetchConversationMessages, meta.allow_text, slug, visitorToken]);

  const pauseVoiceIfActive = useCallback(async () => {
    if (!meta.allow_voice) return false;
    if (!isVoiceConnected || isVoicePaused) return false;

    try {
      await pauseVoiceSession();
      setVoiceReadyBanner('paused');
      return true;
    } catch (err) {
      console.warn('Failed to pause voice session automatically', err);
      setVoiceError(err instanceof Error ? err.message : 'Failed to pause voice session');
      return false;
    }
  }, [isVoiceConnected, isVoicePaused, meta.allow_voice, pauseVoiceSession]);

  const ensureVoiceActive = useCallback(async () => {
    if (!meta.allow_voice) return;
    if (isVoiceConnecting) return;
    setSessionStarted(true);
    setActiveMode('voice');
    collapseDetailsForMobile();
    setVoiceError(null);
    try {
      if (!isVoiceConnected) {
        setVoiceReadyBanner('connecting');
        await startVoiceSession();
      } else if (isVoicePaused) {
        await resumeVoiceSession();
        setVoiceReadyBanner('active');
      } else {
        setVoiceReadyBanner('active');
      }
    } catch (err) {
      setVoiceError(err instanceof Error ? err.message : 'Failed to start voice session');
    }
  }, [
    collapseDetailsForMobile,
    isVoiceConnecting,
    isVoiceConnected,
    isVoicePaused,
    meta.allow_voice,
    resumeVoiceSession,
    startVoiceSession,
  ]);

  const handleVoicePrimary = useCallback(async () => {
    if (!meta.allow_voice) return;
    if (isVoiceConnecting) return;
    setVoiceError(null);
    setActiveMode('voice');
    collapseDetailsForMobile();
    try {
      if (!isVoiceConnected) {
        await startVoiceSession();
        setVoiceReadyBanner('connecting');
      } else if (isVoiceRecording) {
        await pauseVoiceSession();
        setVoiceReadyBanner('paused');
      } else if (isVoicePaused) {
        await resumeVoiceSession();
        setVoiceReadyBanner('active');
      }
    } catch (err) {
      setVoiceError(err instanceof Error ? err.message : 'Voice control error');
    }
  }, [
    collapseDetailsForMobile,
    meta.allow_voice,
    isVoiceConnecting,
    isVoiceConnected,
    isVoiceRecording,
    isVoicePaused,
    startVoiceSession,
    pauseVoiceSession,
    resumeVoiceSession,
  ]);

  const handleSwitchToText = useCallback(async () => {
    if (!meta.allow_text) return;
    setVoiceError(null);
    const wasPaused = await pauseVoiceIfActive();
    if (!conversationId) {
      await handleCreateTextSession();
    } else {
      setActiveMode('text');
      if (wasPaused || isVoicePaused) {
        setVoiceReadyBanner('paused');
      }
      if (conversationId && visitorToken) {
        await fetchConversationMessages(conversationId, visitorToken);
      }
    }
    collapseDetailsForMobile();
  }, [
    collapseDetailsForMobile,
    conversationId,
    fetchConversationMessages,
    handleCreateTextSession,
    isVoicePaused,
    meta.allow_text,
    pauseVoiceIfActive,
    visitorToken,
  ]);

  useEffect(() => {
    if (activeMode !== 'text') return;
    if (!meta.allow_voice) return;
    void pauseVoiceIfActive();
  }, [activeMode, meta.allow_voice, pauseVoiceIfActive]);

  useEffect(() => {
    if (!meta.allow_voice) return;
    if (isVoiceConnecting) {
      setVoiceReadyBanner('connecting');
    } else if (isVoiceConnected) {
      setVoiceReadyBanner(isVoicePaused ? 'paused' : 'active');
    } else {
      setVoiceReadyBanner('idle');
    }
  }, [isVoiceConnecting, isVoiceConnected, isVoicePaused, meta.allow_voice]);

  const resetConversation = useCallback(
    async ({
      regenerateToken = false,
      nextMode = 'voice',
    }: { regenerateToken?: boolean; nextMode?: 'text' | 'voice' } = {}) => {
      setVoiceError(null);
      setIsRestarting(true);
      try {
        try {
          await stopVoiceSession();
        } catch {
          // noop
        }
        try {
          streamAbortRef.current?.abort();
        } catch {
          // noop
        }

        let tokenToUse: string;
        if (regenerateToken) {
          tokenToUse = generateVisitorToken();
        } else if (!visitorToken) {
          tokenToUse = generateVisitorToken();
        } else {
          tokenToUse = visitorToken;
        }
        setStoredToken(slug, tokenToUse);
        setVisitorToken(tokenToUse);
        setConversationId(null);
        setMessages([]);
        setInput('');
        setStatus('idle');
        streamingEventsRef.current = [];
        setStreamingEvents([]);
        seenStatusIdsRef.current.clear();
        setActiveMode(nextMode);
        setVoiceReadyBanner(
          meta.allow_voice ? (nextMode === 'voice' ? 'idle' : 'paused') : 'idle'
        );
        setDetailsExpanded(false);
        return tokenToUse;
      } finally {
        setIsRestarting(false);
      }
    },
    [meta.allow_voice, slug, stopVoiceSession, visitorToken]
  );

  const handleStartNewChat = useCallback(async () => {
    if (!meta.allow_text) return;
    const token = await resetConversation({ regenerateToken: true, nextMode: 'text' });
    await handleCreateTextSession(token);
    chatInputRef.current?.focus();
  }, [handleCreateTextSession, meta.allow_text, resetConversation]);

  const shareTitle = meta.display_name || 'Shared Companion';

  const sendViaRest = useCallback(
    async (
      trimmed: string,
      tempUserId: string,
      draftId: string,
      nextVisitorToken: string,
      nextConversationId: string
    ) => {
      try {
        const response = await fetch(
          `${API_BASE}/public/companions/conversations/${nextConversationId}/messages`,
          {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ visitor_token: nextVisitorToken, content: trimmed }),
          }
        );
        if (!response.ok) {
          const details = await response.text();
          throw new Error(details || `Request failed (${response.status})`);
        }
        const payload = await response.json() as {
          user_message: PublicTurnMessage;
          assistant_message: PublicTurnMessage;
        };
        setMessages((prev) => {
          const filtered = prev.filter((msg) => msg.id !== tempUserId && msg.id !== draftId);
          return [...filtered, payload.user_message, payload.assistant_message];
        });
      } catch (err) {
        console.error('Fallback REST send failed', err);
        setMessages((prev) => prev.filter((msg) => msg.id !== tempUserId && msg.id !== draftId));
        setSendError(err instanceof Error ? err.message : 'Failed to send message');
      }
    },
    []
  );

  const handleSendMessage = useCallback(async () => {
    if (!conversationId || !visitorToken) return;
    const trimmed = input.trim();
    if (!trimmed) return;

    setInput('');
    setSendError(null);
    seenStatusIdsRef.current.clear();
    streamingEventsRef.current = [];
    setStreamingEvents([]);

    const tempUserId = `temp-${Date.now()}`;
    const draftId = `draft-${Date.now()}`;
    const createdAt = new Date().toISOString();

    setMessages((prev) => [
      ...prev,
      { id: tempUserId, role: 'user', content: trimmed, created_at: createdAt },
      { id: draftId, role: 'assistant', content: '', created_at: createdAt },
    ]);

    const controller = new AbortController();
    streamAbortRef.current = controller;
    setIsStreaming(true);
    collapseDetailsForMobile();

    try {
      await subscribeTextStream(
        `${API_BASE}/public/companions/conversations/${conversationId}/messages/stream`,
        null,
        { visitor_token: visitorToken, content: trimmed },
        (evt: TextStreamEvent) => {
          if (evt.type === 'ack') {
            const userMessage = evt.data?.user_message as PublicTurnMessage | undefined;
            if (userMessage) {
              setMessages((prev) => prev.map((msg) => (msg.id === tempUserId ? userMessage : msg)));
            }
          } else if (evt.type === 'status') {
            if (!meta.expose_status_events) return;
            if (evt.id && seenStatusIdsRef.current.has(evt.id)) return;
            if (evt.id) seenStatusIdsRef.current.add(evt.id);
            const stage = (evt.data?.stage ?? 'thinking') as StreamingStatusEvent['stage'];
            const phase = (evt.data?.phase ?? 'start') as StreamingStatusEvent['phase'];
            const persistent = stage === 'memory_stored'; // Memory stored badges are persistent
            const eventMeta = evt.data?.meta ?? null;

            // For persistent events at 'end' phase, add to streamingEvents
            // Both user and assistant memory_stored badges will show on the AI response
            if (persistent && phase === 'end') {
              const newEvent = {
                id: evt.id,
                stage,
                phase,
                meta: eventMeta,
                at: Date.now(),
                persistent: true,
              };
              streamingEventsRef.current = [...streamingEventsRef.current, newEvent];
              setStreamingEvents(streamingEventsRef.current);
            } else {
              // For non-persistent events (fleeting badges), add to streaming events
              const newEvent = {
                id: evt.id,
                stage,
                phase,
                meta: eventMeta,
                at: Date.now(),
                persistent: false,
              };
              streamingEventsRef.current = [...streamingEventsRef.current, newEvent];
              setStreamingEvents(streamingEventsRef.current);
            }
          } else if (evt.type === 'delta') {
            const piece = evt.data?.content || '';
            if (!piece) return;
            setMessages((prev) => prev.map((msg) => (msg.id === draftId ? { ...msg, content: (msg.content || '') + piece } : msg)));
          } else if (evt.type === 'message') {
            const assistant = evt.data?.assistant_message as PublicTurnMessage | undefined;
            if (assistant) {
              setMessages((prev) => prev.map((msg) => (msg.id === draftId ? assistant : msg)));
            }
          } else if (evt.type === 'error') {
            const detail = evt.data?.detail || evt.data?.message || 'Stream error';
            setSendError(typeof detail === 'string' ? detail : 'Stream error');
            setMessages((prev) => prev.filter((msg) => msg.id !== tempUserId && msg.id !== draftId));
            setIsStreaming(false);
          } else if (evt.type === 'done') {
            // Get persistent events from ref (current value, not stale closure)
            const persistentEvents = streamingEventsRef.current.filter(e => e.persistent === true && e.phase === 'end');

            // Clear streaming events FIRST (before setting isStreaming=false)
            // This prevents fleeting badges from briefly showing after stream ends
            streamingEventsRef.current = [];
            setStreamingEvents([]);

            // Move persistent events to message badges
            if (persistentEvents.length > 0) {
              setMessages((prev) => prev.map((msg) =>
                msg.id === draftId ? { ...msg, badges: persistentEvents } : msg
              ));
            }

            // Set streaming to false AFTER clearing events to avoid flicker
            setIsStreaming(false);
          }
        },
        controller.signal
      );
    } catch (err) {
      console.error('Public SSE failed, falling back to REST', err);
      await sendViaRest(trimmed, tempUserId, draftId, visitorToken, conversationId);
    } finally {
      setIsStreaming(false);
      streamingEventsRef.current = [];
      setStreamingEvents([]);
      seenStatusIdsRef.current.clear();
      streamAbortRef.current = null;
    }
  }, [collapseDetailsForMobile, conversationId, input, meta.expose_status_events, sendViaRest, visitorToken]);

  const handleSubmit = useCallback(
    (event: FormEvent) => {
      event.preventDefault();
      if (!isStreaming) {
        void handleSendMessage();
      }
    },
    [handleSendMessage, isStreaming]
  );

  // Removed renderStatusPills - now rendering badges inline with messages

  const voiceButtonLabel = useMemo(() => {
    if (!meta.allow_voice) return 'Voice mode disabled';
    if (isVoiceConnecting) return 'Connecting voice…';
    if (!isVoiceConnected) return 'Start talking by voice';
    if (isVoiceRecording) return 'Pause voice session';
    if (isVoicePaused) return 'Resume voice session';
    return 'Start talking by voice';
  }, [meta.allow_voice, isVoiceConnecting, isVoiceConnected, isVoicePaused, isVoiceRecording]);

  const voiceButtonTitle = useMemo(() => {
    if (!meta.allow_voice) return 'Voice mode disabled for this share';
    if (isVoiceConnecting) return 'Connecting voice session';
    if (!isVoiceConnected) return 'Start talking by voice';
    if (isVoiceRecording) return 'Pause voice session';
    if (isVoicePaused) return 'Voice session paused. Tap to resume.';
    return 'Start talking by voice';
  }, [meta.allow_voice, isVoiceConnecting, isVoiceConnected, isVoicePaused, isVoiceRecording]);

  const showIntro = !sessionStarted;

  const minimalBannerStatus = useMemo(() => {
    if (!meta.allow_voice) return 'Text only';
    if (voiceReadyBanner === 'connecting') return 'Voice connecting';
    if (voiceReadyBanner === 'active') return 'Voice live';
    if (voiceReadyBanner === 'paused') return 'Voice paused';
    return 'Voice ready';
  }, [meta.allow_voice, voiceReadyBanner]);

  const renderIntro = () => (
    <main className="min-h-screen text-white"> {/* bg-gradient-to-br from-black via-[#111] to-[#161616] */}
      <div className="mx-auto flex min-h-screen max-w-5xl flex-col px-6 py-12">
        <header className="space-y-6">
          <div className="inline-flex items-center gap-2 rounded-full border border-white/15 bg-white/10 px-3 py-1 text-xs text-white/80">
            <span className="h-1.5 w-1.5 rounded-full bg-[color:var(--color-brand-solid)]" />
            <span className="translate-y-[1px]">Powered by Emotion Machine</span>
          </div>
          <div>
            <h1 className="text-4xl font-book tracking-[-0.04em] sm:text-5xl">{shareTitle}</h1>
            <p className="pt-2 max-w-2xl text-lg font-light text-white/70">{description}</p>
          </div>
        </header>

        <div className="mt-8 space-y-4">
          <div className="grid gap-3 sm:grid-cols-2">
            {meta.allow_voice && (
              <button
                type="button"
                onClick={() => {
                  void ensureVoiceActive();
                }}
                className={`w-full rounded-full px-4 py-3 text-[20px] font-medium transition ${
                  'bg-white text-black hover:bg-white/80'
                } ${isVoiceConnecting ? 'opacity-80' : ''}`}
                title={voiceButtonTitle}
              >
                {voiceButtonLabel}
              </button>
            )}

            <button
              type="button"
              onClick={() => {
                void handleCreateTextSession();
              }}
              disabled={!meta.allow_text || status === 'loading'}
              className={`w-full rounded-full px-4 py-3 text-[20px] font-light transition ${
                meta.allow_text
                  ? 'bg-[color:var(--color-gray-button)] text-white hover:bg-[color:var(--color-gray-button-hover)]'
                  : 'bg-white/10 text-white/40 cursor-not-allowed'
              } ${status === 'loading' ? 'opacity-80' : ''}`}
            >
              {status === 'loading' ? 'Preparing chat…' : meta.allow_text ? 'Start chatting by text' : 'Text mode disabled'}
            </button>
          </div>
        </div>
      </div>
    </main>
  );

const detailsContent = (
    <div className="mt-4 space-y-4 md:space-y-0 md:grid md:grid-cols-2 md:gap-4">
      <div className="space-y-3">
        {(meta.allow_text || meta.allow_voice) && (
          <div className="space-y-2 md:hidden">
            {meta.allow_text && sessionStarted && (
              <button
                type="button"
                onClick={() => {
                  void handleStartNewChat();
                }}
                disabled={isRestarting || isStreaming}
                className="w-full rounded-full border border-white/10 bg-white/5 px-4 py-2 text-xs text-white transition hover:bg-white/10 disabled:cursor-not-allowed disabled:opacity-60"
                title="Start a new chat session"
              >
                New chat
              </button>
            )}
            <div
              className={`grid gap-2 ${
                meta.allow_text && meta.allow_voice ? 'grid-cols-2' : 'grid-cols-1'
              }`}
            >
              {meta.allow_text && (
                <button
                  type="button"
                  onClick={() => {
                    void handleSwitchToText();
                  }}
                  className={`rounded-full px-4 py-2 text-xs transition ${
                    activeMode === 'text'
                      ? 'bg-white text-black'
                      : 'border border-white/10 bg-white/5 text-white hover:bg-white/10'
                  }`}
                >
                  Text mode
                </button>
              )}
              {meta.allow_voice && (
                <button
                  type="button"
                  onClick={() => {
                    void ensureVoiceActive();
                  }}
                  className={`rounded-full px-4 py-2 text-xs transition ${
                    activeMode === 'voice'
                      ? 'bg-[color:var(--color-green-solid)] text-black'
                      : 'border border-white/10 bg-white/5 text-white hover:bg-white/10'
                  }`}
                >
                  Voice mode
                </button>
              )}
            </div>
          </div>
        )}
        <p className="text-sm text-white/70">{description}</p>
        <div className="text-xs text-white/50 space-y-2">
          <div className="inline-flex items-center gap-2 rounded-full border border-white/10 bg-white/5 px-3 py-1">
            <span className="h-1.5 w-1.5 rounded-full bg-[color:var(--color-brand-solid)]" />
            Powered by Emotion Machine
          </div>
          <div className="flex flex-wrap gap-2">
            <span className={`inline-flex items-center gap-2 rounded-full px-3 py-1 ${meta.allow_text ? 'bg-[color:var(--color-green-bg)] text-[color:var(--color-green-solid)]' : 'bg-white/5 text-white/40'}`}>
              <span className={`h-2 w-2 rounded-full ${meta.allow_text ? 'bg-[color:var(--color-green-solid)]' : 'bg-white/40'}`} />
              Text {meta.allow_text ? 'available' : 'disabled'}
            </span>
            <span className={`inline-flex items-center gap-2 rounded-full px-3 py-1 ${meta.allow_voice ? 'bg-[color:var(--color-green-bg)] text-[color:var(--color-green-solid)]' : 'bg-white/5 text-white/40'}`}>
              <span className={`h-2 w-2 rounded-full ${meta.allow_voice ? 'bg-[color:var(--color-green-solid)]' : 'bg-white/40'}`} />
              Voice {meta.allow_voice ? 'available' : 'disabled'}
            </span>
          </div>
        </div>
      </div>
      <div className="space-y-3 md:space-y-4">
        <div className="border border-white/10 bg-white/5 px-4 py-4">
          <p className="text-xs uppercase text-white/40">Session status</p>
          <p className="mt-2 text-sm text-white">
            {status === 'loading' && 'Preparing session…'}
            {status === 'ready' && 'Live session ready.'}
            {status === 'error' && (error || 'Unable to start session.')}
            {status === 'idle' && 'Start a text or voice session to begin.'}
          </p>
          {conversationId && (
            <p className="mt-2 text-xs text-white/40">
              Conversation ID: <span className="font-mono">{conversationId.slice(0, 12)}•••</span>
            </p>
          )}
        </div>
        <div className="border border-white/10 bg-white/5 px-4 py-4 space-y-2">
          <p className="text-xs uppercase text-white/40">Voice mode</p>
          <p className="text-sm text-white ">{minimalBannerStatus}</p>
          <p className="text-xs text-white/60">
            {meta.allow_voice
              ? 'Start the shared voice session to speak with this companion in real time.'
              : 'This companion currently supports text conversations only.'}
          </p>
          {voiceError && <p className="text-xs text-red-300">{voiceError}</p>}
        </div>
      </div>
    </div>
  );

  // Map connection state for VoiceOrb
  const voiceOrbConnectionState = isVoiceConnecting
    ? 'connecting'
    : isVoiceConnected
      ? 'connected'
      : 'disconnected';

  const handleVoiceOrbPause = useCallback(async () => {
    if (isVoiceConnected && !isVoicePaused) {
      await pauseVoiceSession();
      setVoiceReadyBanner('paused');
    }
  }, [isVoiceConnected, isVoicePaused, pauseVoiceSession]);

  const handleVoiceOrbResume = useCallback(async () => {
    if (isVoiceConnected && isVoicePaused) {
      await resumeVoiceSession();
      setVoiceReadyBanner('active');
    }
  }, [isVoiceConnected, isVoicePaused, resumeVoiceSession]);

  const renderVoiceView = () => (
    <div className="flex w-full max-w-xl flex-col items-center gap-6 px-4">
      <VoiceOrb
        connectionState={voiceOrbConnectionState}
        isPaused={isVoicePaused}
        isCompanionSpeaking={isCompanionSpeaking}
        companionAmplitude={companionAmplitude}
        userAmplitude={userAmplitude}
        onConnect={handleVoicePrimary}
        onDisconnect={handleVoicePrimary}
        onPause={handleVoiceOrbPause}
        onResume={handleVoiceOrbResume}
        error={voiceError}
      />
      {meta.allow_text && (
        <button
          type="button"
          onClick={() => {
            void handleSwitchToText();
          }}
          className="inline-flex items-center gap-2 rounded-full border border-white/15 bg-white/5 px-4 py-2 text-sm text-white hover:bg-white/10"
        >
          <Icon name="message-square" size={16} color="currentColor" />
          Switch to text chat
        </button>
      )}
    </div>
  );

  const renderTextView = () => (
    <div className="relative flex h-full w-full max-w-3xl flex-col min-h-0">
      <ChatThread className="space-y-6 pt-6 pb-[calc(env(safe-area-inset-bottom,0)+8rem)] md:px-6">
        {messages.length === 0 && !isStreaming ? (
          <div className="text-center text-white/50">Say hello to get things started.</div>
        ) : (
          messages.map((message, idx) => {
            // Check if this is THE active draft (last message AND draft ID AND streaming)
            const isActiveDraft = isStreaming &&
                                  message.role === 'assistant' &&
                                  message.id.startsWith('draft-') &&
                                  idx === messages.length - 1;

            if (isActiveDraft) {
              // During streaming, show all streaming events (fleeting + persistent)
              // Persistent ones will be moved to message.badges on 'done'
              const badgeEvents: EventBadgeData[] = streamingEvents.map(event => ({
                ...event,
                stage: event.stage as EventBadgeData['stage'],
              }));

              return (
                <div key={message.id} className="flex flex-col items-start space-y-2">
                  {meta.expose_status_events && (
                    <EventBadgeList
                      events={badgeEvents}
                      isStreaming={true}
                      fallbackText="preparing…"
                    />
                  )}
                  {message.content && (
                    <MessageBubble role="assistant" timestamp={message.created_at}>
                      {message.content}
                    </MessageBubble>
                  )}
                </div>
              );
            }

            // For completed assistant messages with persistent badges
            if (message.role === 'assistant' && message.badges && message.badges.length > 0) {
              return (
                <div key={message.id} className="flex flex-col items-start space-y-2">
                  {meta.expose_status_events && (
                    <EventBadgeList
                      events={message.badges.map(event => ({
                        ...event,
                        stage: event.stage as EventBadgeData['stage'],
                      }))}
                      isStreaming={false}
                    />
                  )}
                  <MessageBubble role={message.role} timestamp={message.created_at}>
                    {message.content}
                  </MessageBubble>
                </div>
              );
            }

            // User messages don't show badges - they're shown on the AI response instead
            return (
              <MessageBubble key={message.id} role={message.role} timestamp={message.created_at}>
                {message.content}
              </MessageBubble>
            );
          })
        )}
      </ChatThread>

      <div className="pointer-events-none fixed bottom-0 left-0 right-0 z-30 transform-gpu bg-black pb-[calc(env(safe-area-inset-bottom,0)+1.5rem)] px-3 md:px-6">
        <div className="pointer-events-auto mx-auto w-full max-w-3xl border-t border-white/10 bg-black md:px-6 pt-6 md:pb-4">
          <ChatInput
            value={input}
            onChange={setInput}
            onSubmit={handleSubmit}
            placeholder={meta.allow_voice ? 'Send a message… or tap the mic to speak' : 'Send a message…'}
            disabled={isStreaming || !conversationId}
            sending={isStreaming}
            className="flex flex-wrap items-center gap-3"
            inputWrapperClassName="relative flex-1 min-w-[220px] rounded-full bg-[color:var(--color-gray-button)] px-4 pt-4 pb-3"
            inputRef={chatInputRef}
            trailingSlot={
              meta.allow_voice ? (
                <button
                  type="button"
                  onClick={() => {
                    void ensureVoiceActive();
                  }}
                  className={`relative flex h-12 w-12 items-center justify-center rounded-full border transition ${
                    isVoiceConnecting
                      ? 'border-[color:var(--color-gray-button)] bg-[#3C3C3C] text-white/70 animate-pulse'
                      : isVoiceConnected && !isVoicePaused
                        ? 'border-transparent bg-[color:var(--color-green-solid)] text-black hover:bg-[color:var(--color-green-solid-hover)]'
                        : isVoicePaused
                          ? 'border-transparent bg-[color:var(--color-green-solid)] text-black hover:bg-[color:var(--color-green-solid-hover)]'
                          : 'border-[color:var(--color-gray-button)] bg-[#3C3C3C] text-white hover:bg-white hover:text-black'
                  }`}
                  title={voiceButtonTitle}
                  aria-pressed={isVoiceConnected && !isVoicePaused}
                >
                  <Icon
                    name={isVoiceConnecting ? 'restart' : isVoiceConnected ? (isVoicePaused ? 'play' : 'pause') : 'mic'}
                    size={16}
                    color="currentColor"
                    className={isVoiceConnecting ? 'animate-spin' : undefined}
                  />
                  {isVoicePaused && (
                    <span className="absolute bottom-2 right-2 h-2.5 w-2.5 rounded-full bg-[color:var(--color-green-solid)]" />
                  )}
                </button>
              ) : null
            }
          />
          {sendError && <p className="mt-2 text-xs text-red-300">{sendError}</p>}
          {voiceError && <p className="mt-2 text-xs text-red-300">{voiceError}</p>}
        </div>
      </div>
    </div>
  );

  const handleDetailsToggle = useCallback(() => {
    setDetailsExpanded((prev) => !prev);
  }, []);

  const activeLayout = (
    <main className="min-h-screen bg-black text-white">
      <div className="flex min-h-screen flex-col">
        <div
          className="sticky top-0 z-30 bg-black"
        >
          <div className="relative border-b border-white/10">
            <div className="flex h-16 items-center justify-between px-6">
              <div>
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium text-white">{shareTitle}</span>
                  <span className="text-xs text-white/40 translate-y-[2px]">{minimalBannerStatus}</span>
                </div>
              </div>
              <div className="flex items-center gap-2">
                {meta.allow_text && sessionStarted && (
                  <button
                    type="button"
                    onClick={() => {
                      void handleStartNewChat();
                    }}
                    disabled={isRestarting || isStreaming}
                    className="hidden shrink-0 rounded-full border border-white/10 bg-white/5 px-3 py-1 text-xs text-white transition hover:bg-white/10 disabled:cursor-not-allowed disabled:opacity-60 md:inline-flex"
                    title="Start a new chat session"
                  >
                    New chat
                  </button>
                )}
                {meta.allow_text && (
                  <button
                    type="button"
                    onClick={() => {
                      void handleSwitchToText();
                    }}
                    className={`hidden shrink-0 rounded-full px-3 py-1 text-xs transition md:inline-flex ${
                      activeMode === 'text'
                        ? 'bg-white text-black'
                        : 'border border-white/10 bg-white/5 text-white hover:bg-white/10'
                    }`}
                  >
                    Text mode
                  </button>
                )}
                {meta.allow_voice && (
                  <button
                    type="button"
                    onClick={() => {
                      void ensureVoiceActive();
                    }}
                    className={`hidden shrink-0 rounded-full px-3 py-1 text-xs transition md:inline-flex ${
                      activeMode === 'voice'
                        ? 'bg-[color:var(--color-green-solid)] text-black'
                        : 'border border-white/10 bg-white/5 text-white hover:bg-white/10'
                    }`}
                  >
                    Voice mode
                  </button>
                )}
                <button
                  type="button"
                  onClick={handleDetailsToggle}
                  className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full border border-white/10 bg-white/5 text-white hover:bg-white/10"
                  aria-expanded={detailsExpanded}
                >
                  <Icon
                    name="chevron-down"
                    size={16}
                    color="currentColor"
                    className={`transition-transform ${detailsExpanded ? 'rotate-180' : ''}`}
                  />
                </button>
              </div>
            </div>
            {detailsExpanded && (
              <div className="absolute left-0 right-0 top-full z-10 border-b border-white/10 bg-black/95 px-6 pb-6 pt-4 shadow-[0_20px_40px_rgba(0,0,0,0.6)]">
                <div className="max-h-[70vh] overflow-y-auto pr-1">
                  {detailsContent}
                </div>
              </div>
            )}
          </div>
        </div>

        <div className="flex flex-1 min-h-0 px-3 py-8 overflow-hidden md:items-center md:justify-center">
          {activeMode === 'voice' && meta.allow_voice ? renderVoiceView() : renderTextView()}
        </div>
      </div>
    </main>
  );

  if (showIntro) {
    return renderIntro();
  }

  return activeLayout;
}
