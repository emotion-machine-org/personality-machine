'use client';

import { FormEvent, ReactNode, RefObject } from 'react';
import Icon from '@/components/ui/icon';

interface ChatInputProps {
  value: string;
  placeholder?: string;
  disabled?: boolean;
  onChange: (value: string) => void;
  onSubmit: (event: FormEvent) => void;
  leadingSlot?: ReactNode;
  trailingSlot?: ReactNode;
  sendLabel?: string;
  sending?: boolean;
  className?: string;
  inputWrapperClassName?: string;
  sendButton?: (ctx: { disabled: boolean; sending: boolean }) => ReactNode;
  inputRef?: RefObject<HTMLInputElement | null>;
}

export function ChatInput({
  value,
  placeholder,
  disabled,
  onChange,
  onSubmit,
  leadingSlot,
  trailingSlot,
  sendLabel = 'Send',
  sending,
  className = '',
  inputWrapperClassName = 'rounded-full bg-[color:var(--color-gray-button)] px-4 py-3',
  sendButton,
  inputRef,
}: ChatInputProps) {
  const isSendDisabled = disabled || !value.trim();

  const defaultSendButton = (
    <button
      type="submit"
      disabled={isSendDisabled}
      className={`flex h-12 w-12 items-center justify-center rounded-full transition ${
        isSendDisabled
          ? 'bg-[#3C3C3C] text-white/30 cursor-not-allowed'
          : 'bg-white text-black hover:bg-white/90'
      }`}
      aria-label={sendLabel}
    >
      <Icon name="arrow-up" size={18} color="currentColor" />
    </button>
  );

  const renderedSendButton = sendButton ? sendButton({ disabled: isSendDisabled, sending: Boolean(sending) }) : defaultSendButton;

  return (
    <form onSubmit={onSubmit} className={`flex items-center gap-3 ${className}`}>
      {leadingSlot}
      <div className={`relative flex-1 ${inputWrapperClassName}`}>
        <input
          type="text"
          value={value}
          onChange={(event) => onChange(event.target.value)}
          placeholder={placeholder}
          disabled={disabled}
          className="w-full bg-transparent text-white placeholder:text-white/40 focus:outline-none disabled:opacity-50"
          ref={inputRef}
        />
      </div>
      {renderedSendButton}
      {trailingSlot}
    </form>
  );
}
