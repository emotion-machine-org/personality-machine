'use client';

import { useEffect } from 'react';
import { useClerk } from '@clerk/nextjs';
import { useSelectedCompanion } from '@/components/providers';

export default function LogoutPage() {
  const { signOut } = useClerk();
  const { setSelectedCompanionId } = useSelectedCompanion();

  useEffect(() => {
    const run = async () => {
      try {
        setSelectedCompanionId(null);
      } catch {
        // Ignore local state cleanup failures during sign-out.
      }
      await signOut({ redirectUrl: '/' });
    };
    run();
  }, [setSelectedCompanionId, signOut]);

  return (
    <div className="flex min-h-[50vh] items-center justify-center px-6">
      <p className="text-sm text-white/60">Signing you out...</p>
    </div>
  );
}
