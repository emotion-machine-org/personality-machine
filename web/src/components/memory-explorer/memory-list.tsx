'use client';

import { useState, useCallback } from 'react';
import { MemoryItem } from './memory-item';
import { useIntersectionObserver } from '@/hooks/useIntersectionObserver';
import type { MemoryV2Entry } from '@/lib/memory-v2-api';

interface MemoryListProps {
  entries: MemoryV2Entry[];
  hasMore: boolean;
  isLoadingMore: boolean;
  newMemoryIds?: Set<string>;
  onLoadMore: () => void;
  onUpdate: (entryId: string, content: string, type: string | null) => Promise<void>;
  onDelete: (entryId: string) => Promise<void>;
}

export function MemoryList({
  entries,
  hasMore,
  isLoadingMore,
  newMemoryIds = new Set(),
  onLoadMore,
  onUpdate,
  onDelete,
}: MemoryListProps) {
  // Track which entry is currently being edited (only one at a time)
  const [editingId, setEditingId] = useState<string | null>(null);

  // Intersection observer for infinite scroll
  const loadMoreRef = useIntersectionObserver(onLoadMore, {
    threshold: 0,
    rootMargin: '100px',
    enabled: hasMore && !isLoadingMore,
  });

  const handleUpdate = useCallback(
    async (entryId: string, content: string, type: string | null) => {
      await onUpdate(entryId, content, type);
    },
    [onUpdate]
  );

  const handleDelete = useCallback(
    async (entryId: string) => {
      await onDelete(entryId);
    },
    [onDelete]
  );

  if (entries.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-[40px]">
        <p className="text-[20px] text-white/50 font-book">No memories found</p>
        <p className="text-[16px] text-white/30 mt-[8px]">
          Start a conversation to generate memories
        </p>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-[20px] items-start px-[2px] w-full">
      {entries.map((entry, index) => {
        const isNew = newMemoryIds.has(entry.id);

        return (
          <MemoryItem
            key={entry.id}
            entry={entry}
            index={index}
            isNew={isNew}
            isEditing={editingId === entry.id}
            onEditStart={() => setEditingId(entry.id)}
            onEditEnd={() => setEditingId(null)}
            onUpdate={handleUpdate}
            onDelete={handleDelete}
          />
        );
      })}

      {/* Load more trigger */}
      {hasMore && (
        <div ref={loadMoreRef} className="py-[20px] w-full flex justify-center">
          {isLoadingMore ? (
            <span className="text-[16px] text-white/40">Loading more...</span>
          ) : (
            <span className="text-[16px] text-white/20">Scroll for more</span>
          )}
        </div>
      )}
    </div>
  );
}
