'use client';

import { useState, useRef, useCallback, useEffect } from 'react';
import { useAuth } from '@clerk/nextjs';
import FormSection from '@/components/ui/form-section';
import Icon from '@/components/ui/icon';
import Dropdown from '@/components/ui/dropdown';
import ConfirmModal from '@/components/ui/confirm-modal';
import {
  useProjectSecrets,
  useCreateProjectSecret,
  useDeleteProjectSecret,
} from '@/hooks/useProjectSecrets';
import type { ProjectSecret } from '@/lib/types';
import { API_CONFIG } from '@/lib/config';

const API_BASE = API_CONFIG.BASE_URL;

interface ToolSpec {
  id: string;
  name: string | null;
  secrets_config?: Record<string, string>;
}

interface ToolsTabProps {
  companionId: string | null;
}

function formatDate(dateString: string): string {
  const date = new Date(dateString);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffDays = Math.floor(diffMs / 86400000);
  if (diffDays < 1) return 'Today';
  if (diffDays < 7) return `${diffDays}d ago`;
  return date.toLocaleDateString();
}

// ─────────────────────────────────────────────────────────────────────────────
// Create Secret Modal
// ─────────────────────────────────────────────────────────────────────────────
function CreateSecretModal({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: () => void;
}) {
  const [name, setName] = useState('');
  const [value, setValue] = useState('');
  const [description, setDescription] = useState('');
  const [error, setError] = useState<string | null>(null);
  const createSecret = useCreateProjectSecret();

  const handleCreate = async () => {
    setError(null);
    if (!name.trim()) {
      setError('Name is required');
      return;
    }
    if (!value.trim()) {
      setError('Value is required');
      return;
    }
    if (!/^[a-zA-Z][a-zA-Z0-9_]*$/.test(name.trim())) {
      setError('Name must start with a letter and contain only letters, numbers, and underscores');
      return;
    }

    try {
      await createSecret.mutateAsync({
        name: name.trim(),
        value: value,
        description: description.trim() || undefined,
      });
      setName('');
      setValue('');
      setDescription('');
      onCreated();
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create secret');
    }
  };

  const handleClose = () => {
    setName('');
    setValue('');
    setDescription('');
    setError(null);
    onClose();
  };

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <button
        aria-label="Close"
        className="absolute inset-0 bg-black/60 backdrop-blur-[2px]"
        onClick={handleClose}
      />
      <div className="relative z-10 w-[400px] max-w-[92vw] rounded-[4px] border border-white/20 bg-black p-5 shadow-xl">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-base font-medium text-white">Create Secret</h3>
          <button onClick={handleClose} className="text-white/60 hover:text-white">
            <Icon name="x" size={16} />
          </button>
        </div>

        <div className="space-y-4">
          {error && (
            <div className="p-2 bg-[color:var(--color-brand-bg)]/50 border border-[color:var(--color-brand-border)]/40 rounded">
              <p className="text-xs text-[color:var(--color-brand-solid)]">{error}</p>
            </div>
          )}

          <div>
            <label htmlFor="secret-name" className="block text-xs text-white/60 mb-1">
              Name
            </label>
            <input
              id="secret-name"
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g., openai_api_key"
              className="w-full bg-[color:var(--color-gray-darker)] border border-white/10 rounded px-3 py-2 text-sm text-white placeholder:text-white/30 focus:outline-none focus:border-white/30 font-mono"
            />
          </div>

          <div>
            <label htmlFor="secret-value" className="block text-xs text-white/60 mb-1">
              Value
            </label>
            <input
              id="secret-value"
              type="password"
              value={value}
              onChange={(e) => setValue(e.target.value)}
              placeholder="Enter secret value"
              className="w-full bg-[color:var(--color-gray-darker)] border border-white/10 rounded px-3 py-2 text-sm text-white placeholder:text-white/30 focus:outline-none focus:border-white/30"
            />
          </div>

          <div>
            <label htmlFor="secret-desc" className="block text-xs text-white/60 mb-1">
              Description (optional)
            </label>
            <input
              id="secret-desc"
              type="text"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="What is this secret for?"
              className="w-full bg-[color:var(--color-gray-darker)] border border-white/10 rounded px-3 py-2 text-sm text-white placeholder:text-white/30 focus:outline-none focus:border-white/30"
            />
          </div>

          <div className="flex justify-end gap-2 pt-2">
            <button
              onClick={handleClose}
              className="px-3 py-1.5 text-sm text-white/70 hover:text-white"
            >
              Cancel
            </button>
            <button
              onClick={handleCreate}
              disabled={createSecret.isPending}
              className="px-4 py-1.5 text-sm font-medium bg-white text-black rounded hover:bg-white/90 disabled:opacity-50"
            >
              {createSecret.isPending ? 'Creating...' : 'Create'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Configure Secrets Modal (link secrets to spec)
// ─────────────────────────────────────────────────────────────────────────────
function ConfigureSecretsModal({
  spec,
  secrets,
  onClose,
  onSave,
}: {
  spec: ToolSpec | null;
  secrets: ProjectSecret[];
  onClose: () => void;
  onSave: (specId: string, secretsConfig: Record<string, string>) => Promise<void>;
}) {
  const [config, setConfig] = useState<Array<{ header: string; secretName: string }>>([]);
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [initializedForSpec, setInitializedForSpec] = useState<string | null>(null);

  useEffect(() => {
    // Only reset config when opening modal for a different spec
    if (spec && spec.id !== initializedForSpec) {
      if (spec.secrets_config && Object.keys(spec.secrets_config).length > 0) {
        setConfig(
          Object.entries(spec.secrets_config).map(([header, secretName]) => ({
            header,
            secretName,
          }))
        );
      } else {
        setConfig([]);
      }
      setInitializedForSpec(spec.id);
    } else if (!spec) {
      setInitializedForSpec(null);
    }
  }, [spec, initializedForSpec]);

  const addRow = () => {
    // Default to "Authorization" as it's the most common header
    setConfig([...config, { header: 'Authorization', secretName: '' }]);
  };

  const removeRow = (index: number) => {
    setConfig(config.filter((_, i) => i !== index));
  };

  const updateRow = (index: number, field: 'header' | 'secretName', value: string) => {
    setConfig(prev => prev.map((row, i) =>
      i === index ? { ...row, [field]: value } : row
    ));
  };

  const handleSave = async () => {
    if (!spec) return;
    setError(null);

    // Validate rows - check for incomplete entries
    for (const row of config) {
      if (row.header.trim() && !row.secretName) {
        setError(`Please select a secret for header "${row.header}"`);
        return;
      }
      if (!row.header.trim() && row.secretName) {
        setError('Please enter a header name for each secret');
        return;
      }
    }

    // Build secrets_config object
    const secretsConfig: Record<string, string> = {};
    for (const row of config) {
      if (row.header.trim() && row.secretName) {
        secretsConfig[row.header.trim()] = row.secretName;
      }
    }

    setIsSaving(true);
    try {
      await onSave(spec.id, secretsConfig);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save');
    } finally {
      setIsSaving(false);
    }
  };

  if (!spec) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <button
        aria-label="Close"
        className="absolute inset-0 bg-black/60 backdrop-blur-[2px]"
        onClick={onClose}
      />
      <div className="relative z-10 w-[480px] max-w-[92vw] rounded-[4px] border border-white/20 bg-black p-5 shadow-xl">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-base font-medium text-white">Configure Secrets</h3>
          <button onClick={onClose} className="text-white/60 hover:text-white">
            <Icon name="x" size={16} />
          </button>
        </div>

        <p className="text-xs text-white/50 mb-4">
          Map HTTP headers to project secrets for <span className="text-white/80">{spec.name}</span>
        </p>

        {error && (
          <div className="mb-4 p-2 bg-[color:var(--color-brand-bg)]/50 border border-[color:var(--color-brand-border)]/40 rounded">
            <p className="text-xs text-[color:var(--color-brand-solid)]">{error}</p>
          </div>
        )}

        <div className="space-y-2 mb-4">
          {config.length === 0 ? (
            <p className="text-xs text-white/40 py-4 text-center">No secrets configured</p>
          ) : (
            <>
              <div className="grid grid-cols-[1fr_1fr_32px] gap-2 text-xs text-white/50 px-1">
                <span>Header Name</span>
                <span>Secret</span>
                <span></span>
              </div>
              {config.map((row, i) => (
                <div key={i} className="grid grid-cols-[1fr_1fr_32px] gap-2">
                  <input
                    type="text"
                    value={row.header}
                    onChange={(e) => updateRow(i, 'header', e.target.value)}
                    placeholder="Authorization"
                    className="bg-[color:var(--color-gray-darker)] border border-white/10 rounded px-2 py-1.5 text-sm text-white placeholder:text-white/30 focus:outline-none focus:border-white/30"
                  />
                  <Dropdown
                    options={secrets.map(s => ({ value: s.secret_name, label: s.secret_name }))}
                    value={row.secretName}
                    onChange={(value) => updateRow(i, 'secretName', value)}
                    placeholder="Select secret..."
                    size="sm"
                  />
                  <button
                    onClick={() => removeRow(i)}
                    className="flex items-center justify-center text-white/40 hover:text-white/70"
                  >
                    <Icon name="x" size={14} />
                  </button>
                </div>
              ))}
            </>
          )}
        </div>

        <button
          onClick={addRow}
          className="w-full py-2 text-xs text-white/50 hover:text-white/80 border border-dashed border-white/20 hover:border-white/40 transition-colors"
        >
          + Add Header Mapping
        </button>

        <div className="flex justify-end gap-2 mt-6">
          <button
            onClick={onClose}
            className="px-3 py-1.5 text-sm text-white/70 hover:text-white"
          >
            Cancel
          </button>
          <button
            onClick={handleSave}
            disabled={isSaving}
            className="px-4 py-1.5 text-sm font-medium bg-white text-black rounded hover:bg-white/90 disabled:opacity-50"
          >
            {isSaving ? 'Saving...' : 'Save'}
          </button>
        </div>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Main ToolsTab Component
// ─────────────────────────────────────────────────────────────────────────────
export default function ToolsTab({
  companionId,
}: ToolsTabProps) {
  const { getToken } = useAuth();

  // Secrets
  const { data: secrets = [], isLoading: secretsLoading } = useProjectSecrets();
  const deleteSecret = useDeleteProjectSecret();
  const [showCreateSecretModal, setShowCreateSecretModal] = useState(false);
  const [secretToDelete, setSecretToDelete] = useState<ProjectSecret | null>(null);

  // Tool specs
  const [toolSpecs, setToolSpecs] = useState<ToolSpec[]>([]);
  const [specsLoading, setSpecsLoading] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [specToDelete, setSpecToDelete] = useState<ToolSpec | null>(null);
  const [specToConfigure, setSpecToConfigure] = useState<ToolSpec | null>(null);

  // Load tool specs
  const loadToolSpecs = useCallback(async () => {
    if (!companionId) {
      setToolSpecs([]);
      return;
    }
    setSpecsLoading(true);
    try {
      const token = await getToken();
      const res = await fetch(`${API_BASE}/api/tools?companion_id=${companionId}`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!res.ok) throw new Error('Failed to load tool specs');
      const data = await res.json();
      setToolSpecs(
        data.map((item: { id: string; spec_name?: string; secrets_config?: Record<string, string> | string | null }) => {
          // Handle secrets_config being returned as JSON string
          let parsedConfig: Record<string, string> | undefined;
          if (item.secrets_config) {
            if (typeof item.secrets_config === 'string') {
              try {
                parsedConfig = JSON.parse(item.secrets_config);
              } catch {
                parsedConfig = undefined;
              }
            } else {
              parsedConfig = item.secrets_config;
            }
          }
          return {
            id: item.id,
            name: item.spec_name || 'OpenAPI spec',
            secrets_config: parsedConfig,
          };
        })
      );
    } catch (err) {
      console.error('Failed to load tool specs:', err);
      setToolSpecs([]);
    } finally {
      setSpecsLoading(false);
    }
  }, [companionId, getToken]);

  useEffect(() => {
    loadToolSpecs();
  }, [loadToolSpecs]);

  // Upload spec
  const handleUpload = useCallback(
    async (file: File) => {
      if (!companionId) return;
      setIsUploading(true);
      setUploadError(null);

      try {
        const text = await file.text();
        let parsed: unknown;
        try {
          parsed = JSON.parse(text);
        } catch {
          setUploadError('Invalid JSON file');
          return;
        }

        const token = await getToken();
        const res = await fetch(`${API_BASE}/api/tools/index`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            Authorization: `Bearer ${token}`,
          },
          body: JSON.stringify({
            companion_id: companionId,
            spec_name: file.name.replace(/\.json$/i, ''),
            openapi_spec: parsed,
          }),
        });

        if (!res.ok) {
          const detail = await res.text();
          throw new Error(detail || `Upload failed: ${res.status}`);
        }

        await loadToolSpecs();
      } catch (err) {
        setUploadError(err instanceof Error ? err.message : 'Upload failed');
      } finally {
        setIsUploading(false);
      }
    },
    [companionId, getToken, loadToolSpecs]
  );

  // Delete spec
  const handleDeleteSpec = async () => {
    if (!specToDelete || !companionId) return;
    try {
      const token = await getToken();
      await fetch(`${API_BASE}/api/tools/${specToDelete.id}?companion_id=${companionId}`, {
        method: 'DELETE',
        headers: { Authorization: `Bearer ${token}` },
      });
      await loadToolSpecs();
    } catch (err) {
      console.error('Failed to delete spec:', err);
    } finally {
      setSpecToDelete(null);
    }
  };

  // Update secrets config
  const handleSaveSecretsConfig = async (specId: string, secretsConfig: Record<string, string>) => {
    if (!companionId) return;
    const token = await getToken();
    const res = await fetch(
      `${API_BASE}/api/tools/${specId}/secrets-config?companion_id=${companionId}`,
      {
        method: 'PATCH',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({ secrets_config: secretsConfig }),
      }
    );
    if (!res.ok) {
      const detail = await res.text();
      throw new Error(detail || 'Failed to update secrets config');
    }
    await loadToolSpecs();
  };

  // Delete secret
  const handleDeleteSecret = async () => {
    if (!secretToDelete) return;
    try {
      await deleteSecret.mutateAsync(secretToDelete.secret_name);
      // Refresh tool specs since backend auto-removes secret references
      await loadToolSpecs();
    } catch (err) {
      console.error('Failed to delete secret:', err);
    } finally {
      setSecretToDelete(null);
    }
  };

  if (!companionId) {
    return (
      <div className="flex items-center justify-center h-full text-white/40 text-sm">
        Select a companion to manage tools
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Project Secrets Section */}
      <FormSection
        title="Project Secrets"
        description="Encrypted credentials available to all tool specs"
      >
        <div className="space-y-2">
          {secretsLoading ? (
            <p className="text-xs text-white/40">Loading secrets...</p>
          ) : secrets.length === 0 ? (
            <p className="text-xs text-white/40">No secrets defined</p>
          ) : (
            <div className="space-y-1">
              {secrets.map((secret) => (
                <div
                  key={secret.id}
                  className="flex items-center justify-between py-3 px-4 bg-white/5 rounded"
                >
                  <div className="flex-1 min-w-0">
                    <code className="text-sm text-white font-mono">{secret.secret_name}</code>
                    {secret.description && (
                      <p className="text-xs text-white/40 truncate mt-1">{secret.description}</p>
                    )}
                  </div>
                  <div className="flex items-center gap-2 ml-2">
                    <span className="text-xs text-white/30">{formatDate(secret.created_at)}</span>
                    <button
                      onClick={() => setSecretToDelete(secret)}
                      className="text-white/40 hover:text-white/70"
                    >
                      <Icon name="trash" size={14} />
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}

          <button
            onClick={() => setShowCreateSecretModal(true)}
            className="w-full py-2 text-xs text-white/50 hover:text-white/80 border border-dashed border-white/20 hover:border-white/40 transition-colors"
          >
            + Add Secret
          </button>
        </div>
      </FormSection>

      {/* Tool Specs Section */}
      <FormSection
        title="Tool Specs"
        description="OpenAPI specifications that define available tools"
      >
        <div className="space-y-2">
          {specsLoading ? (
            <p className="text-xs text-white/40">Loading specs...</p>
          ) : toolSpecs.length === 0 ? (
            <p className="text-xs text-white/40">No tool specs uploaded</p>
          ) : (
            <div className="space-y-2">
              {toolSpecs.map((spec) => {
                const linkedSecrets = spec.secrets_config
                  ? Object.values(spec.secrets_config)
                  : [];
                return (
                  <div
                    key={spec.id}
                    className="py-3 px-3 bg-white/5 rounded"
                  >
                    <div className="flex items-center justify-between">
                      <div className="flex-1 min-w-0">
                        <p className="text-sm text-white font-medium truncate">{spec.name}</p>
                        {linkedSecrets.length > 0 ? (
                          <div className="flex flex-wrap gap-1 mt-1">
                            {linkedSecrets.map((secretName) => (
                              <span
                                key={secretName}
                                className="inline-flex items-center px-1.5 py-0.5 text-xs bg-white/10 text-white/60 rounded font-mono"
                              >
                                {secretName}
                              </span>
                            ))}
                          </div>
                        ) : (
                          <p className="text-xs text-white/30 mt-0.5">No secrets linked</p>
                        )}
                      </div>
                      <div className="flex items-center gap-1 ml-3 shrink-0">
                        <button
                          onClick={() => setSpecToConfigure(spec)}
                          className="p-1.5 text-white/40 hover:text-white/70"
                          title="Link secrets"
                        >
                          <Icon name="link" size={16} />
                        </button>
                        <button
                          onClick={() => setSpecToDelete(spec)}
                          className="p-1.5 text-white/40 hover:text-white/70"
                          title="Delete spec"
                        >
                          <Icon name="trash" size={16} />
                        </button>
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          )}

          <input
            ref={fileInputRef}
            type="file"
            accept=".json,application/json"
            className="hidden"
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) {
                handleUpload(file);
                e.target.value = '';
              }
            }}
          />
          <button
            onClick={() => fileInputRef.current?.click()}
            disabled={isUploading}
            className="w-full py-2 text-xs text-white/50 hover:text-white/80 border border-dashed border-white/20 hover:border-white/40 transition-colors disabled:opacity-50"
          >
            {isUploading ? 'Uploading...' : '+ Upload OpenAPI JSON'}
          </button>
          {uploadError && (
            <p className="text-xs text-[color:var(--color-brand-solid)]">{uploadError}</p>
          )}
        </div>
      </FormSection>

      {/* Modals */}
      <CreateSecretModal
        open={showCreateSecretModal}
        onClose={() => setShowCreateSecretModal(false)}
        onCreated={() => {}}
      />

      <ConfigureSecretsModal
        spec={specToConfigure}
        secrets={secrets}
        onClose={() => setSpecToConfigure(null)}
        onSave={handleSaveSecretsConfig}
      />

      <ConfirmModal
        open={!!secretToDelete}
        title="Delete Secret?"
        message={`Delete "${secretToDelete?.secret_name}"? Any tool specs using this secret will fail.`}
        confirmText="Delete"
        cancelText="Cancel"
        destructive
        onConfirm={handleDeleteSecret}
        onCancel={() => setSecretToDelete(null)}
      />

      <ConfirmModal
        open={!!specToDelete}
        title="Delete Tool Spec?"
        message={`Delete "${specToDelete?.name}"? This will remove all indexed operations.`}
        confirmText="Delete"
        cancelText="Cancel"
        destructive
        onConfirm={handleDeleteSpec}
        onCancel={() => setSpecToDelete(null)}
      />
    </div>
  );
}
