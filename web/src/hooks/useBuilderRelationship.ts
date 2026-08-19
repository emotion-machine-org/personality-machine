/**
 * Hook for managing builder test relationships in the dashboard simulator.
 *
 * Replaces the old builderUser.ts localStorage-based approach with
 * V2 relationship-based storage.
 */

import { useAuth, useUser } from '@clerk/nextjs';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useCallback, useState, useEffect } from 'react';
import { apiClient, type TestUserSummary, type TestUserDetail } from '@/lib/api';

// localStorage key for current builder user ID
const BUILDER_USER_KEY_PREFIX = 'em.builderUserId';

function getStorageKey(clerkUserId: string): string {
  return `${BUILDER_USER_KEY_PREFIX}.${clerkUserId}`;
}

function generateBuilderUserId(clerkUserId: string): string {
  return `builder-${clerkUserId.slice(0, 8)}-${crypto.randomUUID().slice(0, 8)}`;
}

// Read builder user ID from localStorage (or create if not exists)
function getOrCreateBuilderUserId(clerkUserId: string): string {
  const storageKey = getStorageKey(clerkUserId);
  let userId = localStorage.getItem(storageKey);
  if (!userId) {
    userId = generateBuilderUserId(clerkUserId);
    localStorage.setItem(storageKey, userId);
  }
  return userId;
}

interface UseBuilderRelationshipOptions {
  companionId: string | null;
  recreateAfterReset?: boolean;
  resetBehavior?: 'delete_relationship' | 'clear_messages_only' | 'clear_messages_and_profile';
}

interface UseBuilderRelationshipReturn {
  // Current state
  currentUserId: string | null;
  currentRelationship: TestUserDetail | null;
  testUsers: TestUserSummary[];
  isLoading: boolean;
  error: Error | null;

  // Actions
  switchUser: (userId: string) => Promise<void>;
  createNewUser: () => Promise<string>;
  resetRelationship: () => Promise<void>;
  refreshRelationship: () => void;
}

