/**
 * Helper Class for audio format conversion and resampling
 */
export class AudioProcessor {
    static convertInt16ToFloat32(int16: Int16Array): Float32Array {
        const float32 = new Float32Array(int16.length);
        for (let i = 0; i < int16.length; i++) {
            float32[i] = int16[i] / 0x8000;
        }
        return float32;
    }

    static convertFloat32ToInt16(float32: Float32Array): Int16Array {
        const int16 = new Int16Array(float32.length);
        for (let i = 0; i < float32.length; i++) {
            const s = Math.max(-1, Math.min(1, float32[i]));
            int16[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
        }
        return int16;
    }

    static resample(
        input: Float32Array,
        fromRate: number,
        toRate: number
    ): Float32Array {
        if (!input.length || fromRate === toRate) {
            return input;
        }

        const ratio = fromRate / toRate;
        const outLength = Math.max(1, Math.round(input.length / ratio));
        const output = new Float32Array(outLength);

        for (let i = 0; i < outLength; i++) {
            const t = i * ratio;
            const i0 = Math.floor(t);
            const i1 = Math.min(i0 + 1, input.length - 1);
            const w = t - i0;
            output[i] = (1 - w) * input[i0] + w * input[i1];
        }

        return output;
    }
}
