'use client';

import { useState, useRef, useEffect, useCallback } from 'react';
import Icon from '@/components/ui/icon';

interface User {
  id: string;
  user_id: string;
  message_count: number;
}

interface UserSelectorProps {
  users: User[];
  selectedUserId: string | null;
  onSelect: (userId: string) => void;
  isLoading?: boolean;
}

export function UserSelector({
  users,
  selectedUserId,
  onSelect,
  isLoading = false,
}: UserSelectorProps) {
  // Exclude temp_ users (legacy) - show all builder- users including current one with 0 messages
  const filteredUsers = users.filter((u) => !u.user_id.startsWith('temp_'));
  const [isOpen, setIsOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  // Close on outside click
  useEffect(() => {
    if (!isOpen) return;

    const handleClickOutside = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setIsOpen(false);
      }
    };

    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [isOpen]);

  // Truncate long user IDs for display
  const truncateUserId = useCallback((userId: string, maxLen = 16) => {
    if (userId.length <= maxLen) return userId;
    return userId.slice(0, maxLen - 2) + '..';
  }, []);

  const selectedUser = filteredUsers.find((u) => u.user_id === selectedUserId);
  const displayName = selectedUser
    ? `Test User ${truncateUserId(selectedUser.user_id)}`
    : 'Select User';

  return (
    <div className="relative inline-flex" ref={menuRef}>
      <button
        type="button"
        onClick={() => setIsOpen(!isOpen)}
        disabled={isLoading}
        className="flex items-center gap-[2px] bg-black px-[10px] py-[4px]"
      >
        <span
          className="text-[30px] leading-[1.2] tracking-[-1.2px] font-light"
          style={{ color: 'rgba(255,255,255,0.6)' }}
        >
          {isLoading ? 'Loading...' : displayName}
        </span>
        <div className="bg-black h-[28px] w-[10px]" />
        <Icon name="chevron-down" size={36} className="text-white/60" />
      </button>

      {isOpen && (
        <div className="absolute left-0 top-full mt-[4px] z-50 min-w-[280px] bg-black shadow-xl">
          <div className="max-h-[320px] overflow-y-auto">
            {filteredUsers.length === 0 ? (
              <div className="px-[16px] py-[12px] text-[16px] text-white/50">
                No users found
              </div>
            ) : (
              filteredUsers.map((user) => (
                <button
                  key={user.id}
                  type="button"
                  onClick={() => {
                    onSelect(user.user_id);
                    setIsOpen(false);
                  }}
                  className={`w-full px-[16px] py-[12px] text-left text-[16px] transition-colors ${
                    user.user_id === selectedUserId
                      ? 'bg-white/10 text-white'
                      : 'text-white/70 hover:bg-white/5 hover:text-white'
                  }`}
                >
                  <div className="flex items-center justify-between">
                    <span className="font-mono text-[14px]">{user.user_id}</span>
                    <span className="text-[12px] text-white/40 ml-[8px]">
                      {user.message_count} msg{user.message_count !== 1 ? 's' : ''}
                    </span>
                  </div>
                </button>
              ))
            )}
          </div>
        </div>
      )}
    </div>
  );
}
