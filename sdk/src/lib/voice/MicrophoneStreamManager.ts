import { AudioProcessor } from "./AudioProcessor";

export interface MicOptions {
    sampleRate: number;
    bufferSize: number;
    onData: (data: ArrayBuffer) => void;
}

export class MicrophoneStreamManager {
    private mediaStream: MediaStream | null = null;
    private sourceNode: MediaStreamAudioSourceNode | null = null;
    private processorNode: ScriptProcessorNode | null = null;
    private gainNode: GainNode | null = null;
    private audioCtx: AudioContext;

    constructor(audioCtx: AudioContext) {
        this.audioCtx = audioCtx;
    }

    async start(options: MicOptions) {
        if (this.mediaStream) return;

        // 1. Resume context if needed
        if (this.audioCtx.state === 'suspended') await this.audioCtx.resume();

        // 2. Request Mic (Ask browser for target sample rate to save CPU)
        this.mediaStream = await navigator.mediaDevices.getUserMedia({
            audio: {
                sampleRate: options.sampleRate,
                channelCount: 1,
                echoCancellation: true,
                noiseSuppression: true,
                autoGainControl: true,
            }
        });

        this.sourceNode = this.audioCtx.createMediaStreamSource(this.mediaStream);
        this.processorNode = this.audioCtx.createScriptProcessor(options.bufferSize, 1, 1);

        // 3. Prevent Feedback Loop: Mute the output to speakers
        this.gainNode = this.audioCtx.createGain();
        this.gainNode.gain.value = 0;

        this.processorNode.onaudioprocess = (e) => {
            const input = e.inputBuffer.getChannelData(0);

            // Resample only if browser didn't give us what we asked for
            const processed = (this.audioCtx.sampleRate === options.sampleRate)
                ? input
                : AudioProcessor.resample(input, this.audioCtx.sampleRate, options.sampleRate);

            const int16 = AudioProcessor.convertFloat32ToInt16(processed);
            options.onData(int16.buffer as ArrayBuffer);
        };

        // Graph: Source -> Processor -> Gain(0) -> Destination
        this.sourceNode.connect(this.processorNode);
        this.processorNode.connect(this.gainNode);
        this.gainNode.connect(this.audioCtx.destination);
    }

    stop() {
        this.sourceNode?.disconnect();
        this.processorNode?.disconnect();
        this.gainNode?.disconnect();
        this.mediaStream?.getTracks().forEach(t => t.stop());
        this.mediaStream = null;
    }

    isActive(): boolean {
        return !!this.mediaStream?.active;
    }
}
