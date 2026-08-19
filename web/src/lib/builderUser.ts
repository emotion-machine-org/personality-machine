// Utility for persisting a stable builder test user id across refreshes

const KEY_PREFIX = 'em.builderUserId';

const getKey = (userId?: string | null) => userId ? `${KEY_PREFIX}.${userId}` : KEY_PREFIX;

const generateId = (userId?: string | null) => {
  if (typeof window === 'undefined') {
    return userId ? `builder-${userId}` : 'builder-dev';
  }
  const base = userId ? `builder-${userId}` : 'builder';
  const suffix = window.crypto?.randomUUID?.() || Math.random().toString(36).slice(2);
  return `${base}-${suffix}`;
};

export function getOrInitBuilderUserId(userId?: string | null): string {
  if (typeof window === 'undefined') {
    return generateId(userId);
  }
  if (!userId) {
    return generateId(null);
  }
  const key = getKey(userId);
  let id = window.localStorage.getItem(key);
  if (!id) {
    id = generateId(userId);
    window.localStorage.setItem(key, id);
  }
  // Clean up legacy global key to avoid leaking conversations across accounts
  window.localStorage.removeItem(getKey(null));
  return id;
}

export function setBuilderUserId(id: string, userId?: string | null) {
  if (typeof window === 'undefined') return;
  window.localStorage.setItem(getKey(userId), id);
}

export function resetBuilderUserId(userId?: string | null): string {
  const id = generateId(userId);
  if (typeof window !== 'undefined') {
    window.localStorage.setItem(getKey(userId), id);
  }
  return id;
}

export function getLastConversationKey(companionId: string, builderId: string) {
  return `em.lastConversation.${companionId}.${builderId}`;
}

export function getLastConversationId(companionId: string, builderId: string): string | null {
  if (typeof window === 'undefined') return null;
  return window.localStorage.getItem(getLastConversationKey(companionId, builderId));
}

export function setLastConversationId(companionId: string, builderId: string, conversationId: string) {
  if (typeof window === 'undefined') return;
  window.localStorage.setItem(getLastConversationKey(companionId, builderId), conversationId);
}

export function clearLastConversationId(companionId: string, builderId: string) {
  if (typeof window === 'undefined') return;
  window.localStorage.removeItem(getLastConversationKey(companionId, builderId));
}
