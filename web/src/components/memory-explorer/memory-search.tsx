'use client';

import { useState, useCallback, useRef, useEffect } from 'react';
import Icon from '@/components/ui/icon';

interface MemorySearchProps {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  debounceMs?: number;
}

export function MemorySearch({
  value,
  onChange,
  placeholder = 'Search in memories',
  debounceMs = 300,
}: MemorySearchProps) {
  const [localValue, setLocalValue] = useState(value);
  const debounceRef = useRef<NodeJS.Timeout | null>(null);

  // Sync local value when external value changes
  useEffect(() => {
    setLocalValue(value);
  }, [value]);

  const handleChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const newValue = e.target.value;
      setLocalValue(newValue);

      // Clear existing timer
      if (debounceRef.current) {
        clearTimeout(debounceRef.current);
      }

      // Set new debounce timer
      debounceRef.current = setTimeout(() => {
        onChange(newValue);
      }, debounceMs);
    },
    [onChange, debounceMs]
  );

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (debounceRef.current) {
        clearTimeout(debounceRef.current);
      }
    };
  }, []);

  return (
    <div className="flex h-[57px] items-center bg-black px-[16px] w-full shrink-0">
      <div className="flex gap-[16px] items-center w-full">
        <Icon name="search" size={20} className="text-white/40 shrink-0" />
        <input
          type="text"
          value={localValue}
          onChange={handleChange}
          placeholder={placeholder}
          className="flex-1 bg-transparent text-white text-[20px] leading-[1.2] font-book placeholder:text-white/40 border-none outline-none"
        />
      </div>
    </div>
  );
}
