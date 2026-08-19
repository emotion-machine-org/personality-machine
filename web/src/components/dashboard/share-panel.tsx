'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import type { ChangeEvent, FocusEvent } from 'react';
import Icon from '@/components/ui/icon';
import {
  useCompanionShare,
  useCompanionShareAnalytics,
  useUpdateCompanionShare,
  useDisableCompanionShare,
  type SharePayload,
} from '@/hooks/useCompanionShare';
import type { SessionConfig } from '@/hooks/useWebSocketSession';
import type { CompanionConfig } from '@/lib/types';
import { SHARE_CONTEXT_PLACEHOLDER, normalizeShareContext } from '@/lib/share';
import { buildVoiceSnapshotFromPreset, voiceConfigToSnapshot } from '@/lib/voice-presets';
import type { VoiceSnapshot } from '@/lib/voice-presets';

const isPlainRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);

const DEFAULT_PUBLIC_VOICE_SNAPSHOT: VoiceSnapshot = {
  voicePipeline: {
    pipeline_type: 'stt-llm-tts',
    voice_name: 'alloy',
    stt_provider: 'openai',
    llm_provider: 'openai-gpt4o',
    tts_provider: 'openai',
    temperature: 0.7,
  },
  llmProvider: 'openai-gpt4o',
  temperature: 0.7,
};

const buildShareUrl = (slug: string) => {
  const envUrl = process.env.NEXT_PUBLIC_WEB_URL || process.env.NEXT_PUBLIC_SHARE_BASE_URL;
  if (envUrl) {
    return `${envUrl.replace(/\/$/, '')}/companion/${slug}`;
  }
  if (typeof window !== 'undefined') {
    return `${window.location.origin}/companion/${slug}`;
  }
  return `/companion/${slug}`;
};

