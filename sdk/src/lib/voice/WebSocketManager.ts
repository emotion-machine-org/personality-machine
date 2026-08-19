export class WebSocketManager {
    private socket: WebSocket | null = null;
    private onMessageCallback: ((data: ArrayBuffer) => void) | null = null;
    private onOpenCallback: (() => void) | null = null;
    private onErrorCallback: (() => void) | null = null;
    private onCloseCallback: (() => void) | null = null;

    connect(url: string, callbacks: {
        onOpen: () => void;
        onMessage: (data: ArrayBuffer) => void;
        onError: () => void;
        onClose: () => void;
    }) {
        if (this.socket) {
            console.warn('[WebSocketManager] closing existing connection');
            this.disconnect();
        }

        this.onOpenCallback = callbacks.onOpen;
        this.onMessageCallback = callbacks.onMessage;
        this.onErrorCallback = callbacks.onError;
        this.onCloseCallback = callbacks.onClose;

        const ws = new WebSocket(url);
        ws.binaryType = 'arraybuffer';
        this.socket = ws;

        ws.onopen = () => this.onOpenCallback?.();

        ws.onmessage = (event) => {
            if (event.data instanceof ArrayBuffer) {
                this.onMessageCallback?.(event.data);
            } else {
                console.log('[WebSocketManager] non-binary message', event.data);
            }
        };

        ws.onerror = (ev) => {
            console.error('[WebSocketManager] error', ev);
            this.onErrorCallback?.();
        };

        ws.onclose = () => {
            this.socket = null;
            this.onCloseCallback?.();
        };
    }

    send(data: ArrayBuffer) {
        if (this.socket?.readyState === WebSocket.OPEN) {
            this.socket.send(data);
        }
    }

    disconnect(code?: number, reason?: string) {
        if (this.socket) {
            this.socket.close(code, reason);
            this.socket = null;
        }
    }

    isConnected(): boolean {
        return this.socket?.readyState === WebSocket.OPEN;
    }
}
