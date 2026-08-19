/**
 * Debug trace badges component for companion simulator.
 * Shows context engine trace events during streaming and as an expandable summary after completion.
 */

'use client';

import { useState, useEffect, useMemo, useRef } from 'react';
import { cn } from '@/lib/utils';
import Icon from '@/components/ui/icon';
import type { TraceEvent } from '@/hooks/useRelationshipChat';

// Layer color scheme matching context-engine-testing
const LAYER_COLORS: Record<string, { base: string; highlight: string }> = {
  memory: { base: '#4D4318', highlight: '#E5CA59' },
  memory_v2: { base: '#4D4318', highlight: '#E5CA59' },  // Alias for v2 memory layer
  knowledge: { base: '#243D4D', highlight: '#64ABDE' },
  knowledge_base: { base: '#243D4D', highlight: '#64ABDE' },
  tools: { base: '#3D1653', highlight: '#BC54F8' },
  actions: { base: '#334130', highlight: '#85CD75' },
  classifier: { base: '#4D1820', highlight: '#E54D5B' },
  behaviors: { base: '#334130', highlight: '#85CD75' },
  profile: { base: '#4D3D4D', highlight: '#D4A5D4' },
  context: { base: '#353535', highlight: '#C0C0C0' },
};

interface EventBadgeProps {
  event: TraceEvent;
  showTimestamp?: boolean;
}

function EventBadge({ event, showTimestamp = false }: EventBadgeProps) {
  const isGatedEvent = event.name.includes(':gated');
  const displayPhase = isGatedEvent ? 'skipped' : event.phase;

  // Determine layer for inline style
  const layerName = event.name.split(':')[0];
  const layerColors = LAYER_COLORS[layerName];
  const isError = event.phase === 'error';

  return (
    <div
      className={cn(
        'inline-flex items-center gap-1.5 rounded px-2 py-0.5 text-xs',
        isError && 'bg-red-900/50 text-red-400'
      )}
      style={
        !isError
          ? {
              backgroundColor: layerColors?.base || '#353535',
              color: layerColors?.highlight || '#C0C0C0',
            }
          : undefined
      }
    >
      <span className="font-medium">{event.name}</span>
      <span className="opacity-60">{displayPhase}</span>
      {showTimestamp && (
        <span className="opacity-40 text-[10px]">{event.ts_ms}ms</span>
      )}
    </div>
  );
}

interface DebugTraceBadgesProps {
  traceEvents: TraceEvent[];
  isStreaming: boolean;
  className?: string;
}

