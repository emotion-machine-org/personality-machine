'use client';

import { motion } from 'framer-motion';

export interface SelectOption {
  value: string;
  label: string;
  description?: string;
}

export interface SelectOptionsProps {
  options: SelectOption[];
  value: string | null;
  onChange: (value: string) => void;
}

export function SelectOptions({
  options,
  value,
  onChange,
}: SelectOptionsProps) {
  return (
    <div className="flex flex-col gap-2">
      {options.map((option) => {
        const isSelected = value === option.value;
        return (
          <motion.button
            key={option.value}
            type="button"
            onClick={() => onChange(option.value)}
            className={`
              text-left text-[50px] font-light tracking-[-0.04em] leading-[100%] transition-colors
              ${isSelected
                ? 'text-white'
                : 'text-white/80 hover:text-white'
              }
            `}
            whileHover={{ x: 4 }}
            transition={{ duration: 0.15 }}
          >
            {option.label}
          </motion.button>
        );
      })}
    </div>
  );
}

export default SelectOptions;
