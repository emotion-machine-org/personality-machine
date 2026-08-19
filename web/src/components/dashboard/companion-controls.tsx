'use client';

import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { format } from 'date-fns';
import VerticalTabs from '@/components/ui/vertical-tabs';
import FormSection from '@/components/ui/form-section';
import { Textarea } from '@/components/ui/textarea';
import Dropdown, { type DropdownOption } from '@/components/ui/dropdown';
import CustomSwitch from '@/components/ui/switch';
import Icon from '@/components/ui/icon';
import { useCreateMemory, useMemories, useUpdateMemory, useDeleteMemory } from '@/hooks/useMemories';
import PendingChangesBanner from '@/components/ui/pending-changes-banner';
import { useWebSocketSessionCore, type VoiceConfig } from '@/hooks/useWebSocketSession';
import { useCompanions, useCompanion, useUpdateCompanion, useCompanionVersions } from '@/hooks/useCompanions';
import type { CompanionConfig, CompanionVersionSummary, MemoryItem } from '@/lib/types';
import { useSelectedCompanion } from '@/components/providers';
import { useBuilderRelationship } from '@/hooks/useBuilderRelationship';
import { apiClient } from '@/lib/api';
import { useAuth } from '@clerk/nextjs';
import {
  VOICE_NAMES,
  VOICE_PRESET_KEYS,
  getResolvedPopularOption,
  resolvePresetKey,
  getPresetDisplayInfo,
  getPresetOptions,
} from '@/lib/voice-presets';
import ToolsTab from './tools-tab';
import KnowledgeTab from './knowledge-tab';
import BehaviorsTab from './behaviors-tab';
import ContextTab from './context-tab';
import ProfileTab from './profile-tab';
import { MemoryExplorer } from '@/components/memory-explorer';


interface ExtendedFormData {
  identity: string;
  personality: string;
  style: string;
  backstory: string;
  additionalInstructions: string;
  fullSystemPrompt: string;
  popularVoice: string;
  sttProvider: string;
  ttsProvider: string;
  llmProvider: string;
  memoryEvaluationPrompt: string;
  selectedVoice: string;
  coreMemories?: string[];
  // Memory V2 fields
  memoryVersion: 1 | 2;
  memoryIngestionPrompt: string;
  memoryModel: string;
  memoryMaxEntries: number;
}



const TABS = [
  { id: 'system-prompt', label: 'General' },
  { id: 'voice', label: 'LLM & Voice' },
  { id: 'memory', label: 'Memory' },
  { id: 'behaviors', label: 'Behaviors' },
  { id: 'tools', label: 'Tools' },
  { id: 'knowledge', label: 'Knowledge' },
  { id: 'context', label: 'Context' },
  { id: 'profile', label: 'Profile' },
] as const;

// Memory version options
const MEMORY_VERSION_OPTIONS: DropdownOption[] = [
  { value: '1', label: 'V1 - Vector Retrieval' },
  { value: '2', label: 'V2 - Scratchpad' },
];

// Memory V2 model options (matches server defaults)
const MEMORY_V2_MODEL_OPTIONS: DropdownOption[] = [
  { value: 'default', label: 'Default (Gemini 2.0 Flash)' },
  { value: 'google/gemini-2.0-flash-001', label: 'Gemini 2.0 Flash' },
  { value: 'openai/gpt-4o-mini', label: 'GPT-4o Mini' },
  { value: 'openai/gpt-4o', label: 'GPT-4o' },
  { value: 'anthropic/claude-3-haiku', label: 'Claude 3 Haiku' },
];

// Default memory v2 ingestion guidelines (matches server/app/modals/workers/memory_v2_ingest.py)
// Note: The server automatically appends the boilerplate (placeholders, response format) to this
const DEFAULT_MEMORY_INGESTION_GUIDELINES = `You are a memory manager for an AI companion. Your job is to maintain a scratchpad of important facts about the user.

Given a conversation turn, decide what memories to ADD, UPDATE, or DELETE.

## Guidelines

**ADD** new memories when you learn:
- Identity facts (name, age, location, profession)
- Preferences and communication style
- Goals, plans, and aspirations
- Significant life events or milestones
- Important relationships (family, friends, colleagues)
- Constraints or boundaries

**UPDATE** existing memories when:
- Information needs correction
- More detail or context is available
- A fact has changed

**DELETE** memories when:
- Information is explicitly corrected/outdated
- The user asks to forget something
- A memory is redundant with another

## Important
- Be selective - only store genuinely useful information
- Avoid storing: transient details, generic statements, conversation filler
- Prefer updating over adding duplicate information
- Use concise, factual language for memory content
- Write memories from the companion's first-person perspective (use "me" not "the AI companion")`;
const DEFAULT_INTRO_MESSAGE_TEXT = 'Hi, how are you?';

// Voice preset metadata is centralized in @/lib/voice-presets.

