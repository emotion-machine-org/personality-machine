'use client';

import { QueryClient, QueryClientProvider, QueryCache, MutationCache, useQueryClient } from '@tanstack/react-query';
import { ReactQueryDevtools } from '@tanstack/react-query-devtools';
import { useState, useEffect, createContext, useContext, ReactNode, useCallback, useRef, Suspense } from 'react';
import { useSearchParams } from 'next/navigation';
import { useAuth } from '@clerk/nextjs';
import { WebSocketSessionProvider } from '@/hooks/useWebSocketSession';
import { isApiError } from '@/lib/api';
import { SignInButton } from '@clerk/nextjs';

// ============================================================================
// Auth Error Context - handles 401/403 errors globally
// ============================================================================

interface AuthErrorContextType {
  showAuthError: boolean;
  setShowAuthError: (show: boolean) => void;
  triggerAuthError: () => void;
}

const AuthErrorContext = createContext<AuthErrorContextType | undefined>(undefined);

export const useAuthError = () => {
  const context = useContext(AuthErrorContext);
  if (context === undefined) {
    throw new Error('useAuthError must be used within Providers');
  }
  return context;
};

function AuthErrorModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      {/* Backdrop */}
      <div className="absolute inset-0 bg-black/60 backdrop-blur-[2px]" />

      {/* Dialog */}
      <div className="relative z-10 w-[400px] max-w-[92vw] bg-[color:var(--color-gray-darker)] p-8 shadow-xl">
        <div className="space-y-3">
          <h2 className="text-[17px] font-light text-white/70">Session Expired</h2>
          <p className="text-[20px] font-light text-white leading-relaxed">
            Your session has expired. Please sign in again to continue.
          </p>
        </div>

        <div className="mt-7 flex items-center justify-end gap-3">
          <button
            onClick={onClose}
            className="text-[20px] font-light text-white/40 hover:text-white transition-colors"
          >
            Dismiss
          </button>
          <SignInButton mode="modal">
            <button className="text-[20px] font-light text-[color:var(--color-brand-solid)] hover:text-[color:var(--color-brand-solid)]/80 transition-colors">
              Sign In
            </button>
          </SignInButton>
        </div>
      </div>
    </div>
  );
}

// ============================================================================
// Companion Selection Context
// ============================================================================

type OnSaveCallback = () => void | Promise<void>;

interface CompanionContextType {
  selectedCompanionId: string | null;
  setSelectedCompanionId: (id: string | null) => void;
  registerOnSaveCallback: (callback: OnSaveCallback) => void;
  unregisterOnSaveCallback: (callback: OnSaveCallback) => void;
  triggerOnSaveCallbacks: () => Promise<void>;
}

const CompanionContext = createContext<CompanionContextType | undefined>(undefined);

export const useSelectedCompanion = () => {
  const context = useContext(CompanionContext);
  if (context === undefined) {
    throw new Error('useSelectedCompanion must be used within a CompanionProvider');
  }
  return context;
};

// Separate component for URL sync to allow Suspense boundary
function CompanionUrlSync({ setSelectedCompanionId }: { setSelectedCompanionId: (id: string | null) => void }) {
  const searchParams = useSearchParams();

  // Sync companion ID from URL param - runs on mount and when URL changes
  // This ensures client-side navigation (e.g., from onboarding) updates the selection
  useEffect(() => {
    const companionFromUrl = searchParams.get('companion');

    if (companionFromUrl) {
      setSelectedCompanionId(companionFromUrl);
    }
  }, [searchParams, setSelectedCompanionId]);

  return null;
}

