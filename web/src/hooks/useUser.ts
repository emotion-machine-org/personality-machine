import { useAuth } from '@clerk/nextjs';
import { useQuery } from '@tanstack/react-query';
import { apiClient } from '@/lib/api';
import { User } from '@/lib/types';

export const useUser = () => {
  const { getToken, isSignedIn, isLoaded } = useAuth();
  const isAuthDisabled = process.env.NEXT_PUBLIC_DISABLE_AUTH === 'true';

  // Mock user data for development when auth is disabled
  const mockUser: User = {
    id: 'dev-user-1',
    clerk_user_id: 'dev-clerk-1',
    email: 'dev@example.com',
    username: 'dev-user',
    display_name: 'Developer User',
    avatar_url: null,
    auth_provider: 'development',
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    onboarding_completed: false,
    onboarding_completed_at: null,
  };

  // Fetch user profile from /api/me endpoint
  const { data: user, isLoading, error, refetch } = useQuery<User>({
    queryKey: ['user', 'me'],
    queryFn: async () => {
      if (isAuthDisabled) {
        return mockUser;
      }
      const token = await getToken(
        process.env.NEXT_PUBLIC_CLERK_JWT_TEMPLATE
          ? { template: process.env.NEXT_PUBLIC_CLERK_JWT_TEMPLATE }
          : undefined
      );
      return apiClient.getCurrentUser(token);
    },
    enabled: isAuthDisabled || (isLoaded && isSignedIn), // Run immediately if auth disabled
    staleTime: 5 * 60 * 1000, // 5 minutes
    retry: 3,
  });

  return {
    user,
    isLoading: isAuthDisabled ? false : (isLoading || !isLoaded),
    isSignedIn: isAuthDisabled ? true : isSignedIn,
    error,
    refetch,
    // Convenience getters
    userId: user?.id,
    username: user?.username,
    displayName: user?.display_name,
    avatarUrl: user?.avatar_url,
    email: user?.email,
  };
};
