import type {
  VoiceConfig as SessionVoiceConfig,
  STTProvider,
  LLMProvider,
  TTSProvider,
} from '@/hooks/useWebSocketSessionInner';

export const VOICE_NAMES = {
  openai: ['alloy', 'ash', 'ballad', 'coral', 'echo', 'sage', 'shimmer', 'verse'],
  elevenlabs: ['Sarah', 'George', 'Callum', 'Brad', 'Joseph', 'Charlotte', 'Matilda', 'Will', 'Can'],
  cartesia: ['Sophie', 'Savannah', 'Brooke', 'Griffin', 'Zia', 'Carson', 'Wise Lady', 'Ethan'],
} as const;


export type VoiceProvider = keyof typeof VOICE_NAMES;

export interface VoicePresetConfig {
  displayName: string;
  description?: string;
  defaultModel: LLMProvider;
  sttProvider: STTProvider;
  ttsProvider: TTSProvider;
  voiceProvider: VoiceProvider;
}

// New short-key preset format
export const VOICE_PRESETS: Record<string, VoicePresetConfig> = {
  'haiku-4.5-elevenlabs': {
    displayName: 'Claude Haiku 4.5',
    description: 'Recommended',
    defaultModel: 'claude-haiku-4.5',
    sttProvider: 'cartesia',
    ttsProvider: 'elevenlabs',
    voiceProvider: 'elevenlabs',
  },
  'sonnet-4.5-elevenlabs': {
    displayName: 'Claude Sonnet 4.5',
    defaultModel: 'claude-sonnet-4.5',
    sttProvider: 'cartesia',
    ttsProvider: 'elevenlabs',
    voiceProvider: 'elevenlabs',
  },
  'sonnet-4-elevenlabs': {
    displayName: 'Claude Sonnet 4',
    defaultModel: 'claude-sonnet-4',
    sttProvider: 'cartesia',
    ttsProvider: 'elevenlabs',
    voiceProvider: 'elevenlabs',
  },
  'sonnet-3.7-elevenlabs': {
    displayName: 'Claude Sonnet 3.7',
    defaultModel: 'claude-sonnet-3.7',
    sttProvider: 'cartesia',
    ttsProvider: 'elevenlabs',
    voiceProvider: 'elevenlabs',
  },
  'sonnet-4.5-cartesia': {
    displayName: 'Claude Sonnet 4.5 + Cartesia',
    defaultModel: 'claude-sonnet-4.5',
    sttProvider: 'cartesia',
    ttsProvider: 'cartesia',
    voiceProvider: 'cartesia',
  },
  'sonnet-4-cartesia': {
    displayName: 'Claude Sonnet 4 + Cartesia',
    defaultModel: 'claude-sonnet-4',
    sttProvider: 'cartesia',
    ttsProvider: 'cartesia',
    voiceProvider: 'cartesia',
  },
  'sonnet-3.7-cartesia': {
    displayName: 'Claude Sonnet 3.7 + Cartesia',
    defaultModel: 'claude-sonnet-3.7',
    sttProvider: 'cartesia',
    ttsProvider: 'cartesia',
    voiceProvider: 'cartesia',
  },
  'gpt4o-elevenlabs': {
    displayName: 'GPT-4o',
    defaultModel: 'openai-gpt4o',
    sttProvider: 'cartesia',
    ttsProvider: 'elevenlabs',
    voiceProvider: 'elevenlabs',
  },
  'gpt4o-cartesia': {
    displayName: 'GPT-4o + Cartesia',
    defaultModel: 'openai-gpt4o',
    sttProvider: 'cartesia',
    ttsProvider: 'cartesia',
    voiceProvider: 'cartesia',
  },
  'gpt5.1-elevenlabs': {
    displayName: 'GPT-5.1',
    description: 'Latest',
    defaultModel: 'openai-gpt5.1',
    sttProvider: 'cartesia',
    ttsProvider: 'elevenlabs',
    voiceProvider: 'elevenlabs',
  },
  'gemini-flash-elevenlabs': {
    displayName: 'Gemini 2.5 Flash',
    description: 'Fast',
    defaultModel: 'gemini-2.5-flash',
    sttProvider: 'cartesia',
    ttsProvider: 'elevenlabs',
    voiceProvider: 'elevenlabs',
  },
  'kimi-k2-elevenlabs': {
    displayName: 'Kimi K2',
    defaultModel: 'moonshot-kimi-k2',
    sttProvider: 'cartesia',
    ttsProvider: 'elevenlabs',
    voiceProvider: 'elevenlabs',
  },
  'gpt4o-mini-elevenlabs': {
    displayName: 'GPT-4o Mini',
    description: 'Budget',
    defaultModel: 'openai-gpt4o-mini',
    sttProvider: 'deepgram',
    ttsProvider: 'elevenlabs',
    voiceProvider: 'elevenlabs',
  },
} as const;

