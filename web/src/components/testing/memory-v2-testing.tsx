'use client';

import { useState, useCallback, useRef, useEffect, FormEvent } from 'react';
import { useAuth } from '@clerk/nextjs';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { ChatInput } from '@/components/ui/chat-input';
import { MessageBubble } from '@/components/ui/message-bubble';
import Icon from '@/components/ui/icon';
import Dropdown from '@/components/ui/dropdown';
import { cn } from '@/lib/utils';
import { API_CONFIG } from '@/lib/config';

const API_BASE = API_CONFIG.BASE_URL;

// Types
interface Companion {
  id: string;
  name: string;
  memory_version: number;
  memory_enabled: boolean;
}

interface MemoryEntry {
  id: string;
  content: string;
  type: string | null;
  created_at: string;
  updated_at: string;
}

interface MemoryListResponse {
  relationship_id: string;
  entries: MemoryEntry[];
  count: number;
  max_entries: number;
}

interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
}

// Entry type options
const ENTRY_TYPES = [
  { value: 'identity', label: 'Identity' },
  { value: 'preference', label: 'Preference' },
  { value: 'goal', label: 'Goal' },
  { value: 'event', label: 'Event' },
  { value: 'relationship', label: 'Relationship' },
  { value: 'other', label: 'Other' },
];

// Type badge colors (dark theme)
const TYPE_COLORS: Record<string, string> = {
  identity: 'bg-blue-900/50 text-blue-300',
  preference: 'bg-purple-900/50 text-purple-300',
  goal: 'bg-green-900/50 text-green-300',
  event: 'bg-yellow-900/50 text-yellow-300',
  relationship: 'bg-pink-900/50 text-pink-300',
  other: 'bg-white/10 text-white/60',
};

// Section label component
function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <label className="block text-xs font-medium text-white/40 uppercase tracking-wider mb-2">
      {children}
    </label>
  );
}

