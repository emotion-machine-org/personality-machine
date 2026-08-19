import { useAuth } from '@clerk/nextjs';
import { useCallback } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  apiClient,
  type Companion,
  type CompanionConfig,
  type CompanionVersionSummary,
} from '@/lib/api';

// Hook for fetching all companions
export const useCompanions = () => {
  const { getToken, isLoaded, isSignedIn, userId: clerkUserId } = useAuth();
  const isAuthDisabled = process.env.NEXT_PUBLIC_DISABLE_AUTH === 'true';
  const userScope = isAuthDisabled ? 'dev-mode' : clerkUserId ?? 'unauthenticated';
  const queryEnabled = isAuthDisabled || (isLoaded && isSignedIn && !!clerkUserId);

  return useQuery<Companion[]>({
    queryKey: ['companions', userScope],
    queryFn: async () => {
      try {
        let token: string | null = null;

        if (isAuthDisabled) {
          // Use mock token for development - server will recognize this and bypass auth
          token = 'mock-dev-token';
        } else {
          token = await getToken(
            process.env.NEXT_PUBLIC_CLERK_JWT_TEMPLATE
              ? { template: process.env.NEXT_PUBLIC_CLERK_JWT_TEMPLATE }
              : undefined
          );
        }

        const result = await apiClient.getCompanions(token);
        return result;
      } catch (error) {
        console.error('Error in useCompanions:', error);
        throw error;
      }
    },
    staleTime: 2 * 60 * 1000, // 2 minutes
    retry: 3,
    retryDelay: 1000,
    enabled: queryEnabled,
  });
};

// Hook for fetching a specific companion
export const useCompanion = (id: string | null) => {
  const { getToken, isLoaded, isSignedIn, userId: clerkUserId } = useAuth();
  const isAuthDisabled = process.env.NEXT_PUBLIC_DISABLE_AUTH === 'true';
  const userScope = isAuthDisabled ? 'dev-mode' : clerkUserId ?? 'unauthenticated';
  const queryEnabled = !!id && (isAuthDisabled || (isLoaded && isSignedIn && !!clerkUserId));

  const query = useQuery<CompanionConfig>({
    queryKey: ['companions', userScope, id],
    queryFn: async () => {
      let token: string | null = null;

      if (isAuthDisabled) {
        // Use mock token for development - server will recognize this and bypass auth
        token = 'mock-dev-token';
      } else {
        token = await getToken(
          process.env.NEXT_PUBLIC_CLERK_JWT_TEMPLATE
            ? { template: process.env.NEXT_PUBLIC_CLERK_JWT_TEMPLATE }
            : undefined
        );
      }

      return apiClient.getCompanion(id!, token);
    },
    enabled: queryEnabled,
    staleTime: 5 * 60 * 1000, // 5 minutes - avoid constant refetching during sessions
  });

  return {
    ...query,
    // Expose refetch for manual cache refresh
    refetchCompanion: query.refetch
  };
};

// Hook for updating a companion
export const useUpdateCompanion = () => {
  const { getToken, userId: clerkUserId } = useAuth();
  const queryClient = useQueryClient();
  const isAuthDisabled = process.env.NEXT_PUBLIC_DISABLE_AUTH === 'true';
  const userScope = isAuthDisabled ? 'dev-mode' : clerkUserId ?? 'unauthenticated';

  return useMutation({
    mutationFn: async ({ id, config }: { id: string; config: Partial<CompanionConfig> }) => {
      const token = await getToken(
        process.env.NEXT_PUBLIC_CLERK_JWT_TEMPLATE
          ? { template: process.env.NEXT_PUBLIC_CLERK_JWT_TEMPLATE }
          : undefined
      );
      return apiClient.updateCompanion(id, config, token);
    },
    onSuccess: (data, variables) => {
      // Update the specific companion in cache
      queryClient.setQueryData(['companions', userScope, variables.id], data);
      // Invalidate companions list to refresh
      queryClient.invalidateQueries({ queryKey: ['companions', userScope] });
      // Ensure cached version history refreshes so consumers see the latest version number
      queryClient.invalidateQueries({ queryKey: ['companionVersions', userScope, variables.id] });
    },
  });
};

// Hook for updating companion metadata (name/description only)
export const useUpdateCompanionMeta = () => {
  const { getToken, userId: clerkUserId } = useAuth();
  const queryClient = useQueryClient();
  const isAuthDisabled = process.env.NEXT_PUBLIC_DISABLE_AUTH === 'true';
  const userScope = isAuthDisabled ? 'dev-mode' : clerkUserId ?? 'unauthenticated';

  return useMutation({
    mutationFn: async ({ id, meta }: { id: string; meta: { name?: string; description?: string } }) => {
      let token: string | null = null;

      if (isAuthDisabled) {
        token = 'mock-dev-token';
      } else {
        token = await getToken(
          process.env.NEXT_PUBLIC_CLERK_JWT_TEMPLATE
            ? { template: process.env.NEXT_PUBLIC_CLERK_JWT_TEMPLATE }
            : undefined
        );
      }
      return apiClient.updateCompanionMeta(id, meta, token);
    },
    onSuccess: (_data, variables) => {
      // Invalidate companions list so name updates reflect in UI
      queryClient.invalidateQueries({ queryKey: ['companions', userScope] });
      // Also invalidate specific companion config cache
      queryClient.invalidateQueries({ queryKey: ['companions', userScope, variables.id] });
      // Ensure share panel picks up the server-side rename
      queryClient.invalidateQueries({ queryKey: ['companion-share', variables.id] });
    },
  });
};


