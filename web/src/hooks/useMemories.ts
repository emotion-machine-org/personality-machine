"use client";

import { useEffect, useRef } from 'react';
import { useAuth } from '@clerk/nextjs';
import { useQuery, useMutation, useQueryClient, keepPreviousData } from '@tanstack/react-query';
import type { QueryKey } from '@tanstack/react-query';
import { apiClient } from '@/lib/api';
import type { MemoryItem, MemoryStats, UserAnalyticsSummary, UserMemoriesPayload } from '@/lib/types';

type PendingAwareMemory = MemoryItem & { pending?: boolean };

function normalizeMemoryContent(content: string | null | undefined): string {
  return (content ?? '').replace(/\s+/g, ' ').trim().toLowerCase();
}

function memoryIdentityKey(fragment: Pick<MemoryItem, 'content' | 'is_core'>): string {
  return `${fragment.is_core ? 'core' : 'regular'}::${normalizeMemoryContent(fragment.content)}`;
}

function isPendingMemory(item: MemoryItem): item is PendingAwareMemory {
  return Boolean((item as PendingAwareMemory).pending);
}

export function dedupeMemoriesByContentAndCore(memories: MemoryItem[]): MemoryItem[] {
  const result: MemoryItem[] = [];
  const seenIds = new Set<string>();
  const pendingIndexByIdentity = new Map<string, number>();

  for (const item of memories) {
    const resolvedItem: MemoryItem = item;

    if (seenIds.has(resolvedItem.id)) continue;

    const identity = memoryIdentityKey(item);

    if (isPendingMemory(item)) {
      const existingIndex = pendingIndexByIdentity.get(identity);
      if (existingIndex !== undefined) {
        result[existingIndex] = resolvedItem;
      } else {
        pendingIndexByIdentity.set(identity, result.length);
        result.push(resolvedItem);
      }
      seenIds.add(resolvedItem.id);
      continue;
    }

    const pendingIndex = pendingIndexByIdentity.get(identity);
    if (pendingIndex !== undefined) {
      const current = result[pendingIndex];
      if (current && isPendingMemory(current)) {
        result[pendingIndex] = resolvedItem;
        pendingIndexByIdentity.delete(identity);
        seenIds.add(resolvedItem.id);
        continue;
      }
    }

    result.push(resolvedItem);
    seenIds.add(resolvedItem.id);
  }

  return result;
}

const MEMORY_REFETCH_BASE_DELAY = 900;
const MEMORY_REFETCH_BACKOFF = 1.6;
const MEMORY_REFETCH_MAX_DELAY = 8_000;
const MEMORY_REFETCH_MAX_ATTEMPTS = 8;
const MEMORY_REFETCH_STALLED_DELAY = 30_000;

type PendingState = {
  key: string;
  stub: PendingAwareMemory & { pending: true };
  attempts: number;
};

export function useMemories(
  companionId: string | null,
  params?: { limit?: number; offset?: number; conversation_id?: string; order_by?: 'created_at' | 'last_accessed_at' | 'importance'; order_dir?: 'ASC' | 'DESC'; is_core?: boolean },
  options?: { enabled?: boolean }
) {
  const { getToken, isLoaded } = useAuth();
  return useQuery<MemoryItem[]>({
    queryKey: ['memories', companionId, params?.limit || 50, params?.offset || 0, params?.conversation_id || '', params?.order_by || 'created_at', params?.order_dir || 'DESC', params?.is_core ?? null],
    enabled: !!companionId && isLoaded && (options?.enabled ?? true),
    queryFn: async () => {
      const token = await getToken(
        process.env.NEXT_PUBLIC_CLERK_JWT_TEMPLATE
          ? { template: process.env.NEXT_PUBLIC_CLERK_JWT_TEMPLATE }
          : undefined
      );
      return apiClient.getMemories(companionId!, params, token);
    },
    select: (data) => dedupeMemoriesByContentAndCore(data ?? []),
    staleTime: 60_000,
  });
}

export function useMemoryStats(companionId: string | null) {
  const { getToken, isLoaded } = useAuth();
  return useQuery<MemoryStats>({
    queryKey: ['memory-stats', companionId],
    enabled: !!companionId && isLoaded,
    queryFn: async () => {
      const token = await getToken(
        process.env.NEXT_PUBLIC_CLERK_JWT_TEMPLATE
          ? { template: process.env.NEXT_PUBLIC_CLERK_JWT_TEMPLATE }
          : undefined
      );
      return apiClient.getMemoryStats(companionId!, token);
    },
    staleTime: 60_000,
  });
}

