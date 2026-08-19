'use client';

import FormSection from '@/components/ui/form-section';
import { Textarea } from '@/components/ui/textarea';
import Dropdown, { type DropdownOption } from '@/components/ui/dropdown';
import { useCompanion } from '@/hooks/useCompanions';
import Icon from '@/components/ui/icon';
import Link from 'next/link';
import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useAuth } from '@clerk/nextjs';
import { API_CONFIG } from '@/lib/config';

// Layer styling matching test-context-engine exactly
const LAYER_STYLES: Record<string, {
  bgEnabled: string;
  bgDisabled: string;
  textEnabled: string;
  textDisabled: string;
  descEnabled: string;
  descDisabled: string;
  checkboxBorderEnabled: string;
  checkboxBorderDisabled: string;
  checkboxBgEnabled: string;
  checkboxBgDisabled: string;
  checkColor: string;
}> = {
  memory: {
    bgEnabled: 'bg-[#4D4318]',
    bgDisabled: 'bg-[#1a1a1a]',
    textEnabled: 'text-[#E5CA59]',
    textDisabled: 'text-white/40',
    descEnabled: 'text-[#E5CA59]/60',
    descDisabled: 'text-white/30',
    checkboxBorderEnabled: 'border-[#E5CA59]',
    checkboxBorderDisabled: 'border-white/20',
    checkboxBgEnabled: 'bg-[#E5CA59]/20',
    checkboxBgDisabled: 'bg-transparent',
    checkColor: 'text-[#E5CA59]',
  },
  knowledge_base: {
    bgEnabled: 'bg-[#243D4D]',
    bgDisabled: 'bg-[#1a1a1a]',
    textEnabled: 'text-[#64ABDE]',
    textDisabled: 'text-white/40',
    descEnabled: 'text-[#64ABDE]/60',
    descDisabled: 'text-white/30',
    checkboxBorderEnabled: 'border-[#64ABDE]',
    checkboxBorderDisabled: 'border-white/20',
    checkboxBgEnabled: 'bg-[#64ABDE]/20',
    checkboxBgDisabled: 'bg-transparent',
    checkColor: 'text-[#64ABDE]',
  },
  tools: {
    bgEnabled: 'bg-[#3D1653]',
    bgDisabled: 'bg-[#1a1a1a]',
    textEnabled: 'text-[#BC54F8]',
    textDisabled: 'text-white/40',
    descEnabled: 'text-[#BC54F8]/60',
    descDisabled: 'text-white/30',
    checkboxBorderEnabled: 'border-[#BC54F8]',
    checkboxBorderDisabled: 'border-white/20',
    checkboxBgEnabled: 'bg-[#BC54F8]/20',
    checkboxBgDisabled: 'bg-transparent',
    checkColor: 'text-[#BC54F8]',
  },
  behaviors: {
    bgEnabled: 'bg-[#334130]',
    bgDisabled: 'bg-[#1a1a1a]',
    textEnabled: 'text-[#85CD75]',
    textDisabled: 'text-white/40',
    descEnabled: 'text-[#85CD75]/60',
    descDisabled: 'text-white/30',
    checkboxBorderEnabled: 'border-[#85CD75]',
    checkboxBorderDisabled: 'border-white/20',
    checkboxBgEnabled: 'bg-[#85CD75]/20',
    checkboxBgDisabled: 'bg-transparent',
    checkColor: 'text-[#85CD75]',
  },
};

// Layer descriptions for display
const LAYER_DESCRIPTIONS: Record<string, string> = {
  memory: 'LTM, Scratchpad, Regular',
  knowledge_base: 'Files, Dialogue Examples',
  tools: 'Developer-supplied tools',
  behaviors: 'Developer-defined logic',
};

// Classifier model options (from server/app/context/classifier_schemas.py)
const CLASSIFIER_MODEL_OPTIONS: DropdownOption[] = [
  { value: 'gemini-2.0-flash', label: 'Gemini 2.0 Flash' },
  { value: 'gpt-4o-mini', label: 'GPT-4o Mini' },
  { value: 'gpt-5-nano', label: 'GPT-5 Nano' },
  { value: 'gpt-5-mini', label: 'GPT-5 Mini' },
];

