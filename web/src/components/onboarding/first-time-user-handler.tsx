'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { useCompanions, useCreateDefaultCompanion } from '@/hooks/useCompanions';
import { useSelectedCompanion } from '@/components/providers';

interface FirstTimeUserHandlerProps {
  children: React.ReactNode;
  enableOnboarding?: boolean;
}

export default function FirstTimeUserHandler({
  children,
  enableOnboarding = false
}: FirstTimeUserHandlerProps) {
  const { data: companions, isLoading: companionsLoading, error } = useCompanions();
  const createDefaultCompanion = useCreateDefaultCompanion();
  const { selectedCompanionId, setSelectedCompanionId } = useSelectedCompanion();
  const [isCreatingDefault, setIsCreatingDefault] = useState(false);
  const router = useRouter();

  useEffect(() => {
    // Don't do anything if still loading or there's an error
    if (companionsLoading || isCreatingDefault) return;

    // If there's an error, log it but still show children (fallback)
    if (error) {
      console.error('Error fetching companions:', error);
      return;
    }

    // If no companions exist
    if (companions && companions.length === 0) {
      if (enableOnboarding) {
        // Redirect to onboarding flow
        console.log('Redirecting to onboarding...');
        router.push('/onboarding');
        return;
      } else {
        // Auto-create default companion (existing behavior)
        setIsCreatingDefault(true);
        createDefaultCompanion.mutate(undefined, {
          onSuccess: () => {
            setTimeout(() => {
              setIsCreatingDefault(false);
            }, 500);
          },
          onError: (error) => {
            console.error('Failed to create default companion:', error);
            setIsCreatingDefault(false);
          }
        });
      }
    } else if (companions && companions.length > 0) {
      // Only auto-select if no companion is selected at all
      // Don't override URL-based selection - trust it even if not in cached list yet
      // (the list will refresh and include the new companion)
      if (!selectedCompanionId) {
        setSelectedCompanionId(companions[0].id);
      }
    }
  }, [companions, companionsLoading, error, selectedCompanionId, setSelectedCompanionId, createDefaultCompanion, isCreatingDefault, enableOnboarding, router]);

  // Show loading state only while creating default companion
  // Don't block UI just for loading - let components handle their own loading states
  if (isCreatingDefault) {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <div className="text-center">
          <div className="relative w-8 h-8 mb-4 mx-auto">
            {/* Soft, expanding pulse with blurred edge */}
            <span
              className="absolute inset-0 rounded-full animate-ping"
              style={{
                background:
                  'radial-gradient(circle, rgba(255,36,76,0.5) 35%, rgba(255,36,76,0.25) 60%, rgba(255,36,76,0.0) 75%)',
                filter: 'blur(2px)'
              }}
            />
            {/* Core dot with feathered border + glow */}
            <span
              className="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 rounded-full"
              style={{
                width: '10px',
                height: '10px',
                background: 'radial-gradient(circle, #FF244C 60%, rgba(255,36,76,0.0) 80%)',
                filter: 'blur(0.8px)',
                boxShadow: '0 0 12px rgba(255,36,76,0.55)'
              }}
            />
          </div>
          <p className="text-white/30">
            {enableOnboarding ? 'Preparing your setup...' : 'Setting up your AI companion...'}
          </p>
        </div>
      </div>
    );
  }

  return <>{children}</>;
}
