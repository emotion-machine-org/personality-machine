import { useEffect, useRef, useState } from "react";

import { CompanionClient, CompanionClientChangeEvent, type CompanionClientStatus } from "@/lib/voice/CompanionClient.ts";
import { ChatBubble, type ChatBubbleState } from "@/features/EmotionVoiceChat/components/ChatBubble";

interface VoiceChatProps {
  apiKey: string;
  companionId: string;
}

export function VoiceChat({ apiKey, companionId }: VoiceChatProps) {
    const [clientStatus, setClientStatus] = useState<CompanionClientStatus>('init')
    const [conversationId, setConversationId] = useState<string | null>(null);
    const companionClientRef = useRef<CompanionClient>(new CompanionClient({
        apiKey
    }))

    useEffect(() => {
        const syncCompanionClientState = (e: CompanionClientChangeEvent) => {
            setClientStatus(e.detail.newState)
        }

        companionClientRef.current.addEventListener('companion:state_change', syncCompanionClientState)

        return () => {
            companionClientRef.current.removeEventListener('companion:state_change', syncCompanionClientState)
        }
    }, [])

    const stop = async () => {
        companionClientRef.current.disconnect();
    };

    const start = async () => {
        const companionClient = companionClientRef.current;

        if (!conversationId) {
            const newConversationId = await companionClient.startConversation(companionId, {})
            setConversationId(newConversationId);
        } else {
            await companionClient.joinConversation(companionId, conversationId)
        }
    };

    const restart = async () => {
        const companionClient = companionClientRef.current;

        if (clientStatus === 'connected') {
            companionClient.disconnect();
        }

        const newConversationId = await companionClient.startConversation(companionId, {});
        setConversationId(newConversationId);
    }

    const getbubbleStatusFromClientStatus = (status: CompanionClientStatus) => {
        const statusMap: Record<CompanionClientStatus, ChatBubbleState> = {
            init: 'idle',
            ready: 'idle',
            connecting: 'connecting',
            connected: 'connected',
            closed: 'disconnected',
            error: 'error',
        }

        return statusMap[status]
    }

    return (
        <>
            <ChatBubble
                state={getbubbleStatusFromClientStatus(clientStatus)}
                onConnect={start}
                onDisconnect={stop}
                onRestart={restart}
            />
        </>
    );
}
