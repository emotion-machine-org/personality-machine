'use client';

import { useState, useCallback, useEffect, useRef } from 'react';
import { useAuth } from '@clerk/nextjs';
import Icon from '@/components/ui/icon';
import { VoiceOrb } from '@/components/voice/voice-orb';
import { useWebSocketSession } from '@/hooks/useWebSocketSession';
import { useConversationMessages, type CompanionConfig } from '@/hooks/useConversationMessages';
import { useSessionConversation } from '@/hooks/useSessionConversation';
import { useSelectedCompanion } from '@/components/providers';
import { useCompanion } from '@/hooks/useCompanions';
import { getOrInitBuilderUserId, resetBuilderUserId, setBuilderUserId, setLastConversationId, clearLastConversationId } from '@/lib/builderUser';
import { isConversationSimulatorDebugEnabled } from '@/lib/debug';
import { ChatThread } from '@/components/ui/chat-thread';
import { ChatInput } from '@/components/ui/chat-input';
import { MessageBubble } from '@/components/ui/message-bubble';
import { EventBadgeList, type EventBadgeData } from '@/components/ui/event-badge';
import { ImageUploadButton, StagedImagesPreview } from '@/components/ui/chat-image';
import { useImageUpload } from '@/hooks/useImageUpload';
import { API_CONFIG } from '@/lib/config';

const CONVERSATION_SIMULATOR_DEBUG_ENABLED = isConversationSimulatorDebugEnabled;

const debugLog = (...args: Parameters<typeof console.log>) => {
  if (!CONVERSATION_SIMULATOR_DEBUG_ENABLED) return;
  console.log(...args);
};

const debugError = (...args: Parameters<typeof console.error>) => {
  if (!CONVERSATION_SIMULATOR_DEBUG_ENABLED) return;
  console.error(...args);
};

const API_BASE = API_CONFIG.BASE_URL;

// StartNewChatDropdown component
function StartNewChatDropdown({
  onSelect,
  disabled
}: {
  onSelect: (type: 'same-user' | 'different-user') => void;
  disabled?: boolean;
}) {
  const [isOpen, setIsOpen] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setIsOpen(false);
      }
    }
    if (isOpen) {
      document.addEventListener('mousedown', handleClickOutside);
    }
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [isOpen]);

  return (
    <div ref={dropdownRef} className="relative">
      <button
        onClick={() => setIsOpen(!isOpen)}
        disabled={disabled}
        className="flex items-center gap-2 px-4 py-2 rounded-full bg-[var(--color-gray-button)] text-white text-sm font-medium transition-colors hover:bg-[var(--color-gray-button-hover)] disabled:opacity-50 disabled:cursor-not-allowed"
      >
        Start New Chat
        <Icon name="chevron-down" size={12} className={`transition-transform ${isOpen ? 'rotate-180' : ''}`} />
      </button>

      {isOpen && (
        <div className="absolute top-full left-0 mt-1 w-56 bg-[var(--color-dropdown-bg)] border border-white/10 shadow-xl z-50 py-1">
          <button
            onClick={() => {
              onSelect('same-user');
              setIsOpen(false);
            }}
            className="w-full text-left px-4 py-2.5 text-sm text-[var(--color-option-text)] hover:text-white transition-colors"
          >
            New session (same user)
          </button>
          <button
            onClick={() => {
              onSelect('different-user');
              setIsOpen(false);
            }}
            className="w-full text-left px-4 py-2.5 text-sm text-[var(--color-option-text)] hover:text-white transition-colors"
          >
            New session (different user)
          </button>
        </div>
      )}
    </div>
  );
}

