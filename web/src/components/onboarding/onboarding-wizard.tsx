'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { useAuth } from '@clerk/nextjs';
import { useCreateCompanion } from '@/hooks/useCompanions';
import { apiClient } from '@/lib/api';

const DEFAULT_TEMPLATE = {
  name: 'Awesome Voice AI App',
  description: 'Create your own custom AI companion from scratch',
  config: {
    system_prompt: {
      identity: 'You are a helpful AI assistant',
      personality: 'Friendly and professional',
      style: 'Clear and helpful',
      backstory: 'You assist users with various tasks',
      additional_instructions: 'Be helpful and respond appropriately to user needs',
      self_image: 'A capable assistant ready to help',
      full_system_prompt: 'You are a helpful AI assistant. You are friendly and professional. Respond in a clear and helpful way. You assist users with various tasks. Be helpful and respond appropriately to user needs. You see yourself as a capable assistant ready to help.'
    },
    voice: {
      preset: 'gpt4o-mini-elevenlabs',
      voice_name: 'alloy',
    },
    inference: {
      model: 'openai-gpt4o-mini',
      temperature: 0.7,
      max_output_tokens: null,
    },
    memory: {
      enabled: false,
      core_memories: [],
      memory_evaluation_prompt: '',
      recency: 0.995,
      top_k: 50,
      min_saliency: 0.2
    }
  }
};

interface OnboardingWizardProps {
  onComplete?: () => void;
}

export default function OnboardingWizard({ onComplete }: OnboardingWizardProps) {
  const [isCreating, setIsCreating] = useState(false);
  const createCompanion = useCreateCompanion();
  const { getToken } = useAuth();
  const router = useRouter();

  const handleCreateCompanion = async () => {
    if (isCreating) return;

    setIsCreating(true);
    try {
      await createCompanion.mutateAsync({
        name: DEFAULT_TEMPLATE.name,
        description: DEFAULT_TEMPLATE.description,
        config: DEFAULT_TEMPLATE.config
      });

      const token = await getToken(
        process.env.NEXT_PUBLIC_CLERK_JWT_TEMPLATE
          ? { template: process.env.NEXT_PUBLIC_CLERK_JWT_TEMPLATE }
          : undefined
      );
      await apiClient.completeOnboarding(token);

      const finish = () => {
        if (onComplete) {
          onComplete();
        } else {
          router.push('/');
        }
      };

      setTimeout(finish, 1000);
    } catch (error) {
      console.error('Failed to complete onboarding:', error);
      setIsCreating(false);
    }
  };

  return (
    <div className="min-h-screen bg-black text-white">
      <main className="flex min-h-screen flex-col items-center justify-center px-6 py-16">
        <div className="w-full max-w-2xl space-y-12 text-center">
          <div className="space-y-6">
            <h1
              className="px-8 text-[40px] font-light leading-[1.2] tracking-[-0.04em]"
            >
              Build a companion that speaks, listens, and remembers
            </h1>
            <p
              className="text-white/60 text-[18px] font-light leading-[1.3]"
            >
              Personality Machine connects best-in-class speech, memory, and analytics so you can launch a thoughtful AI companion without wiring everything by hand.
            </p>
          </div>

          <div
            className="space-y-5 text-center text-[16px] font-light"
          >
            <div className="text-white">
              <p className="font-semibold mb-2">Design conversations, not plumbing</p>
              <p className="text-white/60">&nbsp;We handle speech-to-speech routing, state, and transcription so you can focus on the experience.</p>
            </div>
            <div className="text-white">
              <p className="font-semibold mb-2">Adapt to your stack</p>
              <p className="text-white/60">&nbsp;Swap between OpenAI, ElevenLabs, Cartesia, and more in a few clicks, with presets that work out of the box.</p>
            </div>
            <div className="text-white">
              <p className="font-semibold mb-2">Ship with confidence</p>
              <p className="text-white/60">&nbsp;Test with real users, view transcripts, and keep iterating without redeploying your app.</p>
            </div>
          </div>

          <div className="space-y-3">
            <button
              type="button"
              onClick={handleCreateCompanion}
              disabled={isCreating}
              aria-busy={isCreating}
              className="w-full bg-white text-black py-4 rounded-full font-medium hover:bg-white/80 transition-colors disabled:cursor-not-allowed disabled:opacity-50 flex items-center justify-center gap-3"
            >
              {isCreating && (
                <span
                  className="h-5 w-5 rounded-full border-2 border-black/40 border-t-black animate-spin"
                  aria-hidden="true"
                />
              )}
              <span className="translate-y-0.5">
                {isCreating ? 'Setting things up…' : 'Get Started'}
              </span>
            </button>
            <p
              className="text-[14px] text-white/60 font-light"
            >
              We’ll create a starter companion you can customize instantly.
            </p>
          </div>
        </div>
      </main>
    </div>
  );
}