// Placeholder for custom instructions
const CUSTOM_INSTRUCTIONS_PLACEHOLDER = `Example custom instructions:
- Enable knowledge_base for questions about menstrual cycles, symptoms, or period health
- Enable tools when user asks to schedule or needs real-time data
- Be more aggressive with memory for returning users`;

interface LayerStatus {
  key: string;
  category: string;
  enabled: boolean;
  description: string;
  displayName: string;
}

interface ClassifierInputs {
  system_prompt: string;
  tool_summaries: Array<{ spec_name: string; summary: string }>;
  knowledge_summary: string | null;
  behavior_hints: Array<{ key: string; name: string; hint: string }>;
}

interface ContextTabProps {
  companionId: string | null;
  classifierEnabled: boolean;
  classifierModel: string;
  classifierInstructions: string;
  messageLimit: number;
  onClassifierEnabledChange: (enabled: boolean) => void;
  onClassifierModelChange: (model: string) => void;
  onClassifierInstructionsChange: (instructions: string) => void;
  onMessageLimitChange: (limit: number) => void;
}

// Hook for fetching classifier inputs
function useClassifierInputs(companionId: string | null) {
  const { getToken } = useAuth();
  const isAuthDisabled = process.env.NEXT_PUBLIC_DISABLE_AUTH === 'true';

  return useQuery<ClassifierInputs>({
    queryKey: ['classifier-inputs', companionId],
    queryFn: async () => {
      const token = isAuthDisabled ? 'mock-dev-token' : await getToken();
      const res = await fetch(
        `${API_CONFIG.BASE_URL}/api/companions/${companionId}/classifier-inputs`,
        {
          headers: { Authorization: `Bearer ${token}` },
        }
      );
      if (!res.ok) throw new Error('Failed to load classifier inputs');
      return res.json();
    },
    enabled: !!companionId,
    staleTime: 60 * 1000, // 1 minute
  });
}

