'use client';

/**
 * Companion Simulator - V2 Relationship-based
 *
 * Uses V2 relationship model for both text (WebSocket) and voice.
 * Replaces the old conversation-simulator.tsx.
 */

import { useState, useCallback, useEffect } from 'react';
import Icon from '@/components/ui/icon';
import Tooltip from '@/components/ui/tooltip';
import { VoiceOrb } from '@/components/voice/voice-orb';
import { PhoneDialButton } from '@/components/voice/phone-dial-button';
import { PhoneDialModal } from '@/components/voice/phone-dial-modal';
import { useWebSocketSession } from '@/hooks/useWebSocketSession';
import { useBuilderRelationship } from '@/hooks/useBuilderRelationship';
import { useRelationshipChat, type ChatMessage } from '@/hooks/useRelationshipChat';
import { useTwilioDialOut } from '@/hooks/useTwilioDialOut';
import { StreamingStatus } from '@/components/ui/streaming-status';
import { DebugTraceBadges } from '@/components/ui/debug-trace-badges';
import { useSelectedCompanion, useAuthError } from '@/components/providers';
import { useCompanion } from '@/hooks/useCompanions';
import { ChatThread } from '@/components/ui/chat-thread';
import { ChatInput } from '@/components/ui/chat-input';
import { MessageBubble } from '@/components/ui/message-bubble';
import { RelationshipSelector } from './relationship-selector';
import { StagedImagesPreview } from '@/components/ui/chat-image';
import { useImageUpload } from '@/hooks/useImageUpload';