export function DebugTraceBadges({
  traceEvents,
  isStreaming,
  className,
}: DebugTraceBadgesProps) {
  const [isExpanded, setIsExpanded] = useState(false);
  const [activeEvents, setActiveEvents] = useState<TraceEvent[]>([]);
  const [showBelow, setShowBelow] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);

  // Close expanded view when clicking outside
  useEffect(() => {
    if (!isExpanded) return;

    const handleClickOutside = (event: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        setIsExpanded(false);
      }
    };

    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [isExpanded]);

  // Calculate position when expanding - show below if not enough space above
  useEffect(() => {
    if (!isExpanded || !buttonRef.current) return;

    const buttonRect = buttonRef.current.getBoundingClientRect();
    const panelHeight = 300; // Approximate max height of expanded panel
    const spaceAbove = buttonRect.top;

    // If not enough space above, show below
    setShowBelow(spaceAbove < panelHeight);
  }, [isExpanded]);

  // Track active (in-progress) events during streaming
  useEffect(() => {
    if (!isStreaming) {
      setActiveEvents([]);
      return;
    }

    // Find events that have started but not ended
    const startEvents = new Map<string, TraceEvent>();
    const endedEvents = new Set<string>();

    for (const event of traceEvents) {
      // Extract base event name (without :start/:end suffix if present in name)
      const baseName = event.name.replace(/:start$|:end$/, '');

      if (event.phase === 'start') {
        startEvents.set(baseName, event);
      } else if (event.phase === 'end') {
        endedEvents.add(baseName);
      }
    }

    // Active events are those that started but haven't ended
    const active: TraceEvent[] = [];
    startEvents.forEach((event, name) => {
      if (!endedEvents.has(name)) {
        active.push(event);
      }
    });

    setActiveEvents(active);
  }, [traceEvents, isStreaming]);

  // Collapse when streaming ends
  useEffect(() => {
    if (!isStreaming) {
      setIsExpanded(false);
    }
  }, [isStreaming]);

  // Group events by layer for summary
  const eventsByLayer = useMemo(() => {
    const grouped: Record<string, TraceEvent[]> = {};
    for (const event of traceEvents) {
      const layer = event.name.split(':')[0];
      if (!grouped[layer]) {
        grouped[layer] = [];
      }
      grouped[layer].push(event);
    }
    return grouped;
  }, [traceEvents]);

  // If no events, don't render anything
  if (traceEvents.length === 0 && !isStreaming) {
    return null;
  }

  // During streaming: show active events as fleeting badges
  if (isStreaming) {
    return (
      <div className={cn('flex flex-wrap gap-1.5', className)}>
        {activeEvents.map((event) => (
          <div
            key={event.id}
            className="animate-pulse"
          >
            <EventBadge event={event} />
          </div>
        ))}
        {activeEvents.length === 0 && traceEvents.length > 0 && (
          <div className="text-xs text-white/40">Processing...</div>
        )}
      </div>
    );
  }

  // After streaming: show collapsed summary with expand option
  return (
    <div ref={containerRef} className={cn('relative', className)}>
      {/* Collapsed summary button */}
      <button
        ref={buttonRef}
        onClick={() => setIsExpanded(!isExpanded)}
        className="inline-flex items-center gap-1.5 rounded-md bg-[#2a2a2a] px-2.5 py-1 text-xs text-white/60 hover:bg-[#333] hover:text-white/80 transition-colors w-fit"
      >
        <span>{traceEvents.length} trace events</span>
        <Icon
          name={isExpanded ? (showBelow ? 'chevron-up' : 'chevron-down') : (showBelow ? 'chevron-down' : 'chevron-up')}
          size={12}
          color="currentColor"
        />
      </button>

      {/* Expanded view - positioned as overlay above or below the button based on available space */}
      {isExpanded && (
        <div
          className={cn(
            'absolute left-0 z-50 w-[400px] max-w-[90vw] bg-[#1a1a1a] border border-white/10 p-3 space-y-3 shadow-xl',
            showBelow ? 'top-full mt-2' : 'bottom-full mb-2'
          )}
        >
          {/* Layer summary */}
          <div className="flex flex-wrap gap-2">
            {Object.entries(eventsByLayer).map(([layer, events]) => {
              const colors = LAYER_COLORS[layer] || { base: '#353535', highlight: '#C0C0C0' };
              const endEvents = events.filter(e => e.phase === 'end');
              const hasError = events.some(e => e.phase === 'error');

              return (
                <div
                  key={layer}
                  className={cn(
                    'inline-flex items-center gap-1 rounded px-2 py-1 text-xs',
                    hasError && 'ring-1 ring-red-500/50'
                  )}
                  style={{
                    backgroundColor: colors.base,
                    color: colors.highlight,
                  }}
                >
                  <span className="font-medium capitalize">{layer}</span>
                  <span className="opacity-60">({endEvents.length})</span>
                </div>
              );
            })}
          </div>

          {/* Full event timeline */}
          <div className="border-t border-white/10 pt-3">
            <div className="text-[10px] text-white/40 uppercase tracking-wider mb-2">
              Timeline
            </div>
            <div className="flex flex-wrap gap-1.5 max-h-[200px] overflow-y-auto">
              {traceEvents.map((event) => (
                <EventBadge key={event.id} event={event} showTimestamp />
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
