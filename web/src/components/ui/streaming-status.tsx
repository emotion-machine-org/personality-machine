/**
 * StreamingStatus - Displays status events during WebSocket streaming
 *
 * Shows stages like "thinking", "retrieving", "ruminating" as they happen
 * during message generation.
 */

import Icon from '@/components/ui/icon';
import type { StatusEvent } from '@/hooks/useRelationshipChat';

type IconName = 'brain' | 'data-transfer-both' | 'database' | 'search';

interface StreamingStatusProps {
  events: StatusEvent[];
  isStreaming: boolean;
  className?: string;
}

// Map stage names to display info
const STAGE_CONFIG: Record<string, { icon: IconName; activeLabel: string; doneLabel: string }> = {
  thinking: {
    icon: 'brain',
    activeLabel: 'thinking…',
    doneLabel: 'thought',
  },
  retrieving: {
    icon: 'data-transfer-both',
    activeLabel: 'retrieving…',
    doneLabel: 'retrieved',
  },
  ruminating: {
    icon: 'brain',
    activeLabel: 'ruminating…',
    doneLabel: 'ruminated',
  },
  memory_stored: {
    icon: 'database',
    activeLabel: 'storing memory…',
    doneLabel: 'memory stored',
  },
  searching: {
    icon: 'search',
    activeLabel: 'searching…',
    doneLabel: 'searched',
  },
};

function getStageConfig(stage: string) {
  return STAGE_CONFIG[stage] || {
    icon: 'brain' as IconName,
    activeLabel: `${stage}…`,
    doneLabel: stage,
  };
}

function formatLabel(event: StatusEvent): string {
  const config = getStageConfig(event.stage);

  if (event.phase === 'start') {
    return config.activeLabel;
  }

  // For 'end' phase, include metadata if available
  if (event.stage === 'retrieving' && event.meta) {
    const items = typeof event.meta.retrieval_items === 'number' ? ` ${event.meta.retrieval_items}` : '';
    const ms = typeof event.meta.retrieval_ms === 'number' ? ` in ${Math.round(event.meta.retrieval_ms)}ms` : '';
    return `retrieved${items}${ms}`.trim();
  }

  if (event.stage === 'memory_stored' && event.meta?.content) {
    const content = String(event.meta.content);
    const trimmed = content.length > 40 ? content.slice(0, 37) + '...' : content;
    return `stored: "${trimmed}"`;
  }

  return config.doneLabel;
}

/**
 * Get the current active status to display
 * Shows the most recent "start" phase event that hasn't ended yet
 */
function getCurrentStatus(events: StatusEvent[]): StatusEvent | null {
  // Track which stages have ended
  const endedStages = new Set<string>();

  // Process events in reverse to find ended stages
  for (let i = events.length - 1; i >= 0; i--) {
    const event = events[i];
    if (event.phase === 'end') {
      endedStages.add(event.stage);
    }
  }

  // Find the most recent "start" event that hasn't ended
  for (let i = events.length - 1; i >= 0; i--) {
    const event = events[i];
    if (event.phase === 'start' && !endedStages.has(event.stage)) {
      return event;
    }
  }

  return null;
}

export function StreamingStatus({ events, isStreaming, className = '' }: StreamingStatusProps) {
  // Get the current active status
  const currentStatus = getCurrentStatus(events);

  // If not streaming or no active status, don't render
  if (!isStreaming && !currentStatus) {
    return null;
  }

  // If streaming but no status yet, show default "preparing"
  if (isStreaming && !currentStatus) {
    return (
      <div className={`inline-flex items-center gap-1.5 rounded-md bg-white/5 px-2.5 py-1 text-sm text-white/70 ${className}`}>
        <Icon name="brain" size={14} className="opacity-70 animate-pulse" />
        <span>preparing…</span>
      </div>
    );
  }

  if (!currentStatus) return null;

  const config = getStageConfig(currentStatus.stage);
  const label = formatLabel(currentStatus);

  return (
    <div className={`inline-flex items-center gap-1.5 rounded-md bg-white/5 px-2.5 py-1 text-sm text-white/70 ${className}`}>
      <Icon name={config.icon} size={14} className="opacity-70 animate-pulse" />
      <span>{label}</span>
    </div>
  );
}

/**
 * StreamingStatusList - Shows all status events (for debug/detailed view)
 */
interface StreamingStatusListProps {
  events: StatusEvent[];
  isStreaming: boolean;
  className?: string;
}

export function StreamingStatusList({ events, isStreaming, className = '' }: StreamingStatusListProps) {
  if (events.length === 0 && !isStreaming) {
    return null;
  }

  // Filter to show only meaningful events
  const visibleEvents = events.filter(e => {
    // Show all 'start' events when streaming
    if (isStreaming && e.phase === 'start') return true;
    // Show persistent 'end' events (like memory_stored)
    if (e.phase === 'end' && e.stage === 'memory_stored') return true;
    return false;
  });

  if (visibleEvents.length === 0 && isStreaming) {
    return (
      <div className={`inline-flex items-center gap-1.5 rounded-md bg-white/5 px-2.5 py-1 text-sm text-white/70 ${className}`}>
        <Icon name="brain" size={14} className="opacity-70 animate-pulse" />
        <span>preparing…</span>
      </div>
    );
  }

  if (visibleEvents.length === 0) {
    return null;
  }

  return (
    <div className={`flex flex-col gap-1 ${className}`}>
      {visibleEvents.map((event) => {
        const config = getStageConfig(event.stage);
        const label = formatLabel(event);
        const isActive = event.phase === 'start';

        return (
          <div
            key={event.id}
            className="inline-flex items-center gap-1.5 rounded-md bg-white/5 px-2.5 py-1 text-sm text-white/70"
          >
            <Icon
              name={config.icon}
              size={14}
              className={`opacity-70 ${isActive ? 'animate-pulse' : ''}`}
            />
            <span>{label}</span>
          </div>
        );
      })}
    </div>
  );
}
