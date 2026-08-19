'use client';

import { useState, useCallback, useEffect } from 'react';
import { useAuth } from '@clerk/nextjs';
import { useSearchParams } from 'next/navigation';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import Icon from '@/components/ui/icon';
import Dropdown from '@/components/ui/dropdown';
import { cn } from '@/lib/utils';
import CodeMirror from '@uiw/react-codemirror';
import { python } from '@codemirror/lang-python';
import { oneDark } from '@codemirror/theme-one-dark';
import { EditorView, keymap } from '@codemirror/view';
import { defaultKeymap, history, historyKeymap } from '@codemirror/commands';
import { API_CONFIG } from '@/lib/config';

const API_BASE = API_CONFIG.BASE_URL;

// Test result interface
interface TestBehaviorResult {
  success: boolean;
  prompt_block: string | null;
  effects: Array<{ type: string; [key: string]: unknown }>;
  trace: Record<string, unknown>;
  error: string | null;
  duration_ms: number;
  context_data?: Record<string, unknown>;
}

// Types
interface Companion {
  id: string;
  name: string;
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
  classifier_hint: string | null;
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

// Default behavior code template
const DEFAULT_BEHAVIOR_CODE = `async def execute(ctx):
    """
    Behavior entry point.

    Reading context:
      ctx.message          - The user's message
      ctx.turn_count       - Current turn number
      ctx.get_app_state(key, default)
      ctx.get_user_state(key, default)
      ctx.get_companion_state(key, default)

    Side effects (applied post-turn):
      ctx.set_app_state(key, value)
      ctx.set_user_state(key, value)
      ctx.set_companion_state(key, value)
      ctx.delete_app_state(key)
      ctx.write_memory(content, importance=0.5)
      ctx.notify_webhook(event_type, data)

    For sync (priority) behaviors:
      ctx.add_prompt_block(text)  - Inject into system prompt

    Return value:
      str  -> For sync behaviors, injected into system prompt
      None -> No prompt injection (effects still apply)
    """
    message = ctx.message.lower()

    # Example: detect mood and inject guidance into prompt
    if "sad" in message or "upset" in message:
        ctx.add_prompt_block("[User seems sad - respond with empathy]")

    if "happy" in message or "excited" in message:
        ctx.add_prompt_block("[User seems happy - share their enthusiasm]")

    # Track interaction count
    count = ctx.get_app_state("interaction_count", 0)
    ctx.set_app_state("interaction_count", count + 1)

    return None
`;

// Reusable components
function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <label className="text-white/60 text-xs font-medium uppercase tracking-wide mb-1.5 block">
      {children}
    </label>
  );
}

// Custom theme extension
const editorTheme = EditorView.theme({
  '&': {
    height: '100%',
    fontSize: '13px',
    backgroundColor: '#000000',
  },
  '.cm-scroller': {
    fontFamily: 'ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace',
    backgroundColor: '#000000',
  },
  '.cm-content': {
    padding: '12px 0',
    backgroundColor: '#000000',
  },
  '.cm-gutters': {
    backgroundColor: '#000000',
    borderRight: '1px solid rgba(255,255,255,0.1)',
  },
  '.cm-lineNumbers': {
    backgroundColor: '#000000',
  },
  '.cm-lineNumbers .cm-gutterElement': {
    color: 'rgba(255,255,255,0.3)',
    padding: '0 8px 0 12px',
    backgroundColor: '#000000',
  },
  '.cm-foldGutter': {
    backgroundColor: '#000000',
  },
  '.cm-foldGutter .cm-gutterElement': {
    backgroundColor: '#000000',
  },
  '.cm-activeLine': {
    backgroundColor: 'rgba(255,255,255,0.05)',
  },
  '.cm-activeLineGutter': {
    backgroundColor: 'rgba(255,255,255,0.05)',
  },
  '&.cm-focused .cm-cursor': {
    borderLeftColor: '#fff',
  },
  '&.cm-focused .cm-selectionBackground, .cm-selectionBackground, &.cm-focused .cm-content ::selection': {
    backgroundColor: 'rgba(100,149,237,0.4) !important',
  },
});