export default function MemoryV2Testing() {
  const { getToken } = useAuth();
  const queryClient = useQueryClient();

  // State
  const [selectedCompanionId, setSelectedCompanionId] = useState<string>('');
  const [userId, setUserId] = useState<string>('test-user-1');
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([]);
  const [inputMessage, setInputMessage] = useState('');
  const [newEntryContent, setNewEntryContent] = useState('');
  const [newEntryType, setNewEntryType] = useState('other');
  const [isSending, setIsSending] = useState(false);
  const [autoRefresh, setAutoRefresh] = useState(true);

  const chatEndRef = useRef<HTMLDivElement>(null);

  // Scroll to bottom of chat
  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [chatMessages]);

  // Fetch companions
  const { data: companions = [], isLoading: companionsLoading } = useQuery<Companion[]>({
    queryKey: ['memory-v2-companions'],
    queryFn: async () => {
      const token = await getToken();
      const res = await fetch(`${API_BASE}/api/memory-v2-testing/companions`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!res.ok) throw new Error('Failed to fetch companions');
      return res.json();
    },
  });

  // Fetch memory entries
  const {
    data: memoryData,
    isLoading: memoryLoading,
    refetch: refetchMemory,
  } = useQuery<MemoryListResponse>({
    queryKey: ['memory-v2-entries', selectedCompanionId, userId],
    queryFn: async () => {
      const token = await getToken();
      const res = await fetch(
        `${API_BASE}/api/memory-v2-testing/companions/${selectedCompanionId}/users/${encodeURIComponent(userId)}/memory`,
        { headers: { Authorization: `Bearer ${token}` } }
      );
      if (!res.ok) throw new Error('Failed to fetch memory');
      return res.json();
    },
    enabled: !!selectedCompanionId && !!userId,
    refetchInterval: autoRefresh ? 2000 : false,
  });

  // Enable Memory V2 mutation
  const enableV2Mutation = useMutation({
    mutationFn: async (companionId: string) => {
      const token = await getToken();
      const res = await fetch(
        `${API_BASE}/api/memory-v2-testing/companions/${companionId}/enable-memory-v2`,
        {
          method: 'POST',
          headers: { Authorization: `Bearer ${token}` },
        }
      );
      if (!res.ok) throw new Error('Failed to enable Memory V2');
      return res.json();
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['memory-v2-companions'] });
    },
  });

  // Add entry mutation
  const addEntryMutation = useMutation({
    mutationFn: async ({ content, type }: { content: string; type: string }) => {
      const token = await getToken();
      const res = await fetch(
        `${API_BASE}/api/memory-v2-testing/companions/${selectedCompanionId}/users/${encodeURIComponent(userId)}/memory`,
        {
          method: 'POST',
          headers: {
            Authorization: `Bearer ${token}`,
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({ content, type }),
        }
      );
      if (!res.ok) throw new Error('Failed to add entry');
      return res.json();
    },
    onSuccess: () => {
      setNewEntryContent('');
      refetchMemory();
    },
  });

  // Delete entry mutation
  const deleteEntryMutation = useMutation({
    mutationFn: async (entryId: string) => {
      const token = await getToken();
      const res = await fetch(
        `${API_BASE}/api/memory-v2-testing/companions/${selectedCompanionId}/users/${encodeURIComponent(userId)}/memory/${entryId}`,
        {
          method: 'DELETE',
          headers: { Authorization: `Bearer ${token}` },
        }
      );
      if (!res.ok) throw new Error('Failed to delete entry');
    },
    onSuccess: () => {
      refetchMemory();
    },
  });

  // Clear all mutation
  const clearAllMutation = useMutation({
    mutationFn: async () => {
      const token = await getToken();
      const res = await fetch(
        `${API_BASE}/api/memory-v2-testing/companions/${selectedCompanionId}/users/${encodeURIComponent(userId)}/memory`,
        {
          method: 'DELETE',
          headers: { Authorization: `Bearer ${token}` },
        }
      );
      if (!res.ok) throw new Error('Failed to clear memory');
    },
    onSuccess: () => {
      refetchMemory();
    },
  });

  // Send chat message
  const sendMessage = useCallback(async () => {
    if (!inputMessage.trim() || !selectedCompanionId || isSending) return;

    setIsSending(true);
    const userMessage = inputMessage.trim();
    setInputMessage('');

    // Add user message to chat
    const updatedMessages: ChatMessage[] = [...chatMessages, { role: 'user', content: userMessage }];
    setChatMessages(updatedMessages);

    try {
      const token = await getToken();
      const res = await fetch(
        `${API_BASE}/api/memory-v2-testing/companions/${selectedCompanionId}/users/${encodeURIComponent(userId)}/chat`,
        {
          method: 'POST',
          headers: {
            Authorization: `Bearer ${token}`,
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({
            message: userMessage,
            history: chatMessages, // Send previous messages as history
          }),
        }
      );

      if (!res.ok) throw new Error('Failed to send message');

      const data = await res.json();

      // Add assistant response to chat
      setChatMessages((prev) => [...prev, { role: 'assistant', content: data.response }]);

      // Refresh memory
      refetchMemory();
    } catch (error) {
      console.error('Chat error:', error);
      setChatMessages((prev) => [
        ...prev,
        { role: 'assistant', content: `Error: ${error instanceof Error ? error.message : 'Unknown error'}` },
      ]);
    } finally {
      setIsSending(false);
    }
  }, [inputMessage, selectedCompanionId, userId, isSending, getToken, refetchMemory, chatMessages]);

  // Get selected companion
  const selectedCompanion = companions.find((c) => c.id === selectedCompanionId);

  return (
    <div className="fixed inset-0 bg-black text-white flex">
      {/* Left panel - Config & Chat */}
      <div className="w-[450px] bg-gray-darker border-r border-white/10 flex flex-col">
        {/* Header */}
        <div className="p-4 border-b border-white/10 shrink-0">
          <h1 className="text-[32px] font-light tracking-[-0.04em]">Memory V2 Testing</h1>
          <p className="text-xs text-white/40">
            Test scratchpad memory with live chat
          </p>
        </div>

        {/* Config Section */}
        <div className="p-4 border-b border-white/10 space-y-4 shrink-0">
          <div>
            <SectionLabel>Companion</SectionLabel>
            {companionsLoading ? (
              <div className="text-sm text-white/40">Loading...</div>
            ) : (
              <Dropdown
                options={companions.map((c) => ({
                  value: c.id,
                  label: c.name,
                  description: c.memory_version === 2 ? 'v2' : 'v1',
                }))}
                value={selectedCompanionId}
                onChange={(value) => {
                  setSelectedCompanionId(value);
                  setChatMessages([]);
                }}
                placeholder="Select companion..."
              />
            )}
          </div>

          <div>
            <SectionLabel>User ID</SectionLabel>
            <input
              type="text"
              value={userId}
              onChange={(e) => setUserId(e.target.value)}
              className="w-full bg-gray-darker text-white text-sm rounded px-3 py-2 border border-white/20 focus:outline-none focus:border-white/40"
              placeholder="test-user-1"
            />
          </div>

          {selectedCompanion && selectedCompanion.memory_version !== 2 && (
            <button
              className="w-full px-4 py-2 text-sm rounded bg-brand-solid text-white hover:bg-brand-solid-hover transition-colors disabled:opacity-50"
              onClick={() => enableV2Mutation.mutate(selectedCompanionId)}
              disabled={enableV2Mutation.isPending}
            >
              {enableV2Mutation.isPending ? 'Enabling...' : 'Enable Memory V2'}
            </button>
          )}

          {selectedCompanion?.memory_version === 2 && (
            <div className="text-xs text-green-400 flex items-center gap-1">
              <Icon name="check" size={12} color="currentColor" />
              Memory V2 Enabled
            </div>
          )}
        </div>

        {/* Chat Section */}
        <div className="flex-1 flex flex-col overflow-hidden p-4">
          <div className="flex items-center justify-between mb-2">
            <SectionLabel>Chat</SectionLabel>
            {chatMessages.length > 0 && (
              <button
                onClick={() => setChatMessages([])}
                className="text-xs text-white/40 hover:text-white transition-colors"
              >
                Clear Chat
              </button>
            )}
          </div>

          {/* Chat Messages */}
          <div className="flex-1 overflow-y-auto rounded border border-white/10 bg-black/30 p-3 mb-3">
            {chatMessages.length === 0 ? (
              <p className="text-center text-sm text-white/30 py-8">
                Start a conversation to test memory injection
              </p>
            ) : (
              <div className="space-y-4">
                {chatMessages.map((msg, i) => (
                  <MessageBubble key={i} role={msg.role}>
                    {msg.content}
                  </MessageBubble>
                ))}
              </div>
            )}
            <div ref={chatEndRef} />
          </div>

          {/* Chat Input */}
          <div className="shrink-0">
            <ChatInput
              value={inputMessage}
              onChange={setInputMessage}
              onSubmit={(e: FormEvent) => {
                e.preventDefault();
                sendMessage();
              }}
              placeholder="Type a message..."
              disabled={!selectedCompanionId || isSending}
              sending={isSending}
              inputWrapperClassName="rounded-full px-4 pt-4 pb-3 bg-[#3C3C3C]"
            />
          </div>
        </div>
      </div>

      {/* Right panel - Memory Scratchpad */}
      <div className="flex-1 flex flex-col overflow-hidden">
        {/* Header */}
        <div className="p-4 border-b border-white/10 flex items-center justify-between shrink-0">
          <div>
            <h2 className="text-lg font-medium">Scratchpad</h2>
            {memoryData && (
              <p className="text-xs text-white/40">
                {memoryData.count} / {memoryData.max_entries} entries
              </p>
            )}
          </div>
          <div className="flex items-center gap-3">
            <label className="flex items-center gap-2 text-xs text-white/60 cursor-pointer">
              <input
                type="checkbox"
                checked={autoRefresh}
                onChange={(e) => setAutoRefresh(e.target.checked)}
                className="rounded bg-gray-darker border-white/20"
              />
              Auto-refresh
            </label>
            <button
              onClick={() => refetchMemory()}
              className="p-2 rounded text-white/40 hover:text-white hover:bg-white/10 transition-colors"
              title="Refresh"
            >
              <Icon name="restart" size={16} color="currentColor" />
            </button>
            {memoryData && memoryData.count > 0 && (
              <button
                onClick={() => clearAllMutation.mutate()}
                disabled={clearAllMutation.isPending}
                className="p-2 rounded text-brand-solid hover:bg-brand-bg transition-colors disabled:opacity-50"
                title="Clear all"
              >
                <Icon name="trash" size={16} color="currentColor" />
              </button>
            )}
          </div>
        </div>

        {/* Memory Entries */}
        <div className="flex-1 overflow-y-auto p-4">
          {memoryLoading ? (
            <p className="text-center text-sm text-white/40 py-8">Loading...</p>
          ) : !memoryData || memoryData.count === 0 ? (
            <p className="text-center text-sm text-white/30 py-8">
              No memories yet. Chat with the companion or add entries manually.
            </p>
          ) : (
            <div className="space-y-2">
              {memoryData.entries.map((entry) => (
                <div
                  key={entry.id}
                  className="group flex items-start justify-between rounded bg-white/5 p-3 hover:bg-white/10 transition-colors"
                >
                  <div className="flex-1 min-w-0">
                    <span
                      className={cn(
                        'inline-block rounded px-2 py-0.5 text-xs font-medium mr-2',
                        TYPE_COLORS[entry.type || 'other'] || TYPE_COLORS.other
                      )}
                    >
                      {entry.type || 'other'}
                    </span>
                    <span className="text-sm text-white/80">{entry.content}</span>
                  </div>
                  <button
                    onClick={() => deleteEntryMutation.mutate(entry.id)}
                    className="ml-3 p-1 opacity-0 group-hover:opacity-100 transition-opacity text-white/40 hover:text-brand-solid"
                    title="Delete"
                  >
                    <Icon name="x" size={14} color="currentColor" />
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Add Entry Form */}
        <div className="p-4 border-t border-white/10 shrink-0">
          <SectionLabel>Add Entry Manually</SectionLabel>
          <div className="flex gap-2">
            <input
              type="text"
              value={newEntryContent}
              onChange={(e) => setNewEntryContent(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && newEntryContent.trim()) {
                  addEntryMutation.mutate({
                    content: newEntryContent.trim(),
                    type: newEntryType,
                  });
                }
              }}
              placeholder="Memory content..."
              className="flex-1 bg-gray-darker text-white text-sm rounded px-3 py-2 border border-white/20 focus:outline-none focus:border-white/40"
              disabled={!selectedCompanionId}
            />
            <Dropdown
              options={ENTRY_TYPES}
              value={newEntryType}
              onChange={setNewEntryType}
              placeholder="Type"
              size="sm"
              className="w-32"
            />
            <button
              onClick={() => {
                if (newEntryContent.trim()) {
                  addEntryMutation.mutate({
                    content: newEntryContent.trim(),
                    type: newEntryType,
                  });
                }
              }}
              disabled={!selectedCompanionId || !newEntryContent.trim() || addEntryMutation.isPending}
              className="px-4 py-2 text-sm rounded bg-white/10 text-white hover:bg-white/20 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            >
              Add
            </button>
          </div>
        </div>

        {/* Debug Info */}
        {memoryData && (
          <div className="p-4 border-t border-white/10 shrink-0">
            <SectionLabel>Debug</SectionLabel>
            <div className="text-xs text-white/40 font-mono">
              <p>Relationship: {memoryData.relationship_id}</p>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
