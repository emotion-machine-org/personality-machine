'use client';

import { useState, useRef, useEffect } from 'react';
import { useSelectedCompanion } from '@/components/providers';
import { useCompanion } from '@/hooks/useCompanions';
import { SharePanelContent } from '@/components/dashboard/share-panel';
import Icon from '@/components/ui/icon';

export default function SharePopover() {
  const [isOpen, setIsOpen] = useState(false);
  const popoverRef = useRef<HTMLDivElement>(null);
  const { selectedCompanionId } = useSelectedCompanion();
  const { data: companionConfig } = useCompanion(selectedCompanionId);

  // Close popover on outside click
  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (popoverRef.current && !popoverRef.current.contains(event.target as Node)) {
        setIsOpen(false);
      }
    }
    if (isOpen) {
      document.addEventListener('mousedown', handleClickOutside);
    }
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [isOpen]);

  return (
    <div ref={popoverRef} className="relative">
      <button
        onClick={() => setIsOpen(!isOpen)}
        disabled={!selectedCompanionId}
        className="flex items-center gap-2 px-3 py-1.5 text-sm text-white/70 bg-white/[0.08] hover:text-white hover:bg-white/15 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
      >
        <Icon name="share" size={14} />
        Share
      </button>

      {isOpen && (
        <div className="absolute top-full right-0 mt-2 w-[380px] bg-[var(--color-dropdown-bg)] border border-white/10 shadow-2xl z-50">
          <SharePanelContent
            companionId={selectedCompanionId}
            companionConfig={companionConfig}
            variant="overlay"
            onClose={() => setIsOpen(false)}
          />
        </div>
      )}
    </div>
  );
}
