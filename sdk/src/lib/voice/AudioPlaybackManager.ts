import { AudioProcessor } from "./AudioProcessor";

export class AudioPlaybackManager {
    private playbackTime: number = 0;
    private readonly LEAD_BUFFER = 0.15; // 150ms
    private audioCtx: AudioContext;
    private targetSampleRate: number

    constructor(
        audioCtx: AudioContext,
        targetSampleRate: number
    ) {
        this.audioCtx = audioCtx;
        this.targetSampleRate = targetSampleRate;
        this.reset();
    }

    reset() {
        this.playbackTime = this.audioCtx.currentTime;
    }

    /**
     * Accepts raw ArrayBuffer (Int16) from server, converts, and queues it.
     */
    enqueue(rawBuffer: ArrayBuffer) {
        const int16 = new Int16Array(rawBuffer);
        if (!int16.length) return;

        // 1. Decode (Int16 -> Float32)
        const float32 = AudioProcessor.convertInt16ToFloat32(int16);

        // 2. Resample (Server Rate -> Browser Context Rate)
        const resampled = (this.targetSampleRate === this.audioCtx.sampleRate)
            ? float32
            : AudioProcessor.resample(float32, this.targetSampleRate, this.audioCtx.sampleRate);

        this.scheduleBuffer(resampled);
    }

    private scheduleBuffer(audioData: Float32Array) {
        const buffer = this.audioCtx.createBuffer(1, audioData.length, this.audioCtx.sampleRate);
        buffer.getChannelData(0).set(audioData);

        const source = this.audioCtx.createBufferSource();
        source.buffer = buffer;
        source.connect(this.audioCtx.destination);

        const now = this.audioCtx.currentTime;
        // Jitter buffer logic: If we fell behind, jump to now + safety buffer
        if (this.playbackTime < now) {
            this.playbackTime = now + this.LEAD_BUFFER;
        }

        source.start(this.playbackTime);
        this.playbackTime += buffer.duration;
    }
}