// Migration from old long-format keys to new short keys
export const PRESET_MIGRATIONS: Record<string, keyof typeof VOICE_PRESETS> = {
  // Anthropic + ElevenLabs
  'sonnet-4.5 - Anthropic (LLM) - Elevenlabs (STT - TTS)': 'sonnet-4.5-elevenlabs',
  'sonnet-4.5 - Cartesia (STT) - Anthropic (LLM) - Elevenlabs (TTS)': 'sonnet-4.5-elevenlabs',
  'sonnet-4 - Anthropic (LLM) - Elevenlabs (STT - TTS)': 'sonnet-4-elevenlabs',
  'sonnet-4 - Cartesia (STT) - Anthropic (LLM) - Elevenlabs (TTS)': 'sonnet-4-elevenlabs',
  'sonnet-3.7 - Anthropic (LLM) - Elevenlabs (STT - TTS)': 'sonnet-3.7-elevenlabs',
  'sonnet-3.7 - Cartesia (STT) - Anthropic (LLM) - Elevenlabs (TTS)': 'sonnet-3.7-elevenlabs',
  // Anthropic + Cartesia
  'sonnet-4.5 - Anthropic (LLM) - Cartesia (STT - TTS)': 'sonnet-4.5-cartesia',
  'sonnet-4.5 - Cartesia (STT) - Anthropic (LLM) - Cartesia (TTS)': 'sonnet-4.5-cartesia',
  'sonnet-4 - Anthropic (LLM) - Cartesia (STT - TTS)': 'sonnet-4-cartesia',
  'sonnet-4 - Cartesia (STT) - Anthropic (LLM) - Cartesia (TTS)': 'sonnet-4-cartesia',
  'sonnet-3.7 - Anthropic (LLM) - Cartesia (STT - TTS)': 'sonnet-3.7-cartesia',
  'sonnet-3.7 - Cartesia (STT) - Anthropic (LLM) - Cartesia (TTS)': 'sonnet-3.7-cartesia',
  // OpenAI
  'gpt4o - OpenAI (LLM) - Elevenlabs (STT - TTS)': 'gpt4o-elevenlabs',
  'gpt4o - Cartesia (STT) - OpenAI (LLM) - Elevenlabs (TTS)': 'gpt4o-elevenlabs',
  'gpt4o - OpenAI (LLM) - Cartesia (STT - TTS)': 'gpt4o-cartesia',
  'gpt4o-mini - OpenAI (STT) - OpenAI (LLM) - Elevenlabs (TTS)': 'gpt4o-mini-elevenlabs',
  'gpt4o-mini - Deepgram (STT) - OpenAI (LLM) - Elevenlabs (TTS)': 'gpt4o-mini-elevenlabs',
  'gpt4o-mini - Cartesia (STT) - OpenAI (LLM) - Elevenlabs (TTS)': 'gpt4o-mini-elevenlabs',
  'gpt-5.1 - Cartesia (STT) - OpenAI (LLM) - Elevenlabs (TTS)': 'gpt5.1-elevenlabs',
  // Gemini
  'gemini-2.5-flash - Google (LLM) - Elevenlabs (STT - TTS)': 'gemini-flash-elevenlabs',
  'gemini-2.5-flash - Cartesia (STT) - Google (LLM) - Elevenlabs (TTS)': 'gemini-flash-elevenlabs',
  // Moonshot
  'kimi-k2 - Moonshot (LLM) - Elevenlabs (STT - TTS)': 'kimi-k2-elevenlabs',
  'kimi-k2 - Cartesia (STT) - Moonshot (LLM) - Elevenlabs (TTS)': 'kimi-k2-elevenlabs',
  // Legacy OpenAI Realtime
  'OpenAI - speech-to-speech': 'gpt4o-elevenlabs',
  'OpenAI - speech-to-speech (Mini)': 'gpt4o-mini-elevenlabs',
  // Legacy Sonnet 3.5
  'sonnet-3.5 - Cartesia (STT) - Anthropic (LLM) - Elevenlabs (TTS)': 'sonnet-3.7-elevenlabs',
  'sonnet-3.5 - Cartesia (STT) - Anthropic (LLM) - Cartesia (TTS)': 'sonnet-3.7-cartesia',
} as const;

