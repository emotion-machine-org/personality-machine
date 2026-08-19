import {
  User,
  WebSocketTicket,
  Companion,
  CompanionConfig,
  CompanionVersionSummary,
  CompanionVersionDetail,
  SessionCreate,
  SessionCreated,
  VoiceMappings,
  MemoryItem,
  MemoryStats,
  MemoryListResponse,
  UserMemoriesPayload,
  UserAnalyticsSummary,
  CompanionShare,
  CompanionShareAnalytics,
  PublicShareMeta,
  ApiKey,
  ApiKeyWithSecret,
  ProjectSecret,
  RelationshipListResponse,
} from './types';
import { API_CONFIG } from './config';

// Re-export types for convenience
export type {
  User,
  WebSocketTicket,
  Companion,
  CompanionConfig,
  CompanionVersionSummary,
  CompanionVersionDetail,
  SessionCreate,
  SessionCreated,
  VoiceMappings,
  CompanionShare,
  CompanionShareAnalytics,
  PublicShareMeta,
  ApiKey,
  ApiKeyWithSecret,
  ProjectSecret,
};

/**
 * Custom error class that includes HTTP status code.
 * This allows retry logic and error handling to check the actual status code.
 */
export class ApiError extends Error {
  readonly status: number;
  readonly isAuthError: boolean;

  constructor(message: string, status: number) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.isAuthError = status === 401 || status === 403;
  }
}

/**
 * Type guard to check if an error is an ApiError
 */
export function isApiError(error: unknown): error is ApiError {
  return error instanceof ApiError;
}

// API client functions
async function makeRequest<T>(
  endpoint: string,
  options: RequestInit = {},
  token?: string | null
): Promise<T> {
  const url = `${API_CONFIG.BASE_URL}${endpoint}`;

  const headers: HeadersInit = {
    'Content-Type': 'application/json',
    ...(token && { 'Authorization': `Bearer ${token}` }),
    ...options.headers,
  };

  const response = await fetch(url, {
    ...options,
    headers,
  });

  if (!response.ok) {
    const errorText = await response.text();
    // Try to parse JSON error response and extract detail field
    let errorMessage = errorText;
    try {
      const errorData = JSON.parse(errorText);
      if (errorData.detail) {
        errorMessage = typeof errorData.detail === 'string'
          ? errorData.detail
          : JSON.stringify(errorData.detail);
      }
    } catch {
      // Not JSON, use raw text
    }
    throw new ApiError(errorMessage || `API Error ${response.status}`, response.status);
  }

  return response.json();
}

