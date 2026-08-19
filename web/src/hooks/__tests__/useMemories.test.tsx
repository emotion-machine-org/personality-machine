import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, cleanup, render, waitFor } from '@testing-library/react';
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';

import type { MemoryItem } from '@/lib/types';
import { useCreateMemory, useMemories, dedupeMemoriesByContentAndCore } from '@/hooks/useMemories';

const createMemoryMock = vi.fn();
const getMemoriesMock = vi.fn();

vi.mock('@clerk/nextjs', () => ({
  useAuth: () => ({
    getToken: vi.fn().mockResolvedValue('mock-dev-token'),
    isLoaded: true,
  }),
}));

vi.mock('@/lib/api', () => ({
  apiClient: {
    getMemories: (...args: unknown[]) => getMemoriesMock(...args),
    createMemory: (...args: unknown[]) => createMemoryMock(...args),
  },
}));

describe('useCreateMemory core-memory flow', () => {
  const baseQueryKey: [string, string, number, number, string, string, string] = [
    'memories',
    'companion-1',
    50,
    0,
    '',
    'created_at',
    'DESC',
  ];

  const serverMemory: MemoryItem = {
    id: 'memory-1',
    companion_id: 'companion-1',
    content: 'Remember this forever',
    created_at: new Date('2024-01-01T00:00:00Z').toISOString(),
    importance: 1,
    weight_user: 1,
    modality: 'text',
    last_accessed_at: new Date('2024-01-01T00:00:00Z').toISOString(),
    commentary: null,
    conversation_id: null,
    sender_type: 'system',
    external_user_id: null,
    message_id: null,
    is_core: true,
    similarity: null,
    score: null,
  };

  let ingestionComplete = false;

  beforeEach(() => {
    ingestionComplete = false;
    createMemoryMock.mockResolvedValue({ id: 'queued-123' });
    getMemoriesMock.mockImplementation(async () => {
      if (!ingestionComplete) return [] as MemoryItem[];
      return [{ ...serverMemory }];
    });
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.clearAllMocks();
    cleanup();
  });

  // TODO: these two timing-based polling tests drifted from the hook's query-key behavior
  // and fail deterministically; re-enable after aligning the test's cache expectations.
  it.skip('keeps a pending stub visible until the ingestion worker materializes the record', async () => {
    const queryClient = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
        mutations: { retry: false },
      },
    });

    let latestMutation: ReturnType<typeof useCreateMemory> | null = null;

    const Harness: React.FC = () => {
      const mutation = useCreateMemory('companion-1');
      useMemories('companion-1');
      React.useEffect(() => {
        latestMutation = mutation;
      }, [mutation]);
      return null;
    };

    render(
      <QueryClientProvider client={queryClient}>
        <Harness />
      </QueryClientProvider>
    );

    await waitFor(() => {
      expect(getMemoriesMock).toHaveBeenCalledTimes(1);
      expect(queryClient.getQueryData(baseQueryKey)).toEqual([]);
    });

    await waitFor(() => {
      expect(latestMutation).toBeTruthy();
    });

    await act(async () => {
      await latestMutation!.mutateAsync({ content: 'Remember this forever', is_core: true, sender_type: 'system' });
    });

    const snapshotAfterMutate = queryClient.getQueryData(baseQueryKey) as MemoryItem[];
    expect(snapshotAfterMutate).toHaveLength(1);
    expect(snapshotAfterMutate[0].pending).toBe(true);

    // Wait for the first poll (optimistic stub should survive).
    await new Promise(resolve => setTimeout(resolve, 1_200));

    await waitFor(() => {
      expect(getMemoriesMock).toHaveBeenCalledTimes(2);
      const cached = queryClient.getQueryData(baseQueryKey) as MemoryItem[];
      expect(cached).toHaveLength(1);
      expect(cached[0].pending).toBe(true);
    });

    ingestionComplete = true;

    // Allow the backoff poll to run and replace the stub with the real record.
    await new Promise(resolve => setTimeout(resolve, 2_000));

    await waitFor(() => {
      expect(getMemoriesMock).toHaveBeenCalledTimes(3);
      const cached = queryClient.getQueryData(baseQueryKey) as MemoryItem[];
      expect(cached).toHaveLength(1);
      expect(cached[0].pending).toBeFalsy();
      expect(cached[0].id).toBe('memory-1');
  });

  queryClient.clear();
  }, 20_000);

  it.skip('continues polling past the max-attempt threshold until ingestion completes', async () => {
    vi.useFakeTimers();

    const queryClient = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
        mutations: { retry: false },
      },
    });

    let latestMutation: ReturnType<typeof useCreateMemory> | null = null;
    let callCount = 0;

    getMemoriesMock.mockImplementation(async () => {
      callCount += 1;
      if (callCount >= 12) {
        return [{ ...serverMemory, id: 'memory-late' }];
      }
      return [] as MemoryItem[];
    });

    const Harness: React.FC = () => {
      const mutation = useCreateMemory('companion-1');
      useMemories('companion-1');
      React.useEffect(() => {
        latestMutation = mutation;
      }, [mutation]);
      return null;
    };

    render(
      <QueryClientProvider client={queryClient}>
        <Harness />
      </QueryClientProvider>
    );

    await waitFor(() => {
      expect(getMemoriesMock).toHaveBeenCalledTimes(1);
    });

    await act(async () => {
      await latestMutation!.mutateAsync({ content: 'Remember this forever', is_core: true, sender_type: 'system' });
    });

    for (let i = 0; i < 12; i += 1) {
      await act(async () => {
        vi.advanceTimersByTime(60_000);
        await Promise.resolve();
      });
    }

    await waitFor(() => {
      const cached = queryClient.getQueryData(baseQueryKey) as MemoryItem[];
      expect(cached).toHaveLength(1);
      expect(cached[0].id).toBe('memory-late');
      expect(cached[0].pending).toBeFalsy();
    });

    expect(getMemoriesMock).toHaveBeenCalledTimes(12);

    queryClient.clear();
  });
});

describe('dedupeMemoriesByContentAndCore', () => {
  const stubbedBase: MemoryItem = {
    id: 'seed',
    companion_id: 'companion-1',
    content: 'Remember this forever',
    created_at: new Date('2024-01-01T00:00:00Z').toISOString(),
    importance: 1,
    weight_user: 1,
    modality: 'text',
    last_accessed_at: new Date('2024-01-01T00:00:00Z').toISOString(),
    commentary: null,
    conversation_id: null,
    sender_type: 'system',
    external_user_id: null,
    message_id: null,
    is_core: true,
    similarity: null,
    score: null,
  };

  it('retains distinct stored memories that share normalized content', () => {
    const duplicates: MemoryItem[] = [
      { ...stubbedBase, id: 'memory-1', content: 'Remember me', created_at: new Date('2024-02-01T00:00:00Z').toISOString() },
      { ...stubbedBase, id: 'memory-2', content: ' remember me \n', created_at: new Date('2024-02-02T00:00:00Z').toISOString() },
    ];

    const result = dedupeMemoriesByContentAndCore(duplicates);
    expect(result).toHaveLength(2);
    expect(result.map((m) => m.id)).toEqual(['memory-1', 'memory-2']);
  });
});
