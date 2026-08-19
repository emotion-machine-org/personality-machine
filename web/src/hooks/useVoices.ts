import { useQuery } from '@tanstack/react-query';
import { apiClient, type VoiceMappings } from '@/lib/api';

// Hook for fetching voice mappings from all providers
export const useVoiceMappings = () => {
  return useQuery<VoiceMappings>({
    queryKey: ['voice-mappings'],
    queryFn: () => apiClient.getVoiceMappings(),
    staleTime: Infinity, // Never refetch once loaded
    gcTime: Infinity, // Keep in cache indefinitely
  });
};
