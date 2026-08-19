// Voice config interface matching the session voice config structure
export interface SessionVoiceConfig {
  pipeline_type: 'openai-realtime' | 'stt-llm-tts';
  stt_provider?: string;
  llm_provider?: string;
  tts_provider?: string;
  voice_name?: string;
  realtimeModel?: string;
  temperature?: number;
}

export const VOICE_NAMES = {
  openai: ['alloy', 'ash', 'ballad', 'coral', 'echo', 'sage', 'shimmer', 'verse'],
  elevenlabs: ['Sarah', 'George', 'Callum', 'Charlotte', 'Matilda', 'Will', 'Tin Can'],
  cartesia: ['Sophie', 'Savannah', 'Brooke', 'Griffin', 'Zia', 'Carson', 'Wise Lady', 'Ethan'],
} as const;

export const DEFAULT_REALTIME_MODEL = 'gpt-4o-realtime-preview-2025-06-03';

export type VoiceProvider = keyof typeof VOICE_NAMES;

export interface PopularOptionConfig {
  label: string;
  voiceProvider: VoiceProvider;
  config: Omit<SessionVoiceConfig, 'voice_name'>;
}

export const POPULAR_OPTIONS: Record<string, PopularOptionConfig> = {
  'sonnet-4.5 - Cartesia (STT) - Anthropic (LLM) - Elevenlabs (TTS)': {
    label: 'sonnet-4.5 - Cartesia (STT) - Anthropic (LLM) - Elevenlabs (TTS)',
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
    voiceProvider: 'cartesia',
    config: {
      pipeline_type: 'stt-llm-tts',
      stt_provider: 'cartesia',
      llm_provider: 'claude-sonnet-4',
      tts_provider: 'cartesia',
      temperature: 0.7,
    },
  },
  'OpenAI - speech-to-speech (Mini)': {
    label: 'OpenAI - speech-to-speech (Mini)',
    voiceProvider: 'openai',
    config: {
      pipeline_type: 'openai-realtime',
      temperature: 0.7,
      realtimeModel: 'gpt-realtime-mini-2025-10-06',
    },
  },
  'OpenAI - speech-to-speech': {
    label: 'OpenAI - speech-to-speech',
    voiceProvider: 'openai',
    config: {
      pipeline_type: 'openai-realtime',
      temperature: 0.7,
      realtimeModel: 'gpt-4o-realtime-preview-2025-06-03',
    },
  },
  'gpt4o - Cartesia (STT) - OpenAI (LLM) - Elevenlabs (TTS)': {
    label: 'gpt4o - Cartesia (STT) - OpenAI (LLM) - Elevenlabs (TTS)',
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
    voiceProvider: 'elevenlabs',
    config: {
      pipeline_type: 'stt-llm-tts',
      stt_provider: 'deepgram',
      llm_provider: 'openai-gpt4o-mini',
      tts_provider: 'elevenlabs',
      temperature: 0.7,
    },
  },
  'gemini-2.5-flash - Cartesia (STT) - Google (LLM) - Elevenlabs (TTS)': {
    label: 'gemini-2.5-flash - Cartesia (STT) - Google (LLM) - Elevenlabs (TTS)',
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

export const POPULAR_OPTION_MIGRATIONS: Record<string, keyof typeof POPULAR_OPTIONS> = {
  'gpt4o-mini - Cartesia (STT) - OpenAI (LLM) - Elevenlabs (TTS)':
    'gemini-2.5-flash - Cartesia (STT) - Google (LLM) - Elevenlabs (TTS)',
  'sonnet-3.5 - Cartesia (STT) - Anthropic (LLM) - Elevenlabs (TTS)':
    'sonnet-3.7 - Cartesia (STT) - Anthropic (LLM) - Elevenlabs (TTS)',
  'sonnet-3.5 - Cartesia (STT) - Anthropic (LLM) - Cartesia (TTS)':
    'sonnet-3.7 - Cartesia (STT) - Anthropic (LLM) - Cartesia (TTS)',
} as const;

export const VOICE_POPULAR_OPTIONS = Object.keys(POPULAR_OPTIONS) as (keyof typeof POPULAR_OPTIONS)[];

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
  if (config.realtimeModel) pipeline.realtime_model = config.realtimeModel;
  if (typeof config.temperature === 'number') pipeline.temperature = config.temperature;
  return pipeline;
};

export const voiceConfigToSnapshot = (
  config: SessionVoiceConfig | null | undefined,
): VoiceSnapshot | null => {
  if (!config) return null;
  const pipeline = pipelineFromVoiceConfig(config);
  const llmProvider =
    config.llm_provider ?? (config.pipeline_type === 'openai-realtime' ? 'openai-gpt4o' : undefined);
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
    return {
      voicePipeline: {
        pipeline_type: 'openai-realtime',
        voice_name: voiceName,
        realtime_model: DEFAULT_REALTIME_MODEL,
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
  if (preset.config.realtimeModel) pipeline.realtime_model = preset.config.realtimeModel;

  const resolvedVoiceName = voiceName ?? defaultVoiceForProvider(preset.voiceProvider);
  pipeline.voice_name = resolvedVoiceName;

  const temperature = requestedTemperature ?? preset.config.temperature ?? 0.7;
  pipeline.temperature = temperature;

  const llmProvider =
    preset.config.llm_provider ??
    (preset.config.pipeline_type === 'openai-realtime' ? 'openai-gpt4o' : undefined);

  return {
    voicePipeline: pipeline,
    llmProvider,
    temperature,
  };
};