export default function CompanionSimulator() {
  const [mode, setMode] = useState<'voice' | 'text'>('text');
  const [debugMode, setDebugMode] = useState(true);
  const { selectedCompanionId, registerOnSaveCallback, unregisterOnSaveCallback } = useSelectedCompanion();
  const { data: companionConfig } = useCompanion(selectedCompanionId);

  // V2 Relationship management
  const {
    currentUserId,
    currentRelationship,
    testUsers,
    isLoading: relationshipLoading,
    switchUser,
    createNewUser,
    resetRelationship,
    refreshRelationship,
  } = useBuilderRelationship({ companionId: selectedCompanionId });

  // Auth error handling
  const { triggerAuthError } = useAuthError();

  // V2 Text chat via WebSocket
  const {
    isConnected: chatConnected,
    isConnecting: chatConnecting,
    isStreaming,
    isAuthError: chatAuthError,
    messages,
    streamingContent,
    statusEvents,
    traceEvents,
    error: chatError,
    connect: connectChat,
    disconnect: disconnectChat,
    sendMessage,
    clearMessages,
  } = useRelationshipChat({
    companionId: selectedCompanionId,
    userId: currentUserId,
    debugMode,
    onMessage: () => {
      // Refresh relationship data after new messages
      refreshRelationship();
    },
  });

  // Trigger global auth error modal when chat has auth error
  useEffect(() => {
    if (chatAuthError) {
      triggerAuthError();
    }
  }, [chatAuthError, triggerAuthError]);

  // Voice session (V2)
  const {
    isConnected: voiceConnected,
    isConnecting: voiceConnecting,
    isPaused,
    userAmplitude,
    companionAmplitude,
    isCompanionSpeaking,
    startSession,
    stopSession,
    pauseSession,
    resumeSession,
    setConfig,
  } = useWebSocketSession();

  // Image upload
  const {
    stagedImages,
    isUploading: isUploadingImages,
    removeStagedImage,
  } = useImageUpload();

  // Twilio dial-out
  const {
    status: twilioCallStatus,
    error: twilioError,
    dialOut,
    reset: resetTwilioCall,
  } = useTwilioDialOut({
    companionId: selectedCompanionId,
    userId: currentUserId,
  });

  const [messageInput, setMessageInput] = useState('');
  const [phoneModalOpen, setPhoneModalOpen] = useState(false);

  // State to prevent auto-reconnect during reset/switch operations
  // Using state (not ref) so changes trigger the auto-connect useEffect
  const [isResetting, setIsResetting] = useState(false);

  // Sync companion config to voice session
  useEffect(() => {
    if (companionConfig && selectedCompanionId && currentUserId) {
      setConfig({
        companionId: selectedCompanionId,
        systemPrompt: companionConfig.system_prompt?.full_system_prompt || '',
        clientExternalUserId: currentUserId,
        useV2: true,
      });
    }
  }, [companionConfig, selectedCompanionId, currentUserId, setConfig]);

  // Connect to text chat when in text mode (skip during reset/switch or auth errors)
  useEffect(() => {
    if (mode === 'text' && selectedCompanionId && currentUserId && !chatConnected && !chatConnecting && !isResetting && !chatAuthError) {
      connectChat();
    }
  }, [mode, selectedCompanionId, currentUserId, chatConnected, chatConnecting, connectChat, isResetting, chatAuthError]);

  // Handle mode toggle
  const handleModeToggle = useCallback(() => {
    if (mode === 'text') {
      // Switching to voice
      disconnectChat();
      setMode('voice');
    } else {
      // Switching to text
      if (voiceConnected || voiceConnecting) {
        stopSession();
      }
      setMode('text');
    }
  }, [mode, voiceConnected, voiceConnecting, disconnectChat, stopSession]);

  // Handle voice toggle
  const handleVoiceToggle = useCallback(() => {
    if (voiceConnected || voiceConnecting) {
      stopSession();
    } else {
      disconnectChat();
      setMode('voice');
      startSession();
    }
  }, [voiceConnected, voiceConnecting, startSession, stopSession, disconnectChat]);

  // Handle send message
  const handleSendMessage = useCallback(async () => {
    const content = messageInput.trim();
    if (!content || isStreaming) return;

    setMessageInput('');

    try {
      await sendMessage(content);
    } catch (err) {
      console.error('[COMPANION_SIMULATOR] Failed to send message:', err);
    }
  }, [messageInput, isStreaming, sendMessage]);

  // Handle new user
  const handleCreateNewUser = useCallback(async () => {
    setIsResetting(true);
    disconnectChat();
    clearMessages();
    try {
      await createNewUser();
    } finally {
      setIsResetting(false);
      // Don't manually call connectChat() here - the useEffect will handle it
      // after React re-renders with the new currentUserId
    }
  }, [disconnectChat, clearMessages, createNewUser]);

  // Handle reset
  const handleReset = useCallback(async () => {
    setIsResetting(true);
    disconnectChat();
    clearMessages();
    try {
      await resetRelationship();
    } finally {
      setIsResetting(false);
      // useEffect will handle reconnection after state updates
    }
  }, [disconnectChat, clearMessages, resetRelationship]);

  // Handle switch user
  const handleSwitchUser = useCallback(async (userId: string) => {
    setIsResetting(true);
    disconnectChat();
    clearMessages();
    try {
      await switchUser(userId);
    } finally {
      setIsResetting(false);
      // useEffect will handle reconnection after state updates
    }
  }, [disconnectChat, clearMessages, switchUser]);

  // Compute voice connection state
  const voiceConnectionState = voiceConnected
    ? 'connected'
    : voiceConnecting
      ? 'connecting'
      : 'disconnected';

  // Handler for when companion config is saved - restart voice session with updated config
  const handleCompanionConfigSaved = useCallback(async () => {
    // Only restart if we have an active voice session
    if (voiceConnected || voiceConnecting) {
      console.log('[COMPANION_SIMULATOR] Config saved, restarting voice session with updated config');
      stopSession();
      // Small delay to ensure clean shutdown before restart
      setTimeout(() => {
        startSession();
      }, 100);
    }
  }, [voiceConnected, voiceConnecting, stopSession, startSession]);

  // Register the config save callback with the provider
  useEffect(() => {
    registerOnSaveCallback(handleCompanionConfigSaved);
    return () => {
      unregisterOnSaveCallback(handleCompanionConfigSaved);
    };
  }, [registerOnSaveCallback, unregisterOnSaveCallback, handleCompanionConfigSaved]);

  // Handle phone call
  const handlePhoneCall = useCallback(async (phoneNumber: string) => {
    await dialOut(phoneNumber);
    setPhoneModalOpen(false);
  }, [dialOut]);

  // Render voice mode
  const renderVoiceMode = () => (
    <div className="h-full flex flex-col items-center justify-center">
      <VoiceOrb
        connectionState={voiceConnectionState}
        userAmplitude={userAmplitude}
        companionAmplitude={companionAmplitude}
        onConnect={startSession}
        onDisconnect={stopSession}
        onPause={pauseSession}
        onResume={resumeSession}
        isPaused={isPaused}
        isCompanionSpeaking={isCompanionSpeaking}
      />
      {/* Phone dial button */}
      <div className="mt-6">
        <PhoneDialButton
          onClick={() => {
            // Reset any previous call state before opening modal
            if (twilioCallStatus === 'error' || twilioCallStatus === 'ended') {
              resetTwilioCall();
            }
            setPhoneModalOpen(true);
          }}
          onEndCall={() => {
            // Reset call status when user clicks to end
            resetTwilioCall();
          }}
          status={twilioCallStatus}
          disabled={!selectedCompanionId || !currentUserId}
        />
      </div>
      {/* Phone dial modal */}
      <PhoneDialModal
        open={phoneModalOpen}
        onCall={handlePhoneCall}
        onCancel={() => setPhoneModalOpen(false)}
        isDialing={twilioCallStatus === 'dialing'}
        error={twilioError}
      />
    </div>
  );

  // Render text mode
  const renderTextMode = () => (
    <div className="h-full flex flex-col bg-black">
      {/* Messages */}
      <div className="flex-1 min-h-0 px-4">
        <div className="mx-auto w-full max-w-2xl h-full">
          <ChatThread autoScroll className="space-y-6 h-full">
            {messages.map((msg: ChatMessage, index: number) => {
              const isLastAssistantMessage =
                msg.role === 'assistant' &&
                index === messages.length - 1 &&
                !isStreaming;
              const showTraceBadge = debugMode && isLastAssistantMessage && traceEvents.length > 0;

              // If we need to show trace badge, wrap in a container
              if (showTraceBadge) {
                return (
                  <div key={msg.id || `msg-${index}`} className="flex flex-col items-start gap-2">
                    <DebugTraceBadges
                      traceEvents={traceEvents}
                      isStreaming={false}
                    />
                    <MessageBubble
                      role={msg.role}
                      timestamp={msg.created_at}
                    >
                      {msg.content}
                    </MessageBubble>
                  </div>
                );
              }

              // Regular message without wrapper
              return (
                <MessageBubble
                  key={msg.id || `msg-${index}`}
                  role={msg.role}
                  timestamp={msg.created_at}
                >
                  {msg.content}
                </MessageBubble>
              );
            })}
            {(isStreaming || streamingContent) && (
              <div className="flex flex-col items-start gap-2">
                {/* Debug trace badges for layered mode (shows trace events) */}
                {debugMode && traceEvents.length > 0 && (
                  <DebugTraceBadges
                    traceEvents={traceEvents}
                    isStreaming={isStreaming}
                  />
                )}
                {/* Streaming status for legacy mode (only when status events exist) */}
                {debugMode && traceEvents.length === 0 && statusEvents.length > 0 && (
                  <StreamingStatus events={statusEvents} isStreaming={isStreaming} />
                )}
                {streamingContent && (
                  <MessageBubble role="assistant">
                    {streamingContent}
                  </MessageBubble>
                )}
              </div>
            )}
          </ChatThread>
        </div>
      </div>

      {/* Input area */}
      <div className="sticky bottom-0 bg-black/40 pt-4 px-4 pb-6">
        <div className="mx-auto w-full max-w-2xl">
          {/* Staged images preview */}
          {stagedImages.length > 0 && (
            <div className="mb-3 rounded-xl bg-[#1f1f1f]">
              <StagedImagesPreview
                images={stagedImages}
                onRemove={removeStagedImage}
              />
            </div>
          )}

          <ChatInput
            value={messageInput}
            onChange={setMessageInput}
            onSubmit={(event) => {
              event.preventDefault();
              handleSendMessage();
            }}
            placeholder="Message..."
            disabled={!chatConnected || isUploadingImages}
            sending={isStreaming || isUploadingImages}
            inputWrapperClassName="rounded-full px-4 pt-4 pb-3 bg-[#3C3C3C]"
            trailingSlot={
              <button
                onClick={handleVoiceToggle}
                className="flex h-12 w-12 items-center justify-center rounded-full bg-[#3C3C3C] text-white transition hover:bg-white hover:text-black"
                title="Switch to Voice Mode"
                type="button"
              >
                <Icon name="mic" size={16} color="currentColor" />
              </button>
            }
          />
        </div>
      </div>

      {/* Connection status */}
      {chatConnecting && (
        <div className="absolute inset-0 bg-black/50 flex items-center justify-center">
          <div className="text-white text-sm">Connecting...</div>
        </div>
      )}

      {chatError && (
        <div className="absolute bottom-20 left-4 right-4 bg-red-500/20 text-red-400 text-sm px-4 py-2 rounded-lg">
          {chatError.message}
        </div>
      )}
    </div>
  );

  // Render empty state
  const renderEmptyState = () => (
    <div className="h-full flex flex-col items-center justify-center">
      <h2 className={`text-3xl font-light leading-[0.9] tracking-[-0.04em] mb-6 ${chatConnecting ? 'text-white/50' : 'text-white'}`}>
        {chatConnecting ? 'Connecting...' : 'Test your companion'}
      </h2>
      <div className="w-full max-w-2xl px-4">
        <ChatInput
          value={messageInput}
          onChange={setMessageInput}
          onSubmit={(event) => {
            event.preventDefault();
            handleSendMessage();
          }}
          placeholder={chatConnecting ? 'Establishing connection...' : 'Message...'}
          disabled={!chatConnected || isUploadingImages}
          sending={isStreaming || isUploadingImages}
          inputWrapperClassName={`rounded-full px-4 pt-4 pb-3 ${chatConnecting ? 'bg-[#2a2a2a]' : 'bg-[#3C3C3C]'}`}
          trailingSlot={
            <button
              onClick={handleVoiceToggle}
              className={`flex h-12 w-12 items-center justify-center rounded-full transition ${chatConnecting ? 'bg-[#2a2a2a] text-white/50' : 'bg-[#3C3C3C] text-white hover:bg-white hover:text-black'}`}
              title="Switch to Voice Mode"
              disabled={chatConnecting}
              type="button"
            >
              <Icon name="mic" size={16} color="currentColor" />
            </button>
          }
        />
      </div>
    </div>
  );

  const showEmptyState = mode === 'text' && messages.length === 0 && !isStreaming;

  return (
    <div className="h-full flex flex-col relative overflow-visible">
      {/* Top navbar */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-white/10 overflow-visible">
        {/* Left: Relationship selector */}
        <RelationshipSelector
          currentUserId={currentUserId}
          currentMessageCount={currentRelationship?.message_count ?? 0}
          lastInteractionAt={currentRelationship?.last_interaction_at ?? null}
          testUsers={testUsers}
          isLoading={relationshipLoading}
          disabled={!selectedCompanionId}
          onSwitchUser={handleSwitchUser}
          onCreateNewUser={handleCreateNewUser}
          onReset={handleReset}
        />

        {/* Right: Controls */}
        <div className="flex items-center gap-2">
          {/* Debug Toggle */}
          <Tooltip content={debugMode ? 'Disable debug mode' : 'Enable debug mode'}>
            <button
              onClick={() => setDebugMode(!debugMode)}
              className={`flex items-center justify-center w-9 h-9 rounded-full transition-colors ${
                debugMode
                  ? 'bg-amber-500/20 text-amber-400 hover:bg-amber-500/30'
                  : 'bg-[var(--color-gray-button)] text-white/60 hover:bg-[var(--color-gray-button-hover)] hover:text-white'
              }`}
            >
              <Icon name="bug" size={14} color="currentColor" />
            </button>
          </Tooltip>

          {/* Mode Toggle */}
          <button
            onClick={handleModeToggle}
            className="px-4 py-2 rounded-full bg-[var(--color-gray-button)] text-white text-sm font-medium transition-colors hover:bg-[var(--color-gray-button-hover)]"
          >
            {mode === 'text' ? 'Switch to Voice' : 'Switch to Text'}
          </button>
        </div>
      </div>

      {/* Content area */}
      <div className="flex-1 overflow-hidden relative bg-black">
        {mode === 'voice'
          ? renderVoiceMode()
          : showEmptyState
            ? renderEmptyState()
            : renderTextMode()}
      </div>
    </div>
  );
}
