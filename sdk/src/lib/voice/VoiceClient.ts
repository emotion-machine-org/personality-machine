import { TypedEventTarget } from "typescript-event-target";

import { DEFAULT_SAMPLE_RATE } from "./types";
import type { VoiceClientStatus, VoiceClientConfig, VoiceClientChangeEventDetail } from "./types";

import { WebSocketManager } from "./WebSocketManager";
import { MicrophoneStreamManager } from "./MicrophoneStreamManager";
import { AudioPlaybackManager } from "./AudioPlaybackManager";

export class VoiceClientChangeEvent extends CustomEvent<VoiceClientChangeEventDetail> {
    static readonly TYPE = "voice:state_change";
    constructor(detail: VoiceClientChangeEventDetail) {
        super(VoiceClientChangeEvent.TYPE, { detail });
    }
}

export interface VoiceEventMap {
    "voice:state_change": VoiceClientChangeEvent;
}

export class VoiceClient extends TypedEventTarget<VoiceEventMap> {
    private audioCtx: AudioContext;
    private playback: AudioPlaybackManager;
    private mic: MicrophoneStreamManager;
    private socket: WebSocketManager;

    private _status: VoiceClientStatus = 'idle';
    private config: { sampleRate: number; bufferSize: number };

    constructor(config: VoiceClientConfig = {}) {
        super();

        this.config = {
            sampleRate: config.sampleRate ?? DEFAULT_SAMPLE_RATE,
            bufferSize: config.bufferSize ?? 4096
        };

        this.audioCtx = new AudioContext();

        this.socket = new WebSocketManager();
        this.mic = new MicrophoneStreamManager(this.audioCtx);
        this.playback = new AudioPlaybackManager(this.audioCtx, this.config.sampleRate);
    }

    get status(): VoiceClientStatus {
        return this._status;
    }

    private setStatus(status: VoiceClientStatus) {
        if (this._status === status) return;
        this._status = status;
        this.dispatchTypedEvent(VoiceClientChangeEvent.TYPE, new VoiceClientChangeEvent({ newState: status }));
    }

    connect(url: string) {
        if (this.status === 'connecting' || this.status === 'connected') return;

        this.setStatus('connecting');

        this.socket.connect(url, {
            onOpen: () => {
                this.setStatus('connected');
                this.playback.reset();
            },
            onMessage: (data) => {
                this.playback.enqueue(data);
            },
            onError: () => this.setStatus('error'),
            onClose: () => this.disconnect()
        });
    }

    async startMicrophone() {
        if (this.audioCtx.state === 'suspended') await this.audioCtx.resume();

        await this.mic.start({
            sampleRate: this.config.sampleRate,
            bufferSize: this.config.bufferSize,
            onData: (chunk) => this.socket.send(chunk)
        });
    }

    stopMicrophone() {
        this.mic.stop();
    }

    disconnect() {
        this.stopMicrophone();
        this.socket.disconnect();
        this.setStatus('closed');
    }

    async destroy() {
        this.disconnect();
        await this.audioCtx.close();
    }
}
