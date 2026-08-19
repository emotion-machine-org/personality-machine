'use client';

import { useState, useCallback, useRef, useMemo } from 'react';
import { useAuth } from '@clerk/nextjs';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Textarea } from '@/components/ui/textarea';
import Icon from '@/components/ui/icon';
import Dropdown from '@/components/ui/dropdown';
import { cn } from '@/lib/utils';
import { API_CONFIG } from '@/lib/config';

const API_BASE = API_CONFIG.BASE_URL;

// Types
interface Companion {
  id: string;
  name: string;
}

interface HistoryMessage {
  role: string;
  content: string;
}

interface ContextEvent {
  name: string;
  phase: 'start' | 'end' | 'info' | 'error';
  meta: Record<string, unknown>;
  ts_ms: number;
}

interface ClassifierResult {
  layers: Record<string, boolean>;  // layer_decisions from backend is layer_name -> should_run
  actions: Array<{ key: string; reason: string }>;
  selected_actions: string[];  // Action keys selected by classifier
  duration_ms?: number;
}


type TriggerValue = string | { type: string; [key: string]: unknown };

interface BehaviorInfo {
  id: string;
  key: string;
  name: string;
  description: string | null;
  source_code: string;
  version: number;
  triggers: TriggerValue[];
  priority: 'sync' | 'async';
  enabled: boolean;
}

function formatTrigger(trigger: TriggerValue): string {
  if (typeof trigger === 'string') return trigger;
  const { type, ...rest } = trigger;
  if (type === 'idle' && 'minutes' in rest) return `idle:${rest.minutes}`;
  if (type === 'cron' && 'expression' in rest) return `cron:${rest.expression}`;
  if (type === 'every_n' && 'n' in rest) return `every_n:${rest.n}`;
  if (type === 'turn_count' && 'turns' in rest) return `turn:${(rest.turns as number[]).join(',')}`;
  if (type === 'keyword' && 'keywords' in rest) return `keyword:${(rest.keywords as string[]).join(',')}`;
  return type;
}

interface LayerExecutionInfo {
  ran: boolean;
  source: string;  // 'classifier' | 'always_run' | 'test_override' | 'triggered' | 'not_requested' | 'skipped_by_test_override'
  classifier_decision: boolean | string[];  // bool for most layers, string[] for actions
  items?: number;
  reason?: string;
  triggered_actions?: string[];
  priority_count?: number;
  async_count?: number;
}

interface ExecutionSummary {
  layers: {
    memory: LayerExecutionInfo;
    knowledge_base: LayerExecutionInfo;
    tools: LayerExecutionInfo;
    actions: LayerExecutionInfo;
  };
  classifier_used: boolean;
  raw_mode: boolean;
}

interface ContextOutput {
  mode: string;
  messages: Array<{ role: string; content: string }>;
  events: ContextEvent[];
  trace: Record<string, unknown>;
  effects: Array<Record<string, unknown>>;
  token_usage: Record<string, unknown>;
  build_ms: number;
  assistant_response?: string;
  llm_ms?: number;
  classifier_result?: ClassifierResult;
  classifier_prompt?: string;
  execution_summary?: ExecutionSummary;
}

interface SSEEvent {
  type: 'mode_start' | 'event' | 'mode_complete' | 'done' | 'error' | 'llm_start' | 'llm_delta' | 'llm_end' | 'llm_error';
  mode?: string;
  output?: ContextOutput;
  name?: string;
  phase?: string;
  meta?: Record<string, unknown>;
  ts_ms?: number;
  detail?: string;
  content?: string;
  error?: string;
  model?: string;
  duration_ms?: number;
  raw_build_ms?: number;
  layered_build_ms?: number;
}

// Saved test types
interface SavedTestConfig {
  user_message: string;
  core_system_prompt?: string | null;
  core_memories?: string[] | null;
  regular_memories?: string[] | null;
  knowledge_results?: string[] | null;
  history?: HistoryMessage[] | null;
  profile_override?: Record<string, unknown> | null;
  include_memory: boolean;
  include_knowledge: boolean;
  include_tools: boolean;
  include_actions: boolean;
  include_profile_in_prompt: boolean;
  use_classifier: boolean;
  classifier_model: string;
  layer_always_run: {
    memory: boolean;
    knowledge: boolean;
    tools: boolean;
    actions: boolean;
  };
  model: string;
  max_output_tokens?: number | null;
}

interface SavedTest {
  id: string;
  companion_id: string;
  name: string;
  config: SavedTestConfig;
  created_at: string;
  updated_at: string;
}

// Test result stored in session (not persisted)
interface TestRunResult {
  testId: string;
  testName: string;
  rawOutput: ContextOutput | null;
  layeredOutput: ContextOutput | null;
  rawEvents: ContextEvent[];
  layeredEvents: ContextEvent[];
  error?: string;
}

// Available models
const MODELS = [
  { value: 'openai-gpt4o-mini', label: 'GPT-4o Mini' },
  { value: 'openai-gpt4o', label: 'GPT-4o' },
  { value: 'claude-sonnet-4', label: 'Claude Sonnet 4' },
  { value: 'gemini-2.5-flash', label: 'Gemini 2.5 Flash' },
];

// Classifier model options
const CLASSIFIER_MODELS = [
  { value: 'gemini-2.0-flash', label: 'Gemini 2.0 Flash' },
  { value: 'gpt-4o-mini', label: 'GPT-4o Mini' },
  { value: 'gpt-5-nano', label: 'GPT-5 Nano' },
  { value: 'gpt-5-mini', label: 'GPT-5 Mini' },
];

const DEFAULT_PROFILE_EXAMPLE = {
  name: 'Alex',
  age: 29,
  phase: 'follicular',
  cycle_day: 7,
  energy_level: 'medium',
};

const DEFAULT_PROFILE_EXAMPLE_JSON = JSON.stringify(DEFAULT_PROFILE_EXAMPLE, null, 2);

// Reusable components
function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <label className="text-white/60 text-xs font-medium uppercase tracking-wide mb-1.5 block">
      {children}
    </label>
  );
}

function ItemList({
  items,
  onChange,
  placeholder,
  addLabel = 'Add item',
}: {
  items: string[];
  onChange: (items: string[]) => void;
  placeholder: string;
  addLabel?: string;
}) {
  const addItem = () => onChange([...items, '']);
  const updateItem = (index: number, value: string) => {
    const updated = [...items];
    updated[index] = value;
    onChange(updated);
  };
  const removeItem = (index: number) => {
    onChange(items.filter((_, i) => i !== index));
  };

  return (
    <div className="space-y-2">
      {items.map((item, index) => (
        <div key={index} className="flex gap-2 items-start">
          <Textarea
            value={item}
            onChange={(e) => updateItem(index, e.target.value)}
            placeholder={placeholder}
            minHeight={48}
            className="flex-1"
          />
          <button
            onClick={() => removeItem(index)}
            className="p-2 text-white/40 hover:text-white/80 transition-colors"
            title="Remove"
          >
            <Icon name="x" size={14} color="currentColor" />
          </button>
        </div>
      ))}
      <button
        onClick={addItem}
        className="text-xs text-white/60 hover:text-white/80 transition-colors flex items-center gap-1"
      >
        <Icon name="plus" size={12} color="currentColor" />
        {addLabel}
      </button>
    </div>
  );
}

function HistoryList({
  history,
  onChange,
}: {
  history: HistoryMessage[];
  onChange: (history: HistoryMessage[]) => void;
}) {
  const addMessage = (role: 'user' | 'assistant') => {
    onChange([...history, { role, content: '' }]);
  };
  const updateMessage = (index: number, content: string) => {
    const updated = [...history];
    updated[index] = { ...updated[index], content };
    onChange(updated);
  };
  const removeMessage = (index: number) => {
    onChange(history.filter((_, i) => i !== index));
  };

  return (
    <div className="space-y-2">
      {history.map((msg, index) => (
        <div key={index} className="flex gap-2 items-start">
          <span
            className={cn(
              'text-xs font-medium px-2 py-1 rounded shrink-0 w-16 text-center',
              msg.role === 'user'
                ? 'bg-blue-bg text-blue-solid'
                : 'bg-green-bg text-green-solid'
            )}
          >
            {msg.role}
          </span>
          <Textarea
            value={msg.content}
            onChange={(e) => updateMessage(index, e.target.value)}
            placeholder={`${msg.role} message...`}
            minHeight={48}
            className="flex-1"
          />
          <button
            onClick={() => removeMessage(index)}
            className="p-2 text-white/40 hover:text-white/80 transition-colors"
            title="Remove"
          >
            <Icon name="x" size={14} color="currentColor" />
          </button>
        </div>
      ))}
      <div className="flex gap-2">
        <button
          onClick={() => addMessage('user')}
          className="text-xs text-blue-solid hover:text-blue-solid-hover transition-colors flex items-center gap-1"
        >
          <Icon name="plus" size={12} color="currentColor" />
          User message
        </button>
        <button
          onClick={() => addMessage('assistant')}
          className="text-xs text-green-solid hover:text-green-solid-hover transition-colors flex items-center gap-1"
        >
          <Icon name="plus" size={12} color="currentColor" />
          Assistant message
        </button>
      </div>
    </div>
  );
}

// Layer color mapping for event badges
const LAYER_COLORS: Record<string, { base: string; highlight: string }> = {
  memory: { base: '#4D4318', highlight: '#E5CA59' },
  knowledge: { base: '#243D4D', highlight: '#64ABDE' },
  knowledge_base: { base: '#243D4D', highlight: '#64ABDE' },  // Alias for layer_decisions key
  tools: { base: '#3D1653', highlight: '#BC54F8' },
  actions: { base: '#334130', highlight: '#85CD75' },
  classifier: { base: '#4D3D18', highlight: '#F5A623' },
};