export const apiClient = {
  // User endpoints
  getCurrentUser: (token?: string | null) =>
    makeRequest<User>('/api/me', {}, token),

  completeOnboarding: (token?: string | null) =>
    makeRequest<{ message: string }>('/api/me/complete-onboarding', { method: 'POST' }, token),

  createCompanionFromOnboardingAnswers: (
    answers: {
      purpose: 'friend' | 'coach' | 'teacher' | 'custom';
      approach: 'playful' | 'supportive' | 'challenging';
      tone: 'casual' | 'direct' | 'formal';
      custom_purpose?: string;
      name?: string;
    },
    token?: string | null
  ) =>
    makeRequest<{ companion_id: string; companion_name: string; message: string }>(
      '/api/onboarding/create-companion-from-answers',
      {
        method: 'POST',
        body: JSON.stringify(answers),
      },
      token
    ),

  createWebSocketTicket: (token?: string | null) =>
    makeRequest<WebSocketTicket>('/api/websocket-ticket', { method: 'POST' }, token),

  // Companion endpoints
  getCompanions: (token?: string | null) =>
    makeRequest<Companion[]>('/api/companions', {}, token),

  getCompanion: (id: string, token?: string | null) =>
    makeRequest<CompanionConfig>(`/api/companions/${id}`, {}, token),

  getCompanionVersions: (id: string, token?: string | null) =>
    makeRequest<CompanionVersionSummary[]>(`/api/companions/${id}/versions`, {}, token),

  getCompanionVersionConfig: (companionId: string, versionId: string, token?: string | null) =>
    makeRequest<CompanionConfig>(`/api/companions/${companionId}/versions/${versionId}`, {}, token),

  updateCompanion: (id: string, config: Partial<CompanionConfig>, token?: string | null) =>
    // Server expects CompanionUpdate shape; wrap config under { config }
    makeRequest<CompanionConfig>(`/api/companions/${id}`, {
      method: 'PUT',
      body: JSON.stringify({ config }),
    }, token),

  // Update companion metadata like name/description without changing config
  updateCompanionMeta: (id: string, meta: { name?: string; description?: string }, token?: string | null) =>
    makeRequest<CompanionConfig>(`/api/companions/${id}`, {
      method: 'PUT',
      body: JSON.stringify(meta),
    }, token),


  createCompanion: (companionData: { name: string; description?: string; config?: Partial<CompanionConfig> }, token?: string | null) =>
    makeRequest<CompanionConfig>('/api/companions', {
      method: 'POST',
      body: JSON.stringify(companionData),
    }, token),

  createDefaultCompanion: (token?: string | null) =>
    makeRequest<CompanionConfig>('/api/users/me/default-companion', {
      method: 'POST',
    }, token),

  // Session endpoints
  createSession: (payload: SessionCreate, token?: string | null) =>
    makeRequest<SessionCreated>('/sessions/', {
      method: 'POST',
      body: JSON.stringify(payload),
    }, token),

  updateSession: (sessionId: string, payload: SessionCreate, token?: string | null) =>
    makeRequest<{ status: string }>(`/sessions/${sessionId}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }, token),

  // Voice mappings endpoint
  getVoiceMappings: (token?: string | null) =>
    makeRequest<VoiceMappings>('/api/voice-mappings', {}, token),

  // Companion sharing endpoints
  getCompanionShare: (companionId: string, token?: string | null) =>
    makeRequest<CompanionShare>(`/api/companions/${companionId}/share`, {}, token),

  updateCompanionShare: (
    companionId: string,
    body: Partial<{
      status: 'draft' | 'active' | 'disabled';
      allow_text: boolean;
      allow_voice: boolean;
      require_auth: boolean;
      expose_status_events: boolean;
      display_name?: string | null;
      description?: string | null;
      version_id?: string | null;
      config_snapshot?: Record<string, unknown> | null;
    }>,
    token?: string | null,
  ) =>
    makeRequest<CompanionShare>(`/api/companions/${companionId}/share`, {
      method: 'POST',
      body: JSON.stringify(body),
    }, token),

  disableCompanionShare: (companionId: string, token?: string | null) =>
    makeRequest<CompanionShare>(`/api/companions/${companionId}/share/disable`, { method: 'POST' }, token),

  getCompanionShareAnalytics: (companionId: string, token?: string | null) =>
    makeRequest<CompanionShareAnalytics>(`/api/companions/${companionId}/share/analytics`, {}, token),

  // Memory endpoints
  getMemories: (
    companionId: string,
    params?: { limit?: number; offset?: number; conversation_id?: string; order_by?: 'created_at' | 'last_accessed_at' | 'importance'; order_dir?: 'ASC' | 'DESC'; external_user_id?: string; is_core?: boolean },
    token?: string | null
  ) => {
    const query = new URLSearchParams();
    if (typeof params?.limit === 'number') query.set('limit', String(params.limit));
    if (typeof params?.offset === 'number') query.set('offset', String(params.offset));
    if (params?.conversation_id) query.set('conversation_id', params.conversation_id);
    if (params?.order_by) query.set('order_by', params.order_by);
    if (params?.order_dir) query.set('order_dir', params.order_dir);
    if (params?.external_user_id) query.set('external_user_id', params.external_user_id);
    if (typeof params?.is_core === 'boolean') query.set('is_core', String(params.is_core));
    const qs = query.toString();
    return makeRequest<MemoryListResponse>(`/api/companions/${companionId}/memories${qs ? `?${qs}` : ''}`, {}, token)
      .then((payload) => payload.items);
  },

  getUserMemoriesByExternalId: (
    companionId: string,
    externalUserId: string,
    params?: { limit?: number; offset?: number; order_by?: 'created_at' | 'last_accessed_at' | 'importance'; order_dir?: 'ASC' | 'DESC' },
    token?: string | null
  ): Promise<UserMemoriesPayload> => {
    const query = new URLSearchParams();
    if (typeof params?.limit === 'number') query.set('limit', String(params.limit));
    if (typeof params?.offset === 'number') query.set('offset', String(params.offset));
    if (params?.order_by) query.set('order_by', params.order_by);
    if (params?.order_dir) query.set('order_dir', params.order_dir);
    query.set('external_user_id', externalUserId);
    const qs = query.toString();
    return makeRequest<MemoryListResponse>(`/api/companions/${companionId}/memories${qs ? `?${qs}` : ''}`, {}, token)
      .then((payload) => ({ items: payload.items, total: payload.total_count }));
  },

  getUserAnalyticsSummary: (
    companionId: string,
    externalUserId: string,
    token?: string | null,
  ) => makeRequest<UserAnalyticsSummary>(
    `/api/analytics/companions/${companionId}/users/${encodeURIComponent(externalUserId)}/summary`,
    {},
    token,
  ),

  createMemory: (
    companionId: string,
    body: { content?: string; message_id?: string; importance?: number; weight_user?: number; modality?: string; commentary?: string; conversation_id?: string; sender_type?: string; external_user_id?: string; is_core?: boolean },
    token?: string | null
  ) => makeRequest<{ id: string }>(`/api/companions/${companionId}/memories`, { method: 'POST', body: JSON.stringify(body) }, token),

  updateMemory: (
    memoryId: string,
    body: { importance?: number; commentary?: string; content?: string },
    token?: string | null
  ) => makeRequest<{ ok: true }>(`/api/memories/${memoryId}`, { method: 'PUT', body: JSON.stringify(body) }, token),

  deleteMemory: (memoryId: string, token?: string | null) =>
    makeRequest<{ ok: true }>(`/api/memories/${memoryId}`, { method: 'DELETE' }, token),

  searchMemories: (
    companionId: string,
    body: { query: string; top_k?: number; min_saliency?: number; conversation_id?: string; external_user_id?: string; sender_type?: string; modality?: string },
    token?: string | null
  ) => makeRequest<{ items: MemoryItem[] }>(`/api/companions/${companionId}/memories/search`, { method: 'POST', body: JSON.stringify(body) }, token),

  getMemoryStats: (companionId: string, token?: string | null) =>
    makeRequest<MemoryStats>(`/api/companions/${companionId}/memories/stats`, {}, token),

  // API Keys endpoints
  getApiKeys: (token?: string | null) =>
    makeRequest<ApiKey[]>('/api/projects/default/keys', {}, token),

  createApiKey: (data: { name?: string }, token?: string | null) =>
    makeRequest<ApiKeyWithSecret>('/api/projects/default/keys', {
      method: 'POST',
      body: JSON.stringify(data),
    }, token),

  revokeApiKey: (keyId: string, token?: string | null) =>
    makeRequest<{ message: string }>(`/api/projects/default/keys/${keyId}`, {
      method: 'DELETE',
    }, token),

  // Project Secrets endpoints
  getProjectSecrets: (token?: string | null) =>
    makeRequest<ProjectSecret[]>('/api/projects/default/secrets', {}, token),

  createProjectSecret: (
    data: { name: string; value: string; description?: string },
    token?: string | null
  ) =>
    makeRequest<ProjectSecret>('/api/projects/default/secrets', {
      method: 'POST',
      body: JSON.stringify(data),
    }, token),

  updateProjectSecret: (
    secretName: string,
    data: { value: string; description?: string },
    token?: string | null
  ) =>
    makeRequest<ProjectSecret>(`/api/projects/default/secrets/${secretName}`, {
      method: 'PUT',
      body: JSON.stringify(data),
    }, token),

  deleteProjectSecret: (secretName: string, token?: string | null) =>
    makeRequest<{ message: string }>(`/api/projects/default/secrets/${secretName}`, {
      method: 'DELETE',
    }, token),

  // Test Users (Relationships) endpoints - for dashboard simulator
  listTestUsers: (companionId: string, token?: string | null) =>
    makeRequest<TestUserSummary[]>(`/api/companions/${companionId}/test-users`, {}, token),

  ensureTestUser: (companionId: string, userId: string, token?: string | null) =>
    makeRequest<TestUserDetail>(`/api/companions/${companionId}/test-users/${encodeURIComponent(userId)}`, {
      method: 'PUT',
    }, token),

  getTestUser: (companionId: string, userId: string, token?: string | null) =>
    makeRequest<TestUserDetail>(`/api/companions/${companionId}/test-users/${encodeURIComponent(userId)}`, {}, token),

  patchTestUserConfig: (
    companionId: string,
    userId: string,
    config: Record<string, unknown>,
    token?: string | null
  ) =>
    makeRequest<{ config: Record<string, unknown>; version: number }>(
      `/api/companions/${companionId}/test-users/${encodeURIComponent(userId)}/config`,
      {
        method: 'PATCH',
        body: JSON.stringify(config),
      },
      token
    ),

  deleteTestUser: (companionId: string, userId: string, token?: string | null) =>
    makeRequest<{ message: string }>(`/api/companions/${companionId}/test-users/${encodeURIComponent(userId)}`, {
      method: 'DELETE',
    }, token),

  resetTestUserConversation: (companionId: string, userId: string, token?: string | null) =>
    makeRequest<{ message: string }>(
      `/api/companions/${companionId}/test-users/${encodeURIComponent(userId)}/reset-conversation`,
      {
        method: 'POST',
      },
      token
    ),

  resetTestUserProfile: (companionId: string, userId: string, token?: string | null) =>
    makeRequest<{ message: string }>(`/api/companions/${companionId}/test-users/${encodeURIComponent(userId)}/reset-profile`, {
      method: 'POST',
    }, token),

  // Dashboard WebSocket tokens (uses Clerk auth)
  getTextWsToken: (companionId: string, userId: string, token?: string | null) =>
    makeRequest<DashboardWsTokenResponse>(`/api/companions/${companionId}/test-users/${encodeURIComponent(userId)}/ws-token`, {
      method: 'POST',
    }, token),

  getVoiceWsToken: (companionId: string, userId: string, voiceConfig?: Record<string, unknown>, token?: string | null) =>
    makeRequest<DashboardVoiceTokenResponse>(`/api/companions/${companionId}/test-users/${encodeURIComponent(userId)}/voice-token`, {
      method: 'POST',
      body: JSON.stringify(voiceConfig ? { voice_config: voiceConfig } : {}),
    }, token),

  // Get message history for a test user
  getTestUserMessages: (companionId: string, userId: string, limit?: number, token?: string | null) =>
    makeRequest<TestUserMessage[]>(`/api/companions/${companionId}/test-users/${encodeURIComponent(userId)}/messages${limit ? `?limit=${limit}` : ''}`, {}, token),

  // Relationships (dashboard - uses Clerk auth)
  listRelationships: (companionId: string, params?: { limit?: number; offset?: number; search?: string }, token?: string | null) => {
    const query = new URLSearchParams();
    if (params?.limit) query.set('limit', String(params.limit));
    if (params?.offset) query.set('offset', String(params.offset));
    if (params?.search) query.set('search', params.search);
    const qs = query.toString();
    return makeRequest<RelationshipListResponse>(`/api/companions/${companionId}/relationships${qs ? `?${qs}` : ''}`, {}, token);
  },

  deleteRelationship: (companionId: string, relationshipId: string, token?: string | null) =>
    makeRequest<void>(`/api/companions/${companionId}/relationships/${relationshipId}`, {
      method: 'DELETE',
    }, token),

  // Twilio dial-out (dashboard - uses Clerk auth)
  twilioDialOut: (companionId: string, userId: string, toNumber: string, token?: string | null) =>
    makeRequest<TwilioDialOutResponse>(`/api/companions/${companionId}/test-users/${encodeURIComponent(userId)}/twilio-dial-out`, {
      method: 'POST',
      body: JSON.stringify({
        to_number: toNumber,
      }),
    }, token),

  // Twilio call transcript (dashboard - uses Clerk auth)
  getTwilioCallTranscript: (
    companionId: string,
    userId: string,
    callSid: string,
    limit = 500,
    token?: string | null
  ) =>
    makeRequest<TwilioCallTranscriptMessage[]>(
      `/api/companions/${companionId}/test-users/${encodeURIComponent(userId)}/twilio-calls/${encodeURIComponent(callSid)}/messages?limit=${limit}`,
      {},
      token
    ),

  // DialogMachine: hot context
  getDialogmachineHotContext: (
    companionId: string,
    userId: string,
    token?: string | null
  ) =>
    makeRequest<DialogmachineHotContextResponse>(
      `/api/dialogmachine/companions/${companionId}/test-users/${encodeURIComponent(userId)}/hot-context`,
      {},
      token
    ),

  updateDialogmachineHotContext: (
    companionId: string,
    userId: string,
    content: string,
    token?: string | null
  ) =>
    makeRequest<DialogmachineHotContextResponse>(
      `/api/dialogmachine/companions/${companionId}/test-users/${encodeURIComponent(userId)}/hot-context`,
      {
        method: 'PUT',
        body: JSON.stringify({ content }),
      },
      token
    ),

  // DialogMachine: prompt override
  getDialogmachinePromptOverride: (
    companionId: string,
    userId: string,
    token?: string | null
  ) =>
    makeRequest<DialogmachinePromptOverrideResponse>(
      `/api/dialogmachine/companions/${companionId}/test-users/${encodeURIComponent(userId)}/prompt-override`,
      {},
      token
    ),

  updateDialogmachinePromptOverride: (
    companionId: string,
    userId: string,
    promptOverride: string | null,
    token?: string | null
  ) =>
    makeRequest<DialogmachinePromptOverrideResponse>(
      `/api/dialogmachine/companions/${companionId}/test-users/${encodeURIComponent(userId)}/prompt-override`,
      {
        method: 'PUT',
        body: JSON.stringify({ prompt_override: promptOverride }),
      },
      token
    ),

  // DialogMachine: per-relationship guardrails
  getDialogmachineGuardrails: (
    companionId: string,
    userId: string,
    token?: string | null
  ) =>
    makeRequest<DialogmachineGuardrailsResponse>(
      `/api/dialogmachine/companions/${companionId}/test-users/${encodeURIComponent(userId)}/guardrails`,
      {},
      token
    ),

  updateDialogmachineGuardrails: (
    companionId: string,
    userId: string,
    guardrails: string | null,
    token?: string | null
  ) =>
    makeRequest<DialogmachineGuardrailsResponse>(
      `/api/dialogmachine/companions/${companionId}/test-users/${encodeURIComponent(userId)}/guardrails`,
      {
        method: 'PUT',
        body: JSON.stringify({ guardrails }),
      },
      token
    ),

  // DialogMachine: simulate background noise settings
  getDialogmachineBackgroundNoise: (
    companionId: string,
    userId: string,
    token?: string | null
  ) =>
    makeRequest<DialogmachineBackgroundNoiseResponse>(
      `/api/dialogmachine/companions/${companionId}/test-users/${encodeURIComponent(userId)}/background-noise`,
      {},
      token
    ),

  updateDialogmachineBackgroundNoise: (
    companionId: string,
    userId: string,
    payload: {
      enabled: boolean;
      noise_type: string;
      volume?: number;
    },
    token?: string | null
  ) =>
    makeRequest<DialogmachineBackgroundNoiseResponse>(
      `/api/dialogmachine/companions/${companionId}/test-users/${encodeURIComponent(userId)}/background-noise`,
      {
        method: 'PUT',
        body: JSON.stringify(payload),
      },
      token
    ),

  // DialogMachine: tool calls / task delegation
  getDialogmachineToolCalls: (
    companionId: string,
    userId: string,
    token?: string | null
  ) =>
    makeRequest<DialogmachineToolCallsResponse>(
      `/api/dialogmachine/companions/${companionId}/test-users/${encodeURIComponent(userId)}/tool-calls`,
      {},
      token
    ),

  updateDialogmachineToolCalls: (
    companionId: string,
    userId: string,
    payload: {
      enabled?: boolean;
      selected_tools?: string[];
    },
    token?: string | null
  ) =>
    makeRequest<DialogmachineToolCallsResponse>(
      `/api/dialogmachine/companions/${companionId}/test-users/${encodeURIComponent(userId)}/tool-calls`,
      {
        method: 'PUT',
        body: JSON.stringify(payload),
      },
      token
    ),

  // DialogMachine: workspace LLM provider/model selection
  getDialogmachineLlmSettings: (
    companionId: string,
    userId: string,
    token?: string | null
  ) =>
    makeRequest<DialogmachineLlmSettingsResponse>(
      `/api/dialogmachine/companions/${companionId}/test-users/${encodeURIComponent(userId)}/llm`,
      {},
      token
    ),

  updateDialogmachineLlmSettings: (
    companionId: string,
    userId: string,
    payload: {
      provider: string;
    },
    token?: string | null
  ) =>
    makeRequest<DialogmachineLlmSettingsResponse>(
      `/api/dialogmachine/companions/${companionId}/test-users/${encodeURIComponent(userId)}/llm`,
      {
        method: 'PUT',
        body: JSON.stringify(payload),
      },
      token
    ),

  // DialogMachine: workspace ElevenLabs voice/model/settings
  getDialogmachineElevenlabsSettings: (
    companionId: string,
    userId: string,
    token?: string | null
  ) =>
    makeRequest<DialogmachineElevenlabsSettingsResponse>(
      `/api/dialogmachine/companions/${companionId}/test-users/${encodeURIComponent(userId)}/elevenlabs`,
      {},
      token
    ),

  updateDialogmachineElevenlabsSettings: (
    companionId: string,
    userId: string,
    payload: Partial<{
      voice_id: string | null;
      voice_name: string | null;
      model_id: string;
      stability: number;
      similarity_boost: number;
      style: number;
      speed: number;
      use_speaker_boost: boolean;
      language_override_enabled: boolean;
      language_code: string;
    }>,
    token?: string | null
  ) =>
    makeRequest<DialogmachineElevenlabsSettingsResponse>(
      `/api/dialogmachine/companions/${companionId}/test-users/${encodeURIComponent(userId)}/elevenlabs`,
      {
        method: 'PUT',
        body: JSON.stringify(payload),
      },
      token
    ),

  listDialogmachineElevenlabsVoices: (token?: string | null) =>
    makeRequest<DialogmachineElevenlabsVoice[]>('/api/dialogmachine/elevenlabs/voices', {}, token),

  cloneDialogmachineElevenlabsVoice: async (
    name: string,
    audioFiles: File[],
    token?: string | null
  ): Promise<DialogmachineElevenlabsVoice> => {
    if (!audioFiles.length) {
      throw new ApiError('No audio recordings provided for cloning.', 400);
    }
    const url = `${API_CONFIG.BASE_URL}/api/dialogmachine/elevenlabs/voices/clone`;
    const formData = new FormData();
    formData.append('name', name);
    audioFiles.forEach(file => {
      formData.append('audio', file);
    });

    const response = await fetch(url, {
      method: 'POST',
      headers: {
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: formData,
    });

    if (!response.ok) {
      const errorText = await response.text();
      let errorMessage = errorText;
      try {
        const errorData = JSON.parse(errorText);
        if (errorData.detail) {
          errorMessage = typeof errorData.detail === 'string'
            ? errorData.detail
            : JSON.stringify(errorData.detail);
        }
      } catch {
        // Not JSON
      }
      throw new ApiError(errorMessage || `API Error ${response.status}`, response.status);
    }

    return response.json();
  },

  // DialogMachine: simulate token and real dial
  createDialogmachineSimulateToken: (
    companionId: string,
    userId: string,
    voiceConfig?: Record<string, unknown>,
    token?: string | null
  ) =>
    makeRequest<DialogmachineVoiceTokenResponse>(
      `/api/dialogmachine/companions/${companionId}/test-users/${encodeURIComponent(userId)}/simulate-token`,
      {
        method: 'POST',
        body: JSON.stringify(voiceConfig ? { voice_config: voiceConfig } : {}),
      },
      token
    ),

  dialDialogmachine: (
    companionId: string,
    userId: string,
    toNumber: string,
    ivrGoal?: string,
    token?: string | null
  ) =>
    makeRequest<TwilioDialOutResponse>(
      `/api/dialogmachine/companions/${companionId}/test-users/${encodeURIComponent(userId)}/dial`,
      {
        method: 'POST',
        body: JSON.stringify({
          to_number: toNumber,
          ...(ivrGoal ? { ivr_goal: ivrGoal } : {}),
        }),
      },
      token
    ),

  getDialogmachineCallTranscript: (
    companionId: string,
    userId: string,
    callSid: string,
    limit = 500,
    token?: string | null
  ) =>
    makeRequest<TwilioCallTranscriptMessage[]>(
      `/api/dialogmachine/companions/${companionId}/test-users/${encodeURIComponent(userId)}/twilio-calls/${encodeURIComponent(callSid)}/messages?limit=${limit}`,
      {},
      token
    ),
};

// Test User types
export interface TestUserSummary {
  id: string;
  user_id: string;
  message_count: number;
  last_interaction_at: string | null;
  profile_preview: { name?: string } | null;
}

export interface TestUserDetail {
  id: string;
  user_id: string;
  message_count: number;
  last_interaction_at: string | null;
  profile: Record<string, unknown>;
  config: Record<string, unknown>;
  created_at: string;
}

export interface DashboardWsTokenResponse {
  token: string;
  relationship_id: string;
  expires_in: number;
  ws_url: string;
}

export interface DashboardVoiceTokenResponse {
  token: string;
  relationship_id: string;
  expires_in: number;
  ws_url: string;
}

export interface TestUserMessage {
  id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  created_at: string;
}

export interface TwilioDialOutResponse {
  call_sid: string;
  status: string;
  call_id: string;
}

export interface DialogmachineHotContextResponse {
  relationship_id: string;
  content: string;
  exists: boolean;
}

export interface DialogmachinePromptOverrideResponse {
  relationship_id: string;
  prompt_override: string | null;
}

export interface DialogmachineGuardrailsResponse {
  relationship_id: string;
  guardrails: string | null;
}

export interface DialogmachineBackgroundNoiseResponse {
  relationship_id: string;
  enabled: boolean;
  noise_type: string;
  volume: number;
  available_noise_types: string[];
}

export interface DialogmachineToolCallsResponse {
  relationship_id: string;
  enabled: boolean;
  selected_tools: string[];
  available_tools: string[];
}

export interface DialogmachineLlmModelOption {
  id: string;
  label: string;
  description: string | null;
}

export interface DialogmachineLlmSettingsResponse {
  relationship_id: string;
  provider: string;
  available_models: DialogmachineLlmModelOption[];
}

export interface DialogmachineElevenlabsModelOption {
  id: string;
  label: string;
}

export interface DialogmachineElevenlabsVoice {
  voice_id: string;
  name: string;
  category: string | null;
}

export interface DialogmachineElevenlabsSettingsResponse {
  relationship_id: string;
  voice_id: string | null;
  voice_name: string | null;
  model_id: string;
  stability: number;
  similarity_boost: number;
  style: number;
  speed: number;
  use_speaker_boost: boolean;
  language_override_enabled: boolean;
  language_code: string;
  available_models: DialogmachineElevenlabsModelOption[];
}

export interface DialogmachineVoiceTokenResponse {
  token: string;
  relationship_id: string;
  expires_in: number;
  ws_url: string;
}

export interface TwilioCallTranscriptMessage {
  id: string;
  role: string;
  content: string;
  created_at: string;
  call_sid: string | null;
  call_id: string | null;
  call_mode: string | null;
}

export interface TwilioCallStatusResponse {
  call_sid: string;
  status: string;
  duration: number | null;
  timestamp: string;
}

/**
 * Get Twilio call status (no auth required).
 * This is a standalone function because it doesn't need auth tokens.
 */
export async function getTwilioCallStatus(callSid: string): Promise<TwilioCallStatusResponse> {
  const url = `${API_CONFIG.BASE_URL}/twilio/status/${callSid}`;
  const response = await fetch(url);

  if (!response.ok) {
    throw new ApiError(`Failed to get call status: ${response.status}`, response.status);
  }

  return response.json();
}
