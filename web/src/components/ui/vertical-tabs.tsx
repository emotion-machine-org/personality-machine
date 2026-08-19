'use client';

import { cn } from '@/lib/utils';

interface Tab {
  id: string;
  label: string;
  disabled?: boolean;
}

interface VerticalTabsProps {
  tabs: readonly Tab[];
  activeTab: string;
  onTabChange: (tabId: string) => void;
  className?: string;
}

export default function VerticalTabs({
  tabs,
  activeTab,
  onTabChange,
  className,
}: VerticalTabsProps) {
  return (
    <nav className={cn("flex flex-col gap-1", className)}>
      {tabs.map((tab) => (
        <button
          key={tab.id}
          onClick={() => {
            if (!tab.disabled) {
              onTabChange(tab.id);
            }
          }}
          disabled={tab.disabled}
          className={cn(
            "text-left py-1.5 text-sm transition-colors leading-tight",
            tab.disabled
              ? "text-white/30 cursor-not-allowed"
              : activeTab === tab.id
                ? "text-[var(--color-tab-selected)]"
                : "text-[var(--color-tab-unselected)] hover:text-[var(--color-tab-selected)]"
          )}
        >
          {tab.label}
        </button>
      ))}
    </nav>
  );
}