// DEPRECATED: Legacy interface and data structure kept for backward compatibility
export interface PopularOptionConfig {
  label: string;
  displayName: string;
  description?: string;
  voiceProvider: VoiceProvider;
  config: Omit<SessionVoiceConfig, 'voice_name'>;
}

// DEPRECATED: Use VOICE_PRESETS instead
export const POPULAR_OPTIONS: Record<string, PopularOptionConfig> = {
  'sonnet-4.5 - Cartesia (STT) - Anthropic (LLM) - Elevenlabs (TTS)': {
    label: 'sonnet-4.5 - Cartesia (STT) - Anthropic (LLM) - Elevenlabs (TTS)',
    displayName: 'Claude Sonnet 4.5',
    description: 'Recommended',
    voiceProvider: 'elevenlabs',
    config: {
      pipeline_type: 'stt-llm-tts',
      stt_provider: 'cartesia',
      llm_provider: 'claude-sonnet-4.5',
      tts_provider: 'elevenlabs',
      temperature: 0.7,
    },
  },
  'sonnet-4 - Cartesia (STT) - Anthropic (LLM) - Elevenlabs (TTS)': {
    label: 'sonnet-4 - Cartesia (STT) - Anthropic (LLM) - Elevenlabs (TTS)',
    displayName: 'Claude Sonnet 4',
    voiceProvider: 'elevenlabs',
    config: {
      pipeline_type: 'stt-llm-tts',
      stt_provider: 'cartesia',
      llm_provider: 'claude-sonnet-4',
      tts_provider: 'elevenlabs',
      temperature: 0.7,
    },
  },
  'sonnet-4.5 - Cartesia (STT) - Anthropic (LLM) - Cartesia (TTS)': {
    label: 'sonnet-4.5 - Cartesia (STT) - Anthropic (LLM) - Cartesia (TTS)',
    displayName: 'Claude Sonnet 4.5 + Cartesia',
    voiceProvider: 'cartesia',
    config: {
      pipeline_type: 'stt-llm-tts',
      stt_provider: 'cartesia',
      llm_provider: 'claude-sonnet-4.5',
      tts_provider: 'cartesia',
      temperature: 0.7,
    },
  },
  'sonnet-4 - Cartesia (STT) - Anthropic (LLM) - Cartesia (TTS)': {
    label: 'sonnet-4 - Cartesia (STT) - Anthropic (LLM) - Cartesia (TTS)',
    displayName: 'Claude Sonnet 4 + Cartesia',
    voiceProvider: 'cartesia',
    config: {
      pipeline_type: 'stt-llm-tts',
      stt_provider: 'cartesia',
      llm_provider: 'claude-sonnet-4',
      tts_provider: 'cartesia',
      temperature: 0.7,
    },
  },
  'gpt4o - Cartesia (STT) - OpenAI (LLM) - Elevenlabs (TTS)': {
    label: 'gpt4o - Cartesia (STT) - OpenAI (LLM) - Elevenlabs (TTS)',
    displayName: 'GPT-4o',
    voiceProvider: 'elevenlabs',
    config: {
      pipeline_type: 'stt-llm-tts',
      stt_provider: 'cartesia',
      llm_provider: 'openai-gpt4o',
      tts_provider: 'elevenlabs',
      temperature: 0.7,
    },
  },
  'sonnet-3.7 - Cartesia (STT) - Anthropic (LLM) - Elevenlabs (TTS)': {
    label: 'sonnet-3.7 - Cartesia (STT) - Anthropic (LLM) - Elevenlabs (TTS)',
    displayName: 'Claude Sonnet 3.7',
    voiceProvider: 'elevenlabs',
    config: {
      pipeline_type: 'stt-llm-tts',
      stt_provider: 'cartesia',
      llm_provider: 'claude-sonnet-3.7',
      tts_provider: 'elevenlabs',
      temperature: 0.7,
    },
  },
  'sonnet-3.7 - Cartesia (STT) - Anthropic (LLM) - Cartesia (TTS)': {
    label: 'sonnet-3.7 - Cartesia (STT) - Anthropic (LLM) - Cartesia (TTS)',
    displayName: 'Claude Sonnet 3.7 + Cartesia',
    voiceProvider: 'cartesia',
    config: {
      pipeline_type: 'stt-llm-tts',
      stt_provider: 'cartesia',
      llm_provider: 'claude-sonnet-3.7',
      tts_provider: 'cartesia',
      temperature: 0.7,
    },
  },
  'gpt4o-mini - OpenAI (STT) - OpenAI (LLM) - Elevenlabs (TTS)': {
    label: 'gpt4o-mini - OpenAI (STT) - OpenAI (LLM) - Elevenlabs (TTS)',
    displayName: 'GPT-4o Mini',
    description: 'Budget',
    voiceProvider: 'elevenlabs',
    config: {
      pipeline_type: 'stt-llm-tts',
      stt_provider: 'openai',
      llm_provider: 'openai-gpt4o-mini',
      tts_provider: 'elevenlabs',
      temperature: 0.7,
    },
  },
  'gpt4o-mini - Deepgram (STT) - OpenAI (LLM) - Elevenlabs (TTS)': {
    label: 'gpt4o-mini - Deepgram (STT) - OpenAI (LLM) - Elevenlabs (TTS)',
    displayName: 'GPT-4o Mini + Deepgram',
    description: 'Budget',
    voiceProvider: 'elevenlabs',
    config: {
      pipeline_type: 'stt-llm-tts',
      stt_provider: 'deepgram',
      llm_provider: 'openai-gpt4o-mini',
      tts_provider: 'elevenlabs',
      temperature: 0.7,
    },
  },
  'gpt-5.1 - Cartesia (STT) - OpenAI (LLM) - Elevenlabs (TTS)': {
    label: 'gpt-5.1 - Cartesia (STT) - OpenAI (LLM) - Elevenlabs (TTS)',
    displayName: 'GPT-5.1',
    description: 'Latest',
    voiceProvider: 'elevenlabs',
    config: {
      pipeline_type: 'stt-llm-tts',
      stt_provider: 'cartesia',
      llm_provider: 'openai-gpt5.1',
      tts_provider: 'elevenlabs',
      temperature: 0.7,
    },
  },
  'gemini-2.5-flash - Cartesia (STT) - Google (LLM) - Elevenlabs (TTS)': {
    label: 'gemini-2.5-flash - Cartesia (STT) - Google (LLM) - Elevenlabs (TTS)',
    displayName: 'Gemini 2.5 Flash',
    description: 'Fast',
    voiceProvider: 'elevenlabs',
    config: {
      pipeline_type: 'stt-llm-tts',
      stt_provider: 'cartesia',
      llm_provider: 'gemini-2.5-flash',
      tts_provider: 'elevenlabs',
      temperature: 0.7,
    },
  },
  'kimi-k2 - Cartesia (STT) - Moonshot (LLM) - Elevenlabs (TTS)': {
    label: 'kimi-k2 - Cartesia (STT) - Moonshot (LLM) - Elevenlabs (TTS)',
    displayName: 'Kimi K2',
    voiceProvider: 'elevenlabs',
    config: {
      pipeline_type: 'stt-llm-tts',
      stt_provider: 'cartesia',
      llm_provider: 'moonshot-kimi-k2',
      tts_provider: 'elevenlabs',
      temperature: 0.7,
    },
  },
} as const;