function CodeEditor({
  value,
  onChange,
  className,
}: {
  value: string;
  onChange: (value: string) => void;
  className?: string;
}) {
  return (
    <div className={cn('h-full rounded border border-white/20 overflow-hidden', className)}>
      <CodeMirror
        value={value}
        onChange={onChange}
        extensions={[
          python(),
          editorTheme,
          history(),
          keymap.of([...defaultKeymap, ...historyKeymap]),
        ]}
        theme={oneDark}
        basicSetup={{
          lineNumbers: true,
          highlightActiveLineGutter: true,
          highlightActiveLine: true,
          foldGutter: true,
          dropCursor: true,
          allowMultipleSelections: true,
          indentOnInput: true,
          bracketMatching: true,
          closeBrackets: true,
          autocompletion: true,
          rectangularSelection: true,
          crosshairCursor: false,
          highlightSelectionMatches: true,
          tabSize: 4,
        }}
        style={{ height: '100%' }}
      />
    </div>
  );
}

// Main Component
export default function BehaviorTesting() {
  const { getToken } = useAuth();
  const queryClient = useQueryClient();
  const searchParams = useSearchParams();

  // State
  const [selectedCompanion, setSelectedCompanion] = useState<string>('');
  const [initialCompanionSet, setInitialCompanionSet] = useState(false);
  const [sourceCode, setSourceCode] = useState(DEFAULT_BEHAVIOR_CODE);
  const [selectedBehavior, setSelectedBehavior] = useState<BehaviorInfo | null>(null);

  // Test state
  const [testResult, setTestResult] = useState<TestBehaviorResult | null>(null);
  const [isTesting, setIsTesting] = useState(false);
  const [mockMessage, setMockMessage] = useState('Hello, how are you?');
  const [mockTurnCount, setMockTurnCount] = useState(1);

  // Register state
  const [isRegistering, setIsRegistering] = useState(false);
  const [registerStatus, setRegisterStatus] = useState<'idle' | 'success' | 'error'>('idle');
  const [registerError, setRegisterError] = useState<string | null>(null);

  // Manage (unlink/delete) state
  const [manageStatus, setManageStatus] = useState<'idle' | 'unlinked' | 'deleted' | 'error'>('idle');
  const [manageError, setManageError] = useState<string | null>(null);
  const [behaviorKey, setBehaviorKey] = useState('');
  const [behaviorName, setBehaviorName] = useState('');
  const [behaviorDescription, setBehaviorDescription] = useState('');
  const [triggerInput, setTriggerInput] = useState('always');
  const [priority, setPriority] = useState<'sync' | 'async'>('async');
  const [enabled, setEnabled] = useState(true);
  const [classifierHint, setClassifierHint] = useState('');

  // Fetch companions
  const { data: companions = [] } = useQuery<Companion[]>({
    queryKey: ['companions'],
    queryFn: async () => {
      const token = await getToken();
      const res = await fetch(`${API_BASE}/api/companions`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!res.ok) throw new Error('Failed to fetch companions');
      return res.json();
    },
  });

  // Set companion from URL query parameter once companions are loaded
  useEffect(() => {
    if (initialCompanionSet || companions.length === 0) return;

    const companionId = searchParams.get('companion');
    if (companionId) {
      // Verify the companion exists in the user's companions
      const companionExists = companions.some(c => c.id === companionId);
      if (companionExists) {
        setSelectedCompanion(companionId);
      }
    }
    setInitialCompanionSet(true);
  }, [companions, searchParams, initialCompanionSet]);

  // Fetch behaviors linked to selected companion (not all project behaviors)
  const { data: companionBehaviors = [], isLoading: behaviorsLoading } = useQuery<BehaviorInfo[]>({
    queryKey: ['companion-behaviors', selectedCompanion],
    queryFn: async () => {
      if (!selectedCompanion) return [];
      const token = await getToken();
      const res = await fetch(`${API_BASE}/api/context-engine-testing/companions/${selectedCompanion}/behaviors?linked_only=true`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!res.ok) return [];
      const data = await res.json();
      return data.behaviors;
    },
    enabled: !!selectedCompanion,
  });

  // Load behavior into editor
  const loadBehavior = useCallback((behavior: BehaviorInfo) => {
    setSelectedBehavior(behavior);
    setSourceCode(behavior.source_code);
    setBehaviorKey(behavior.key);
    setBehaviorName(behavior.name);
    setBehaviorDescription(behavior.description || '');
    setTriggerInput(behavior.triggers.map(formatTrigger).join(', ') || 'always');
    setPriority(behavior.priority);
    setEnabled(behavior.enabled);
    setClassifierHint(behavior.classifier_hint || '');
    // Clear any manage errors from previous behavior
    setManageStatus('idle');
    setManageError(null);
  }, []);

  // Copy code to clipboard
  const copyToClipboard = useCallback(async () => {
    await navigator.clipboard.writeText(sourceCode);
  }, [sourceCode]);

  // Test behavior in sandbox
  const testBehavior = useCallback(async () => {
    setIsTesting(true);
    setTestResult(null);
    try {
      const token = await getToken();
      const res = await fetch(`${API_BASE}/api/context-engine-testing/behaviors/test`, {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          source_code: sourceCode,
          mock_message: mockMessage,
          mock_turn_count: mockTurnCount,
          mock_app_state: {},
          mock_user_state: {},
          mock_companion_state: {},
        }),
      });
      const result = await res.json();
      setTestResult(result);
    } catch (error) {
      setTestResult({
        success: false,
        error: error instanceof Error ? error.message : 'Unknown error',
        prompt_block: null,
        effects: [],
        trace: {},
        duration_ms: 0,
      });
    } finally {
      setIsTesting(false);
    }
  }, [getToken, sourceCode, mockMessage, mockTurnCount]);

  // Register behavior
  const registerBehavior = useCallback(async () => {
    if (!selectedCompanion || !behaviorKey || !behaviorName) {
      return;
    }
    setIsRegistering(true);
    setRegisterStatus('idle');
    setRegisterError(null);
    try {
      const token = await getToken();
      const triggers = triggerInput.split(',').map(t => t.trim()).filter(Boolean);
      const res = await fetch(`${API_BASE}/api/context-engine-testing/behaviors/register`, {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          companion_id: selectedCompanion,
          key: behaviorKey,
          name: behaviorName,
          description: behaviorDescription || null,
          source_code: sourceCode,
          triggers,
          priority,
          enabled,
          classifier_hint: classifierHint || null,
        }),
      });
      if (res.ok) {
        setRegisterStatus('success');
        // Refresh behaviors list
        queryClient.invalidateQueries({ queryKey: ['companion-behaviors', selectedCompanion] });
        // Auto-clear success after 3s
        setTimeout(() => setRegisterStatus('idle'), 3000);
      } else {
        const errorData = await res.json().catch(() => ({}));
        setRegisterStatus('error');
        setRegisterError(errorData.detail || `Error ${res.status}`);
      }
    } catch (err) {
      setRegisterStatus('error');
      setRegisterError(err instanceof Error ? err.message : 'Unknown error');
    } finally {
      setIsRegistering(false);
    }
  }, [getToken, selectedCompanion, behaviorKey, behaviorName, behaviorDescription, sourceCode, triggerInput, priority, enabled, classifierHint, queryClient]);

  // Helper to clear behavior selection
  const clearBehaviorSelection = useCallback(() => {
    setSelectedBehavior(null);
    setSourceCode(DEFAULT_BEHAVIOR_CODE);
    setBehaviorKey('');
    setBehaviorName('');
    setBehaviorDescription('');
    setTriggerInput('always');
    setPriority('async');
    setEnabled(true);
    setClassifierHint('');
  }, []);

  // Unlink behavior from companion (keeps behavior in project)
  const unlinkBehavior = useCallback(async () => {
    if (!selectedCompanion || !selectedBehavior) return;
    setManageStatus('idle');
    setManageError(null);
    try {
      const token = await getToken();
      const res = await fetch(
        `${API_BASE}/api/context-engine-testing/companions/${selectedCompanion}/behaviors/${selectedBehavior.key}`,
        {
          method: 'DELETE',
          headers: { Authorization: `Bearer ${token}` },
        }
      );
      if (res.ok) {
        const data = await res.json();
        // Check if it was actually linked (idempotent endpoint)
        if (data.message?.includes('was not linked')) {
          setManageStatus('error');
          setManageError('Behavior is not linked to this companion');
        } else {
          setManageStatus('unlinked');
          queryClient.invalidateQueries({ queryKey: ['companion-behaviors', selectedCompanion] });
          clearBehaviorSelection();
        }
      } else {
        const data = await res.json().catch(() => ({}));
        setManageStatus('error');
        setManageError(data.detail || 'Failed to unlink behavior');
      }
    } catch (err) {
      console.error('Failed to unlink behavior:', err);
      setManageStatus('error');
      setManageError('Network error');
    }
  }, [getToken, selectedCompanion, selectedBehavior, queryClient, clearBehaviorSelection]);

  // Delete behavior entirely from project
  const deleteBehavior = useCallback(async () => {
    if (!selectedCompanion || !selectedBehavior) return;
    setManageStatus('idle');
    setManageError(null);
    try {
      const token = await getToken();
      const res = await fetch(
        `${API_BASE}/api/context-engine-testing/behaviors/${selectedBehavior.key}?companion_id=${selectedCompanion}`,
        {
          method: 'DELETE',
          headers: { Authorization: `Bearer ${token}` },
        }
      );
      if (res.ok) {
        setManageStatus('deleted');
        queryClient.invalidateQueries({ queryKey: ['companion-behaviors', selectedCompanion] });
        clearBehaviorSelection();
      } else if (res.status === 404) {
        setManageStatus('error');
        setManageError('Behavior not found in project');
      } else {
        const data = await res.json().catch(() => ({}));
        setManageStatus('error');
        setManageError(data.detail || 'Failed to delete behavior');
      }
    } catch (err) {
      console.error('Failed to delete behavior:', err);
      setManageStatus('error');
      setManageError('Network error');
    }
  }, [getToken, selectedCompanion, selectedBehavior, queryClient, clearBehaviorSelection]);

  return (
    <div className="h-screen bg-black text-white flex overflow-hidden">
      {/* Left Sidebar - Behaviors List */}
      <div className="w-80 shrink-0 bg-gray-darker border-r border-white/10 flex flex-col">
        <div className="p-4 border-b border-white/10 shrink-0">
          <h1 className="text-[32px] font-light tracking-[-0.04em]">Behavior Testing</h1>
          <p className="text-xs text-white/40">
            Write and manage behaviors for companions
          </p>
        </div>

        <div className="p-4 space-y-3 border-b border-white/10">
          <div>
            <SectionLabel>Companion</SectionLabel>
            <Dropdown
              options={companions.map(c => ({ value: c.id, label: c.name }))}
              value={selectedCompanion}
              onChange={(value) => {
                setSelectedCompanion(value);
                setSelectedBehavior(null);
              }}
              placeholder="Select companion..."
            />
          </div>

          <button
            onClick={() => {
              setSelectedBehavior(null);
              setSourceCode(DEFAULT_BEHAVIOR_CODE);
            }}
            className="w-full px-4 py-2 bg-green-solid hover:bg-green-solid-hover text-black rounded text-sm font-medium transition-colors inline-flex items-center justify-center gap-1.5"
          >
            <Icon name="plus" size={14} color="currentColor" />
            <span>New Behavior</span>
          </button>
        </div>

        {/* Registered Behaviors */}
        <div className="flex-1 overflow-y-auto p-4">
          <SectionLabel>Project Behaviors ({companionBehaviors.length})</SectionLabel>
          {behaviorsLoading ? (
            <div className="text-xs text-white/40 text-center py-4">Loading...</div>
          ) : (
            <div className="space-y-2 mt-2">
              {companionBehaviors.map((behavior) => (
                <button
                  key={behavior.id}
                  onClick={() => loadBehavior(behavior)}
                  className={cn(
                    'w-full text-left p-3 rounded border transition-colors',
                    selectedBehavior?.id === behavior.id
                      ? 'border-green-solid bg-green-solid/10'
                      : 'border-white/10 hover:border-white/30 bg-gray-dark'
                  )}
                >
                  <div className="flex items-center justify-between">
                    <span className="text-sm font-medium">{behavior.name}</span>
                    <div className="flex items-center gap-1">
                      {behavior.priority === 'sync' && (
                        <span className="text-[10px] px-1.5 py-0.5 bg-yellow-500/20 text-yellow-400 rounded">
                          Sync
                        </span>
                      )}
                      {behavior.enabled ? (
                        <span className="text-[10px] px-1.5 py-0.5 bg-green-solid/20 text-green-solid rounded">
                          On
                        </span>
                      ) : (
                        <span className="text-[10px] px-1.5 py-0.5 bg-white/10 text-white/40 rounded">
                          Off
                        </span>
                      )}
                    </div>
                  </div>
                  <div className="text-xs text-white/50 mt-0.5">{behavior.key}</div>
                  {behavior.triggers.length > 0 && (
                    <div className="flex flex-wrap gap-1 mt-1.5">
                      {behavior.triggers.slice(0, 3).map((trigger, i) => (
                        <span
                          key={i}
                          className="text-[10px] px-1.5 py-0.5 bg-white/10 rounded"
                        >
                          {formatTrigger(trigger)}
                        </span>
                      ))}
                      {behavior.triggers.length > 3 && (
                        <span className="text-[10px] text-white/40">
                          +{behavior.triggers.length - 3}
                        </span>
                      )}
                    </div>
                  )}
                </button>
              ))}
              {selectedCompanion && companionBehaviors.length === 0 && (
                <p className="text-xs text-white/40 text-center py-4">
                  No behaviors registered for this project
                </p>
              )}
              {!selectedCompanion && (
                <p className="text-xs text-white/40 text-center py-4">
                  Select a companion to view behaviors
                </p>
              )}
            </div>
          )}
        </div>

        {/* Action Buttons */}
        <div className="p-4 border-t border-white/10 space-y-2">
          <button
            onClick={copyToClipboard}
            className="w-full px-4 py-2.5 bg-white/10 hover:bg-white/20 text-white rounded text-sm font-medium transition-colors inline-flex items-center justify-center gap-1.5"
          >
            <Icon name="link" size={14} color="currentColor" />
            <span>Copy Code</span>
          </button>
          <a
            href="/test-context-engine"
            className="w-full px-4 py-2.5 bg-yellow-solid hover:bg-yellow-solid-hover text-black rounded text-sm font-medium transition-colors inline-flex items-center justify-center gap-1.5"
          >
            <Icon name="play" size={14} color="currentColor" />
            <span>Test in Context Engine</span>
          </a>
        </div>
      </div>

      {/* Main Content - Code Editor */}
      <div className="flex-1 flex flex-col min-h-0 min-w-0">
        <div className="px-4 py-3 border-b border-white/10 shrink-0 flex items-center justify-between">
          <div>
            <span className="text-xs text-white/60 font-medium uppercase tracking-wide">
              {selectedBehavior ? `${selectedBehavior.name} (${selectedBehavior.key})` : 'New Behavior'}
            </span>
            {selectedBehavior && (
              <span className="text-xs text-white/40 ml-2">v{selectedBehavior.version}</span>
            )}
          </div>
        </div>
        <div className="flex-1 p-4 overflow-hidden min-h-0">
          <CodeEditor value={sourceCode} onChange={setSourceCode} />
        </div>
      </div>

      {/* Right Panel - Test & Register */}
      <div className="w-96 shrink-0 bg-gray-darker border-l border-white/10 flex flex-col overflow-hidden">
        <div className="p-4 border-b border-white/10">
          <h2 className="text-sm font-medium">Test & Register</h2>
          <p className="text-xs text-white/40 mt-1">
            Test behaviors in sandbox and register to companion
          </p>
        </div>

        <div className="flex-1 overflow-y-auto p-4 space-y-4">
          {/* Test Section */}
          <div className="space-y-3">
            <SectionLabel>Test in Sandbox</SectionLabel>
            <div className="space-y-2">
              <div>
                <label className="text-xs text-white/50 block mb-1">Mock Message</label>
                <input
                  type="text"
                  value={mockMessage}
                  onChange={(e) => setMockMessage(e.target.value)}
                  className="w-full bg-gray-dark text-white text-sm rounded px-3 py-2 border border-white/20 focus:outline-none focus:border-white/40"
                  placeholder="User message..."
                />
              </div>
              <div>
                <label className="text-xs text-white/50 block mb-1">Turn Count</label>
                <input
                  type="number"
                  value={mockTurnCount}
                  onChange={(e) => setMockTurnCount(parseInt(e.target.value) || 0)}
                  className="w-full bg-gray-dark text-white text-sm rounded px-3 py-2 border border-white/20 focus:outline-none focus:border-white/40"
                  min={0}
                />
              </div>
            </div>
            <button
              onClick={testBehavior}
              disabled={isTesting}
              className="w-full px-4 py-2.5 bg-blue-600 hover:bg-blue-700 disabled:bg-blue-800 disabled:opacity-50 text-white rounded text-sm font-medium transition-colors inline-flex items-center justify-center gap-1.5"
            >
              <Icon name="play" size={14} color="currentColor" />
              <span>{isTesting ? 'Testing...' : 'Test in Sandbox'}</span>
            </button>

            {/* Test Results */}
            {testResult && (
              <div className={cn(
                'p-3 rounded border text-xs',
                testResult.success
                  ? 'bg-green-solid/10 border-green-solid/30'
                  : 'bg-red-500/10 border-red-500/30'
              )}>
                <div className="flex items-center justify-between mb-2">
                  <span className={testResult.success ? 'text-green-400' : 'text-red-400'}>
                    {testResult.success ? '✓ Success' : '✗ Failed'}
                  </span>
                  <span className="text-white/40">{testResult.duration_ms.toFixed(0)}ms</span>
                </div>
                {testResult.error && (
                  <div className="text-red-400 font-mono text-xs break-all">{testResult.error}</div>
                )}
                {testResult.prompt_block && (
                  <div className="mt-2">
                    <span className="text-white/60">Prompt Block:</span>
                    <pre className="mt-1 p-2 bg-black/50 rounded text-white/80 whitespace-pre-wrap">{testResult.prompt_block}</pre>
                  </div>
                )}
                {testResult.effects.length > 0 && (
                  <div className="mt-2">
                    <span className="text-white/60">Effects ({testResult.effects.length}):</span>
                    <pre className="mt-1 p-2 bg-black/50 rounded text-white/80 whitespace-pre-wrap max-h-24 overflow-y-auto">
                      {JSON.stringify(testResult.effects, null, 2)}
                    </pre>
                  </div>
                )}
              </div>
            )}
          </div>

          <div className="border-t border-white/10 my-4" />

          {/* Register Section */}
          <div className="space-y-3">
            <SectionLabel>Register Behavior</SectionLabel>
            <div className="space-y-2">
              <div>
                <label className="text-xs text-white/50 block mb-1">Key (unique ID)</label>
                <input
                  type="text"
                  value={behaviorKey}
                  onChange={(e) => setBehaviorKey(e.target.value)}
                  className="w-full bg-gray-dark text-white text-sm rounded px-3 py-2 border border-white/20 focus:outline-none focus:border-white/40"
                  placeholder="my_behavior"
                />
              </div>
              <div>
                <label className="text-xs text-white/50 block mb-1">Name</label>
                <input
                  type="text"
                  value={behaviorName}
                  onChange={(e) => setBehaviorName(e.target.value)}
                  className="w-full bg-gray-dark text-white text-sm rounded px-3 py-2 border border-white/20 focus:outline-none focus:border-white/40"
                  placeholder="My Behavior"
                />
              </div>
              <div>
                <label className="text-xs text-white/50 block mb-1">Description (optional)</label>
                <input
                  type="text"
                  value={behaviorDescription}
                  onChange={(e) => setBehaviorDescription(e.target.value)}
                  className="w-full bg-gray-dark text-white text-sm rounded px-3 py-2 border border-white/20 focus:outline-none focus:border-white/40"
                  placeholder="What this behavior does..."
                />
              </div>
              <div>
                <label className="text-xs text-white/50 block mb-1">Classifier Hint (optional)</label>
                <input
                  type="text"
                  value={classifierHint}
                  onChange={(e) => setClassifierHint(e.target.value)}
                  className="w-full bg-gray-dark text-white text-sm rounded px-3 py-2 border border-white/20 focus:outline-none focus:border-white/40"
                  placeholder="When should classifier trigger this?"
                />
              </div>
              <div>
                <label className="text-xs text-white/50 block mb-1">Triggers (comma-separated)</label>
                <input
                  type="text"
                  value={triggerInput}
                  onChange={(e) => setTriggerInput(e.target.value)}
                  className="w-full bg-gray-dark text-white text-sm rounded px-3 py-2 border border-white/20 focus:outline-none focus:border-white/40"
                  placeholder="always, keyword:help"
                />
              </div>
              <div className="flex gap-2">
                <div className="flex-1">
                  <label className="text-xs text-white/50 block mb-1">Priority</label>
                  <Dropdown
                    options={[
                      { value: 'async', label: 'Async' },
                      { value: 'sync', label: 'Sync' },
                    ]}
                    value={priority}
                    onChange={(value) => setPriority(value as 'sync' | 'async')}
                  />
                </div>
                <div className="flex-1">
                  <label className="text-xs text-white/50 block mb-1">Enabled</label>
                  <button
                    onClick={() => setEnabled(!enabled)}
                    className={cn(
                      'w-full px-3 py-2 rounded text-sm border transition-colors',
                      enabled
                        ? 'bg-green-solid/20 border-green-solid/40 text-green-400'
                        : 'bg-gray-dark border-white/20 text-white/60'
                    )}
                  >
                    {enabled ? 'On' : 'Off'}
                  </button>
                </div>
              </div>
            </div>
            <button
              onClick={registerBehavior}
              disabled={isRegistering || !selectedCompanion || !behaviorKey || !behaviorName}
              className="w-full px-4 py-2.5 bg-green-solid hover:bg-green-solid-hover disabled:bg-green-solid/50 disabled:opacity-50 text-black rounded text-sm font-medium transition-colors inline-flex items-center justify-center gap-1.5"
            >
              <Icon name="check" size={14} color="currentColor" />
              <span>{isRegistering ? 'Registering...' : 'Register to Companion'}</span>
            </button>
            {!selectedCompanion && (
              <p className="text-xs text-white/40 text-center">Select a companion first</p>
            )}
            {registerStatus === 'success' && (
              <p className="text-xs text-green-400 text-center">✓ Behavior registered successfully!</p>
            )}
            {registerStatus === 'error' && (
              <p className="text-xs text-red-400 text-center">✗ {registerError}</p>
            )}
          </div>

          {/* Manage Behavior Section - only show when a behavior is selected */}
          {selectedBehavior && (
            <>
              <div className="border-t border-white/10 my-4" />
              <div className="space-y-3">
                <SectionLabel>Manage Behavior</SectionLabel>
                <p className="text-xs text-white/40">
                  Selected: <span className="text-white/70">{selectedBehavior.key}</span>
                </p>
                <div className="space-y-2">
                  <button
                    onClick={unlinkBehavior}
                    className="w-full px-4 py-2 bg-gray-dark hover:bg-gray-700 border border-white/20 hover:border-white/30 text-white/80 hover:text-white rounded text-sm transition-colors inline-flex items-center justify-center gap-2"
                  >
                    <Icon name="unlink" size={14} color="currentColor" />
                    <span>Unlink from Companion</span>
                  </button>
                  <button
                    onClick={() => {
                      if (confirm(`Delete behavior "${selectedBehavior.key}" from project? This removes the behavior and all its links.`)) {
                        deleteBehavior();
                      }
                    }}
                    className="w-full px-4 py-2 bg-red-500/10 hover:bg-red-500/20 border border-red-500/30 hover:border-red-500/50 text-red-400 hover:text-red-300 rounded text-sm transition-colors inline-flex items-center justify-center gap-2"
                  >
                    <Icon name="trash" size={14} color="currentColor" />
                    <span>Delete from Project</span>
                  </button>
                </div>
                {manageStatus === 'error' && manageError && (
                  <p className="text-xs text-red-400 text-center">✗ {manageError}</p>
                )}
                <p className="text-xs text-white/30">
                  Unlink removes this behavior from the companion. Delete removes it from the entire project.
                </p>
              </div>
            </>
          )}

          <div className="border-t border-white/10 my-4" />

          {/* Quick Reference */}
          <div>
            <SectionLabel>Trigger Options</SectionLabel>
            <div className="space-y-1.5 text-xs text-white/60">
              <div className="flex items-start gap-2">
                <code className="px-1.5 py-0.5 bg-white/10 rounded shrink-0">always</code>
                <span>Every turn</span>
              </div>
              <div className="flex items-start gap-2">
                <code className="px-1.5 py-0.5 bg-white/10 rounded shrink-0">every_n:5</code>
                <span>Every 5th turn</span>
              </div>
              <div className="flex items-start gap-2">
                <code className="px-1.5 py-0.5 bg-white/10 rounded shrink-0">turn_count:1</code>
                <span>Specific turn</span>
              </div>
              <div className="flex items-start gap-2">
                <code className="px-1.5 py-0.5 bg-white/10 rounded shrink-0">keyword:help</code>
                <span>Keyword match</span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
