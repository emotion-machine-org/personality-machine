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
      <div className="relative z-10 w-[360px] max-w-[92vw] rounded-[4px] border border-white/20 bg-black pt-4 pb-2 px-4 shadow-xl">
        <div className="flex items-start gap-3">
          <div className="flex-1 min-w-0">
            <div className="text-sm font-medium text-white mb-1 truncate">
              {title}
            </div>
            <div className="text-xs text-white/70 leading-relaxed">
              {message}
            </div>
          </div>
        </div>
        <div className="mt-1 flex items-center justify-end gap-2">
          <button
            onClick={onCancel}
            className="px-3 py-1.5 text-sm font-bold text-white/80 hover:text-white transition-colors"
          >
            {cancelText}
          </button>
          <button
            onClick={onConfirm}
            className={cn(
              "px-3 py-1.5 text-sm font-bold transition-colors",
              destructive
                ? "text-[#FF244C]/90 hover:text-[#FF244C]"
                : "text-white/80 hover:text-white",
            )}
          >
            {confirmText}
          </button>
        </div>
      </div>
    </div>
  );
}