export function useUserMemoriesByExternalId(
  companionId: string | null,
  externalUserId: string | null,
  params?: { limit?: number; offset?: number; order_by?: 'created_at' | 'last_accessed_at' | 'importance'; order_dir?: 'ASC' | 'DESC' }
) {
  const { getToken, isLoaded } = useAuth();
  return useQuery<UserMemoriesPayload, Error>({
    queryKey: [
      'user-memories',
      companionId,
      externalUserId,
      params?.limit ?? 50,
      params?.offset ?? 0,
      params?.order_by ?? 'created_at',
      params?.order_dir ?? 'DESC',
    ],
    enabled: !!companionId && !!externalUserId && isLoaded,
    placeholderData: keepPreviousData,
    queryFn: async () => {
      const token = await getToken(
        process.env.NEXT_PUBLIC_CLERK_JWT_TEMPLATE
          ? { template: process.env.NEXT_PUBLIC_CLERK_JWT_TEMPLATE }
          : undefined
      );
      return apiClient.getUserMemoriesByExternalId(
        companionId!,
        externalUserId!,
        params,
        token,
      );
    },
  });
}

export function useUserAnalyticsSummary(
  companionId: string | null,
  externalUserId: string | null,
) {
  const { getToken, isLoaded } = useAuth();
  return useQuery<UserAnalyticsSummary, Error>({
    queryKey: ['user-analytics-summary', companionId, externalUserId],
    enabled: !!companionId && !!externalUserId && isLoaded,
    queryFn: async () => {
      const token = await getToken(
        process.env.NEXT_PUBLIC_CLERK_JWT_TEMPLATE
          ? { template: process.env.NEXT_PUBLIC_CLERK_JWT_TEMPLATE }
          : undefined
      );
      return apiClient.getUserAnalyticsSummary(
        companionId!,
        externalUserId!,
        token,
      );
    },
    staleTime: 30_000,
  });
}

export function useUserMemoriesPanel(
  companionId: string | null,
  externalUserId: string | null,
  params?: { limit?: number; offset?: number; order_by?: 'created_at' | 'last_accessed_at' | 'importance'; order_dir?: 'ASC' | 'DESC' }
) {
  const memories = useUserMemoriesByExternalId(companionId, externalUserId, params);
  const summary = useUserAnalyticsSummary(companionId, externalUserId);

  const loading = memories.isLoading || summary.isLoading;
  const error = memories.error?.message || summary.error?.message || null;

  return {
    memories: memories.data?.items ?? [],
    total: memories.data?.total ?? 0,
    summary: summary.data ?? null,
    loading,
    error,
    memoriesQuery: memories,
    summaryQuery: summary,
  };
}

export function useSearchMemories(companionId: string | null) {
  const { getToken, isLoaded } = useAuth();
  return useMutation<{ items: MemoryItem[] }, Error, { query: string; top_k?: number; min_saliency?: number; conversation_id?: string; external_user_id?: string; sender_type?: string; modality?: string }>({
    mutationFn: async (body) => {
      if (!companionId || !isLoaded) throw new Error('No companion selected');
      const token = await getToken(
        process.env.NEXT_PUBLIC_CLERK_JWT_TEMPLATE
          ? { template: process.env.NEXT_PUBLIC_CLERK_JWT_TEMPLATE }
          : undefined
      );
      return apiClient.searchMemories(companionId!, body, token);
    },
  });
}

