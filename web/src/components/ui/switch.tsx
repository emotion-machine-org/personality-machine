'use client';

import * as React from 'react';
import * as Switch from '@radix-ui/react-switch';
import { cn } from '@/lib/utils';

interface SwitchProps {
  checked?: boolean;
  onCheckedChange?: (checked: boolean) => void;
  label: string;
  className?: string;
  disabled?: boolean;
  // Tailwind text size class for the label, e.g. "text-base" or "text-[12px]"
  labelTextClassName?: string;
  // Tailwind classes to customize the toggle Root (track), e.g. "w-10 h-6"
  toggleButtonClassName?: string;
  // Tailwind classes to customize the toggle Thumb (knob), e.g. "w-5 h-5 data-[state=checked]:translate-x-[16px]"
  toggleThumbClassName?: string;
}

export default function CustomSwitch({ checked = false, onCheckedChange, label, className, disabled = false, labelTextClassName, toggleButtonClassName, toggleThumbClassName }: SwitchProps) {
  return (
    <div className={cn('flex items-center gap-3', className)}>
      <span className={cn('font-book text-white', labelTextClassName ? labelTextClassName : 'text-base')}>{label}</span>
      <Switch.Root
        checked={checked}
        onCheckedChange={onCheckedChange}
        disabled={disabled}
        className={cn(
          'w-12 h-7 rounded-full relative transition-colors bg-white/20 data-[state=checked]:bg-white/40',
          disabled ? 'opacity-40 cursor-not-allowed' : 'cursor-pointer',
          toggleButtonClassName
        )}
      >
        <Switch.Thumb
          className={cn(
            'block w-6 h-6 rounded-full transition-transform duration-200 translate-x-0.5 will-change-transform bg-white/70 data-[state=checked]:translate-x-[22px] data-[state=checked]:bg-white',
            disabled ? 'pointer-events-none' : '',
            toggleThumbClassName
          )}
        />
      </Switch.Root>
    </div>
  );
}