export function useBuilderRelationship({
  companionId,
  recreateAfterReset = true,
  resetBehavior = 'clear_messages_only',
}: UseBuilderRelationshipOptions): UseBuilderRelationshipReturn {
  const { getToken } = useAuth();
  const { user: clerkUser } = useUser();
  const queryClient = useQueryClient();

  const clerkUserId = clerkUser?.id ?? null;

  // Current builder user ID - stored in state so it updates when changed
  const [currentUserId, setCurrentUserId] = useState<string | null>(null);

  // Initialize from localStorage when clerkUserId is available
  useEffect(() => {
    if (typeof window === 'undefined' || !clerkUserId) {
      setCurrentUserId(null);
      return;
    }
    setCurrentUserId(getOrCreateBuilderUserId(clerkUserId));
  }, [clerkUserId]);

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

  // Query: List all test users for this companion
  const testUsersQuery = useQuery<TestUserSummary[]>({
    queryKey: ['testUsers', companionId],
    queryFn: async () => {
      if (!companionId) return [];
      const token = await getAuthToken();
      return apiClient.listTestUsers(companionId, token);
    },
    enabled: !!companionId,
    staleTime: 30 * 1000, // 30 seconds
  });

  // Query: Get current relationship details
  const relationshipQuery = useQuery<TestUserDetail | null>({
    queryKey: ['testUser', companionId, currentUserId],
    queryFn: async () => {
      if (!companionId || !currentUserId) return null;
      const token = await getAuthToken();
      try {
        return await apiClient.getTestUser(companionId, currentUserId, token);
      } catch {
        // Relationship doesn't exist yet - that's okay
        return null;
      }
    },
    enabled: !!companionId && !!currentUserId,
    staleTime: 10 * 1000, // 10 seconds
    retry: false, // Don't retry on 404 (relationship may not exist yet)
  });

  // Mutation: Ensure relationship exists (used when switching users)
  const ensureRelationshipMutation = useMutation({
    mutationFn: async (userId: string) => {
      if (!companionId) throw new Error('No companion selected');
      const token = await getAuthToken();
      return apiClient.ensureTestUser(companionId, userId, token);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['testUsers', companionId] });
      queryClient.invalidateQueries({ queryKey: ['testUser', companionId] });
    },
  });

  // Mutation: Delete relationship (legacy behavior)
  const deleteRelationshipMutation = useMutation({
    mutationFn: async (userId: string) => {
      if (!companionId) throw new Error('No companion selected');
      const token = await getAuthToken();
      return apiClient.deleteTestUser(companionId, userId, token);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['testUsers', companionId] });
      queryClient.invalidateQueries({ queryKey: ['testUser', companionId] });
    },
  });

  // Mutation: Reset relationship runtime state while keeping relationship/config.
  const resetConversationMutation = useMutation({
    mutationFn: async (userId: string) => {
      if (!companionId) throw new Error('No companion selected');
      const token = await getAuthToken();
      return apiClient.resetTestUserConversation(companionId, userId, token);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['testUsers', companionId] });
      queryClient.invalidateQueries({ queryKey: ['testUser', companionId] });
    },
  });

  // Mutation: Reset profile + messages (legacy onboarding behavior)
  const resetProfileMutation = useMutation({
    mutationFn: async (userId: string) => {
      if (!companionId) throw new Error('No companion selected');
      const token = await getAuthToken();
      return apiClient.resetTestUserProfile(companionId, userId, token);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['testUsers', companionId] });
      queryClient.invalidateQueries({ queryKey: ['testUser', companionId] });
    },
  });

  // Action: Switch to a different user
  const switchUser = useCallback(
    async (userId: string) => {
      if (!clerkUserId) return;

      // Update localStorage
      const storageKey = getStorageKey(clerkUserId);
      localStorage.setItem(storageKey, userId);

      // Update state immediately so UI reflects the change
      setCurrentUserId(userId);

      // Ensure relationship exists
      await ensureRelationshipMutation.mutateAsync(userId);

      // Invalidate queries to refresh
      queryClient.invalidateQueries({ queryKey: ['testUser', companionId] });
    },
    [clerkUserId, companionId, ensureRelationshipMutation, queryClient]
  );

  // Action: Create a new test user
  const createNewUser = useCallback(async () => {
    if (!clerkUserId) throw new Error('Not authenticated');

    const newUserId = generateBuilderUserId(clerkUserId);

    // Update localStorage
    const storageKey = getStorageKey(clerkUserId);
    localStorage.setItem(storageKey, newUserId);

    // Update state immediately so UI reflects the change
    setCurrentUserId(newUserId);

    // Ensure relationship exists
    await ensureRelationshipMutation.mutateAsync(newUserId);

    return newUserId;
  }, [clerkUserId, ensureRelationshipMutation]);

  // Action: Reset current relationship based on configured behavior
  const resetRelationship = useCallback(async () => {
    if (!currentUserId) return;

    if (resetBehavior === 'clear_messages_only') {
      await resetConversationMutation.mutateAsync(currentUserId);
      return;
    }

    if (resetBehavior === 'clear_messages_and_profile') {
      await resetProfileMutation.mutateAsync(currentUserId);
      return;
    }

    // Delete the relationship - it will be recreated when WebSocket reconnects
    await deleteRelationshipMutation.mutateAsync(currentUserId);

    // Optionally recreate immediately to avoid transient 404s in some surfaces.
    if (recreateAfterReset) {
      await ensureRelationshipMutation.mutateAsync(currentUserId);
    }
  }, [
    currentUserId,
    resetBehavior,
    resetConversationMutation,
    resetProfileMutation,
    deleteRelationshipMutation,
    ensureRelationshipMutation,
    recreateAfterReset,
  ]);

  // Action: Refresh relationship data
  const refreshRelationship = useCallback(() => {
    queryClient.invalidateQueries({ queryKey: ['testUsers', companionId] });
    queryClient.invalidateQueries({ queryKey: ['testUser', companionId, currentUserId] });
  }, [queryClient, companionId, currentUserId]);

  return {
    currentUserId,
    currentRelationship: relationshipQuery.data ?? null,
    testUsers: testUsersQuery.data ?? [],
    isLoading: testUsersQuery.isLoading || relationshipQuery.isLoading,
    error: testUsersQuery.error ?? relationshipQuery.error ?? null,

    switchUser,
    createNewUser,
    resetRelationship,
    refreshRelationship,
  };
}
