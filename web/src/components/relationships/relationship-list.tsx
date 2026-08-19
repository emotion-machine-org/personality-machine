'use client';

import { useState, useEffect } from 'react';
import Icon from '@/components/ui/icon';
import ConfirmModal from '@/components/ui/confirm-modal';
import { useRelationships, useDeleteRelationship } from '@/hooks/useRelationships';
import type { RelationshipSummary } from '@/lib/types';

function formatRelativeTime(dateString: string | null): string {
  if (!dateString) return 'Never';
  const date = new Date(dateString);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffMins = Math.floor(diffMs / 60000);
  const diffHours = Math.floor(diffMs / 3600000);
  const diffDays = Math.floor(diffMs / 86400000);

  if (diffMins < 1) return 'Just now';
  if (diffMins < 60) return `${diffMins}m ago`;
  if (diffHours < 24) return `${diffHours}h ago`;
  if (diffDays < 30) return `${diffDays}d ago`;
  return date.toLocaleDateString();
}

interface RelationshipListProps {
  companionId: string | null;
  selectedId: string | null;
  onSelect: (relationship: RelationshipSummary) => void;
}

const PAGE_SIZE = 50;

export default function RelationshipList({ companionId, selectedId, onSelect }: RelationshipListProps) {
  const [search, setSearch] = useState('');
  const [page, setPage] = useState(0);
  const [deleteTarget, setDeleteTarget] = useState<RelationshipSummary | null>(null);

  const debouncedSearch = useDebounced(search, 150);

  const { data, isLoading, error } = useRelationships(companionId, {
    limit: PAGE_SIZE,
    offset: page * PAGE_SIZE,
    search: debouncedSearch || undefined,
  });

  const deleteRelationship = useDeleteRelationship(companionId);

  const handleDelete = async () => {
    if (!deleteTarget) return;
    try {
      await deleteRelationship.mutateAsync(deleteTarget.id);
      setDeleteTarget(null);
    } catch (err) {
      console.error('Failed to delete relationship:', err);
    }
  };

  const items = data?.items ?? [];
  const total = data?.total ?? 0;
  const hasMore = data?.has_more ?? false;

  return (
    <div className="flex flex-col h-full">
      {/* Search */}
      <div className="p-3 border-b border-white/10">
        <div className="relative">
          <Icon name="search" size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-white/30" />
          <input
            type="text"
            value={search}
            onChange={(e) => { setSearch(e.target.value); setPage(0); }}
            placeholder="Search by user ID..."
            className="w-full bg-[var(--color-input-editable)] border-none rounded pl-8 pr-3 py-1.5 text-[13px] font-book text-white placeholder:text-[var(--color-placeholder)] focus:outline-none focus:ring-1 focus:ring-white/20"
          />
        </div>
      </div>

      {/* List */}
      <div className="flex-1 overflow-y-auto min-h-0">
        {isLoading ? (
          <div className="flex items-center justify-center py-12 text-[13px] text-white/40">
            Loading relationships...
          </div>
        ) : error ? (
          <div className="flex items-center justify-center py-12 text-[13px] text-[color:var(--color-brand-solid)]">
            Failed to load relationships
          </div>
        ) : items.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-16 px-4 text-center">
            <p className="text-[13px] text-white/40">
              {debouncedSearch ? 'No relationships match your search' : 'No relationships yet'}
            </p>
            {!debouncedSearch && (
              <p className="text-[11px] text-white/25 mt-1">
                Relationships are created when users message your companion
              </p>
            )}
          </div>
        ) : (
          <div>
            {items.map((rel) => (
              <div
                key={rel.id}
                role="button"
                tabIndex={0}
                onClick={() => onSelect(rel)}
                onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') onSelect(rel); }}
                className={`w-full text-left px-3 py-2.5 border-b border-white/5 transition-colors group cursor-pointer ${
                  selectedId === rel.id
                    ? 'bg-white/5 border-l-2 border-l-white/40'
                    : 'hover:bg-white/[0.03] border-l-2 border-l-transparent'
                }`}
              >
                <div className="flex items-center justify-between">
                  <span className="text-[13px] font-book text-white truncate mr-2">
                    {rel.user_id}
                  </span>
                  <button
                    onClick={(e) => { e.stopPropagation(); setDeleteTarget(rel); }}
                    className="opacity-0 group-hover:opacity-100 p-1 text-white/30 hover:text-[color:var(--color-brand-solid)] transition-all"
                    title="Delete relationship"
                  >
                    <Icon name="trash" size={12} />
                  </button>
                </div>
                <div className="flex items-center gap-3 mt-0.5 text-[11px] text-white/35">
                  <span>{rel.message_count} msgs</span>
                  <span>{formatRelativeTime(rel.last_interaction_at)}</span>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Pagination */}
      {total > PAGE_SIZE && (
        <div className="flex items-center justify-between px-3 py-2 border-t border-white/10 text-[11px] text-white/40">
          <button
            onClick={() => setPage((p) => Math.max(0, p - 1))}
            disabled={page === 0}
            className="hover:text-white disabled:opacity-30 disabled:hover:text-white/40 transition-colors"
          >
            Prev
          </button>
          <span>{page * PAGE_SIZE + 1}–{Math.min((page + 1) * PAGE_SIZE, total)} of {total}</span>
          <button
            onClick={() => setPage((p) => p + 1)}
            disabled={!hasMore}
            className="hover:text-white disabled:opacity-30 disabled:hover:text-white/40 transition-colors"
          >
            Next
          </button>
        </div>
      )}

      <ConfirmModal
        open={!!deleteTarget}
        title="Delete Relationship?"
        message={`This will permanently delete all messages, profile, and memory for "${deleteTarget?.user_id}". This cannot be undone.`}
        confirmText="Delete"
        cancelText="Cancel"
        destructive
        onConfirm={handleDelete}
        onCancel={() => setDeleteTarget(null)}
      />
    </div>
  );
}

// Simple debounce hook
function useDebounced(value: string, delay: number): string {
  const [debounced, setDebounced] = useState(value);

  useEffect(() => {
    const handler = setTimeout(() => setDebounced(value), delay);
    return () => clearTimeout(handler);
  }, [value, delay]);

  return debounced;
}