function CompanionProvider({ children }: { children: ReactNode }) {
  // Initialize to null on both server and client to avoid hydration mismatch.
  // URL param is read on mount and on URL changes via CompanionUrlSync.
  const [selectedCompanionId, setSelectedCompanionId] = useState<string | null>(null);
  const { userId: clerkUserId } = useAuth();
  const queryClient = useQueryClient();
  const previousUserIdRef = useRef<string | null | undefined>(undefined);

  // Clear state and cache when user changes (sign-out/sign-in)
  // This prevents stale companion IDs from previous accounts
  useEffect(() => {
    // Skip on initial mount (previousUserIdRef is undefined)
    if (previousUserIdRef.current === undefined) {
      previousUserIdRef.current = clerkUserId;
      return;
    }

    // User changed - clear everything
    if (previousUserIdRef.current !== clerkUserId) {
      console.log('[CompanionProvider] User changed, clearing state and cache');
      setSelectedCompanionId(null);
      // Clear all companion-related queries from previous user
      queryClient.removeQueries({ predicate: (query) => {
        const key = query.queryKey[0];
        return key === 'companions' || key === 'testUsers' || key === 'testUser';
      }});
      previousUserIdRef.current = clerkUserId;
    }
  }, [clerkUserId, queryClient]);

  // Callbacks for when companion config is saved
  const onSaveCallbacksRef = useRef<Set<OnSaveCallback>>(new Set());

  const registerOnSaveCallback = useCallback((callback: OnSaveCallback) => {
    onSaveCallbacksRef.current.add(callback);
  }, []);

  const unregisterOnSaveCallback = useCallback((callback: OnSaveCallback) => {
    onSaveCallbacksRef.current.delete(callback);
  }, []);

  const triggerOnSaveCallbacks = useCallback(async () => {
    const callbacks = Array.from(onSaveCallbacksRef.current);
    for (const callback of callbacks) {
      try {
        await callback();
      } catch (error) {
        console.error('[CompanionProvider] Error in onSave callback:', error);
      }
    }
  }, []);

  return (
    <CompanionContext.Provider value={{
      selectedCompanionId,
      setSelectedCompanionId,
      registerOnSaveCallback,
      unregisterOnSaveCallback,
      triggerOnSaveCallbacks,
    }}>
      {/* Suspense boundary for useSearchParams - required by Next.js 15 */}
      <Suspense fallback={null}>
        <CompanionUrlSync setSelectedCompanionId={setSelectedCompanionId} />
      </Suspense>
      {children}
    </CompanionContext.Provider>
  );
}

export default function Providers({ children }: { children: React.ReactNode }) {
  const [showAuthError, setShowAuthError] = useState(false);

  // Use ref to avoid recreating QueryClient when auth error state changes
  const triggerAuthErrorRef = useRef<() => void>(() => {});
  triggerAuthErrorRef.current = () => setShowAuthError(true);

  const [queryClient] = useState(() => new QueryClient({
    queryCache: new QueryCache({
      onError: (error) => {
        // Show auth error modal on 401/403 errors
        if (isApiError(error) && error.isAuthError) {
          triggerAuthErrorRef.current();
        }
      },
    }),
    mutationCache: new MutationCache({
      onError: (error) => {
        // Show auth error modal on 401/403 errors
        if (isApiError(error) && error.isAuthError) {
          triggerAuthErrorRef.current();
        }
      },
    }),
    defaultOptions: {
      queries: {
        // With SSR, we usually want to set some default staleTime
        // above 0 to avoid refetching immediately on the client
        staleTime: 60 * 1000,
        retry: (failureCount, error: unknown) => {
          // Don't retry on 401/403 auth errors - user needs to sign in
          if (isApiError(error) && error.isAuthError) {
            return false;
          }
          return failureCount < 3;
        },
      },
      mutations: {
        retry: (failureCount, error: unknown) => {
          // Don't retry on 401/403 auth errors - user needs to sign in
          if (isApiError(error) && error.isAuthError) {
            return false;
          }
          return failureCount < 3;
        },
      },
    },
  }));

  const triggerAuthError = useCallback(() => {
    setShowAuthError(true);
  }, []);

  return (
    <AuthErrorContext.Provider value={{ showAuthError, setShowAuthError, triggerAuthError }}>
      <QueryClientProvider client={queryClient}>
        <CompanionProvider>
          <WebSocketSessionProvider>
            {children}
            <ReactQueryDevtools initialIsOpen={false} />
          </WebSocketSessionProvider>
        </CompanionProvider>
      </QueryClientProvider>
      <AuthErrorModal open={showAuthError} onClose={() => setShowAuthError(false)} />
    </AuthErrorContext.Provider>
  );
}
