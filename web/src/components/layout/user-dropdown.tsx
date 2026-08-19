'use client';

import { useState, useRef, useEffect } from 'react';
import { useClerk } from '@clerk/nextjs';
import { useUser } from '@/hooks/useUser';
import { useSelectedCompanion } from '@/components/providers';
import Icon from '@/components/ui/icon';

export default function UserDropdown() {
  const [isOpen, setIsOpen] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);
  const { signOut } = useClerk();
  const { user, avatarUrl } = useUser();
  const { setSelectedCompanionId } = useSelectedCompanion();

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

  const handleSignOut = async () => {
    try {
      setSelectedCompanionId(null);
    } catch {
      // ignore state reset issues during sign out
    }
    await signOut({ redirectUrl: '/' });
  };

  return (
    <div ref={dropdownRef} className="relative">
      <button
        onClick={() => setIsOpen(!isOpen)}
        className="flex items-center gap-2 px-2 py-1.5 text-white/80 hover:text-white transition-colors rounded-md hover:bg-white/5"
      >
        <div className="w-6 h-6 rounded-full bg-gray-darker flex items-center justify-center overflow-hidden">
          {avatarUrl ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={avatarUrl}
              alt={user?.display_name || 'User'}
              className="w-full h-full object-cover"
            />
          ) : (
            <Icon name="user" size={14} color="rgba(255, 255, 255, 0.6)" />
          )}
        </div>
        <Icon
          name="chevron-down"
          size={14}
          className={`transition-transform ${isOpen ? 'rotate-180' : ''}`}
        />
      </button>

      {isOpen && (
        <div className="absolute top-full right-0 mt-2 w-48 bg-[var(--color-dropdown-bg)] border border-white/10 shadow-xl z-50 py-2">
          {user?.display_name && (
            <div className="px-4 py-2 border-b border-white/10">
              <div className="text-base text-white truncate">{user.display_name}</div>
              <div className="text-sm text-[var(--color-option-text)] truncate">{user.email}</div>
            </div>
          )}
          <button
            onClick={handleSignOut}
            className="w-full text-left px-4 pt-[18px] pb-2.5 text-sm text-[var(--color-link-text)] hover:text-white transition-colors flex items-center gap-2"
          >
            <Icon name="log-out" size={14} />
            Sign Out
          </button>
        </div>
      )}
    </div>
  );
}
