'use client';

import { useAuth } from '@clerk/nextjs';
import {
  memo,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type FormEvent,
} from 'react';
import { Maximize2, Mic, Square, Trash2, X } from 'lucide-react';
import {
  useCompanion,
  useCompanions,
  useUpdateCompanion,
  useUpdateCompanionMeta,
} from '@/hooks/useCompanions';
import { useBuilderRelationship } from '@/hooks/useBuilderRelationship';
import { useSelectedCompanion } from '@/components/providers';
import { useWebSocketSession, type LLMProvider } from '@/hooks/useWebSocketSession';
import { useRelationshipChat, type ChatMessage } from '@/hooks/useRelationshipChat';
import { RelationshipSelector } from '@/components/dashboard/relationship-selector';
import FormSection from '@/components/ui/form-section';
import VerticalTabs from '@/components/ui/vertical-tabs';
import { Textarea } from '@/components/ui/textarea';
import Dropdown from '@/components/ui/dropdown';
import CustomSwitch from '@/components/ui/switch';
import { ChatThread } from '@/components/ui/chat-thread';
import { ChatInput } from '@/components/ui/chat-input';
import { MessageBubble } from '@/components/ui/message-bubble';
import { VoiceOrb } from '@/components/voice/voice-orb';
import {
  apiClient,
  getTwilioCallStatus,
  type CompanionConfig,
  type DialogmachineElevenlabsSettingsResponse,
  type DialogmachineLlmSettingsResponse,
  type DialogmachineElevenlabsVoice,
  type TwilioCallTranscriptMessage,
} from '@/lib/api';

const FAST_BRAIN_VOICE_CONFIG = {
  pipeline_type: 'stt-llm-tts' as const,
  stt_provider: 'cartesia' as const,
  llm_provider: 'fast-brain' as const,
  tts_provider: 'elevenlabs' as const,
  voice_name: 'Can',
  temperature: 0.4,
};

const TERMINAL_CALL_STATUSES = new Set(['completed', 'busy', 'no-answer', 'canceled', 'failed']);
const BACKGROUND_NOISE_OPTIONS = [
  {
    value: 'restaurant_chatter',
    label: 'Restaurant chatter',
    description: 'Subtle dining-room murmur',
  },
  {
    value: 'city',
    label: 'City',
    description: 'City street ambience',
  },
  {
    value: 'office_hum',
    label: 'Office hum',
    description: 'Quiet room + HVAC tone',
  },
] as const;
const DEFAULT_BACKGROUND_NOISE_TYPE = BACKGROUND_NOISE_OPTIONS[0].value;
const DEFAULT_BACKGROUND_NOISE_VOLUME = 0.12;
type DialogmachineToolKey = 'end_call' | 'task_delegation';
type ElevenlabsModelOption = { id: string; label: string };
type DialogmachineLlmOption = { id: LLMProvider; label: string; description?: string };
const DEFAULT_DIALOGMACHINE_LLM_PROVIDER: LLMProvider = 'fast-brain';
const DEFAULT_DIALOGMACHINE_LLM_OPTIONS: DialogmachineLlmOption[] = [
  {
    id: 'fast-brain',
    label: 'Gemini 2.5 Flash (Default)',
    description: 'OpenRouter: google/gemini-2.5-flash',
  },
  {
    id: 'openai-gpt4o',
    label: 'OpenAI GPT-4o',
    description: 'OpenAI: gpt-4o',
  },
  {
    id: 'openai-gpt4o-mini',
    label: 'OpenAI GPT-4o mini',
    description: 'OpenAI: gpt-4o-mini',
  },
  {
    id: 'openai-gpt5.1',
    label: 'OpenAI GPT-5.1',
    description: 'OpenAI: gpt-5.1',
  },
  {
    id: 'claude-haiku-4.5',
    label: 'Claude Haiku 4.5',
    description: 'OpenRouter: anthropic/claude-haiku-4.5',
  },
  {
    id: 'claude-sonnet-4',
    label: 'Claude Sonnet 4',
    description: 'OpenRouter: anthropic/claude-sonnet-4',
  },
  {
    id: 'claude-sonnet-4.5',
    label: 'Claude Sonnet 4.5',
    description: 'OpenRouter: anthropic/claude-sonnet-4.5',
  },
  {
    id: 'claude-sonnet-4.6',
    label: 'Claude Sonnet 4.6',
    description: 'OpenRouter: anthropic/claude-sonnet-4.6',
  },
  {
    id: 'claude-opus-4',
    label: 'Claude Opus 4',
    description: 'OpenRouter: anthropic/claude-opus-4',
  },
  {
    id: 'claude-opus-4.5',
    label: 'Claude Opus 4.5',
    description: 'OpenRouter: anthropic/claude-opus-4.5',
  },
  {
    id: 'claude-opus-4.6',
    label: 'Claude Opus 4.6',
    description: 'OpenRouter: anthropic/claude-opus-4.6',
  },
  {
    id: 'gemini-2.5-flash',
    label: 'Gemini 2.5 Flash',
    description: 'OpenRouter: google/gemini-2.5-flash',
  },
  {
    id: 'gemini-3.1-flash-lite-preview',
    label: 'Gemini 3.1 Flash Lite Preview',
    description: 'OpenRouter: google/gemini-3.1-flash-lite-preview',
  },
];

function isDialogmachineLlmProvider(value: string): value is LLMProvider {
  return DEFAULT_DIALOGMACHINE_LLM_OPTIONS.some(option => option.id === value);
}
const TOOL_OPTIONS: Array<{
  value: DialogmachineToolKey;
  label: string;
  description: string;
}> = [
  {
    value: 'end_call',
    label: 'End Call',
    description: 'Allows the agent to politely close Twilio calls when finished.',
  },
  {
    value: 'task_delegation',
    label: 'Task Delegation',
    description: 'Allows Fast Brain to delegate work to Slow Brain/OpenClaw.',
  },
];
const TOOL_PRESETS = [
  {
    value: 'phone_basic',
    label: 'Phone Basic',
    description: 'End call enabled, delegation disabled.',
    selected: ['end_call'] as DialogmachineToolKey[],
  },
  {
    value: 'agentic',
    label: 'Agentic',
    description: 'End call + task delegation enabled.',
    selected: ['end_call', 'task_delegation'] as DialogmachineToolKey[],
  },
  {
    value: 'none',
    label: 'None',
    description: 'No tool behaviors enabled.',
    selected: [] as DialogmachineToolKey[],
  },
] as const;
const CUSTOM_TOOL_PRESET = 'custom';
const DEFAULT_SELECTED_TOOLS = TOOL_PRESETS[0].selected;
const DEFAULT_ELEVENLABS_MODEL_ID = 'eleven_turbo_v2_5';
const DEFAULT_ELEVENLABS_STABILITY = 0.7;
const DEFAULT_ELEVENLABS_SIMILARITY = 0.8;
const DEFAULT_ELEVENLABS_STYLE = 0.5;
const DEFAULT_ELEVENLABS_SPEED = 1.0;
const DEFAULT_ELEVENLABS_SPEAKER_BOOST = true;
const DEFAULT_ELEVENLABS_LANGUAGE_OVERRIDE = false;
const DEFAULT_ELEVENLABS_LANGUAGE_CODE = 'en';
const DEFAULT_ELEVENLABS_MODELS = [
  { id: 'eleven_flash_v2_5', label: 'Eleven Flash v2.5' },
  { id: 'eleven_turbo_v2_5', label: 'Eleven Turbo v2.5' },
  { id: 'eleven_multilingual_v2', label: 'Eleven Multilingual v2' },
] as const;
const ELEVENLABS_SANDO_VOICE_ID = 'orz8Yrvbzt78sfmUK5hU';
const ELEVENLABS_VOICE_PRESETS: Record<
  string,
  {
    model_id: string;
    speed: number;
    stability: number;
    similarity: number;
    style: number;
    speaker_boost: boolean;
    language_override: boolean;
    language_code: string;
  }
> = {
  [ELEVENLABS_SANDO_VOICE_ID]: {
    model_id: 'eleven_multilingual_v2',
    speed: 1.07,
    stability: 0.75,
    similarity: 1.0,
    style: 0.32,
    speaker_boost: true,
    language_override: true,
    language_code: 'en',
  },
};
const ELEVENLABS_LANGUAGE_OPTIONS = [
  { value: 'en', label: 'English' },
  { value: 'tr', label: 'Turkish' },
  { value: 'es', label: 'Spanish' },
  { value: 'fr', label: 'French' },
  { value: 'de', label: 'German' },
] as const;
const ELEVENLABS_CLONE_TRANSCRIPT = `# Voice Transcript for Elevenlabs

Hello. I'm recording a clean sample of my voice today, speaking English with my natural accent. I'll speak clearly, but I'll keep my normal rhythm, so it still sounds like me.

This morning I opened the window, felt the cool air, and thought, "What a wonderful view." The wind moved the trees, the streets were quiet, and I could hear a distant ferry horn. I made tea, sliced warm bread, and ate a fresh bagel. Then I checked my messages, wrote a short note, and planned the week.

Here are a few details with numbers, letters, and punctuation. My order number is T-R dash two-zero-four-seven dash B. The reference code is A-seven-F dash one-nine-Q. The price was twelve dollars and thirty cents, plus eight percent tax. The time was 7:45 p.m., and the total was 1,024 units.

Now I'll read some dates and measurements: Monday, Tuesday, Wednesday, Thursday, Friday. January 3rd, 2024; July 19th, 2025; and February 25th, 2026. The temperature is 21.5 degrees, the distance is three kilometers, and the battery is at 62 percent.

I want good coverage of tricky sounds: thin, then; thought, though; three, these; weather, whether. Wine and vine. Very and wary. Ship and sheep. Cat, cut, and cot. I will say them naturally, without forcing anything.

Imagine a short conversation: "Hi, could you help me with this?" - "Yes, of course. What's the problem?" - "The screen is flickering, the charger is loose, and the button won't work." - "No worries. We can fix it." I can sound curious, worried, or relaxed. I can pause, breathe, and continue.

If you ask me, "Why are you doing this?" my answer is simple: I want my voice to sound like me: friendly, steady, and honest. I can speak softly, I can speak loudly, and I can speak with excitement: Yes, that's it! Please wait a moment. Let's try again. Welcome back.

I like traveling: Madrid, Vienna, Istanbul, Tokyo, and New York. I enjoy coffee, music, and long walks by the Embarcadero. Sometimes I read science articles, sometimes I watch a film, and sometimes I simply rest.

One last line for variety: "Sphinx of black quartz, judge my vow." Thank you for listening, and thank you for your patience.`;

const SETTINGS_TABS = [
  { id: 'workspace', label: 'Workspace' },
  { id: 'relationship', label: 'Relationship' },
  { id: 'hot-context', label: 'Hot Context' },
  { id: 'voice-clone', label: 'Voice Clone' },
  { id: 'twilio', label: 'Twilio', disabled: true },
] as const;
const RECORDING_WAVE_BAR_COUNT = 36;
const EMPTY_RECORDING_WAVE = Array.from({ length: RECORDING_WAVE_BAR_COUNT }, () => 0.08);
const CLONE_RECORDING_TARGET_SECONDS = 120;

type CloneRecording = {
  id: string;
  file: File;
  url: string;
  durationSeconds: number;
};

type CloneWaveformProps = {
  data: number[];
  height?: number;
};