export function useCreateMemory(companionId: string | null) {
  const { getToken, isLoaded } = useAuth();
  const qc = useQueryClient();
  const pendingRef = useRef<Map<string, PendingState>>(new Map());
  const pollersRef = useRef<Map<string, ReturnType<typeof setTimeout>>>(new Map());

  useEffect(() => {
    return () => {
        // eslint-disable-next-line react-hooks/exhaustive-deps
      for (const timeout of pollersRef.current.values()) {
        clearTimeout(timeout);
      }
      pollersRef.current.clear();
        // eslint-disable-next-line react-hooks/exhaustive-deps
      pendingRef.current.clear();
    };
  }, []);

  const applyPendingToCaches = () => {
    if (!companionId) return;
    const pendingStubs = Array.from(pendingRef.current.values()).map((state) => state.stub);
    const matches = qc.getQueriesData<MemoryItem[]>({ queryKey: ['memories', companionId] });
    for (const [key, data] of matches) {
      const filtered = (data || []).filter((item) => {
        if (!isPendingMemory(item)) return true;
        return pendingRef.current.has(memoryIdentityKey(item));
      });
      const next = dedupeMemoriesByContentAndCore([...pendingStubs, ...filtered]);
      qc.setQueryData(key, next);
    }
  };

  const cancelPoll = (identity: string) => {
    const handle = pollersRef.current.get(identity);
    if (handle) {
      clearTimeout(handle);
      pollersRef.current.delete(identity);
    }
  };

  const finalizePending = (identity: string) => {
    pendingRef.current.delete(identity);
    cancelPoll(identity);
    applyPendingToCaches();
    if (companionId) {
      qc.invalidateQueries({ queryKey: ['memory-stats', companionId] });
    }
  };

  const isResolvedInCache = (identity: string) => {
    if (!companionId) return false;
    const matches = qc.getQueriesData<MemoryItem[]>({ queryKey: ['memories', companionId] });
    return matches.some(([, data]) =>
      (data || []).some((item) => !isPendingMemory(item) && memoryIdentityKey(item) === identity)
    );
  };

  const schedulePoll = (identity: string) => {
    if (pollersRef.current.has(identity)) return;

    const poll = async () => {
      const state = pendingRef.current.get(identity);
      if (!state || !companionId) {
        cancelPoll(identity);
        return;
      }

      state.attempts += 1;
      await qc.invalidateQueries({ queryKey: ['memories', companionId] });
      applyPendingToCaches();

      if (isResolvedInCache(identity)) {
        finalizePending(identity);
        return;
      }

      if (state.attempts >= MEMORY_REFETCH_MAX_ATTEMPTS) {
        state.attempts = MEMORY_REFETCH_MAX_ATTEMPTS;
        console.warn('Core memory ingestion is taking longer than expected; keeping pending stub and retrying.');
        const stalledHandle = setTimeout(poll, MEMORY_REFETCH_STALLED_DELAY);
        pollersRef.current.set(identity, stalledHandle);
        return;
      }

      const delay = Math.min(
        MEMORY_REFETCH_BASE_DELAY * Math.pow(MEMORY_REFETCH_BACKOFF, state.attempts),
        MEMORY_REFETCH_MAX_DELAY,
      );
      const handle = setTimeout(poll, delay);
      pollersRef.current.set(identity, handle);
    };

    const initialHandle = setTimeout(poll, MEMORY_REFETCH_BASE_DELAY);
    pollersRef.current.set(identity, initialHandle);
  };

  return useMutation<
    { id: string },
    Error,
    { content?: string; message_id?: string; importance?: number; weight_user?: number; modality?: string; commentary?: string; conversation_id?: string; sender_type?: string; external_user_id?: string; is_core?: boolean },
    { prevSnapshots: Array<{ key: QueryKey; data: MemoryItem[] | undefined }>; pendingKey?: string }
  >({
    mutationFn: async (body) => {
      if (!companionId || !isLoaded) throw new Error('No companion selected');
      const token = await getToken(
        process.env.NEXT_PUBLIC_CLERK_JWT_TEMPLATE
          ? { template: process.env.NEXT_PUBLIC_CLERK_JWT_TEMPLATE }
          : undefined
      );
      return apiClient.createMemory(companionId!, body, token);
    },
    onMutate: async (body) => {
      if (!companionId) return { prevSnapshots: [] };

      // Prior implementation inserted an optimistic row then immediately invalidated queries,
      // which erased the stub before the background worker (server/app/services/memory_service.py:150)
      // had written the real record. We now keep a pending stub alive until ingestion completes.
      await qc.cancelQueries({ queryKey: ['memories', companionId] });
      const matches = qc.getQueriesData<MemoryItem[]>({ queryKey: ['memories', companionId] });
      const prevSnapshots = matches.map(([key, data]) => ({ key, data }));

      const now = new Date().toISOString();
      const optimisticId = `optimistic-${Date.now()}`;
      const stub: PendingAwareMemory & { pending: true } = {
        id: optimisticId,
        companion_id: companionId,
        content: body.content?.trim() || '(new memory)',
        created_at: now,
        importance: typeof body.importance === 'number' ? body.importance : 0.5,
        weight_user: typeof body.weight_user === 'number' ? body.weight_user : 1.0,
        modality: body.modality || 'text',
        last_accessed_at: now,
        commentary: body.commentary || null,
        conversation_id: body.conversation_id || null,
        sender_type: body.sender_type || 'user',
        external_user_id: null,
        message_id: null,
        is_core: !!body.is_core,
        similarity: null,
        score: null,
        pending: true,
      };

      const identity = memoryIdentityKey(stub);
      pendingRef.current.set(identity, { key: identity, stub, attempts: 0 });
      applyPendingToCaches();

      return { prevSnapshots, pendingKey: identity };
    },
    onError: (_err, _body, ctx) => {
      if (ctx?.pendingKey) {
        pendingRef.current.delete(ctx.pendingKey);
        cancelPoll(ctx.pendingKey);
      }
      if (ctx?.prevSnapshots) {
        for (const snap of ctx.prevSnapshots) {
          qc.setQueryData(snap.key, snap.data);
        }
      }
      applyPendingToCaches();
    },
    onSuccess: (_res, _body, ctx) => {
      if (!ctx?.pendingKey) return;
      if (isResolvedInCache(ctx.pendingKey)) {
        finalizePending(ctx.pendingKey);
        return;
      }
      schedulePoll(ctx.pendingKey);
    },
  });
}

