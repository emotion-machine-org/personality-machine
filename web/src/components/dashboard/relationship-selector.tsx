'use client';

import { useState, useRef, useEffect } from 'react';
import Icon from '@/components/ui/icon';
import Tooltip from '@/components/ui/tooltip';
import type { TestUserSummary } from '@/lib/api';

// Format relative time (e.g., "2 min ago", "1 hour ago")
function formatRelativeTime(dateString: string | null): string {
  if (!dateString) return '';
  const date = new Date(dateString);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffMins = Math.floor(diffMs / 60000);

  if (diffMins < 1) return 'just now';
  if (diffMins < 60) return `${diffMins} min ago`;
  const diffHours = Math.floor(diffMins / 60);
  if (diffHours < 24) return `${diffHours}h ago`;
  const diffDays = Math.floor(diffHours / 24);
  if (diffDays < 7) return `${diffDays}d ago`;
  return date.toLocaleDateString();
}

// Format user ID for display (truncate builder- prefix)
function formatUserId(userId: string): string {
  if (userId.startsWith('builder-')) {
    // Show last 8 chars after builder-
    const suffix = userId.slice(8);
    return suffix.length > 12 ? `...${suffix.slice(-12)}` : suffix;
  }
  return userId.length > 16 ? `${userId.slice(0, 16)}...` : userId;
}

interface RelationshipSelectorProps {
  currentUserId: string | null;
  currentMessageCount: number;
  lastInteractionAt: string | null;
  testUsers: TestUserSummary[];
  isLoading: boolean;
  disabled?: boolean;
  resetTooltip?: string;
  onSwitchUser: (userId: string) => void;
  onCreateNewUser: () => void;
  onReset: () => void;
}

export function RelationshipSelector({
  currentUserId,
  currentMessageCount,
  lastInteractionAt,
  testUsers,
  isLoading,
  disabled,
  resetTooltip = 'Reset conversation (clear messages, profile, and memory state)',
  onSwitchUser,
  onCreateNewUser,
  onReset,
}: RelationshipSelectorProps) {
  const [isOpen, setIsOpen] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);

  // Close dropdown on outside click
  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setIsOpen(false);
      }
    }
    if (isOpen) {
      document.addEventListener('mousedown', handleClickOutside);
    }
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [isOpen]);

  // Other users (not current)
  const otherUsers = testUsers.filter((u) => u.user_id !== currentUserId);

  return (
    <div className="flex items-center gap-2">
      {/* User selector dropdown */}
      <div ref={dropdownRef} className="relative">
        <button
          onClick={() => setIsOpen(!isOpen)}
          disabled={disabled || isLoading}
          className="flex items-center gap-2 px-4 py-2 rounded-full bg-[var(--color-gray-button)] text-white text-sm font-medium transition-colors hover:bg-[var(--color-gray-button-hover)] disabled:opacity-50 disabled:cursor-not-allowed"
        >
          <Icon name="user" size={14} />
          <span className="max-w-[140px] truncate">
            {currentUserId ? formatUserId(currentUserId) : 'Select User'}
          </span>
          {currentMessageCount > 0 && (
            <span className="text-xs text-white/60">
              {currentMessageCount} msg{currentMessageCount !== 1 ? 's' : ''}
            </span>
          )}
          <Icon
            name="chevron-down"
            size={12}
            className={`transition-transform ${isOpen ? 'rotate-180' : ''}`}
          />
        </button>

        {isOpen && (
          <div className="absolute top-full left-0 mt-1 min-w-[280px] bg-[var(--color-dropdown-bg)] border border-white/10 shadow-xl z-50 py-1 max-h-80 overflow-y-auto overflow-x-hidden">
            {/* Current user */}
            {currentUserId && (
              <>
                <div className="px-3 py-1.5 text-xs text-white/40 uppercase tracking-wider">
                  Current
                </div>
                <div className="px-4 py-2.5 bg-white/5 flex items-center justify-between">
                  <div>
                    <div className="text-sm text-white font-medium flex items-center gap-2">
                      <Icon name="user" size={12} className="text-white/60" />
                      {formatUserId(currentUserId)}
                    </div>
                    <div className="text-xs text-white/50 mt-0.5">
                      {currentMessageCount} messages
                      {lastInteractionAt && ` · ${formatRelativeTime(lastInteractionAt)}`}
                    </div>
                  </div>
                  <div className="w-2 h-2 rounded-full bg-green-500" title="Active" />
                </div>
              </>
            )}

            {/* Other users */}
            {otherUsers.length > 0 && (
              <>
                <div className="px-3 py-1.5 text-xs text-white/40 uppercase tracking-wider mt-2">
                  Recent Users
                </div>
                {otherUsers.slice(0, 5).map((user) => (
                  <button
                    key={user.id}
                    onClick={() => {
                      onSwitchUser(user.user_id);
                      setIsOpen(false);
                    }}
                    className="w-full text-left px-4 py-2.5 text-sm text-[var(--color-option-text)] hover:text-white hover:bg-white/5 transition-colors flex items-center justify-between"
                  >
                    <div>
                      <div className="flex items-center gap-2">
                        <Icon name="user" size={12} className="text-white/40" />
                        {formatUserId(user.user_id)}
                        {user.profile_preview?.name && (
                          <span className="text-xs text-white/40">
                            ({user.profile_preview.name})
                          </span>
                        )}
                      </div>
                      <div className="text-xs text-white/40 mt-0.5">
                        {user.message_count} messages
                        {user.last_interaction_at && ` · ${formatRelativeTime(user.last_interaction_at)}`}
                      </div>
                    </div>
                  </button>
                ))}
              </>
            )}

            {/* Divider */}
            <div className="border-t border-white/10 my-1" />

            {/* Actions */}
            <button
              onClick={() => {
                onCreateNewUser();
                setIsOpen(false);
              }}
              className="w-full text-left px-4 py-2.5 text-sm text-[var(--color-option-text)] hover:text-white transition-colors flex items-center gap-2 whitespace-nowrap"
            >
              <Icon name="plus" size={14} />
              New Test User
            </button>
          </div>
        )}
      </div>

      {/* Reset button */}
      <Tooltip content={resetTooltip}>
        <button
          onClick={onReset}
          disabled={disabled || !currentUserId}
          className="flex items-center justify-center w-9 h-9 rounded-full bg-[var(--color-gray-button)] text-white/70 transition-colors hover:bg-[var(--color-gray-button-hover)] hover:text-white disabled:opacity-50 disabled:cursor-not-allowed"
        >
          <Icon name="restart" size={14} />
        </button>
      </Tooltip>
    </div>
  );
}
