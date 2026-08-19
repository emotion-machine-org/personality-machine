// client/hooks/useOpenAIAudio.tsx
// Replace your useAudioPlayer and useMicStream with this

import { useRef, useCallback } from 'react';
import { WavRecorder, WavStreamPlayer } from 'wavtools';

interface UseOpenAIAudioReturn {
  initializeAudio: () => Promise<void>;
  startRecording: (onAudioData: (data: Int16Array) => void) => Promise<void>;
  stopRecording: () => void;
  playAudio: (audioData: Int16Array) => void;
  cleanup: () => void;
  isInitialized: boolean;
}

export function useOpenAIAudio(): UseOpenAIAudioReturn {
  const wavRecorderRef = useRef<WavRecorder | null>(null);
  const wavStreamPlayerRef = useRef<WavStreamPlayer | null>(null);
  const isInitializedRef = useRef(false);
  const isRecordingRef = useRef(false);

  const initializeAudio = useCallback(async () => {
    try {
      console.log('[AUDIO] Initializing OpenAI audio tools...');

      // Initialize with exact OpenAI specs
      const wavRecorder = new WavRecorder({
        sampleRate: 24000  // Exactly 24kHz as required by OpenAI
      });

      const wavStreamPlayer = new WavStreamPlayer({
        sampleRate: 24000  // Exactly 24kHz as required by OpenAI
      });

      wavRecorderRef.current = wavRecorder;
      wavStreamPlayerRef.current = wavStreamPlayer;

      // Setup audio permissions and connections
      await wavRecorder.begin();
      await wavStreamPlayer.connect();

      isInitializedRef.current = true;
      console.log('[AUDIO] OpenAI audio tools initialized successfully');

    } catch (error) {
      console.error('[AUDIO] Failed to initialize audio tools:', error);
      throw error;
    }
  }, []);

  const startRecording = useCallback(
    async (onAudioData: (data: Int16Array) => void) => {
      const wavRecorder = wavRecorderRef.current;
      if (!wavRecorder || !isInitializedRef.current) {
        throw new Error('Audio not initialized. Call initializeAudio() first.');
      }

      if (isRecordingRef.current) {
        console.warn('[AUDIO] Already recording');
        return;
      }

      try {
        console.log('[AUDIO] Starting recording...');
        isRecordingRef.current = true;

        // helper for Float32 → Int16 (same math as OpenAI helpers)
        const float32ToInt16 = (src: Float32Array) => {
          const dst = new Int16Array(src.length);
          for (let i = 0; i < src.length; i++) {
            const s = Math.max(-1, Math.min(1, src[i]));
            dst[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
          }
          return dst;
        };

        // Capture 8 192-sample frames (~340 ms @ 24 kHz)
        await wavRecorder.record((chunk: any) => {
          if (!isRecordingRef.current) return;

          let pcm: Int16Array | null = null;

          /* 1 . mono buffer (may be Int16Array *or* ArrayBuffer) */
          if (chunk?.mono) {
            pcm =
              chunk.mono instanceof Int16Array
                ? chunk.mono
                : new Int16Array(chunk.mono); // ArrayBuffer → Int16Array
          }

          /* 2 . raw fallback (single-channel mics) */
          else if (chunk?.raw) {
            pcm =
              chunk.raw instanceof Int16Array
                ? chunk.raw
                : new Int16Array(chunk.raw);
          }

          /* 3 . web worklet may emit Float32Array */
          else if (chunk instanceof Float32Array) {
            pcm = float32ToInt16(chunk);
          }

          if (pcm && pcm.length) {
            //if (__DEV__) console.log('[DEBUG] onAudioData got →', pcm.length);
            onAudioData(pcm);
          }
        }, 8192);

        console.log('[AUDIO] Recording started successfully');
      } catch (error) {
        console.error('[AUDIO] Failed to start recording:', error);
        isRecordingRef.current = false;
        throw error;
      }
    },
    []
  );

  const stopRecording = useCallback(() => {
    const wavRecorder = wavRecorderRef.current;
    if (!wavRecorder || !isRecordingRef.current) {
      return;
    }

    console.log('[AUDIO] Stopping recording...');
    isRecordingRef.current = false;

    try {
      wavRecorder.pause(); // Stop recording
      console.log('[AUDIO] Recording stopped');
    } catch (error) {
      console.error('[AUDIO] Error stopping recording:', error);
    }
  }, []);

  const playAudio = useCallback((audioData: Int16Array) => {
    const wavStreamPlayer = wavStreamPlayerRef.current;
    if (!wavStreamPlayer || !isInitializedRef.current) {
      console.warn('[AUDIO] Player not initialized');
      return;
    }

    try {
      // This is OpenAI's exact method - simple and proven
      wavStreamPlayer.add16BitPCM(audioData, `audio-${Date.now()}`);
    } catch (error) {
      console.error('[AUDIO] Error playing audio:', error);
    }
  }, []);

  const cleanup = useCallback(() => {
    console.log('[AUDIO] Cleaning up audio tools...');

    isRecordingRef.current = false;
    isInitializedRef.current = false;

    if (wavRecorderRef.current) {
      try {
        wavRecorderRef.current.end();
      } catch (error) {
        console.error('[AUDIO] Error cleaning up recorder:', error);
      }
      wavRecorderRef.current = null;
    }

    if (wavStreamPlayerRef.current) {
      try {
        wavStreamPlayerRef.current.interrupt();
      } catch (error) {
        console.error('[AUDIO] Error cleaning up player:', error);
      }
      wavStreamPlayerRef.current = null;
    }

    console.log('[AUDIO] Audio cleanup complete');
  }, []);

  return {
    initializeAudio,
    startRecording,
    stopRecording,
    playAudio,
    cleanup,
    isInitialized: isInitializedRef.current
  };
}