// DEPRECATED: Use PRESET_MIGRATIONS instead
export const POPULAR_OPTION_MIGRATIONS: Record<string, keyof typeof POPULAR_OPTIONS> = {
  'gpt4o-mini - Cartesia (STT) - OpenAI (LLM) - Elevenlabs (TTS)':
    'gemini-2.5-flash - Cartesia (STT) - Google (LLM) - Elevenlabs (TTS)',
  'sonnet-3.5 - Cartesia (STT) - Anthropic (LLM) - Elevenlabs (TTS)':
    'sonnet-3.7 - Cartesia (STT) - Anthropic (LLM) - Elevenlabs (TTS)',
  'sonnet-3.5 - Cartesia (STT) - Anthropic (LLM) - Cartesia (TTS)':
    'sonnet-3.7 - Cartesia (STT) - Anthropic (LLM) - Cartesia (TTS)',
  'OpenAI - speech-to-speech': 'gpt4o - Cartesia (STT) - OpenAI (LLM) - Elevenlabs (TTS)',
  'OpenAI - speech-to-speech (Mini)': 'gpt4o - Cartesia (STT) - OpenAI (LLM) - Elevenlabs (TTS)',
  // Short key → long key mappings (for server-migrated presets)
  'sonnet-4.5-elevenlabs': 'sonnet-4.5 - Cartesia (STT) - Anthropic (LLM) - Elevenlabs (TTS)',
  'sonnet-4-elevenlabs': 'sonnet-4 - Cartesia (STT) - Anthropic (LLM) - Elevenlabs (TTS)',
  'sonnet-3.7-elevenlabs': 'sonnet-3.7 - Cartesia (STT) - Anthropic (LLM) - Elevenlabs (TTS)',
  'sonnet-4.5-cartesia': 'sonnet-4.5 - Cartesia (STT) - Anthropic (LLM) - Cartesia (TTS)',
  'sonnet-4-cartesia': 'sonnet-4 - Cartesia (STT) - Anthropic (LLM) - Cartesia (TTS)',
  'sonnet-3.7-cartesia': 'sonnet-3.7 - Cartesia (STT) - Anthropic (LLM) - Cartesia (TTS)',
  'gpt4o-elevenlabs': 'gpt4o - Cartesia (STT) - OpenAI (LLM) - Elevenlabs (TTS)',
  'gpt4o-cartesia': 'gpt4o - Cartesia (STT) - OpenAI (LLM) - Cartesia (TTS)',
  'gpt4o-mini-elevenlabs': 'gpt4o-mini - Deepgram (STT) - OpenAI (LLM) - Elevenlabs (TTS)',
  'gemini-flash-elevenlabs': 'gemini-2.5-flash - Cartesia (STT) - Google (LLM) - Elevenlabs (TTS)',
  'kimi-k2-elevenlabs': 'kimi-k2 - Cartesia (STT) - Moonshot (LLM) - Elevenlabs (TTS)',
} as const;

