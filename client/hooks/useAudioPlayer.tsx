import { useRef, useEffect } from 'react';
import { Platform } from 'react-native';

export function useAudioPlayer() {
  const audioContextRef = useRef<AudioContext | null>(null);
  const gainNodeRef = useRef<GainNode | null>(null);
  const audioQueueRef = useRef<AudioBuffer[]>([]);
  const isPlayingRef = useRef(false);
  const nextStartTimeRef = useRef<number>(0);
  const expectedSampleRate = 24000;

  useEffect(() => {
    if (Platform.OS === 'web') {
      // Force specific sample rate
      const audioContext = new AudioContext({
        sampleRate: expectedSampleRate,
        latencyHint: 'interactive' // Optimize for low latency
      });

      const gainNode = audioContext.createGain();
      gainNode.connect(audioContext.destination);
      gainNode.gain.value = 0.8; // Slightly reduce volume to prevent clipping

      audioContextRef.current = audioContext;
      gainNodeRef.current = gainNode;
      nextStartTimeRef.current = 0;

      console.log('[AUDIO] Audio player initialized with sample rate:', audioContext.sampleRate);
      console.log('[AUDIO] Audio context state:', audioContext.state);
    }

    return () => {
      audioContextRef.current?.close();
    };
  }, []);

  const playAudio = async (audioData: ArrayBuffer) => {
    if (!audioContextRef.current || !gainNodeRef.current) {
      console.warn('[AUDIO] Audio context not initialized');
      return;
    }

    try {
      // Resume audio context if suspended
      if (audioContextRef.current.state === 'suspended') {
        await audioContextRef.current.resume();
        console.log('[AUDIO] Audio context resumed');
      }

      // Validate audio data
      if (audioData.byteLength === 0) {
        console.warn('[AUDIO] Received empty audio buffer');
        return;
      }

      const pcm16Array = new Int16Array(audioData);
      console.log(`[AUDIO] Processing ${pcm16Array.length} samples (${(pcm16Array.length / expectedSampleRate * 1000).toFixed(1)}ms)`);

      // Create AudioBuffer
      const audioBuffer = audioContextRef.current.createBuffer(
        1, // mono
        pcm16Array.length,
        expectedSampleRate
      );

      const channelData = audioBuffer.getChannelData(0);

      // Improved Int16 to Float32 conversion with dithering
      for (let i = 0; i < pcm16Array.length; i++) {
        // Convert with proper scaling and add minimal dithering
        let sample = pcm16Array[i] / 32768.0;

        // Add very small dither to reduce quantization noise
        sample += (Math.random() - 0.5) * (1.0 / 32768.0);

        // Clamp to valid range
        channelData[i] = Math.max(-1, Math.min(1, sample));
      }

      // Remove the problematic high-pass filter - it was causing artifacts
      // Instead, apply gentle smoothing to reduce clicks
      if (channelData.length > 2) {
        // Simple smoothing filter to reduce clicks at boundaries
        const smoothSamples = 64; // Smooth first and last 64 samples

        // Smooth start
        for (let i = 1; i < Math.min(smoothSamples, channelData.length); i++) {
          const factor = i / smoothSamples;
          channelData[i] = channelData[i] * factor + channelData[i-1] * (1 - factor) * 0.1;
        }

        // Smooth end
        for (let i = channelData.length - smoothSamples; i < channelData.length - 1; i++) {
          if (i >= 0) {
            const factor = (channelData.length - i) / smoothSamples;
            channelData[i] = channelData[i] * factor + channelData[i+1] * (1 - factor) * 0.1;
          }
        }
      }

      // Use scheduled playback for gapless audio
      scheduleAudioBuffer(audioBuffer);

    } catch (error) {
      console.error('[AUDIO] Error processing audio:', error);
    }
  };

  const scheduleAudioBuffer = (audioBuffer: AudioBuffer) => {
    if (!audioContextRef.current || !gainNodeRef.current) return;

    try {
      const source = audioContextRef.current.createBufferSource();
      source.buffer = audioBuffer;
      source.connect(gainNodeRef.current);

      const currentTime = audioContextRef.current.currentTime;

      // Schedule audio to play immediately or after previous audio
      let startTime = Math.max(currentTime, nextStartTimeRef.current);

      // If we're too far behind, reset to current time to avoid building up latency
      if (startTime - currentTime > 0.5) {
        console.log('[AUDIO] Resetting timing to prevent excessive latency');
        startTime = currentTime;
        nextStartTimeRef.current = currentTime;
      }

      source.start(startTime);

      // Update next start time for gapless playback
      nextStartTimeRef.current = startTime + audioBuffer.duration;

      console.log(`[AUDIO] Scheduled audio: ${audioBuffer.duration.toFixed(3)}s at ${startTime.toFixed(3)}s (next: ${nextStartTimeRef.current.toFixed(3)}s)`);

      source.onended = () => {
        // Clean up timing if no more audio is queued
        const now = audioContextRef.current!.currentTime;
        if (nextStartTimeRef.current <= now) {
          nextStartTimeRef.current = 0;
          isPlayingRef.current = false;
        }
      };

      isPlayingRef.current = true;

    } catch (error) {
      console.error('[AUDIO] Error scheduling audio buffer:', error);
    }
  };

  const clearQueue = () => {
    audioQueueRef.current = [];
    nextStartTimeRef.current = 0;
    isPlayingRef.current = false;
    console.log('[AUDIO] Audio queue and timing cleared');
  };

  const getQueueLength = () => {
    return audioQueueRef.current.length;
  };

  return {
    playAudio,
    clearQueue,
    getQueueLength,
    isPlaying: isPlayingRef.current
  };
}