function CloneWaveform({ data, height = 56 }: CloneWaveformProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);

  const renderWaveform = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const rect = canvas.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    ctx.clearRect(0, 0, rect.width, rect.height);

    const barWidth = 4;
    const barGap = 2;
    const barRadius = 2;
    const baseBarHeight = 4;
    const fadeWidth = 24;
    const barCount = Math.max(1, Math.floor(rect.width / (barWidth + barGap)));
    const centerY = rect.height / 2;
    const barColor = 'rgba(255,255,255,0.95)';

    for (let i = 0; i < barCount; i += 1) {
      const dataIndex = Math.floor((i / barCount) * data.length);
      const value = data[dataIndex] ?? 0;
      const barHeight = Math.max(baseBarHeight, value * rect.height * 0.78);
      const x = i * (barWidth + barGap);
      const y = centerY - barHeight / 2;
      ctx.fillStyle = barColor;
      ctx.globalAlpha = 0.28 + value * 0.72;
      ctx.beginPath();
      ctx.roundRect(x, y, barWidth, barHeight, barRadius);
      ctx.fill();
    }

    if (fadeWidth > 0 && rect.width > 0) {
      const gradient = ctx.createLinearGradient(0, 0, rect.width, 0);
      const fadePercent = Math.min(0.2, fadeWidth / rect.width);
      gradient.addColorStop(0, 'rgba(255,255,255,1)');
      gradient.addColorStop(fadePercent, 'rgba(255,255,255,0)');
      gradient.addColorStop(1 - fadePercent, 'rgba(255,255,255,0)');
      gradient.addColorStop(1, 'rgba(255,255,255,1)');
      ctx.globalCompositeOperation = 'destination-out';
      ctx.fillStyle = gradient;
      ctx.fillRect(0, 0, rect.width, rect.height);
      ctx.globalCompositeOperation = 'source-over';
    }

    ctx.globalAlpha = 1;
  }, [data]);

  useEffect(() => {
    const canvas = canvasRef.current;
    const container = containerRef.current;
    if (!canvas || !container) return;

    const resize = () => {
      const rect = container.getBoundingClientRect();
      const dpr = window.devicePixelRatio || 1;
      canvas.width = rect.width * dpr;
      canvas.height = rect.height * dpr;
      canvas.style.width = `${rect.width}px`;
      canvas.style.height = `${rect.height}px`;
      const ctx = canvas.getContext('2d');
      if (ctx) {
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      }
      renderWaveform();
    };

    const observer = new ResizeObserver(resize);
    observer.observe(container);
    resize();
    return () => observer.disconnect();
  }, [renderWaveform]);

  useEffect(() => {
    renderWaveform();
  }, [renderWaveform]);

  return (
    <div
      ref={containerRef}
      className="h-14 w-full rounded bg-black"
      style={{ height: `${height}px` }}
    >
      <canvas ref={canvasRef} className="h-full w-full block" />
    </div>
  );
}

type CloneBudgetDonutProps = {
  progress: number;
};

function CloneBudgetDonut({ progress }: CloneBudgetDonutProps) {
  const size = 24;
  const stroke = 2.5;
  const radius = (size - stroke) / 2;
  const circumference = 2 * Math.PI * radius;
  const clamped = Math.min(1, Math.max(0, progress));
  const dashOffset = circumference * (1 - clamped);

  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} className="shrink-0">
      <circle
        cx={size / 2}
        cy={size / 2}
        r={radius}
        fill="none"
        stroke="rgba(255,255,255,0.2)"
        strokeWidth={stroke}
      />
      <circle
        cx={size / 2}
        cy={size / 2}
        r={radius}
        fill="none"
        stroke="rgba(255,255,255,0.95)"
        strokeWidth={stroke}
        strokeLinecap="round"
        strokeDasharray={circumference}
        strokeDashoffset={dashOffset}
        transform={`rotate(-90 ${size / 2} ${size / 2})`}
      />
    </svg>
  );
}

function formatSeconds(totalSeconds: number): string {
  const safe = Math.max(0, Math.round(totalSeconds));
  const minutes = Math.floor(safe / 60);
  const seconds = safe % 60;
  return `${minutes}:${String(seconds).padStart(2, '0')}`;
}

function buildSliderStyle(value: number, min: number, max: number): CSSProperties {
  const safeMax = max > min ? max : min + 1;
  const clamped = Math.min(safeMax, Math.max(min, value));
  const fill = ((clamped - min) / (safeMax - min)) * 100;
  return {
    ['--range-fill' as string]: `${fill}%`,
  };
}

function formatRelationshipUserLabel(userId: string): string {
  if (userId.startsWith('builder-')) {
    const suffix = userId.slice(8);
    return suffix.length > 14 ? `...${suffix.slice(-14)}` : suffix;
  }
  return userId.length > 22 ? `${userId.slice(0, 22)}...` : userId;
}

type TextSimulationPanelProps = {
  messages: ChatMessage[];
  isStreaming: boolean;
  streamingContent: string;
  isConnected: boolean;
  isConnecting: boolean;
  error: Error | null;
  onSendMessage: (content: string) => Promise<void>;
};

const TextSimulationPanel = memo(function TextSimulationPanel({
  messages,
  isStreaming,
  streamingContent,
  isConnected,
  isConnecting,
  error,
  onSendMessage,
}: TextSimulationPanelProps) {
  const [inputValue, setInputValue] = useState('');

  const handleSubmit = useCallback(
    async (event: FormEvent<Element>) => {
      event.preventDefault();
      const content = inputValue.trim();
      if (!content || isStreaming || !isConnected) return;

      setInputValue('');
      await onSendMessage(content);
    },
    [inputValue, isStreaming, isConnected, onSendMessage]
  );

  return (
    <div className="relative h-full flex flex-col bg-black">
      <div className="flex-1 min-h-0 px-4">
        <div className="mx-auto w-full max-w-2xl h-full">
          <ChatThread autoScroll className="space-y-6 h-full">
            {messages.length === 0 && !isStreaming ? (
              <div className="h-full flex items-center justify-center">
                <p className="text-sm text-white/45">
                  Start a text simulation in the active relationship workspace.
                </p>
              </div>
            ) : (
              <>
                {messages.map((message: ChatMessage, index: number) => (
                  <MessageBubble
                    key={message.id || `${message.role}-${message.created_at || 'no-ts'}-${index}`}
                    role={message.role}
                    timestamp={message.created_at}
                  >
                    {message.content}
                  </MessageBubble>
                ))}
                {(isStreaming || streamingContent) && streamingContent && (
                  <MessageBubble role="assistant">{streamingContent}</MessageBubble>
                )}
              </>
            )}
          </ChatThread>
        </div>
      </div>

      <div className="border-t border-white/10 px-4 py-4 bg-black/60">
        <div className="mx-auto w-full max-w-2xl">
          <ChatInput
            value={inputValue}
            onChange={setInputValue}
            onSubmit={event => {
              void handleSubmit(event);
            }}
            placeholder={isConnecting ? 'Connecting...' : 'Send a text message...'}
            disabled={!isConnected}
            sending={isStreaming}
            inputWrapperClassName="rounded-full px-4 pt-4 pb-3 bg-[#3C3C3C]"
          />
        </div>
      </div>

      {isConnecting && (
        <div className="absolute inset-0 bg-black/45 flex items-center justify-center">
          <div className="text-sm text-white/80">Connecting text session...</div>
        </div>
      )}

      {error && (
        <div className="absolute bottom-24 left-4 right-4 bg-red-500/20 border border-red-500/30 text-red-300 text-sm px-3 py-2">
          {error.message}
        </div>
      )}
    </div>
  );
});

