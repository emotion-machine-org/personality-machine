'use client';

import { useState, useCallback, useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { useAuth } from '@clerk/nextjs';
import { useQueryClient } from '@tanstack/react-query';
import { apiClient } from '@/lib/api';
import { ModeSelector, type OnboardingMode } from './ModeSelector';
import { ConversationalTextOnboarding } from './ConversationalTextOnboarding';
import { VoiceOnboarding } from './VoiceOnboarding';

// Fixed UUID for the onboarding companion (must match server seed script)
const ONBOARDING_COMPANION_ID = '00000000-0000-0000-0000-000000000001';

export function OnboardingFlow() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const { getToken } = useAuth();
  const [mode, setMode] = useState<OnboardingMode | null>(null);
  const [internalUserId, setInternalUserId] = useState<string | null>(null);

  // Helper to get fresh token with template
  const getAuthToken = useCallback(async () => {
    return getToken(
      process.env.NEXT_PUBLIC_CLERK_JWT_TEMPLATE
        ? { template: process.env.NEXT_PUBLIC_CLERK_JWT_TEMPLATE }
        : undefined
    );
  }, [getToken]);

  // Fetch internal user UUID and reset relationship on mount (first step)
  // This ensures a fresh start every time user visits onboarding
  useEffect(() => {
    const initOnboarding = async () => {
      try {
        const token = await getAuthToken();
        const user = await apiClient.getCurrentUser(token);

        // Reset profile and messages BEFORE setting internalUserId
        // This prevents race condition where voice session starts before reset completes
        try {
          await apiClient.resetTestUserProfile(ONBOARDING_COMPANION_ID, user.id, token);
        } catch (resetErr) {
          // Non-fatal - relationship may not exist yet (fresh user), or companion not seeded
          console.warn('Failed to reset onboarding state:', resetErr);
        }

        // Set internalUserId after reset attempt - this gates session start
        setInternalUserId(user.id);
      } catch (err) {
        // Fatal - couldn't get user
        console.error('Failed to initialize onboarding:', err);
      }
    };
    initOnboarding();
  }, [getAuthToken]);

  const handleModeSelect = useCallback((selectedMode: OnboardingMode) => {
    setMode(selectedMode);
  }, []);

  // Handle completion from conversational text onboarding
  const handleConversationalComplete = useCallback(async (companionId: string) => {
    // Clear any stale individual companion queries from previous sessions
    queryClient.removeQueries({
      predicate: (query) => {
        const key = query.queryKey;
        return key[0] === 'companions' && key.length === 3;
      },
    });

    // Also clear test user queries which might reference stale companions
    queryClient.removeQueries({
      predicate: (query) => query.queryKey[0] === 'testUsers' || query.queryKey[0] === 'testUser',
    });

    // Refetch companions list
    await queryClient.refetchQueries({
      predicate: (query) => {
        const key = query.queryKey;
        return key[0] === 'companions' && key.length === 2;
      },
    });

    // Redirect to the new companion's dashboard
    if (companionId) {
      router.push(`/?companion=${companionId}`);
    } else {
      router.push('/');
    }
  }, [router, queryClient]);

  const handleSwitchToVoice = useCallback(() => {
    setMode('voice');
  }, []);

  const handleSwitchToText = useCallback(() => {
    setMode('text');
  }, []);

  // Mode selector
  if (mode === null) {
    return <ModeSelector onSelectMode={handleModeSelect} />;
  }

  // Voice mode
  if (mode === 'voice') {
    return (
      <VoiceOnboarding
        companionId={ONBOARDING_COMPANION_ID}
        internalUserId={internalUserId}
        onComplete={handleConversationalComplete}
        onSwitchToText={handleSwitchToText}
      />
    );
  }

  // Text mode
  return (
    <ConversationalTextOnboarding
      companionId={ONBOARDING_COMPANION_ID}
      getToken={getAuthToken}
      onComplete={handleConversationalComplete}
      onSwitchToVoice={handleSwitchToVoice}
    />
  );
}

export default OnboardingFlow;
