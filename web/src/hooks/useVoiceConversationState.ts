'use client';

/**
 * useVoiceConversationState - Manages voice conversation state machine
 *
 * Matches VoiceOrb's state machine logic exactly:
 * - User speaking detection with amplitude threshold
 * - Processing/thinking state after user stops speaking (1.5s timeout)
 * - Companion speaking state
 *
 * State flow:
 * idle → listening → user → processing → companion → listening → ...
 */

import { useEffect, useRef, useState, useCallback } from 'react';

export type ConversationState = 'idle' | 'listening' | 'user' | 'processing' | 'companion';

// Lower threshold than VoiceOrb (0.08) because smoothed amplitude takes time to build up
// This helps detect speaking faster for onboarding UX
const SPEAKING_THRESHOLD = 0.03;
const USER_SILENCE_BEFORE_THINKING = 1000;

export interface UseVoiceConversationStateInput {
  /** Whether the WebSocket is connected and active */
  isActive: boolean;
  /** Current user voice amplitude (0-1) */
  userAmplitude: number;
  /** Whether the companion is currently speaking */
  isCompanionSpeaking: boolean;
  /** Whether the WebSocket is currently connecting */
  isConnecting?: boolean;
}

export interface UseVoiceConversationStateReturn {
  /** Current conversation state */
  conversationState: ConversationState;
  /** Whether the companion is in processing/thinking state */
  isProcessing: boolean;
  /** Human-readable status text */
  getStatusText: () => string;
}

export function useVoiceConversationState(
  input: UseVoiceConversationStateInput
): UseVoiceConversationStateReturn {
  const { isActive, userAmplitude, isCompanionSpeaking, isConnecting = false } = input;

  const [conversationState, setConversationState] = useState<ConversationState>('idle');
  const processingTimerRef = useRef<number | null>(null);

  // Single useEffect matching VoiceOrb's state machine exactly
  useEffect(() => {
    if (!isActive) {
      setConversationState('idle');
      return;
    }

    const isUserSpeaking = userAmplitude > SPEAKING_THRESHOLD;

    if (isCompanionSpeaking) {
      // Companion is speaking - clear any processing timer
      if (processingTimerRef.current) {
        clearTimeout(processingTimerRef.current);
        processingTimerRef.current = null;
      }
      setConversationState('companion');
    } else if (isUserSpeaking) {
      // User is speaking - clear processing timer
      if (processingTimerRef.current) {
        clearTimeout(processingTimerRef.current);
        processingTimerRef.current = null;
      }
      setConversationState('user');
    } else if (conversationState === 'user') {
      // User stopped speaking - start thinking timer
      if (!processingTimerRef.current) {
        processingTimerRef.current = window.setTimeout(() => {
          setConversationState('processing');
          processingTimerRef.current = null;
        }, USER_SILENCE_BEFORE_THINKING);
      }
    } else if (conversationState === 'companion') {
      // Companion finished speaking - go back to listening
      setConversationState('listening');
    } else if (conversationState === 'idle') {
      // Just became active - transition to listening
      setConversationState('listening');
    }
  }, [isActive, userAmplitude, isCompanionSpeaking, conversationState]);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (processingTimerRef.current) {
        clearTimeout(processingTimerRef.current);
      }
    };
  }, []);

  const isProcessing = conversationState === 'processing';

  const getStatusText = useCallback(() => {
    if (isConnecting) {
      return 'Connecting...';
    }

    switch (conversationState) {
      case 'idle':
        return 'Tap to start';
      case 'listening':
        return 'Listening...';
      case 'user':
        return 'Listening...';
      case 'processing':
        return 'Thinking...';
      case 'companion':
        return 'Speaking...';
      default:
        return '';
    }
  }, [conversationState, isConnecting]);

  return {
    conversationState,
    isProcessing,
    getStatusText,
  };
}
