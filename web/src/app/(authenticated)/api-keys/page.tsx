'use client';

import { useState } from 'react';
import Icon from '@/components/ui/icon';
import ConfirmModal from '@/components/ui/confirm-modal';
import FormSection from '@/components/ui/form-section';
import { useApiKeys, useCreateApiKey, useRevokeApiKey } from '@/hooks/useApiKeys';
import type { ApiKey, ApiKeyWithSecret } from '@/lib/types';

function formatDate(dateString: string | null): string {
  if (!dateString) return 'Never';
  const date = new Date(dateString);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffMins = Math.floor(diffMs / 60000);
  const diffHours = Math.floor(diffMs / 3600000);
  const diffDays = Math.floor(diffMs / 86400000);

  if (diffMins < 1) return 'Just now';
  if (diffMins < 60) return `${diffMins} min ago`;
  if (diffHours < 24) return `${diffHours} hour${diffHours > 1 ? 's' : ''} ago`;
  if (diffDays < 7) return `${diffDays} day${diffDays > 1 ? 's' : ''} ago`;
  return date.toLocaleDateString();
}

function StatusBadge({ status }: { status: 'active' | 'revoked' }) {
  const isActive = status === 'active';
  return (
    <span
      className={`inline-flex items-center gap-1.5 px-2 py-0.5 text-xs rounded-full ${
        isActive
          ? 'bg-[color:var(--color-green-bg)] text-[color:var(--color-green-solid-hover)] border border-[color:var(--color-green-border)]/60'
          : 'bg-white/5 text-white/50 border border-white/10'
      }`}
    >
      <span className={`h-1.5 w-1.5 rounded-full ${isActive ? 'bg-current' : 'bg-white/40'}`} />
      {isActive ? 'Active' : 'Revoked'}
    </span>
  );
}

