'use client';

import { useState, useCallback, useEffect, useMemo } from 'react';
import { useAuth, useUser } from '@clerk/nextjs';
import { useQuery } from '@tanstack/react-query';
import { MemorySearch } from './memory-search';
import { UserSelector } from './user-selector';
import { MemoryList } from './memory-list';
import { MiniChat } from './mini-chat';
import { memoryV2Api } from '@/lib/memory-v2-api';
import type { MemoryV2Entry } from '@/lib/memory-v2-api';
import { apiClient, type TestUserSummary } from '@/lib/api';

// Read builder user ID from localStorage (same key as useBuilderRelationship)
// Does NOT create new users - only reads existing ones
function getBuilderUserIdFromStorage(clerkUserId: string | null): string | null {
  if (typeof window === 'undefined' || !clerkUserId) return null;
  return localStorage.getItem(`em.builderUserId.${clerkUserId}`);
}

interface MemoryExplorerProps {
  companionId: string;
  isOpen: boolean;
  onClose: () => void;
}

export function MemoryExplorer({ companionId, isOpen, onClose }: MemoryExplorerProps) {
  const { getToken } = useAuth();
  const { user: clerkUser } = useUser();

  // Read the current builder user from localStorage (same as companion-simulator)
  // Does NOT create new users - that's only done in companion-simulator
  const builderUserId = useMemo(
    () => getBuilderUserIdFromStorage(clerkUser?.id ?? null),
    [clerkUser?.id]
  );

  // Fetch test users list from API
  const getAuthToken = useCallback(async () => {
    const isAuthDisabled = process.env.NEXT_PUBLIC_DISABLE_AUTH === 'true';
    if (isAuthDisabled) return 'mock-dev-token';
    return getToken(
      process.env.NEXT_PUBLIC_CLERK_JWT_TEMPLATE
        ? { template: process.env.NEXT_PUBLIC_CLERK_JWT_TEMPLATE }
        : undefined
    );
  }, [getToken]);

  const { data: testUsers = [], isLoading: isLoadingUsers } = useQuery<TestUserSummary[]>({
    queryKey: ['testUsers', companionId],
    queryFn: async () => {
      if (!companionId) return [];
      const token = await getAuthToken();
      return apiClient.listTestUsers(companionId, token);
    },
    enabled: isOpen && !!companionId,
    staleTime: 30 * 1000,
  });

  // State
  const [selectedUserId, setSelectedUserId] = useState<string | null>(null);
  const [entries, setEntries] = useState<MemoryV2Entry[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');

  // Loading states
  const [isLoadingMemories, setIsLoadingMemories] = useState(false);
  const [isLoadingMore, setIsLoadingMore] = useState(false);

  // Chat state
  const [isChatOpen, setIsChatOpen] = useState(false);
  const [newMemoryIds, setNewMemoryIds] = useState<Set<string>>(new Set());

  // Auto-select the current builder user when opening (syncs with companion-simulator)
  useEffect(() => {
    if (isOpen && builderUserId && !selectedUserId) {
      setSelectedUserId(builderUserId);
    }
  }, [isOpen, builderUserId, selectedUserId]);

  // Convert testUsers to the format expected by UserSelector
  const users = testUsers.map((u) => ({
    id: u.user_id,
    user_id: u.user_id,
    message_count: u.message_count,
  }));

  // Load memories when user changes or search changes
  useEffect(() => {
    if (!isOpen || !companionId || !selectedUserId) {
      setEntries([]);
      setHasMore(false);
      return;
    }

    const loadMemories = async () => {
      setIsLoadingMemories(true);
      try {
        const token = await getToken();
        const response = await memoryV2Api.listMemories(
          companionId,
          selectedUserId,
          { limit: 20, search: searchQuery || undefined },
          token
        );
        setEntries(response.entries);
        setNextCursor(response.next_cursor);
        setHasMore(response.has_more);
      } catch (error) {
        console.error('Failed to load memories:', error);
        setEntries([]);
        setHasMore(false);
      } finally {
        setIsLoadingMemories(false);
      }
    };

    loadMemories();
  }, [isOpen, companionId, selectedUserId, searchQuery, getToken]);

  // Load more entries
  const handleLoadMore = useCallback(async () => {
    if (!companionId || !selectedUserId || !nextCursor || isLoadingMore) return;

    setIsLoadingMore(true);
    try {
      const token = await getToken();
      const response = await memoryV2Api.listMemories(
        companionId,
        selectedUserId,
        { limit: 20, cursor: nextCursor, search: searchQuery || undefined },
        token
      );
      setEntries((prev) => [...prev, ...response.entries]);
      setNextCursor(response.next_cursor);
      setHasMore(response.has_more);
    } catch (error) {
      console.error('Failed to load more memories:', error);
    } finally {
      setIsLoadingMore(false);
    }
  }, [companionId, selectedUserId, nextCursor, searchQuery, isLoadingMore, getToken]);

  // Update memory
  const handleUpdate = useCallback(
    async (entryId: string, content: string, type: string | null) => {
      if (!companionId || !selectedUserId) return;

      const token = await getToken();
      const updated = await memoryV2Api.updateMemory(
        companionId,
        selectedUserId,
        entryId,
        { content, type },
        token
      );

      setEntries((prev) => prev.map((e) => (e.id === entryId ? updated : e)));
    },
    [companionId, selectedUserId, getToken]
  );

  // Delete memory
  const handleDelete = useCallback(
    async (entryId: string) => {
      if (!companionId || !selectedUserId) return;

      const token = await getToken();
      await memoryV2Api.deleteMemory(companionId, selectedUserId, entryId, token);

      setEntries((prev) => prev.filter((e) => e.id !== entryId));
    },
    [companionId, selectedUserId, getToken]
  );

  // Handle chat message - uses the selected user (same as companion-simulator)
  const handleChatSend = useCallback(
    async (message: string, history: Array<{ role: 'user' | 'assistant'; content: string }>) => {
      if (!selectedUserId) {
        throw new Error('No user selected');
      }

      const token = await getToken();
      const response = await memoryV2Api.tempChat(
        companionId,
        {
          message,
          history,
          user_id: selectedUserId, // Use the selected builder user
        },
        token
      );

      // Highlight new memories in the list
      if (response.new_memories.length > 0) {
        const newIds = new Set(response.new_memories.map((m) => m.id));
        setNewMemoryIds((prev) => new Set([...prev, ...newIds]));

        // Refresh the memory list to show new entries
        setEntries(response.memory_entries);
        setHasMore(false);
      }

      return response.response;
    },
    [companionId, selectedUserId, getToken]
  );

  // Handle close with cleanup
  const handleClose = useCallback(() => {
    setIsChatOpen(false);
    onClose();
  }, [onClose]);

  // Don't render if not open
  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 bg-[#1a1a1a] flex flex-col overflow-hidden">
      {/* Close button - top right */}
      <button
        onClick={handleClose}
        className="absolute top-[20px] right-[20px] text-white/40 hover:text-white transition-colors z-10"
      >
        <span className="text-[16px]">Close</span>
      </button>

      {/* Main content - centered with max-width ~663px */}
      <div className="flex flex-col gap-[20px] w-[663px] mx-auto pt-[48px] h-full overflow-hidden">
        {/* Header: "Memories of" + User selector */}
        <div className="flex gap-[10px] items-center h-[35px] w-full shrink-0">
          <span
            className="text-[30px] leading-[1.2] tracking-[-1.2px] font-light"
            style={{ color: 'rgba(255,255,255,0.6)' }}
          >
            Memories of{' '}
          </span>
          <UserSelector
            users={users}
            selectedUserId={selectedUserId}
            onSelect={setSelectedUserId}
            isLoading={isLoadingUsers}
          />
        </div>

        {/* Search */}
        <MemorySearch value={searchQuery} onChange={setSearchQuery} />

        {/* Memory list - scrollable area */}
        <div className="flex-1 overflow-y-auto min-h-0 pb-[40px] scrollbar-on-hover">
          {isLoadingMemories ? (
            <div className="flex items-center justify-center py-[40px]">
              <span className="text-[20px] text-white/40 font-book">Loading memories...</span>
            </div>
          ) : (
            <MemoryList
              entries={entries}
              hasMore={hasMore}
              isLoadingMore={isLoadingMore}
              newMemoryIds={newMemoryIds}
              onLoadMore={handleLoadMore}
              onUpdate={handleUpdate}
              onDelete={handleDelete}
            />
          )}
        </div>
      </div>

      {/* Mini Chat FAB - uses the selected builder user */}
      <MiniChat
        isOpen={isChatOpen}
        onToggle={() => setIsChatOpen(!isChatOpen)}
        onSend={handleChatSend}
        isLoading={isLoadingMemories || !selectedUserId}
      />
    </div>
  );
}
