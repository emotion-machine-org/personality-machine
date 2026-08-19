/**
 * Hook for managing Twilio outbound calls from the dashboard.
 *
 * Initiates a call via the /twilio/dial-out endpoint and tracks call status
 * using Twilio status callbacks with polling.
 */

import { useAuth } from '@clerk/nextjs';
import { useState, useCallback, useEffect, useRef } from 'react';
import { apiClient, TwilioDialOutResponse, ApiError, getTwilioCallStatus } from '@/lib/api';

export type TwilioCallStatus = 'idle' | 'dialing' | 'ringing' | 'connected' | 'ended' | 'error';

interface UseTwilioDialOutOptions {
  companionId: string | null;
  userId: string | null;
}

interface UseTwilioDialOutReturn {
  // State
  status: TwilioCallStatus;
  callSid: string | null;
  error: string | null;

  // Actions
  dialOut: (phoneNumber: string) => Promise<void>;
  reset: () => void;
}

/**
 * Map Twilio status to UI status.
 * Twilio statuses: queued, initiated, ringing, in-progress, completed, busy, no-answer, canceled, failed
 */
function mapTwilioStatus(twilioStatus: string): TwilioCallStatus {
  switch (twilioStatus) {
    case 'queued':
    case 'initiated':
      return 'dialing';
    case 'ringing':
      return 'ringing';
    case 'in-progress':
    case 'answered':
      return 'connected';
    case 'completed':
    case 'busy':
    case 'no-answer':
    case 'canceled':
      return 'ended';
    case 'failed':
      return 'error';
    default:
      // Unknown status - keep current
      return 'dialing';
  }
}

export function useTwilioDialOut({
  companionId,
  userId,
}: UseTwilioDialOutOptions): UseTwilioDialOutReturn {
  const { getToken } = useAuth();

  const [status, setStatus] = useState<TwilioCallStatus>('idle');
  const [callSid, setCallSid] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Track if we should keep polling
  const pollingRef = useRef<boolean>(false);
  const pollIntervalRef = useRef<NodeJS.Timeout | null>(null);

  // Helper to get auth token
  const getAuthToken = useCallback(async () => {
    const isAuthDisabled = process.env.NEXT_PUBLIC_DISABLE_AUTH === 'true';
    if (isAuthDisabled) return 'mock-dev-token';
    return getToken(
      process.env.NEXT_PUBLIC_CLERK_JWT_TEMPLATE
        ? { template: process.env.NEXT_PUBLIC_CLERK_JWT_TEMPLATE }
        : undefined
    );
  }, [getToken]);

  // Poll for status updates
  const pollStatus = useCallback(async (sid: string) => {
    try {
      const statusResponse = await getTwilioCallStatus(sid);
      const newStatus = mapTwilioStatus(statusResponse.status);

      setStatus(prevStatus => {
        // Don't downgrade status (e.g., from connected to ringing)
        if (prevStatus === 'connected' && newStatus !== 'ended' && newStatus !== 'error') {
          return prevStatus;
        }
        return newStatus;
      });

      // Stop polling if call ended or errored
      if (newStatus === 'ended' || newStatus === 'error') {
        pollingRef.current = false;
      }
    } catch (err) {
      console.error('[TWILIO_POLL] Error polling status:', err);
      // Don't change status on poll error - keep trying
    }
  }, []);

  // Effect to manage polling
  useEffect(() => {
    if (callSid && pollingRef.current) {
      // Start polling
      const poll = () => {
        if (pollingRef.current && callSid) {
          pollStatus(callSid);
        }
      };

      // Poll immediately
      poll();

      // Then poll every 2 seconds
      pollIntervalRef.current = setInterval(poll, 2000);

      return () => {
        if (pollIntervalRef.current) {
          clearInterval(pollIntervalRef.current);
          pollIntervalRef.current = null;
        }
      };
    }
  }, [callSid, pollStatus]);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      pollingRef.current = false;
      if (pollIntervalRef.current) {
        clearInterval(pollIntervalRef.current);
      }
    };
  }, []);

  const dialOut = useCallback(async (phoneNumber: string) => {
    if (!companionId || !userId) {
      setError('Companion or user not selected');
      setStatus('error');
      return;
    }

    setStatus('dialing');
    setError(null);
    setCallSid(null);
    pollingRef.current = false;

    try {
      const token = await getAuthToken();
      const response: TwilioDialOutResponse = await apiClient.twilioDialOut(
        companionId,
        userId,
        phoneNumber,
        token
      );

      setCallSid(response.call_sid);

      // Start polling for status updates
      pollingRef.current = true;
    } catch (err) {
      console.error('[TWILIO_DIAL_OUT] Error:', err);

      if (err instanceof ApiError) {
        setError(err.message);
      } else if (err instanceof Error) {
        setError(err.message);
      } else {
        setError('Failed to initiate call');
      }

      setStatus('error');
    }
  }, [companionId, userId, getAuthToken]);

  const reset = useCallback(() => {
    pollingRef.current = false;
    if (pollIntervalRef.current) {
      clearInterval(pollIntervalRef.current);
      pollIntervalRef.current = null;
    }
    setStatus('idle');
    setCallSid(null);
    setError(null);
  }, []);

  return {
    status,
    callSid,
    error,
    dialOut,
    reset,
  };
}
