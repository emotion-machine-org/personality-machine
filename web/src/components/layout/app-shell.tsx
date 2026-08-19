'use client';

import GlobalNavbar from '@/components/layout/global-navbar';

interface AppShellProps {
  children: React.ReactNode;
}

export default function AppShell({ children }: AppShellProps) {
  return (
    <div className="flex h-[100dvh] min-h-0 flex-col bg-black overflow-hidden">
      <GlobalNavbar />
      <main className="flex-1 min-h-0 overflow-hidden">
        {children}
      </main>
    </div>
  );
}