// Hook for creating a new companion
export const useCreateCompanion = () => {
  const { getToken, userId: clerkUserId } = useAuth();
  const queryClient = useQueryClient();
  const isAuthDisabled = process.env.NEXT_PUBLIC_DISABLE_AUTH === 'true';
  const userScope = isAuthDisabled ? 'dev-mode' : clerkUserId ?? 'unauthenticated';

  return useMutation({
    mutationFn: async (companionData: { name: string; description?: string; config?: Partial<CompanionConfig> }) => {
      let token: string | null = null;

      if (isAuthDisabled) {
        // Use mock token for development - server will recognize this and bypass auth
        token = 'mock-dev-token';
      } else {
        token = await getToken(
          process.env.NEXT_PUBLIC_CLERK_JWT_TEMPLATE
            ? { template: process.env.NEXT_PUBLIC_CLERK_JWT_TEMPLATE }
            : undefined
        );
      }

      return apiClient.createCompanion(companionData, token);
    },
    onSuccess: (data, variables) => {
      // Invalidate companions list to show new companion
      queryClient.invalidateQueries({ queryKey: ['companions', userScope] });

      // Return the companion data for further use
      return { ...data, name: variables.name };
    },
  });
};

// Hook for creating default companion for new users
export const useCreateDefaultCompanion = () => {
  const { getToken, userId: clerkUserId } = useAuth();
  const queryClient = useQueryClient();
  const isAuthDisabled = process.env.NEXT_PUBLIC_DISABLE_AUTH === 'true';
  const userScope = isAuthDisabled ? 'dev-mode' : clerkUserId ?? 'unauthenticated';

  return useMutation({
    mutationFn: async () => {
      let token: string | null = null;

      if (isAuthDisabled) {
        token = 'mock-dev-token';
      } else {
        token = await getToken(
          process.env.NEXT_PUBLIC_CLERK_JWT_TEMPLATE
            ? { template: process.env.NEXT_PUBLIC_CLERK_JWT_TEMPLATE }
            : undefined
        );
      }

      return apiClient.createDefaultCompanion(token);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['companions', userScope] });
    },
  });
};

// Hook for fetching companion versions and individual snapshots
export const useCompanionVersions = (
  companionId: string | null,
  options?: { enabled?: boolean }
) => {
  const { getToken, isLoaded, isSignedIn, userId: clerkUserId } = useAuth();
  const queryClient = useQueryClient();
  const isAuthDisabled = process.env.NEXT_PUBLIC_DISABLE_AUTH === 'true';
  const userScope = isAuthDisabled ? 'dev-mode' : clerkUserId ?? 'unauthenticated';
  const authReady = isAuthDisabled || (isLoaded && isSignedIn && !!clerkUserId);
  const queryEnabled = !!companionId && authReady && (options?.enabled ?? true);

  const versionsQuery = useQuery<CompanionVersionSummary[]>({
    queryKey: ['companionVersions', userScope, companionId],
    queryFn: async () => {
      if (!companionId) {
        throw new Error('Companion ID is required');
      }
      let token: string | null = null;

      if (isAuthDisabled) {
        token = 'mock-dev-token';
      } else {
        token = await getToken(
          process.env.NEXT_PUBLIC_CLERK_JWT_TEMPLATE
            ? { template: process.env.NEXT_PUBLIC_CLERK_JWT_TEMPLATE }
            : undefined
        );
      }

      return apiClient.getCompanionVersions(companionId, token);
    },
    enabled: queryEnabled,
    staleTime: 60 * 1000,
  });

  const fetchVersionConfig = useCallback(
    async (versionId: string) => {
      if (!companionId) {
        throw new Error('Companion ID is required to fetch version config');
      }
      if (!authReady) {
        throw new Error('Authentication not ready for fetching companion versions');
      }

      const token = isAuthDisabled
        ? 'mock-dev-token'
        : await getToken(
            process.env.NEXT_PUBLIC_CLERK_JWT_TEMPLATE
              ? { template: process.env.NEXT_PUBLIC_CLERK_JWT_TEMPLATE }
              : undefined
          );

      return queryClient.fetchQuery({
        queryKey: ['companionVersion', userScope, companionId, versionId],
        queryFn: () => apiClient.getCompanionVersionConfig(companionId, versionId, token),
        staleTime: 5 * 60 * 1000,
      });
    },
    [
      companionId,
      getToken,
      isAuthDisabled,
      queryClient,
      authReady,
      userScope,
    ]
  );

  return {
    ...versionsQuery,
    fetchVersionConfig,
  };
};
