export class EmotionError extends Error {
    constructor(message?: string) {
        super(message);
    }
}

export class AuthenticationError extends EmotionError {
    constructor(message?: string) {
        super(message);
    }
}
