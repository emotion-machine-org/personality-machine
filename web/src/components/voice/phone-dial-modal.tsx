'use client';

import { useState, useCallback, useRef, useEffect } from 'react';
import { cn } from '@/lib/utils';

interface PhoneDialModalProps {
  open: boolean;
  onCall: (phoneNumber: string) => void;
  onCancel: () => void;
  isDialing?: boolean;
  error?: string | null;
}

/**
 * Formats a phone number (without country code) as the user types.
 */
function formatLocalNumber(value: string): string {
  // Remove all non-digit characters
  const digits = value.replace(/\D/g, '');

  if (!digits) return '';

  // Format as (XXX) XXX-XXXX for numbers with 10+ digits
  if (digits.length <= 3) return digits;
  if (digits.length <= 6) return `(${digits.slice(0, 3)}) ${digits.slice(3)}`;
  return `(${digits.slice(0, 3)}) ${digits.slice(3, 6)}-${digits.slice(6, 10)}`;
}

/**
 * Converts country code + local number to E.164 format.
 */
function toE164(countryCode: string, localNumber: string): string {
  const codeDigits = countryCode.replace(/\D/g, '');
  const numberDigits = localNumber.replace(/\D/g, '');

  return `+${codeDigits}${numberDigits}`;
}

/**
 * Validates E.164 phone number format.
 */
function isValidE164(value: string): boolean {
  const pattern = /^\+[1-9]\d{6,14}$/;
  return pattern.test(value);
}

export function PhoneDialModal({
  open,
  onCall,
  onCancel,
  isDialing = false,
  error = null,
}: PhoneDialModalProps) {
  const [countryCode, setCountryCode] = useState('1');
  const [localNumber, setLocalNumber] = useState('');
  const [validationError, setValidationError] = useState<string | null>(null);
  const numberInputRef = useRef<HTMLInputElement>(null);

  // Focus the number input when modal opens
  useEffect(() => {
    if (open && numberInputRef.current) {
      // Small delay to ensure modal is rendered
      setTimeout(() => numberInputRef.current?.focus(), 50);
    }
  }, [open]);

  const handleCountryCodeChange = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    // Only allow digits, max 4 characters
    const value = e.target.value.replace(/\D/g, '').slice(0, 4);
    setCountryCode(value);
    setValidationError(null);
  }, []);

  const handleLocalNumberChange = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const formatted = formatLocalNumber(e.target.value);
    setLocalNumber(formatted);
    setValidationError(null);
  }, []);

  const handleCall = useCallback(() => {
    if (!countryCode) {
      setValidationError('Please enter a country code');
      return;
    }

    if (!localNumber) {
      setValidationError('Please enter a phone number');
      return;
    }

    const e164 = toE164(countryCode, localNumber);

    if (!isValidE164(e164)) {
      setValidationError('Please enter a valid phone number');
      return;
    }

    onCall(e164);
  }, [countryCode, localNumber, onCall]);

  const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !isDialing) {
      handleCall();
    } else if (e.key === 'Escape') {
      onCancel();
    }
  }, [handleCall, isDialing, onCancel]);

  const handleClose = useCallback(() => {
    setLocalNumber('');
    setValidationError(null);
    onCancel();
  }, [onCancel]);

  if (!open) return null;

  const displayError = validationError || error;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      {/* Backdrop */}
      <button
        aria-label="Close"
        className="absolute inset-0 bg-black/60 backdrop-blur-[2px]"
        onClick={handleClose}
        disabled={isDialing}
      />

      {/* Dialog */}
      <div className="relative z-10 w-[420px] max-w-[92vw] bg-[color:var(--color-gray-darker)] p-8 shadow-xl">
        <div className="space-y-4">
          <h2 className="text-[17px] font-light text-white/70">Call via Twilio</h2>
          <p className="text-[15px] font-light text-white/50">
            The companion will speak with the person on the phone.
          </p>

          {/* Phone input - two fields */}
          <div className="space-y-3">
            <div className="flex gap-3">
              {/* Country code */}
              <div className="w-24">
                <label className="block text-xs text-white/40 mb-1.5">Country</label>
                <div className="relative">
                  <span className="absolute left-3 top-1/2 -translate-y-1/2 text-white/50 text-lg">+</span>
                  <input
                    type="tel"
                    placeholder="1"
                    value={countryCode}
                    onChange={handleCountryCodeChange}
                    onKeyDown={handleKeyDown}
                    disabled={isDialing}
                    className={cn(
                      'w-full bg-black/30 border rounded-lg pl-7 pr-3 py-3 text-white text-lg font-light',
                      'placeholder:text-white/30 focus:outline-none focus:ring-2 focus:ring-[#FF5372]/50',
                      displayError ? 'border-red-500/50' : 'border-white/10',
                      isDialing && 'opacity-50 cursor-not-allowed'
                    )}
                  />
                </div>
              </div>

              {/* Phone number */}
              <div className="flex-1">
                <label className="block text-xs text-white/40 mb-1.5">Phone number</label>
                <input
                  ref={numberInputRef}
                  type="tel"
                  placeholder="(555) 123-4567"
                  value={localNumber}
                  onChange={handleLocalNumberChange}
                  onKeyDown={handleKeyDown}
                  disabled={isDialing}
                  className={cn(
                    'w-full bg-black/30 border rounded-lg px-4 py-3 text-white text-lg font-light',
                    'placeholder:text-white/30 focus:outline-none focus:ring-2 focus:ring-[#FF5372]/50',
                    displayError ? 'border-red-500/50' : 'border-white/10',
                    isDialing && 'opacity-50 cursor-not-allowed'
                  )}
                />
              </div>
            </div>

            {displayError && (
              <p className="text-red-400 text-sm font-light">{displayError}</p>
            )}
          </div>
        </div>

        <div className="mt-7 flex items-center justify-end gap-1">
          <button
            onClick={handleClose}
            disabled={isDialing}
            className="px-4 py-2 text-[16px] font-light text-white/40 hover:text-white transition-colors disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            onClick={handleCall}
            disabled={isDialing || !localNumber || !countryCode}
            className={cn(
              'px-4 py-2 text-[16px] font-light transition-colors',
              isDialing
                ? 'text-amber-400'
                : 'text-[#FF5372] hover:text-[#FF4D6A]',
              (!localNumber || !countryCode || isDialing) && 'opacity-50 cursor-not-allowed'
            )}
          >
            {isDialing ? 'Calling...' : 'Call'}
          </button>
        </div>
      </div>
    </div>
  );
}