export default function ConversationSimulator() {
  const [mode, setMode] = useState<'voice' | 'text'>('text');
  // Stable builder user id across refreshes
  const { getToken, userId } = useAuth();
  // Avoid generating a random builder user id during SSR to prevent hydration mismatch.
  // The actual id is resolved client-side after mount.
  const [builderUserId, setBuilderIdState] = useState<string>('');
  const { selectedCompanionId, registerOnSaveCallback, unregisterOnSaveCallback } = useSelectedCompanion();
  const { data: companionConfig } = useCompanion(selectedCompanionId);

  const {
    isConnected,
    isConnecting,
    isPaused,
    userAmplitude,
    companionAmplitude,
    isCompanionSpeaking,
    toggleSession,
    startSession,
    pauseSession,
    resumeSession,
    config,
    setConfig,
    sessionId,
    lastSessionId
  } = useWebSocketSession();

  // Session-conversation management
  const {
    currentConversationId,
    conversationState,
    startNewVoiceSession,
    endVoiceSession,
    setConversationFromSession,
    setConversationDirect,
    clearConversation,
    startTextMode
  } = useSessionConversation();

  // Conversation messages management
  const {
    messages,
    loading: messagesLoading,
    error: messagesError,
    loadConversation,
    clearMessages,
    sendMessageToConversation,
    sendMessageToConversationStream,
    isStreaming,
    stopStreaming,
    streamingEvents,
  } = useConversationMessages();

  // Image upload management
  const {
    stagedImages,
    isUploading: isUploadingImages,
    uploadImages,
    removeStagedImage,
    clearStagedImages,
    getStagedImageIds,
    uploadError,
    clearUploadError,
  } = useImageUpload();

  const fetchLatestCompanionConversation = useCallback(async (): Promise<string | null> => {
    if (!selectedCompanionId) return null;

    const params = new URLSearchParams();
    if (builderUserId) {
      params.set('external_user_id', builderUserId);
    }
    params.set('external_user_prefix', 'builder-');
    const query = params.toString();

    const token = await getToken(
      process.env.NEXT_PUBLIC_CLERK_JWT_TEMPLATE
        ? { template: process.env.NEXT_PUBLIC_CLERK_JWT_TEMPLATE }
        : undefined
    );

    try {
      const response = await fetch(
        `${API_BASE}/conversations/by-companion/${selectedCompanionId}/latest${query ? `?${query}` : ''}`,
        {
          headers: {
            ...(token ? { Authorization: `Bearer ${token}` } : {}),
          },
        }
      );

      if (response.status === 404 || response.status === 204) {
        return null;
      }

      if (!response.ok) {
        throw new Error(`Failed to fetch latest conversation: ${response.statusText}`);
      }

      const data = await response.json().catch(() => null);
      if (data && typeof data.id === 'string') {
        return data.id as string;
      }
    } catch (error) {
      debugError('[CONVERSATION_SIMULATOR] Failed to fetch latest conversation', error);
    }

    return null;
  }, [selectedCompanionId, builderUserId, getToken]);

  useEffect(() => {
    const nextId = getOrInitBuilderUserId(userId);
    setBuilderIdState(nextId);
    setBuilderUserId(nextId, userId);
  }, [userId]);

  // Force refresh messages when switching to text mode
  const refreshMessages = useCallback(() => {
    if (!currentConversationId) return;
    debugLog('[CONVERSATION_SIMULATOR] Manually refreshing messages for:', currentConversationId);
    loadConversation(currentConversationId).then(found => {
      if (!found) {
        debugLog('[CONVERSATION_SIMULATOR] Conversation not found during manual refresh:', currentConversationId);
        clearConversation();
        if (selectedCompanionId) {
          clearLastConversationId(selectedCompanionId, builderUserId);
        }
      }
    });
  }, [currentConversationId, loadConversation, clearConversation, selectedCompanionId, builderUserId]);

  const [messageInput, setMessageInput] = useState('');
  const lastVoiceToggleRef = useRef<number>(0);
  const textScrollContainerRef = useRef<HTMLDivElement | null>(null);
  const currentConversationIdRef = useRef<string | null>(currentConversationId);
  const hydratedContextRef = useRef<string | null>(null);

  useEffect(() => {
    currentConversationIdRef.current = currentConversationId;
  }, [currentConversationId]);

  // Effect to handle session-conversation lifecycle
  useEffect(() => {
    debugLog('[CONVERSATION_SIMULATOR] Session lifecycle effect triggered:', {
      isConnected,
      isConnecting,
      sessionId,
      lastSessionId,
      conversationState,
      currentConversationId
    });

    if (!isConnected && !isConnecting && lastSessionId && conversationState === 'voice_ended' && !currentConversationId) {
      debugLog('[CONVERSATION_SIMULATOR] Voice session ended, looking up conversation for session:', lastSessionId);
      setConversationFromSession(lastSessionId);
    }
  }, [isConnected, isConnecting, lastSessionId, conversationState, currentConversationId, setConversationFromSession, sessionId]);

  // Sync simulator state when the selected companion or builder user changes
  useEffect(() => {
    let cancelled = false;

    if (!selectedCompanionId || !userId) {
      clearConversation();
      setConfig({ clientExternalUserId: builderUserId });
      return () => {
        cancelled = true;
      };
    }

    setConfig({ clientExternalUserId: builderUserId });

    const hydrate = async () => {
      const targetCompanionId = selectedCompanionId!;
      const hydrationKey = `${targetCompanionId}:${builderUserId ?? ''}`;

      if (
        currentConversationIdRef.current &&
        hydratedContextRef.current === hydrationKey
      ) {
        setLastConversationId(targetCompanionId, builderUserId, currentConversationIdRef.current);
        return;
      }

      if (hydratedContextRef.current && hydratedContextRef.current !== hydrationKey) {
        clearConversation();
      }

      const latestId = await fetchLatestCompanionConversation();
      if (cancelled) return;

      if (latestId) {
        if (latestId !== currentConversationIdRef.current) {
          setConversationDirect(latestId);
        }
        setLastConversationId(targetCompanionId, builderUserId, latestId);
      } else {
        clearConversation();
        clearLastConversationId(targetCompanionId, builderUserId);
      }

      hydratedContextRef.current = hydrationKey;
    };

    hydrate();

    return () => {
      cancelled = true;
    };
  }, [
    selectedCompanionId,
    builderUserId,
    userId,
    fetchLatestCompanionConversation,
    clearConversation,
    setConversationDirect,
    setConfig,
  ]);

  // Effect to load conversation messages when conversation ID changes
  useEffect(() => {
    if (!currentConversationId) {
      clearMessages();
      return;
    }

    let cancelled = false;
    loadConversation(currentConversationId).then(found => {
      if (cancelled) return;
      if (found) {
        if (selectedCompanionId) {
          setLastConversationId(selectedCompanionId, builderUserId, currentConversationId);
        }
      } else {
        clearConversation();
        if (selectedCompanionId) {
          clearLastConversationId(selectedCompanionId, builderUserId);
        }
      }
    });

    return () => {
      cancelled = true;
    };
  }, [
    currentConversationId,
    loadConversation,
    clearMessages,
    selectedCompanionId,
    builderUserId,
    clearConversation,
  ]);


  const scrollTextModeToBottom = useCallback(
    (behavior: ScrollBehavior = 'smooth') => {
      const container = textScrollContainerRef.current;
      if (!container) return;
      container.scrollTo({ top: container.scrollHeight, behavior });
    },
    []
  );

  useEffect(() => {
    if (mode !== 'text') return;
    scrollTextModeToBottom('auto');
  }, [mode, scrollTextModeToBottom]);

  useEffect(() => {
    if (mode !== 'text') return;
    const frame = requestAnimationFrame(() => {
      scrollTextModeToBottom(isStreaming ? 'auto' : 'smooth');
    });
    return () => cancelAnimationFrame(frame);
  }, [messages, mode, isStreaming, scrollTextModeToBottom]);

  const ensureConversationExists = useCallback(async (): Promise<string | null> => {
    // If we already have a conversation, start text mode and return it
    if (currentConversationId) {
      startTextMode();
      return currentConversationId;
    }

    // Try to get conversation from current or last session first
    const sessionToCheck = sessionId || lastSessionId;
    if (sessionToCheck) {
      debugLog('[CONVERSATION_SIMULATOR] Looking up conversation for session:', sessionToCheck);
      try {
        const token = await getToken(
          process.env.NEXT_PUBLIC_CLERK_JWT_TEMPLATE
            ? { template: process.env.NEXT_PUBLIC_CLERK_JWT_TEMPLATE }
            : undefined
        );
    const response = await fetch(`${API_BASE}/conversations/by-session/${sessionToCheck}` , {
          headers: {
            ...(token ? { Authorization: `Bearer ${token}` } : {}),
          },
        });
        if (response.ok) {
          const conversation = await response.json();
          if (conversation && conversation.id) {
            debugLog('[CONVERSATION_SIMULATOR] Found session conversation:', conversation.id);
            setConversationDirect(conversation.id);
            return conversation.id;
          }
        }
      } catch (error) {
        debugError('[CONVERSATION_SIMULATOR] Error looking up session conversation:', error);
      }
    }

    if (selectedCompanionId) {
      const latest = await fetchLatestCompanionConversation();
      if (latest) {
        setConversationDirect(latest);
        return latest;
      }
    }

    // Only create new conversation if we're in idle state (no voice sessions at all)
    if (conversationState === 'idle' && selectedCompanionId) {
      debugLog('[CONVERSATION_SIMULATOR] Creating new conversation for text-only mode');
      try {
        const token = await getToken(
          process.env.NEXT_PUBLIC_CLERK_JWT_TEMPLATE
            ? { template: process.env.NEXT_PUBLIC_CLERK_JWT_TEMPLATE }
            : undefined
        );
        const response = await fetch(`${API_BASE}/conversations/`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            ...(token ? { Authorization: `Bearer ${token}` } : {}),
          },
          body: JSON.stringify({
            companion_id: selectedCompanionId,
            external_user_id: builderUserId,
          }),
        });

        if (response.ok) {
          const conversation = await response.json();
          debugLog('[CONVERSATION_SIMULATOR] Created conversation:', conversation.id);

          // Set the conversation directly for text mode
          setConversationDirect(conversation.id);
          setLastConversationId(selectedCompanionId, builderUserId, conversation.id);
          return conversation.id;
        } else {
          debugError('[CONVERSATION_SIMULATOR] Failed to create conversation:', response.status);
          alert('Failed to create conversation. Please try again.');
          return null;
        }
      } catch (error) {
        debugError('[CONVERSATION_SIMULATOR] Error creating conversation:', error);
        alert('Failed to create conversation. Please try again.');
        return null;
      }
    } else if (!selectedCompanionId) {
      alert('Please select a companion first');
      return null;
    } else {
      debugLog('[CONVERSATION_SIMULATOR] No conversation found and not in idle state. Current state:', conversationState);
      return null;
    }
  }, [
    currentConversationId,
    conversationState,
    lastSessionId,
    sessionId,
    selectedCompanionId,
    startTextMode,
    setConversationDirect,
    getToken,
    builderUserId,
    fetchLatestCompanionConversation,
  ]);

  const handleTabClick = useCallback(async (tabId: string) => {
    debugLog('[CONVERSATION_SIMULATOR] Tab click:', {
      tabId,
      mode,
      conversationState,
      currentConversationId,
      sessionId,
      lastSessionId
    });

    if (tabId === 'switch-to-text') {
      if (mode === 'voice') {
        // Switching to text mode
        debugLog('[CONVERSATION_SIMULATOR] Switching to text mode');

        // If voice session is active, pause it automatically for smoother UX
        if (conversationState === 'voice_active' && isConnected && !isPaused) {
          debugLog('[CONVERSATION_SIMULATOR] Auto-pausing voice session for text mode');
          await pauseSession();
        }

        // Switch to text mode and ensure conversation exists
        setMode('text');
        await ensureConversationExists();

        // Always force refresh messages when switching to text mode to get latest voice messages
        setTimeout(() => {
          refreshMessages();
        }, 200);
      } else {
        // Switching back to voice mode - always allowed
        debugLog('[CONVERSATION_SIMULATOR] Switching back to voice mode');
        setMode('voice');
      }
    } else if (tabId === 'new-session:same-user' || tabId === 'new-session:different-user') {
      const sameUser = tabId.endsWith('same-user');
      try {
        let effectiveUserId = builderUserId;
        if (!sameUser) {
          effectiveUserId = resetBuilderUserId(userId);
          setBuilderIdState(effectiveUserId);
          setBuilderUserId(effectiveUserId, userId);
        }
        if (!selectedCompanionId) {
          alert('Please select a companion first.');
          return;
        }
        // Ensure voice will use this id
        setConfig({ clientExternalUserId: effectiveUserId });
        // Create a fresh conversation for text testing
        const token = await getToken(
          process.env.NEXT_PUBLIC_CLERK_JWT_TEMPLATE
            ? { template: process.env.NEXT_PUBLIC_CLERK_JWT_TEMPLATE }
            : undefined
        );
        const response = await fetch(`${API_BASE}/conversations/`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            ...(token ? { Authorization: `Bearer ${token}` } : {}),
          },
          body: JSON.stringify({
            companion_id: selectedCompanionId,
            external_user_id: effectiveUserId,
          }),
        });
        if (response.ok) {
          const conversation = await response.json();
          setConversationDirect(conversation.id);
          setLastConversationId(selectedCompanionId, effectiveUserId, conversation.id);
          if (mode !== 'text') setMode('text');
        } else {
          const err = await response.text();
          debugError('[CONVERSATION_SIMULATOR] Failed to create new session conversation:', response.status, err);
          alert('Failed to create session.');
        }
      } catch (e) {
        debugError('[CONVERSATION_SIMULATOR] New session error:', e);
      }
    }
  }, [
    mode,
    conversationState,
    currentConversationId,
    sessionId,
    lastSessionId,
    isConnected,
    isPaused,
    pauseSession,
    ensureConversationExists,
    refreshMessages,
    builderUserId,
    userId,
    selectedCompanionId,
    setConfig,
    getToken,
    setConversationDirect,
  ]);

  const handleSendMessage = async () => {
    const hasText = messageInput.trim().length > 0;
    const hasImages = stagedImages.length > 0 && !isUploadingImages;

    // Need either text or images to send
    if (!hasText && !hasImages) return;

    // Ensure conversation exists before sending message
    let effectiveConversationId = currentConversationId;
    if (!effectiveConversationId) {
      effectiveConversationId = await ensureConversationExists();
      if (!effectiveConversationId) return; // Failed to create conversation
    }

    const currentMessage = messageInput;
    const currentImageIds = getStagedImageIds();

    // Clear input and staged images immediately to provide instant feedback
    setMessageInput('');
    clearStagedImages();

    // Extract LLM provider from the current voice pipeline configuration
    const pipelineLLMProvider = config.voiceConfig?.llm_provider;
    const pipelineType = config.voiceConfig?.pipeline_type;

    // Determine the LLM provider to use based on pipeline configuration
    // Note: openai-realtime is legacy and auto-converted to stt-llm-tts by the server
    let llmProvider: string;
    if (pipelineLLMProvider) {
      // Use the LLM from the pipeline config
      llmProvider = pipelineLLMProvider;
    } else {
      // Fallback to default OpenAI model
      llmProvider = 'openai-gpt4o';
    }

    // Use the selected companion's configuration or fallback to current session config
    const messageCompanionConfig: CompanionConfig = {
      system_prompt: companionConfig?.system_prompt.full_system_prompt || config.systemPrompt,
      llm_provider: llmProvider,
      temperature: companionConfig?.inference?.temperature || config.voiceConfig?.temperature || 0.7
    };

    debugLog('[CONVERSATION_SIMULATOR] Sending text message with LLM:', llmProvider, '(pipeline:', pipelineType + ')', 'images:', currentImageIds.length);

    const STREAM_ENABLED = (process.env.NEXT_PUBLIC_TEXT_STREAM_ENABLED || '').toLowerCase() === 'true';
    try {
      if (STREAM_ENABLED) {
        await sendMessageToConversationStream(effectiveConversationId, currentMessage, messageCompanionConfig, currentImageIds);
      } else {
        await sendMessageToConversation(effectiveConversationId, currentMessage, messageCompanionConfig, currentImageIds);
      }
    } catch (error) {
      console.error('Failed to send message:', error);
      // Restore message on error
      setMessageInput(currentMessage);
      alert('Failed to send message. Please try again.');
    }
  };

  const handleVoiceToggle = useCallback(async () => {
    // Debounce rapid clicks
    const now = Date.now();
    if (now - lastVoiceToggleRef.current < 500) {
      debugLog('[CONVERSATION_SIMULATOR] Voice toggle debounced - too rapid');
      return;
    }
    lastVoiceToggleRef.current = now;

    debugLog('[CONVERSATION_SIMULATOR] Voice toggle clicked:', {
      isConnected,
      isConnecting,
      isPaused,
      conversationState,
      selectedCompanionId
    });

    if (!isConnected && !isConnecting) {
      // Starting new voice session
      if (!selectedCompanionId) {
        alert('Please select a companion first');
        return;
      }

      // Use the latest system prompt from companionConfig (fetched from server)
      // rather than config.systemPrompt (local state that may be stale after edits)
      const latestSystemPrompt = companionConfig?.system_prompt.full_system_prompt
        || config.systemPrompt
        || 'You are a helpful and friendly companion.';

      debugLog('[CONVERSATION_SIMULATOR] Starting new voice session with companion:', selectedCompanionId);
      debugLog('[CONVERSATION_SIMULATOR] Using system prompt from companionConfig:', latestSystemPrompt?.substring(0, 100) + '...');
      debugLog('[CONVERSATION_SIMULATOR] Continuing conversation:', currentConversationId || 'new');
      setConfig({
        companionId: selectedCompanionId,
        systemPrompt: latestSystemPrompt,
        clientExternalUserId: builderUserId,
        conversationId: currentConversationId || undefined,
      });

      await startNewVoiceSession();
      toggleSession();
    } else if (isConnected && isPaused) {
      // Resuming paused session
      debugLog('[CONVERSATION_SIMULATOR] Resuming voice session');
      await resumeSession();
    } else if (isConnected && !isPaused) {
      // Pausing active session
      debugLog('[CONVERSATION_SIMULATOR] Pausing voice session');
      await pauseSession();
    }
  }, [
    isConnected,
    isConnecting,
    isPaused,
    conversationState,
    toggleSession,
    pauseSession,
    resumeSession,
    startNewVoiceSession,
    selectedCompanionId,
    setConfig,
    config.systemPrompt,
    builderUserId,
    companionConfig,
    currentConversationId,
  ]);

  const [isRestarting, setIsRestarting] = useState(false);

  // Helper function to wait for session cleanup completion
  const waitForSessionCleanup = useCallback((): Promise<void> => {
    return new Promise((resolve) => {
      // If already disconnected, resolve immediately
      if (!isConnected && !isConnecting) {
        debugLog('[CONVERSATION_SIMULATOR] Already disconnected, no cleanup needed');
        resolve();
        return;
      }

      let attempts = 0;
      const maxAttempts = 40; // 40 * 50ms = 2 seconds max wait

      // Otherwise, wait for disconnection
      const checkDisconnected = () => {
        attempts++;

        if (!isConnected && !isConnecting) {
          debugLog('[CONVERSATION_SIMULATOR] Disconnection detected after', attempts * 50, 'ms');
          resolve();
        } else if (attempts >= maxAttempts) {
          debugLog('[CONVERSATION_SIMULATOR] Timeout waiting for disconnection, proceeding anyway');
          resolve();
        } else {
          // Check again in 50ms
          setTimeout(checkDisconnected, 50);
        }
      };

      checkDisconnected();
    });
  }, [isConnected, isConnecting]);

  const handleRestartSession = useCallback(async () => {
    if (isRestarting) {
      debugLog('[CONVERSATION_SIMULATOR] Restart already in progress, ignoring');
      return;
    }

    debugLog('[CONVERSATION_SIMULATOR] Restarting voice session. Current state:', {
      isConnected, isConnecting, isPaused, sessionId
    });
    setIsRestarting(true);

    try {
      // Stop any existing session and wait for cleanup
      if (isConnected || isConnecting || isPaused) {
        debugLog('[CONVERSATION_SIMULATOR] Stopping current session for restart');

        // Use toggleSession for proper cleanup (it handles both start/stop)
        if (isConnected || isConnecting) {
          toggleSession(); // This calls stopSession internally
        }
        endVoiceSession();

        // Wait for actual disconnection
        debugLog('[CONVERSATION_SIMULATOR] Waiting for session cleanup...');
        await waitForSessionCleanup();
        debugLog('[CONVERSATION_SIMULATOR] Session cleanup completed');
      }

      // Start new session with updated config
      if (selectedCompanionId) {
        debugLog('[CONVERSATION_SIMULATOR] Starting new session with updated config');

        // Use the latest system prompt from companionConfig (fetched from server)
        // to ensure we pick up any changes made in companion-controls
        const latestSystemPrompt = companionConfig?.system_prompt.full_system_prompt
          || config.systemPrompt
          || 'You are a helpful and friendly companion.';

        setConfig({
          companionId: selectedCompanionId,
          systemPrompt: latestSystemPrompt,
        });

        // Start new session using direct startSession (not toggle)
        debugLog('[CONVERSATION_SIMULATOR] Calling startNewVoiceSession...');
        await startNewVoiceSession();

        debugLog('[CONVERSATION_SIMULATOR] Calling startSession directly...');
        await startSession(); // Direct start instead of toggle

        debugLog('[CONVERSATION_SIMULATOR] New session restart completed');
      } else {
        debugLog('[CONVERSATION_SIMULATOR] No companion selected for restart');
      }
    } catch (error) {
      debugError('[CONVERSATION_SIMULATOR] Error during restart:', error);
    } finally {
      debugLog('[CONVERSATION_SIMULATOR] Restart process finished, resetting restart state');
      setIsRestarting(false);
    }
  }, [isRestarting, isConnected, isConnecting, isPaused, sessionId, toggleSession, startSession, waitForSessionCleanup, startNewVoiceSession, endVoiceSession, selectedCompanionId, companionConfig, setConfig, config.systemPrompt]);

  // Handler for when companion config is saved - restart session with updated config
  const handleCompanionConfigSaved = useCallback(async () => {
    debugLog('[CONVERSATION_SIMULATOR] Companion config saved, checking if session needs restart');

    // Only restart if we have an active or paused voice session
    if (isConnected || isConnecting) {
      debugLog('[CONVERSATION_SIMULATOR] Active session detected, restarting with updated config');
      await handleRestartSession();
    } else {
      debugLog('[CONVERSATION_SIMULATOR] No active session, skipping restart');
    }
  }, [isConnected, isConnecting, handleRestartSession]);

  // Register the callback with the provider
  useEffect(() => {
    registerOnSaveCallback(handleCompanionConfigSaved);
    return () => {
      unregisterOnSaveCallback(handleCompanionConfigSaved);
    };
  }, [registerOnSaveCallback, unregisterOnSaveCallback, handleCompanionConfigSaved]);

  const handleImageSelect = useCallback(async (files: FileList) => {
    // Ensure conversation exists before uploading
    let effectiveConversationId = currentConversationId;
    if (!effectiveConversationId) {
      effectiveConversationId = await ensureConversationExists();
      if (!effectiveConversationId) return;
    }

    await uploadImages(effectiveConversationId, files);
  }, [currentConversationId, ensureConversationExists, uploadImages]);

  // Map connection state for VoiceOrb
  const voiceOrbConnectionState = isConnecting
    ? 'connecting'
    : isConnected
      ? 'connected'
      : 'disconnected';

  const handleVoicePause = useCallback(async () => {
    if (isConnected && !isPaused) {
      await pauseSession();
    }
  }, [isConnected, isPaused, pauseSession]);

  const handleVoiceResume = useCallback(async () => {
    if (isConnected && isPaused) {
      await resumeSession();
    }
  }, [isConnected, isPaused, resumeSession]);

  const renderVoiceMode = () => (
    <div className="absolute inset-0 flex items-center justify-center">
      <VoiceOrb
        connectionState={voiceOrbConnectionState}
        isPaused={isPaused}
        isCompanionSpeaking={isCompanionSpeaking}
        companionAmplitude={companionAmplitude}
        userAmplitude={userAmplitude}
        onConnect={handleVoiceToggle}
        onDisconnect={handleVoiceToggle}
        onPause={handleVoicePause}
        onResume={handleVoiceResume}
      />
    </div>
  );


