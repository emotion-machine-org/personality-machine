'use client';

import { motion } from 'framer-motion';
import { ChevronLeft } from 'lucide-react';

export interface OnboardingQuestionProps {
  question: string;
  subtitle?: string;
  currentStep?: number;
  totalSteps?: number;
  onBack?: () => void;
  children: React.ReactNode;
}

export function OnboardingQuestion({
  question,
  subtitle,
  currentStep,
  totalSteps,
  onBack,
  children,
}: OnboardingQuestionProps) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -20 }}
      transition={{ duration: 0.3, ease: 'easeOut' }}
      className="w-full"
    >
      {/* Step indicator - only show if steps are provided */}
      {currentStep && totalSteps && (
        <div className="text-white/40 text-sm font-light mb-6">
          Step {currentStep} of {totalSteps}
        </div>
      )}

      {/* Question */}
      <h2 className="text-[50px] font-light tracking-[-0.04em] leading-[100%] text-white/60 mb-8">
        {question}
      </h2>

      {/* Subtitle */}
      {subtitle && (
        <p className="text-white/40 mb-8">{subtitle}</p>
      )}

      {/* Options */}
      <div className="mb-8">
        {children}
      </div>

      {/* Back button */}
      {onBack && (
        <button
          type="button"
          onClick={onBack}
          className="flex items-center gap-1 text-white/40 hover:text-white/60 text-sm font-light transition-colors mt-8"
        >
          <ChevronLeft size={16} />
          <span>Back</span>
        </button>
      )}
    </motion.div>
  );
}

export default OnboardingQuestion;
