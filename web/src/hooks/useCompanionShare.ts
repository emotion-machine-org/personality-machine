import { useAuth } from '@clerk/nextjs';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiClient, type CompanionShare, type CompanionShareAnalytics } from '@/lib/api';

export type SharePayload = Partial<{
  status: 'draft' | 'active' | 'disabled';
  allow_text: boolean;
  allow_voice: boolean;
  require_auth: boolean;
  expose_status_events: boolean;
  display_name?: string | null;
  // Context copy displayed on the public share page.
  description?: string | null;
  version_id?: string | null;
  config_snapshot?: Record<string, unknown> | null;
}>;

const getTokenOrMock = async (getToken: ReturnType<typeof useAuth>['getToken']) => {
  const isAuthDisabled = process.env.NEXT_PUBLIC_DISABLE_AUTH === 'true';
  if (isAuthDisabled) {
    return 'mock-dev-token';
  }
  return getToken(
    process.env.NEXT_PUBLIC_CLERK_JWT_TEMPLATE
      ? { template: process.env.NEXT_PUBLIC_CLERK_JWT_TEMPLATE }
      : undefined
  );
};

export const useCompanionShare = (companionId: string | null) => {
  const { getToken, isLoaded, isSignedIn } = useAuth();
  const isAuthDisabled = process.env.NEXT_PUBLIC_DISABLE_AUTH === 'true';

  return useQuery<CompanionShare>({
    queryKey: ['companion-share', companionId],
    enabled: !!companionId && (isAuthDisabled || (isLoaded && isSignedIn)),
    queryFn: async () => {
      const token = await getTokenOrMock(getToken);
      return apiClient.getCompanionShare(companionId!, token);
    },
    staleTime: 0,
    refetchOnWindowFocus: true,
    refetchOnReconnect: true,
    refetchOnMount: 'always',
  });
};

export const useCompanionShareAnalytics = (companionId: string | null) => {
  const { getToken, isLoaded, isSignedIn } = useAuth();
  const isAuthDisabled = process.env.NEXT_PUBLIC_DISABLE_AUTH === 'true';

  return useQuery<CompanionShareAnalytics>({
    queryKey: ['companion-share-analytics', companionId],
    enabled: !!companionId && (isAuthDisabled || (isLoaded && isSignedIn)),
    queryFn: async () => {
      const token = await getTokenOrMock(getToken);
      return apiClient.getCompanionShareAnalytics(companionId!, token);
    },
    refetchInterval: 30_000,
  });
};

export const useUpdateCompanionShare = (companionId: string | null) => {
  const { getToken } = useAuth();
  const queryClient = useQueryClient();
  const shareQueryKey = ['companion-share', companionId] as const;
  const analyticsQueryKey = ['companion-share-analytics', companionId] as const;

  return useMutation({
    mutationFn: async (body: SharePayload) => {
      if (!companionId) throw new Error('Missing companionId');
      const token = await getTokenOrMock(getToken);
      return apiClient.updateCompanionShare(companionId, body, token);
    },
    onMutate: async (body: SharePayload) => {
      if (!companionId) return undefined;

      await queryClient.cancelQueries({ queryKey: shareQueryKey });

      const previousShare = queryClient.getQueryData<CompanionShare>(shareQueryKey);

      if (previousShare) {
        const optimisticShare: CompanionShare = {
          ...previousShare,
          ...body,
          status: body.status ?? previousShare.status,
          allow_text: body.allow_text ?? previousShare.allow_text,
          allow_voice: body.allow_voice ?? previousShare.allow_voice,
          require_auth: body.require_auth ?? previousShare.require_auth,
          expose_status_events: body.expose_status_events ?? previousShare.expose_status_events,
          description: body.description ?? previousShare.description,
          display_name: body.display_name ?? previousShare.display_name,
          version_id: body.version_id ?? previousShare.version_id,
          config_snapshot: body.config_snapshot ?? previousShare.config_snapshot,
        };

        queryClient.setQueryData(shareQueryKey, optimisticShare);
      }

      return { previousShare };
    },
    onError: (_err, _body, context) => {
      if (context?.previousShare) {
        queryClient.setQueryData(shareQueryKey, context.previousShare);
      }
    },
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: shareQueryKey });
      queryClient.invalidateQueries({ queryKey: analyticsQueryKey });
    },
  });
};

export const useDisableCompanionShare = (companionId: string | null) => {
  const { getToken } = useAuth();
  const queryClient = useQueryClient();
  const shareQueryKey = ['companion-share', companionId] as const;
  const analyticsQueryKey = ['companion-share-analytics', companionId] as const;

  return useMutation({
    mutationFn: async () => {
      if (!companionId) throw new Error('Missing companionId');
      const token = await getTokenOrMock(getToken);
      return apiClient.disableCompanionShare(companionId, token);
    },
    onMutate: async () => {
      if (!companionId) return undefined;

      await queryClient.cancelQueries({ queryKey: shareQueryKey });

      const previousShare = queryClient.getQueryData<CompanionShare>(shareQueryKey);

      if (previousShare) {
        const optimisticShare: CompanionShare = {
          ...previousShare,
          status: 'disabled',
        };

        queryClient.setQueryData(shareQueryKey, optimisticShare);
      }

      return { previousShare };
    },
    onError: (_err, _variables, context) => {
      if (context?.previousShare) {
        queryClient.setQueryData(shareQueryKey, context.previousShare);
      }
    },
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: shareQueryKey });
      queryClient.invalidateQueries({ queryKey: analyticsQueryKey });
    },
  });
};
