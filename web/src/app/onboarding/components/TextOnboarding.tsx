'use client';

import { useState, useCallback } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { OnboardingQuestion, SelectOptions, MinimalTextInput } from '@/components/onboarding/form-elements';
import { GenerativeArtCanvas } from '@/components/generative-art';

export type Purpose = 'friend' | 'coach' | 'teacher';
export type Approach = 'playful' | 'supportive' | 'challenging';
export type Tone = 'casual' | 'direct' | 'formal';

export interface OnboardingAnswers {
  purpose?: Purpose | string;
  approach?: Approach;
  tone?: Tone;
  name?: string;
  customPurpose?: string;
}

interface TextOnboardingProps {
  onComplete: (answers: OnboardingAnswers) => void;
  onSwitchToVoice?: () => void;
}

const PURPOSE_OPTIONS = [
  { value: 'friend', label: 'Friend' },
  { value: 'coach', label: 'Coach' },
  { value: 'teacher', label: 'Teacher' },
];

const APPROACH_OPTIONS = [
  { value: 'playful', label: 'Playful' },
  { value: 'supportive', label: 'Supportive' },
  { value: 'challenging', label: 'Challenging' },
];

const TONE_OPTIONS = [
  { value: 'casual', label: 'Casual' },
  { value: 'direct', label: 'Direct' },
  { value: 'formal', label: 'Formal' },
];

export function TextOnboarding({ onComplete, onSwitchToVoice }: TextOnboardingProps) {
  const [step, setStep] = useState(1);
  const [answers, setAnswers] = useState<OnboardingAnswers>({});
  const [isCreating, setIsCreating] = useState(false);
  const [customPurpose, setCustomPurpose] = useState('');

  const handlePurposeSelect = useCallback((value: string) => {
    setAnswers((prev) => ({ ...prev, purpose: value as Purpose, customPurpose: undefined }));
    setCustomPurpose('');
    // Auto-advance after a brief delay for better UX
    setTimeout(() => setStep(2), 200);
  }, []);

  const handleCustomPurposeSubmit = useCallback((value: string) => {
    setAnswers((prev) => ({ ...prev, purpose: 'custom', customPurpose: value }));
    setTimeout(() => setStep(2), 200);
  }, []);

  const handleApproachSelect = useCallback((value: string) => {
    setAnswers((prev) => ({ ...prev, approach: value as Approach }));
    setTimeout(() => setStep(3), 200);
  }, []);

  const handleToneSelect = useCallback((value: string) => {
    const newAnswers = { ...answers, tone: value as Tone };
    setAnswers(newAnswers);
    setIsCreating(true);

    // Simulate creation delay then complete
    setTimeout(() => {
      onComplete(newAnswers);
    }, 1500);
  }, [answers, onComplete]);

  const handleBack = useCallback(() => {
    if (step > 1) {
      setStep(step - 1);
    }
  }, [step]);

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
            Creating...
          </motion.p>
        </motion.div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-[var(--color-panel-bg)] flex items-center justify-center p-6">
      <div className="w-full max-w-[556px]">
        <AnimatePresence mode="wait">
          {step === 1 && (
            <OnboardingQuestion
              key="purpose"
              question="What role should your companion play?"
            >
              <SelectOptions
                options={PURPOSE_OPTIONS}
                value={answers.purpose || null}
                onChange={handlePurposeSelect}
              />
              <div className="mt-4">
                <MinimalTextInput
                  value={customPurpose}
                  onChange={setCustomPurpose}
                  onSubmit={handleCustomPurposeSubmit}
                  placeholder="Something else..."
                />
              </div>
            </OnboardingQuestion>
          )}

          {step === 2 && (
            <OnboardingQuestion
              key="approach"
              question="How should your companion interact with you?"
              onBack={handleBack}
            >
              <SelectOptions
                options={APPROACH_OPTIONS}
                value={answers.approach || null}
                onChange={handleApproachSelect}
              />
            </OnboardingQuestion>
          )}

          {step === 3 && (
            <OnboardingQuestion
              key="tone"
              question="What tone should your companion use?"
              onBack={handleBack}
            >
              <SelectOptions
                options={TONE_OPTIONS}
                value={answers.tone || null}
                onChange={handleToneSelect}
              />
            </OnboardingQuestion>
          )}
        </AnimatePresence>

        {/* Switch to voice mode */}
        {onSwitchToVoice && (
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
      </div>
    </div>
  );
}

export default TextOnboarding;
