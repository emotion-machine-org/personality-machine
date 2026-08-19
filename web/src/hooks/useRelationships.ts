import { useAuth } from '@clerk/nextjs';
import { useQuery, useMutation, useQueryClient, keepPreviousData } from '@tanstack/react-query';
import { apiClient } from '@/lib/api';
import type { RelationshipListResponse } from '@/lib/types';
import type { TestUserMessage } from '@/lib/api';

async function getToken(
  getTokenFn: ReturnType<typeof useAuth>['getToken']
): Promise<string | null> {
  const isAuthDisabled = process.env.NEXT_PUBLIC_DISABLE_AUTH === 'true';
  if (isAuthDisabled) return 'mock-dev-token';
  return getTokenFn(
    process.env.NEXT_PUBLIC_CLERK_JWT_TEMPLATE
      ? { template: process.env.NEXT_PUBLIC_CLERK_JWT_TEMPLATE }
      : undefined
  );
}

export function useRelationships(
  companionId: string | null,
  params?: { limit?: number; offset?: number; search?: string }
) {
  const { getToken: getTokenFn, isLoaded, isSignedIn } = useAuth();
  const isAuthDisabled = process.env.NEXT_PUBLIC_DISABLE_AUTH === 'true';
  const queryEnabled = (isAuthDisabled || (isLoaded && isSignedIn)) && !!companionId;

  return useQuery<RelationshipListResponse>({
    queryKey: ['relationships', companionId, params?.limit, params?.offset, params?.search],
    queryFn: async () => {
      const token = await getToken(getTokenFn);
      return apiClient.listRelationships(companionId!, params, token);
    },
    staleTime: 30 * 1000,
    placeholderData: keepPreviousData,
    enabled: queryEnabled,
  });
}

export function useDeleteRelationship(companionId: string | null) {
  const { getToken: getTokenFn } = useAuth();
  const queryClient = useQueryClient();

  return useMutation<void, Error, string>({
    mutationFn: async (relationshipId: string) => {
      const token = await getToken(getTokenFn);
      return apiClient.deleteRelationship(companionId!, relationshipId, token);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['relationships', companionId] });
    },
  });
}

export function useRelationshipMessages(
  companionId: string | null,
  userId: string | null,
  limit?: number
) {
  const { getToken: getTokenFn, isLoaded, isSignedIn } = useAuth();
  const isAuthDisabled = process.env.NEXT_PUBLIC_DISABLE_AUTH === 'true';
  const queryEnabled = (isAuthDisabled || (isLoaded && isSignedIn)) && !!companionId && !!userId;

  return useQuery<TestUserMessage[]>({
    queryKey: ['relationship-messages', companionId, userId, limit],
    queryFn: async () => {
      const token = await getToken(getTokenFn);
      return apiClient.getTestUserMessages(companionId!, userId!, limit, token);
    },
    staleTime: 15 * 1000,
    enabled: queryEnabled,
  });
}
