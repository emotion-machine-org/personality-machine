import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, render, waitFor } from '@testing-library/react';
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';

import { useUpdateCompanionMeta } from '@/hooks/useCompanions';

vi.mock('@clerk/nextjs', () => ({
  useAuth: () => ({
    getToken: vi.fn(),
    userId: 'user-123',
  }),
}));

const updateCompanionMeta = vi.fn();

vi.mock('@/lib/api', () => ({
  apiClient: {
    updateCompanionMeta: (...args: unknown[]) => updateCompanionMeta(...args),
  },
}));

describe('useUpdateCompanionMeta', () => {
  const originalDisableAuth = process.env.NEXT_PUBLIC_DISABLE_AUTH;

  beforeEach(() => {
    process.env.NEXT_PUBLIC_DISABLE_AUTH = 'true';
    updateCompanionMeta.mockResolvedValue({});
  });

  afterEach(() => {
    process.env.NEXT_PUBLIC_DISABLE_AUTH = originalDisableAuth;
    vi.clearAllMocks();
  });

  it('invalidates the companion share query after a successful mutation', async () => {
    const queryClient = new QueryClient();
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries');

    let latestMutation: ReturnType<typeof useUpdateCompanionMeta> | null = null;

    const Harness: React.FC<{ onReady: (mutation: ReturnType<typeof useUpdateCompanionMeta>) => void }> = ({
      onReady,
    }) => {
      const mutation = useUpdateCompanionMeta();
      React.useEffect(() => {
        onReady(mutation);
      }, [mutation, onReady]);
      return null;
    };

    render(
      <QueryClientProvider client={queryClient}>
        <Harness
          onReady={(mutation) => {
            latestMutation = mutation;
          }}
        />
      </QueryClientProvider>
    );

    await waitFor(() => {
      expect(latestMutation).toBeTruthy();
    });

    await act(async () => {
      await latestMutation!.mutateAsync({ id: 'companion-1', meta: { name: 'Renamed Companion' } });
    });

    expect(updateCompanionMeta).toHaveBeenCalledWith(
      'companion-1',
      { name: 'Renamed Companion' },
      'mock-dev-token'
    );

    const shareInvalidation = invalidateSpy.mock.calls.find(
      ([filters]) => Array.isArray(filters?.queryKey) && filters.queryKey[0] === 'companion-share'
    );
    expect(shareInvalidation?.[0]?.queryKey).toEqual(['companion-share', 'companion-1']);

    invalidateSpy.mockRestore();
    queryClient.clear();
  });
});
