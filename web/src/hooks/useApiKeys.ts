import { useAuth } from '@clerk/nextjs';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiClient, type ApiKey, type ApiKeyWithSecret } from '@/lib/api';

// Hook for fetching all API keys
export const useApiKeys = () => {
  const { getToken, isLoaded, isSignedIn, userId: clerkUserId } = useAuth();
  const isAuthDisabled = process.env.NEXT_PUBLIC_DISABLE_AUTH === 'true';
  const userScope = isAuthDisabled ? 'dev-mode' : clerkUserId ?? 'unauthenticated';
  const queryEnabled = isAuthDisabled || (isLoaded && isSignedIn && !!clerkUserId);

  return useQuery<ApiKey[]>({
    queryKey: ['apiKeys', userScope],
    queryFn: async () => {
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

      return apiClient.getApiKeys(token);
    },
    staleTime: 2 * 60 * 1000, // 2 minutes
    retry: 3,
    retryDelay: 1000,
    enabled: queryEnabled,
  });
};

// Hook for creating a new API key
export const useCreateApiKey = () => {
  const { getToken, userId: clerkUserId } = useAuth();
  const queryClient = useQueryClient();
  const isAuthDisabled = process.env.NEXT_PUBLIC_DISABLE_AUTH === 'true';
  const userScope = isAuthDisabled ? 'dev-mode' : clerkUserId ?? 'unauthenticated';

  return useMutation<ApiKeyWithSecret, Error, { name?: string }>({
    mutationFn: async (data) => {
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

      return apiClient.createApiKey(data, token);
    },
    onSuccess: () => {
      // Invalidate API keys list to show new key
      queryClient.invalidateQueries({ queryKey: ['apiKeys', userScope] });
    },
  });
};

// Hook for revoking an API key
export const useRevokeApiKey = () => {
  const { getToken, userId: clerkUserId } = useAuth();
  const queryClient = useQueryClient();
  const isAuthDisabled = process.env.NEXT_PUBLIC_DISABLE_AUTH === 'true';
  const userScope = isAuthDisabled ? 'dev-mode' : clerkUserId ?? 'unauthenticated';

  return useMutation<{ message: string }, Error, string>({
    mutationFn: async (keyId) => {
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

      return apiClient.revokeApiKey(keyId, token);
    },
    onSuccess: () => {
      // Invalidate API keys list to reflect revoked status
      queryClient.invalidateQueries({ queryKey: ['apiKeys', userScope] });
    },
  });
};
