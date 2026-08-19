'use client';

import { useState, useCallback, useRef, useEffect } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { MinimalTextInput } from '@/components/onboarding/form-elements';
import { GenerativeArtCanvas } from '@/components/generative-art';
import { API_CONFIG } from '@/lib/config';

interface Message {
  role: 'user' | 'assistant';
  content: string;
}

interface ConversationalTextOnboardingProps {
  companionId: string;
  getToken: () => Promise<string | null>;
  onComplete: (companionId: string) => void;
  onSwitchToVoice?: () => void;
}

// Patterns that indicate companion creation is complete
const COMPLETION_PATTERNS = [
  /is ready/i,
  /is being created/i,
  /creating your companion/i,
  /companion.*created/i,
  /all set/i,
  /you.*redirected/i,
];

// Extract companion name from completion message (e.g., "Your companion 'Luna' is ready!")
function extractCompanionName(message: string): string | null {
  const patterns = [
    /['"]([^'"]+)['"]\s*is\s*ready/i,
    /companion\s+['"]?([^'"!.]+)['"]?\s*is/i,
    /creating\s+['"]?([^'"!.]+)['"]?/i,
  ];
  for (const pattern of patterns) {
    const match = message.match(pattern);
    if (match?.[1]) {
      return match[1].trim();
    }
  }
  return null;
}

