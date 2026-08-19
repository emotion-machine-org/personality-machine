/**
 * Standardized error handling system for consistent user experience.
 * Provides user-friendly error messages and centralized error processing.
 */

import { createLogger } from './debug';

const logger = createLogger('ERROR_HANDLER');

export interface AppError {
  code: string;
  message: string;
  userMessage: string;
  originalError?: unknown;
}

export interface ErrorHandler {
  handle: (error: unknown, fallbackMessage: string, context?: string) => AppError;
  showToUser: (error: AppError) => void;
  createError: (code: string, message: string, userMessage: string) => AppError;
}

/**
 * Common error codes and their user-friendly messages
 */
export const ERROR_CODES = {
  NETWORK_ERROR: 'NETWORK_ERROR',
  API_ERROR: 'API_ERROR',
  VALIDATION_ERROR: 'VALIDATION_ERROR',
  WEBSOCKET_ERROR: 'WEBSOCKET_ERROR',
  AUDIO_ERROR: 'AUDIO_ERROR',
  SESSION_ERROR: 'SESSION_ERROR',
  CONVERSATION_ERROR: 'CONVERSATION_ERROR',
  NO_COMPANION_SELECTED: 'NO_COMPANION_SELECTED',
  UNKNOWN_ERROR: 'UNKNOWN_ERROR'
} as const;

const USER_MESSAGES = {
  [ERROR_CODES.NETWORK_ERROR]: 'Unable to connect to the server. Please check your internet connection and try again.',
  [ERROR_CODES.API_ERROR]: 'There was a problem with the server. Please try again in a moment.',
  [ERROR_CODES.VALIDATION_ERROR]: 'Please check your input and try again.',
  [ERROR_CODES.WEBSOCKET_ERROR]: 'Connection to the voice service was lost. Please try reconnecting.',
  [ERROR_CODES.AUDIO_ERROR]: 'There was a problem with the audio system. Please check your microphone permissions.',
  [ERROR_CODES.SESSION_ERROR]: 'There was a problem with your session. Please try restarting.',
  [ERROR_CODES.CONVERSATION_ERROR]: 'There was a problem loading the conversation. Please try again.',
  [ERROR_CODES.NO_COMPANION_SELECTED]: 'Please select a companion before starting a session.',
  [ERROR_CODES.UNKNOWN_ERROR]: 'An unexpected error occurred. Please try again.'
} as const;

/**
 * Creates a standardized error handler for a specific context
 * @param context - The context/component where errors are handled
 * @returns ErrorHandler instance
 */
export const createErrorHandler = (context: string): ErrorHandler => {
  return {
    handle: (error: unknown, fallbackMessage: string, errorContext?: string): AppError => {
      const fullContext = errorContext ? `${context}:${errorContext}` : context;

      // Log the original error for debugging
      logger.error(`Error in ${fullContext}:`, error);

      // Determine error code and user message
      let code: string = ERROR_CODES.UNKNOWN_ERROR;
      let message = fallbackMessage;
      let userMessage: string = USER_MESSAGES[ERROR_CODES.UNKNOWN_ERROR];

      if (error instanceof Error) {
        message = error.message;

        // Categorize error based on message content
        if (error.message.includes('fetch') || error.message.includes('network')) {
          code = ERROR_CODES.NETWORK_ERROR;
          userMessage = USER_MESSAGES[ERROR_CODES.NETWORK_ERROR];
        } else if (error.message.includes('WebSocket') || error.message.includes('connection')) {
          code = ERROR_CODES.WEBSOCKET_ERROR;
          userMessage = USER_MESSAGES[ERROR_CODES.WEBSOCKET_ERROR];
        } else if (error.message.includes('audio') || error.message.includes('microphone')) {
          code = ERROR_CODES.AUDIO_ERROR;
          userMessage = USER_MESSAGES[ERROR_CODES.AUDIO_ERROR];
        } else if (error.message.includes('companion')) {
          code = ERROR_CODES.NO_COMPANION_SELECTED;
          userMessage = USER_MESSAGES[ERROR_CODES.NO_COMPANION_SELECTED];
        } else if (error.message.includes('session')) {
          code = ERROR_CODES.SESSION_ERROR;
          userMessage = USER_MESSAGES[ERROR_CODES.SESSION_ERROR];
        } else if (error.message.includes('conversation')) {
          code = ERROR_CODES.CONVERSATION_ERROR;
          userMessage = USER_MESSAGES[ERROR_CODES.CONVERSATION_ERROR];
        }
      } else if (typeof error === 'string') {
        message = error;
      }

      return {
        code,
        message,
        userMessage,
        originalError: error
      };
    },

    showToUser: (error: AppError) => {
      // For now, use alert as fallback but this should be replaced with toast notifications
      // TODO: Replace with proper toast notification system
      alert(error.userMessage);

      // Log for debugging
      logger.error(`User notified of error: ${error.code} - ${error.userMessage}`);
    },

    createError: (code: string, message: string, userMessage: string): AppError => {
      return {
        code,
        message,
        userMessage
      };
    }
  };
};

// Pre-created error handlers for common contexts
export const errorHandler = {
  websocket: createErrorHandler('WebSocketSession'),
  conversation: createErrorHandler('ConversationSimulator'),
  sessionConversation: createErrorHandler('SessionConversation'),
  messages: createErrorHandler('ConversationMessages'),
  audio: createErrorHandler('WebSocketAudio')
};