export default function CompanionControls() {
  const { getToken } = useAuth();
  const isAuthDisabled = process.env.NEXT_PUBLIC_DISABLE_AUTH === 'true';
  const [activeTab, setActiveTab] = useState('system-prompt');
  const [memoryEnabled, setMemoryEnabled] = useState(true);
  const [classifierInstructions, setClassifierInstructions] = useState<string>('');
  const [classifierEnabled, setClassifierEnabled] = useState<boolean>(true);
  const [classifierModel, setClassifierModel] = useState<string>('gemini-2.0-flash');
  const [introMessageEnabled, setIntroMessageEnabled] = useState<boolean>(false);
  const [introMessageText, setIntroMessageText] = useState<string>(DEFAULT_INTRO_MESSAGE_TEXT);
  const [includeProfileInPrompt, setIncludeProfileInPrompt] = useState<boolean>(false);
  const [knowledgeClassifierSummary, setKnowledgeClassifierSummary] = useState<string>('');
  const [messageLimit, setMessageLimit] = useState<number>(200);
  const [maxOutputTokens, setMaxOutputTokens] = useState<number | null>(null);
  const [maxOutputTokensInput, setMaxOutputTokensInput] = useState<string>('');
  const [maxOutputTokensError, setMaxOutputTokensError] = useState<string | null>(null);
  const [showPendingBanner, setShowPendingBanner] = useState(false);
  // Track previous connection state to detect session (re)starts
  const prevConnectionRef = useRef<{ isConnected: boolean; isConnecting: boolean }>({
    isConnected: false,
    isConnecting: false,
  });
  const [bannerSuppressed, setBannerSuppressed] = useState(false);
  const bannerSuppressTimeoutRef = useRef<NodeJS.Timeout | null>(null);
  const initialConfigRef = useRef<string | null>(null);
  const lastLoadedCompanionIdRef = useRef<string | null>(null);
  const [isSaving, setIsSaving] = useState(false);
  const [isHistoryOpen, setIsHistoryOpen] = useState(false);
  const [hasOpenedHistory, setHasOpenedHistory] = useState(false);
  const [isApplyingVersion, setIsApplyingVersion] = useState(false);
  const [selectedVersionId, setSelectedVersionId] = useState<string | null>(null);
  const [isMemoryExplorerOpen, setIsMemoryExplorerOpen] = useState(false);

  const { selectedCompanionId, setSelectedCompanionId, triggerOnSaveCallbacks } = useSelectedCompanion();
  const {
    currentUserId,
    currentRelationship,
    refreshRelationship,
  } = useBuilderRelationship({ companionId: selectedCompanionId });
  const { config, setConfig, isConnected, isConnecting } = useWebSocketSessionCore();
  const { data: companions } = useCompanions();
  const { data: companionConfig } = useCompanion(selectedCompanionId);
  const { mutateAsync: updateCompanion } = useUpdateCompanion();
  // Lazy-load versions only when History menu is opened (reduces initial API calls)
  const {
    data: companionVersions,
    isLoading: versionsLoading,
    error: versionsError,
    refetch: refetchVersions,
    fetchVersionConfig,
  } = useCompanionVersions(selectedCompanionId, { enabled: hasOpenedHistory });
  // Core-memory POST enqueues async ingestion (MemoryService.create). Hook keeps a pending row alive until the worker commits.
  const createMemory = useCreateMemory(selectedCompanionId);
  const isMemoryTabActive = activeTab === 'memory';
  // Only fetch core memories - server-side filtering reduces payload significantly
  const { data: coreMemories = [] } = useMemories(
    selectedCompanionId,
    { limit: 100, order_by: 'created_at', order_dir: 'DESC', is_core: true },
    { enabled: isMemoryTabActive }
  );
  const updateMemory = useUpdateMemory();
  const deleteMemory = useDeleteMemory();

  const selectedVersionLabel = useMemo(() => {
    if (!selectedVersionId || !companionVersions) return null;
    const match = companionVersions.find((v) => v.id === selectedVersionId);
    if (!match) return null;
    return `v${match.version_number}`;
  }, [companionVersions, selectedVersionId]);

  const persistedIncludeProfileInPrompt = useMemo(() => {
    const relConfig = (currentRelationship?.config || {}) as Record<string, unknown>;
    if (typeof relConfig.include_profile_in_prompt === 'boolean') {
      return relConfig.include_profile_in_prompt;
    }
    if (typeof relConfig.include_app_state_in_prompt === 'boolean') {
      return relConfig.include_app_state_in_prompt;
    }
    return false;
  }, [currentRelationship]);

  const includeProfileInPromptDirty = includeProfileInPrompt !== persistedIncludeProfileInPrompt;

  // Auto-select first companion only when no selection exists and no URL param
  // Don't override URL-based selection - wait for CompanionUrlSync to process it
  useEffect(() => {
    if (companions && companions.length > 0 && !selectedCompanionId) {
      // Check if there's a companion param in the URL - if so, wait for URL sync
      const urlParams = new URLSearchParams(window.location.search);
      const companionFromUrl = urlParams.get('companion');
      if (companionFromUrl) {
        // URL has a companion param - don't auto-select, let CompanionUrlSync handle it
        return;
      }
      // No companion selected and no URL param - pick the first one
      setSelectedCompanionId(companions[0].id);
    }
  }, [companions, selectedCompanionId, setSelectedCompanionId]);


  // Load companion config into form only when switching companions (avoid overwriting local edits)
  useEffect(() => {
    if (companionConfig && selectedCompanionId) {
      const fullPrompt = companionConfig.system_prompt.full_system_prompt || '';

      const availablePresets: string[] = VOICE_PRESET_KEYS as string[];
      let persistedPopular: string | undefined;
      // Use preset field (resolve to new format key)
      const persistedPreset = companionConfig.voice?.preset;
      if (persistedPreset) {
        const resolved = resolvePresetKey(persistedPreset);
        if (resolved && availablePresets.includes(resolved)) {
          persistedPopular = resolved;
        }
      }
      const persistedVoiceName: string | undefined = companionConfig.voice?.voice_name || undefined;

      // Only initialize voice/system state when switching companions or on first mount
      const companionChanged = lastLoadedCompanionIdRef.current !== selectedCompanionId;

      if (companionChanged) {
        setFormData(prev => ({
          ...prev,
          // Just use fullSystemPrompt directly - no structured fields
          identity: '',
          personality: '',
          style: '',
          backstory: '',
          additionalInstructions: '',
          fullSystemPrompt: fullPrompt,
          memoryEvaluationPrompt: companionConfig.memory.memory_evaluation_prompt || '',
          coreMemories: [...(companionConfig.memory.core_memories || [])],
          // Reset voice selections to the persisted configuration for this companion
          popularVoice: persistedPopular || '',
          selectedVoice: persistedVoiceName || '',
          // Memory V2 fields
          memoryVersion: companionConfig.memory.version ?? 1,
          memoryIngestionPrompt: companionConfig.memory.ingestion_prompt ?? '',
          memoryModel: companionConfig.memory.model || 'default',
          memoryMaxEntries: companionConfig.memory.max_entries ?? 100,
        }));
        setMemoryEnabled(companionConfig.memory.enabled);
        setMaxOutputTokens(companionConfig.inference?.max_output_tokens ?? null);
        setMaxOutputTokensInput(companionConfig.inference?.max_output_tokens?.toString() ?? '');
        setMaxOutputTokensError(null);
        setClassifierInstructions(companionConfig.classifier?.instructions ?? '');
        setClassifierEnabled(companionConfig.classifier?.enabled ?? true);
        setClassifierModel(companionConfig.classifier?.model ?? 'gemini-2.0-flash');
        setIntroMessageEnabled(companionConfig.intro_message?.enabled ?? false);
        setIntroMessageText(
          (companionConfig.intro_message?.text || '').trim() || DEFAULT_INTRO_MESSAGE_TEXT
        );
        setKnowledgeClassifierSummary(companionConfig.knowledge?.classifier_summary ?? '');
        setMessageLimit(companionConfig.context?.message_limit ?? 200);
        lastLoadedCompanionIdRef.current = selectedCompanionId;
        initialConfigRef.current = null; // Force recompute below for new companion snapshot
      }

      // Store initial config snapshot for dirty detection (per companion)
      const configKey = `${companionConfig.system_prompt.full_system_prompt}-${JSON.stringify(companionConfig.voice)}-${companionConfig.memory.enabled}`;
      if (initialConfigRef.current === null) {
        initialConfigRef.current = configKey;
        if (!isConnected && !isConnecting) {
          setShowPendingBanner(false);
        }
      }
    }
  }, [companionConfig, selectedCompanionId, isConnected, isConnecting]);

  // Keep relationship-level profile injection toggle in sync with active test user state.
  useEffect(() => {
    setIncludeProfileInPrompt(persistedIncludeProfileInPrompt);
  }, [persistedIncludeProfileInPrompt, currentRelationship?.id, currentUserId, selectedCompanionId]);

  // Clear pending banner when a new voice session starts (connecting -> true or connected -> true)
  useEffect(() => {
    const prev = prevConnectionRef.current;
    // If a new connect sequence starts, clear pending banner because changes are being applied now
    if (!prev.isConnecting && isConnecting) {
      setShowPendingBanner(false);
      // Suppress any flicker during restart/connect cycle
      setBannerSuppressed(true);
      if (bannerSuppressTimeoutRef.current) clearTimeout(bannerSuppressTimeoutRef.current);
      bannerSuppressTimeoutRef.current = setTimeout(() => {
        setBannerSuppressed(false);
        bannerSuppressTimeoutRef.current = null;
      }, 1500);
    }
    // Also guard the edge case where we jump straight to connected
    if (!prev.isConnected && isConnected) {
      setShowPendingBanner(false);
    }
    prevConnectionRef.current = { isConnected, isConnecting };
  }, [isConnected, isConnecting]);

  // Cleanup suppression timer on unmount
  useEffect(() => {
    return () => {
      if (bannerSuppressTimeoutRef.current) clearTimeout(bannerSuppressTimeoutRef.current);
    };
  }, []);
  
  // (moved) Autosave memory settings comes after formData is defined below
  
  // Form state would typically be managed with React Query mutations
  const [formData, setFormData] = useState<ExtendedFormData>({
    identity: '',
    personality: '',
    style: '',
    backstory: '',
    additionalInstructions: '',
    fullSystemPrompt: '',
    popularVoice: '',
    sttProvider: '',
    ttsProvider: '',
    llmProvider: '',
    memoryEvaluationPrompt: '',
    selectedVoice: '',
    coreMemories: [],
    // Memory V2 defaults
    memoryVersion: 1,
    memoryIngestionPrompt: '',
    memoryModel: 'default',
    memoryMaxEntries: 100,
  });
  


  const handleInputChange = useCallback((field: keyof ExtendedFormData) => (value: string) => {
    setFormData(prev => {
      const newData = { ...prev, [field]: value };

      // Show pending changes banner when configuration changes
      setShowPendingBanner(true);
      
      return newData;
    });
  }, []);

  // Memoized helper functions
  const getVoiceOptionsForPopularChoice = useCallback((popularChoice: string): string[] => {
    const option = getResolvedPopularOption(popularChoice);
    return option ? [...VOICE_NAMES[option.voiceProvider]] : [];
  }, []);

  // Removed autosave for memory; we persist via explicit Save button instead
  
  const buildVoiceConfig = useCallback((popularChoice: string, selectedVoice: string): VoiceConfig => {
    const option = getResolvedPopularOption(popularChoice);
    if (!option) return config.voiceConfig;

    return {
      ...option.config,
      voice_name: selectedVoice || VOICE_NAMES[option.voiceProvider][0] || 'alloy'
    };
  }, [config.voiceConfig]);

  const handlePopularVoiceChange = useCallback((option: string) => {
    setFormData(prev => ({ ...prev, popularVoice: option, selectedVoice: '' }));
    setShowPendingBanner(true);
  }, []);

  const handleVoiceChange = useCallback((voice: string) => {
    setFormData(prev => ({ ...prev, selectedVoice: voice }));
    setShowPendingBanner(true);
  }, []);

  const handleApplyVersion = useCallback(async (version: CompanionVersionSummary) => {
    if (!selectedCompanionId) return;
    setIsApplyingVersion(true);
    try {
      const snapshot = await fetchVersionConfig(version.id);
      const fullPrompt = snapshot.system_prompt.full_system_prompt || '';

      const availablePresets: string[] = VOICE_PRESET_KEYS as string[];
      let resolvedPopular: string | undefined;
      // Use preset field (resolve to new format key)
      const persistedPreset = snapshot.voice?.preset;
      if (persistedPreset) {
        const resolved = resolvePresetKey(persistedPreset);
        if (resolved && availablePresets.includes(resolved)) {
          resolvedPopular = resolved;
        }
      }
      const persistedVoiceName: string = snapshot.voice?.voice_name || '';

      setFormData(prev => ({
        ...prev,
        // Just use fullSystemPrompt directly - no structured fields
        identity: '',
        personality: '',
        style: '',
        backstory: '',
        additionalInstructions: '',
        fullSystemPrompt: fullPrompt,
        memoryEvaluationPrompt: snapshot.memory.memory_evaluation_prompt || '',
        coreMemories: [...(snapshot.memory.core_memories || [])],
        popularVoice: resolvedPopular || '',
        selectedVoice: persistedVoiceName || '',
        // Memory V2 fields
        memoryVersion: snapshot.memory.version ?? 1,
        memoryIngestionPrompt: snapshot.memory.ingestion_prompt ?? '',
        memoryModel: snapshot.memory.model || 'default',
        memoryMaxEntries: snapshot.memory.max_entries ?? 100,
      }));
      setMemoryEnabled(snapshot.memory.enabled);
      setIntroMessageEnabled(snapshot.intro_message?.enabled ?? false);
      setIntroMessageText(
        (snapshot.intro_message?.text || '').trim() || DEFAULT_INTRO_MESSAGE_TEXT
      );
      setShowPendingBanner(true);
      setSelectedVersionId(version.id);
      setIsHistoryOpen(false);
    } catch (error) {
      console.error('Failed to load companion version', error);
    } finally {
      setIsApplyingVersion(false);
    }
  }, [fetchVersionConfig, selectedCompanionId]);

  // System prompt - just use fullSystemPrompt directly
  const systemPrompt = formData.fullSystemPrompt || '';

  // Derive voice config - NO useEffect!
  const currentVoiceConfig = useMemo(() => {
    if (!formData.popularVoice) return config.voiceConfig;
    return buildVoiceConfig(formData.popularVoice, formData.selectedVoice);
  }, [formData.popularVoice, formData.selectedVoice, buildVoiceConfig, config.voiceConfig]);

  // Use actual formData for display (no need to derive)
  const derivedFormData = useMemo(() => ({
    ...formData,
    fullSystemPrompt: systemPrompt
  }), [formData, systemPrompt]);

  // Persisted voice selections derived from current companion config
  const persistedVoiceName = useMemo(() => companionConfig?.voice?.voice_name || '', [companionConfig]);
  const persistedPopularChoice = useMemo(() => {
    const availablePresets: string[] = VOICE_PRESET_KEYS as string[];
    const preset = companionConfig?.voice?.preset;
    if (preset) {
      const resolved = resolvePresetKey(preset);
      if (resolved && availablePresets.includes(resolved)) {
        return resolved;
      }
    }
    return '';
  }, [companionConfig]);
  const defaultVoiceForPopular = useCallback((popularChoice: string) => {
    const option = getResolvedPopularOption(popularChoice);
    if (!option) return 'alloy';
    const list = VOICE_NAMES[option.voiceProvider];
    return list?.[0] || 'alloy';
  }, []);

  // Voice name we intend to save, based on current UI state
  const voiceNameToSave = useMemo(() => {
    if (derivedFormData.selectedVoice) return derivedFormData.selectedVoice;
    if (derivedFormData.popularVoice) return defaultVoiceForPopular(derivedFormData.popularVoice);
    return persistedVoiceName;
  }, [derivedFormData.selectedVoice, derivedFormData.popularVoice, defaultVoiceForPopular, persistedVoiceName]);

  // Determine if there are unsaved changes across System Prompt, Memory, or Voice
  const hasUnsavedChanges = useMemo(() => {
    if (!companionConfig) return false;
    const promptDirty = (systemPrompt || '') !== (companionConfig.system_prompt.full_system_prompt || '');
    const memoryDirty = (memoryEnabled !== !!companionConfig.memory.enabled) ||
      ((formData.memoryEvaluationPrompt || '') !== (companionConfig.memory.memory_evaluation_prompt || ''));
    // Memory V2 dirty checks
    const configModel = companionConfig.memory.model || 'default';
    // Ingestion prompt: treat "default text in form with null in backend" as NOT dirty
    // because backend uses default when stored value is empty/null
    const storedIngestionPrompt = companionConfig.memory.ingestion_prompt ?? '';
    const isIngestionPromptDirty = formData.memoryIngestionPrompt !== storedIngestionPrompt &&
      !(storedIngestionPrompt === '' && formData.memoryIngestionPrompt === DEFAULT_MEMORY_INGESTION_GUIDELINES);
    const memoryV2Dirty =
      (formData.memoryVersion !== (companionConfig.memory.version ?? 1)) ||
      isIngestionPromptDirty ||
      (formData.memoryModel !== configModel) ||
      (formData.memoryMaxEntries !== (companionConfig.memory.max_entries ?? 100));
    const voiceDirty = (voiceNameToSave || '') !== (persistedVoiceName || '');
    const popularDirty = (derivedFormData.popularVoice || '') !== (persistedPopularChoice || '');
    const tokensDirty = (maxOutputTokens ?? null) !== (companionConfig.inference?.max_output_tokens ?? null);
    const classifierInstructionsDirty = (classifierInstructions || '') !== (companionConfig.classifier?.instructions ?? '');
    const classifierEnabledDirty = classifierEnabled !== (companionConfig.classifier?.enabled ?? true);
    const classifierModelDirty = classifierModel !== (companionConfig.classifier?.model ?? 'gemini-2.0-flash');
    const classifierDirty = classifierInstructionsDirty || classifierEnabledDirty || classifierModelDirty;
    const persistedIntroText =
      (companionConfig.intro_message?.text || '').trim() || DEFAULT_INTRO_MESSAGE_TEXT;
    const currentIntroText = (introMessageText || '').trim() || DEFAULT_INTRO_MESSAGE_TEXT;
    const introDirty =
      introMessageEnabled !== (companionConfig.intro_message?.enabled ?? false) ||
      currentIntroText !== persistedIntroText;
    const knowledgeSummaryDirty = (knowledgeClassifierSummary || '') !== (companionConfig.knowledge?.classifier_summary ?? '');
    return (
      promptDirty ||
      memoryDirty ||
      memoryV2Dirty ||
      voiceDirty ||
      popularDirty ||
      tokensDirty ||
      classifierDirty ||
      introDirty ||
      knowledgeSummaryDirty ||
      includeProfileInPromptDirty
    );
  }, [
    companionConfig,
    systemPrompt,
    memoryEnabled,
    formData.memoryEvaluationPrompt,
    formData.memoryVersion,
    formData.memoryIngestionPrompt,
    formData.memoryModel,
    formData.memoryMaxEntries,
    voiceNameToSave,
    persistedVoiceName,
    derivedFormData.popularVoice,
    persistedPopularChoice,
    maxOutputTokens,
    classifierInstructions,
    classifierEnabled,
    classifierModel,
    introMessageEnabled,
    introMessageText,
    knowledgeClassifierSummary,
    includeProfileInPromptDirty,
  ]);

  // Explicit Save handler to persist current edits
  const handleSaveAll = useCallback(async () => {
    if (!selectedCompanionId || !companionConfig) return;
    setIsSaving(true);
    try {
      const mergedConfig: Partial<CompanionConfig> = {
        ...companionConfig,
        system_prompt: {
          ...companionConfig.system_prompt,
          full_system_prompt: systemPrompt,
        },
        memory: {
          ...companionConfig.memory,
          enabled: memoryEnabled,
          memory_evaluation_prompt: formData.memoryEvaluationPrompt,
          // Memory V2 fields
          version: formData.memoryVersion,
          ingestion_prompt: formData.memoryIngestionPrompt || null,
          model: formData.memoryModel === 'default' ? null : formData.memoryModel || null,
          max_entries: formData.memoryMaxEntries,
        },
        voice: {
          ...companionConfig.voice,
          preset: derivedFormData.popularVoice || null,
          voice_name: voiceNameToSave || null,
        },
        inference: {
          model: companionConfig.inference?.model ?? null,
          temperature: companionConfig.inference?.temperature ?? 0.7,
          max_output_tokens: maxOutputTokens,
        },
        classifier: {
          ...companionConfig.classifier,
          enabled: classifierEnabled,
          model: classifierModel,
          instructions: classifierInstructions || null,
        },
        intro_message: {
          enabled: introMessageEnabled,
          text: (introMessageText || '').trim() || DEFAULT_INTRO_MESSAGE_TEXT,
          send_once_per_relationship: true,
        },
        knowledge: {
          ...companionConfig.knowledge,
          classifier_summary: knowledgeClassifierSummary || null,
        },
        context: {
          target_prompt_fraction: companionConfig.context?.target_prompt_fraction ?? 0.4,
          ...companionConfig.context,
          message_limit: messageLimit,
        },
      };
      await updateCompanion({ id: selectedCompanionId, config: mergedConfig });

      // Relationship-level setting for the active simulator user.
      if (includeProfileInPromptDirty && currentUserId) {
        const token = isAuthDisabled
          ? 'mock-dev-token'
          : await getToken(
              process.env.NEXT_PUBLIC_CLERK_JWT_TEMPLATE
                ? { template: process.env.NEXT_PUBLIC_CLERK_JWT_TEMPLATE }
                : undefined
            );

        await apiClient.ensureTestUser(selectedCompanionId, currentUserId, token);
        await apiClient.patchTestUserConfig(
          selectedCompanionId,
          currentUserId,
          { include_profile_in_prompt: includeProfileInPrompt },
          token
        );
        refreshRelationship();
      }

      setShowPendingBanner(false);
      // Trigger callbacks to restart any active voice sessions with the updated config
      await triggerOnSaveCallbacks();
    } catch (e) {
      console.error('Save failed', e);
    } finally {
      setIsSaving(false);
    }
  }, [
    selectedCompanionId,
    companionConfig,
    systemPrompt,
    memoryEnabled,
    formData.memoryEvaluationPrompt,
    formData.memoryVersion,
    formData.memoryIngestionPrompt,
    formData.memoryModel,
    formData.memoryMaxEntries,
    voiceNameToSave,
    triggerOnSaveCallbacks,
    derivedFormData.popularVoice,
    maxOutputTokens,
    classifierInstructions,
    classifierEnabled,
    classifierModel,
    introMessageEnabled,
    introMessageText,
    knowledgeClassifierSummary,
    includeProfileInPromptDirty,
    includeProfileInPrompt,
    currentUserId,
    getToken,
    isAuthDisabled,
    refreshRelationship,
    messageLimit,
    updateCompanion,
  ]);

  // Single effect to sync derived values to external config
  useEffect(() => {
    setConfig({ 
      systemPrompt,
      voiceConfig: currentVoiceConfig 
    });
  }, [systemPrompt, currentVoiceConfig, setConfig]);

  const formattedCompanionConfigPreview = useMemo(() => {
    if (!companionConfig) return 'Loading...';

    const truncate = (val: unknown, maxLen = 50): unknown => {
      if (typeof val === 'string' && val.length > maxLen) {
        return val.slice(0, maxLen) + '...';
      }
      return val;
    };

    // Filter out system_prompt (shown above) and voice catalog bloat
    const filtered = Object.fromEntries(
      Object.entries(companionConfig).filter(([key]) => key !== 'system_prompt')
    );

    // Clean up voice config - only show user selections
    if (filtered.voice) {
      const v = filtered.voice as Record<string, unknown>;
      filtered.voice = {
        preset: v.preset || null,
        voice_name: v.voice_name || null,
      };
    }

    // Clean up memory config - truncate long prompts
    if (filtered.memory) {
      const mem = filtered.memory as Record<string, unknown>;
      filtered.memory = {
        ...mem,
        memory_evaluation_prompt: truncate(mem.memory_evaluation_prompt),
        ingestion_prompt: truncate(mem.ingestion_prompt),
      };
    }

    return JSON.stringify(filtered, null, 2);
  }, [companionConfig]);


  const renderSystemPromptTab = () => {
    return (
      <div className="space-y-[20px]">
        {(showPendingBanner && isConnected && !bannerSuppressed) && (
          <div className="-mt-[20px] ">
            <PendingChangesBanner
              onDismiss={() => setShowPendingBanner(false)}
            />
          </div>
        )}
      <FormSection
        title="Full System Prompt"
        description="Complete system prompt including all categories above"
      >
        <Textarea
          minHeight={240}
          value={derivedFormData.fullSystemPrompt}
          onChange={(e) => {
            const newPrompt = e.target.value;
            handleInputChange('fullSystemPrompt')(newPrompt);
          }}
          placeholder="Complete system prompt will appear here..."
        />
      </FormSection>

      <FormSection
        title="Intro Message (WebSocket)"
        description="Automatically send a first assistant message when a text websocket connects, before the user sends anything."
      >
        <div className="space-y-4">
          <CustomSwitch
            checked={introMessageEnabled}
            onCheckedChange={(checked) => {
              setIntroMessageEnabled(checked);
              if (checked && !(introMessageText || '').trim()) {
                setIntroMessageText(DEFAULT_INTRO_MESSAGE_TEXT);
              }
              setShowPendingBanner(true);
            }}
            label="Enable intro message"
            labelTextClassName="text-base"
          />

          <Textarea
            minHeight={100}
            value={introMessageText}
            onChange={(e) => {
              setIntroMessageText(e.target.value);
              setShowPendingBanner(true);
            }}
            placeholder="Hi, how are you?"
          />

        </div>
      </FormSection>

      <FormSection
        title="Config"
        description="Companion configuration (read-only)"
      >
        <div className="bg-[var(--color-input-readonly)] px-4 py-3 max-h-[400px] overflow-y-auto">
          <pre className="text-[13px] text-[var(--color-text-readonly)] whitespace-pre-wrap font-mono">
            {formattedCompanionConfigPreview}
          </pre>
        </div>
      </FormSection>
    </div>
  );
  };

  // Get display info for current selection
  const currentPresetInfo = useMemo(() =>
    getPresetDisplayInfo(derivedFormData.popularVoice),
    [derivedFormData.popularVoice]
  );

  // Get preset options with display names as DropdownOption[]
  const presetOptions: DropdownOption[] = useMemo(() =>
    getPresetOptions().map(opt => ({
      value: opt.key,
      label: opt.displayName,
      description: opt.description,
    })),
    []
  );

  const renderVoiceTab = () => (
    <div className="space-y-5">
      {(showPendingBanner && isConnected && !bannerSuppressed) && (
        <div className="-mt-5 mb-5">
          <PendingChangesBanner
            onDismiss={() => setShowPendingBanner(false)}
          />
        </div>
      )}

      {/* Pipeline Selection */}
      <FormSection
        title="Voice Pipeline"
        description="Choose your AI model and voice providers."
      >
        <div className="space-y-3">
          <Dropdown
            options={presetOptions}
            value={derivedFormData.popularVoice}
            onChange={handlePopularVoiceChange}
            placeholder="Select a pipeline..."
          />

          {/* Pipeline indicator */}
          {currentPresetInfo && (
            <div className="flex items-center gap-2 text-xs text-white/50 px-1">
              <span className="text-white/70">{currentPresetInfo.stt}</span>
              <span className="text-white/30">→</span>
              <span className="text-white/70">{currentPresetInfo.llm}</span>
              <span className="text-white/30">→</span>
              <span className="text-white/70">{currentPresetInfo.tts}</span>
            </div>
          )}
        </div>
      </FormSection>

      {/* Voice Selection */}
      {derivedFormData.popularVoice && (
        <FormSection
          title="Voice"
          description="Choose a voice for text-to-speech."
        >
          <Dropdown
            options={availableVoiceOptions}
            value={derivedFormData.selectedVoice}
            onChange={handleVoiceChange}
            placeholder="Select a voice"
          />
        </FormSection>
      )}

      {/* Max Output Tokens */}
      <FormSection
        title="Max Output Tokens"
        description={`Limit the length of AI responses (default: 180${companionConfig?.inference?.max_output_tokens ? `, current: ${companionConfig.inference.max_output_tokens.toLocaleString()}` : ''}).`}
      >
        <div className="space-y-1.5">
          <input
            type="text"
            inputMode="numeric"
            value={maxOutputTokensInput}
            onChange={(e) => {
              const raw = e.target.value;
              setMaxOutputTokensInput(raw);

              if (raw === '') {
                setMaxOutputTokens(null);
                setMaxOutputTokensError(null);
                setShowPendingBanner(true);
                return;
              }

              if (!/^\d+$/.test(raw)) {
                setMaxOutputTokensError('Please enter a valid number');
                return;
              }

              const num = parseInt(raw, 10);
              if (num < 1 || num > 16384) {
                setMaxOutputTokensError('Must be between 1 and 16,384');
                return;
              }

              setMaxOutputTokensError(null);
              setMaxOutputTokens(num);
              setShowPendingBanner(true);
            }}
            placeholder="e.g. 4096"
            className={`w-32 bg-[var(--color-input-editable)] text-white font-book text-xs leading-tight px-[10px] py-2 placeholder:text-[var(--color-placeholder)] focus:outline-none transition-colors ${
              maxOutputTokensError ? 'ring-1 ring-red-500/50' : ''
            }`}
          />
          {maxOutputTokensError && (
            <p className="text-[11px] text-red-400">{maxOutputTokensError}</p>
          )}
        </div>
      </FormSection>

      {/* Temperature (future enhancement) */}
      {/* <FormSection
        title="Temperature"
        description="Controls randomness in responses."
      >
        <div className="flex items-center gap-3">
          <input
            type="range"
            min="0"
            max="1"
            step="0.1"
            value={0.7}
            className="flex-1"
          />
          <span className="text-sm text-white/60 w-8">0.7</span>
        </div>
      </FormSection> */}
    </div>
  );

  const renderMemoryTab = () => (
    <div className="space-y-5">
      {(showPendingBanner && isConnected && !bannerSuppressed) && (
        <div className="-mt-5 mb-5">
          <PendingChangesBanner
            onDismiss={() => setShowPendingBanner(false)}
          />
        </div>
      )}
      <CustomSwitch
        checked={memoryEnabled}
        onCheckedChange={(checked) => {
          setMemoryEnabled(checked);
          setShowPendingBanner(true);
        }}
        label="Memory"
        labelTextClassName="text-lg"
      />

      {/* Memory Version Selector */}
      <FormSection
        title="Memory Version"
        description="V1 uses vector retrieval for semantic search. V2 uses a scratchpad with LLM-based ingestion for structured memory management."
      >
        <Dropdown
          options={MEMORY_VERSION_OPTIONS}
          value={String(formData.memoryVersion)}
          onChange={(val) => {
            setFormData(prev => ({ ...prev, memoryVersion: Number(val) as 1 | 2 }));
            setShowPendingBanner(true);
          }}
          placeholder="Select memory version"
        />
      </FormSection>

      {/* V1-specific: Memory Evaluation Guidance */}
      {formData.memoryVersion === 1 && (
        <FormSection
          title="Memory Evaluation Guidance"
          description="Describe how your companion should decide what is worth remembering. We'll transform this into a structured rubric and append a strict 1–10 scoring harness automatically."
        >
          <Textarea
            minHeight={140}
            value={derivedFormData.memoryEvaluationPrompt}
            onChange={(e) => handleInputChange('memoryEvaluationPrompt')(e.target.value)}
            placeholder={`Examples of useful guidance:\n• Prioritize stable personal facts (name, roles, preferences), goals and deadlines, commitments, rules/constraints.\n• Capture strong likes/dislikes, long-term projects, and nuanced feedback that should shape future replies.\n• De-emphasize transient chit-chat, generic Q&A, and ephemeral details.\n(We will apply a consistent 1–10 scoring rubric for you.)`}
          />
        </FormSection>
      )}

      {/* V2-specific fields */}
      {formData.memoryVersion === 2 && (
        <>
          {/* Explore Memories Link */}
          <button
            onClick={() => setIsMemoryExplorerOpen(true)}
            disabled={!selectedCompanionId}
            className="group flex items-center gap-2 text-left transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            <span className="font-book text-lg text-white group-hover:text-white/80 transition-colors">
              Explore Memories
            </span>
            <Icon
              name="up-right-arrow"
              size={12}
              className="text-white group-hover:text-white/80 transition-colors"
            />
          </button>

          <FormSection
            title="Ingestion Guidelines"
            description="Customize what the AI should remember. The system automatically handles the conversation data and response format."
          >
            <div className="space-y-2">
              <div className="flex justify-end">
                <button
                  type="button"
                  onClick={() => {
                    setFormData(prev => ({ ...prev, memoryIngestionPrompt: DEFAULT_MEMORY_INGESTION_GUIDELINES }));
                    setShowPendingBanner(true);
                  }}
                  className="text-xs text-white/60 hover:text-white transition-colors underline"
                >
                  {formData.memoryIngestionPrompt ? 'Reset to Default' : 'Load Default Guidelines'}
                </button>
              </div>
              <Textarea
                minHeight={180}
                value={formData.memoryIngestionPrompt}
                onChange={(e) => {
                  setFormData(prev => ({ ...prev, memoryIngestionPrompt: e.target.value }));
                  setShowPendingBanner(true);
                }}
                placeholder="Leave empty to use default guidelines, or click 'Load Default Guidelines' above to view and customize."
              />
            </div>
          </FormSection>

          <FormSection
            title="Ingestion Model"
            description="The LLM model used to analyze conversations and extract memories."
          >
            <Dropdown
              options={MEMORY_V2_MODEL_OPTIONS}
              value={formData.memoryModel}
              onChange={(val) => {
                setFormData(prev => ({ ...prev, memoryModel: val }));
                setShowPendingBanner(true);
              }}
              placeholder="Select model"
            />
          </FormSection>

          <FormSection
            title="Max Entries"
            description="Maximum number of memory entries to store in the scratchpad (1-500)."
          >
            <input
              type="text"
              inputMode="numeric"
              value={formData.memoryMaxEntries}
              onChange={(e) => {
                const raw = e.target.value;
                if (raw === '') return;
                if (!/^\d+$/.test(raw)) return;
                const val = Math.max(1, Math.min(500, parseInt(raw, 10) || 100));
                setFormData(prev => ({ ...prev, memoryMaxEntries: val }));
                setShowPendingBanner(true);
              }}
              placeholder="e.g. 100"
              className="w-32 bg-[var(--color-input-editable)] text-white font-book text-xs leading-tight px-[10px] py-2 placeholder:text-[var(--color-placeholder)] focus:outline-none transition-colors"
            />
          </FormSection>
        </>
      )}

      {/**
       * Display core memories from DB (memories table) for reliability across reloads.
       * Fallback to config list if DB has none (e.g., before any core memories are added).
       */}
      <CoreMemoriesEditor
        items={coreMemories}
        onAdd={async (val) => {
          const text = (val || '').trim();
          if (!text) return;
          setFormData(prev => ({ ...prev, coreMemories: [...(prev.coreMemories || []), text] }));
          setShowPendingBanner(true);
          try {
            // Persist a corresponding memory row with high importance, system sender, and mark as core
            await createMemory.mutateAsync({ content: text, importance: 1.0, sender_type: 'system', is_core: true });
          } catch (e) { console.error('Failed to persist core memory item', e); }
        }}
        onUpdate={async (id, val) => {
          const text = (val || '').trim();
          if (!text) return;
          try {
            await updateMemory.mutateAsync({ memoryId: id, content: text, companionId: selectedCompanionId! });
          } catch (e) {
            console.error('Failed to update core memory item', e);
          }
        }}
        onDelete={async (id) => {
          try {
            await deleteMemory.mutateAsync({ memoryId: id, companionId: selectedCompanionId! });
          } catch (e) {
            console.error('Failed to delete core memory item', e);
          }
        }}
      />
    </div>
  );


  // Reset history state when companion changes
  useEffect(() => {
    setIsHistoryOpen(false);
    setHasOpenedHistory(false);
    setSelectedVersionId(null);
  }, [selectedCompanionId]);
  
  const availableVoiceOptions = useMemo(() => 
    getVoiceOptionsForPopularChoice(derivedFormData.popularVoice),
    [derivedFormData.popularVoice, getVoiceOptionsForPopularChoice]
  );

  return (
    <div className="h-full flex flex-col">
      {/* Settings Title + History - spans full width */}
      <div className="flex items-center justify-between pb-4">
        <h1 className="text-2xl font-light text-[var(--color-title-text)]">Settings</h1>
        <CompanionHistoryMenuStandalone
          isOpen={isHistoryOpen}
          setIsOpen={(open) => {
            setIsHistoryOpen(open);
            if (open) setHasOpenedHistory(true);
          }}
          isDisabled={!selectedCompanionId}
          versions={companionVersions}
          loading={versionsLoading}
          error={versionsError}
          isApplying={isApplyingVersion}
          onRefresh={refetchVersions}
          onSelect={handleApplyVersion}
          selectedVersionId={selectedVersionId}
          selectedVersionLabel={selectedVersionLabel}
        />
      </div>

      {/* Main layout: sidebar + content */}
      <div className="flex-1 flex min-h-0">
        {/* Vertical Tabs Sidebar - narrower width, text wraps gracefully */}
        <div className="w-20 min-w-[70px] flex-shrink-0 flex flex-col pr-3">
          {/* Vertical Tabs */}
          <VerticalTabs
            tabs={TABS}
            activeTab={activeTab}
            onTabChange={setActiveTab}
          />
        </div>

        {/* Main Content Area */}
        <div className="flex-1 flex flex-col min-w-0 pl-6 border-l border-white/10">
          {/* Scrollable Content */}
          <div className="flex-1 pb-4 overflow-y-auto" style={{ scrollbarWidth: 'none' }}>
            <style jsx>{`
              div::-webkit-scrollbar {
                display: none;
              }
            `}</style>
            {activeTab === 'system-prompt' && renderSystemPromptTab()}
            {activeTab === 'voice' && renderVoiceTab()}
            {activeTab === 'memory' && renderMemoryTab()}
            {activeTab === 'profile' && (
              <ProfileTab
                companionId={selectedCompanionId}
                currentUserId={currentUserId}
                includeProfileInPrompt={includeProfileInPrompt}
                includeProfileInPromptDisabled={!currentUserId}
                onIncludeProfileInPromptChange={setIncludeProfileInPrompt}
                onPendingChange={() => setShowPendingBanner(true)}
              />
            )}
            {activeTab === 'behaviors' && <BehaviorsTab companionId={selectedCompanionId} />}
            {activeTab === 'tools' && <ToolsTab companionId={selectedCompanionId} />}
            {activeTab === 'knowledge' && (
              <KnowledgeTab
                companionId={selectedCompanionId}
                classifierSummary={knowledgeClassifierSummary}
                onClassifierSummaryChange={(value) => {
                  setKnowledgeClassifierSummary(value);
                  setShowPendingBanner(true);
                }}
              />
            )}
            {activeTab === 'context' && (
              <ContextTab
                companionId={selectedCompanionId}
                classifierEnabled={classifierEnabled}
                classifierModel={classifierModel}
                classifierInstructions={classifierInstructions}
                messageLimit={messageLimit}
                onClassifierEnabledChange={(enabled) => {
                  setClassifierEnabled(enabled);
                  setShowPendingBanner(true);
                }}
                onClassifierModelChange={(model) => {
                  setClassifierModel(model);
                  setShowPendingBanner(true);
                }}
                onClassifierInstructionsChange={(value) => {
                  setClassifierInstructions(value);
                  setShowPendingBanner(true);
                }}
                onMessageLimitChange={(value) => {
                  setMessageLimit(value);
                  setShowPendingBanner(true);
                }}
              />
            )}
          </div>
        </div>
      </div>

      {/* Save Button - Full Width at Bottom */}
      <div className="flex-shrink-0 pt-4 pb-2">
        <button
          className={`w-full h-12 text-lg font-medium rounded-full transition-colors ${
            hasUnsavedChanges && !isSaving
              ? 'bg-white text-black hover:bg-white/90'
              : 'bg-[var(--color-input-readonly)] text-[var(--color-text-readonly)] cursor-not-allowed'
          }`}
          disabled={!hasUnsavedChanges || isSaving}
          onClick={handleSaveAll}
        >
          {isSaving ? 'Saving…' : 'Save'}
        </button>
      </div>

      {/* Memory Explorer Overlay */}
      {selectedCompanionId && (
        <MemoryExplorer
          companionId={selectedCompanionId}
          isOpen={isMemoryExplorerOpen}
          onClose={() => setIsMemoryExplorerOpen(false)}
        />
      )}
    </div>
  );
}