function CreateKeyModal({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: (key: ApiKeyWithSecret) => void;
}) {
  const [name, setName] = useState('');
  const createApiKey = useCreateApiKey();

  const handleCreate = async () => {
    try {
      const result = await createApiKey.mutateAsync({ name: name.trim() || undefined });
      onCreated(result);
      setName('');
    } catch (error) {
      console.error('Failed to create API key:', error);
    }
  };

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <button
        aria-label="Close"
        className="absolute inset-0 bg-black/60 backdrop-blur-[2px]"
        onClick={onClose}
      />
      <div className="relative z-10 w-[400px] max-w-[92vw] bg-[color:var(--color-gray-darker)] p-8 shadow-xl">
        <div className="space-y-3 mb-6">
          <h2 className="text-[17px] font-light text-white/70">New API Key</h2>
          <p className="text-[13px] font-book text-white/50">
            Create a key for programmatic access to your companions.
          </p>
        </div>

        <div className="space-y-4">
          <div>
            <label htmlFor="key-name" className="block text-[13px] font-book text-white/60 mb-1.5">
              Name (optional)
            </label>
            <input
              id="key-name"
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g., Production Key"
              className="w-full bg-[var(--color-input-editable)] border-none rounded px-3 py-2 text-[13px] font-book text-white placeholder:text-[var(--color-placeholder)] focus:outline-none focus:ring-1 focus:ring-white/20"
            />
          </div>

          <div className="flex items-center justify-end pt-2">
            <button
              onClick={onClose}
              className="pl-3 text-[15px] font-light text-white/40 hover:text-white transition-colors"
            >
              Cancel
            </button>
            <button
              onClick={handleCreate}
              disabled={createApiKey.isPending}
              className="pl-3 text-[15px] font-light text-white/70 hover:text-white transition-colors disabled:opacity-50"
            >
              {createApiKey.isPending ? 'Creating...' : 'Create'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

function KeyCreatedModal({
  apiKey,
  onClose,
}: {
  apiKey: ApiKeyWithSecret | null;
  onClose: () => void;
}) {
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    if (!apiKey) return;
    try {
      await navigator.clipboard.writeText(apiKey.secret);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch (error) {
      console.error('Failed to copy:', error);
    }
  };

  if (!apiKey) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div className="absolute inset-0 bg-black/60 backdrop-blur-[2px]" />
      <div className="relative z-10 w-[480px] max-w-[92vw] bg-[color:var(--color-gray-darker)] p-8 shadow-xl">
        <div className="space-y-3 mb-6">
          <h2 className="text-[17px] font-light text-white/70">Key Created</h2>
        </div>

        <div className="space-y-4">
          <div className="flex items-start gap-2 p-3 bg-[color:var(--color-brand-bg)]/50 border border-[color:var(--color-brand-border)]/40 rounded">
            <Icon name="info" size={14} className="text-[color:var(--color-brand-solid)] mt-0.5 shrink-0" />
            <p className="text-[13px] font-book text-[color:var(--color-brand-solid)]">
              Copy this key now. You won&apos;t be able to see it again.
            </p>
          </div>

          <div>
            <label className="block text-[13px] font-book text-white/60 mb-1.5">Your API Key</label>
            <div className="flex items-center gap-2">
              <code className="flex-1 bg-[var(--color-input-readonly)] rounded px-3 py-2 text-[12px] text-white/80 font-mono overflow-x-auto">
                {apiKey.secret}
              </code>
              <button
                onClick={handleCopy}
                className="shrink-0 px-3 py-2 text-[13px] font-book text-white/50 hover:text-white border border-white/10 hover:border-white/20 rounded transition-colors"
              >
                {copied ? 'Copied!' : 'Copy'}
              </button>
            </div>
          </div>

          <div className="flex items-center justify-end pt-2">
            <button
              onClick={onClose}
              className="text-[15px] font-light text-white/70 hover:text-white transition-colors"
            >
              Done
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

function ApiKeyRow({
  apiKey,
  onRevoke,
}: {
  apiKey: ApiKey;
  onRevoke: (key: ApiKey) => void;
}) {
  return (
    <div className="flex items-center gap-4 py-3 border-b border-white/5 last:border-0">
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-[13px] font-book text-white truncate">
            {apiKey.name || '(unnamed)'}
          </span>
          <StatusBadge status={apiKey.status} />
        </div>
        <div className="mt-1 flex items-center gap-3 text-[11px] text-white/35">
          <code className="font-mono">{apiKey.prefix}</code>
          <span>Created {formatDate(apiKey.created_at)}</span>
          <span>Last used {formatDate(apiKey.last_used_at)}</span>
        </div>
      </div>
      {apiKey.status === 'active' && (
        <button
          onClick={() => onRevoke(apiKey)}
          className="shrink-0 px-3 py-1 text-xs font-book text-[color:var(--color-brand-solid)] hover:text-[color:var(--color-brand-solid-hover)] border border-[color:var(--color-brand-border)]/40 hover:border-[color:var(--color-brand-border)] rounded transition-colors"
        >
          Revoke
        </button>
      )}
    </div>
  );
}

function ApiKeysContent() {
  const { data: apiKeys, isLoading, error } = useApiKeys();
  const revokeApiKey = useRevokeApiKey();

  const [showCreateModal, setShowCreateModal] = useState(false);
  const [createdKey, setCreatedKey] = useState<ApiKeyWithSecret | null>(null);
  const [keyToRevoke, setKeyToRevoke] = useState<ApiKey | null>(null);

  const handleRevoke = async () => {
    if (!keyToRevoke) return;
    try {
      await revokeApiKey.mutateAsync(keyToRevoke.id);
      setKeyToRevoke(null);
    } catch (error) {
      console.error('Failed to revoke API key:', error);
    }
  };

  const activeKeys = apiKeys?.filter((k) => k.status === 'active') ?? [];
  const revokedKeys = apiKeys?.filter((k) => k.status === 'revoked') ?? [];

  return (
    <div className="h-full overflow-y-auto">
      <div className="max-w-3xl mx-auto px-6 py-8">
        {/* Header */}
        <div className="flex items-center justify-between mb-8">
          <div>
            <h1 className="text-2xl font-light text-white">API Keys</h1>
            <p className="mt-1.5 text-sm font-book text-white/40">
              Manage keys for programmatic access to your companions.
            </p>
          </div>
          <button
            onClick={() => setShowCreateModal(true)}
            className="inline-flex items-center justify-center gap-1.5 px-4 py-2 text-sm font-book bg-white text-black rounded-full hover:bg-white/90 transition-colors"
          >
            <Icon name="plus" size={14} className="relative -top-px" />
            <span>Create Key</span>
          </button>
        </div>

        {isLoading ? (
          <div className="text-center py-16 text-[13px] text-white/40">Loading API keys...</div>
        ) : error ? (
          <div className="text-center py-16 text-[13px] text-[color:var(--color-brand-solid)]">
            Failed to load API keys. Please try again.
          </div>
        ) : apiKeys?.length === 0 ? (
          <div className="text-center py-16">
            <p className="text-[13px] text-white/40 mb-2">No API keys yet</p>
            <button
              onClick={() => setShowCreateModal(true)}
              className="text-[13px] font-book text-white/60 hover:text-white transition-colors"
            >
              Create your first API key
            </button>
          </div>
        ) : (
          <div className="space-y-8">
            {activeKeys.length > 0 && (
              <FormSection title="Active Keys" description="Keys currently in use for API authentication.">
                <div className="bg-[var(--color-dropdown-bg)] p-4">
                  {activeKeys.map((key) => (
                    <ApiKeyRow key={key.id} apiKey={key} onRevoke={setKeyToRevoke} />
                  ))}
                </div>
              </FormSection>
            )}

            {revokedKeys.length > 0 && (
              <FormSection title="Revoked Keys" description="Previously active keys that have been disabled.">
                <div className="bg-[var(--color-dropdown-bg)]/50 p-4">
                  {revokedKeys.map((key) => (
                    <ApiKeyRow key={key.id} apiKey={key} onRevoke={setKeyToRevoke} />
                  ))}
                </div>
              </FormSection>
            )}
          </div>
        )}

        {/* Usage section */}
        <div className="mt-10">
          <FormSection title="Usage" description="Include your API key in the Authorization header of your requests.">
            <code className="block bg-[var(--color-dropdown-bg)] px-4 py-3 text-[12px] text-white/60 font-mono overflow-x-auto leading-relaxed">
              curl -H &quot;Authorization: Bearer emk_your_api_key&quot; https://api.emotionmachine.ai/v1/companions
            </code>
          </FormSection>
        </div>
      </div>

      <CreateKeyModal
        open={showCreateModal}
        onClose={() => setShowCreateModal(false)}
        onCreated={(key) => {
          setShowCreateModal(false);
          setCreatedKey(key);
        }}
      />

      <KeyCreatedModal apiKey={createdKey} onClose={() => setCreatedKey(null)} />

      <ConfirmModal
        open={!!keyToRevoke}
        title="Revoke API Key?"
        message={`This will immediately disable the key "${keyToRevoke?.name || keyToRevoke?.prefix}". Any applications using this key will stop working.`}
        confirmText="Revoke"
        cancelText="Cancel"
        destructive
        onConfirm={handleRevoke}
        onCancel={() => setKeyToRevoke(null)}
      />
    </div>
  );
}

export default function ApiKeysPage() {
  return <ApiKeysContent />;
}
