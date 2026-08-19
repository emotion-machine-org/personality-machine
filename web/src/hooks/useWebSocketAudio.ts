'use client';

import { useRef, useCallback, useState } from 'react';
import { WavRecorder, WavStreamPlayer } from 'wavtools';
import { calculateAmplitudeFromPCM, smoothAmplitude } from '@/lib/audioProcessing';

interface UseWebSocketAudioReturn {
  initializeAudio: (pipelineType?: string) => Promise<void>;
  startRecording: (onAudioData: (data: Int16Array) => void) => Promise<void>;
  stopRecording: () => void;
  playAudio: (audioData: Int16Array) => void;
  cleanup: () => void;
  isInitialized: boolean;
  isRecording: boolean;
  userAmplitude: number;
  companionAmplitude: number;
  isCompanionSpeaking: boolean;
}

const OPENAI_SAMPLE_RATE = 24000;
const TTS_SAMPLE_RATE = 24000;
const STT_SAMPLE_RATE = 16000;
const COMPANION_SILENCE_TIMEOUT = 300; // ms of silence before companion stops speaking

const float32ToInt16 = (src: Float32Array): Int16Array => {
  const dst = new Int16Array(src.length);
  for (let i = 0; i < src.length; i++) {
    const s = Math.max(-1, Math.min(1, src[i]));
    dst[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
  }
  return dst;
};

const resampleInt16 = (input: Int16Array, inputRate: number, targetRate: number): Int16Array => {
  if (!input.length || inputRate === targetRate) {
    return input;
  }
  const ratio = inputRate / targetRate;
  const outputLength = Math.max(1, Math.round(input.length / ratio));
  const output = new Int16Array(outputLength);

  for (let i = 0; i < outputLength; i++) {
    const mappedIndex = i * ratio;
    const leftIndex = Math.floor(mappedIndex);
    const rightIndex = Math.min(leftIndex + 1, input.length - 1);
    const weight = mappedIndex - leftIndex;
    const left = input[leftIndex];
    const right = input[rightIndex];
    const sample = (1 - weight) * left + weight * right;
    const clamped = Math.max(-32768, Math.min(32767, Math.round(sample)));
    output[i] = clamped;
  }

  return output;
};

export function useWebSocketAudio(): UseWebSocketAudioReturn {
  const wavRecorderRef = useRef<WavRecorder | null>(null);
  const wavStreamPlayerRef = useRef<WavStreamPlayer | null>(null);
  const currentPipelineTypeRef = useRef<string | undefined>(undefined);
  const expectedRecordSampleRateRef = useRef<number>(OPENAI_SAMPLE_RATE);
  const expectedPlaybackSampleRateRef = useRef<number>(TTS_SAMPLE_RATE);
  const actualRecordSampleRateRef = useRef<number>(OPENAI_SAMPLE_RATE);
  const actualPlaybackSampleRateRef = useRef<number>(TTS_SAMPLE_RATE);
  const [isInitialized, setIsInitialized] = useState(false);
  const [isRecording, setIsRecording] = useState(false);
  const [userAmplitude, setUserAmplitude] = useState(0);
  const [companionAmplitude, setCompanionAmplitude] = useState(0);
  const [isCompanionSpeaking, setIsCompanionSpeaking] = useState(false);
  const companionSilenceTimerRef = useRef<number | null>(null);
  const userAmplitudeRef = useRef(0);
  const companionAmplitudeRef = useRef(0);

  const initializeAudio = useCallback(async (pipelineType?: string) => {
    const expectedRecordRate = pipelineType === 'stt-llm-tts' ? STT_SAMPLE_RATE : OPENAI_SAMPLE_RATE;
    const expectedPlaybackRate = TTS_SAMPLE_RATE;
    expectedRecordSampleRateRef.current = expectedRecordRate;
    expectedPlaybackSampleRateRef.current = expectedPlaybackRate;

    if (
      isInitialized &&
      currentPipelineTypeRef.current === pipelineType &&
      wavRecorderRef.current &&
      wavStreamPlayerRef.current
    ) {
      return;
    }

    if (isInitialized && currentPipelineTypeRef.current !== pipelineType) {
      try {
        wavRecorderRef.current?.end?.();
      } catch {}
      try {
        wavStreamPlayerRef.current?.interrupt?.();
      } catch {}
      wavRecorderRef.current = null;
      wavStreamPlayerRef.current = null;
      setIsInitialized(false);
    }

    const attempts: Array<{
      recordRate?: number;
      playbackRate?: number;
      label: string;
    }> = [
      { recordRate: expectedRecordRate, playbackRate: expectedPlaybackRate, label: 'expected sample rate' },
      { recordRate: undefined, playbackRate: undefined, label: 'hardware default sample rate' },
    ];

    let lastError: unknown = null;

    for (const attempt of attempts) {
      const recorder = attempt.recordRate
        ? new WavRecorder({ sampleRate: attempt.recordRate })
        : new WavRecorder();
      const player = attempt.playbackRate
        ? new WavStreamPlayer({ sampleRate: attempt.playbackRate })
        : new WavStreamPlayer();

      try {
        await recorder.begin();
        await player.connect();

        wavRecorderRef.current = recorder;
        wavStreamPlayerRef.current = player;
        actualRecordSampleRateRef.current = recorder.sampleRate;
        actualPlaybackSampleRateRef.current = player.sampleRate;
        currentPipelineTypeRef.current = pipelineType;
        setIsInitialized(true);
        console.info(
          `[WEBSOCKET_AUDIO] Initialized recorder at ${recorder.sampleRate}Hz and player at ${player.sampleRate}Hz (${attempt.label}).`
        );
        return;
      } catch (error) {
        lastError = error;
        console.warn(
          `[WEBSOCKET_AUDIO] Failed to init audio context at ${attempt.label}:`,
          error
        );
        try {
          await recorder.end();
        } catch {}
        if (typeof player.interrupt === 'function') {
          try {
            await player.interrupt();
          } catch {}
        }
      }
    }

    throw lastError ?? new Error('Unable to initialize audio context');
  }, [isInitialized]);

  const startRecording = useCallback(
    async (onAudioData: (data: Int16Array) => void) => {
      const wavRecorder = wavRecorderRef.current;
      if (!wavRecorder || !wavStreamPlayerRef.current) {
        throw new Error('Audio not initialized. Call initializeAudio() first.');
      }

      if (isRecording) {
        return;
      }

      try {
        setIsRecording(true);

        await wavRecorder.record((data: { mono: Int16Array; raw: Int16Array }) => {
          let pcm: Int16Array | null = null;

          if (data?.mono) {
            pcm = data.mono instanceof Int16Array ? data.mono : new Int16Array(data.mono);
          } else if (data?.raw) {
            pcm = data.raw instanceof Int16Array ? data.raw : new Int16Array(data.raw);
          } else if (data instanceof Float32Array) {
            pcm = float32ToInt16(data);
          }

          if (pcm && pcm.length) {
            // Calculate user amplitude for visualization
            const rawAmplitude = calculateAmplitudeFromPCM(pcm);
            const smoothed = smoothAmplitude(userAmplitudeRef.current, rawAmplitude, 0.3);
            userAmplitudeRef.current = smoothed;
            setUserAmplitude(smoothed);

            const processed = resampleInt16(
              pcm,
              actualRecordSampleRateRef.current,
              expectedRecordSampleRateRef.current
            );
            onAudioData(processed);
          }
        });
      } catch (error) {
        setIsRecording(false);
        throw error;
      }
    },
    [isRecording]
  );

  const stopRecording = useCallback(() => {
    const wavRecorder = wavRecorderRef.current;
    if (!wavRecorder || !isRecording) {
      return;
    }

    setIsRecording(false);

    try {
      wavRecorder.pause();
    } catch {}
  }, [isRecording]);

  const playAudio = useCallback((audioData: Int16Array) => {
    const wavStreamPlayer = wavStreamPlayerRef.current;
    if (!wavStreamPlayer) {
      return;
    }

    try {
      // Calculate companion amplitude for visualization
      const rawAmplitude = calculateAmplitudeFromPCM(audioData);
      const smoothed = smoothAmplitude(companionAmplitudeRef.current, rawAmplitude, 0.3);
      companionAmplitudeRef.current = smoothed;
      setCompanionAmplitude(smoothed);

      // Mark companion as speaking
      setIsCompanionSpeaking(true);

      // Reset silence timer
      if (companionSilenceTimerRef.current) {
        clearTimeout(companionSilenceTimerRef.current);
      }
      companionSilenceTimerRef.current = window.setTimeout(() => {
        setIsCompanionSpeaking(false);
        setCompanionAmplitude(0);
        companionAmplitudeRef.current = 0;
      }, COMPANION_SILENCE_TIMEOUT);

      const processed = resampleInt16(
        audioData,
        expectedPlaybackSampleRateRef.current,
        actualPlaybackSampleRateRef.current
      );
      wavStreamPlayer.add16BitPCM(processed, `audio-${Date.now()}`);
    } catch {}
  }, []);

  const cleanup = useCallback(() => {
    setIsRecording(false);
    setIsInitialized(false);
    setUserAmplitude(0);
    setCompanionAmplitude(0);
    setIsCompanionSpeaking(false);
    userAmplitudeRef.current = 0;
    companionAmplitudeRef.current = 0;
    currentPipelineTypeRef.current = undefined;

    // Clear silence timer
    if (companionSilenceTimerRef.current) {
      clearTimeout(companionSilenceTimerRef.current);
      companionSilenceTimerRef.current = null;
    }

    if (wavRecorderRef.current) {
      try {
        wavRecorderRef.current.end();
      } catch {}
      wavRecorderRef.current = null;
    }

    if (wavStreamPlayerRef.current) {
      try {
        wavStreamPlayerRef.current.interrupt();
      } catch {}
      wavStreamPlayerRef.current = null;
    }
  }, []);

  return {
    initializeAudio,
    startRecording,
    stopRecording,
    playAudio,
    cleanup,
    isInitialized,
    isRecording,
    userAmplitude,
    companionAmplitude,
    isCompanionSpeaking,
  };
}