function EventBadge({ event }: { event: ContextEvent }) {
  const isGatedEvent = event.name.includes(':gated');
  const displayPhase = isGatedEvent ? 'skipped' : event.phase;

  // Determine layer for inline style (Tailwind can't handle dynamic bracket notation)
  const layerName = event.name.split(':')[0];
  const layerColors = LAYER_COLORS[layerName];
  const isError = event.phase === 'error';

  return (
    <div
      className={cn(
        'inline-flex items-center gap-1.5 rounded px-2 py-0.5 text-xs',
        isError && 'bg-brand-bg text-brand-solid'
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
      <span className="opacity-40">{event.ts_ms.toFixed(1)}ms</span>
    </div>
  );
}

function OutputPanel({
  title,
  output,
  events,
  isLoading,
  streamingResponse,
}: {
  title: string;
  output: ContextOutput | null;
  events: ContextEvent[];
  isLoading: boolean;
  streamingResponse: string;
}) {
  const [activeTab, setActiveTab] = useState<'conversation' | 'trace' | 'effects'>(
    'conversation'
  );

  return (
    <div className="flex-1 flex flex-col bg-[var(--color-panel-bg)] overflow-hidden border border-white/10">
      <div className="flex items-center justify-between px-4 py-2.5 border-b border-white/10 bg-[var(--color-input-readonly)]">
        <div className="flex items-center gap-2">
          <h3 className="text-sm font-medium text-white">{title}</h3>
          {output && (
            <span className="text-xs text-white/40">
              ctx: {output.build_ms.toFixed(0)}ms
              {output.llm_ms ? ` | llm: ${output.llm_ms.toFixed(0)}ms` : ''}
              {output.llm_ms ? ` | total: ${(output.build_ms + output.llm_ms).toFixed(0)}ms` : ''}
            </span>
          )}
        </div>
        {isLoading && (
          <div className="flex items-center gap-1.5 text-yellow-solid text-xs">
            <Icon name="brain" size={12} color="currentColor" className="animate-pulse" />
            {streamingResponse ? 'Generating...' : 'Building...'}
          </div>
        )}
      </div>

      {/* Events timeline - collapsible */}
      <details open className="border-b border-white/10 bg-black/30 group">
        <summary className="px-4 py-2 cursor-pointer text-xs text-white/40 hover:text-white/60 transition-colors flex items-center gap-2">
          <svg
            className="w-3 h-3 transition-transform group-open:rotate-90"
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
          >
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
          </svg>
          Events ({events.length})
        </summary>
        <div className="px-4 pb-2">
          <div className="flex flex-wrap gap-1.5">
            {events.length === 0 ? (
              <span className="text-xs text-white/40">No events yet</span>
            ) : (
              events.map((evt, i) => <EventBadge key={i} event={evt} />)
            )}
          </div>
        </div>
      </details>

      {/* Tabs */}
      <div className="flex border-b border-white/10">
        {(['conversation', 'trace', 'effects'] as const).map((tab) => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            className={cn(
              'px-4 py-2 text-xs font-medium transition-colors',
              activeTab === tab
                ? 'text-white border-b-2 border-white'
                : 'text-white/40 hover:text-white/60'
            )}
          >
            {tab.charAt(0).toUpperCase() + tab.slice(1)}
            {tab === 'effects' && output && (
              <span className="ml-1 text-white/40">
                ({output.effects.length})
              </span>
            )}
          </button>
        ))}
      </div>

      {/* Content */}
      <div className="flex-1 overflow-auto p-4 bg-black">
        {activeTab === 'conversation' ? (
          <div className="space-y-4">
            {/* Classifier results - collapsible (only shown when classifier was used) */}
            {output?.classifier_result && (
              <details className="group">
                <summary className="cursor-pointer text-xs text-[#F5A623] hover:text-[#F5A623]/80 transition-colors flex items-center gap-2">
                  <svg
                    className="w-3 h-3 transition-transform group-open:rotate-90 -translate-y-px"
                    fill="none"
                    viewBox="0 0 24 24"
                    stroke="currentColor"
                  >
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                  </svg>
                  <span>Classifier Result {output.classifier_result.duration_ms ? `(${output.classifier_result.duration_ms.toFixed(0)}ms)` : ''}</span>
                </summary>
                <div className="mt-3 space-y-2">
                  {/* Layer execution status - from execution_summary */}
                  <div className="bg-[#1a1a0a] border border-[#4D3D18] px-3 py-2">
                    <p className="text-xs text-white/60 mb-2 font-medium">Layer Execution:</p>
                    <div className="flex flex-wrap gap-2">
                      {(() => {
                        const summary = output.execution_summary;
                        const allLayers = ['memory', 'knowledge_base', 'tools', 'actions'] as const;

                        return allLayers.map((layer) => {
                          const layerInfo = summary?.layers?.[layer];
                          const layerColors = LAYER_COLORS[layer] || { base: '#353535', highlight: '#C0C0C0' };

                          const isActive = layerInfo?.ran ?? false;
                          const source = layerInfo?.source ?? 'unknown';
                          const classifierDecision = layer === 'actions'
                            ? (Array.isArray(layerInfo?.classifier_decision) && layerInfo.classifier_decision.length > 0)
                            : (layerInfo?.classifier_decision ?? false);

                          // Determine if this was an override (ran but classifier said no)
                          const wasOverridden = isActive && !classifierDecision && source !== 'classifier';
                          const wasTestOverride = source === 'test_override';

                          // Build tooltip with details
                          const tooltipParts: string[] = [];
                          if (source) tooltipParts.push(`Source: ${source}`);
                          if (layerInfo?.items !== undefined) tooltipParts.push(`Items: ${layerInfo.items}`);
                          if (layerInfo?.reason) tooltipParts.push(`Reason: ${layerInfo.reason}`);
                          if (layerInfo?.triggered_actions?.length) {
                            tooltipParts.push(`Triggered: ${layerInfo.triggered_actions.join(', ')}`);
                          }
                          const tooltip = tooltipParts.join('\n');

                          return (
                            <div
                              key={layer}
                              className="flex items-center gap-1.5 px-2 py-1 rounded text-xs"
                              style={{
                                backgroundColor: isActive ? layerColors.base : '#1a1a1a',
                                opacity: isActive ? 1 : 0.5,
                              }}
                              title={tooltip}
                            >
                              <span className={cn(
                                'w-2 h-2 rounded-full',
                                isActive ? 'bg-green-500' : 'bg-red-500'
                              )} />
                              <span
                                className="font-medium capitalize"
                                style={{ color: isActive ? layerColors.highlight : '#666' }}
                              >
                                {layer.replace('_', ' ')}
                              </span>
                              {wasTestOverride && (
                                <span className="text-blue-400 text-[10px]" title="Test override">🧪</span>
                              )}
                              {wasOverridden && !wasTestOverride && (
                                <span className="text-yellow-500 text-[10px]" title="Always run override">⚡</span>
                              )}
                            </div>
                          );
                        });
                      })()}
                    </div>
                  </div>
                  {/* Selected behaviors */}
                  {output.classifier_result.selected_actions && output.classifier_result.selected_actions.length > 0 && (
                    <div className="bg-[#1a1a0a] border border-[#4D3D18] px-3 py-2 rounded">
                      <p className="text-xs text-white/60 mb-2 font-medium">Classifier Selected Behaviors:</p>
                      <div className="flex flex-wrap gap-2">
                        {output.classifier_result.selected_actions.map((actionKey: string, i: number) => (
                          <div
                            key={i}
                            className="flex items-center gap-1.5 px-2 py-1 rounded text-xs"
                            style={{ backgroundColor: '#1a2a1a', border: '1px solid #2a4a2a' }}
                          >
                            <span className="w-2 h-2 rounded-full bg-green-500" />
                            <span className="text-[#85CD75] font-medium">{actionKey}</span>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              </details>
            )}

            {/* Classifier prompt - what the classifier receives (only shown when classifier was used) */}
            {output?.classifier_prompt && (
              <details className="group">
                <summary className="cursor-pointer text-xs text-[#F5A623]/70 hover:text-[#F5A623] transition-colors flex items-center gap-2">
                  <svg
                    className="w-3 h-3 transition-transform group-open:rotate-90 -translate-y-px"
                    fill="none"
                    viewBox="0 0 24 24"
                    stroke="currentColor"
                  >
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                  </svg>
                  <span>What Classifier Receives</span>
                </summary>
                <div className="mt-3">
                  <div className="bg-[#0d0d0d] border border-[#1f1f1f] px-3 py-2">
                    <p className="text-xs leading-relaxed text-white/50 font-light whitespace-pre-wrap">
                      {output.classifier_prompt}
                    </p>
                  </div>
                </div>
              </details>
            )}

            {/* System messages (context) - collapsible */}
            {output && output.messages.filter(m => m.role === 'system').length > 0 && (
              <details className="group">
                <summary className="cursor-pointer text-xs text-white/40 hover:text-white/60 transition-colors flex items-center gap-2">
                  <svg
                    className="w-3 h-3 transition-transform group-open:rotate-90 -translate-y-px"
                    fill="none"
                    viewBox="0 0 24 24"
                    stroke="currentColor"
                  >
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                  </svg>
                  <span>System context ({output.messages.filter(m => m.role === 'system').length} blocks)</span>
                </summary>
                <div className="mt-3 space-y-2">
                  {output.messages.filter(m => m.role === 'system').map((msg, i) => (
                    <div key={i} className="bg-[#0d0d0d] border border-[#1f1f1f] px-3 py-2">
                      <p className="text-xs leading-relaxed text-white/50 font-light whitespace-pre-wrap">
                        {msg.content}
                      </p>
                    </div>
                  ))}
                </div>
              </details>
            )}

            {/* User message */}
            {output && output.messages.filter(m => m.role === 'user').map((msg, i) => (
              <div key={`user-${i}`} className="flex flex-col items-end">
                <p className="text-[18px] font-book leading-relaxed text-white text-right break-words whitespace-pre-wrap max-w-[85%]">
                  {msg.content}
                </p>
              </div>
            ))}

            {/* Assistant/Companion response */}
            {(streamingResponse || output?.assistant_response) && (
              <div className="flex flex-col items-start">
                <div className="w-fit bg-[#161616] rounded-lg px-3 py-2 max-w-[85%]">
                  <p className="m-0 text-[18px] leading-relaxed text-white break-words whitespace-pre-wrap">
                    {streamingResponse || output?.assistant_response}
                  </p>
                </div>
              </div>
            )}

            {/* Empty state */}
            {!output && !streamingResponse && (
              <div className="text-white/40 text-sm text-center py-8">
                {isLoading ? 'Building context...' : 'Run test to see conversation'}
              </div>
            )}
          </div>
        ) : activeTab === 'trace' ? (
          output ? (
            <div className="bg-[#0d0d0d] border border-[#1f1f1f] px-4 py-3">
              <pre className="text-xs text-white/60 whitespace-pre-wrap font-mono">
                {JSON.stringify(output.trace, null, 2)}
              </pre>
            </div>
          ) : (
            <div className="text-white/40 text-sm text-center py-8">
              {isLoading ? 'Building context...' : 'Run test to see trace'}
            </div>
          )
        ) : (
          output ? (
            <div className="space-y-2">
              {output.effects.length === 0 ? (
                <span className="text-white/40 text-sm">No effects</span>
              ) : (
                output.effects.map((effect, i) => (
                  <div key={i} className="bg-[#0d0d0d] border border-[#1f1f1f] px-4 py-3">
                    <pre className="text-xs text-white/60 whitespace-pre-wrap font-mono">
                      {JSON.stringify(effect, null, 2)}
                    </pre>
                  </div>
                ))
              )}
            </div>
          ) : (
            <div className="text-white/40 text-sm text-center py-8">
              {isLoading ? 'Building context...' : 'Run test to see effects'}
            </div>
          )
        )}
      </div>
    </div>
  );
}

// Save Test Modal
function SaveTestModal({
  isOpen,
  onClose,
  onSave,
  isSaving,
}: {
  isOpen: boolean;
  onClose: () => void;
  onSave: (name: string) => void;
  isSaving: boolean;
}) {
  const [name, setName] = useState('');

  if (!isOpen) return null;

  const handleSave = () => {
    if (name.trim()) {
      onSave(name.trim());
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div className="absolute inset-0 bg-black/60 backdrop-blur-[2px]" onClick={onClose} />
      <div className="relative z-10 w-[360px] max-w-[92vw] rounded-[4px] border border-white/20 bg-black pt-4 pb-2 px-4 shadow-xl">
        <h3 className="text-sm font-medium text-white mb-3">Save Test Configuration</h3>
        <input
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Enter test name..."
          autoFocus
          onKeyDown={(e) => {
            if (e.key === 'Enter') handleSave();
            if (e.key === 'Escape') onClose();
          }}
          className="w-full bg-gray-dark text-white text-sm rounded px-3 py-2 border border-white/20 focus:outline-none focus:border-white/40 placeholder:text-white/40"
        />
        <div className="mt-3 flex items-center justify-end gap-2">
          <button
            onClick={onClose}
            className="px-3 py-1.5 text-sm text-white/60 hover:text-white transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={handleSave}
            disabled={!name.trim() || isSaving}
            className={cn(
              'px-3 py-1.5 text-sm rounded transition-colors',
              name.trim() && !isSaving
                ? 'bg-white/10 text-white hover:bg-white/20'
                : 'bg-white/5 text-white/30 cursor-not-allowed'
            )}
          >
            {isSaving ? 'Saving...' : 'Save'}
          </button>
        </div>
      </div>
    </div>
  );
}

// Test Toolbar Component
function TestToolbar({
  savedTests,
  selectedTestId,
  onSelectTest,
  onSaveTest,
  onSaveAsTest,
  onUpdateTest,
  onDeleteTest,
  onRunAll,
  isRunningAll,
  runAllProgress,
  testResults,
  currentResultIndex,
  onNavigateResult,
  disabled,
  isSaving,
  justSaved,
}: {
  savedTests: SavedTest[];
  selectedTestId: string | null;
  onSelectTest: (testId: string | null) => void;
  onSaveTest: () => void;
  onSaveAsTest: () => void;
  onUpdateTest: () => void;
  onDeleteTest: () => void;
  onRunAll: () => void;
  isRunningAll: boolean;
  runAllProgress: { current: number; total: number };
  testResults: TestRunResult[];
  currentResultIndex: number;
  onNavigateResult: (direction: 'prev' | 'next') => void;
  disabled: boolean;
  isSaving: boolean;
  justSaved: boolean;
}) {
  const hasResults = testResults.length > 0;
  const currentResult = hasResults ? testResults[currentResultIndex] : null;
  const hasSelectedTest = selectedTestId !== null;

  return (
    <div className="flex items-center gap-3 px-4 py-2 bg-black border-b border-white/20">
      {/* Test Selector Dropdown */}
      <div className="flex items-center gap-2">
        <select
          value={selectedTestId || ''}
          onChange={(e) => onSelectTest(e.target.value || null)}
          disabled={disabled || isRunningAll}
          className="bg-gray-dark text-white text-sm rounded px-3 py-1.5 pr-8 border border-white/20 focus:outline-none focus:border-white/40 appearance-none bg-[url('data:image/svg+xml;charset=utf-8,%3Csvg%20xmlns%3D%22http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%22%20width%3D%2212%22%20height%3D%2212%22%20fill%3D%22none%22%20stroke%3D%22%23888%22%20stroke-width%3D%222%22%3E%3Cpath%20d%3D%22m2%204%204%204%204-4%22%2F%3E%3C%2Fsvg%3E')] bg-[length:12px] bg-[right_8px_center] bg-no-repeat min-w-[160px] disabled:opacity-50"
        >
          <option value="">Select saved test...</option>
          {savedTests.map((test) => (
            <option key={test.id} value={test.id}>
              {test.name}
            </option>
          ))}
        </select>
      </div>

      {/* Save buttons - different based on whether a test is selected */}
      {hasSelectedTest ? (
        <>
          {/* Save (update existing) */}
          <button
            onClick={onUpdateTest}
            disabled={disabled || isRunningAll || isSaving}
            className={cn(
              'px-3 py-1.5 text-sm rounded transition-all flex items-center gap-1.5',
              disabled || isRunningAll || isSaving
                ? 'bg-white/5 text-white/30 cursor-not-allowed'
                : justSaved
                  ? 'bg-green-solid text-white'
                  : 'bg-white/10 text-white hover:bg-white/20'
            )}
          >
            {isSaving ? (
              'Saving...'
            ) : justSaved ? (
              <>
                <Icon name="check" size={12} color="currentColor" />
                Saved
              </>
            ) : (
              'Save'
            )}
          </button>

          {/* Save As (create new) */}
          <button
            onClick={onSaveAsTest}
            disabled={disabled || isRunningAll || isSaving}
            className={cn(
              'px-3 py-1.5 text-sm rounded transition-colors flex items-center gap-1.5',
              disabled || isRunningAll || isSaving
                ? 'bg-white/5 text-white/30 cursor-not-allowed'
                : 'bg-white/10 text-white hover:bg-white/20'
            )}
          >
            <Icon name="plus" size={12} color="currentColor" />
            Save As
          </button>

          {/* Delete */}
          <button
            onClick={onDeleteTest}
            disabled={disabled || isRunningAll || isSaving}
            className={cn(
              'px-3 py-1.5 text-sm rounded transition-colors flex items-center gap-1.5',
              disabled || isRunningAll || isSaving
                ? 'bg-white/5 text-white/30 cursor-not-allowed'
                : 'text-brand-solid hover:bg-brand-bg'
            )}
          >
            <Icon name="trash" size={12} color="currentColor" />
            Delete
          </button>
        </>
      ) : (
        /* Save Test Button (create new) */
        <button
          onClick={onSaveTest}
          disabled={disabled || isRunningAll}
          className={cn(
            'px-3 py-1.5 text-sm rounded transition-colors flex items-center gap-1.5',
            disabled || isRunningAll
              ? 'bg-white/5 text-white/30 cursor-not-allowed'
              : 'bg-white/10 text-white hover:bg-white/20'
          )}
        >
          <Icon name="plus" size={12} color="currentColor" />
          Save Test
        </button>
      )}

      {/* Run All Button */}
      <button
        onClick={onRunAll}
        disabled={disabled || isRunningAll || savedTests.length === 0}
        className={cn(
          'px-3 py-1.5 text-sm rounded transition-colors flex items-center gap-1.5',
          disabled || isRunningAll || savedTests.length === 0
            ? 'bg-white/5 text-white/30 cursor-not-allowed'
            : 'bg-brand-solid text-white hover:bg-brand-solid-hover'
        )}
      >
        {isRunningAll ? 'Running...' : 'Run All'}
      </button>

      {/* Progress Bar (shown during Run All) */}
      {isRunningAll && runAllProgress.total > 0 && (
        <div className="flex items-center gap-2 ml-2">
          <div className="w-32 h-2 bg-white/10 rounded-full overflow-hidden">
            <div
              className="h-full bg-brand-solid transition-all duration-300"
              style={{ width: `${(runAllProgress.current / runAllProgress.total) * 100}%` }}
            />
          </div>
          <span className="text-xs text-white/60">
            {runAllProgress.current}/{runAllProgress.total}
          </span>
        </div>
      )}

      {/* Spacer */}
      <div className="flex-1" />

      {/* Navigation Arrows (shown after Run All completes) */}
      {hasResults && !isRunningAll && (
        <div className="flex items-center gap-2">
          <span className="text-xs text-white/60 mr-2">
            {currentResult?.testName || `Test ${currentResultIndex + 1}`}
          </span>
          <button
            onClick={() => onNavigateResult('prev')}
            disabled={currentResultIndex === 0}
            className={cn(
              'p-1.5 rounded transition-colors',
              currentResultIndex === 0
                ? 'text-white/20 cursor-not-allowed'
                : 'text-white/60 hover:text-white hover:bg-white/10'
            )}
          >
            <Icon name="chevron-left" size={16} color="currentColor" />
          </button>
          <span className="text-xs text-white/40">
            {currentResultIndex + 1} / {testResults.length}
          </span>
          <button
            onClick={() => onNavigateResult('next')}
            disabled={currentResultIndex === testResults.length - 1}
            className={cn(
              'p-1.5 rounded transition-colors',
              currentResultIndex === testResults.length - 1
                ? 'text-white/20 cursor-not-allowed'
                : 'text-white/60 hover:text-white hover:bg-white/10'
            )}
          >
            <Icon name="chevron-right" size={16} color="currentColor" />
          </button>
        </div>
      )}
    </div>
  );
}

// Main component
export default function ContextEngineTesting() {
  const { getToken } = useAuth();
  const queryClient = useQueryClient();

  // Form state
  const [selectedCompanionId, setSelectedCompanionId] = useState<string>('');
  const [userMessage, setUserMessage] = useState('Hello, how are you?');
  const [coreSystemPrompt, setCoreSystemPrompt] = useState('');
  const [coreMemories, setCoreMemories] = useState<string[]>([]);
  const [regularMemories, setRegularMemories] = useState<string[]>([]);
  const [knowledgeResults, setKnowledgeResults] = useState<string[]>([]);
  const [history, setHistory] = useState<HistoryMessage[]>([]);
  const [profileJson, setProfileJson] = useState(DEFAULT_PROFILE_EXAMPLE_JSON);
  const [includeMemory, setIncludeMemory] = useState(true);
  const [includeKnowledge, setIncludeKnowledge] = useState(true);
  const [includeTools, setIncludeTools] = useState(false);
  const [includeBehaviors, setIncludeBehaviors] = useState(false);
  const [includeProfileInPrompt, setIncludeProfileInPrompt] = useState(false);
  const [selectedModel, setSelectedModel] = useState('openai-gpt4o-mini');
  const [maxOutputTokens, setMaxOutputTokens] = useState<number | null>(null);
  const [companionMaxOutputTokens, setCompanionMaxOutputTokens] = useState<number | null>(null);
  const [toolSpecStatus, setToolSpecStatus] = useState<string | null>(null);
  const [isUploadingToolSpec, setIsUploadingToolSpec] = useState(false);
  const toolSpecInputRef = useRef<HTMLInputElement | null>(null);
  const [toolSpecs, setToolSpecs] = useState<Array<{ id: string; name: string | null }>>([]);

  // Classifier settings
  const [useClassifier, setUseClassifier] = useState(false);
  const [classifierModel, setClassifierModel] = useState('gemini-2.0-flash');
  const [alwaysRunMemory, setAlwaysRunMemory] = useState(false);
  const [alwaysRunKnowledge, setAlwaysRunKnowledge] = useState(false);
  const [alwaysRunTools, setAlwaysRunTools] = useState(false);
  const [alwaysRunBehaviors, setAlwaysRunBehaviors] = useState(false);

  // Output state
  const [rawOutput, setRawOutput] = useState<ContextOutput | null>(null);
  const [layeredOutput, setLayeredOutput] = useState<ContextOutput | null>(null);
  const [rawEvents, setRawEvents] = useState<ContextEvent[]>([]);
  const [layeredEvents, setLayeredEvents] = useState<ContextEvent[]>([]);
  const [rawStreamingResponse, setRawStreamingResponse] = useState('');
  const [layeredStreamingResponse, setLayeredStreamingResponse] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [currentMode, setCurrentMode] = useState<'raw' | 'layered' | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Saved tests state
  const [selectedTestId, setSelectedTestId] = useState<string | null>(null);
  const [isSaveModalOpen, setIsSaveModalOpen] = useState(false);
  const [isSavingTest, setIsSavingTest] = useState(false);
  const [justSaved, setJustSaved] = useState(false);
  const [isRunningAll, setIsRunningAll] = useState(false);
  const [runAllProgress, setRunAllProgress] = useState({ current: 0, total: 0 });
  const [testResults, setTestResults] = useState<TestRunResult[]>([]);
  const [currentResultIndex, setCurrentResultIndex] = useState(0);

  const abortRef = useRef<AbortController | null>(null);

  const profileValidation = useMemo(() => {
    const raw = profileJson.trim();
    if (!raw) {
      return {
        profile: null as Record<string, unknown> | null,
        error: 'Profile JSON cannot be empty.',
      };
    }

    try {
      const parsed = JSON.parse(raw);
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
        return {
          profile: null as Record<string, unknown> | null,
          error: 'Profile JSON must be an object.',
        };
      }
      return { profile: parsed as Record<string, unknown>, error: null as string | null };
    } catch {
      return {
        profile: null as Record<string, unknown> | null,
        error: 'Profile JSON is invalid.',
      };
    }
  }, [profileJson]);

  // Fetch companions
  const { data: companions = [] } = useQuery<Companion[]>({
    queryKey: ['context-testing-companions'],
    queryFn: async () => {
      const token = await getToken();
      const res = await fetch(`${API_BASE}/api/context-engine-testing/companions`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!res.ok) throw new Error('Failed to fetch companions');
      return res.json();
    },
  });

  // Fetch behaviors for the selected companion's project
  const { data: companionBehaviors = [] } = useQuery<BehaviorInfo[]>({
    queryKey: ['companion-behaviors', selectedCompanionId],
    queryFn: async () => {
      if (!selectedCompanionId) return [];
      const token = await getToken();
      const res = await fetch(`${API_BASE}/api/context-engine-testing/companions/${selectedCompanionId}/behaviors`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!res.ok) return [];
      const data = await res.json();
      return data.behaviors;
    },
    enabled: !!selectedCompanionId,
  });

  // Fetch saved tests for selected companion
  const { data: savedTests = [] } = useQuery<SavedTest[]>({
    queryKey: ['context-engine-tests', selectedCompanionId],
    queryFn: async () => {
      if (!selectedCompanionId) return [];
      const token = await getToken();
      const res = await fetch(`${API_BASE}/api/context-engine-testing/tests/${selectedCompanionId}`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!res.ok) throw new Error('Failed to fetch saved tests');
      return res.json();
    },
    enabled: !!selectedCompanionId,
  });

  const visibleCompanionBehaviors = useMemo(
    () => companionBehaviors.filter((behavior) => behavior.enabled || behavior.triggers.length > 0),
    [companionBehaviors]
  );

  // Load companion context when selected
  const loadCompanionContext = useCallback(
    async (companionId: string) => {
      if (!companionId) {
        setCompanionMaxOutputTokens(null);
        setMaxOutputTokens(null);
        return;
      }
      try {
        const token = await getToken();
        const res = await fetch(
          `${API_BASE}/api/context-engine-testing/companions/${companionId}/context`,
          { headers: { Authorization: `Bearer ${token}` } }
        );
        if (!res.ok) throw new Error('Failed to fetch companion context');
        const data = await res.json();
        setCoreSystemPrompt(data.core_system_prompt || '');
        setCoreMemories(data.core_memories || []);
        // Load max_output_tokens from companion config
        setCompanionMaxOutputTokens(data.max_output_tokens || null);
        setMaxOutputTokens(data.max_output_tokens || null);
      } catch (err) {
        console.error('Failed to load companion context:', err);
      }
    },
    [getToken]
  );

  const loadToolSpecs = useCallback(
    async (companionId: string) => {
      if (!companionId) {
        setToolSpecs([]);
        return;
      }
      try {
        const token = await getToken();
        const res = await fetch(`${API_BASE}/api/tools?companion_id=${companionId}`, {
          headers: { Authorization: `Bearer ${token}` },
        });
        if (!res.ok) throw new Error('Failed to fetch tool specs');
        const data = await res.json();
        setToolSpecs(
          data.map((item: { id: string; spec_name?: string }) => ({
            id: item.id,
            name: item.spec_name || 'OpenAPI spec',
          }))
        );
      } catch (err) {
        console.error('Failed to load tool specs:', err);
        setToolSpecs([]);
      }
    },
    [getToken]
  );

  const handleToolSpecUpload = useCallback(async (file: File) => {
    if (!selectedCompanionId) {
      setToolSpecStatus('Select a companion before uploading a tool spec.');
      return;
    }

    setIsUploadingToolSpec(true);
    setToolSpecStatus(null);
    try {
      const text = await file.text();
      let parsed: unknown;
      try {
        parsed = JSON.parse(text);
          // eslint-disable-next-line @typescript-eslint/no-unused-vars
      } catch (err) {
        setToolSpecStatus('Invalid JSON file. Please upload a valid OpenAPI JSON.');
        return;
      }

      const token = await getToken();
      const res = await fetch(`${API_BASE}/api/tools/index`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({
          companion_id: selectedCompanionId,
          spec_name: file.name,
          openapi_spec: parsed,
        }),
      });

      if (!res.ok) {
        const detail = await res.text();
        throw new Error(detail || `Upload failed with status ${res.status}`);
      }

      await loadToolSpecs(selectedCompanionId);
      setToolSpecStatus(null);
    } catch (err) {
      setToolSpecStatus((err as Error).message);
    } finally {
      setIsUploadingToolSpec(false);
    }
  }, [getToken, selectedCompanionId, loadToolSpecs]);

  // Get current configuration as SavedTestConfig
  const getCurrentConfig = useCallback((): SavedTestConfig => {
    return {
      user_message: userMessage,
      core_system_prompt: coreSystemPrompt || null,
      core_memories: coreMemories.length > 0 ? coreMemories.filter(Boolean) : null,
      regular_memories: regularMemories.length > 0 ? regularMemories.filter(Boolean) : null,
      knowledge_results: knowledgeResults.length > 0 ? knowledgeResults.filter(Boolean) : null,
      history: history.length > 0 ? history.filter((h) => h.content) : null,
      profile_override: profileValidation.profile,
      include_memory: includeMemory,
      include_knowledge: includeKnowledge,
      include_tools: includeTools,
      include_actions: includeBehaviors,
      include_profile_in_prompt: includeProfileInPrompt,
      use_classifier: useClassifier,
      classifier_model: classifierModel,
      layer_always_run: {
        memory: alwaysRunMemory,
        knowledge: alwaysRunKnowledge,
        tools: alwaysRunTools,
        actions: alwaysRunBehaviors,
      },
      model: selectedModel,
      max_output_tokens: maxOutputTokens,
    };
  }, [
    userMessage, coreSystemPrompt, coreMemories, regularMemories, knowledgeResults,
    history, profileValidation.profile, includeMemory, includeKnowledge, includeTools, includeBehaviors, includeProfileInPrompt,
    useClassifier, classifierModel, alwaysRunMemory, alwaysRunKnowledge,
    alwaysRunTools, alwaysRunBehaviors, selectedModel, maxOutputTokens,
  ]);

  // Load a test config into the form
  const loadTestConfig = useCallback((config: SavedTestConfig) => {
    setUserMessage(config.user_message);
    setCoreSystemPrompt(config.core_system_prompt || '');
    setCoreMemories(config.core_memories || []);
    setRegularMemories(config.regular_memories || []);
    setKnowledgeResults(config.knowledge_results || []);
    setHistory(config.history || []);
    setIncludeProfileInPrompt(config.include_profile_in_prompt ?? false);
    if (
      config.profile_override &&
      typeof config.profile_override === 'object' &&
      !Array.isArray(config.profile_override)
    ) {
      setProfileJson(JSON.stringify(config.profile_override, null, 2));
    } else {
      setProfileJson(DEFAULT_PROFILE_EXAMPLE_JSON);
    }
    setIncludeMemory(config.include_memory);
    setIncludeKnowledge(config.include_knowledge);
    setIncludeTools(config.include_tools);
    setIncludeBehaviors(config.include_actions);
    setUseClassifier(config.use_classifier);
    setClassifierModel(config.classifier_model);
    setAlwaysRunMemory(config.layer_always_run.memory);
    setAlwaysRunKnowledge(config.layer_always_run.knowledge);
    setAlwaysRunTools(config.layer_always_run.tools);
    setAlwaysRunBehaviors(config.layer_always_run.actions);
    setSelectedModel(config.model);
    setMaxOutputTokens(config.max_output_tokens || null);
  }, []);

  // Save current config as a new test
  const saveTest = useCallback(async (name: string) => {
    if (!selectedCompanionId) return;

    setIsSavingTest(true);
    try {
      const token = await getToken();
      const config = getCurrentConfig();

      const res = await fetch(`${API_BASE}/api/context-engine-testing/tests`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({
          companion_id: selectedCompanionId,
          name,
          config,
        }),
      });

      if (!res.ok) throw new Error('Failed to save test');

      const savedTest = await res.json();

      // Refresh saved tests list and select the new test
      await queryClient.invalidateQueries({ queryKey: ['context-engine-tests', selectedCompanionId] });
      setSelectedTestId(savedTest.id);
      setIsSaveModalOpen(false);
    } catch (err) {
      console.error('Failed to save test:', err);
      setError((err as Error).message);
    } finally {
      setIsSavingTest(false);
    }
  }, [selectedCompanionId, getCurrentConfig, getToken, queryClient]);

  // Update existing test with current config
  const updateTest = useCallback(async () => {
    if (!selectedCompanionId || !selectedTestId) return;

    setIsSavingTest(true);
    setJustSaved(false);
    try {
      const token = await getToken();
      const config = getCurrentConfig();

      const res = await fetch(`${API_BASE}/api/context-engine-testing/tests/${selectedTestId}`, {
        method: 'PUT',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({ config }),
      });

      if (!res.ok) throw new Error('Failed to update test');

      // Refresh saved tests list
      await queryClient.invalidateQueries({ queryKey: ['context-engine-tests', selectedCompanionId] });

      // Show success feedback
      setJustSaved(true);
      setTimeout(() => setJustSaved(false), 2000);
    } catch (err) {
      console.error('Failed to update test:', err);
      setError((err as Error).message);
    } finally {
      setIsSavingTest(false);
    }
  }, [selectedCompanionId, selectedTestId, getCurrentConfig, getToken, queryClient]);

  // Delete a test
  const deleteTest = useCallback(async () => {
    if (!selectedCompanionId || !selectedTestId) return;

    // Find test name for confirmation
    const test = savedTests.find((t) => t.id === selectedTestId);
    if (!test) return;

    const confirmed = window.confirm(`Delete test "${test.name}"?`);
    if (!confirmed) return;

    setIsSavingTest(true);
    try {
      const token = await getToken();

      const res = await fetch(`${API_BASE}/api/context-engine-testing/tests/${selectedTestId}`, {
        method: 'DELETE',
        headers: {
          Authorization: `Bearer ${token}`,
        },
      });

      if (!res.ok) throw new Error('Failed to delete test');

      // Clear selection and refresh list
      setSelectedTestId(null);
      await queryClient.invalidateQueries({ queryKey: ['context-engine-tests', selectedCompanionId] });
    } catch (err) {
      console.error('Failed to delete test:', err);
      setError((err as Error).message);
    } finally {
      setIsSavingTest(false);
    }
  }, [selectedCompanionId, selectedTestId, savedTests, getToken, queryClient]);

  // Select a saved test and load its config
  const handleSelectTest = useCallback((testId: string | null) => {
    setSelectedTestId(testId);
    // Clear test results when selecting a different test
    setTestResults([]);
    setCurrentResultIndex(0);

    if (testId) {
      const test = savedTests.find((t) => t.id === testId);
      if (test) {
        loadTestConfig(test.config);
      }
    }
  }, [savedTests, loadTestConfig]);

  // Navigate between test results
  const handleNavigateResult = useCallback((direction: 'prev' | 'next') => {
    setCurrentResultIndex((prev) => {
      const newIndex = direction === 'prev' ? prev - 1 : prev + 1;
      if (newIndex >= 0 && newIndex < testResults.length) {
        return newIndex;
      }
      return prev;
    });
  }, [testResults.length]);

  // Get the displayed output based on whether we're viewing test results
  const displayedRawOutput = useMemo(() => {
    if (testResults.length > 0 && !isRunningAll) {
      return testResults[currentResultIndex]?.rawOutput || null;
    }
    return rawOutput;
  }, [testResults, currentResultIndex, isRunningAll, rawOutput]);

  const displayedLayeredOutput = useMemo(() => {
    if (testResults.length > 0 && !isRunningAll) {
      return testResults[currentResultIndex]?.layeredOutput || null;
    }
    return layeredOutput;
  }, [testResults, currentResultIndex, isRunningAll, layeredOutput]);

  const displayedRawEvents = useMemo(() => {
    if (testResults.length > 0 && !isRunningAll) {
      return testResults[currentResultIndex]?.rawEvents || [];
    }
    return rawEvents;
  }, [testResults, currentResultIndex, isRunningAll, rawEvents]);

  const displayedLayeredEvents = useMemo(() => {
    if (testResults.length > 0 && !isRunningAll) {
      return testResults[currentResultIndex]?.layeredEvents || [];
    }
    return layeredEvents;
  }, [testResults, currentResultIndex, isRunningAll, layeredEvents]);

  // Run test with streaming
  const runTest = useCallback(async () => {
    if (!selectedCompanionId) {
      setError('Please select a companion');
      return;
    }
    if (includeProfileInPrompt && profileValidation.error) {
      setError(`Profile JSON error: ${profileValidation.error}`);
      return;
    }

    // Abort any previous request
    if (abortRef.current) {
      abortRef.current.abort();
    }
    abortRef.current = new AbortController();

    setIsLoading(true);
    setError(null);
    setRawOutput(null);
    setLayeredOutput(null);
    setRawEvents([]);
    setLayeredEvents([]);
    setRawStreamingResponse('');
    setLayeredStreamingResponse('');
    setCurrentMode(null);

    // Track start times for each mode to calculate relative timestamps
    const modeStartTimes: Record<string, number> = {};

    try {
      const token = await getToken();
      const body = {
        companion_id: selectedCompanionId,
        user_message: userMessage,
        core_system_prompt: coreSystemPrompt || null,
        core_memories: coreMemories.length > 0 ? coreMemories.filter(Boolean) : null,
        regular_memories:
          regularMemories.length > 0 ? regularMemories.filter(Boolean) : null,
        knowledge_results:
          knowledgeResults.length > 0 ? knowledgeResults.filter(Boolean) : null,
        history: history.length > 0 ? history.filter((h) => h.content) : null,
        include_memory: includeMemory,
        include_knowledge: includeKnowledge,
        include_tools: includeTools,
        include_behaviors: includeBehaviors,
        include_profile_in_prompt: includeProfileInPrompt,
        profile_override: includeProfileInPrompt ? profileValidation.profile : null,
        use_classifier: useClassifier,
        classifier_model: classifierModel,
        layer_always_run: {
          memory: alwaysRunMemory,
          knowledge: alwaysRunKnowledge,
          tools: alwaysRunTools,
          actions: alwaysRunBehaviors,
        },
        model: selectedModel,
        temperature: 0.7,
        max_output_tokens: maxOutputTokens,
      };

      const res = await fetch(`${API_BASE}/api/context-engine-testing/run/stream`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`,
          Accept: 'text/event-stream',
        },
        body: JSON.stringify(body),
        signal: abortRef.current.signal,
      });

      if (!res.ok || !res.body) {
        throw new Error(`Request failed: ${res.status}`);
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });

        let idx;
        while ((idx = buffer.indexOf('\n\n')) !== -1) {
          const chunk = buffer.slice(0, idx);
          buffer = buffer.slice(idx + 2);

          // Parse SSE event
          let evtType: string | null = null;
          const dataLines: string[] = [];
          for (const line of chunk.split('\n')) {
            if (line.startsWith('event:')) {
              evtType = line.slice(6).trim();
            } else if (line.startsWith('data:')) {
              dataLines.push(line.slice(5).trim());
            }
          }

          const dataStr = dataLines.join('\n');
          let parsed: SSEEvent = { type: 'done' };
          try {
            parsed = dataStr ? JSON.parse(dataStr) : { type: 'done' };
          } catch {
            continue;
          }

          // Handle event based on type
          if (evtType === 'mode_start') {
            setCurrentMode(parsed.mode as 'raw' | 'layered');
            // Record start time for this mode
            modeStartTimes[parsed.mode as string] = performance.now();
            // Clear streaming response when starting a new mode
            if (parsed.mode === 'raw') {
              setRawStreamingResponse('');
            } else if (parsed.mode === 'layered') {
              setLayeredStreamingResponse('');
            }
          } else if (evtType === 'event') {
            const evt: ContextEvent = {
              name: parsed.name || 'unknown',
              phase: (parsed.phase as ContextEvent['phase']) || 'info',
              meta: parsed.meta || {},
              ts_ms: parsed.ts_ms || 0,
            };
            if (parsed.mode === 'raw') {
              setRawEvents((prev) => [...prev, evt]);
            } else if (parsed.mode === 'layered') {
              setLayeredEvents((prev) => [...prev, evt]);
            }
          } else if (evtType === 'llm_start') {
            // LLM generation starting - add event with relative timestamp
            const modeKey = parsed.mode as string;
            const startTime = modeStartTimes[modeKey] || performance.now();
            const relativeMs = performance.now() - startTime;
            const evt: ContextEvent = {
              name: 'llm:generating',
              phase: 'start',
              meta: { model: parsed.model },
              ts_ms: relativeMs,
            };
            if (parsed.mode === 'raw') {
              setRawEvents((prev) => [...prev, evt]);
            } else if (parsed.mode === 'layered') {
              setLayeredEvents((prev) => [...prev, evt]);
            }
          } else if (evtType === 'llm_delta') {
            // Streaming LLM response
            if (parsed.mode === 'raw' && parsed.content) {
              setRawStreamingResponse((prev) => prev + parsed.content);
            } else if (parsed.mode === 'layered' && parsed.content) {
              setLayeredStreamingResponse((prev) => prev + parsed.content);
            }
          } else if (evtType === 'llm_end') {
            // LLM generation complete - add event with relative timestamp
            const modeKey = parsed.mode as string;
            const startTime = modeStartTimes[modeKey] || performance.now();
            const relativeMs = performance.now() - startTime;
            const evt: ContextEvent = {
              name: 'llm:generating',
              phase: 'end',
              meta: { duration_ms: parsed.duration_ms },
              ts_ms: relativeMs,
            };
            if (parsed.mode === 'raw') {
              setRawEvents((prev) => [...prev, evt]);
            } else if (parsed.mode === 'layered') {
              setLayeredEvents((prev) => [...prev, evt]);
            }
          } else if (evtType === 'llm_error') {
            const modeKey = parsed.mode as string;
            const startTime = modeStartTimes[modeKey] || performance.now();
            const relativeMs = performance.now() - startTime;
            const evt: ContextEvent = {
              name: 'llm:error',
              phase: 'error',
              meta: { error: parsed.error },
              ts_ms: relativeMs,
            };
            if (parsed.mode === 'raw') {
              setRawEvents((prev) => [...prev, evt]);
            } else if (parsed.mode === 'layered') {
              setLayeredEvents((prev) => [...prev, evt]);
            }
          } else if (evtType === 'mode_complete') {
            if (parsed.mode === 'raw' && parsed.output) {
              setRawOutput(parsed.output);
              setRawStreamingResponse(''); // Clear streaming, use final response
            } else if (parsed.mode === 'layered' && parsed.output) {
              setLayeredOutput(parsed.output);
              setLayeredStreamingResponse(''); // Clear streaming, use final response
            }
          } else if (evtType === 'done') {
            setCurrentMode(null);
          } else if (evtType === 'error') {
            setError(parsed.detail || 'Unknown error');
          }
        }
      }
    } catch (err) {
      if ((err as Error).name !== 'AbortError') {
        setError((err as Error).message);
      }
    } finally {
      setIsLoading(false);
      setCurrentMode(null);
    }
  }, [
    selectedCompanionId,
    userMessage,
    coreSystemPrompt,
    coreMemories,
    regularMemories,
    knowledgeResults,
    history,
    includeMemory,
    includeKnowledge,
    includeTools,
    includeBehaviors,
    includeProfileInPrompt,
    profileValidation.profile,
    profileValidation.error,
    useClassifier,
    classifierModel,
    alwaysRunMemory,
    alwaysRunKnowledge,
    alwaysRunTools,
    alwaysRunBehaviors,
    selectedModel,
    maxOutputTokens,
    getToken,
  ]);

  // Run a single test and return results (for Run All)
  const runSingleTest = useCallback(async (test: SavedTest): Promise<TestRunResult> => {
    const config = test.config;
    const token = await getToken();

    let resultRawOutput: ContextOutput | null = null;
    let resultLayeredOutput: ContextOutput | null = null;
    const resultRawEvents: ContextEvent[] = [];
    const resultLayeredEvents: ContextEvent[] = [];
    let resultError: string | undefined;

    try {
      const body = {
        companion_id: selectedCompanionId,
        user_message: config.user_message,
        core_system_prompt: config.core_system_prompt || null,
        core_memories: config.core_memories || null,
        regular_memories: config.regular_memories || null,
        knowledge_results: config.knowledge_results || null,
        history: config.history || null,
        include_memory: config.include_memory,
        include_knowledge: config.include_knowledge,
        include_tools: config.include_tools,
        include_behaviors: config.include_actions,
        include_profile_in_prompt: config.include_profile_in_prompt ?? false,
        profile_override:
          config.include_profile_in_prompt && config.profile_override
            ? config.profile_override
            : null,
        use_classifier: config.use_classifier,
        classifier_model: config.classifier_model,
        layer_always_run: config.layer_always_run,
        model: config.model,
        temperature: 0.7,
        max_output_tokens: config.max_output_tokens,
      };

      const res = await fetch(`${API_BASE}/api/context-engine-testing/run/stream`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`,
          Accept: 'text/event-stream',
        },
        body: JSON.stringify(body),
      });

      if (!res.ok || !res.body) {
        throw new Error(`Request failed: ${res.status}`);
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });

        let idx;
        while ((idx = buffer.indexOf('\n\n')) !== -1) {
          const chunk = buffer.slice(0, idx);
          buffer = buffer.slice(idx + 2);

          let evtType: string | null = null;
          const dataLines: string[] = [];
          for (const line of chunk.split('\n')) {
            if (line.startsWith('event:')) {
              evtType = line.slice(6).trim();
            } else if (line.startsWith('data:')) {
              dataLines.push(line.slice(5).trim());
            }
          }

          const dataStr = dataLines.join('\n');
          let parsed: SSEEvent = { type: 'done' };
          try {
            parsed = dataStr ? JSON.parse(dataStr) : { type: 'done' };
          } catch {
            continue;
          }

          if (evtType === 'event') {
            const evt: ContextEvent = {
              name: parsed.name || 'unknown',
              phase: (parsed.phase as ContextEvent['phase']) || 'info',
              meta: parsed.meta || {},
              ts_ms: parsed.ts_ms || 0,
            };
            if (parsed.mode === 'raw') {
              resultRawEvents.push(evt);
            } else if (parsed.mode === 'layered') {
              resultLayeredEvents.push(evt);
            }
          } else if (evtType === 'mode_complete') {
            if (parsed.mode === 'raw' && parsed.output) {
              resultRawOutput = parsed.output;
            } else if (parsed.mode === 'layered' && parsed.output) {
              resultLayeredOutput = parsed.output;
            }
          } else if (evtType === 'error') {
            resultError = parsed.detail || 'Unknown error';
          }
        }
      }
    } catch (err) {
      resultError = (err as Error).message;
    }

    return {
      testId: test.id,
      testName: test.name,
      rawOutput: resultRawOutput,
      layeredOutput: resultLayeredOutput,
      rawEvents: resultRawEvents,
      layeredEvents: resultLayeredEvents,
      error: resultError,
    };
  }, [selectedCompanionId, getToken]);

  // Run all saved tests sequentially
  const runAllTests = useCallback(async () => {
    if (!selectedCompanionId || savedTests.length === 0) return;

    setIsRunningAll(true);
    setTestResults([]);
    setCurrentResultIndex(0);
    setRunAllProgress({ current: 0, total: savedTests.length });
    setError(null);

    // Clear current outputs
    setRawOutput(null);
    setLayeredOutput(null);
    setRawEvents([]);
    setLayeredEvents([]);
    setRawStreamingResponse('');
    setLayeredStreamingResponse('');

    const results: TestRunResult[] = [];

    for (let i = 0; i < savedTests.length; i++) {
      const test = savedTests[i];
      setRunAllProgress({ current: i, total: savedTests.length });

      // Load test config into form (so user sees current test)
      loadTestConfig(test.config);

      // Run the test
      const result = await runSingleTest(test);
      results.push(result);

      // Update progress
      setRunAllProgress({ current: i + 1, total: savedTests.length });
    }

    setTestResults(results);
    setCurrentResultIndex(0);
    setIsRunningAll(false);
  }, [selectedCompanionId, savedTests, loadTestConfig, runSingleTest]);

  return (
    <div className="fixed inset-0 bg-black text-white flex">
      {/* Left panel - Inputs */}
      <div className="w-96 bg-gray-darker border-r border-white/10 flex flex-col">
        <div className="p-4 border-b border-white/10 shrink-0">
          <h1 className="text-[32px] font-light tracking-[-0.04em]">Context Engine Testing</h1>
          <p className="text-xs text-white/40">
            Test the layered orchestrator with custom inputs
          </p>
        </div>

        <div className="flex-1 overflow-y-auto p-4 space-y-6">
          {/* Companion selector */}
          <div>
            <SectionLabel>Companion</SectionLabel>
            <Dropdown
              options={companions.map(c => ({ value: c.id, label: c.name }))}
              value={selectedCompanionId}
              onChange={(value) => {
                setSelectedCompanionId(value);
                loadCompanionContext(value);
                loadToolSpecs(value);
              }}
              placeholder="Select companion..."
            />
          </div>

          {/* User message */}
          <div>
            <SectionLabel>User Message</SectionLabel>
            <Textarea
              value={userMessage}
              onChange={(e) => setUserMessage(e.target.value)}
              placeholder="Enter user message..."
              minHeight={64}
            />
          </div>

          {/* Core system prompt */}
          <div>
            <SectionLabel>Core System Prompt (override)</SectionLabel>
            <Textarea
              value={coreSystemPrompt}
              onChange={(e) => setCoreSystemPrompt(e.target.value)}
              placeholder="Leave empty to use companion's default..."
              minHeight={100}
            />
          </div>

          {/* Core memories */}
          <div>
            <SectionLabel>Core Memories</SectionLabel>
            <ItemList
              items={coreMemories}
              onChange={setCoreMemories}
              placeholder="Core memory..."
              addLabel="Add core memory"
            />
          </div>

          {/* Regular memories */}
          <div>
            <SectionLabel>Regular Memories (override retrieval)</SectionLabel>
            <ItemList
              items={regularMemories}
              onChange={setRegularMemories}
              placeholder="Retrieved memory..."
              addLabel="Add memory"
            />
          </div>

          {/* Knowledge results */}
          <div>
            <SectionLabel>Knowledge Results (override retrieval)</SectionLabel>
            <ItemList
              items={knowledgeResults}
              onChange={setKnowledgeResults}
              placeholder="Knowledge chunk..."
              addLabel="Add knowledge"
            />
          </div>

          {/* History */}
          <div>
            <SectionLabel>Conversation History</SectionLabel>
            <HistoryList history={history} onChange={setHistory} />
          </div>

          {/* Profile override */}
          <div>
            <div className="flex items-center justify-between mb-1.5">
              <SectionLabel>Profile (Layered Mode)</SectionLabel>
              <button
                onClick={() => setProfileJson(DEFAULT_PROFILE_EXAMPLE_JSON)}
                className="text-[10px] text-white/40 hover:text-white/70 transition-colors"
              >
                Reset example
              </button>
            </div>
            <label
              className={cn(
                'flex items-center gap-3 px-3 py-2 cursor-pointer transition-all',
                includeProfileInPrompt ? 'bg-[#3A5030]' : 'bg-gray-dark/50 opacity-60'
              )}
            >
              <input
                type="checkbox"
                checked={includeProfileInPrompt}
                onChange={(e) => setIncludeProfileInPrompt(e.target.checked)}
                className="sr-only"
              />
              <div
                className={cn(
                  'w-4 h-4 rounded-sm border-2 flex items-center justify-center transition-colors',
                  includeProfileInPrompt ? 'bg-[#85CD75] border-[#85CD75]' : 'border-white/40'
                )}
              >
                {includeProfileInPrompt && (
                  <svg width="10" height="10" viewBox="0 0 24 24" fill="none">
                    <path
                      d="M5 13L9 17L19 7"
                      stroke="#1a1a1a"
                      strokeWidth="3"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    />
                  </svg>
                )}
              </div>
              <div className="flex-1">
                <span
                  className={cn(
                    'text-sm font-medium',
                    includeProfileInPrompt ? 'text-[#85CD75]' : 'text-white'
                  )}
                >
                  Include profile in layered prompt
                </span>
                <span className="text-xs text-white/40 ml-2">inject as # PROFILE block</span>
              </div>
            </label>
            <div className="mt-2 space-y-1">
              <Textarea
                value={profileJson}
                onChange={(e) => setProfileJson(e.target.value)}
                minHeight={120}
                placeholder="Profile JSON..."
                className={cn(
                  profileValidation.error &&
                    'ring-1 ring-brand-solid/50 focus:ring-brand-solid/60'
                )}
              />
              <p className="text-[10px] text-white/40">
                This profile is only applied to layered mode when the toggle is enabled.
              </p>
              {profileValidation.error && (
                <p className="text-[10px] text-brand-solid">{profileValidation.error}</p>
              )}
            </div>
          </div>

          {/* Tool specs upload */}
          <div>
            <SectionLabel>Tool Specs</SectionLabel>
            <div className="space-y-2">
              {toolSpecs.length > 0 && (
                <div className="space-y-1">
                  {toolSpecs.map((spec) => (
                    <div key={spec.id} className="flex items-center justify-between px-3 py-2 rounded border border-white/10 bg-gray-dark/50">
                      <span className="text-sm text-white/80">{spec.name || 'OpenAPI spec'}</span>
                      <button
                        onClick={async () => {
                          if (!selectedCompanionId) return;
                          try {
                            const token = await getToken();
                            const res = await fetch(`${API_BASE}/api/tools/${spec.id}?companion_id=${selectedCompanionId}`, {
                              method: 'DELETE',
                              headers: { Authorization: `Bearer ${token}` },
                            });
                            if (!res.ok) throw new Error('Failed to delete tool spec');
                            await loadToolSpecs(selectedCompanionId);
                          } catch (err) {
                            console.error('Failed to delete tool spec:', err);
                          }
                        }}
                        className="text-white/50 hover:text-white transition-colors"
                        title="Remove"
                      >
                        <Icon name="x" size={14} color="currentColor" />
                      </button>
                    </div>
                  ))}
                </div>
              )}
              <input
                ref={toolSpecInputRef}
                type="file"
                accept="application/json"
                className="hidden"
                onChange={(e) => {
                  const file = e.target.files?.[0];
                  if (file) {
                    void handleToolSpecUpload(file);
                    e.target.value = '';
                  }
                }}
              />
              <button
                onClick={() => toolSpecInputRef.current?.click()}
                className={cn(
                  'text-xs text-white/60 hover:text-white/80 transition-colors flex items-center gap-1',
                  isUploadingToolSpec && 'cursor-not-allowed opacity-70'
                )}
                disabled={isUploadingToolSpec}
              >
                <Icon name="plus" size={12} color="currentColor" />
                {isUploadingToolSpec ? 'Indexing...' : 'Add OpenAPI JSON'}
              </button>
              {toolSpecStatus && (
                <p className="text-xs text-brand-solid whitespace-pre-wrap">{toolSpecStatus}</p>
              )}
            </div>
          </div>

          {/* Layer toggles - colored rows matching context_engineering_v2 diagram */}
          <div>
            <SectionLabel>Context Layers</SectionLabel>
            <div className="space-y-1">
              {/* Memory layer - Yellow/Gold */}
              <label
                className={cn(
                  'flex items-center gap-3 px-3 py-2 cursor-pointer transition-all',
                  includeMemory
                    ? 'bg-[#4D4318]'
                    : 'bg-gray-dark/50 opacity-60'
                )}
              >
                <input
                  type="checkbox"
                  checked={includeMemory}
                  onChange={(e) => setIncludeMemory(e.target.checked)}
                  className="sr-only"
                />
                <div
                  className={cn(
                    'w-4 h-4 rounded-sm border-2 flex items-center justify-center transition-colors',
                    includeMemory ? 'bg-[#E5CA59] border-[#E5CA59]' : 'border-white/40'
                  )}
                >
                  {includeMemory && (
                    <svg width="10" height="10" viewBox="0 0 24 24" fill="none">
                      <path d="M5 13L9 17L19 7" stroke="#1a1a1a" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round"/>
                    </svg>
                  )}
                </div>
                <div className="flex-1">
                  <span className={cn('text-sm font-medium', includeMemory ? 'text-[#E5CA59]' : 'text-white')}>Memory</span>
                  <span className="text-xs text-white/40 ml-2">LTM, Scratchpad, Regular</span>
                </div>
              </label>

              {/* Knowledge layer - Blue */}
              <label
                className={cn(
                  'flex items-center gap-3 px-3 py-2 cursor-pointer transition-all',
                  includeKnowledge
                    ? 'bg-[#243D4D]'
                    : 'bg-gray-dark/50 opacity-60'
                )}
              >
                <input
                  type="checkbox"
                  checked={includeKnowledge}
                  onChange={(e) => setIncludeKnowledge(e.target.checked)}
                  className="sr-only"
                />
                <div
                  className={cn(
                    'w-4 h-4 rounded-sm border-2 flex items-center justify-center transition-colors',
                    includeKnowledge ? 'bg-[#64ABDE] border-[#64ABDE]' : 'border-white/40'
                  )}
                >
                  {includeKnowledge && (
                    <svg width="10" height="10" viewBox="0 0 24 24" fill="none">
                      <path d="M5 13L9 17L19 7" stroke="#1a1a1a" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round"/>
                    </svg>
                  )}
                </div>
                <div className="flex-1">
                  <span className={cn('text-sm font-medium', includeKnowledge ? 'text-[#64ABDE]' : 'text-white')}>Knowledge</span>
                  <span className="text-xs text-white/40 ml-2">Files, Dialogue Examples</span>
                </div>
              </label>

              {/* Tools layer - Purple */}
              <label
                className={cn(
                  'flex items-center gap-3 px-3 py-2 cursor-pointer transition-all',
                  includeTools
                    ? 'bg-[#3D1653]'
                    : 'bg-gray-dark/50 opacity-60'
                )}
              >
                <input
                  type="checkbox"
                  checked={includeTools}
                  onChange={(e) => setIncludeTools(e.target.checked)}
                  className="sr-only"
                />
                <div
                  className={cn(
                    'w-4 h-4 rounded-sm border-2 flex items-center justify-center transition-colors',
                    includeTools ? 'bg-[#BC54F8] border-[#BC54F8]' : 'border-white/40'
                  )}
                >
                  {includeTools && (
                    <svg width="10" height="10" viewBox="0 0 24 24" fill="none">
                      <path d="M5 13L9 17L19 7" stroke="#1a1a1a" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round"/>
                    </svg>
                  )}
                </div>
                <div className="flex-1">
                  <span className={cn('text-sm font-medium', includeTools ? 'text-[#BC54F8]' : 'text-white')}>Tools</span>
                  <span className="text-xs text-white/40 ml-2">Developer-supplied tools</span>
                </div>
              </label>

              {/* Behaviors layer - Green */}
              <label
                className={cn(
                  'flex items-center gap-3 px-3 py-2 cursor-pointer transition-all',
                  includeBehaviors
                    ? 'bg-[#334130]'
                    : 'bg-gray-dark/50 opacity-60'
                )}
              >
                <input
                  type="checkbox"
                  checked={includeBehaviors}
                  onChange={(e) => setIncludeBehaviors(e.target.checked)}
                  className="sr-only"
                />
                <div
                  className={cn(
                    'w-4 h-4 rounded-sm border-2 flex items-center justify-center transition-colors',
                    includeBehaviors ? 'bg-[#85CD75] border-[#85CD75]' : 'border-white/40'
                  )}
                >
                  {includeBehaviors && (
                    <svg width="10" height="10" viewBox="0 0 24 24" fill="none">
                      <path d="M5 13L9 17L19 7" stroke="#1a1a1a" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round"/>
                    </svg>
                  )}
                </div>
                <div className="flex-1">
                  <span className={cn('text-sm font-medium', includeBehaviors ? 'text-[#85CD75]' : 'text-white')}>Behaviors</span>
                  <span className="text-xs text-white/40 ml-2">Developer-defined logic</span>
                </div>
              </label>
            </div>
          </div>

          {/* Intent Classifier */}
          <div>
            <SectionLabel>Intent Classifier</SectionLabel>
            <div className="space-y-2">
              {/* Classifier toggle */}
              <label
                className={cn(
                  'flex items-center gap-3 px-3 py-2 cursor-pointer transition-all',
                  useClassifier
                    ? 'bg-[#4D3D18]'
                    : 'bg-gray-dark/50 opacity-60'
                )}
              >
                <input
                  type="checkbox"
                  checked={useClassifier}
                  onChange={(e) => setUseClassifier(e.target.checked)}
                  className="sr-only"
                />
                <div
                  className={cn(
                    'w-4 h-4 rounded-sm border-2 flex items-center justify-center transition-colors',
                    useClassifier ? 'bg-[#F5A623] border-[#F5A623]' : 'border-white/40'
                  )}
                >
                  {useClassifier && (
                    <svg width="10" height="10" viewBox="0 0 24 24" fill="none">
                      <path d="M5 13L9 17L19 7" stroke="#1a1a1a" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round"/>
                    </svg>
                  )}
                </div>
                <div className="flex-1">
                  <span className={cn('text-sm font-medium', useClassifier ? 'text-[#F5A623]' : 'text-white')}>Enable Classifier</span>
                  <span className="text-xs text-white/40 ml-2">LLM-based layer routing</span>
                </div>
              </label>

              {/* Classifier model selector - only visible when classifier enabled */}
              {useClassifier && (
                <div className="pl-7">
                  <Dropdown
                    options={CLASSIFIER_MODELS}
                    value={classifierModel}
                    onChange={setClassifierModel}
                  />
                </div>
              )}

              {/* Always run toggles - only visible when classifier enabled */}
              {useClassifier && (
                <div className="pl-7 mt-3">
                  <span className="text-xs text-white/40 mb-2 block">Always run (bypass classifier):</span>
                  <div className="flex flex-wrap gap-2">
                    <label className={cn(
                      'flex items-center gap-1.5 px-2 py-1 cursor-pointer transition-all rounded text-xs',
                      alwaysRunMemory ? 'bg-[#4D4318] text-[#E5CA59]' : 'bg-gray-dark/50 text-white/60'
                    )}>
                      <input
                        type="checkbox"
                        checked={alwaysRunMemory}
                        onChange={(e) => setAlwaysRunMemory(e.target.checked)}
                        className="sr-only"
                      />
                      <div className={cn(
                        'w-3 h-3 rounded-sm border flex items-center justify-center',
                        alwaysRunMemory ? 'bg-[#E5CA59] border-[#E5CA59]' : 'border-white/40'
                      )}>
                        {alwaysRunMemory && (
                          <svg width="8" height="8" viewBox="0 0 24 24" fill="none">
                            <path d="M5 13L9 17L19 7" stroke="#1a1a1a" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round"/>
                          </svg>
                        )}
                      </div>
                      Memory
                    </label>

                    <label className={cn(
                      'flex items-center gap-1.5 px-2 py-1 cursor-pointer transition-all rounded text-xs',
                      alwaysRunKnowledge ? 'bg-[#243D4D] text-[#64ABDE]' : 'bg-gray-dark/50 text-white/60'
                    )}>
                      <input
                        type="checkbox"
                        checked={alwaysRunKnowledge}
                        onChange={(e) => setAlwaysRunKnowledge(e.target.checked)}
                        className="sr-only"
                      />
                      <div className={cn(
                        'w-3 h-3 rounded-sm border flex items-center justify-center',
                        alwaysRunKnowledge ? 'bg-[#64ABDE] border-[#64ABDE]' : 'border-white/40'
                      )}>
                        {alwaysRunKnowledge && (
                          <svg width="8" height="8" viewBox="0 0 24 24" fill="none">
                            <path d="M5 13L9 17L19 7" stroke="#1a1a1a" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round"/>
                          </svg>
                        )}
                      </div>
                      Knowledge
                    </label>

                    <label className={cn(
                      'flex items-center gap-1.5 px-2 py-1 cursor-pointer transition-all rounded text-xs',
                      alwaysRunTools ? 'bg-[#3D1653] text-[#BC54F8]' : 'bg-gray-dark/50 text-white/60'
                    )}>
                      <input
                        type="checkbox"
                        checked={alwaysRunTools}
                        onChange={(e) => setAlwaysRunTools(e.target.checked)}
                        className="sr-only"
                      />
                      <div className={cn(
                        'w-3 h-3 rounded-sm border flex items-center justify-center',
                        alwaysRunTools ? 'bg-[#BC54F8] border-[#BC54F8]' : 'border-white/40'
                      )}>
                        {alwaysRunTools && (
                          <svg width="8" height="8" viewBox="0 0 24 24" fill="none">
                            <path d="M5 13L9 17L19 7" stroke="#1a1a1a" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round"/>
                          </svg>
                        )}
                      </div>
                      Tools
                    </label>

                    <label className={cn(
                      'flex items-center gap-1.5 px-2 py-1 cursor-pointer transition-all rounded text-xs',
                      alwaysRunBehaviors ? 'bg-[#334130] text-[#85CD75]' : 'bg-gray-dark/50 text-white/60'
                    )}>
                      <input
                        type="checkbox"
                        checked={alwaysRunBehaviors}
                        onChange={(e) => setAlwaysRunBehaviors(e.target.checked)}
                        className="sr-only"
                      />
                      <div className={cn(
                        'w-3 h-3 rounded-sm border flex items-center justify-center',
                        alwaysRunBehaviors ? 'bg-[#85CD75] border-[#85CD75]' : 'border-white/40'
                      )}>
                        {alwaysRunBehaviors && (
                          <svg width="8" height="8" viewBox="0 0 24 24" fill="none">
                            <path d="M5 13L9 17L19 7" stroke="#1a1a1a" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round"/>
                          </svg>
                        )}
                      </div>
                      Behaviors
                    </label>
                  </div>
                </div>
              )}
            </div>
          </div>

          {/* Model selector */}
          <div>
            <SectionLabel>Model</SectionLabel>
            <Dropdown
              options={MODELS}
              value={selectedModel}
              onChange={setSelectedModel}
              className="rounded-none"
            />
          </div>

          {/* Max Output Tokens */}
          <div>
            <SectionLabel>Max Output Tokens</SectionLabel>
            <div className="flex items-center gap-2">
              <input
                type="number"
                value={maxOutputTokens ?? ''}
                onChange={(e) => {
                  const val = e.target.value;
                  setMaxOutputTokens(val ? parseInt(val, 10) : null);
                }}
                placeholder={companionMaxOutputTokens ? `${companionMaxOutputTokens} (companion default)` : '4096 (default)'}
                min={1}
                max={16384}
                className="flex-1 bg-gray-dark text-white text-sm rounded-none px-3 py-2 border border-white/20 focus:outline-none focus:border-white/40 placeholder:text-white/30 [appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none"
              />
              {maxOutputTokens !== companionMaxOutputTokens && companionMaxOutputTokens && (
                <button
                  onClick={() => setMaxOutputTokens(companionMaxOutputTokens)}
                  className="text-xs text-white/40 hover:text-white/60 transition-colors"
                  title="Reset to companion default"
                >
                  Reset
                </button>
              )}
            </div>
            {companionMaxOutputTokens && (
              <p className="text-[10px] text-white/40 mt-1">
                Companion config: {companionMaxOutputTokens} tokens
              </p>
            )}
          </div>

          {/* Registered Behaviors */}
          {selectedCompanionId && (
            <div>
              <div className="flex items-center justify-between mb-1.5">
                <SectionLabel>Registered Behaviors ({visibleCompanionBehaviors.length})</SectionLabel>
              </div>
              {visibleCompanionBehaviors.length > 0 ? (
                <div className="space-y-1">
                  {visibleCompanionBehaviors.map((behavior) => (
                    <div
                      key={behavior.id}
                      className={cn(
                        'px-3 py-2 rounded-none border transition-all',
                        behavior.enabled
                          ? 'bg-[#334130] border-[#334130]'
                          : 'bg-gray-dark/50 border-white/10 opacity-60'
                      )}
                    >
                      <div className="flex items-center justify-between">
                        <span className={cn('text-sm font-medium', behavior.enabled ? 'text-[#85CD75]' : 'text-white/60')}>
                          {behavior.name}
                        </span>
                        {behavior.priority === 'sync' && (
                          <span className="text-[9px] px-1 py-0.5 bg-yellow-500/20 text-yellow-400 rounded-none">
                            Sync
                          </span>
                        )}
                      </div>
                      <div className="text-[10px] text-white/40 mt-0.5">{behavior.key}</div>
                      {behavior.triggers.length > 0 && (
                        <div className="flex flex-wrap gap-1 mt-1">
                          {behavior.triggers.slice(0, 2).map((trigger, i) => (
                            <span key={i} className="text-[9px] px-1 py-0.5 bg-white/10 rounded-none text-white/60">
                              {formatTrigger(trigger)}
                            </span>
                          ))}
                          {behavior.triggers.length > 2 && (
                            <span className="text-[9px] text-white/40">+{behavior.triggers.length - 2}</span>
                          )}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              ) : (
                <div className="text-xs text-white/40 text-center py-3 bg-gray-dark/30 rounded-none border border-dashed border-white/10">
                  No linked behaviors found for this companion.
                </div>
              )}
            </div>
          )}
        </div>

        {/* Run button - fixed at bottom */}
        <div className="p-4 border-t border-white/10 shrink-0 bg-gray-darker">
          {error && (
            <div className="mb-3 p-2 bg-brand-bg text-brand-solid text-xs rounded">
              {error}
            </div>
          )}
          <button
            onClick={runTest}
            disabled={
              isLoading ||
              !selectedCompanionId ||
              (includeProfileInPrompt && !!profileValidation.error)
            }
            className={cn(
              'w-full py-2.5 rounded-full font-medium text-sm transition-colors',
              isLoading ||
                !selectedCompanionId ||
                (includeProfileInPrompt && !!profileValidation.error)
                ? 'bg-gray-button text-white/40 cursor-not-allowed'
                : 'bg-brand-solid text-white hover:bg-brand-solid-hover'
            )}
          >
            {isLoading ? 'Running...' : 'Run Test'}
          </button>
        </div>
      </div>

      {/* Right panel - Outputs */}
      <div className="flex-1 flex flex-col overflow-hidden">
        {/* Test Toolbar */}
        <TestToolbar
          savedTests={savedTests}
          selectedTestId={selectedTestId}
          onSelectTest={handleSelectTest}
          onSaveTest={() => setIsSaveModalOpen(true)}
          onSaveAsTest={() => setIsSaveModalOpen(true)}
          onUpdateTest={updateTest}
          onDeleteTest={deleteTest}
          onRunAll={runAllTests}
          isRunningAll={isRunningAll}
          runAllProgress={runAllProgress}
          testResults={testResults}
          currentResultIndex={currentResultIndex}
          onNavigateResult={handleNavigateResult}
          disabled={!selectedCompanionId || isLoading}
          isSaving={isSavingTest}
          justSaved={justSaved}
        />

        {/* Output Panels */}
        <div className="flex-1 flex p-4 gap-4 overflow-hidden">
          <OutputPanel
            title="Raw Mode"
            output={displayedRawOutput}
            events={displayedRawEvents}
            isLoading={(isLoading || isRunningAll) && (currentMode === 'raw' || currentMode === null)}
            streamingResponse={rawStreamingResponse}
          />
          <OutputPanel
            title="Layered Mode"
            output={displayedLayeredOutput}
            events={displayedLayeredEvents}
            isLoading={(isLoading || isRunningAll) && currentMode === 'layered'}
            streamingResponse={layeredStreamingResponse}
          />
        </div>
      </div>

      {/* Save Test Modal */}
      <SaveTestModal
        isOpen={isSaveModalOpen}
        onClose={() => setIsSaveModalOpen(false)}
        onSave={saveTest}
        isSaving={isSavingTest}
      />
    </div>
  );
}