const renderTextMode = () => (
  <div className="h-full flex flex-col">
    <div
      ref={textScrollContainerRef}
      className="flex-1 overflow-y-auto p-[24px]"
      style={{ scrollbarWidth: 'thin', scrollbarColor: 'rgba(255, 255, 255, 0.2) transparent', scrollbarGutter: 'stable' }}
    >
      <style jsx>{`
        div::-webkit-scrollbar { width: 1px; }
        div::-webkit-scrollbar-track { background: transparent; }
        div::-webkit-scrollbar-thumb { background: rgba(255, 255, 255, 0.2); border-radius: 0; }
        div::-webkit-scrollbar-thumb:hover { background: rgba(255, 255, 255, 0.4); }
      `}</style>

      {conversationState === 'voice_active' && (
        <div className={`mb-4 px-3 py-3 ${isPaused ? 'bg-[color:var(--color-green-bg)]' : 'bg-[color:var(--color-blue-bg)]'}`}>
          <p className={`text-sm ${isPaused ? 'text-[color:var(--color-green-solid)]' : 'text-[color:var(--color-blue-solid)]'}`}>
            {isPaused
              ? 'Voice session is paused. Switch back to Voice tab to resume or continue here in text.'
              : 'Voice session is active. You can continue the conversation in both voice and text modes.'}
          </p>
        </div>
      )}

      {messagesLoading && <div className="mb-4 text-center text-white/60">Loading conversation...</div>}
      {messagesError && (
        <div className="mb-4 bg-[color:var(--color-brand-bg)] p-3">
          <p className="text-sm text-[color:var(--color-brand-solid)]">{messagesError}</p>
        </div>
      )}

      <div className="mx-auto w-full max-w-2xl">
        <ChatThread autoScroll={!isStreaming} className="space-y-6">
          {messages.length === 0 && !messagesLoading && conversationState === 'text_active' ? (
            <div className="py-8 text-center text-white/60">No messages yet. Start the conversation!</div>
          ) : (
            messages.map((message, idx) => {
              if (!message || typeof (message as { role?: unknown }).role !== 'string') return null;
              if (message.role === 'system') return null;

              // Check if this is THE active draft (last message AND draft ID AND streaming)
              const isActiveDraft = isStreaming &&
                                    message.role === 'assistant' &&
                                    message.id.startsWith('draft-') &&
                                    idx === messages.length - 1;

              if (isActiveDraft) {
                // During streaming, show all streaming events (both fleeting and persistent)
                // Persistent ones will be moved to message.badges on 'done'
                const badgeEvents: EventBadgeData[] = streamingEvents.map(event => ({
                  ...event,
                  stage: event.stage as EventBadgeData['stage'],
                }));

                return (
                  <div key={`draft-${idx}`} className="flex flex-col items-start space-y-2">
                    <EventBadgeList
                      events={badgeEvents}
                      isStreaming={true}
                      fallbackText="preparing…"
                    />
                    {message.content && (
                      <MessageBubble role="assistant" timestamp={message.created_at}>
                        {message.content}
                      </MessageBubble>
                    )}
                  </div>
                );
              }

              // For completed assistant messages, show persistent badges from the message itself
              if (message.role === 'assistant' && message.badges && message.badges.length > 0) {
                return (
                  <div key={message.id ?? `m-${idx}`} className="flex flex-col items-start space-y-2">
                    <EventBadgeList
                      events={message.badges.map(event => ({
                        ...event,
                        stage: event.stage as EventBadgeData['stage'],
                      }))}
                      isStreaming={false}
                    />
                    <MessageBubble role={message.role as 'user' | 'assistant'} timestamp={message.created_at}>
                      {message.content}
                    </MessageBubble>
                  </div>
                );
              }

              // User messages don't show badges - they're shown on the AI response instead
              return (
                <MessageBubble key={message.id ?? `m-${idx}`} role={message.role as 'user' | 'assistant'} timestamp={message.created_at}>
                  {message.content}
                </MessageBubble>
              );
            })
          )}
        </ChatThread>
      </div>
    </div>

    <div className="sticky bottom-0 bg-black/40 pt-4">
      <div className="mx-auto w-full max-w-2xl">
        {/* Staged images preview */}
        {stagedImages.length > 0 && (
          <div className="mb-3 rounded-xl bg-[#1f1f1f]">
            <StagedImagesPreview images={stagedImages} onRemove={removeStagedImage} />
          </div>
        )}

        {/* Upload error display */}
        {uploadError && (
          <div className="mb-2 flex items-center justify-between rounded-lg bg-[color:var(--color-brand-bg)] px-3 py-2">
            <span className="text-sm text-[color:var(--color-brand-solid)]">{uploadError}</span>
            <button
              type="button"
              onClick={clearUploadError}
              className="text-white/60 hover:text-white"
            >
              <Icon name="x" size={14} />
            </button>
          </div>
        )}

        <ChatInput
          value={messageInput}
          onChange={setMessageInput}
          onSubmit={(event) => {
            event.preventDefault();
            handleSendMessage();
          }}
          placeholder={conversationState === 'idle' ? 'Start typing to begin conversation...' : 'Message...'}
          disabled={(!currentConversationId && conversationState !== 'idle') || isUploadingImages}
          sending={isStreaming || isUploadingImages}
          className="flex flex-wrap items-center gap-2"
          inputWrapperClassName="rounded-full px-4 pt-4 pb-3 bg-[#3C3C3C]"
          leadingSlot={
            <ImageUploadButton
              onSelect={handleImageSelect}
              disabled={isStreaming || isUploadingImages}
            />
          }
          trailingSlot={
            <>
              {isStreaming && (
                <button
                  onClick={stopStreaming}
                  className="inline-flex h-12 items-center rounded-[8px] bg-[#3C3C3C] px-4 text-sm text-white hover:bg-white hover:text-black"
                  type="button"
                >
                  Stop
                </button>
              )}
              <button
                onClick={handleVoiceToggle}
                className="flex h-12 w-12 items-center justify-center rounded-full bg-[#3C3C3C] text-white transition hover:bg-white hover:text-black"
                title="Switch to Voice Mode"
                type="button"
              >
                <Icon name="mic" size={16} color="currentColor" />
              </button>
            </>
          }
        />
      </div>
    </div>
  </div>
);


  // Handler for "Start New Chat" dropdown
  const handleNewSession = useCallback((type: 'same-user' | 'different-user') => {
    handleTabClick(type === 'same-user' ? 'new-session:same-user' : 'new-session:different-user');
  }, [handleTabClick]);

  // Handler for mode toggle
  const handleModeToggle = useCallback(() => {
    handleTabClick('switch-to-text');
  }, [handleTabClick]);

  // Determine if we should show empty state (no messages, not loading)
  // Show centered input when no messages exist, move to bottom once user sends first message
  const hasMessages = messages.length > 0;
  const showEmptyState = !hasMessages && !messagesLoading;

  const renderEmptyState = () => (
    <div className="h-full flex flex-col items-center justify-center">
      <h2 className="text-white text-3xl font-light leading-[0.9] tracking-[-0.04em] mb-6">Test your companion</h2>
      <div className="w-full max-w-2xl px-4">
        <ChatInput
          value={messageInput}
          onChange={setMessageInput}
          onSubmit={(event) => {
            event.preventDefault();
            handleSendMessage();
          }}
          placeholder="Message..."
          disabled={isUploadingImages}
          sending={isStreaming || isUploadingImages}
          className="flex flex-wrap items-center gap-2"
          inputWrapperClassName="rounded-full px-4 pt-4 pb-3 bg-[#3C3C3C]"
          leadingSlot={
            <ImageUploadButton
              onSelect={handleImageSelect}
              disabled={isStreaming || isUploadingImages}
            />
          }
          trailingSlot={
            <>
              <button
                onClick={handleVoiceToggle}
                className="flex h-12 w-12 items-center justify-center rounded-full bg-[#3C3C3C] text-white transition hover:bg-white hover:text-black"
                title="Switch to Voice Mode"
                type="button"
              >
                <Icon name="mic" size={16} color="currentColor" />
              </button>
            </>
          }
        />
      </div>
    </div>
  );

  return (
    <div className="h-full flex flex-col relative">
      {/* Custom header bar */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-white/10">
        {/* Left: Start New Chat */}
        <div className="flex items-center gap-2">
          <StartNewChatDropdown
            onSelect={handleNewSession}
            disabled={!selectedCompanionId}
          />
        </div>

        {/* Right: Mode Toggle */}
        <button
          onClick={handleModeToggle}
          className="px-4 py-2 rounded-full bg-[var(--color-gray-button)] text-white text-sm font-medium transition-colors hover:bg-[var(--color-gray-button-hover)]"
        >
          {mode === 'text' ? 'Switch to Voice' : 'Switch to Text'}
        </button>
      </div>

      {/* Content area */}
      <div className="flex-1 overflow-hidden relative">
        {mode === 'voice' ? renderVoiceMode() : (showEmptyState ? renderEmptyState() : renderTextMode())}
      </div>
    </div>
  );
}
