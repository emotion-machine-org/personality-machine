import Icon from '@/components/ui/icon';

export type EventBadgeType = 'retrieving' | 'thinking' | 'ruminating' | 'memory_stored';

export interface EventBadgeData {
  id?: string;
  stage: EventBadgeType;
  phase: 'start' | 'end';
  meta?: Record<string, unknown> | null;
  at: number;
  persistent?: boolean; // If true, badge stays visible after phase ends
}

interface EventBadgeProps {
  event: EventBadgeData;
  className?: string;
}

/**
 * Format the label text for an event badge based on stage and phase
 */
export function formatEventLabel(event: EventBadgeData): string {
  if (event.stage === 'retrieving') {
    if (event.phase === 'start') return 'retrieving…';
    const items = typeof event.meta?.retrieval_items === 'number' ? ` ${event.meta.retrieval_items}` : '';
    const millis = typeof event.meta?.retrieval_ms === 'number' ? ` in ${Math.round(event.meta.retrieval_ms)}ms` : '';
    return `retrieved${items}${millis}`.trim();
  }
  if (event.stage === 'thinking') {
    return event.phase === 'start' ? 'thinking…' : 'thought';
  }
  if (event.stage === 'ruminating') {
    return event.phase === 'start' ? 'ruminating…' : 'ruminated';
  }
  if (event.stage === 'memory_stored') {
    // Memory stored badge shows the stored content (trimmed)
    const content = event.meta?.content;
    if (typeof content === 'string' && content.length > 0) {
      // Trim to first 50 chars for display
      const trimmed = content.length > 50 ? content.slice(0, 47) + '...' : content;
      return `Memory stored: "${trimmed}"`;
    }
    return 'Memory stored';
  }
  return 'processing…';
}

/**
 * Get the appropriate icon name for an event stage
 */
export function getEventIcon(stage: EventBadgeType): 'data-transfer-both' | 'brain' | 'database' {
  switch (stage) {
    case 'retrieving':
      return 'data-transfer-both';
    case 'thinking':
    case 'ruminating':
      return 'brain';
    case 'memory_stored':
      return 'database';
    default:
      return 'brain';
  }
}

/**
 * EventBadge component - displays a status badge for streaming events
 *
 * @param event - The event data to display
 * @param className - Optional additional CSS classes
 */
export function EventBadge({ event, className = '' }: EventBadgeProps) {
  const icon = getEventIcon(event.stage);
  const label = formatEventLabel(event);

  return (
    <div className={`inline-flex items-center gap-1.5 rounded-[4px] bg-[#1f1f1f] px-2 py-1 text-white/80 ${className}`}>
      <Icon name={icon} size={14} color="white" className="opacity-80" />
      <span className="font-light translate-y-[1px]">{label}</span>
    </div>
  );
}

/**
 * Filter events to show only relevant ones based on their persistent flag
 * For fleeting events, only show "start" phase events
 * For persistent events, show "end" phase events (when they're finalized)
 */
export function filterVisibleEvents(events: EventBadgeData[], isStreaming: boolean): EventBadgeData[] {
  if (!isStreaming) {
    // When not streaming, only show persistent events
    return events.filter(e => e.persistent && e.phase === 'end');
  }

  // During streaming, show:
  // - Persistent events at "end" phase (finalized)
  // - Fleeting events at "start" phase (active status indicators)
  return events.filter(e => {
    if (e.persistent) {
      return e.phase === 'end'; // Only show finalized persistent badges
    }
    return e.phase === 'start'; // Show active fleeting badges
  });
}

/**
 * EventBadgeList component - renders a list of event badges
 */
interface EventBadgeListProps {
  events: EventBadgeData[];
  isStreaming: boolean;
  className?: string;
  fallbackText?: string; // Text to show when streaming but no events yet
}

export function EventBadgeList({
  events,
  isStreaming,
  className = '',
  fallbackText = 'preparing…'
}: EventBadgeListProps) {
  const visibleEvents = filterVisibleEvents(events, isStreaming);

  // Show fallback when streaming but no visible events
  if (isStreaming && visibleEvents.length === 0) {
    return (
      <div className={`inline-flex items-center gap-1.5 rounded-[4px] bg-[#1f1f1f] px-2 py-1 text-white/80 ${className}`}>
        <Icon name="brain" size={14} color="white" className="opacity-80" />
        <span className="font-light translate-y-[1px]">{fallbackText}</span>
      </div>
    );
  }

  // Don't render anything if not streaming and no persistent events
  if (visibleEvents.length === 0) {
    return null;
  }

  return (
    <div className={`flex flex-col gap-2 ${className}`}>
      {visibleEvents.map((event, idx) => (
        <EventBadge
          key={`${event.id || event.stage}-${event.phase}-${event.at}-${idx}`}
          event={event}
        />
      ))}
    </div>
  );
}