export default function ContextTab({
  companionId,
  classifierEnabled,
  classifierModel,
  classifierInstructions,
  messageLimit,
  onClassifierEnabledChange,
  onClassifierModelChange,
  onClassifierInstructionsChange,
  onMessageLimitChange,
}: ContextTabProps) {
  const { data: companionConfig } = useCompanion(companionId);
  const { data: classifierInputs, isLoading: inputsLoading } = useClassifierInputs(companionId);

  // Derive layer statuses from companion config
  const layers = useMemo((): LayerStatus[] => {
    if (!companionConfig) return [];

    const result: LayerStatus[] = [];

    // Memory layer - check both config.memory.enabled and layers array
    const memoryEnabled = companionConfig.memory?.enabled ?? false;
    const memoryLayer = companionConfig.layers?.find(
      (l) => l.key === 'memory' || l.category === 'memory'
    );
    result.push({
      key: 'memory',
      category: 'memory',
      enabled: memoryEnabled || memoryLayer?.enabled || false,
      description: LAYER_DESCRIPTIONS.memory,
      displayName: 'Memory',
    });

    // Knowledge layer - check layers array
    const knowledgeLayer = companionConfig.layers?.find(
      (l) => l.key === 'knowledge_base' || l.category === 'knowledge_base'
    );
    result.push({
      key: 'knowledge_base',
      category: 'knowledge_base',
      enabled: knowledgeLayer?.enabled || false,
      description: LAYER_DESCRIPTIONS.knowledge_base,
      displayName: 'Knowledge',
    });

    // Tools layer - check layers array
    const toolsLayer = companionConfig.layers?.find(
      (l) => l.key === 'tools' || l.category === 'tools'
    );
    result.push({
      key: 'tools',
      category: 'tools',
      enabled: toolsLayer?.enabled || false,
      description: LAYER_DESCRIPTIONS.tools,
      displayName: 'Tools',
    });

    // Behaviors layer - check layers array
    const behaviorsLayer = companionConfig.layers?.find(
      (l) =>
        l.key === 'behaviors' ||
        l.category === 'behaviors' ||
        l.key === 'actions' ||
        l.category === 'actions'
    );
    result.push({
      key: 'behaviors',
      category: 'behaviors',
      enabled: behaviorsLayer?.enabled || false,
      description: LAYER_DESCRIPTIONS.behaviors,
      displayName: 'Behaviors',
    });

    return result;
  }, [companionConfig]);

  // Check if there are any layer-specific inputs to show
  const hasToolSummaries = (classifierInputs?.tool_summaries?.length ?? 0) > 0;
  const hasKnowledgeSummary = !!classifierInputs?.knowledge_summary;
  const hasBehaviorHints = (classifierInputs?.behavior_hints?.length ?? 0) > 0;
  const hasLayerInputs = hasToolSummaries || hasKnowledgeSummary || hasBehaviorHints;

  if (!companionId) {
    return (
      <div className="flex items-center justify-center h-full text-white/40 text-sm">
        Select a companion to configure context
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Context Layers */}
      <FormSection
        title="Context Layers"
        description="Layers provide different types of context to your companion. The classifier decides which layers to activate based on the user's message."
      >
        <div className="space-y-1">
          {layers.map((layer) => {
            const styles = LAYER_STYLES[layer.category] || LAYER_STYLES.memory;
            const bgColor = layer.enabled ? styles.bgEnabled : styles.bgDisabled;
            const textColor = layer.enabled ? styles.textEnabled : styles.textDisabled;
            const descColor = layer.enabled ? styles.descEnabled : styles.descDisabled;
            const checkboxBorder = layer.enabled ? styles.checkboxBorderEnabled : styles.checkboxBorderDisabled;
            const checkboxBg = layer.enabled ? styles.checkboxBgEnabled : styles.checkboxBgDisabled;

            return (
              <div
                key={layer.key}
                className={`flex items-center gap-3 py-3 px-4 ${bgColor}`}
              >
                {/* Checkbox indicator */}
                <div
                  className={`w-5 h-5 flex items-center justify-center border ${checkboxBorder} ${checkboxBg}`}
                >
                  {layer.enabled && (
                    <Icon name="check" size={12} className={styles.checkColor} />
                  )}
                </div>

                {/* Layer name and description */}
                <span className={`font-medium ${textColor}`}>
                  {layer.displayName}
                </span>
                <span className={`text-sm ${descColor}`}>
                  {layer.description}
                </span>
              </div>
            );
          })}
        </div>

        {/* Test Context Engine Link */}
        <Link
          href="/test-context-engine"
          className="group flex items-center gap-2 mt-3 text-left transition-colors"
        >
          <span className="font-book text-lg text-white group-hover:text-white/80 transition-colors">
            Test Context Engine
          </span>
          <Icon
            name="up-right-arrow"
            size={12}
            className="text-white group-hover:text-white/80 transition-colors"
          />
        </Link>
      </FormSection>

      {/* Message History Limit */}
      <FormSection
        title="Message History Limit"
        description="Number of recent messages loaded into context each turn. Higher values provide more context but use more tokens. Minimum: 10, Default: 200."
      >
        <div className="space-y-1.5">
          <input
            type="text"
            inputMode="numeric"
            value={messageLimit}
            onChange={(e) => {
              const raw = e.target.value;
              if (raw === '') {
                onMessageLimitChange(200);
                return;
              }
              const num = parseInt(raw, 10);
              if (!isNaN(num) && num >= 10) {
                onMessageLimitChange(num);
              }
            }}
            placeholder="200"
            className="w-32 bg-[var(--color-input-editable)] text-white font-book text-xs leading-tight px-[10px] py-2 placeholder:text-[var(--color-placeholder)] focus:outline-none transition-colors"
          />
          <p className="text-[11px] text-white/40">
            Controls how many recent messages the AI can see. Also triggers automatic conversation summarization at each threshold (e.g., at 200, 400, 600 messages).
          </p>
        </div>
      </FormSection>

      {/* Intent Classifier Toggle */}
      <FormSection
        title="Intent Classifier"
        description="LLM-based layer routing that analyzes each message to decide which layers to activate."
        toggle={{
          checked: classifierEnabled,
          onCheckedChange: onClassifierEnabledChange,
        }}
      />

      {/* Model Selection */}
      <FormSection
        title="Classifier Model"
        description="The LLM model used to classify user intent and decide which layers to activate."
      >
        <Dropdown
          options={CLASSIFIER_MODEL_OPTIONS}
          value={classifierModel}
          onChange={onClassifierModelChange}
          placeholder="Select a model..."
        />
      </FormSection>

      {/* Classifier System Prompt (Read-only) */}
      <FormSection
        title="Classifier System Prompt"
        description="The default system prompt sent to the classifier LLM. This prompt instructs the classifier how to analyze messages and decide which layers to activate."
      >
        {inputsLoading ? (
          <div className="flex items-center gap-2 text-white/40 text-sm py-4">
            <div className="w-4 h-4 border-2 border-white/20 border-t-white/60 rounded-full animate-spin" />
            Loading...
          </div>
        ) : (
          <div className="bg-[var(--color-input-readonly)] px-4 py-3 text-[13px] text-white/60 font-mono whitespace-pre-wrap max-h-[300px] overflow-y-auto">
            {classifierInputs?.system_prompt || 'Unable to load classifier system prompt'}
          </div>
        )}
      </FormSection>

      {/* Custom Classifier Instructions */}
      <FormSection
        title="Custom Instructions"
        description="Additional instructions appended to the classifier prompt. Use this to extend the default rules for your specific use case. These are added after the system prompt above."
      >
        <Textarea
          value={classifierInstructions}
          onChange={(e) => onClassifierInstructionsChange(e.target.value)}
          placeholder={CUSTOM_INSTRUCTIONS_PLACEHOLDER}
          minHeight={140}
        />
      </FormSection>

      {/* Layer-Specific Classifier Inputs */}
      <FormSection
        title="Layer-Specific Classifier Inputs"
        description="These layer descriptions are provided to the classifier to help it understand when to activate each layer."
      >
        {inputsLoading ? (
          <div className="flex items-center gap-2 text-white/40 text-sm py-4">
            <div className="w-4 h-4 border-2 border-white/20 border-t-white/60 rounded-full animate-spin" />
            Loading classifier inputs...
          </div>
        ) : !hasLayerInputs ? (
          <div className="text-white/40 text-sm py-2">
            No layer-specific classifier inputs configured. Add classifier summaries to your tools, knowledge base, or behaviors to help the classifier make better decisions.
          </div>
        ) : (
          <div className="space-y-4">
            {/* Knowledge Summary */}
            {hasKnowledgeSummary && (
              <div>
                <div className="flex items-center gap-2 mb-2">
                  <span className="text-[#64ABDE] font-medium text-sm">Knowledge</span>
                </div>
                <div className="bg-[var(--color-input-readonly)] px-4 py-3 text-[13px] text-white/60 font-mono">
                  {classifierInputs?.knowledge_summary}
                </div>
              </div>
            )}

            {/* Tool Summaries */}
            {hasToolSummaries && (
              <div>
                <div className="flex items-center gap-2 mb-2">
                  <span className="text-[#BC54F8] font-medium text-sm">Tools</span>
                </div>
                <div className="bg-[var(--color-input-readonly)] px-4 py-3 text-[13px] text-white/60 font-mono space-y-2">
                  {classifierInputs?.tool_summaries.map((tool, idx) => (
                    <div key={idx}>
                      <span className="text-[#BC54F8]">{tool.spec_name || 'Tool Spec'}:</span>{' '}
                      {tool.summary}
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Behavior Hints */}
            {hasBehaviorHints && (
              <div>
                <div className="flex items-center gap-2 mb-2">
                  <span className="text-[#85CD75] font-medium text-sm">Available Behaviors</span>
                </div>
                <div className="bg-[var(--color-input-readonly)] px-4 py-3 text-[13px] text-white/60 font-mono space-y-2">
                  {classifierInputs?.behavior_hints.map((behavior, idx) => (
                    <div key={idx}>
                      <span className="text-[#85CD75]">{behavior.key}:</span>{' '}
                      {behavior.hint}
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
      </FormSection>
    </div>
  );
}
