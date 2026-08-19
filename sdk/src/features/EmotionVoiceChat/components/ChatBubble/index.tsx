import styles from './styles.module.scss';

import Icon from "@/components/ui/icon.tsx";

export type ChatBubbleState = 'idle' | 'connecting' | 'connected' | 'disconnected' | 'error';

export interface ChatBubbleProps {
    state: ChatBubbleState;
    onConnect(): void;
    onDisconnect(): void;
    onRestart(): void;
}

export function ChatBubble({ state, onConnect, onDisconnect, onRestart }: ChatBubbleProps) {
    const handleInteractorClick = () => {
        if (state === 'idle') {
            onConnect();
        } else if (state === "connected") {
            onDisconnect();
        } else if (state === 'disconnected') {
            onConnect();
        }
    }

    return (
        <div className={ styles.bubble }>
            <button type="button" className={styles.bubble__interactor} onClick={() => handleInteractorClick()}>
                { state }
            </button>

            <button type="button" className={styles.bubble__restart} onClick={() => onRestart()}>
                <Icon name="restart" size={22} color="white" className="animate-spin" />
            </button>
        </div>
    )
}
