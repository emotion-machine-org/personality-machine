import { TypedEventTarget } from "typescript-event-target";

import { VoiceClient } from "@/lib/voice/VoiceClient.ts";
import { AuthenticationError } from "@/lib/voice/errors.ts";

export interface CompanionClientOptions {
    apiKey: string;
    baseUrl?: string; // e.g., "https://api.emotionmachine.ai"
    sampleRate?: number;
}

export interface ConversationOptions {
    companionId: string;
    conversationId?: string;
    voiceConfig?: {
        pipeline_type: string;
        voice_name: string;
        temperature: number;
        realtimeModel: string;
    };
}

export type CompanionClientStatus = 'init' | 'ready' | 'connecting' | 'connected' | 'closed' | 'error';

export interface CompanionEventMap {
    "companion:state_change": CustomEvent<{ newState: CompanionClientStatus }>;
    "error": CustomEvent<{ error: Error }>;
}

interface SessionResponse {
    id: string;
    conversation_id: string;
    ws_url: string;
}

export interface CompanionClientChangeEventDetail {
    newState: CompanionClientStatus;
}

export class CompanionClientChangeEvent extends CustomEvent<CompanionClientChangeEventDetail> {
    static readonly TYPE = "companion:state_change";
    constructor(detail: CompanionClientChangeEventDetail) {
        super(CompanionClientChangeEvent.TYPE, { detail });
    }
}

const DEFAULT_BASE_URL = "https://api.emotionmachine.ai";

export class CompanionClient extends TypedEventTarget<CompanionEventMap> {
    private voiceClient: VoiceClient;
    private baseUrl: string;
    _status: CompanionClientStatus = 'init';
    apiKey: string;

    constructor(options: CompanionClientOptions) {
        super();
        this.apiKey = options.apiKey;
        this.baseUrl = options.baseUrl?.replace(/\/$/, '') ?? DEFAULT_BASE_URL;
        this.voiceClient = new VoiceClient();
        this.setupEventBridge();
        this.authenticate();
    }

    private setupEventBridge() {
        this.voiceClient.addEventListener("voice:state_change", (e) => {
            const voiceState = e.detail.newState;

            switch (voiceState) {
                case 'connecting':
                    this.updateStatus('connecting')
                    break;

                case 'connected':
                    this.updateStatus('connected');
                    break;

                case 'closed':
                    this.updateStatus('closed');
                    break;

                case 'error':
                    // TODO handle errors
                    break;
            }
        });

    }

    async authenticate() {
        const companionsResp = await fetch(`${this.baseUrl}/v1/companions`, {
            headers: new Headers({
                'Authorization': `Bearer ${this.apiKey}`
            }),
        })

        if (!companionsResp.ok && companionsResp.status === 401) {
            this.updateStatus('error');
            throw new AuthenticationError();
        }
    }

    async fetchConversation(companionId: string, conversationOptions?: Omit<Partial<ConversationOptions>, 'companionId'>): Promise<SessionResponse> {
        const requestBody: Record<string, unknown> = {
            companionId: companionId,
            voiceConfig: conversationOptions?.voiceConfig ?? {
                pipeline_type: "openai-realtime",
                voice_name: "alloy",
                temperature: 0.7,
                realtimeModel: "gpt-realtime-mini-2025-10-06"
            },
        };

        if (conversationOptions?.conversationId) {
            requestBody.conversationId = conversationOptions.conversationId;
        }
        const resp = await fetch(`${this.baseUrl}/v1/sessions`, {
            method: 'POST',
            headers: new Headers({
                'Authorization': `Bearer ${this.apiKey}`,
                'Content-Type': 'application/json',
                'Accept': 'application/json',
            }),
            body: JSON.stringify(requestBody),
        })

        // TODO improve http handling
        if (!resp.ok) {
            throw new Error('ResponseError')
        }

        return await resp.json() as SessionResponse;
    }

    async startConversation(companionId: string, conversationOptions: Omit<Partial<ConversationOptions>, 'companionId'>) {
        const session = await this.fetchConversation(companionId, conversationOptions);

        this.voiceClient.connect(session.ws_url)
        this.voiceClient.startMicrophone()

        return session.conversation_id;
    }

    async joinConversation(companionId: string, conversationId: string, conversationOptions?: Omit<Partial<ConversationOptions>, 'companionId' | 'conversationId'>) {
        const session = await this.fetchConversation(companionId, {
            ...conversationOptions,
            conversationId
        });

        this.voiceClient.connect(session.ws_url);
        this.voiceClient.startMicrophone();
    }

    private updateStatus(newState: CompanionClientStatus) {
        if (this._status === newState) return;
        this._status = newState;
        this.dispatchTypedEvent("companion:state_change", new CompanionClientChangeEvent({newState }));
    }

    async disconnect() {
        this.voiceClient.disconnect();
    }
}
