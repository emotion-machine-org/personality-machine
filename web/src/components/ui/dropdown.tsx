'use client';

import * as React from 'react';
import * as Select from '@radix-ui/react-select';
import Icon from './icon';
import { cn } from '@/lib/utils';

export interface DropdownOption {
  value: string;
  label: string;
  description?: string;
}

interface DropdownProps {
  /** Options can be strings or objects with value/label/description */
  options: string[] | DropdownOption[];
  value?: string;
  onChange?: (value: string) => void;
  placeholder?: string;
  className?: string;
  disabled?: boolean;
  /** Size variant - default is 'md' */
  size?: 'sm' | 'md';
}

function normalizeOptions(options: string[] | DropdownOption[]): DropdownOption[] {
  return options.map(opt =>
    typeof opt === 'string'
      ? { value: opt, label: opt }
      : opt
  );
}

export default function Dropdown({
  options,
  value,
  onChange,
  placeholder = "Select option",
  className,
  disabled = false,
  size = 'md',
}: DropdownProps) {
  const normalizedOptions = normalizeOptions(options);

  // Find selected option label for display
  const selectedOption = normalizedOptions.find(opt => opt.value === value);

  const sizeClasses = {
    sm: 'px-2 py-1.5 text-sm',
    md: 'pl-3 pr-8 py-2 text-sm',
  };

  const itemSizeClasses = {
    sm: 'px-2 py-1.5 text-sm',
    md: 'px-3 py-2 text-sm',
  };

  return (
    <Select.Root value={value} onValueChange={onChange} disabled={disabled}>
      <Select.Trigger
        className={cn(
          "relative w-full bg-[var(--color-panel-bg)] text-white font-book leading-tight border border-white/20",
          sizeClasses[size],
          "hover:bg-gray-dark transition-colors",
          "focus:outline-none focus:border-white/40",
          "flex items-center text-left",
          "data-[placeholder]:text-[var(--color-placeholder)]",
          "disabled:opacity-50 disabled:cursor-not-allowed",
          className
        )}
      >
        <span className="flex-1 truncate">
          {selectedOption ? (
            <>
              {selectedOption.label}
              {selectedOption.description && (
                <span className="text-white/50"> · {selectedOption.description}</span>
              )}
            </>
          ) : (
            <span className="text-[var(--color-placeholder)]">{placeholder}</span>
          )}
        </span>
        <Select.Icon className="pointer-events-none absolute inset-y-0 right-2 flex items-center">
          <Icon name="chevron-down" size={14} color="rgba(255, 255, 255, 0.6)" />
        </Select.Icon>
      </Select.Trigger>

      <Select.Portal>
        <Select.Content
          position="popper"
          side="bottom"
          sideOffset={1}
          avoidCollisions
          align="start"
          className={cn(
            "bg-[var(--color-panel-bg)] border border-white/20 shadow-lg z-50 overflow-hidden",
            "data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95",
            "data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=closed]:zoom-out-95",
          )}
          style={{
            width: 'var(--radix-select-trigger-width)',
            maxHeight: 'var(--radix-select-content-available-height)',
          }}
        >
          <Select.Viewport className="max-h-60 overflow-y-auto py-1">
            {normalizedOptions.map(option => (
              <Select.Item
                key={option.value}
                value={option.value}
                className={cn(
                  "w-full text-left text-white/80 outline-none cursor-pointer transition-colors",
                  "hover:text-white hover:bg-white/10 focus:bg-white/10 focus:text-white",
                  "data-[highlighted]:bg-white/10 data-[highlighted]:text-white",
                  itemSizeClasses[size],
                )}
              >
                <Select.ItemText>
                  {option.label}
                  {option.description && (
                    <span className="text-white/50"> · {option.description}</span>
                  )}
                </Select.ItemText>
              </Select.Item>
            ))}
          </Select.Viewport>
        </Select.Content>
      </Select.Portal>
    </Select.Root>
  );
}