export const VOICE_POPULAR_OPTIONS = Object.keys(POPULAR_OPTIONS) as (keyof typeof POPULAR_OPTIONS)[];

// New preset key list for UI
export const VOICE_PRESET_KEYS = Object.keys(VOICE_PRESETS) as (keyof typeof VOICE_PRESETS)[];

// Resolve preset key - handles both old and new format keys
export const resolvePresetKey = (key: string | undefined | null): keyof typeof VOICE_PRESETS | null => {
  if (!key) return null;
  // Check if it's already a new format key
  if (key in VOICE_PRESETS) {
    return key as keyof typeof VOICE_PRESETS;
  }
  // Try migration from old format
  const migrated = PRESET_MIGRATIONS[key];
  return migrated ?? null;
};

// Get preset config by key (handles both formats)
export const getPreset = (key: string | undefined | null): VoicePresetConfig | undefined => {
  const resolved = resolvePresetKey(key);
  return resolved ? VOICE_PRESETS[resolved] : undefined;
};

// Format provider name for display
const formatProvider = (provider: string | undefined): string => {
  if (!provider) return 'Unknown';
  const map: Record<string, string> = {
    'cartesia': 'Cartesia',
    'openai': 'OpenAI',
    'deepgram': 'Deepgram',
    'elevenlabs': 'ElevenLabs',
    'ultravox': 'Ultravox',
    'claude-sonnet-4.5': 'Claude 4.5',
    'claude-sonnet-4': 'Claude 4',
    'claude-sonnet-3.7': 'Claude 3.7',
    'openai-gpt4o': 'GPT-4o',
    'openai-gpt4o-mini': 'GPT-4o Mini',
    'openai-gpt5.1': 'GPT-5.1',
    'gemini-2.5-flash': 'Gemini Flash',
    'moonshot-kimi-k2': 'Kimi K2',
  };
  return map[provider] || provider;
};

