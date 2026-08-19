/**
 * Environment-aware debug logging utility.
 * Automatically suppresses debug logs in production while preserving error logs.
 */

declare const process: { env: Record<string, string | undefined> };

export interface Logger {
  info: (message: string, data?: unknown) => void;
  warn: (message: string, data?: unknown) => void;
  error: (message: string, error?: unknown) => void;
  debug: (message: string, data?: unknown) => void;
}

/**
 * Creates a namespaced logger with environment-aware logging
 * @param namespace - The namespace/component identifier for logs
 * @returns Logger instance with info, warn, error, and debug methods
 */
export const createLogger = (namespace: string): Logger => {
  const isDevelopment = process.env.NODE_ENV === 'development';

  return {
    info: (message: string, data?: unknown) => {
      if (isDevelopment) {
        if (data !== undefined) {
          console.log(`[${namespace}] ${message}`, data);
        } else {
          console.log(`[${namespace}] ${message}`);
        }
      }
    },

    warn: (message: string, data?: unknown) => {
      if (isDevelopment) {
        if (data !== undefined) {
          console.warn(`[${namespace}] ${message}`, data);
        } else {
          console.warn(`[${namespace}] ${message}`);
        }
      }
    },

    error: (message: string, error?: unknown) => {
      // Always log errors, even in production
      if (error !== undefined) {
        console.error(`[${namespace}] ${message}`, error);
      } else {
        console.error(`[${namespace}] ${message}`);
      }
    },

    debug: (message: string, data?: unknown) => {
      // Only log debug messages in development
      if (isDevelopment) {
        if (data !== undefined) {
          console.debug(`[${namespace}] ${message}`, data);
        } else {
          console.debug(`[${namespace}] ${message}`);
        }
      }
    }
  };
};

const conversationSimulatorDebugEnabled = process.env.NEXT_PUBLIC_DEBUG_CONVERSATION_SIMULATOR === 'true';

export const debugFlags = {
  conversationSimulator: conversationSimulatorDebugEnabled,
};

export const isConversationSimulatorDebugEnabled = conversationSimulatorDebugEnabled;

// Pre-created loggers for common components
export const logger = {
  websocket: createLogger('WEBSOCKET_SESSION'),
  conversation: createLogger('CONVERSATION_SIMULATOR'),
  sessionConversation: createLogger('SESSION_CONVERSATION'),
  messages: createLogger('CONVERSATION_MESSAGES'),
  audio: createLogger('WEBSOCKET_AUDIO')
};
