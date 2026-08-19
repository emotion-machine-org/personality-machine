'use client';

import { useState, useRef } from 'react';
import { motion } from 'framer-motion';

export interface MinimalTextInputProps {
  value: string;
  onChange: (value: string) => void;
  onSubmit?: (value: string) => void;
  placeholder?: string;
}

export function MinimalTextInput({
  value,
  onChange,
  onSubmit,
  placeholder = 'Something else...',
}: MinimalTextInputProps) {
  const [isFocused, setIsFocused] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && value.trim() && onSubmit) {
      onSubmit(value.trim());
    }
  };

  const showPlaceholder = !value && !isFocused;

  return (
    <div className="relative">
      <div
        className="relative cursor-text"
        onClick={() => inputRef.current?.focus()}
      >
        {/* Blinking cursor when focused and empty - positioned at start */}
        {isFocused && !value && (
          <motion.span
            className="absolute left-0 top-0 w-[2px] h-[50px] bg-white/80"
            animate={{ opacity: [1, 0] }}
            transition={{ duration: 0.8, repeat: Infinity, repeatType: 'reverse' }}
          />
        )}

        <input
          ref={inputRef}
          type="text"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onFocus={() => setIsFocused(true)}
          onBlur={() => setIsFocused(false)}
          onKeyDown={handleKeyDown}
          className="bg-transparent border-none outline-none text-[50px] font-light tracking-[-0.04em] leading-[100%] text-white/80 placeholder-transparent w-full"
          style={{ caretColor: 'transparent' }}
        />
      </div>

      {/* Placeholder text */}
      {showPlaceholder && (
        <span
          className="absolute left-0 top-0 text-[50px] font-light tracking-[-0.04em] leading-[100%] text-white/40 pointer-events-none"
          onClick={() => inputRef.current?.focus()}
        >
          {placeholder}
        </span>
      )}

      {/* Underline */}
      <div className="relative mt-1">
        <div className="h-[1px] bg-white/20 w-full" />
        <motion.div
          className="absolute top-0 left-0 h-[1px] bg-white/60"
          initial={{ width: 0 }}
          animate={{ width: isFocused || value ? '100%' : 0 }}
          transition={{ duration: 0.2 }}
        />
      </div>
    </div>
  );
}

export default MinimalTextInput;
