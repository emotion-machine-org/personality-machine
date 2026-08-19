import { useAuth } from '@clerk/nextjs';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiClient, type ProjectSecret } from '@/lib/api';

// Hook for fetching all project secrets
export const useProjectSecrets = () => {
  const { getToken, isLoaded, isSignedIn, userId: clerkUserId } = useAuth();
  const isAuthDisabled = process.env.NEXT_PUBLIC_DISABLE_AUTH === 'true';
  const userScope = isAuthDisabled ? 'dev-mode' : clerkUserId ?? 'unauthenticated';
  const queryEnabled = isAuthDisabled || (isLoaded && isSignedIn && !!clerkUserId);

  return useQuery<ProjectSecret[]>({
    queryKey: ['projectSecrets', userScope],
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

      return apiClient.getProjectSecrets(token);
    },
    staleTime: 2 * 60 * 1000, // 2 minutes
    retry: 3,
    retryDelay: 1000,
    enabled: queryEnabled,
  });
};

// Hook for creating a new project secret
export const useCreateProjectSecret = () => {
  const { getToken, userId: clerkUserId } = useAuth();
  const queryClient = useQueryClient();
  const isAuthDisabled = process.env.NEXT_PUBLIC_DISABLE_AUTH === 'true';
  const userScope = isAuthDisabled ? 'dev-mode' : clerkUserId ?? 'unauthenticated';

  return useMutation<ProjectSecret, Error, { name: string; value: string; description?: string }>({
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

      return apiClient.createProjectSecret(data, token);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['projectSecrets', userScope] });
    },
  });
};

// Hook for updating a project secret
export const useUpdateProjectSecret = () => {
  const { getToken, userId: clerkUserId } = useAuth();
  const queryClient = useQueryClient();
  const isAuthDisabled = process.env.NEXT_PUBLIC_DISABLE_AUTH === 'true';
  const userScope = isAuthDisabled ? 'dev-mode' : clerkUserId ?? 'unauthenticated';

  return useMutation<ProjectSecret, Error, { secretName: string; value: string; description?: string }>({
    mutationFn: async ({ secretName, ...data }) => {
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

      return apiClient.updateProjectSecret(secretName, data, token);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['projectSecrets', userScope] });
    },
  });
};

// Hook for deleting a project secret
export const useDeleteProjectSecret = () => {
  const { getToken, userId: clerkUserId } = useAuth();
  const queryClient = useQueryClient();
  const isAuthDisabled = process.env.NEXT_PUBLIC_DISABLE_AUTH === 'true';
  const userScope = isAuthDisabled ? 'dev-mode' : clerkUserId ?? 'unauthenticated';

  return useMutation<{ message: string }, Error, string>({
    mutationFn: async (secretName) => {
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

      return apiClient.deleteProjectSecret(secretName, token);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['projectSecrets', userScope] });
    },
  });
};