// Helper to get display info for a preset (works with both formats)
export const getPresetDisplayInfo = (key: string | undefined | null): {
  displayName: string;
  description?: string;
  stt: string;
  llm: string;
  tts: string;
} | null => {
  // Try new format first
  const newPreset = getPreset(key);
  if (newPreset) {
    return {
      displayName: newPreset.displayName,
      description: newPreset.description,
      stt: formatProvider(newPreset.sttProvider),
      llm: formatProvider(newPreset.defaultModel),
      tts: formatProvider(newPreset.ttsProvider),
    };
  }

  // Fall back to legacy format
  const legacyPreset = getResolvedPopularOption(key);
  if (!legacyPreset) return null;

  return {
    displayName: legacyPreset.displayName,
    description: legacyPreset.description,
    stt: formatProvider(legacyPreset.config.stt_provider),
    llm: formatProvider(legacyPreset.config.llm_provider),
    tts: formatProvider(legacyPreset.config.tts_provider),
  };
};

// Get list of presets with display info for rendering (new format)
export const getVoicePresetOptions = (): Array<{
  key: string;
  displayName: string;
  description?: string;
}> => {
  return Object.entries(VOICE_PRESETS).map(([key, config]) => ({
    key,
    displayName: config.displayName,
    description: config.description,
  }));
};

// Get list of presets with display info for rendering (uses new short-key format)
export const getPresetOptions = (): Array<{
  key: string;
  displayName: string;
  description?: string;
}> => {
  return Object.entries(VOICE_PRESETS).map(([key, config]) => ({
    key,
    displayName: config.displayName,
    description: config.description,
  }));
};

export const resolvePopularOptionKey = (
  key: string | undefined | null,
): keyof typeof POPULAR_OPTIONS | null => {
  if (!key) return null;
  if (key in POPULAR_OPTIONS) {
    return key as keyof typeof POPULAR_OPTIONS;
  }
  const migrated = POPULAR_OPTION_MIGRATIONS[key];
  return migrated ?? null;
};