export function useUpdateMemory() {
  const { getToken } = useAuth();
  const qc = useQueryClient();
  return useMutation<
    { ok: true },
    Error,
    { memoryId: string; importance?: number; commentary?: string; content?: string; companionId: string },
    { prevSnapshots: Array<{ key: QueryKey; data: MemoryItem[] | undefined }> }
  >({
    mutationFn: async ({ memoryId, importance, commentary, content }) => {
      const token = await getToken(
        process.env.NEXT_PUBLIC_CLERK_JWT_TEMPLATE
          ? { template: process.env.NEXT_PUBLIC_CLERK_JWT_TEMPLATE }
          : undefined
      );
      return apiClient.updateMemory(memoryId, { importance, commentary, content }, token);
    },
    onMutate: async (vars) => {
      const { companionId, memoryId, importance, commentary, content } = vars;
      await qc.cancelQueries({ queryKey: ['memories', companionId] });
      const matches = qc.getQueriesData<MemoryItem[]>({ queryKey: ['memories', companionId] });
      const prevSnapshots = matches.map(([key, data]) => ({ key, data }));
      for (const [key, data] of matches) {
        const next = (data || []).map(m => m.id === memoryId ? {
          ...m,
          importance: typeof importance === 'number' ? importance : m.importance,
          commentary: typeof commentary === 'string' ? commentary : m.commentary,
          content: typeof content === 'string' ? content : m.content,
        } : m);
        qc.setQueryData(key, next);
      }
      return { prevSnapshots };
    },
    onError: (_err, _vars, ctx) => {
      if (!ctx?.prevSnapshots) return;
      for (const snap of ctx.prevSnapshots) {
        qc.setQueryData(snap.key, snap.data);
      }
    },
    onSettled: (_data, _error, vars) => {
      qc.invalidateQueries({ queryKey: ['memories', vars.companionId] });
      qc.invalidateQueries({ queryKey: ['memory-stats', vars.companionId] });
    },
  });
}

export function useDeleteMemory() {
  const { getToken } = useAuth();
  const qc = useQueryClient();
  return useMutation<
    { ok: true },
    Error,
    { memoryId: string; companionId: string },
    { prevSnapshots: Array<{ key: QueryKey; data: MemoryItem[] | undefined }> }
  >({
    mutationFn: async ({ memoryId }) => {
      const token = await getToken(
        process.env.NEXT_PUBLIC_CLERK_JWT_TEMPLATE
          ? { template: process.env.NEXT_PUBLIC_CLERK_JWT_TEMPLATE }
          : undefined
      );
      return apiClient.deleteMemory(memoryId, token);
    },
    onMutate: async (vars) => {
      const { companionId, memoryId } = vars;
      await qc.cancelQueries({ queryKey: ['memories', companionId] });
      const matches = qc.getQueriesData<MemoryItem[]>({ queryKey: ['memories', companionId] });
      const prevSnapshots = matches.map(([key, data]) => ({ key, data }));
      for (const [key, data] of matches) {
        const next = (data || []).filter(m => m.id !== memoryId);
        qc.setQueryData(key, next);
      }
      return { prevSnapshots };
    },
    onError: (_err, _vars, ctx) => {
      if (!ctx?.prevSnapshots) return;
      for (const snap of ctx.prevSnapshots) {
        qc.setQueryData(snap.key, snap.data);
      }
    },
    onSettled: (_data, _error, vars) => {
      qc.invalidateQueries({ queryKey: ['memories', vars.companionId] });
      qc.invalidateQueries({ queryKey: ['memory-stats', vars.companionId] });
    },
  });
}
