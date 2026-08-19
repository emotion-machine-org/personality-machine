'use client';

import { useCallback, useEffect, useState } from 'react';
import { motion } from 'framer-motion';
import { GenerativeArtCanvas } from '@/components/generative-art';
import { useWebSocketSession } from '@/hooks/useWebSocketSession';
import { useVoiceConversationState } from '@/hooks/useVoiceConversationState';
interface VoiceOnboardingProps {
  companionId: string;
  internalUserId: string | null;
  onComplete: (companionId: string) => void;
  onSwitchToText: () => void;
}

export function VoiceOnboarding({ companionId, internalUserId, onComplete, onSwitchToText }: VoiceOnboardingProps) {
  const [companionCreated, setCompanionCreated] = useState<{
    id: string;
    name: string;
  } | null>(null);
  const [error, setError] = useState<string | null>(null);

  const {
    isConnecting,
    isConnected,
    isPaused,
    userAmplitude,
    companionAmplitude,
    isCompanionSpeaking,
    startSession,
    stopSession,
    pauseSession,
    resumeSession,
    setOnJsonMessage,
  } = useWebSocketSession();

  // Use client-side conversation state detection (same logic as VoiceOrb)
  const { isProcessing } = useVoiceConversationState({
    isActive: isConnected && !isPaused,
    userAmplitude,
    isCompanionSpeaking,
    isConnecting,
  });

  // Handle JSON messages from WebSocket (tool_result events)
  useEffect(() => {
    setOnJsonMessage((message) => {
      // Check for tool_result event from companion creation
      if (message.type === 'tool_result' && message.tool === 'create_companion_from_answers') {
        if (message.success && message.result) {
          // Tool succeeded - companion was created
          const companionId = message.result.companion_id;
          const companionName = message.result.companion_name || 'Your Companion';
          setCompanionCreated({ id: companionId, name: companionName });

          // Stop the session gracefully and call completion handler
          setTimeout(async () => {
            await stopSession();
            onComplete(companionId);
          }, 3000);
        } else {
          setError('Failed to create companion. Please try again.');
        }
      }
    });

    return () => {
      setOnJsonMessage(null);
    };
  }, [setOnJsonMessage, stopSession, onComplete]);

  const handleClick = useCallback(async () => {
    if (isConnecting) return;

    if (!isConnected) {
      if (!internalUserId) {
        setError('Loading user data...');
        return;
      }
      try {
        setError(null);
        await startSession({
          companionId,
          clientExternalUserId: internalUserId,
          voiceConfig: {
            pipeline_type: 'stt-llm-tts',
            stt_provider: 'cartesia',
            llm_provider: 'claude-haiku-4.5',
            tts_provider: 'elevenlabs',
            voice_name: 'Sarah',
            temperature: 0.7,
          },
          systemPrompt: '',
          // Note: Profile/messages already cleared on page load in OnboardingFlow
        });
      } catch {
        setError('Failed to connect. Please try again.');
      }
    } else if (isPaused) {
      await resumeSession();
    } else {
      await pauseSession();
    }
  }, [isConnecting, isConnected, isPaused, startSession, pauseSession, resumeSession, internalUserId, companionId]);

  const handleDisconnect = useCallback(async () => {
    await stopSession();
  }, [stopSession]);

  const getStatusText = () => {
    // Special states take priority
    if (companionCreated) {
      return `Creating ${companionCreated.name}...`;
    }
    if (error) {
      return 'Connection error';
    }
    if (isConnecting) {
      return 'Connecting...';
    }
    if (!isConnected) {
      return 'Tap to start';
    }
    if (isPaused) {
      return 'Paused';
    }
    // Use client-side timeout detection (same as VoiceOrb)
    if (isProcessing) {
      return 'Thinking...';
    }
    if (isCompanionSpeaking) {
      return 'Speaking...';
    }
    return 'Listening...';
  };

  return (
    <div className="min-h-screen bg-[var(--color-panel-bg)] flex flex-col items-center justify-center p-6">
      <motion.div
        initial={{ opacity: 0, scale: 0.9 }}
        animate={{ opacity: 1, scale: 1 }}
        transition={{ duration: 0.4 }}
        className="flex flex-col items-center"
      >
        <button
          type="button"
          onClick={handleClick}
          disabled={isConnecting || !!companionCreated}
          className="focus:outline-none disabled:cursor-not-allowed"
        >
          <GenerativeArtCanvas
            width={500}
            height={500}
            userAmplitude={userAmplitude}
            companionAmplitude={companionAmplitude}
            isThinking={isProcessing || isConnecting}
            palette="velvet"
          />
        </button>

        <motion.p
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: 0.3 }}
          className="mt-8 text-[50px] font-light tracking-[-0.04em] leading-[100%] text-white/60"
        >
          {getStatusText()}
        </motion.p>

        {error && (
          <motion.p
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            className="mt-4 text-red-400 text-sm font-light text-center max-w-md"
          >
            {error}
          </motion.p>
        )}

        {!isConnected && !isConnecting && !error && (
          <motion.p
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ delay: 0.5 }}
            className="mt-4 text-white/40 text-sm font-light text-center max-w-md"
          >
            Talk with our onboarding guide to create your first companion.
          </motion.p>
        )}

        {companionCreated && (
          <motion.p
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            className="mt-4 text-white/60 text-sm font-light text-center max-w-md"
          >
            Redirecting to your new companion...
          </motion.p>
        )}

      </motion.div>

      <div className="mt-16 flex gap-4">
        {isConnected && !companionCreated && (
          <motion.button
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            type="button"
            onClick={handleDisconnect}
            className="text-white/40 hover:text-white/60 text-sm font-light transition-colors"
          >
            End Session
          </motion.button>
        )}

        {!companionCreated && (
          <motion.button
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ delay: 0.7 }}
            type="button"
            onClick={onSwitchToText}
            className="text-white/40 hover:text-white/60 text-sm font-light transition-colors"
          >
            Switch to Text Mode
          </motion.button>
        )}
      </div>
    </div>
  );
}

export default VoiceOnboarding;
