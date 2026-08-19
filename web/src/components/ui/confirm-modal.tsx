"use client";

import { cn } from "@/lib/utils";

interface ConfirmModalProps {
  open: boolean;
  title?: string;
  message?: string;
  confirmText?: string;
  cancelText?: string;
  destructive?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

export default function ConfirmModal({
  open,
  title = "Are you sure?",
  message = "This action cannot be undone.",
  confirmText = "Delete",
  cancelText = "Cancel",
  destructive = false,
  onConfirm,
  onCancel,
}: ConfirmModalProps) {
  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      {/* Backdrop */}
      <button
        aria-label="Close"
        className="absolute inset-0 bg-black/60 backdrop-blur-[2px]"
        onClick={onCancel}
      />

      {/* Dialog */}
      <div className="relative z-10 w-[400px] max-w-[92vw] bg-[color:var(--color-gray-darker)] p-8 shadow-xl">
        <div className="space-y-3">
          <h2 className="text-[17px] font-light text-white/70">{title}</h2>
          <p className="text-[20px] font-light text-white leading-relaxed">{message}</p>
        </div>

        <div className="mt-7 flex items-center justify-end">
          <button
            onClick={onCancel}
            className="pl-3 text-[20px] font-light text-white/40 hover:text-white transition-colors"
          >
            {cancelText}
          </button>
          <button
            onClick={onConfirm}
            className={cn(
              "pl-3 text-[20px] font-light transition-colors",
              destructive
                ? "text-[color:var(--color-brand-solid)]/80 hover:text-[#FF4D6A]"
                : "text-white/60 hover:text-white"
            )}
          >
            {confirmText}
          </button>
        </div>
      </div>
    </div>
  );
}
