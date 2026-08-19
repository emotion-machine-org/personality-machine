export const DEFAULT_SAMPLE_RATE = 24000;

export type VoiceClientStatus = 'idle' | 'connecting' | 'connected' | 'closed' | 'error';

export interface VoiceClientConfig {
    sampleRate?: number;
    bufferSize?: number; // Default 4096
}

export interface VoiceClientChangeEventDetail {
    newState: VoiceClientStatus;
}
