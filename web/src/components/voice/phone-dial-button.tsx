'use client';

import { motion } from 'framer-motion';
import Icon from '@/components/ui/icon';
import Tooltip from '@/components/ui/tooltip';
import { cn } from '@/lib/utils';

export type TwilioCallStatus = 'idle' | 'dialing' | 'ringing' | 'connected' | 'ended' | 'error';

export interface PhoneDialButtonProps {
  onClick: () => void;
  onEndCall?: () => void;
  status?: TwilioCallStatus;
  disabled?: boolean;
  className?: string;
}

const statusConfig: Record<TwilioCallStatus, { tooltip: string; color: string; bgColor: string }> = {
  idle: {
    tooltip: 'Call via phone',
    color: 'text-white/70',
    bgColor: 'bg-white/10 hover:bg-white/20',
  },
  dialing: {
    tooltip: 'Dialing...',
    color: 'text-amber-400',
    bgColor: 'bg-amber-500/20',
  },
  ringing: {
    tooltip: 'Ringing...',
    color: 'text-amber-400',
    bgColor: 'bg-amber-500/20',
  },
  connected: {
    tooltip: 'Click to end call',
    color: 'text-green-400',
    bgColor: 'bg-green-500/20 hover:bg-red-500/20',
  },
  ended: {
    tooltip: 'Call ended',
    color: 'text-white/50',
    bgColor: 'bg-white/5',
  },
  error: {
    tooltip: 'Call failed - click to retry',
    color: 'text-red-400',
    bgColor: 'bg-red-500/20',
  },
};

export function PhoneDialButton({
  onClick,
  onEndCall,
  status = 'idle',
  disabled = false,
  className,
}: PhoneDialButtonProps) {
  const config = statusConfig[status];
  const isDialingOrRinging = status === 'dialing' || status === 'ringing';
  const isConnected = status === 'connected';

  const handleClick = () => {
    if (isConnected && onEndCall) {
      onEndCall();
    } else if (!isDialingOrRinging) {
      onClick();
    }
  };

  return (
    <Tooltip content={config.tooltip}>
      <motion.button
        onClick={handleClick}
        disabled={disabled || isDialingOrRinging}
        className={cn(
          'flex h-12 w-12 items-center justify-center rounded-full transition-colors',
          config.bgColor,
          config.color,
          disabled && 'opacity-50 cursor-not-allowed',
          isConnected && 'hover:text-red-400',
          className
        )}
        whileHover={!disabled && !isDialingOrRinging ? { scale: 1.05 } : undefined}
        whileTap={!disabled && !isDialingOrRinging ? { scale: 0.95 } : undefined}
        animate={
          isDialingOrRinging
            ? { scale: [1, 1.05, 1] }
            : undefined
        }
        transition={
          isDialingOrRinging
            ? { duration: 1, repeat: Infinity }
            : { duration: 0.15 }
        }
      >
        <Icon
          name={isConnected ? 'phone-off' : status === 'error' ? 'phone-off' : 'phone'}
          size={18}
          color="currentColor"
        />
      </motion.button>
    </Tooltip>
  );
}
