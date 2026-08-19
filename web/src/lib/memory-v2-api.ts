/**
 * API client for Memory V2 endpoints.
 * Supports pagination, search, inline editing, and temp chat for memory testing.
 */

import { API_CONFIG } from './config';

// Types
export interface MemoryV2Entry {
  id: string;
  content: string;
  type: string | null;
  created_at: string;
  updated_at: string;
}

export interface MemoryV2ListResponse {
  relationship_id: string;
  entries: MemoryV2Entry[];
  count: number;
  total: number;
  max_entries: number;
  next_cursor: string | null;
  has_more: boolean;
}

export interface TempChatRequest {
  message: string;
  history: Array<{ role: 'user' | 'assistant'; content: string }>;
  user_id: string; // Uses the selected builder user (same as companion-simulator)
}

export interface TempChatResponse {
  response: string;
  memory_entries: MemoryV2Entry[];
  new_memories: MemoryV2Entry[];
}

// API helper
async function makeRequest<T>(
  endpoint: string,
  options: RequestInit = {},
  token?: string | null
): Promise<T> {
  const url = `${API_CONFIG.BASE_URL}${endpoint}`;
  const headers: HeadersInit = {
    'Content-Type': 'application/json',
    ...(token && { Authorization: `Bearer ${token}` }),
    ...options.headers,
  };

  const response = await fetch(url, { ...options, headers });

  if (!response.ok) {
    const errorText = await response.text();
    let errorMessage = errorText;
    try {
      const errorData = JSON.parse(errorText);
      if (errorData.detail) {
        errorMessage =
          typeof errorData.detail === 'string'
            ? errorData.detail
            : JSON.stringify(errorData.detail);
      }
    } catch {
      // Not JSON, use raw text
    }
    throw new Error(errorMessage || `API Error ${response.status}`);
  }

  // Handle 204 No Content
  if (response.status === 204) {
    return undefined as T;
  }

  return response.json();
}

// API client
export const memoryV2Api = {
  /**
   * List memory entries with cursor-based pagination and optional search.
   */
  listMemories: (
    companionId: string,
    userId: string,
    params: {
      limit?: number;
      cursor?: string;
      search?: string;
      type_filter?: string;
    } = {},
    token?: string | null
  ): Promise<MemoryV2ListResponse> => {
    const query = new URLSearchParams();
    if (params.limit) query.set('limit', String(params.limit));
    if (params.cursor) query.set('cursor', params.cursor);
    if (params.search) query.set('search', params.search);
    if (params.type_filter) query.set('type_filter', params.type_filter);
    const qs = query.toString();
    return makeRequest(
      `/api/memory-v2-testing/companions/${companionId}/users/${encodeURIComponent(userId)}/memory${qs ? `?${qs}` : ''}`,
      {},
      token
    );
  },

  /**
   * Update a memory entry's content and/or type.
   */
  updateMemory: (
    companionId: string,
    userId: string,
    entryId: string,
    body: { content?: string; type?: string | null },
    token?: string | null
  ): Promise<MemoryV2Entry> =>
    makeRequest(
      `/api/memory-v2-testing/companions/${companionId}/users/${encodeURIComponent(userId)}/memory/${entryId}`,
      { method: 'PATCH', body: JSON.stringify(body) },
      token
    ),

  /**
   * Delete a memory entry.
   */
  deleteMemory: (
    companionId: string,
    userId: string,
    entryId: string,
    token?: string | null
  ): Promise<void> =>
    makeRequest(
      `/api/memory-v2-testing/companions/${companionId}/users/${encodeURIComponent(userId)}/memory/${entryId}`,
      { method: 'DELETE' },
      token
    ),

  /**
   * Create a new memory entry manually.
   */
  createMemory: (
    companionId: string,
    userId: string,
    body: { content: string; type?: string },
    token?: string | null
  ): Promise<MemoryV2Entry> =>
    makeRequest(
      `/api/memory-v2-testing/companions/${companionId}/users/${encodeURIComponent(userId)}/memory`,
      { method: 'POST', body: JSON.stringify(body) },
      token
    ),

  /**
   * Chat for testing memory consolidation.
   * Uses the provided user_id and waits for memory ingestion to complete.
   */
  tempChat: (
    companionId: string,
    body: TempChatRequest,
    token?: string | null
  ): Promise<TempChatResponse> =>
    makeRequest(
      `/api/memory-v2-testing/companions/${companionId}/temp-chat`,
      { method: 'POST', body: JSON.stringify(body) },
      token
    ),
};
