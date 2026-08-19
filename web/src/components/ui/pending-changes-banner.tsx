'use client';

import Icon from './icon';

interface PendingChangesBannerProps {
  message?: string;
  onDismiss?: () => void;
}

export default function PendingChangesBanner({
  message = "Changes will apply to your next conversation",
  onDismiss
}: PendingChangesBannerProps) {
  return (
    <div className="bg-yellow-bg p-3 mb-4 flex items-center justify-between">
      <div className="flex items-center gap-2">
        <Icon name="info" className="w-4 h-4 -mt-0.5 text-orange-400 flex-shrink-0" />
        <span className="font-book text-sm text-orange-200">
          {message}
        </span>
      </div>
      {onDismiss && (
        <button
          onClick={onDismiss}
          className="text-yellow-solid hover:text-yellow-solid-hover transition-colors"
          aria-label="Dismiss banner"
        >
          <Icon name="x" className="w-4 h-4" />
        </button>
      )}
    </div>
  );
}
