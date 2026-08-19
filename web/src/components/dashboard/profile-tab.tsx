'use client';

import { useState, useEffect, useCallback } from 'react';
import FormSection from '@/components/ui/form-section';
import { useCompanion, useUpdateCompanion } from '@/hooks/useCompanions';
import CodeMirror from '@uiw/react-codemirror';
import { EditorView, keymap } from '@codemirror/view';
import { defaultKeymap, history, historyKeymap } from '@codemirror/commands';

// Custom theme extension (adapted from behavior-testing)
const editorTheme = EditorView.theme({
  '&': {
    fontSize: '13px',
    backgroundColor: '#000000',
    height: '100%',
  },
  '.cm-scroller': {
    fontFamily: 'ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace',
    backgroundColor: '#000000',
    overflow: 'auto',
  },
  '.cm-content': {
    padding: '12px 0',
    backgroundColor: '#000000',
    color: 'rgba(255, 255, 255, 0.75)',
  },
  '.cm-line': {
    color: 'rgba(255, 255, 255, 0.75)',
  },
  '.cm-activeLine': {
    backgroundColor: 'rgba(255,255,255,0.05)',
  },
  '&.cm-focused .cm-cursor': {
    borderLeftColor: '#fff',
  },
  '&.cm-focused .cm-selectionBackground, .cm-selectionBackground, &.cm-focused .cm-content ::selection': {
    backgroundColor: 'rgba(100,149,237,0.4) !important',
  },
});

interface ProfileTabProps {
  companionId: string | null;
  currentUserId: string | null;
  includeProfileInPrompt: boolean;
  includeProfileInPromptDisabled: boolean;
  onIncludeProfileInPromptChange: (enabled: boolean) => void;
  onPendingChange?: () => void;
}

export default function ProfileTab({
  companionId,
  currentUserId,
  includeProfileInPrompt,
  includeProfileInPromptDisabled,
  onIncludeProfileInPromptChange,
  onPendingChange,
}: ProfileTabProps) {
  const { data: companionConfig } = useCompanion(companionId);
  const { mutateAsync: updateCompanion } = useUpdateCompanion();

  const [schemaText, setSchemaText] = useState('');
  const [parseError, setParseError] = useState<string | null>(null);
  const [isSaving, setIsSaving] = useState(false);
  const [hasChanges, setHasChanges] = useState(false);

  // Load profile_schema from companion config
  useEffect(() => {
    if (companionConfig?.profile_schema) {
      setSchemaText(JSON.stringify(companionConfig.profile_schema, null, 2));
      setParseError(null);
      setHasChanges(false);
    } else {
      setSchemaText('{}');
      setHasChanges(false);
    }
  }, [companionConfig]);

  const handleSchemaChange = useCallback((value: string) => {
    setSchemaText(value);
    setHasChanges(true);
    onPendingChange?.();

    // Validate JSON as user types
    if (value.trim() === '') {
      setParseError(null);
      return;
    }
    try {
      JSON.parse(value);
      setParseError(null);
    } catch {
      setParseError('Invalid JSON syntax');
    }
  }, [onPendingChange]);

  const handleSave = useCallback(async () => {
    if (!companionId || !companionConfig) return;

    // Parse and validate
    let parsed: Record<string, unknown>;
    try {
      parsed = JSON.parse(schemaText || '{}');
      if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
        setParseError('Profile schema must be a JSON object');
        return;
      }
    } catch {
      setParseError('Invalid JSON syntax');
      return;
    }

    setIsSaving(true);
    try {
      await updateCompanion({
        id: companionId,
        config: {
          ...companionConfig,
          profile_schema: parsed,
        },
      });
      setHasChanges(false);
      setParseError(null);
    } catch (e) {
      console.error('Failed to save profile schema', e);
      setParseError('Failed to save. Please try again.');
    } finally {
      setIsSaving(false);
    }
  }, [companionId, companionConfig, schemaText, updateCompanion]);

  const handleFormat = useCallback(() => {
    try {
      const parsed = JSON.parse(schemaText);
      setSchemaText(JSON.stringify(parsed, null, 2));
      setParseError(null);
    } catch {
      setParseError('Cannot format: Invalid JSON');
    }
  }, [schemaText]);

  if (!companionId) {
    return (
      <div className="text-white/60 text-sm">
        Select a companion to configure profile schema.
      </div>
    );
  }

  return (
    <div className="space-y-5">
      <FormSection
        title="Profile In Prompt"
        description="Relationship-level setting for the active simulator user. When enabled, the user profile is injected into model context as a #PROFILE system block."
        toggle={{
          checked: includeProfileInPrompt,
          onCheckedChange: (checked) => {
            onIncludeProfileInPromptChange(checked);
            onPendingChange?.();
          },
          disabled: includeProfileInPromptDisabled,
        }}
      >
        <div className="space-y-2">
          <p className="text-[11px] text-white/40">
            {currentUserId
              ? `Active test user: ${currentUserId}`
              : 'No active test user selected.'}
          </p>
        </div>
      </FormSection>

      <FormSection
        title="Profile Schema"
        description="Define a JSON schema template for user profiles. This schema determines what structured data is stored for each user-companion relationship."
      >
        <div className="space-y-3">
          <div className="flex justify-end gap-2">
            <button
              type="button"
              onClick={handleFormat}
              className="text-xs text-white/60 hover:text-white transition-colors underline"
            >
              Format JSON
            </button>
          </div>

          <div className={`overflow-hidden bg-black ${parseError ? 'border border-red-500/50' : ''}`}>
            <CodeMirror
              value={schemaText}
              onChange={handleSchemaChange}
              extensions={[
                editorTheme,
                history(),
                keymap.of([...defaultKeymap, ...historyKeymap]),
              ]}
              theme="dark"
              basicSetup={{
                lineNumbers: false,
                highlightActiveLineGutter: false,
                highlightActiveLine: true,
                foldGutter: false,
                dropCursor: true,
                allowMultipleSelections: true,
                indentOnInput: false,
                bracketMatching: true,
                closeBrackets: false,
                autocompletion: false,
                rectangularSelection: true,
                crosshairCursor: false,
                highlightSelectionMatches: true,
                tabSize: 2,
              }}
              style={{ height: '300px' }}
            />
          </div>

          {parseError && (
            <p className="text-xs text-red-400">{parseError}</p>
          )}

          <div className="flex justify-end">
            <button
              type="button"
              onClick={handleSave}
              disabled={!hasChanges || !!parseError || isSaving}
              className={`px-4 py-2 text-sm transition-colors ${
                hasChanges && !parseError && !isSaving
                  ? 'bg-white/10 text-white hover:bg-white/20'
                  : 'bg-white/5 text-white/30 cursor-not-allowed'
              }`}
            >
              {isSaving ? 'Saving...' : 'Save'}
            </button>
          </div>

          <p className="text-sm text-white/50">
            Adding fields is safe—existing profiles receive new defaults on read. Renaming or removing fields may break behaviors that reference them.
          </p>
        </div>
      </FormSection>
    </div>
  );
}