const StatusBadge = ({ status }: { status: 'draft' | 'active' | 'disabled' }) => {
  const styles: Record<typeof status, string> = {
    draft: 'bg-[color:var(--color-gray-dark)] text-white/80 border-white/10',
    active: 'bg-[color:var(--color-green-bg)] text-[color:var(--color-green-solid-hover)] border-[color:var(--color-green-border)]/60',
    disabled: 'bg-[color:var(--color-brand-bg)] text-[color:var(--color-brand-solid)] border-[color:var(--color-brand-border)]/40',
  };
  const labels: Record<typeof status, string> = {
    draft: 'Draft',
    active: 'Active',
    disabled: 'Disabled',
  };
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-1 text-xs rounded-full border ${styles[status]}`}>
      <span className="h-1.5 w-1.5 rounded-full bg-current" />
      {labels[status]}
    </span>
  );
};

interface SharePanelBaseProps {
  companionId: string | null;
  sessionConfig?: SessionConfig;
  companionConfig?: CompanionConfig | null;
}

export interface SharePanelContentProps extends SharePanelBaseProps {
  variant?: 'overlay' | 'page';
  onClose?: () => void;
}

export function SharePanelContent({
  companionId,
  sessionConfig,
  companionConfig,
  variant = 'overlay',
  onClose,
}: SharePanelContentProps) {
  const { data: share, isLoading, isFetching, error, refetch: refetchShare } = useCompanionShare(companionId);
  const { data: analytics } = useCompanionShareAnalytics(companionId);
  const updateShare = useUpdateCompanionShare(companionId);
  const disableShare = useDisableCompanionShare(companionId);

  const [copyState, setCopyState] = useState<'idle' | 'copied' | 'error'>('idle');

  // Context field state
  const [contextDraft, setContextDraft] = useState('');
  const [contextBaseline, setContextBaseline] = useState('');
  const [contextFeedback, setContextFeedback] = useState<'idle' | 'saving' | 'saved' | 'error'>('idle');
  const [contextError, setContextError] = useState<string | null>(null);
  const [isSavingContext, setIsSavingContext] = useState(false);
  const contextFeedbackTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const shareUrl = useMemo(() => (share ? buildShareUrl(share.slug) : null), [share]);
  const shareSnapshotRecord = share?.config_snapshot ?? null;

  const shareSnapshotData = useMemo((): {
    voicePipeline: Record<string, unknown> | null;
    llmProvider?: string;
    temperature?: number;
  } => {
    if (!isPlainRecord(shareSnapshotRecord)) {
      return {
        voicePipeline: null,
        llmProvider: undefined,
        temperature: undefined,
      };
    }
    const record = shareSnapshotRecord;
    const pipelineCandidate = record['voice_pipeline'];
    const voicePipeline = isPlainRecord(pipelineCandidate) ? (pipelineCandidate as Record<string, unknown>) : null;
    const llmProvider =
      typeof record['llm_provider'] === 'string' ? (record['llm_provider'] as string) : undefined;
    const rawTemperature = record['temperature'];
    let temperature: number | undefined;
    if (typeof rawTemperature === 'number') {
      temperature = rawTemperature;
    } else if (typeof rawTemperature === 'string') {
      const parsed = Number(rawTemperature);
      if (!Number.isNaN(parsed)) {
        temperature = parsed;
      }
    }
    return { voicePipeline, llmProvider, temperature };
  }, [shareSnapshotRecord]);

  const shareVoiceSnapshot = useMemo<VoiceSnapshot | null>(() => {
    if (!shareSnapshotData.voicePipeline) {
      return null;
    }
    return {
      voicePipeline: shareSnapshotData.voicePipeline,
      llmProvider: shareSnapshotData.llmProvider,
      temperature: shareSnapshotData.temperature,
    };
  }, [shareSnapshotData]);

  const sessionVoiceConfig = sessionConfig?.voiceConfig;
  const sessionVoiceSnapshot = useMemo(
    () => voiceConfigToSnapshot(sessionVoiceConfig),
    [sessionVoiceConfig],
  );

  const savedPopularOption = companionConfig?.voice?.preset ?? null;
  const savedVoiceName = companionConfig?.voice?.voice_name ?? null;
  const savedTemperature = companionConfig?.inference?.temperature ?? null;

  const presetVoiceSnapshot = useMemo(
    () =>
      buildVoiceSnapshotFromPreset(savedPopularOption, {
        voiceName: savedVoiceName,
        temperature: savedTemperature,
      }),
    [savedPopularOption, savedVoiceName, savedTemperature],
  );

  const voiceSnapshot = useMemo<VoiceSnapshot>(() => {
    if (sessionVoiceSnapshot) return sessionVoiceSnapshot;
    if (presetVoiceSnapshot) return presetVoiceSnapshot;
    if (shareVoiceSnapshot) return shareVoiceSnapshot;
    return DEFAULT_PUBLIC_VOICE_SNAPSHOT;
  }, [sessionVoiceSnapshot, presetVoiceSnapshot, shareVoiceSnapshot]);

  const baselineTemperature = typeof savedTemperature === 'number' ? savedTemperature : undefined;
  const effectiveTemperature =
    voiceSnapshot.temperature ??
    shareSnapshotData.temperature ??
    baselineTemperature ??
    0.7;
  const effectiveLlmProvider =
    voiceSnapshot.llmProvider ??
    shareSnapshotData.llmProvider ??
    'openai-gpt4o-mini';
  const pendingChanges = Boolean(share?.status === 'active' && share?.has_pending_changes);

  useEffect(() => {
    const nextValue = share?.description ?? '';
    setContextDraft(nextValue);
    const trimmed = normalizeShareContext(nextValue);
    setContextBaseline(trimmed);
    setContextError(null);
  }, [share?.description]);

  const buildConfigSnapshot = useMemo(() => {
    const systemPrompt = companionConfig?.system_prompt?.full_system_prompt
      || sessionConfig?.systemPrompt
      || 'You are a helpful and friendly companion.';

    const memoryEnabled = Boolean(companionConfig?.memory?.enabled);

    const snapshot: Record<string, unknown> = {
      system_prompt: systemPrompt,
      memory_enabled: memoryEnabled,
      llm_provider: effectiveLlmProvider,
      temperature: effectiveTemperature,
    };

    const pipeline: Record<string, unknown> = {
      ...voiceSnapshot.voicePipeline,
      temperature: effectiveTemperature,
    };
    snapshot.voice_pipeline = pipeline;

    return snapshot;
  }, [companionConfig, sessionConfig, effectiveLlmProvider, effectiveTemperature, voiceSnapshot]);

  const handleToggle = async (field: 'allow_text' | 'allow_voice' | 'expose_status_events') => {
    if (!share) return;
    const payload: SharePayload = { [field]: !share[field] } as SharePayload;
    await updateShare.mutateAsync(payload);
  };

  const handlePublish = async () => {
    if (!share) return;
    await updateShare.mutateAsync({ status: 'active', config_snapshot: buildConfigSnapshot });
  };

  const handleDisable = async () => {
    if (!share) return;
    await disableShare.mutateAsync();
  };

  const localSnapshotFingerprint = useMemo(
    () => JSON.stringify(buildConfigSnapshot),
    [buildConfigSnapshot]
  );

  const lastFingerprintRef = useRef<string | null>(null);

  useEffect(() => {
    if (!companionId) return;
    if (!share) {
      lastFingerprintRef.current = localSnapshotFingerprint;
      return;
    }
    if (lastFingerprintRef.current === null) {
      lastFingerprintRef.current = localSnapshotFingerprint;
      return;
    }
    if (lastFingerprintRef.current !== localSnapshotFingerprint) {
      lastFingerprintRef.current = localSnapshotFingerprint;
      refetchShare();
    }
  }, [companionId, localSnapshotFingerprint, refetchShare, share]);

  const handleCopy = async () => {
    if (!shareUrl) return;
    try {
      await navigator.clipboard.writeText(shareUrl);
      setCopyState('copied');
      setTimeout(() => setCopyState('idle'), 2000);
    } catch (err) {
      console.error('Failed to copy share url', err);
      setCopyState('error');
      setTimeout(() => setCopyState('idle'), 2500);
    }
  };

  const handleContextChange = (event: ChangeEvent<HTMLTextAreaElement>) => {
    const value = event.target.value;
    setContextDraft(value);
    if (contextFeedbackTimeoutRef.current) {
      clearTimeout(contextFeedbackTimeoutRef.current);
      contextFeedbackTimeoutRef.current = null;
    }
    if (contextFeedback === 'saved' || contextFeedback === 'error') {
      setContextFeedback('idle');
    }
    if (contextError) {
      setContextError(null);
    }
  };

  const handleContextBlur = async (event: FocusEvent<HTMLTextAreaElement>) => {
    const normalizedValue = normalizeShareContext(event.target.value);
    setContextDraft(normalizedValue);

    if (!share) return;
    if (isSavingContext) return;
    if (normalizedValue === contextBaseline) return;

    setIsSavingContext(true);
    setContextFeedback('saving');
    setContextError(null);

    try {
      await updateShare.mutateAsync({ description: normalizedValue });
      setContextBaseline(normalizedValue);
      setContextFeedback('saved');
      if (contextFeedbackTimeoutRef.current) {
        clearTimeout(contextFeedbackTimeoutRef.current);
      }
      contextFeedbackTimeoutRef.current = setTimeout(() => {
        setContextFeedback('idle');
        contextFeedbackTimeoutRef.current = null;
      }, 2000);
    } catch (err) {
      console.error('Failed to update share context', err);
      setContextFeedback('error');
      setContextError('Unable to save. Please try again.');
    } finally {
      setIsSavingContext(false);
    }
  };

  useEffect(() => () => {
    if (contextFeedbackTimeoutRef.current) {
      clearTimeout(contextFeedbackTimeoutRef.current);
      contextFeedbackTimeoutRef.current = null;
    }
  }, []);

  const contextStatusMessage =
    contextFeedback === 'saving'
      ? 'Saving…'
      : contextFeedback === 'saved'
      ? 'Saved'
      : contextFeedback === 'error'
      ? contextError ?? 'Unable to save. Please try again.'
      : null;

  const contextStatusClass =
    contextFeedback === 'error'
      ? 'text-[color:var(--color-brand-solid)]'
      : 'text-white/50';

  const busy = updateShare.isPending || disableShare.isPending || isSavingContext;
  const isOverlay = variant === 'overlay';

  const containerClasses = isOverlay
    ? 'max-h-[calc(100vh-8rem)] overflow-y-auto overscroll-contain px-5 py-6'
    : 'w-full max-w-2xl border border-[color:var(--color-gray-button)] bg-black/65 px-8 py-10 shadow-2xl';

  const headingLabel = companionId ? 'Share' : 'Select a companion';
  const headingTitle = companionId ? (share?.display_name || 'Create a public link') : 'Choose a companion';

  const renderShareSettings = () => {
    if (!companionId) {
      return <p className="text-sm text-white/60">Select a companion from the dropdown to configure public sharing.</p>;
    }
    if (!share) {
      if (isLoading || isFetching) {
        return <p>Loading share settings…</p>;
      }
      if (error) {
        return <p className="text-red-300">Unable to load share data. Please try again.</p>;
      }
      return <p>No share configuration found.</p>;
    }

    return (
      <>
        {pendingChanges && (
          <div className="bg-[color:var(--color-brand-bg)]/70 px-3 py-2 text-xs text-[color:var(--color-brand-solid)]">
            <p className="font-medium">Changes detected</p>
            <p className="mt-1 text-[color:var(--color-brand-solid)]/80">
              Publish updates so visitors see the latest prompt, memory settings, and behaviors.
            </p>
          </div>
        )}

        <div className="space-y-2">
          <p className="text-xs uppercase tracking-wide text-white/40">Share Link</p>
          <div className="flex items-center gap-2 bg-black/40 px-3 py-2">
            <div className="flex-1 truncate text-white/80 text-sm" title={shareUrl || ''}>
              {shareUrl}
            </div>
            <button
              type="button"
              className="border border-[color:var(--color-gray-button)] px-2 py-1 text-xs text-white/80 hover:border-white/30"
              onClick={handleCopy}
              disabled={!shareUrl}
            >
              {copyState === 'copied' ? 'Copied!' : copyState === 'error' ? 'Retry' : 'Copy'}
            </button>
          </div>
        </div>

        <div className="mt-3 space-y-2">
          <div className="flex items-center justify-between">
            <label htmlFor="share-context" className="text-sm font-medium text-white">
              Context Description
            </label>
            {contextStatusMessage ? (
              <span className={`text-xs ${contextStatusClass}`}>
                {contextStatusMessage}
              </span>
            ) : null}
          </div>
          <textarea
            id="share-context"
            className="h-24 w-full resize-none bg-black/40 px-3 py-2 text-sm text-white/90 placeholder:text-white/30 focus:bg-black/60 focus:outline-none"
            placeholder={SHARE_CONTEXT_PLACEHOLDER}
            value={contextDraft}
            onChange={handleContextChange}
            onBlur={handleContextBlur}
            disabled={!share}
            rows={4}
          />
          <p className="text-xs text-white/40">
            Give visitors a little context so they know what to expect before they start chatting.
          </p>
        </div>

        <div className="grid grid-cols-2 gap-3 text-xs">
          {([
            { label: 'Sessions', value: analytics?.sessions ?? 0 },
            { label: 'Messages', value: analytics?.total_messages ?? 0 },
            { label: 'Voice Sessions', value: analytics?.total_voice_sessions ?? 0 },
            {
              label: 'Last Activity',
              value: analytics?.last_activity_at ? new Date(analytics.last_activity_at).toLocaleString() : '—',
            },
          ] as const).map((item) => (
            <div
              key={item.label}
              className="bg-black/40 p-3"
            >
              <p className="text-white/50">{item.label}</p>
              <p className="mt-1 text-base text-white/90">{item.value}</p>
            </div>
          ))}
        </div>

        <div className="mt-4 space-y-3">
          <label className="flex items-center justify-between gap-4 text-sm text-white">
            <span className="flex flex-col">
              Allow Text Mode
              <span className="text-xs text-white/40">Enable chat UI for visitors</span>
            </span>
            <button
              type="button"
              onClick={() => handleToggle('allow_text')}
              disabled={busy}
              className={`relative inline-flex h-6 w-10 items-center rounded-full border border-transparent transition ${share.allow_text ? 'bg-[color:var(--color-green-solid)]' : 'bg-[color:var(--color-gray-button)]'}`}
            >
              <span
                className={`inline-block h-4 w-4 transform rounded-full bg-white transition ${share.allow_text ? 'translate-x-4' : 'translate-x-1'}`}
              />
            </button>
          </label>

          <label className="flex items-center justify-between gap-4 text-sm text-white">
            <span className="flex flex-col">
              Allow Voice Mode
              <span className="text-xs text-white/40">Preview — ensure your pipeline supports public access</span>
            </span>
            <button
              type="button"
              onClick={() => handleToggle('allow_voice')}
              disabled={busy}
              className={`relative inline-flex h-6 w-10 items-center rounded-full border border-transparent transition ${share.allow_voice ? 'bg-[color:var(--color-green-solid)]' : 'bg-[color:var(--color-gray-button)]'}`}
            >
              <span
                className={`inline-block h-4 w-4 transform rounded-full bg-white transition ${share.allow_voice ? 'translate-x-4' : 'translate-x-1'}`}
              />
            </button>
          </label>

          <label className="flex items-center justify-between gap-4 text-sm text-white">
            <span className="flex flex-col">
              Show Streaming Status Pills
              <span className="text-xs text-white/40">Allow visitors to see retrieving / thinking phases</span>
            </span>
            <button
              type="button"
              onClick={() => handleToggle('expose_status_events')}
              disabled={busy}
              className={`relative inline-flex h-6 w-10 items-center rounded-full border border-transparent transition ${share.expose_status_events ? 'bg-[color:var(--color-green-solid)]' : 'bg-[color:var(--color-gray-button)]'}`}
            >
              <span
                className={`inline-block h-4 w-4 transform rounded-full bg-white transition ${share.expose_status_events ? 'translate-x-4' : 'translate-x-1'}`}
              />
            </button>
          </label>
        </div>

        <div className="mt-5 flex flex-col gap-2">
          {share.status !== 'active' && (
            <button
              type="button"
              onClick={handlePublish}
              disabled={busy}
              className={`inline-flex items-center justify-center border px-3 py-2 text-sm font-medium transition border-[color:var(--color-green-bg)] bg-[color:var(--color-green-bg)] text-[color:var(--color-green-solid-hover)] hover:border-[color:var(--color-green-solid)] ${busy ? 'opacity-70 cursor-not-allowed' : ''}`}
            >
              Publish Share Link
            </button>
          )}

          {share.status === 'active' && pendingChanges && (
            <button
              type="button"
              onClick={handlePublish}
              disabled={busy}
              className={`inline-flex items-center justify-center border px-3 py-2 text-sm font-medium transition border-[color:var(--color-green-bg)] bg-[color:var(--color-green-bg)] text-[color:var(--color-green-solid-hover)] hover:border-[color:var(--color-green-solid)] ${busy ? 'opacity-70 cursor-not-allowed' : ''}`}
            >
              Publish Updates
            </button>
          )}

          {share.status === 'active' && (
            <button
              type="button"
              onClick={handleDisable}
              disabled={busy}
              className={`inline-flex items-center justify-center border px-3 py-2 text-sm font-medium transition border-[color:var(--color-brand-bg)] bg-[color:var(--color-brand-bg)] text-[color:var(--color-brand-solid)] hover:border-[color:var(--color-brand-solid)] ${busy ? 'opacity-70 cursor-not-allowed' : ''}`}
            >
              Disable Share
            </button>
          )}
          <p className="text-xs text-white/40">
            Share links are public and do not require Clerk authentication. Voice mode will respect your live pipeline and usage limits.
          </p>
        </div>
      </>
    );
  };

  return (
    <div className={containerClasses}>
      <div className="flex items-start justify-between">
        <div>
          <p className="text-sm font-light text-white/40">{headingLabel}</p>
          <div className="mt-0.5 flex items-center gap-2">
            <h3 className="text-xl font-light text-white/90">{headingTitle}</h3>
            {share && <StatusBadge status={share.status} />}
          </div>
        </div>
        {isOverlay && onClose && (
          <button
            type="button"
            onClick={onClose}
            className="text-white/60 hover:text-white"
            aria-label="Close share panel"
          >
            <Icon name="x" size={16} />
          </button>
        )}
      </div>

      <div className="mt-4 space-y-3 text-sm text-white/70">
        {renderShareSettings()}
      </div>
    </div>
  );
}

export interface SharePanelProps extends SharePanelBaseProps {
  companionId: string;
  onClose: () => void;
}

export function SharePanel({ companionId, onClose, sessionConfig, companionConfig }: SharePanelProps) {
  return (
    <div className="absolute right-4 top-24 z-30 w-[360px] max-w-full border border-[color:var(--color-gray-button)] bg-black/65 backdrop-blur shadow-2xl">
      <SharePanelContent
        companionId={companionId}
        sessionConfig={sessionConfig}
        companionConfig={companionConfig}
        onClose={onClose}
        variant="overlay"
      />
    </div>
  );
}
