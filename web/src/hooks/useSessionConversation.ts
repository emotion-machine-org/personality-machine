/**
 * Hook for managing session-to-conversation lifecycle.
 * Handles the flow from voice sessions to text conversations based on the CONVERSATION_PERSISTENCE_PLAN.
 */

import { useState, useCallback } from 'react';
import { useAuth } from '@clerk/nextjs';
import { API_CONFIG } from '@/lib/config';

const API_BASE = API_CONFIG.BASE_URL;

export type ConversationState = 'idle' | 'voice_active' | 'voice_ended' | 'text_active';

interface UseSessionConversationResult {
  currentConversationId: string | null;
  conversationState: ConversationState;
  startNewVoiceSession: () => Promise<void>;
  endVoiceSession: () => void;
  setConversationFromSession: (sessionId: string) => Promise<void>;
  setConversationDirect: (conversationId: string) => void;
  clearConversation: () => void;
  startTextMode: () => void;
}

export function useSessionConversation(): UseSessionConversationResult {
  const [currentConversationId, setCurrentConversationId] = useState<string | null>(null);
  const [conversationState, setConversationState] = useState<ConversationState>('idle');
  const { getToken } = useAuth();

  const startNewVoiceSession = useCallback(async () => {
    // Keep the existing conversation ID if we have one
    // The conversation ID is passed to the voice session via SessionConfig.conversationId
    setConversationState('voice_active');
  }, []);

  const endVoiceSession = useCallback(() => {
    // Transition to voice_ended state - conversation ID available for text continuation
    setConversationState('voice_ended');
  }, []);

  const setConversationFromSession = useCallback(async (sessionId: string) => {
    if (!sessionId) {
      return;
    }

    try {
      // Look up the conversation created for this session
      const token = await getToken(
        process.env.NEXT_PUBLIC_CLERK_JWT_TEMPLATE
          ? { template: process.env.NEXT_PUBLIC_CLERK_JWT_TEMPLATE }
          : undefined
      );
      const response = await fetch(`${API_BASE}/conversations/by-session/${sessionId}` , {
        headers: {
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
      });

      if (response.ok) {
        const conversationData = await response.json();
        if (conversationData && conversationData.id) {
          setCurrentConversationId(conversationData.id);
          setConversationState('voice_ended');
        } else {
          setConversationState('idle');
        }
      } else {
        setConversationState('idle');
      }
    } catch {
      setConversationState('idle');
    }
  }, [getToken]);

  const setConversationDirect = useCallback((conversationId: string) => {
    setCurrentConversationId(conversationId);
    setConversationState('text_active');
  }, []);

  const startTextMode = useCallback(() => {
    if (currentConversationId) {
      setConversationState('text_active');
    }
  }, [currentConversationId]);

  const clearConversation = useCallback(() => {
    setCurrentConversationId(null);
    setConversationState('idle');
  }, []);

  return {
    currentConversationId,
    conversationState,
    startNewVoiceSession,
    endVoiceSession,
    setConversationFromSession,
    setConversationDirect,
    clearConversation,
    startTextMode
  };
}