interface CompanionHistoryMenuStandaloneProps {
  isOpen: boolean;
  setIsOpen: (open: boolean) => void;
  isDisabled: boolean;
  versions?: CompanionVersionSummary[];
  loading: boolean;
  error: unknown;
  isApplying: boolean;
  onRefresh: () => Promise<unknown> | unknown;
  onSelect: (version: CompanionVersionSummary) => Promise<void> | void;
  selectedVersionId: string | null;
  selectedVersionLabel: string | null;
}

function CompanionHistoryMenuStandalone({
  isOpen,
  setIsOpen,
  isDisabled,
  versions,
  loading,
  error,
  isApplying,
  onRefresh,
  onSelect,
  selectedVersionId,
  selectedVersionLabel,
}: CompanionHistoryMenuStandaloneProps) {
  const menuRef = useRef<HTMLDivElement>(null);

  const closeMenu = useCallback(() => {
    setIsOpen(false);
  }, [setIsOpen]);

  const handleToggle = useCallback(() => {
    if (isDisabled) return;
    setIsOpen(!isOpen);
  }, [isDisabled, isOpen, setIsOpen]);

  // Close on outside click
  useEffect(() => {
    if (!isOpen) return;
    function handleClickOutside(e: MouseEvent) {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        closeMenu();
      }
    }
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [isOpen, closeMenu]);

  const handleRefresh = useCallback(() => {
    void onRefresh();
  }, [onRefresh]);

  const errorMessage = useMemo(() => {
    if (!error) return null;
    if (error instanceof Error) return error.message;
    if (typeof error === 'string') return error;
    return 'Unable to load versions.';
  }, [error]);

  const buttonLabel = selectedVersionLabel ? `History (${selectedVersionLabel})` : 'History';
  const disableInteractions = isApplying;

  const formatTimestamp = useCallback((value: string) => {
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) {
      return value;
    }
    return format(parsed, 'MMM d, yyyy h:mm a');
  }, []);

  return (
    <div className="relative" ref={menuRef}>
      <button
        type="button"
        onClick={handleToggle}
        disabled={isDisabled}
        className={`flex items-center gap-1 px-3 py-1.5 text-sm transition-colors bg-[var(--color-dropdown-bg)] ${
          isDisabled ? 'text-white/30 cursor-not-allowed' : 'text-white hover:text-white/80'
        }`}
      >
        <span>{buttonLabel}</span>
        <Icon name={isOpen ? 'chevron-up' : 'chevron-down'} size={12} className="text-white/60" />
      </button>
      {isOpen && (
        <div
          className="absolute right-0 z-30 mt-2 w-[260px] border border-white/10 bg-[var(--color-dropdown-bg)] shadow-xl"
        >
          <div className="px-3 pt-2 pb-1 text-xs uppercase tracking-wide text-white/40">Version history</div>
          {isApplying && (
            <div className="px-3 pb-1 text-xs text-white/50">Applying selected version…</div>
          )}
          <div className="max-h-64 overflow-y-auto py-1">
            {loading ? (
              <div className="px-3 py-2 text-sm text-white/60">Loading history…</div>
            ) : errorMessage ? (
              <div className="px-3 py-2 text-sm text-white/60 space-y-2">
                <div>{errorMessage}</div>
                <button
                  type="button"
                  className="text-xs text-white/60 hover:text-white"
                  onClick={handleRefresh}
                >
                  Retry
                </button>
              </div>
            ) : versions && versions.length > 0 ? (
              versions.map((version) => {
                const isActive = selectedVersionId === version.id;
                return (
                  <button
                    key={version.id}
                    type="button"
                    disabled={disableInteractions}
                    onClick={() => {
                      void onSelect(version);
                    }}
                    className={`w-full px-3 py-2 text-left text-sm transition-colors ${
                      isActive
                        ? 'bg-white/10 text-white'
                        : 'text-white/80 hover:text-white hover:bg-white/10'
                    } ${disableInteractions ? 'cursor-wait opacity-60' : ''}`}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-medium">v{version.version_number}</span>
                      <span className="text-xs text-white/50">{formatTimestamp(version.created_at)}</span>
                    </div>
                    <div className="mt-1 text-xs leading-snug text-white/50 line-clamp-2">
                      {(() => {
                        const text = version.config.system_prompt.identity
                          || version.config.system_prompt.personality
                          || version.config.system_prompt.full_system_prompt
                          || 'No description provided.';
                        return text.length > 60 ? text.slice(0, 60) + '...' : text;
                      })()}
                    </div>
                  </button>
                );
              })
            ) : (
              <div className="px-3 py-2 text-sm text-white/60">No saved versions yet.</div>
            )}
          </div>
          {!loading && !errorMessage && versions && versions.length > 0 && (
            <div className="border-t border-white/10 px-3 py-1.5">
              <button
                type="button"
                className="text-xs text-white/60 hover:text-white"
                onClick={handleRefresh}
                disabled={disableInteractions}
              >
                Refresh history
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function CoreMemoriesEditor({ items, onAdd, onUpdate, onDelete }: {
  items: MemoryItem[];
  onAdd: (val: string) => void;
  onUpdate: (id: string, val: string) => void;
  onDelete: (id: string) => void;
}) {
  const [draft, setDraft] = useState('');
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editingVal, setEditingVal] = useState('');

  return (
    <FormSection
      title="Core Memories"
      description="Add permanent, high-importance memories your companion should always consider."
    >
      <div className="space-y-2">
        <Textarea
          minHeight={60}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="e.g., The user values directness and privacy; always ask before storing sensitive info."
        />
        <div className="flex justify-end">
          <button
            className="px-4 py-2 text-sm bg-white/10 hover:bg-white/20 transition-colors"
            onClick={() => { onAdd(draft); setDraft(''); }}
          >Add</button>
        </div>

        {items.length > 0 && <div className="mt-3.5 h-px bg-white/10" />}
        <div className="space-y-2">
          {items.map((m) => {
            const isPending = Boolean(m.pending);
            return (
              <div key={m.id} className="flex items-start gap-3 p-2 rounded hover:bg-white/5">
                <div className="flex-1">
                  {editingId === m.id && !isPending ? (
                    <Textarea minHeight={60} value={editingVal} onChange={(e) => setEditingVal(e.target.value)} />
                  ) : (
                    <div className="text-sm text-white">{m.content}</div>
                  )}
                </div>
                <div className="flex items-center  gap-2 pt-1">
                  {isPending ? (
                    <div className="-translate-y-0.5 px-2 py-0.5 rounded-[4px] bg-[color:var(--color-brand-bg)]/70 text-[color:var(--color-brand-solid)]">
                      <span className="text-xs">Syncing...</span>
                    </div>
                  ) : editingId === m.id ? (
                    <>
                      <button className="text-xs px-2 py-1 bg-white/10 hover:bg-white/20 transition-colors" onClick={() => { onUpdate(m.id, editingVal.trim()); setEditingId(null); setEditingVal(''); }}>Save</button>
                      <button className="text-xs px-2 py-1 bg-white/5 hover:bg-white/10 transition-colors" onClick={() => { setEditingId(null); setEditingVal(''); }}>Cancel</button>
                    </>
                  ) : (
                    <>
                      <button
                        className="text-white/70 hover:text-white"
                        title="Edit"
                        onClick={() => {
                          if (isPending) return;
                          setEditingId(m.id);
                          setEditingVal(m.content);
                        }}
                      >
                        <Icon name="pencil" size={14} />
                      </button>
                      <button
                        className="text-white/70 hover:text-white"
                        title="Delete"
                        onClick={() => onDelete(m.id)}
                      >
                        <Icon name="trash" size={14} />
                      </button>
                    </>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </FormSection>
  );
}
