'use client';

import { useState } from 'react';
import { useRelationshipMessages } from '@/hooks/useRelationships';
import { useUserMemoriesPanel } from '@/hooks/useMemories';
import type { RelationshipSummary } from '@/lib/types';
import type { TestUserMessage } from '@/lib/api';

function formatRelativeTime(dateString: string | null): string {
  if (!dateString) return 'Never';
  const date = new Date(dateString);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffMins = Math.floor(diffMs / 60000);
  const diffHours = Math.floor(diffMs / 3600000);
  const diffDays = Math.floor(diffMs / 86400000);

  if (diffMins < 1) return 'Just now';
  if (diffMins < 60) return `${diffMins}m ago`;
  if (diffHours < 24) return `${diffHours}h ago`;
  if (diffDays < 30) return `${diffDays}d ago`;
  return date.toLocaleDateString();
}

type Tab = 'messages' | 'profile' | 'memory';

interface RelationshipDetailProps {
  companionId: string;
  relationship: RelationshipSummary;
}

export default function RelationshipDetail({ companionId, relationship }: RelationshipDetailProps) {
  const [activeTab, setActiveTab] = useState<Tab>('messages');
  const { data: messages, isLoading: messagesLoading, error: messagesError } = useRelationshipMessages(companionId, relationship.user_id, 200);

  const messageCount = messages?.length ?? relationship.message_count;

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="px-4 py-3 border-b border-white/10">
        <h2 className="text-sm font-book text-white truncate">{relationship.user_id}</h2>
        <div className="flex items-center gap-3 mt-0.5 text-[11px] text-white/35">
          <span>{messageCount} messages</span>
          <span>Last active {formatRelativeTime(relationship.last_interaction_at)}</span>
          <span>Created {formatRelativeTime(relationship.created_at)}</span>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex items-center gap-0 px-4 border-b border-white/10">
        {(['messages', 'profile', 'memory'] as Tab[]).map((tab) => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            className={`px-3 py-2 text-[13px] font-book transition-colors border-b-2 ${
              activeTab === tab
                ? 'text-white border-white/60'
                : 'text-white/35 border-transparent hover:text-white/60'
            }`}
          >
            {tab.charAt(0).toUpperCase() + tab.slice(1)}
          </button>
        ))}
      </div>

      {/* Tab Content */}
      <div className="flex-1 overflow-y-auto min-h-0">
        {activeTab === 'messages' && (
          <MessagesTab messages={messages} isLoading={messagesLoading} error={messagesError} />
        )}
        {activeTab === 'profile' && (
          <ProfileTab profile={relationship.profile} />
        )}
        {activeTab === 'memory' && (
          <MemoryTab companionId={companionId} userId={relationship.user_id} />
        )}
      </div>
    </div>
  );
}

function MessagesTab({ messages, isLoading, error }: { messages: TestUserMessage[] | undefined; isLoading: boolean; error: Error | null }) {
  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-12 text-[13px] text-white/40">
        Loading messages...
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex items-center justify-center py-12 text-[13px] text-[color:var(--color-brand-solid)]">
        Failed to load messages
      </div>
    );
  }

  if (!messages || messages.length === 0) {
    return (
      <div className="flex items-center justify-center py-12 text-[13px] text-white/40">
        No messages yet
      </div>
    );
  }

  return (
    <div className="p-4 space-y-3">
      {messages.map((msg, index) => (
        <div
          key={msg.id || `msg-${index}`}
          className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}
        >
          <div
            className={`max-w-[80%] ${
              msg.role === 'assistant'
                ? 'bg-[var(--color-dropdown-bg)] rounded-lg px-3 py-2'
                : ''
            }`}
          >
            <p className={`text-[13px] font-book leading-relaxed break-words whitespace-pre-wrap ${
              msg.role === 'user' ? 'text-white/80 text-right' : 'text-white'
            }`}>
              {msg.content}
            </p>
            {msg.created_at && (
              <p className={`text-[10px] text-white/25 mt-1 ${msg.role === 'user' ? 'text-right' : ''}`}>
                {new Date(msg.created_at).toLocaleString('en-US', {
                  month: 'short',
                  day: 'numeric',
                  hour: 'numeric',
                  minute: '2-digit',
                  hour12: true,
                })}
              </p>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}

function ProfileTab({ profile }: { profile: Record<string, unknown> }) {
  const hasProfile = profile && Object.keys(profile).length > 0;

  if (!hasProfile) {
    return (
      <div className="flex items-center justify-center py-12 text-[13px] text-white/40">
        No profile data
      </div>
    );
  }

  return (
    <div className="p-4">
      <pre className="text-[12px] font-mono text-white/70 leading-relaxed whitespace-pre-wrap overflow-x-auto bg-[var(--color-dropdown-bg)] rounded-lg p-3">
        {JSON.stringify(profile, null, 2)}
      </pre>
    </div>
  );
}

function MemoryTab({ companionId, userId }: { companionId: string; userId: string }) {
  const { memories, total, loading, error } = useUserMemoriesPanel(companionId, userId);

  if (loading) {
    return (
      <div className="flex items-center justify-center py-12 text-[13px] text-white/40">
        Loading memories...
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex items-center justify-center py-12 text-[13px] text-[color:var(--color-brand-solid)]">
        Failed to load memories
      </div>
    );
  }

  if (!memories || memories.length === 0) {
    return (
      <div className="flex items-center justify-center py-12 text-[13px] text-white/40">
        No memories stored
      </div>
    );
  }

  return (
    <div className="p-4 space-y-2">
      <p className="text-[11px] text-white/30 mb-3">{total} memories</p>
      {memories.map((mem) => (
        <div
          key={mem.id}
          className="bg-[var(--color-dropdown-bg)] rounded px-3 py-2 text-[12px] font-book text-white/70 leading-relaxed"
        >
          {mem.content}
          <div className="flex items-center gap-2 mt-1 text-[10px] text-white/25">
            <span>Importance: {mem.importance}</span>
            <span>{formatRelativeTime(mem.created_at)}</span>
          </div>
        </div>
      ))}
    </div>
  );
}