export const getResolvedPopularOption = (
  key: string | undefined | null,
): PopularOptionConfig | undefined => {
  if (!key) return undefined;

  // First try new format (VOICE_PRESETS)
  const newPreset = VOICE_PRESETS[key];
  if (newPreset) {
    // Convert VOICE_PRESETS format to PopularOptionConfig format
    return {
      label: key,
      displayName: newPreset.displayName,
      description: newPreset.description,
      voiceProvider: newPreset.voiceProvider,
      config: {
        pipeline_type: 'stt-llm-tts',
        stt_provider: newPreset.sttProvider,
        llm_provider: newPreset.defaultModel,
        tts_provider: newPreset.ttsProvider,
        temperature: 0.7,
      },
    };
  }

  // Fall back to old format (POPULAR_OPTIONS)
  const resolved = resolvePopularOptionKey(key);
  return resolved ? POPULAR_OPTIONS[resolved] : undefined;
};

export const defaultVoiceForProvider = (provider: VoiceProvider): string => {
  const list = VOICE_NAMES[provider];
  return list[0] ?? 'alloy';
};

export interface VoiceSnapshot {
  voicePipeline: Record<string, unknown>;
  llmProvider?: string;
  temperature?: number;
}

export const pipelineFromVoiceConfig = (config: SessionVoiceConfig): Record<string, unknown> => {
  const pipeline: Record<string, unknown> = {
    pipeline_type: config.pipeline_type,
  };
  if (config.stt_provider) pipeline.stt_provider = config.stt_provider;
  if (config.llm_provider) pipeline.llm_provider = config.llm_provider;
  if (config.tts_provider) pipeline.tts_provider = config.tts_provider;
  if (config.voice_name) pipeline.voice_name = config.voice_name;
  if (typeof config.temperature === 'number') pipeline.temperature = config.temperature;
  return pipeline;
};

export const voiceConfigToSnapshot = (
  config: SessionVoiceConfig | null | undefined,
): VoiceSnapshot | null => {
  if (!config) return null;
  const pipeline = pipelineFromVoiceConfig(config);
  const llmProvider = config.llm_provider;
  const temperature = typeof config.temperature === 'number' ? config.temperature : undefined;
  return {
    voicePipeline: pipeline,
    llmProvider,
    temperature,
  };
};

export const buildVoiceSnapshotFromPreset = (
  key: string | undefined | null,
  options: {
    voiceName?: string | null;
    temperature?: number | null;
  } = {},
): VoiceSnapshot | null => {
  const preset = getResolvedPopularOption(key);
  const voiceName = options.voiceName && options.voiceName.trim() ? options.voiceName.trim() : undefined;
  const requestedTemperature =
    typeof options.temperature === 'number' ? options.temperature : undefined;

  if (!preset) {
    if (!voiceName) return null;
    const temperature = requestedTemperature ?? 0.7;
    // Default to STT-LLM-TTS pipeline with OpenAI providers
    return {
      voicePipeline: {
        pipeline_type: 'stt-llm-tts',
        voice_name: voiceName,
        stt_provider: 'openai',
        llm_provider: 'openai-gpt4o',
        tts_provider: 'openai',
        temperature,
      },
      llmProvider: 'openai-gpt4o',
      temperature,
    };
  }

  const pipeline: Record<string, unknown> = {
    pipeline_type: preset.config.pipeline_type,
  };

  if (preset.config.stt_provider) pipeline.stt_provider = preset.config.stt_provider;
  if (preset.config.llm_provider) pipeline.llm_provider = preset.config.llm_provider;
  if (preset.config.tts_provider) pipeline.tts_provider = preset.config.tts_provider;

  const resolvedVoiceName = voiceName ?? defaultVoiceForProvider(preset.voiceProvider);
  pipeline.voice_name = resolvedVoiceName;

  const temperature = requestedTemperature ?? preset.config.temperature ?? 0.7;
  pipeline.temperature = temperature;

  const llmProvider = preset.config.llm_provider;

  return {
    voicePipeline: pipeline,
    llmProvider,
    temperature,
  };
};
