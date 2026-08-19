import { useEffect, useRef } from 'react';

interface UseMicStreamOptions {
  sampleRate?: number;
  chunkSize?: number;
}

export function useMicStream(
  ws: WebSocket | null,
  isActive: boolean,
  options: UseMicStreamOptions = {}
) {
  const {
    sampleRate = 24000,
    chunkSize = 4800, // Increased for better quality (200ms chunks)
  } = options;

  const streamRef = useRef<MediaStream | null>(null);
  const contextRef = useRef<AudioContext | null>(null);
  const workletNodeRef = useRef<AudioWorkletNode | null>(null);

  useEffect(() => {
    if (!isActive || !ws || ws.readyState !== WebSocket.OPEN) {
      return;
    }

    let cleanup: (() => void) | null = null;

    const initAudioStream = async () => {
      try {
        console.log('[MIC] Starting microphone stream...');

        // Request microphone with optimal settings
        const stream = await navigator.mediaDevices.getUserMedia({
          audio: {
            sampleRate: sampleRate,
            channelCount: 1,
            echoCancellation: true,
            noiseSuppression: true,
            autoGainControl: true,
            // Add additional constraints for better quality
            //latency: 0.01, // 10ms latency
            //volume: 1.0,
          },
        });

        streamRef.current = stream;
        console.log('[MIC] Got media stream with tracks:', stream.getAudioTracks().map(t => ({
          label: t.label,
          enabled: t.enabled,
          settings: t.getSettings()
        })));

        // Create AudioContext with forced sample rate
        const audioContext = new AudioContext({
          sampleRate,
          latencyHint: 'interactive'
        });
        contextRef.current = audioContext;

        const source = audioContext.createMediaStreamSource(stream);
        console.log('[MIC] Created audio source, actual sample rate:', audioContext.sampleRate);

        await setupAudioWorklet(audioContext, source);

        cleanup = () => {
          console.log('[MIC] Cleaning up audio stream');
          if (workletNodeRef.current) {
            workletNodeRef.current.disconnect();
          }
          source.disconnect();
          stream.getTracks().forEach(track => {
            track.stop();
            console.log('[MIC] Stopped track:', track.label);
          });
          audioContext.close();
        };

      } catch (error) {
        console.error('[MIC] Error setting up audio stream:', error);
        if (error instanceof Error) {
          if (error.name === 'NotAllowedError') {
            console.error('[MIC] Microphone permission denied');
          } else if (error.name === 'NotFoundError') {
            console.error('[MIC] No microphone found');
          } else if (error.name === 'AbortError') {
            console.error('[MIC] Operation aborted');
          }
        }
      }
    };

    const setupAudioWorklet = async (context: AudioContext, source: MediaStreamAudioSourceNode) => {
      // Improved AudioWorklet processor
      const workletCode = `
        class MicrophoneProcessor extends AudioWorkletProcessor {
          constructor() {
            super();
            this.bufferSize = ${chunkSize};
            this.buffer = new Float32Array(this.bufferSize);
            this.bufferIndex = 0;
            this.frameCount = 0;

            // Add smoothing to reduce clicks
            this.lastSample = 0;
            this.dcOffset = 0;
            this.dcAlpha = 0.995;
          }

          process(inputs, outputs, parameters) {
            const input = inputs[0];
            if (!input || !input[0]) return true;

            const inputData = input[0];
            this.frameCount++;

            for (let i = 0; i < inputData.length; i++) {
              let sample = inputData[i];

              // Simple DC offset removal
              this.dcOffset = this.dcOffset * this.dcAlpha + sample * (1 - this.dcAlpha);
              sample = sample - this.dcOffset;

              // Add very gentle smoothing to prevent clicks
              sample = sample * 0.95 + this.lastSample * 0.05;
              this.lastSample = sample;

              this.buffer[this.bufferIndex] = sample;
              this.bufferIndex++;

              if (this.bufferIndex >= this.bufferSize) {
                // Convert to Int16 with improved quality
                const int16Array = new Int16Array(this.bufferSize);
                for (let j = 0; j < this.bufferSize; j++) {
                  // Apply soft limiting to prevent clipping
                  let val = this.buffer[j];
                  if (Math.abs(val) > 0.95) {
                    val = val > 0 ? 0.95 : -0.95;
                  }

                  // Convert to int16 with rounding
                  int16Array[j] = Math.round(val * 32767);
                }

                // Send with frame info for debugging
                this.port.postMessage({
                  audioData: int16Array.buffer,
                  frameNumber: this.frameCount,
                  bufferSize: this.bufferSize,
                  sampleRate: sampleRate
                });

                this.bufferIndex = 0;
              }
            }

            return true;
          }
        }
        registerProcessor('microphone-processor', MicrophoneProcessor);
      `;

      const blob = new Blob([workletCode], { type: 'application/javascript' });
      const workletUrl = URL.createObjectURL(blob);

      await context.audioWorklet.addModule(workletUrl);

      const workletNode = new AudioWorkletNode(context, 'microphone-processor');
      workletNodeRef.current = workletNode;

      let frameCount = 0;
      workletNode.port.onmessage = (event) => {
        if (ws.readyState === WebSocket.OPEN) {
          const { audioData, frameNumber, bufferSize } = event.data;
          frameCount++;

          try {
            ws.send(audioData);

            // Log every 50 frames to monitor stream
            if (frameCount % 50 === 0) {
              console.log(`[MIC] Sent frame ${frameNumber}, ${audioData.byteLength} bytes (${bufferSize} samples)`);
            }
          } catch (error) {
            console.error('[MIC] Error sending audio:', error);
          }
        }
      };

      source.connect(workletNode);

      URL.revokeObjectURL(workletUrl);
      console.log('[MIC] AudioWorklet setup complete with chunk size:', chunkSize);
    };

    initAudioStream();

    return () => {
      cleanup?.();
    };
  }, [isActive, ws, sampleRate, chunkSize]);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (streamRef.current) {
        streamRef.current.getTracks().forEach(track => track.stop());
      }
      if (contextRef.current) {
        contextRef.current.close();
      }
    };
  }, []);
}

export default useMicStream;