export function ConversationalTextOnboarding({
  companionId,
  getToken,
  onComplete,
  onSwitchToVoice,
}: ConversationalTextOnboardingProps) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [inputValue, setInputValue] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [isCreating, setIsCreating] = useState(false);
  const [createdCompanionName, setCreatedCompanionName] = useState<string | null>(null);
  const [dotCount, setDotCount] = useState(0);
  const completionCheckRef = useRef(false);

  // Animate dots when loading
  useEffect(() => {
    if (!isLoading) return;
    const interval = setInterval(() => {
      setDotCount((c) => (c + 1) % 4);
    }, 400);
    return () => clearInterval(interval);
  }, [isLoading]);

  // Current question to display
  const currentQuestion = messages.length === 0
    ? "What kind of companion do you want?"
    : messages[messages.length - 1]?.role === 'assistant'
      ? messages[messages.length - 1].content
      : null;

  // Check if a message indicates completion
  const checkForCompletion = useCallback((content: string): boolean => {
    return COMPLETION_PATTERNS.some(pattern => pattern.test(content));
  }, []);

  // Send message to onboarding companion via JWT-authed API
  const sendMessage = useCallback(async (content: string) => {
    if (isLoading || completionCheckRef.current) return;

    setIsLoading(true);
    setMessages(prev => [...prev, { role: 'user', content }]);
    setInputValue('');

    try {
      // Get fresh token for each request
      const token = await getToken();
      if (!token) {
        throw new Error('Not authenticated');
      }

      const response = await fetch(
        `${API_CONFIG.BASE_URL}/api/onboarding/chat`,
        {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${token}`,
          },
          body: JSON.stringify({ message: content }),
        }
      );

      if (!response.ok) {
        throw new Error(`API error: ${response.status}`);
      }

      const data = await response.json();
      const assistantMessage = data.response || '';

      setMessages(prev => [...prev, { role: 'assistant', content: assistantMessage }]);

      // Check if this response indicates completion
      if (checkForCompletion(assistantMessage)) {
        completionCheckRef.current = true;
        setIsCreating(true);

        const extractedName = extractCompanionName(assistantMessage);
        setCreatedCompanionName(extractedName || 'your companion');

        // Give a moment for the user to see the message, then find the new companion
        setTimeout(async () => {
          try {
            // Fetch user's companions to find the newly created one
            const companionsResponse = await fetch(
              `${API_CONFIG.BASE_URL}/api/companions`,
              {
                headers: {
                  'Authorization': `Bearer ${token}`,
                },
              }
            );

            if (companionsResponse.ok) {
              const companions = await companionsResponse.json();
              // Find the most recently created companion (excluding onboarding companion)
              const userCompanions = companions.filter(
                (c: { id: string }) => c.id !== companionId
              );
              if (userCompanions.length > 0) {
                // Sort by created_at descending and take the first
                const newest = userCompanions.sort(
                  (a: { created_at: string }, b: { created_at: string }) =>
                    new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
                )[0];
                onComplete(newest.id);
                return;
              }
            }
            // Fallback: just redirect without specific companion
            onComplete('');
          } catch {
            onComplete('');
          }
        }, 1500);
      }
    } catch (error) {
      console.error('Failed to send message:', error);
      // Remove the user message on error
      setMessages(prev => prev.slice(0, -1));
      setInputValue(content); // Restore input
    } finally {
      setIsLoading(false);
    }
  }, [companionId, getToken, isLoading, checkForCompletion, onComplete]);

  const handleSubmit = useCallback((value: string) => {
    if (value.trim()) {
      sendMessage(value.trim());
    }
  }, [sendMessage]);

  // Creating state - show loading animation
  if (isCreating) {
    return (
      <div className="flex flex-col items-center justify-center min-h-screen bg-[var(--color-panel-bg)] p-6">
        <motion.div
          initial={{ opacity: 0, scale: 0.9 }}
          animate={{ opacity: 1, scale: 1 }}
          transition={{ duration: 0.3 }}
          className="flex flex-col items-center"
        >
          <GenerativeArtCanvas
            width={200}
            height={200}
            isThinking={true}
            palette="ember"
          />
          <motion.p
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ delay: 0.3 }}
            className="mt-8 text-[50px] font-light tracking-[-0.04em] leading-[100%] text-white/60"
          >
            Creating {createdCompanionName}...
          </motion.p>
        </motion.div>
      </div>
    );
  }


  return (
    <div className="min-h-screen bg-[var(--color-panel-bg)] flex items-center justify-center p-6">
      <div className="w-full max-w-[556px]">
        <AnimatePresence mode="wait">
          <motion.div
            key={messages.length}
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -20 }}
            transition={{ duration: 0.3, ease: 'easeOut' }}
            className="w-full"
          >
            {/* Question */}
            {currentQuestion && (
              <h2 className="text-[50px] font-light tracking-[-0.04em] leading-[100%] text-white/60 mb-8">
                {currentQuestion}
              </h2>
            )}

            {/* Input area */}
            {!isLoading && currentQuestion && (
              <div className="mb-8">
                <MinimalTextInput
                  value={inputValue}
                  onChange={setInputValue}
                  onSubmit={handleSubmit}
                  placeholder="Type your answer..."
                />
              </div>
            )}

            {/* Loading indicator */}
            {isLoading && (
              <motion.h2
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                className="text-[50px] font-light tracking-[-0.04em] leading-[100%] text-white/60 mb-8"
              >
                Thinking{'.'.repeat(dotCount)}
              </motion.h2>
            )}
          </motion.div>
        </AnimatePresence>

        {/* Switch to voice mode */}
        {onSwitchToVoice && messages.length === 0 && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ delay: 0.5 }}
            className="mt-16"
          >
            <button
              type="button"
              onClick={onSwitchToVoice}
              className="text-white/40 hover:text-white/60 text-sm font-light transition-colors"
            >
              Prefer to talk? Switch to voice mode
            </button>
          </motion.div>
        )}

        {/* Start over button (after first message) */}
        {messages.length > 0 && !isLoading && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            className="mt-8"
          >
            <button
              type="button"
              onClick={() => {
                setMessages([]);
                setInputValue('');
              }}
              className="text-white/40 hover:text-white/60 text-sm font-light transition-colors"
            >
              Start over
            </button>
          </motion.div>
        )}
      </div>
    </div>
  );
}

export default ConversationalTextOnboarding;
