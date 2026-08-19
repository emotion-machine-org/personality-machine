'use client';

import { motion } from 'framer-motion';

export type OnboardingMode = 'voice' | 'text';

interface ModeSelectorProps {
  onSelectMode: (mode: OnboardingMode) => void;
}

export function ModeSelector({ onSelectMode }: ModeSelectorProps) {
  return (
    <div className="min-h-screen bg-[var(--color-panel-bg)] flex items-center justify-center p-6">
      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.4 }}
        className="w-full max-w-[556px]"
      >
        {/* Question */}
        <h1 className="text-[50px] font-light tracking-[-0.04em] leading-[100%] text-white/60 mb-8">
          How do you want to set up your companion?
        </h1>

        {/* Options */}
        <div className="flex flex-col gap-2">
          <motion.button
            type="button"
            onClick={() => onSelectMode('text')}
            className="text-left text-[50px] font-light tracking-[-0.04em] leading-[100%] text-white/80 hover:text-white transition-colors"
            whileHover={{ x: 4 }}
            transition={{ duration: 0.15 }}
          >
            By typing
          </motion.button>

          <motion.button
            type="button"
            onClick={() => onSelectMode('voice')}
            className="text-left text-[50px] font-light tracking-[-0.04em] leading-[100%] text-white/80 hover:text-white transition-colors"
            whileHover={{ x: 4 }}
            transition={{ duration: 0.15 }}
          >
            By talking
          </motion.button>
        </div>
      </motion.div>
    </div>
  );
}

export default ModeSelector;