export default function DialogmachineTesting() {
  const { getToken } = useAuth();
  const isAuthDisabled = process.env.NEXT_PUBLIC_DISABLE_AUTH === 'true';
  const { selectedCompanionId, setSelectedCompanionId } = useSelectedCompanion();
  const { data: companions = [] } = useCompanions();
  const { data: companionConfig, refetchCompanion } = useCompanion(selectedCompanionId);
  const { mutateAsync: updateCompanion } = useUpdateCompanion();
  const { mutateAsync: updateCompanionMeta } = useUpdateCompanionMeta();

  const {
    currentUserId,
    currentRelationship,
    testUsers,
    isLoading: relationshipLoading,
    switchUser,
    createNewUser,
    resetRelationship,
  } = useBuilderRelationship({
    companionId: selectedCompanionId,
    resetBehavior: 'clear_messages_only',
  });

  const {
    isConnected: voiceConnected,
    isConnecting: voiceConnecting,
    isPaused,
    userAmplitude,
    companionAmplitude,
    isCompanionSpeaking,
    startSession,
    stopSession,
    pauseSession,
    resumeSession,
    setConfig,
  } = useWebSocketSession();

  const {
    isConnected: textConnected,
    isConnecting: textConnecting,
    isStreaming: textStreaming,
    messages: textMessages,
    streamingContent: textStreamingContent,
    error: textError,
    connect: connectTextChat,
    disconnect: disconnectTextChat,
    sendMessage: sendTextMessage,
    clearMessages: clearTextMessages,
  } = useRelationshipChat({
    companionId: selectedCompanionId,
    userId: currentUserId,
  });

  const [basePrompt, setBasePrompt] = useState('');
  const [promptOverride, setPromptOverride] = useState('');
  const [relationshipGuardrails, setRelationshipGuardrails] = useState('');
  const [hotContext, setHotContext] = useState('');
  const [companionName, setCompanionName] = useState('');
  const [savedBasePrompt, setSavedBasePrompt] = useState('');
  const [savedPromptOverride, setSavedPromptOverride] = useState('');
  const [savedRelationshipGuardrails, setSavedRelationshipGuardrails] = useState('');
  const [savedHotContext, setSavedHotContext] = useState('');
  const [savedCompanionName, setSavedCompanionName] = useState('');
  const [backgroundNoiseEnabled, setBackgroundNoiseEnabled] = useState(false);
  const [backgroundNoiseType, setBackgroundNoiseType] = useState<string>(DEFAULT_BACKGROUND_NOISE_TYPE);
  const [backgroundNoiseVolume, setBackgroundNoiseVolume] = useState(DEFAULT_BACKGROUND_NOISE_VOLUME);
  const [savedBackgroundNoiseEnabled, setSavedBackgroundNoiseEnabled] = useState(false);
  const [savedBackgroundNoiseType, setSavedBackgroundNoiseType] = useState<string>(
    DEFAULT_BACKGROUND_NOISE_TYPE
  );
  const [savedBackgroundNoiseVolume, setSavedBackgroundNoiseVolume] = useState(
    DEFAULT_BACKGROUND_NOISE_VOLUME
  );
  const [selectedTools, setSelectedTools] = useState<DialogmachineToolKey[]>([
    ...DEFAULT_SELECTED_TOOLS,
  ]);
  const [savedSelectedTools, setSavedSelectedTools] = useState<DialogmachineToolKey[]>([
    ...DEFAULT_SELECTED_TOOLS,
  ]);
  const [dialogmachineLlmOptions, setDialogmachineLlmOptions] = useState<DialogmachineLlmOption[]>(
    [...DEFAULT_DIALOGMACHINE_LLM_OPTIONS]
  );
  const [dialogmachineLlmProvider, setDialogmachineLlmProvider] = useState<LLMProvider>(
    DEFAULT_DIALOGMACHINE_LLM_PROVIDER
  );
  const [savedDialogmachineLlmProvider, setSavedDialogmachineLlmProvider] = useState<LLMProvider>(
    DEFAULT_DIALOGMACHINE_LLM_PROVIDER
  );
  const [elevenlabsVoices, setElevenlabsVoices] = useState<DialogmachineElevenlabsVoice[]>([]);
  const [elevenlabsVoicesLoading, setElevenlabsVoicesLoading] = useState(false);
  const [elevenlabsVoicesError, setElevenlabsVoicesError] = useState<string | null>(null);
  const [elevenlabsModels, setElevenlabsModels] = useState<ElevenlabsModelOption[]>([
    ...DEFAULT_ELEVENLABS_MODELS,
  ]);
  const [selectedElevenlabsVoiceId, setSelectedElevenlabsVoiceId] = useState('');
  const [selectedElevenlabsVoiceName, setSelectedElevenlabsVoiceName] = useState('');
  const [elevenlabsModelId, setElevenlabsModelId] = useState(DEFAULT_ELEVENLABS_MODEL_ID);
  const [elevenlabsStability, setElevenlabsStability] = useState(DEFAULT_ELEVENLABS_STABILITY);
  const [elevenlabsSimilarity, setElevenlabsSimilarity] = useState(DEFAULT_ELEVENLABS_SIMILARITY);
  const [elevenlabsStyle, setElevenlabsStyle] = useState(DEFAULT_ELEVENLABS_STYLE);
  const [elevenlabsSpeed, setElevenlabsSpeed] = useState(DEFAULT_ELEVENLABS_SPEED);
  const [elevenlabsSpeakerBoost, setElevenlabsSpeakerBoost] = useState(
    DEFAULT_ELEVENLABS_SPEAKER_BOOST
  );
  const [elevenlabsLanguageOverride, setElevenlabsLanguageOverride] = useState(
    DEFAULT_ELEVENLABS_LANGUAGE_OVERRIDE
  );
  const [elevenlabsLanguageCode, setElevenlabsLanguageCode] = useState(
    DEFAULT_ELEVENLABS_LANGUAGE_CODE
  );
  const [savedElevenlabsVoiceId, setSavedElevenlabsVoiceId] = useState('');
  const [savedElevenlabsVoiceName, setSavedElevenlabsVoiceName] = useState('');
  const [savedElevenlabsModelId, setSavedElevenlabsModelId] = useState(DEFAULT_ELEVENLABS_MODEL_ID);
  const [savedElevenlabsStability, setSavedElevenlabsStability] = useState(
    DEFAULT_ELEVENLABS_STABILITY
  );
  const [savedElevenlabsSimilarity, setSavedElevenlabsSimilarity] = useState(
    DEFAULT_ELEVENLABS_SIMILARITY
  );
  const [savedElevenlabsStyle, setSavedElevenlabsStyle] = useState(DEFAULT_ELEVENLABS_STYLE);
  const [savedElevenlabsSpeed, setSavedElevenlabsSpeed] = useState(DEFAULT_ELEVENLABS_SPEED);
  const [savedElevenlabsSpeakerBoost, setSavedElevenlabsSpeakerBoost] = useState(
    DEFAULT_ELEVENLABS_SPEAKER_BOOST
  );
  const [savedElevenlabsLanguageOverride, setSavedElevenlabsLanguageOverride] = useState(
    DEFAULT_ELEVENLABS_LANGUAGE_OVERRIDE
  );
  const [savedElevenlabsLanguageCode, setSavedElevenlabsLanguageCode] = useState(
    DEFAULT_ELEVENLABS_LANGUAGE_CODE
  );
  const [cloneVoiceName, setCloneVoiceName] = useState('');
  const [isRecordingClone, setIsRecordingClone] = useState(false);
  const [isCloningVoice, setIsCloningVoice] = useState(false);
  const [cloneStatus, setCloneStatus] = useState<string | null>(null);
  const [hasInteractedCloneRecorder, setHasInteractedCloneRecorder] = useState(false);
  const [cloneRecordings, setCloneRecordings] = useState<CloneRecording[]>([]);
  const [recordingWave, setRecordingWave] = useState<number[]>(() => [...EMPTY_RECORDING_WAVE]);
  const [recordingSeconds, setRecordingSeconds] = useState(0);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const recordingStreamRef = useRef<MediaStream | null>(null);
  const recordedChunksRef = useRef<Blob[]>([]);
  const recordingAudioContextRef = useRef<AudioContext | null>(null);
  const recordingRafRef = useRef<number | null>(null);
  const recordingTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const recordingStartedAtRef = useRef<number | null>(null);
  const cloneRecordingsRef = useRef<CloneRecording[]>([]);
  const [nameSyncedCompanionId, setNameSyncedCompanionId] = useState<string | null>(null);
  const [isSaving, setIsSaving] = useState(false);
  const [workspaceError, setWorkspaceError] = useState<string | null>(null);
  const [saveMessage, setSaveMessage] = useState<string | null>(null);

  const [phoneNumber, setPhoneNumber] = useState('');
  const [ivrGoal, setIvrGoal] = useState('');
  const [dialError, setDialError] = useState<string | null>(null);
  const [isDialing, setIsDialing] = useState(false);
  const [callSid, setCallSid] = useState<string | null>(null);
  const [callStatus, setCallStatus] = useState<string>('idle');
  const [callDuration, setCallDuration] = useState<number | null>(null);

  const [transcriptCallSid, setTranscriptCallSid] = useState('');
  const [transcript, setTranscript] = useState<TwilioCallTranscriptMessage[]>([]);
  const [transcriptLoading, setTranscriptLoading] = useState(false);
  const [transcriptError, setTranscriptError] = useState<string | null>(null);
  const [activeSettingsTab, setActiveSettingsTab] = useState<string>('workspace');
  const [activeRunTab, setActiveRunTab] = useState<'text' | 'simulate' | 'dial'>('text');
  const [isRelationshipMutating, setIsRelationshipMutating] = useState(false);
  const [relationshipUserSearch, setRelationshipUserSearch] = useState('');
  const [isBasePromptFocused, setIsBasePromptFocused] = useState(false);
  const backdropPressStartedRef = useRef(false);

  const getAuthToken = useCallback(async () => {
    if (isAuthDisabled) return 'mock-dev-token';
    return getToken(
      process.env.NEXT_PUBLIC_CLERK_JWT_TEMPLATE
        ? { template: process.env.NEXT_PUBLIC_CLERK_JWT_TEMPLATE }
        : undefined
    );
  }, [getToken, isAuthDisabled]);

  useEffect(() => {
    if (!selectedCompanionId && companions.length > 0) {
      setSelectedCompanionId(companions[0].id);
    }
  }, [companions, selectedCompanionId, setSelectedCompanionId]);

  useEffect(() => {
    const fullPrompt = companionConfig?.system_prompt?.full_system_prompt || '';
    setBasePrompt(fullPrompt);
    setSavedBasePrompt(fullPrompt);
  }, [companionConfig]);

  const normalizePromptOverride = useCallback((value: string) => value.trim(), []);
  const normalizeGuardrails = useCallback((value: string) => value.trim(), []);
  const normalizeNoiseType = useCallback((value: string | null | undefined) => {
    const candidate = value || '';
    return BACKGROUND_NOISE_OPTIONS.some(option => option.value === candidate)
      ? candidate
      : DEFAULT_BACKGROUND_NOISE_TYPE;
  }, []);
  const normalizeSelectedTools = useCallback((values: string[] | null | undefined) => {
    const requested = new Set((values || []).map(value => value.trim()));
    return TOOL_OPTIONS.map(option => option.value).filter(value => requested.has(value));
  }, []);
  const normalizeDialogmachineLlmProvider = useCallback((value: string | null | undefined) => {
    if (!value) return DEFAULT_DIALOGMACHINE_LLM_PROVIDER;
    return isDialogmachineLlmProvider(value)
      ? value
      : DEFAULT_DIALOGMACHINE_LLM_PROVIDER;
  }, []);
  const applyDialogmachineLlmSettings = useCallback(
    (
      payload: DialogmachineLlmSettingsResponse,
      options: { markSaved?: boolean } = { markSaved: true }
    ) => {
      const availableModels = payload.available_models?.length
        ? payload.available_models
        : DEFAULT_DIALOGMACHINE_LLM_OPTIONS;
      const normalizedOptions = availableModels.reduce<DialogmachineLlmOption[]>(
        (acc, option) => {
          if (!isDialogmachineLlmProvider(option.id)) return acc;
          acc.push({
            id: option.id,
            label: option.label,
            ...(option.description ? { description: option.description } : {}),
          });
          return acc;
        },
        []
      );
      const nextOptions = normalizedOptions.length
        ? normalizedOptions
        : [...DEFAULT_DIALOGMACHINE_LLM_OPTIONS];
      const nextProvider = nextOptions.some(option => option.id === payload.provider)
        ? (payload.provider as LLMProvider)
        : normalizeDialogmachineLlmProvider(payload.provider);

      setDialogmachineLlmOptions(nextOptions);
      setDialogmachineLlmProvider(nextProvider);
      if (options.markSaved !== false) {
        setSavedDialogmachineLlmProvider(nextProvider);
      }
    },
    [normalizeDialogmachineLlmProvider]
  );
  const applyElevenlabsVoicePreset = useCallback((voiceId: string) => {
    const preset = ELEVENLABS_VOICE_PRESETS[voiceId];
    if (!preset) return;
    setElevenlabsModelId(preset.model_id);
    setElevenlabsSpeed(preset.speed);
    setElevenlabsStability(preset.stability);
    setElevenlabsSimilarity(preset.similarity);
    setElevenlabsStyle(preset.style);
    setElevenlabsSpeakerBoost(preset.speaker_boost);
    setElevenlabsLanguageOverride(preset.language_override);
    setElevenlabsLanguageCode(preset.language_code);
  }, []);
  const normalizeElevenlabsVoice = useCallback((voiceId: string, voiceName: string) => {
    if (!voiceId) return { voiceId: '', voiceName: voiceName.trim() };
    const match = elevenlabsVoices.find(item => item.voice_id === voiceId);
    return {
      voiceId,
      voiceName: match?.name || voiceName.trim() || '',
    };
  }, [elevenlabsVoices]);
  const applyElevenlabsSettings = useCallback(
    (
      payload: DialogmachineElevenlabsSettingsResponse,
      options: { markSaved?: boolean } = { markSaved: true }
    ) => {
      const normalized = normalizeElevenlabsVoice(payload.voice_id || '', payload.voice_name || '');
      const availableModels = payload.available_models?.length
        ? payload.available_models.map(model => ({ id: model.id, label: model.label }))
        : [...DEFAULT_ELEVENLABS_MODELS];
      setElevenlabsModels(availableModels);
      setSelectedElevenlabsVoiceId(normalized.voiceId);
      setSelectedElevenlabsVoiceName(normalized.voiceName);
      setElevenlabsModelId(payload.model_id || DEFAULT_ELEVENLABS_MODEL_ID);
      setElevenlabsStability(payload.stability ?? DEFAULT_ELEVENLABS_STABILITY);
      setElevenlabsSimilarity(payload.similarity_boost ?? DEFAULT_ELEVENLABS_SIMILARITY);
      setElevenlabsStyle(payload.style ?? DEFAULT_ELEVENLABS_STYLE);
      setElevenlabsSpeed(payload.speed ?? DEFAULT_ELEVENLABS_SPEED);
      setElevenlabsSpeakerBoost(payload.use_speaker_boost ?? DEFAULT_ELEVENLABS_SPEAKER_BOOST);
      setElevenlabsLanguageOverride(
        payload.language_override_enabled ?? DEFAULT_ELEVENLABS_LANGUAGE_OVERRIDE
      );
      setElevenlabsLanguageCode(payload.language_code || DEFAULT_ELEVENLABS_LANGUAGE_CODE);

      if (options.markSaved !== false) {
        setSavedElevenlabsVoiceId(normalized.voiceId);
        setSavedElevenlabsVoiceName(normalized.voiceName);
        setSavedElevenlabsModelId(payload.model_id || DEFAULT_ELEVENLABS_MODEL_ID);
        setSavedElevenlabsStability(payload.stability ?? DEFAULT_ELEVENLABS_STABILITY);
        setSavedElevenlabsSimilarity(payload.similarity_boost ?? DEFAULT_ELEVENLABS_SIMILARITY);
        setSavedElevenlabsStyle(payload.style ?? DEFAULT_ELEVENLABS_STYLE);
        setSavedElevenlabsSpeed(payload.speed ?? DEFAULT_ELEVENLABS_SPEED);
        setSavedElevenlabsSpeakerBoost(
          payload.use_speaker_boost ?? DEFAULT_ELEVENLABS_SPEAKER_BOOST
        );
        setSavedElevenlabsLanguageOverride(
          payload.language_override_enabled ?? DEFAULT_ELEVENLABS_LANGUAGE_OVERRIDE
        );
        setSavedElevenlabsLanguageCode(payload.language_code || DEFAULT_ELEVENLABS_LANGUAGE_CODE);
      }
    },
    [normalizeElevenlabsVoice]
  );
  const getToolPreset = useCallback((values: DialogmachineToolKey[]) => {
    const sorted = [...values].sort().join('|');
    for (const preset of TOOL_PRESETS) {
      if ([...preset.selected].sort().join('|') === sorted) {
        return preset.value;
      }
    }
    return CUSTOM_TOOL_PRESET;
  }, []);
  const taskDelegationEnabled = selectedTools.includes('task_delegation');
  const endCallEnabled = selectedTools.includes('end_call');
  const filteredRelationshipUsers = useMemo(() => {
    const query = relationshipUserSearch.trim().toLowerCase();
    const sorted = [...testUsers].sort((a, b) => {
      if (a.user_id === currentUserId) return -1;
      if (b.user_id === currentUserId) return 1;
      return (b.last_interaction_at || '').localeCompare(a.last_interaction_at || '');
    });
    if (!query) return sorted;
    return sorted.filter(user => {
      const candidate = `${user.user_id} ${user.profile_preview?.name || ''}`.toLowerCase();
      return candidate.includes(query);
    });
  }, [testUsers, currentUserId, relationshipUserSearch]);

  useEffect(() => {
    if (!selectedCompanionId || !currentUserId) return;
    setConfig({
      companionId: selectedCompanionId,
      clientExternalUserId: currentUserId,
      useV2: true,
      useDialogmachine: true,
      voiceConfig: {
        ...FAST_BRAIN_VOICE_CONFIG,
        llm_provider: dialogmachineLlmProvider,
        voice_name: selectedElevenlabsVoiceName || FAST_BRAIN_VOICE_CONFIG.voice_name,
        tts_voice_id: selectedElevenlabsVoiceId || undefined,
        elevenlabs_model_id: elevenlabsModelId,
        elevenlabs_stability: elevenlabsStability,
        elevenlabs_similarity_boost: elevenlabsSimilarity,
        elevenlabs_style: elevenlabsStyle,
        elevenlabs_speed: elevenlabsSpeed,
        elevenlabs_use_speaker_boost: elevenlabsSpeakerBoost,
        elevenlabs_language_code: elevenlabsLanguageOverride ? elevenlabsLanguageCode : null,
        background_noise_enabled: backgroundNoiseEnabled,
        background_noise_type: backgroundNoiseType,
        background_noise_volume: backgroundNoiseVolume,
        fast_brain_delegate_enabled: taskDelegationEnabled,
        fast_brain_end_call_enabled: endCallEnabled,
      },
    });
  }, [
    selectedCompanionId,
    currentUserId,
    setConfig,
    backgroundNoiseEnabled,
    backgroundNoiseType,
    backgroundNoiseVolume,
    dialogmachineLlmProvider,
    taskDelegationEnabled,
    endCallEnabled,
    selectedElevenlabsVoiceName,
    selectedElevenlabsVoiceId,
    elevenlabsModelId,
    elevenlabsStability,
    elevenlabsSimilarity,
    elevenlabsStyle,
    elevenlabsSpeed,
    elevenlabsSpeakerBoost,
    elevenlabsLanguageOverride,
    elevenlabsLanguageCode,
  ]);

  const loadElevenlabsVoices = useCallback(async () => {
    setElevenlabsVoicesError(null);
    setElevenlabsVoicesLoading(true);
    try {
      const token = await getAuthToken();
      const voices = await apiClient.listDialogmachineElevenlabsVoices(token);
      setElevenlabsVoices(voices);
    } catch (error) {
      setElevenlabsVoicesError(
        error instanceof Error ? error.message : 'Failed to load ElevenLabs voices'
      );
    } finally {
      setElevenlabsVoicesLoading(false);
    }
  }, [getAuthToken]);

  const loadDialogmachineState = useCallback(async () => {
    if (!selectedCompanionId || !currentUserId) return;
    setWorkspaceError(null);
    try {
      const token = await getAuthToken();
      const [hot, prompt, guardrails, noise, toolCalls, llmSettings, elevenlabsSettings] = await Promise.all([
        apiClient.getDialogmachineHotContext(selectedCompanionId, currentUserId, token),
        apiClient.getDialogmachinePromptOverride(selectedCompanionId, currentUserId, token),
        apiClient.getDialogmachineGuardrails(selectedCompanionId, currentUserId, token),
        apiClient.getDialogmachineBackgroundNoise(selectedCompanionId, currentUserId, token),
        apiClient.getDialogmachineToolCalls(selectedCompanionId, currentUserId, token),
        apiClient.getDialogmachineLlmSettings(selectedCompanionId, currentUserId, token),
        apiClient.getDialogmachineElevenlabsSettings(selectedCompanionId, currentUserId, token),
      ]);
      const nextHotContext = hot.content || '';
      const nextPromptOverride = prompt.prompt_override || '';
      const nextGuardrails = guardrails.guardrails || '';
      const nextNoiseType = normalizeNoiseType(noise.noise_type);
      const nextNoiseVolume =
        typeof noise.volume === 'number' ? noise.volume : DEFAULT_BACKGROUND_NOISE_VOLUME;
      setHotContext(nextHotContext);
      setPromptOverride(nextPromptOverride);
      setRelationshipGuardrails(nextGuardrails);
      setSavedHotContext(nextHotContext);
      setSavedPromptOverride(nextPromptOverride);
      setSavedRelationshipGuardrails(nextGuardrails);
      setBackgroundNoiseEnabled(Boolean(noise.enabled));
      setBackgroundNoiseType(nextNoiseType);
      setBackgroundNoiseVolume(nextNoiseVolume);
      setSavedBackgroundNoiseEnabled(Boolean(noise.enabled));
      setSavedBackgroundNoiseType(nextNoiseType);
      setSavedBackgroundNoiseVolume(nextNoiseVolume);
      const hasSelectedTools = Array.isArray(toolCalls.selected_tools);
      const nextTools = normalizeSelectedTools(
        hasSelectedTools
          ? toolCalls.selected_tools
          : toolCalls.enabled
            ? ['task_delegation']
            : DEFAULT_SELECTED_TOOLS
      );
      setSelectedTools(nextTools);
      setSavedSelectedTools(nextTools);
      applyDialogmachineLlmSettings(llmSettings);
      applyElevenlabsSettings(elevenlabsSettings);
    } catch (error) {
      setWorkspaceError(error instanceof Error ? error.message : 'Failed to load dialogmachine state');
    }
  }, [
    selectedCompanionId,
    currentUserId,
    getAuthToken,
    normalizeNoiseType,
    normalizeSelectedTools,
    applyDialogmachineLlmSettings,
    applyElevenlabsSettings,
  ]);

  useEffect(() => {
    loadDialogmachineState();
  }, [loadDialogmachineState]);

  useEffect(() => {
    void loadElevenlabsVoices();
  }, [loadElevenlabsVoices]);

  useEffect(() => {
    if (activeRunTab === 'text') {
      if (
        selectedCompanionId &&
        currentUserId &&
        !textConnected &&
        !textConnecting &&
        !isRelationshipMutating
      ) {
        void connectTextChat();
      }
      return;
    }
    if (textConnected || textConnecting) {
      disconnectTextChat();
    }
  }, [
    activeRunTab,
    selectedCompanionId,
    currentUserId,
    textConnected,
    textConnecting,
    isRelationshipMutating,
    connectTextChat,
    disconnectTextChat,
  ]);

  const runRelationshipAction = useCallback(
    async (action: () => Promise<unknown>) => {
      setIsRelationshipMutating(true);
      if (activeRunTab === 'text') {
        disconnectTextChat();
        clearTextMessages();
      }
      try {
        await action();
      } finally {
        setIsRelationshipMutating(false);
      }
    },
    [activeRunTab, disconnectTextChat, clearTextMessages]
  );

  const handleSwitchRelationshipUser = useCallback(
    async (userId: string) => {
      await runRelationshipAction(() => switchUser(userId));
    },
    [runRelationshipAction, switchUser]
  );

  const handleCreateRelationshipUser = useCallback(async () => {
    await runRelationshipAction(() => createNewUser());
  }, [runRelationshipAction, createNewUser]);

  const handleResetRelationship = useCallback(async () => {
    await runRelationshipAction(() => resetRelationship());
  }, [runRelationshipAction, resetRelationship]);

  const handleSendTextModeMessage = useCallback(async (content: string) => {
    if (!content.trim() || textStreaming) return;
    try {
      await sendTextMessage(content);
    } catch (error) {
      setWorkspaceError(error instanceof Error ? error.message : 'Failed to send text message');
    }
  }, [textStreaming, sendTextMessage]);

  useEffect(() => {
    if (!isBasePromptFocused) return;

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setIsBasePromptFocused(false);
      }
    };

    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    window.addEventListener('keydown', onKeyDown);

    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener('keydown', onKeyDown);
    };
  }, [isBasePromptFocused]);

  const hasUnsavedChanges = useMemo(() => {
    const promptChanged = basePrompt !== savedBasePrompt;
    const overrideChanged =
      normalizePromptOverride(promptOverride) !== normalizePromptOverride(savedPromptOverride);
    const guardrailsChanged =
      normalizeGuardrails(relationshipGuardrails) !== normalizeGuardrails(savedRelationshipGuardrails);
    const hotContextChanged = hotContext !== savedHotContext;
    const companionNameChanged = companionName.trim() !== savedCompanionName.trim();
    const backgroundNoiseChanged =
      backgroundNoiseEnabled !== savedBackgroundNoiseEnabled ||
      backgroundNoiseType !== savedBackgroundNoiseType ||
      Math.abs(backgroundNoiseVolume - savedBackgroundNoiseVolume) > 0.0001;
    const currentToolsSignature = [...selectedTools].sort().join('|');
    const savedToolsSignature = [...savedSelectedTools].sort().join('|');
    const toolCallsChanged = currentToolsSignature !== savedToolsSignature;
    const llmChanged = dialogmachineLlmProvider !== savedDialogmachineLlmProvider;
    const elevenlabsChanged =
      selectedElevenlabsVoiceId !== savedElevenlabsVoiceId ||
      selectedElevenlabsVoiceName !== savedElevenlabsVoiceName ||
      elevenlabsModelId !== savedElevenlabsModelId ||
      Math.abs(elevenlabsStability - savedElevenlabsStability) > 0.0001 ||
      Math.abs(elevenlabsSimilarity - savedElevenlabsSimilarity) > 0.0001 ||
      Math.abs(elevenlabsStyle - savedElevenlabsStyle) > 0.0001 ||
      Math.abs(elevenlabsSpeed - savedElevenlabsSpeed) > 0.0001 ||
      elevenlabsSpeakerBoost !== savedElevenlabsSpeakerBoost ||
      elevenlabsLanguageOverride !== savedElevenlabsLanguageOverride ||
      elevenlabsLanguageCode !== savedElevenlabsLanguageCode;
    return (
      promptChanged ||
      overrideChanged ||
      guardrailsChanged ||
      hotContextChanged ||
      companionNameChanged ||
      backgroundNoiseChanged ||
      toolCallsChanged ||
      llmChanged ||
      elevenlabsChanged
    );
  }, [
    basePrompt,
    savedBasePrompt,
    promptOverride,
    savedPromptOverride,
    relationshipGuardrails,
    savedRelationshipGuardrails,
    hotContext,
    savedHotContext,
    companionName,
    savedCompanionName,
    backgroundNoiseEnabled,
    savedBackgroundNoiseEnabled,
    backgroundNoiseType,
    savedBackgroundNoiseType,
    backgroundNoiseVolume,
    savedBackgroundNoiseVolume,
    selectedTools,
    savedSelectedTools,
    dialogmachineLlmProvider,
    savedDialogmachineLlmProvider,
    selectedElevenlabsVoiceId,
    savedElevenlabsVoiceId,
    selectedElevenlabsVoiceName,
    savedElevenlabsVoiceName,
    elevenlabsModelId,
    savedElevenlabsModelId,
    elevenlabsStability,
    savedElevenlabsStability,
    elevenlabsSimilarity,
    savedElevenlabsSimilarity,
    elevenlabsStyle,
    savedElevenlabsStyle,
    elevenlabsSpeed,
    savedElevenlabsSpeed,
    elevenlabsSpeakerBoost,
    savedElevenlabsSpeakerBoost,
    elevenlabsLanguageOverride,
    savedElevenlabsLanguageOverride,
    elevenlabsLanguageCode,
    savedElevenlabsLanguageCode,
    normalizePromptOverride,
    normalizeGuardrails,
  ]);

  const handleSaveAll = useCallback(async () => {
    if (!selectedCompanionId || !currentUserId || !companionConfig) return;

    setWorkspaceError(null);
    setSaveMessage(null);
    setIsSaving(true);

    const normalizedOverride = normalizePromptOverride(promptOverride);
    const savedNormalizedOverride = normalizePromptOverride(savedPromptOverride);
    const normalizedGuardrails = normalizeGuardrails(relationshipGuardrails);
    const savedNormalizedGuardrails = normalizeGuardrails(savedRelationshipGuardrails);
    const basePromptChanged = basePrompt !== savedBasePrompt;
    const promptOverrideChanged = normalizedOverride !== savedNormalizedOverride;
    const guardrailsChanged = normalizedGuardrails !== savedNormalizedGuardrails;
    const hotContextChanged = hotContext !== savedHotContext;
    const normalizedCompanionName = companionName.trim();
    const companionNameChanged = normalizedCompanionName !== savedCompanionName.trim();
    const backgroundNoiseChanged =
      backgroundNoiseEnabled !== savedBackgroundNoiseEnabled ||
      backgroundNoiseType !== savedBackgroundNoiseType ||
      Math.abs(backgroundNoiseVolume - savedBackgroundNoiseVolume) > 0.0001;
    const currentToolsSignature = [...selectedTools].sort().join('|');
    const savedToolsSignature = [...savedSelectedTools].sort().join('|');
    const toolCallsChanged = currentToolsSignature !== savedToolsSignature;
    const llmChanged = dialogmachineLlmProvider !== savedDialogmachineLlmProvider;

    try {
      const token = await getAuthToken();

      if (companionNameChanged) {
        if (!normalizedCompanionName) {
          throw new Error('Companion name cannot be empty');
        }
        await updateCompanionMeta({
          id: selectedCompanionId,
          meta: { name: normalizedCompanionName },
        });
        setCompanionName(normalizedCompanionName);
        setSavedCompanionName(normalizedCompanionName);
      }

      if (basePromptChanged) {
        const updated: CompanionConfig = {
          ...companionConfig,
          system_prompt: {
            ...companionConfig.system_prompt,
            full_system_prompt: basePrompt,
          },
        };
        await updateCompanion({ id: selectedCompanionId, config: updated });
        setSavedBasePrompt(basePrompt);
        await refetchCompanion();
      }

      if (promptOverrideChanged) {
        const payload = normalizedOverride ? normalizedOverride : null;
        const response = await apiClient.updateDialogmachinePromptOverride(
          selectedCompanionId,
          currentUserId,
          payload,
          token
        );
        const savedValue = response.prompt_override || '';
        setPromptOverride(savedValue);
        setSavedPromptOverride(savedValue);
      }

      if (guardrailsChanged) {
        const payload = normalizedGuardrails ? normalizedGuardrails : null;
        const response = await apiClient.updateDialogmachineGuardrails(
          selectedCompanionId,
          currentUserId,
          payload,
          token
        );
        const savedValue = response.guardrails || '';
        setRelationshipGuardrails(savedValue);
        setSavedRelationshipGuardrails(savedValue);
      }

      if (hotContextChanged) {
        const response = await apiClient.updateDialogmachineHotContext(
          selectedCompanionId,
          currentUserId,
          hotContext,
          token
        );
        const savedValue = response.content || '';
        setHotContext(savedValue);
        setSavedHotContext(savedValue);
      }

      if (backgroundNoiseChanged) {
        const response = await apiClient.updateDialogmachineBackgroundNoise(
          selectedCompanionId,
          currentUserId,
          {
            enabled: backgroundNoiseEnabled,
            noise_type: backgroundNoiseType,
            volume: backgroundNoiseVolume,
          },
          token
        );
        const savedNoiseType = normalizeNoiseType(response.noise_type);
        const savedVolume =
          typeof response.volume === 'number'
            ? response.volume
            : DEFAULT_BACKGROUND_NOISE_VOLUME;
        setBackgroundNoiseEnabled(Boolean(response.enabled));
        setBackgroundNoiseType(savedNoiseType);
        setBackgroundNoiseVolume(savedVolume);
        setSavedBackgroundNoiseEnabled(Boolean(response.enabled));
        setSavedBackgroundNoiseType(savedNoiseType);
        setSavedBackgroundNoiseVolume(savedVolume);
      }

      if (toolCallsChanged) {
        const response = await apiClient.updateDialogmachineToolCalls(
          selectedCompanionId,
          currentUserId,
          { selected_tools: selectedTools },
          token
        );
        const savedTools = normalizeSelectedTools(response.selected_tools);
        setSelectedTools(savedTools);
        setSavedSelectedTools(savedTools);
      }

      if (llmChanged) {
        const response = await apiClient.updateDialogmachineLlmSettings(
          selectedCompanionId,
          currentUserId,
          { provider: dialogmachineLlmProvider },
          token
        );
        applyDialogmachineLlmSettings(response);
      }

      const response = await apiClient.updateDialogmachineElevenlabsSettings(
        selectedCompanionId,
        currentUserId,
        {
          voice_id: selectedElevenlabsVoiceId || null,
          voice_name: selectedElevenlabsVoiceName || null,
          model_id: elevenlabsModelId,
          stability: elevenlabsStability,
          similarity_boost: elevenlabsSimilarity,
          style: elevenlabsStyle,
          speed: elevenlabsSpeed,
          use_speaker_boost: elevenlabsSpeakerBoost,
          language_override_enabled: elevenlabsLanguageOverride,
          language_code: elevenlabsLanguageCode,
        },
        token
      );
      applyElevenlabsSettings(response);

      setSaveMessage('Saved settings.');
    } catch (error) {
      setWorkspaceError(error instanceof Error ? error.message : 'Failed to save workspace');
    } finally {
      setIsSaving(false);
    }
  }, [
    selectedCompanionId,
    currentUserId,
    companionConfig,
    normalizePromptOverride,
    normalizeGuardrails,
    promptOverride,
    savedPromptOverride,
    relationshipGuardrails,
    savedRelationshipGuardrails,
    basePrompt,
    savedBasePrompt,
    hotContext,
    savedHotContext,
    companionName,
    savedCompanionName,
    getAuthToken,
    updateCompanion,
    updateCompanionMeta,
    refetchCompanion,
    backgroundNoiseEnabled,
    savedBackgroundNoiseEnabled,
    backgroundNoiseType,
    savedBackgroundNoiseType,
    backgroundNoiseVolume,
    savedBackgroundNoiseVolume,
    selectedTools,
    savedSelectedTools,
    dialogmachineLlmProvider,
    savedDialogmachineLlmProvider,
    selectedElevenlabsVoiceId,
    selectedElevenlabsVoiceName,
    elevenlabsModelId,
    elevenlabsStability,
    elevenlabsSimilarity,
    elevenlabsStyle,
    elevenlabsSpeed,
    elevenlabsSpeakerBoost,
    elevenlabsLanguageOverride,
    elevenlabsLanguageCode,
    normalizeSelectedTools,
    normalizeNoiseType,
    applyDialogmachineLlmSettings,
    applyElevenlabsSettings,
  ]);

  const handleDial = useCallback(async () => {
    if (!selectedCompanionId || !currentUserId) {
      setDialError('Select companion and test user first.');
      return;
    }
    if (!/^\+[1-9]\d{1,14}$/.test(phoneNumber.trim())) {
      setDialError('Use E.164 format (example: +14155551234).');
      return;
    }
    setDialError(null);
    setIsDialing(true);
    try {
      const token = await getAuthToken();
      const result = await apiClient.dialDialogmachine(
        selectedCompanionId,
        currentUserId,
        phoneNumber.trim(),
        ivrGoal.trim() || undefined,
        token
      );
      setCallSid(result.call_sid);
      setCallStatus(result.status);
      setCallDuration(null);
      setTranscriptCallSid(result.call_sid);
      setActiveRunTab('dial');
    } catch (error) {
      setDialError(error instanceof Error ? error.message : 'Failed to dial.');
    } finally {
      setIsDialing(false);
    }
  }, [selectedCompanionId, currentUserId, phoneNumber, ivrGoal, getAuthToken]);

  useEffect(() => {
    if (!callSid) return;
    let cancelled = false;
    let intervalId: ReturnType<typeof setInterval> | null = null;

    const poll = async () => {
      try {
        const status = await getTwilioCallStatus(callSid);
        if (cancelled) return;
        setCallStatus(status.status);
        setCallDuration(status.duration);
        if (TERMINAL_CALL_STATUSES.has(status.status) && intervalId) {
          clearInterval(intervalId);
          intervalId = null;
        }
      } catch {
        // Keep polling; transient status failures are expected occasionally.
      }
    };

    poll();
    intervalId = setInterval(poll, 2000);

    return () => {
      cancelled = true;
      if (intervalId) clearInterval(intervalId);
    };
  }, [callSid]);

  const loadTranscript = useCallback(async () => {
    if (!selectedCompanionId || !currentUserId || !transcriptCallSid.trim()) {
      setTranscriptError('Enter a valid Call SID.');
      return;
    }
    setTranscriptError(null);
    setTranscriptLoading(true);
    try {
      const token = await getAuthToken();
      const data = await apiClient.getDialogmachineCallTranscript(
        selectedCompanionId,
        currentUserId,
        transcriptCallSid.trim(),
        800,
        token
      );
      setTranscript(data);
    } catch (error) {
      setTranscriptError(error instanceof Error ? error.message : 'Failed to load transcript');
    } finally {
      setTranscriptLoading(false);
    }
  }, [selectedCompanionId, currentUserId, transcriptCallSid, getAuthToken]);

  useEffect(() => {
    if (!transcriptCallSid || !TERMINAL_CALL_STATUSES.has(callStatus)) return;
    loadTranscript();
  }, [callStatus, transcriptCallSid, loadTranscript]);

  const stopRecordingVisuals = useCallback(() => {
    if (recordingRafRef.current) {
      cancelAnimationFrame(recordingRafRef.current);
      recordingRafRef.current = null;
    }
    if (recordingTimerRef.current) {
      clearInterval(recordingTimerRef.current);
      recordingTimerRef.current = null;
    }
    recordingStartedAtRef.current = null;
    setRecordingSeconds(0);
    const audioContext = recordingAudioContextRef.current;
    recordingAudioContextRef.current = null;
    if (audioContext) {
      void audioContext.close().catch(() => {});
    }
  }, []);

  const startRecordingVisuals = useCallback((stream: MediaStream) => {
    stopRecordingVisuals();
    setRecordingWave(Array.from({ length: 60 }, () => 0.06));
    recordingStartedAtRef.current = Date.now();
    setRecordingSeconds(0);
    recordingTimerRef.current = setInterval(() => {
      const startedAt = recordingStartedAtRef.current;
      if (!startedAt) return;
      setRecordingSeconds(Math.floor((Date.now() - startedAt) / 1000));
    }, 250);

    const AudioContextCtor =
      window.AudioContext ||
      (window as Window & { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (!AudioContextCtor) return;

    const audioContext = new AudioContextCtor();
    recordingAudioContextRef.current = audioContext;
    const source = audioContext.createMediaStreamSource(stream);
    const analyser = audioContext.createAnalyser();
    analyser.fftSize = 256;
    analyser.smoothingTimeConstant = 0.8;
    source.connect(analyser);

    const data = new Uint8Array(analyser.frequencyBinCount);
    let lastWaveUpdate = 0;
    const render = (time: number) => {
      if (!recordingAudioContextRef.current) return;
      analyser.getByteFrequencyData(data);
      if (time - lastWaveUpdate >= 48) {
        const startFreq = Math.floor(data.length * 0.05);
        const endFreq = Math.floor(data.length * 0.4);
        const relevantData = data.slice(startFreq, endFreq);
        const total = relevantData.reduce((sum, value) => sum + value, 0);
        const avg = relevantData.length ? total / relevantData.length : 0;
        const normalized = Math.max(0.05, Math.min(1, avg / 255));
        setRecordingWave(prev => {
          const next = [...prev, normalized];
          return next.length > 120 ? next.slice(next.length - 120) : next;
        });
        lastWaveUpdate = time;
      }
      recordingRafRef.current = requestAnimationFrame(render);
    };
    recordingRafRef.current = requestAnimationFrame(render);
  }, [stopRecordingVisuals]);

  useEffect(() => {
    cloneRecordingsRef.current = cloneRecordings;
  }, [cloneRecordings]);

  useEffect(() => {
    return () => {
      stopRecordingVisuals();
      cloneRecordingsRef.current.forEach(recording => {
        URL.revokeObjectURL(recording.url);
      });
      recordingStreamRef.current?.getTracks().forEach(track => track.stop());
      recordingStreamRef.current = null;
    };
  }, [stopRecordingVisuals]);

  const startCloneRecording = useCallback(async () => {
    setHasInteractedCloneRecorder(true);
    setCloneStatus(null);
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === 'undefined') {
      setCloneStatus('Recording is not supported in this browser.');
      return;
    }

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      recordingStreamRef.current = stream;
      startRecordingVisuals(stream);
      const mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
        ? 'audio/webm;codecs=opus'
        : 'audio/webm';
      const recorder = new MediaRecorder(stream, { mimeType });
      recordedChunksRef.current = [];
      recorder.ondataavailable = event => {
        if (event.data.size > 0) {
          recordedChunksRef.current.push(event.data);
        }
      };
      recorder.onstop = () => {
        const startedAt = recordingStartedAtRef.current;
        const durationSeconds = startedAt
          ? Math.max(1, Math.round((Date.now() - startedAt) / 1000))
          : Math.max(1, recordingSeconds);
        stopRecordingVisuals();
        const blob = new Blob(recordedChunksRef.current, { type: recorder.mimeType || 'audio/webm' });
        if (!blob.size) {
          setCloneStatus('No audio captured. Please record again.');
          setIsRecordingClone(false);
          stream.getTracks().forEach(track => track.stop());
          recordingStreamRef.current = null;
          mediaRecorderRef.current = null;
          return;
        }
        const file = new File([blob], 'elevenlabs-clone-sample.webm', {
          type: blob.type || 'audio/webm',
        });
        const url = URL.createObjectURL(blob);
        setCloneRecordings(prev => [
          ...prev,
          {
            id: crypto.randomUUID(),
            file,
            url,
            durationSeconds,
          },
        ]);
        setCloneStatus('Recording saved.');
        setIsRecordingClone(false);
        stream.getTracks().forEach(track => track.stop());
        recordingStreamRef.current = null;
        mediaRecorderRef.current = null;
      };
      mediaRecorderRef.current = recorder;
      recorder.start();
      setIsRecordingClone(true);
      setCloneStatus('Recording...');
    } catch (error) {
      stopRecordingVisuals();
      setCloneStatus(error instanceof Error ? error.message : 'Failed to start recording');
      recordingStreamRef.current?.getTracks().forEach(track => track.stop());
      recordingStreamRef.current = null;
      setIsRecordingClone(false);
    }
  }, [startRecordingVisuals, stopRecordingVisuals, recordingSeconds]);

  const stopCloneRecording = useCallback(() => {
    const recorder = mediaRecorderRef.current;
    if (!recorder || recorder.state === 'inactive') return;
    recorder.stop();
    setCloneStatus('Processing recording...');
  }, []);

  const cloneVoice = useCallback(async () => {
    if (cloneRecordings.length === 0) {
      setCloneStatus('Record at least one sample first.');
      return;
    }
    const totalSeconds = cloneRecordings.reduce((sum, clip) => sum + clip.durationSeconds, 0);
    if (totalSeconds < CLONE_RECORDING_TARGET_SECONDS) {
      setCloneStatus('Record at least 2:00 total before creating a clone.');
      return;
    }
    const name = cloneVoiceName.trim();
    if (!name) {
      setCloneStatus('Name your cloned voice first.');
      return;
    }

    setIsCloningVoice(true);
    setCloneStatus(null);
    try {
      const token = await getAuthToken();
      const created = await apiClient.cloneDialogmachineElevenlabsVoice(
        name,
        cloneRecordings.map(clip => clip.file),
        token
      );
      setCloneStatus(`Cloned voice created: ${created.name}`);
      await loadElevenlabsVoices();
      setSelectedElevenlabsVoiceId(created.voice_id);
      setSelectedElevenlabsVoiceName(created.name);
    } catch (error) {
      setCloneStatus(error instanceof Error ? error.message : 'Failed to clone voice');
    } finally {
      setIsCloningVoice(false);
    }
  }, [cloneRecordings, cloneVoiceName, getAuthToken, loadElevenlabsVoices]);

  const removeCloneRecording = useCallback((id: string) => {
    setCloneRecordings(prev => {
      const next: CloneRecording[] = [];
      for (const clip of prev) {
        if (clip.id === id) {
          URL.revokeObjectURL(clip.url);
        } else {
          next.push(clip);
        }
      }
      return next;
    });
  }, []);

  const selectedVoiceCategory = useMemo(() => {
    if (!selectedElevenlabsVoiceId) return null;
    return elevenlabsVoices.find(v => v.voice_id === selectedElevenlabsVoiceId)?.category || null;
  }, [selectedElevenlabsVoiceId, elevenlabsVoices]);
  const selectedDialogmachineLlmOption = useMemo(() => {
    return (
      dialogmachineLlmOptions.find(option => option.id === dialogmachineLlmProvider) ||
      DEFAULT_DIALOGMACHINE_LLM_OPTIONS.find(option => option.id === dialogmachineLlmProvider) ||
      DEFAULT_DIALOGMACHINE_LLM_OPTIONS[0]
    );
  }, [dialogmachineLlmOptions, dialogmachineLlmProvider]);

  const totalCloneRecordingSeconds = useMemo(
    () => cloneRecordings.reduce((sum, clip) => sum + clip.durationSeconds, 0),
    [cloneRecordings]
  );
  const cloneBudgetProgress = Math.min(1, totalCloneRecordingSeconds / CLONE_RECORDING_TARGET_SECONDS);
  const canCreateClone = totalCloneRecordingSeconds >= CLONE_RECORDING_TARGET_SECONDS;

  const voiceConnectionState = voiceConnected
    ? 'connected'
    : voiceConnecting
      ? 'connecting'
      : 'disconnected';

  const selectedCompanionName = useMemo(() => {
    return companions.find(c => c.id === selectedCompanionId)?.name || 'Select companion';
  }, [companions, selectedCompanionId]);

  useEffect(() => {
    if (!selectedCompanionId) {
      setCompanionName('');
      setSavedCompanionName('');
      setNameSyncedCompanionId(null);
      return;
    }
    const currentName = companions.find(c => c.id === selectedCompanionId)?.name || '';
    if (nameSyncedCompanionId !== selectedCompanionId) {
      setCompanionName(currentName);
      setSavedCompanionName(currentName);
      setNameSyncedCompanionId(selectedCompanionId);
      return;
    }
    if (!savedCompanionName && currentName) {
      setSavedCompanionName(currentName);
      if (!companionName) {
        setCompanionName(currentName);
      }
    }
  }, [selectedCompanionId, companions, nameSyncedCompanionId, savedCompanionName, companionName]);

  useEffect(() => {
    if (!selectedElevenlabsVoiceId || elevenlabsVoices.length === 0) return;
    const match = elevenlabsVoices.find(voice => voice.voice_id === selectedElevenlabsVoiceId);
    if (!match) return;
    if (selectedElevenlabsVoiceName !== match.name) {
      setSelectedElevenlabsVoiceName(match.name);
    }
    if (savedElevenlabsVoiceId === selectedElevenlabsVoiceId && savedElevenlabsVoiceName !== match.name) {
      setSavedElevenlabsVoiceName(match.name);
    }
  }, [
    selectedElevenlabsVoiceId,
    selectedElevenlabsVoiceName,
    savedElevenlabsVoiceId,
    savedElevenlabsVoiceName,
    elevenlabsVoices,
  ]);

  return (
    <div className="grid h-full min-h-0 grid-cols-1 lg:grid-cols-[3fr_4fr]">
      <div className="min-h-0 border-b border-white/20 bg-[var(--color-panel-bg)] px-4 py-4 lg:border-b-0 lg:border-r">
        <div className="flex h-full min-h-0 flex-col">
          <div className="flex items-center justify-between pb-4">
            <h1 className="text-2xl font-light text-[var(--color-title-text)]">Dialog Machine</h1>
            <span className="text-xs text-white/50">Restaurant-open workflow</span>
          </div>

          {(workspaceError || saveMessage) && (
            <div
              className={`mb-4 border px-3 py-2 text-sm ${
                workspaceError ? 'border-red-500/30 text-red-300' : 'border-green-500/30 text-green-300'
              }`}
            >
              {workspaceError || saveMessage}
            </div>
          )}

          <div className="flex flex-1 min-h-0">
            <div className="w-24 min-w-[76px] flex-shrink-0 pr-3">
              <VerticalTabs
                tabs={SETTINGS_TABS}
                activeTab={activeSettingsTab}
                onTabChange={setActiveSettingsTab}
              />
            </div>

            <div className="flex-1 min-w-0 border-l border-white/10 pl-6 pb-4 overflow-y-auto scrollbar-none">
              {activeSettingsTab === 'workspace' && (
                <div className="space-y-5">
              <FormSection
                title="Companion Name"
                description=""
              >
                <div className="space-y-3">
                  <input
                    value={companionName}
                    onChange={e => setCompanionName(e.target.value)}
                    placeholder="Companion name"
                    aria-label="Companion Name"
                    className="w-full bg-[var(--color-input-editable)] text-white text-sm px-3 py-2 focus:outline-none"
                  />
                  <div className="text-xs text-white/55">
                    Active: <span className="text-white/75">{selectedCompanionName}</span>
                    {' · '}
                    relationship user <span className="text-white/75">{currentUserId || '-'}</span>
                  </div>
                </div>
              </FormSection>

                  <FormSection
                    title="Base System Prompt"
                    description="Companion-level prompt. Applied unless a DialogMachine relationship override exists."
                  >
                    <div className="relative">
                      <Textarea
                        minHeight={220}
                        className="pr-4 pb-16"
                        value={basePrompt}
                        onChange={e => setBasePrompt(e.target.value)}
                        placeholder="Base companion system prompt..."
                      />
                      <button
                        type="button"
                        onClick={() => setIsBasePromptFocused(true)}
                        className="absolute left-1/2 bottom-3 -translate-x-1/2 inline-flex items-center gap-2 rounded-full border border-white/12 bg-black/45 px-3 py-1.5 text-xs text-white/90 transition-colors hover:bg-black/60 hover:text-white focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-white/35"
                        aria-label="Expand base system prompt into focused document view"
                      >
                        <Maximize2 className="h-3.5 w-3.5" />
                        Expand
                      </button>
                    </div>
                  </FormSection>

                  <FormSection
                    title="Guardrails"
                    description="Relationship-scoped behavior guardrails appended at runtime for the active relationship."
                  >
                    <div className="space-y-3">
                      <Textarea
                        minHeight={170}
                        value={relationshipGuardrails}
                        onChange={e => setRelationshipGuardrails(e.target.value)}
                        placeholder="Optional relationship guardrails..."
                      />
                      <div className="flex justify-end">
                        <button
                          onClick={() => setRelationshipGuardrails('')}
                          className="px-4 py-2 text-sm bg-white/5 hover:bg-white/10 transition-colors"
                        >
                          Clear Guardrails
                        </button>
                      </div>
                    </div>
                  </FormSection>

                  <FormSection
                    title="Simulation Background Noise"
                    description="Blend subtle ambience under assistant voice during Simulate sessions."
                  >
                    <div className="space-y-3">
                      <CustomSwitch
                        checked={backgroundNoiseEnabled}
                        onCheckedChange={setBackgroundNoiseEnabled}
                        label="Enable background noise"
                        labelTextClassName="text-sm text-white/85"
                      />
                      <div className={`space-y-1 ${backgroundNoiseEnabled ? '' : 'opacity-60'}`}>
                        <label className="text-xs text-white/50">Noise Preset</label>
                        <Dropdown
                          options={BACKGROUND_NOISE_OPTIONS.map(option => ({
                            value: option.value,
                            label: option.label,
                            description: option.description,
                          }))}
                          value={backgroundNoiseType}
                          onChange={value => setBackgroundNoiseType(normalizeNoiseType(value))}
                          disabled={!backgroundNoiseEnabled}
                          placeholder="Select background noise"
                        />
                      </div>
                    </div>
                  </FormSection>

                  <FormSection
                    title="Fast Brain LLM Model"
                    description="Choose the LLM provider/model used by Text, Simulate, and DialogMachine dial sessions."
                  >
                    <div className="space-y-2">
                      <label className="text-xs text-white/50">Model</label>
                      <Dropdown
                        options={dialogmachineLlmOptions.map(option => ({
                          value: option.id,
                          label: option.label,
                          description: option.description,
                        }))}
                        value={dialogmachineLlmProvider}
                        onChange={value => {
                          if (!isDialogmachineLlmProvider(value)) return;
                          setDialogmachineLlmProvider(value);
                        }}
                        placeholder="Select LLM model"
                      />
                    </div>
                  </FormSection>

                  <FormSection
                    title="ElevenLabs Voice"
                    description="Select any ElevenLabs voice and configure per-workspace TTS model/settings."
                  >
                    <div className="space-y-4">
                      <div className="flex items-center">
                        <span className="text-xs text-white/50">
                          {elevenlabsVoicesLoading
                            ? 'Loading voices...'
                            : `${elevenlabsVoices.length} voice${elevenlabsVoices.length === 1 ? '' : 's'} loaded`}
                        </span>
                      </div>
                      {elevenlabsVoicesError && (
                        <p className="text-xs text-red-300">{elevenlabsVoicesError}</p>
                      )}

                      <div className="space-y-1">
                        <label className="text-xs text-white/50">Voice</label>
                        <Dropdown
                          options={elevenlabsVoices.map(voice => ({
                            value: voice.voice_id,
                            label: voice.name,
                            description: voice.category || undefined,
                          }))}
                          value={selectedElevenlabsVoiceId}
                          onChange={value => {
                            setSelectedElevenlabsVoiceId(value);
                            const picked = elevenlabsVoices.find(v => v.voice_id === value);
                            setSelectedElevenlabsVoiceName(picked?.name || '');
                            applyElevenlabsVoicePreset(value);
                          }}
                          placeholder={elevenlabsVoicesLoading ? 'Loading voices...' : 'Select ElevenLabs voice'}
                        />
                        {selectedVoiceCategory && (
                          <p className="text-xs text-white/45">Category: {selectedVoiceCategory}</p>
                        )}
                      </div>

                      <div className="space-y-1">
                        <label className="text-xs text-white/50">Model</label>
                        <Dropdown
                          options={elevenlabsModels.map(model => ({
                            value: model.id,
                            label: model.label,
                          }))}
                          value={elevenlabsModelId}
                          onChange={setElevenlabsModelId}
                          placeholder="Select ElevenLabs model"
                        />
                      </div>

                      <div className="grid gap-4 sm:grid-cols-2">
                        <label className="space-y-1 text-xs text-white/70">
                          <span className="block">Speed ({elevenlabsSpeed.toFixed(2)})</span>
                          <div className="flex items-center justify-between text-[11px] text-white/45">
                            <span>Slower</span>
                            <span>Faster</span>
                          </div>
                          <input
                            type="range"
                            min={0.7}
                            max={1.2}
                            step={0.01}
                            value={elevenlabsSpeed}
                            onChange={event => setElevenlabsSpeed(Number(event.target.value))}
                            style={buildSliderStyle(elevenlabsSpeed, 0.7, 1.2)}
                            className="dm-range"
                          />
                        </label>
                        <label className="space-y-1 text-xs text-white/70">
                          <span className="block">Stability ({Math.round(elevenlabsStability * 100)}%)</span>
                          <div className="flex items-center justify-between text-[11px] text-white/45">
                            <span>More variable</span>
                            <span>More stable</span>
                          </div>
                          <input
                            type="range"
                            min={0}
                            max={1}
                            step={0.01}
                            value={elevenlabsStability}
                            onChange={event => setElevenlabsStability(Number(event.target.value))}
                            style={buildSliderStyle(elevenlabsStability, 0, 1)}
                            className="dm-range"
                          />
                        </label>
                        <label className="space-y-1 text-xs text-white/70">
                          <span className="block">Similarity ({Math.round(elevenlabsSimilarity * 100)}%)</span>
                          <div className="flex items-center justify-between text-[11px] text-white/45">
                            <span>Low</span>
                            <span>High</span>
                          </div>
                          <input
                            type="range"
                            min={0}
                            max={1}
                            step={0.01}
                            value={elevenlabsSimilarity}
                            onChange={event => setElevenlabsSimilarity(Number(event.target.value))}
                            style={buildSliderStyle(elevenlabsSimilarity, 0, 1)}
                            className="dm-range"
                          />
                        </label>
                        <label className="space-y-1 text-xs text-white/70">
                          <span className="block">Style ({Math.round(elevenlabsStyle * 100)}%)</span>
                          <div className="flex items-center justify-between text-[11px] text-white/45">
                            <span>None</span>
                            <span>Exaggerated</span>
                          </div>
                          <input
                            type="range"
                            min={0}
                            max={1}
                            step={0.01}
                            value={elevenlabsStyle}
                            onChange={event => setElevenlabsStyle(Number(event.target.value))}
                            style={buildSliderStyle(elevenlabsStyle, 0, 1)}
                            className="dm-range"
                          />
                        </label>
                      </div>

                      <div className="grid gap-3 sm:grid-cols-2">
                        <CustomSwitch
                          checked={elevenlabsSpeakerBoost}
                          onCheckedChange={setElevenlabsSpeakerBoost}
                          label="Speaker boost"
                          labelTextClassName="text-sm text-white/85"
                        />
                        <CustomSwitch
                          checked={elevenlabsLanguageOverride}
                          onCheckedChange={setElevenlabsLanguageOverride}
                          label="Language override"
                          labelTextClassName="text-sm text-white/85"
                        />
                      </div>

                      <div className={`space-y-1 ${elevenlabsLanguageOverride ? '' : 'opacity-60'}`}>
                        <label className="text-xs text-white/50">Language</label>
                        <Dropdown
                          options={ELEVENLABS_LANGUAGE_OPTIONS.map(option => ({
                            value: option.value,
                            label: option.label,
                          }))}
                          value={elevenlabsLanguageCode}
                          onChange={setElevenlabsLanguageCode}
                          disabled={!elevenlabsLanguageOverride}
                          placeholder="Select language"
                        />
                      </div>

                    </div>
                  </FormSection>

                  <FormSection
                    title="Tools"
                    description="Configure runtime call tools. You can use presets or multi-select manually."
                  >
                    <div className="space-y-3">
                      <div className="space-y-1">
                        <label className="text-xs text-white/50">Preset</label>
                        <Dropdown
                          options={[
                            ...TOOL_PRESETS.map(preset => ({
                              value: preset.value,
                              label: preset.label,
                              description: preset.description,
                            })),
                            {
                              value: CUSTOM_TOOL_PRESET,
                              label: 'Custom',
                              description: 'Manual multi-select',
                            },
                          ]}
                          value={getToolPreset(selectedTools)}
                          onChange={value => {
                            const preset = TOOL_PRESETS.find(item => item.value === value);
                            if (!preset) return;
                            setSelectedTools([...preset.selected]);
                          }}
                          placeholder="Select preset"
                        />
                      </div>
                      <div className="space-y-2 border border-white/10 bg-black/20 p-3">
                        {TOOL_OPTIONS.map(option => (
                          <div key={option.value} className="space-y-1">
                            <CustomSwitch
                              checked={selectedTools.includes(option.value)}
                              onCheckedChange={checked => {
                                setSelectedTools(prev => {
                                  if (checked) return normalizeSelectedTools([...prev, option.value]);
                                  return normalizeSelectedTools(
                                    prev.filter(value => value !== option.value)
                                  );
                                });
                              }}
                              label={option.label}
                              labelTextClassName="text-sm text-white/85"
                            />
                            <p className="pl-0 text-xs text-white/50">{option.description}</p>
                          </div>
                        ))}
                      </div>
                    </div>
                  </FormSection>
                </div>
              )}
              {activeSettingsTab === 'relationship' && (
                <div className="space-y-5">
                  <FormSection
                    title="Relationship User"
                    description="Search and switch the active relationship user for Simulate and Dial sessions."
                  >
                    <div className="space-y-3">
                      <input
                        value={relationshipUserSearch}
                        onChange={event => setRelationshipUserSearch(event.target.value)}
                        placeholder="Search by user id or profile name"
                        className="w-full bg-[var(--color-input-editable)] text-white text-sm px-3 py-2 focus:outline-none"
                      />
                      <Dropdown
                        options={filteredRelationshipUsers.map(user => ({
                          value: user.user_id,
                          label: formatRelationshipUserLabel(user.user_id),
                          description: user.profile_preview?.name || undefined,
                        }))}
                        value={currentUserId || undefined}
                        onChange={value => {
                          void handleSwitchRelationshipUser(value);
                        }}
                        placeholder={
                          filteredRelationshipUsers.length
                            ? 'Select relationship user'
                            : 'No matching users'
                        }
                        disabled={
                          relationshipLoading ||
                          isRelationshipMutating ||
                          filteredRelationshipUsers.length === 0
                        }
                      />
                      <div className="flex items-center gap-2">
                        <button
                          onClick={() => {
                            void handleCreateRelationshipUser();
                          }}
                          disabled={isRelationshipMutating}
                          className="px-4 py-2 text-sm bg-white/5 hover:bg-white/10 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                        >
                          New Test User
                        </button>
                        <button
                          onClick={() => {
                            void handleResetRelationship();
                          }}
                          disabled={!currentUserId || isRelationshipMutating}
                          className="px-4 py-2 text-sm bg-white/5 hover:bg-white/10 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                        >
                          Reset Conversation
                        </button>
                      </div>
                      <div className="text-xs text-white/55">
                        Active: <span className="text-white/75">{selectedCompanionName}</span>
                        {' · '}
                        relationship user <span className="text-white/75">{currentUserId || '-'}</span>
                      </div>
                    </div>
                  </FormSection>

                  <FormSection
                    title="Prompt Override"
                    description="Relationship-scoped override. Runtime precedence: override first, then base prompt."
                  >
                    <div className="space-y-3">
                      <Textarea
                        minHeight={220}
                        value={promptOverride}
                        onChange={e => setPromptOverride(e.target.value)}
                        placeholder="Optional relationship override..."
                      />
                      <div className="flex justify-end">
                        <button
                          onClick={() => setPromptOverride('')}
                          className="px-4 py-2 text-sm bg-white/5 hover:bg-white/10 transition-colors"
                        >
                          Clear Override
                        </button>
                      </div>
                    </div>
                  </FormSection>
                </div>
              )}
              {activeSettingsTab === 'hot-context' && (
                <div className="space-y-5">
                  <FormSection
                    title="hot_context.md"
                    description="Fast-brain context file loaded for the active relationship during runtime."
                  >
                    <Textarea
                      minHeight={420}
                      value={hotContext}
                      onChange={e => setHotContext(e.target.value)}
                      placeholder="hot_context.md contents..."
                    />
                  </FormSection>
                </div>
              )}
              {activeSettingsTab === 'voice-clone' && (
                <div className="space-y-5">
                  <FormSection
                    title="Clone ElevenLabs Voice"
                    description="Read the transcript, record your sample, and create a reusable voice."
                  >
                    <div className="space-y-4">
                      <div className="text-xs text-white/50">
                        New voices appear in Workspace → ElevenLabs Voice after cloning.
                      </div>
                      <Textarea
                        minHeight={220}
                        value={ELEVENLABS_CLONE_TRANSCRIPT}
                        readOnly
                        className="opacity-90"
                      />
                      <label className="space-y-1 block">
                        <span className="text-xs text-white/50">Clone voice name</span>
                        <input
                          value={cloneVoiceName}
                          onChange={event => setCloneVoiceName(event.target.value)}
                          placeholder="My cloned voice"
                          className="w-full bg-[var(--color-input-editable)] text-white text-sm px-3 py-2 focus:outline-none"
                        />
                      </label>
                      <div className="flex flex-wrap items-center gap-2">
                        <button
                          type="button"
                          onClick={() => {
                            if (isRecordingClone) {
                              stopCloneRecording();
                              return;
                            }
                            void startCloneRecording();
                          }}
                          disabled={isCloningVoice}
                          className="inline-flex items-center gap-2 px-3 py-2 text-sm bg-white/10 hover:bg-white/20 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                        >
                          {isRecordingClone ? (
                            <Square className="h-4 w-4" />
                          ) : (
                            <Mic className="h-4 w-4" />
                          )}
                          {isRecordingClone ? 'Stop' : 'Record'}
                        </button>
                      </div>
                      {hasInteractedCloneRecorder && (
                        <div className="border border-white/10 bg-black p-3 space-y-2">
                          <div className="flex items-center justify-between text-xs">
                            <span className="text-white/70">
                              {isRecordingClone
                                ? `Recording ${recordingSeconds}s`
                                : cloneRecordings.length > 0
                                  ? 'Recording ready'
                                  : 'Recorder idle'}
                            </span>
                            {isRecordingClone && (
                              <span className="inline-flex items-center gap-1 text-red-300">
                                <span className="h-2 w-2 rounded-full bg-red-400 animate-pulse" />
                                Live
                              </span>
                            )}
                          </div>
                          <CloneWaveform data={recordingWave} />
                          <p className="text-[11px] text-white/45">
                            Tip: record 20-30 seconds in a quiet environment, close to your mic.
                          </p>
                        </div>
                      )}
                      <div className="flex items-center gap-2">
                        <CloneBudgetDonut progress={cloneBudgetProgress} />
                        <div className="text-xs text-white/70">
                          Recording budget: {formatSeconds(totalCloneRecordingSeconds)} /{' '}
                          {formatSeconds(CLONE_RECORDING_TARGET_SECONDS)}
                        </div>
                      </div>
                      {cloneRecordings.length === 0 ? (
                        <p className="text-xs text-white/45">No recordings yet.</p>
                      ) : (
                        <div className="space-y-2">
                          {cloneRecordings.map((clip, index) => (
                            <div
                              key={clip.id}
                              className="flex items-center gap-1 rounded-full bg-white px-2"
                            >
                              <audio controls src={clip.url} className="dialogmachine-clone-audio flex-1 min-w-0" />
                              <button
                                type="button"
                                onClick={() => removeCloneRecording(clip.id)}
                                className="inline-flex h-8 w-8 items-center justify-center text-black hover:text-black/80 transition-colors"
                                aria-label={`Delete recording ${index + 1}`}
                              >
                                <Trash2 className="h-4 w-4" />
                              </button>
                            </div>
                          ))}
                        </div>
                      )}
                      {canCreateClone ? (
                        <button
                          type="button"
                          onClick={() => {
                            void cloneVoice();
                          }}
                          disabled={isCloningVoice || isRecordingClone || !cloneVoiceName.trim()}
                          className="mt-2 w-full px-3 py-2 text-sm bg-white text-black hover:bg-white/90 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                        >
                          {isCloningVoice ? 'Cloning...' : 'Create Clone'}
                        </button>
                      ) : (
                        <p className="text-xs text-white/45">
                          Record at least 2:00 total to enable cloning.
                        </p>
                      )}
                      {cloneStatus && <p className="text-xs text-white/70">{cloneStatus}</p>}
                    </div>
                  </FormSection>
                </div>
              )}
            </div>
          </div>

          <div className="flex-shrink-0 pt-4 pb-2">
            <button
              className={`w-full h-12 text-lg font-medium rounded-full transition-colors ${
                hasUnsavedChanges && !isSaving
                  ? 'bg-white text-black hover:bg-white/90'
                  : 'bg-[var(--color-input-readonly)] text-[var(--color-text-readonly)] cursor-not-allowed'
              }`}
              disabled={!hasUnsavedChanges || isSaving || !selectedCompanionId || !currentUserId}
              onClick={() => {
                void handleSaveAll();
              }}
            >
              {isSaving ? 'Saving…' : 'Save'}
            </button>
          </div>
        </div>
      </div>

      <div className="min-h-0 bg-[var(--color-conversation-bg)]">
        <div className="relative flex h-full min-h-0 flex-col overflow-visible">
          <div className="flex items-center justify-between border-b border-white/10 px-4 py-3 overflow-visible">
            <RelationshipSelector
              currentUserId={currentUserId}
              currentMessageCount={currentRelationship?.message_count || 0}
              lastInteractionAt={currentRelationship?.last_interaction_at || null}
              testUsers={testUsers}
              isLoading={relationshipLoading || isRelationshipMutating}
              disabled={!selectedCompanionId}
              resetTooltip="Reset conversation (clear messages only)"
              onSwitchUser={(userId: string) => {
                void handleSwitchRelationshipUser(userId);
              }}
              onCreateNewUser={() => {
                void handleCreateRelationshipUser();
              }}
              onReset={() => {
                void handleResetRelationship();
              }}
            />

            <div className="flex items-center gap-2">
              <div className="inline-flex items-center rounded-full bg-[var(--color-gray-button)] p-1">
                <button
                  onClick={() => {
                    if (voiceConnected || voiceConnecting) {
                      void stopSession();
                    }
                    setActiveRunTab('text');
                  }}
                  aria-pressed={activeRunTab === 'text'}
                  className={`px-4 py-2 rounded-full text-sm font-medium transition-colors ${
                    activeRunTab === 'text'
                      ? 'bg-white text-black'
                      : 'bg-transparent text-white/80 hover:text-white'
                  }`}
                >
                  Text
                </button>
                <button
                  onClick={() => setActiveRunTab('simulate')}
                  aria-pressed={activeRunTab === 'simulate'}
                  className={`px-4 py-2 rounded-full text-sm font-medium transition-colors ${
                    activeRunTab === 'simulate'
                      ? 'bg-white text-black'
                      : 'bg-transparent text-white/80 hover:text-white'
                  }`}
                >
                  Voice Simulate
                </button>
                <button
                  onClick={() => setActiveRunTab('dial')}
                  aria-pressed={activeRunTab === 'dial'}
                  className={`px-4 py-2 rounded-full text-sm font-medium transition-colors ${
                    activeRunTab === 'dial'
                      ? 'bg-white text-black'
                      : 'bg-transparent text-white/80 hover:text-white'
                  }`}
                >
                  Dial + Transcript
                </button>
              </div>
            </div>
          </div>

          <div className="flex-1 min-h-0 overflow-hidden bg-black">
            {activeRunTab === 'text' ? (
              <TextSimulationPanel
                messages={textMessages}
                isStreaming={textStreaming}
                streamingContent={textStreamingContent}
                isConnected={textConnected}
                isConnecting={textConnecting}
                error={textError}
                onSendMessage={handleSendTextModeMessage}
              />
            ) : activeRunTab === 'simulate' ? (
              <div className="h-full flex flex-col items-center justify-center">
                <VoiceOrb
                  connectionState={voiceConnectionState}
                  userAmplitude={userAmplitude}
                  companionAmplitude={companionAmplitude}
                  onConnect={() => {
                    disconnectTextChat();
                    setActiveRunTab('simulate');
                    void startSession({
                      companionId: selectedCompanionId || '',
                      clientExternalUserId: currentUserId || '',
                      useV2: true,
                      useDialogmachine: true,
                      voiceConfig: {
                        ...FAST_BRAIN_VOICE_CONFIG,
                        llm_provider: dialogmachineLlmProvider,
                        voice_name: selectedElevenlabsVoiceName || FAST_BRAIN_VOICE_CONFIG.voice_name,
                        tts_voice_id: selectedElevenlabsVoiceId || undefined,
                        elevenlabs_model_id: elevenlabsModelId,
                        elevenlabs_stability: elevenlabsStability,
                        elevenlabs_similarity_boost: elevenlabsSimilarity,
                        elevenlabs_style: elevenlabsStyle,
                        elevenlabs_speed: elevenlabsSpeed,
                        elevenlabs_use_speaker_boost: elevenlabsSpeakerBoost,
                        elevenlabs_language_code: elevenlabsLanguageOverride
                          ? elevenlabsLanguageCode
                          : null,
                        background_noise_enabled: backgroundNoiseEnabled,
                        background_noise_type: backgroundNoiseType,
                        background_noise_volume: backgroundNoiseVolume,
                        fast_brain_delegate_enabled: taskDelegationEnabled,
                        fast_brain_end_call_enabled: endCallEnabled,
                      },
                    });
                  }}
                  onDisconnect={() => {
                    void stopSession();
                  }}
                  onPause={() => {
                    void pauseSession();
                  }}
                  onResume={() => {
                    void resumeSession();
                  }}
                  isPaused={isPaused}
                  isCompanionSpeaking={isCompanionSpeaking}
                />
                <p className="mt-4 text-sm text-white/55">
                  cartesia -&gt; {selectedDialogmachineLlmOption.label} -&gt; elevenlabs
                </p>
                <p className="mt-1 text-xs text-white/45">
                  Uses relationship prompt override + hot context when configured.
                </p>
              </div>
            ) : (
              <div className="h-full overflow-y-auto p-4 space-y-4">
                <section className="border border-white/10 bg-[var(--color-panel-bg)] p-4 space-y-3">
                  <h2 className="text-sm uppercase tracking-wide text-white/80">Real Twilio Call</h2>
                  <div className="grid gap-3 md:grid-cols-2">
                    <label className="space-y-1">
                      <span className="text-xs text-white/50">To Number (E.164)</span>
                      <input
                        value={phoneNumber}
                        onChange={e => setPhoneNumber(e.target.value)}
                        placeholder="+14155551234"
                        className="w-full bg-[var(--color-input-editable)] text-white text-sm px-3 py-2 focus:outline-none"
                      />
                    </label>
                    <label className="space-y-1">
                      <span className="text-xs text-white/50">Optional IVR Goal</span>
                      <input
                        value={ivrGoal}
                        onChange={e => setIvrGoal(e.target.value)}
                        placeholder="Ask if the restaurant is open now"
                        className="w-full bg-[var(--color-input-editable)] text-white text-sm px-3 py-2 focus:outline-none"
                      />
                    </label>
                  </div>

                  <div className="flex items-center gap-3">
                    <button
                      onClick={() => void handleDial()}
                      disabled={isDialing || !selectedCompanionId || !currentUserId}
                      className="px-4 py-2 text-sm bg-white/10 hover:bg-white/20 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                    >
                      {isDialing ? 'Dialing...' : 'Dial Number'}
                    </button>
                    <span className="text-xs text-white/55">
                      status: <span className="text-white/75">{callStatus}</span>
                      {callDuration !== null ? ` · duration: ${callDuration}s` : ''}
                    </span>
                  </div>

                  {callSid && (
                    <div className="text-xs text-white/55">
                      call_sid: <span className="text-white/75">{callSid}</span>
                    </div>
                  )}
                  {dialError && <div className="text-sm text-red-300">{dialError}</div>}
                </section>

                <section className="border border-white/10 bg-[var(--color-panel-bg)] p-4 space-y-3">
                  <h2 className="text-sm uppercase tracking-wide text-white/80">Call Transcript</h2>
                  <div className="flex gap-2">
                    <input
                      value={transcriptCallSid}
                      onChange={e => setTranscriptCallSid(e.target.value)}
                      placeholder="CAxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
                      className="flex-1 bg-[var(--color-input-editable)] text-white text-sm px-3 py-2 focus:outline-none"
                    />
                    <button
                      onClick={() => void loadTranscript()}
                      disabled={transcriptLoading || !selectedCompanionId || !currentUserId}
                      className="px-4 py-2 text-sm bg-white/10 hover:bg-white/20 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                    >
                      {transcriptLoading ? 'Loading...' : 'Load Transcript'}
                    </button>
                  </div>

                  {transcriptError && <div className="text-sm text-red-300">{transcriptError}</div>}

                  <div className="max-h-[420px] overflow-auto border border-white/10 bg-black/30 p-3">
                    {transcript.length === 0 ? (
                      <p className="text-sm text-white/45">No transcript messages loaded.</p>
                    ) : (
                      <div className="space-y-3">
                        {transcript.map(message => (
                          <div
                            key={message.id}
                            className="border border-white/10 bg-black/30 p-2"
                          >
                            <div className="flex items-center justify-between text-xs text-white/50">
                              <span>{message.role}</span>
                              <span>{new Date(message.created_at).toLocaleTimeString()}</span>
                            </div>
                            <p className="mt-1 whitespace-pre-wrap text-sm text-white/80">
                              {message.content}
                            </p>
                            {message.call_mode && (
                              <p className="mt-1 text-[11px] text-white/45">
                                mode: {message.call_mode}
                              </p>
                            )}
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                </section>
              </div>
            )}
          </div>
        </div>
      </div>

      {isBasePromptFocused && (
        <div
          className="fixed inset-0 z-[80] flex items-center justify-center bg-black/65 backdrop-blur-[2px] p-4"
          role="dialog"
          aria-modal="true"
          aria-label="Base system prompt focused view"
          onPointerDown={event => {
            backdropPressStartedRef.current = event.target === event.currentTarget;
          }}
          onPointerUp={event => {
            const endedOnBackdrop = event.target === event.currentTarget;
            if (backdropPressStartedRef.current && endedOnBackdrop) {
              setIsBasePromptFocused(false);
            }
            backdropPressStartedRef.current = false;
          }}
          onPointerCancel={() => {
            backdropPressStartedRef.current = false;
          }}
        >
          <div
            className="relative w-full max-w-4xl h-[82vh] bg-[var(--color-panel-bg)] border border-white/15 shadow-[0_18px_70px_rgba(0,0,0,0.5)] flex flex-col"
            onClick={event => event.stopPropagation()}
          >
            <div className="absolute right-4 top-4 z-10 flex items-center gap-2">
              <button
                type="button"
                onClick={() => {
                  void handleSaveAll();
                }}
                disabled={!hasUnsavedChanges || isSaving || !selectedCompanionId || !currentUserId}
                className={`inline-flex h-9 items-center px-4 rounded-full text-sm font-medium transition-colors ${
                  !hasUnsavedChanges || isSaving || !selectedCompanionId || !currentUserId
                    ? 'bg-[var(--color-gray-button)] text-white/60 cursor-not-allowed'
                    : 'bg-white text-black hover:bg-white/90'
                }`}
              >
                {isSaving ? 'Saving…' : 'Save'}
              </button>
              <button
                type="button"
                onClick={() => setIsBasePromptFocused(false)}
                className="inline-flex items-center justify-center h-9 w-9 text-white/70 hover:text-white hover:bg-white/10 transition-colors"
                aria-label="Close focused view"
              >
                <X className="h-4 w-4" />
              </button>
            </div>

            <div className="flex-1 overflow-hidden px-6 py-6 pt-14">
              <textarea
                value={basePrompt}
                onChange={e => setBasePrompt(e.target.value)}
                placeholder="Base companion system prompt..."
                className="h-full min-h-[65vh] w-full resize-none overflow-y-auto bg-transparent text-white/92 font-book text-[17px] leading-8 focus:outline-none"
              />
            </div>
          </div>
        </div>
      )}

      <style jsx>{`
        .dm-range {
          appearance: none;
          -webkit-appearance: none;
          width: 100%;
          height: 20px;
          background: transparent;
          cursor: pointer;
          outline: none;
        }

        .dm-range::-webkit-slider-runnable-track {
          height: 4px;
          border-radius: 9999px;
          background: linear-gradient(
            to right,
            rgba(255, 255, 255, 0.9) var(--range-fill, 50%),
            rgba(255, 255, 255, 0.2) var(--range-fill, 50%)
          );
        }

        .dm-range::-webkit-slider-thumb {
          appearance: none;
          -webkit-appearance: none;
          width: 18px;
          height: 18px;
          margin-top: -7px;
          border-radius: 9999px;
          border: 1px solid rgba(255, 255, 255, 0.35);
          background: #ffffff;
          box-shadow: 0 2px 6px rgba(0, 0, 0, 0.45);
          transition: transform 120ms ease, box-shadow 120ms ease;
        }

        .dm-range:hover::-webkit-slider-thumb {
          transform: scale(1.03);
        }

        .dm-range:focus-visible::-webkit-slider-thumb {
          box-shadow:
            0 0 0 3px rgba(255, 255, 255, 0.2),
            0 2px 6px rgba(0, 0, 0, 0.45);
        }

        .dm-range::-moz-range-track {
          height: 4px;
          border-radius: 9999px;
          background: rgba(255, 255, 255, 0.2);
        }

        .dm-range::-moz-range-progress {
          height: 4px;
          border-radius: 9999px;
          background: rgba(255, 255, 255, 0.9);
        }

        .dm-range::-moz-range-thumb {
          width: 18px;
          height: 18px;
          border-radius: 9999px;
          border: 1px solid rgba(255, 255, 255, 0.35);
          background: #ffffff;
          box-shadow: 0 2px 6px rgba(0, 0, 0, 0.45);
          transition: transform 120ms ease, box-shadow 120ms ease;
        }

        .dm-range:hover::-moz-range-thumb {
          transform: scale(1.03);
        }
      `}</style>
    </div>
  );
}
