'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { cn } from '@/lib/utils';
import Icon from '@/components/ui/icon';

interface ColumnNavbarProps {
  title: string;
  tabs: Array<{
    id: string;
    label: string;
    disabled?: boolean;
    menu?: Array<{ id: string; label: string; disabled?: boolean }>; // optional dropdown menu
  }>;
  activeTab: string;
  onTabChange: (tabId: string) => void;
  titleClassName?: string;
  debugInfo?: React.ReactNode;
  // Optional inline-edit support for the title
  editableTitle?: boolean;
  onTitleSave?: (newTitle: string) => Promise<void> | void;
  rightSlot?: (helpers: {
    closeTabMenus: () => void;
    closeAllMenus: () => void;
    registerCloseHandler: (handler: () => void) => () => void;
    setMenuOpenState: (open: boolean) => void;
  }) => React.ReactNode;
}

export default function ColumnNavbar({
  title,
  tabs,
  activeTab,
  onTabChange,
  titleClassName,
  debugInfo,
  editableTitle,
  onTitleSave,
  rightSlot,
}: ColumnNavbarProps) {
  const [isEditing, setEditing] = useState(false as boolean);
  const [draft, setDraft] = useState(title);
  const [openMenuId, setOpenMenuId] = useState<string | null>(null);
  const [hasExternalMenuOpen, setHasExternalMenuOpen] = useState(false);
  const externalCloseHandlersRef = useRef<Set<() => void>>(new Set());

  const closeTabMenus = useCallback(() => {
    setOpenMenuId(null);
  }, []);

  const registerCloseHandler = useCallback((handler: () => void) => {
    externalCloseHandlersRef.current.add(handler);
    return () => {
      externalCloseHandlersRef.current.delete(handler);
    };
  }, []);

  const closeExternalMenus = useCallback(() => {
    for (const handler of Array.from(externalCloseHandlersRef.current)) {
      try {
        handler();
      } catch {
        // Ignore close handler failures to avoid interrupting other menus
      }
    }
  }, []);

  const closeAllMenus = useCallback(() => {
    closeTabMenus();
    closeExternalMenus();
    setHasExternalMenuOpen(false);
  }, [closeTabMenus, closeExternalMenus]);

  // keep local draft in sync if parent changes title while not editing
  useEffect(() => {
    if (!isEditing) setDraft(title);
  }, [title, isEditing]);

  // Close open menu on outside click
  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      const target = e.target as HTMLElement | null;
      if (!target) return;
      // If a menu is open and the click is outside any menu container, close it
      const menus = Array.from(document.querySelectorAll('[data-em-navbar-menu="open"]'));
      const isInsideAny = menus.some((menu) => menu.contains(target));
      if (!isInsideAny) {
        closeAllMenus();
      }
    }
    if (openMenuId || hasExternalMenuOpen) {
      document.addEventListener('mousedown', handleClickOutside);
    }
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [closeAllMenus, hasExternalMenuOpen, openMenuId]);

  return (
    <div className="border-b border-white/20 flex flex-col pt-1 pb-1 relative">
      {/* Title Row */}
      <div className="mb-2">
        {editableTitle ? (
          !isEditing ? (
            <button
              type="button"
              onClick={() => setEditing(true)}
              className={cn("font-book text-sm text-[var(--color-title-text)] text-left hover:text-white transition-colors", titleClassName)}
              title="Click to edit name"
            >
              {title}
            </button>
          ) : (
            <div className="flex items-center gap-2">
              <input
                className="bg-transparent border border-white/20 rounded px-2 py-1 text-sm text-white focus:outline-none focus:border-white/40 min-w-[140px]"
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                autoFocus
                onKeyDown={(e) => {
                  if (e.key === 'Enter') {
                    // Optimistic: exit edit mode immediately
                    setEditing(false);
                    try {
                      void onTitleSave?.(draft.trim());
                    } catch (err) {
                      console.error('Failed to save title', err);
                    }
                  }
                }}
              />
              <button
                type="button"
                className="p-1 text-white/80 hover:text-white"
                aria-label="Save name"
                title="Save name"
                onClick={() => {
                  // Optimistic: exit edit mode immediately
                  setEditing(false);
                  try {
                    void onTitleSave?.(draft.trim());
                  } catch (err) {
                    console.error('Failed to save title', err);
                  }
                }}
              >
                <Icon name="check" size={12} />
              </button>
              <button
                type="button"
                className="p-1 text-white/60 hover:text-white"
                aria-label="Cancel"
                title="Cancel"
                onClick={() => {
                  setDraft(title);
                  setEditing(false);
                }}
              >
                <Icon name="x" size={12} />
              </button>
            </div>
          )
        ) : (
          <h2 className={cn("font-book text-sm text-[var(--color-title-text)]", titleClassName)}>{title}</h2>
        )}
      </div>

      {/* Tabs Row */}
      <div className="flex items-center gap-3 pb-0 [&::-webkit-scrollbar]:hidden" style={{ scrollbarWidth: 'none' }}>
        <div className="flex gap-6 flex-nowrap">
          {tabs.map((tab) => (
            <div key={tab.id} className="relative">
              <button
                onClick={() => {
                  if (tab.disabled) return;
                  if (tab.menu && tab.menu.length > 0) {
                    setOpenMenuId((prev) => (prev === tab.id ? null : tab.id));
                  } else {
                    onTabChange(tab.id);
                  }
                }}
                disabled={tab.disabled}
                className={cn(
                  "font-medium text-sm transition-colors whitespace-nowrap",
                  tab.disabled
                    ? "text-white/30 cursor-not-allowed"
                    : activeTab === tab.id
                      ? "text-[var(--color-tab-selected)]"
                      : "text-[var(--color-tab-unselected)] hover:text-[var(--color-tab-selected)]"
                )}
              >
                {tab.label}
                {tab.menu && tab.menu.length > 0 && (
                  <Icon name="chevron-down" size={12} className="inline-block ml-1 align-middle" />
                )}
              </button>
              {tab.menu && tab.menu.length > 0 && openMenuId === tab.id && (
                <div
                  data-em-navbar-menu="open"
                  className="absolute z-50 mt-2 min-w-[220px] text-left rounded-md border border-white/10 bg-[#1f1f1f] shadow-lg py-1"
                  style={{ top: '100%', left: 0 }}
                >
                  {tab.menu.map((item) => (
                    <button
                      key={item.id}
                      disabled={!!item.disabled}
                      onClick={() => {
                        closeTabMenus();
                        onTabChange(`${tab.id}:${item.id}`);
                      }}
                      className={cn(
                        "w-full text-left px-3 py-2 text-sm",
                        item.disabled ? 'text-white/30 cursor-not-allowed' : 'text-white/80 hover:text-white hover:bg-white/5'
                      )}
                    >
                      {item.label}
                    </button>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
        {rightSlot && (
          <div className="ml-auto flex items-center">
            {rightSlot({
              closeTabMenus,
              closeAllMenus,
              registerCloseHandler,
              setMenuOpenState: setHasExternalMenuOpen,
            })}
          </div>
        )}
      </div>

      {/* Debug Info - positioned absolutely to not affect navbar height */}
      {debugInfo && (
        <div className="absolute top-4 right-0 text-[8px] text-white/40 max-w-[200px] space-y-0.5 max-h-20 overflow-y-auto z-10">
          {debugInfo}
        </div>
      )}
    </div>
  );
}
