'use client';

import { useState, useRef, useCallback, useEffect } from 'react';
import { useAuth } from '@clerk/nextjs';
import FormSection from '@/components/ui/form-section';
import { Textarea } from '@/components/ui/textarea';
import Icon from '@/components/ui/icon';
import ConfirmModal from '@/components/ui/confirm-modal';
import { API_CONFIG } from '@/lib/config';

const API_BASE = API_CONFIG.BASE_URL;

interface KnowledgeAsset {
  id: string;
  filename: string;
  mime_type: string;
  size_bytes: number;
  status: string;
  created_at: string;
}

interface KnowledgeTabProps {
  companionId: string | null;
  classifierSummary?: string;
  onClassifierSummaryChange?: (value: string) => void;
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

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export default function KnowledgeTab({
  companionId,
  classifierSummary,
  onClassifierSummaryChange,
}: KnowledgeTabProps) {
  const { getToken } = useAuth();

  const [assets, setAssets] = useState<KnowledgeAsset[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [assetToDelete, setAssetToDelete] = useState<KnowledgeAsset | null>(null);

  // Load knowledge assets
  const loadAssets = useCallback(async () => {
    if (!companionId) {
      setAssets([]);
      return;
    }
    setIsLoading(true);
    try {
      const token = await getToken();
      const res = await fetch(
        `${API_BASE}/api/companions/${companionId}/knowledge-assets`,
        {
          headers: { Authorization: `Bearer ${token}` },
        }
      );
      if (!res.ok) throw new Error('Failed to load knowledge assets');
      const data = await res.json();
      setAssets(data);
    } catch (err) {
      console.error('Failed to load knowledge assets:', err);
      setAssets([]);
    } finally {
      setIsLoading(false);
    }
  }, [companionId, getToken]);

  useEffect(() => {
    loadAssets();
  }, [loadAssets]);

  // Upload file
  const handleUpload = useCallback(
    async (file: File) => {
      if (!companionId) return;
      setIsUploading(true);
      setUploadError(null);

      try {
        const token = await getToken();
        const formData = new FormData();
        formData.append('file', file);

        const res = await fetch(
          `${API_BASE}/api/companions/${companionId}/knowledge-assets`,
          {
            method: 'POST',
            headers: {
              Authorization: `Bearer ${token}`,
            },
            body: formData,
          }
        );

        if (!res.ok) {
          const detail = await res.text();
          throw new Error(detail || `Upload failed: ${res.status}`);
        }

        const asset = await res.json();

        // Trigger ingestion to process the uploaded asset
        const ingestRes = await fetch(
          `${API_BASE}/api/companions/${companionId}/knowledge`,
          {
            method: 'POST',
            headers: {
              Authorization: `Bearer ${token}`,
              'Content-Type': 'application/json',
            },
            body: JSON.stringify({
              type: 'asset',
              asset_id: asset.id,
            }),
          }
        );

        if (!ingestRes.ok) {
          const detail = await ingestRes.text();
          throw new Error(detail || `Ingestion failed: ${ingestRes.status}`);
        }

        await loadAssets();
      } catch (err) {
        setUploadError(err instanceof Error ? err.message : 'Upload failed');
      } finally {
        setIsUploading(false);
      }
    },
    [companionId, getToken, loadAssets]
  );

  // Delete asset
  const handleDeleteAsset = async () => {
    if (!assetToDelete || !companionId) return;
    try {
      const token = await getToken();
      await fetch(
        `${API_BASE}/api/companions/${companionId}/knowledge-assets/${assetToDelete.id}`,
        {
          method: 'DELETE',
          headers: { Authorization: `Bearer ${token}` },
        }
      );
      await loadAssets();
    } catch (err) {
      console.error('Failed to delete asset:', err);
    } finally {
      setAssetToDelete(null);
    }
  };

  if (!companionId) {
    return (
      <div className="flex items-center justify-center h-full text-white/40 text-sm">
        Select a companion to manage knowledge
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Classifier Summary */}
      <FormSection
        title="Classifier Summary"
        description="Help the classifier understand when to activate this layer. Describe what's in your knowledge base. This description tells the classifier when to enable the knowledge layer."
      >
        <Textarea
          value={classifierSummary || 'Documentation, FAQs, knowledge sources'}
          onChange={(e) => onClassifierSummaryChange?.(e.target.value)}
          minHeight={100}
        />
      </FormSection>

      <FormSection
        title="Knowledge Assets"
        description="Upload documents to enhance your companion's knowledge"
      >
        <div className="space-y-2">
          {isLoading ? (
            <p className="text-xs text-white/40">Loading assets...</p>
          ) : assets.length === 0 ? (
            <p className="text-xs text-white/40">No knowledge assets uploaded</p>
          ) : (
            <div className="space-y-2">
              {assets.map((asset) => (
                <div
                  key={asset.id}
                  className="py-3 px-3 bg-white/5 rounded"
                >
                  <div className="flex items-center justify-between">
                    <div className="flex-1 min-w-0">
                      <p className="text-sm text-white font-medium truncate">
                        {asset.filename}
                      </p>
                      <div className="flex items-center gap-2 mt-1">
                        <span className="text-xs text-white/40">
                          {formatFileSize(asset.size_bytes)}
                        </span>
                        <span className="text-xs text-white/30">•</span>
                        <span className="text-xs text-white/40">
                          {formatDate(asset.created_at)}
                        </span>
                        {asset.status !== 'uploaded' && (
                          <>
                            <span className="text-xs text-white/30">•</span>
                            <span
                              className={`inline-flex items-center px-1.5 py-0.5 text-xs rounded ${
                                asset.status === 'ready'
                                  ? 'bg-green-500/20 text-green-400'
                                  : asset.status === 'processing'
                                  ? 'bg-yellow-500/20 text-yellow-400'
                                  : asset.status === 'failed'
                                  ? 'bg-red-500/20 text-red-400'
                                  : 'bg-white/10 text-white/60'
                              }`}
                            >
                              {asset.status}
                            </span>
                          </>
                        )}
                      </div>
                    </div>
                    <div className="flex items-center gap-1 ml-3 shrink-0">
                      <button
                        onClick={() => setAssetToDelete(asset)}
                        className="p-1.5 text-white/40 hover:text-white/70"
                        title="Delete asset"
                      >
                        <Icon name="trash" size={16} />
                      </button>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}

          <input
            ref={fileInputRef}
            type="file"
            accept=".txt,.md,.pdf,.json,.csv,.jsonl"
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
            {isUploading ? 'Uploading...' : '+ Upload Knowledge File'}
          </button>
          {uploadError && (
            <p className="text-xs text-[color:var(--color-brand-solid)]">{uploadError}</p>
          )}
        </div>
      </FormSection>

      <ConfirmModal
        open={!!assetToDelete}
        title="Delete Knowledge Asset?"
        message={`Delete "${assetToDelete?.filename}"? This will remove the document from your companion's knowledge base.`}
        confirmText="Delete"
        cancelText="Cancel"
        destructive
        onConfirm={handleDeleteAsset}
        onCancel={() => setAssetToDelete(null)}
      />
    </div>
  );
}
