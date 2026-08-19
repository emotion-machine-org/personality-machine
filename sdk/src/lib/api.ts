import type {
  User,
  WebSocketTicket,
  Companion,
  CompanionConfig,
  CompanionVersionSummary,
  CompanionVersionDetail,
  SessionCreate,
  SessionCreated,
  ConversationSummary,
  AnalyticsMessage,
  SystemPromptResponse,
  VoiceMappings,
  ConversationLabel,
  MemoryItem,
  MemoryStats,
  MemoryListResponse,
  UserMemoriesPayload,
  UserAnalyticsSummary,
  CompanionShare,
  CompanionShareAnalytics,
  PublicShareMeta,
} from './types';

declare const process: { env: Record<string, string | undefined> };
const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8100';

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
  ConversationSummary,
  AnalyticsMessage,
  SystemPromptResponse,
  VoiceMappings,
  CompanionShare,
  CompanionShareAnalytics,
  PublicShareMeta,
};

// API client functions
async function makeRequest<T>(
  endpoint: string,
  options: RequestInit = {},
  token?: string | null
): Promise<T> {
  const url = `${API_BASE}${endpoint}`;

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
    throw new Error(`API Error ${response.status}: ${errorText}`);
  }

  return response.json();
}

export const apiClient = {
  // User endpoints
  getCurrentUser: (token?: string | null) =>
    makeRequest<User>('/api/me', {}, token),

  completeOnboarding: (token?: string | null) =>
    makeRequest<{ message: string }>('/api/me/complete-onboarding', { method: 'POST' }, token),

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

  // Analytics endpoints
  getAnalyticsConversations: (companionId: string, token?: string | null) =>
    makeRequest<ConversationSummary[]>(`/api/analytics/companions/${companionId}/conversations`, {}, token),

  getAnalyticsMessages: (
    conversationId: string,
    token?: string | null,
    opts?: { view?: 'redacted' | 'original'; fallback?: boolean }
  ) => {
    const params = new URLSearchParams();
    if (opts?.view) params.set('view', opts.view);
    if (typeof opts?.fallback === 'boolean') params.set('fallback', opts.fallback ? 'true' : 'false');
    const qs = params.toString();
    return makeRequest<AnalyticsMessage[]>(`/api/analytics/conversations/${conversationId}/messages${qs ? `?${qs}` : ''}`, {}, token);
  },

  getAnalyticsSystemPrompt: (conversationId: string, token?: string | null) =>
    makeRequest<SystemPromptResponse>(`/api/analytics/conversations/${conversationId}/system-prompt`, {}, token),

  deleteAnalyticsConversation: (conversationId: string, token?: string | null) =>
    makeRequest<{ message: string }>(`/api/analytics/conversations/${conversationId}`, { method: 'DELETE' }, token),

  // Labeling endpoints
  triggerLabelingJob: (
    companionId: string,
    body: { model?: string; provider?: string; skip_existing?: boolean; since?: string },
    token?: string | null
  ) => makeRequest<{ job_id: string }>(`/api/analytics/companions/${companionId}/label-conversations`, {
    method: 'POST',
    body: JSON.stringify(body)
  }, token),

  getLabelingJob: (jobId: string, token?: string | null) =>
    makeRequest<{ id: string; status: string; total_conversations: number; processed_count: number; error_count: number; created_at?: string; started_at?: string; completed_at?: string }>(`/api/analytics/jobs/${jobId}`, {}, token),

  getConversationLabels: (
    companionId: string,
    params?: { limit?: number; offset?: number; fast?: boolean },
    token?: string | null
  ) => {
    const query = new URLSearchParams();
    if (typeof params?.limit === 'number') query.set('limit', String(params.limit));
    if (typeof params?.offset === 'number') query.set('offset', String(params.offset));
    if (typeof params?.fast === 'boolean') query.set('fast', params.fast ? 'true' : 'false');
    const qs = query.toString();
    return makeRequest<ConversationLabel[]>(`/api/analytics/companions/${companionId}/labels${qs ? `?${qs}` : ''}`, {}, token);
  },

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

  // Privacy redaction endpoints
  triggerPrivacyRedaction: (
    conversationId: string,
    body: { model?: string; provider?: string; force?: boolean },
    token?: string | null
  ) => makeRequest<{ job_id: string }>(`/api/analytics/conversations/${conversationId}/privacy/redact`, {
    method: 'POST',
    body: JSON.stringify(body)
  }, token),

  getPrivacyStatus: (conversationId: string, token?: string | null) =>
    makeRequest<{ status?: string; job_id?: string; stale: boolean; error?: string; enabled?: boolean; last_computed_at?: string }>(`/api/analytics/conversations/${conversationId}/privacy/status`, {}, token),

  togglePrivacyFlag: (conversationId: string, enabled: boolean, token?: string | null) =>
    makeRequest<{ ok: true }>(`/api/analytics/conversations/${conversationId}/privacy/toggle`, {
      method: 'POST',
      body: JSON.stringify({ enabled })
    }, token),

  // Memory endpoints
  getMemories: (
    companionId: string,
    params?: { limit?: number; offset?: number; conversation_id?: string; order_by?: 'created_at' | 'last_accessed_at' | 'importance'; order_dir?: 'ASC' | 'DESC'; external_user_id?: string },
    token?: string | null
  ) => {
    const query = new URLSearchParams();
    if (typeof params?.limit === 'number') query.set('limit', String(params.limit));
    if (typeof params?.offset === 'number') query.set('offset', String(params.offset));
    if (params?.conversation_id) query.set('conversation_id', params.conversation_id);
    if (params?.order_by) query.set('order_by', params.order_by);
    if (params?.order_dir) query.set('order_dir', params.order_dir);
    if (params?.external_user_id) query.set('external_user_id', params.external_user_id);
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
};
