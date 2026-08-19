'use client';

import { useRouter, usePathname } from 'next/navigation';
import { useSelectedCompanion } from '@/components/providers';
import { cn } from '@/lib/utils';
import CompanionDropdown from './companion-dropdown';
import IntegratePopover from './integrate-popover';
import SharePopover from './share-popover';
import UserDropdown from './user-dropdown';

interface NavItem {
  label: string;
  href: string;
  requiresCompanion?: boolean;
}

const NAV_ITEMS: NavItem[] = [
  { label: 'Builder', href: '/' },
  { label: 'Relationships', href: '/relationships', requiresCompanion: true },
  { label: 'API Keys', href: '/api-keys' },
];

export default function GlobalNavbar() {
  const router = useRouter();
  const pathname = usePathname();
  const { selectedCompanionId } = useSelectedCompanion();

  const handleNavigate = (item: NavItem) => {
    if (item.requiresCompanion && !selectedCompanionId) {
      return;
    }
    router.push(item.href);
  };

  const isActive = (href: string) => {
    if (href === '/') {
      return pathname === '/';
    }
    return pathname === href || pathname.startsWith(`${href}/`);
  };

  return (
    <header className="border-b border-white/20 bg-[var(--color-panel-bg)]">
      <div className="flex items-center px-4 py-3">
        {/* Left: Companion dropdown */}
        <CompanionDropdown />

        {/* Right: Navigation + Actions + User */}
        <div className="flex items-center gap-4 ml-auto">
          <nav className="flex items-center gap-5 text-sm">
            {NAV_ITEMS.map((item) => {
              const disabled = item.requiresCompanion && !selectedCompanionId;
              return (
                <button
                  key={item.href}
                  type="button"
                  onClick={() => handleNavigate(item)}
                  disabled={disabled}
                  className={cn(
                    'whitespace-nowrap transition-colors',
                    disabled
                      ? 'cursor-not-allowed text-white/30'
                      : isActive(item.href)
                        ? 'text-white'
                        : 'text-white/50 hover:text-white'
                  )}
                >
                  {item.label}
                </button>
              );
            })}
          </nav>
          <div className="flex items-center gap-1">
            <IntegratePopover />
            <SharePopover />
          </div>
          <UserDropdown />
        </div>
      </div>
    </header>
  );
}
