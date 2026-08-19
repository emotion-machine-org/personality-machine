'use client';

import { useState, useCallback, useMemo } from 'react';
import { useAuth } from '@clerk/nextjs';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import FormSection from '@/components/ui/form-section';
import Icon from '@/components/ui/icon';
import ConfirmModal from '@/components/ui/confirm-modal';
import { API_CONFIG } from '@/lib/config';
import { useCompanion, useUpdateCompanion } from '@/hooks/useCompanions';

const API_BASE = API_CONFIG.BASE_URL;

// Types (matching behavior-testing.tsx)
type TriggerValue = string | { type: string; [key: string]: unknown };

interface BehaviorInfo {
  id: string;
  key: string;
  name: string;
  description: string | null;
  source_code: string;
  version: number;
  triggers: TriggerValue[];
  priority: 'sync' | 'async';
  enabled: boolean;
  classifier_hint: string | null;
}

interface BehaviorsTabProps {
  companionId: string | null;
}

function formatTrigger(trigger: TriggerValue): string {
  if (typeof trigger === 'string') return trigger;
  const { type, ...rest } = trigger;
  if (type === 'idle' && 'minutes' in rest) return `idle:${rest.minutes}`;
  if (type === 'cron' && 'expression' in rest) return `cron:${rest.expression}`;
  if (type === 'every_n' && 'n' in rest) return `every_n:${rest.n}`;
  if (type === 'turn_count' && 'turns' in rest) return `turn:${(rest.turns as number[]).join(',')}`;
  if (type === 'keyword' && 'keywords' in rest) return `keyword:${(rest.keywords as string[]).join(',')}`;
  return type;
}

export default function BehaviorsTab({ companionId }: BehaviorsTabProps) {
  const { getToken } = useAuth();
  const queryClient = useQueryClient();
  const { data: companionConfig } = useCompanion(companionId);
  const { mutateAsync: updateCompanionConfig, isPending: isUpdatingCompanionConfig } =
    useUpdateCompanion();

  const [behaviorToDelete, setBehaviorToDelete] = useState<BehaviorInfo | null>(null);
  const [togglingBehaviorId, setTogglingBehaviorId] = useState<string | null>(null);
  const [isTogglingBehaviorLayer, setIsTogglingBehaviorLayer] = useState(false);

  // Fetch behaviors linked to this companion (not all project behaviors)
  const { data: behaviors = [], isLoading, error } = useQuery<BehaviorInfo[]>({
    queryKey: ['companion-behaviors', companionId],
    queryFn: async () => {
      if (!companionId) return [];
      const token = await getToken();
      const res = await fetch(
        `${API_BASE}/api/context-engine-testing/companions/${companionId}/behaviors?linked_only=true`,
        { headers: { Authorization: `Bearer ${token}` } }
      );
      if (!res.ok) {
        // 404 means no behaviors registered - that's fine
        if (res.status === 404) return [];
        // Other errors should be thrown
        const errorData = await res.json().catch(() => ({}));
        throw new Error(errorData.detail || `Failed to load behaviors (${res.status})`);
      }
      const data = await res.json();
      return data.behaviors || [];
    },
    enabled: !!companionId,
  });

  // Toggle behavior enabled state
  const toggleBehavior = useMutation({
    mutationFn: async ({ behavior, enabled }: { behavior: BehaviorInfo; enabled: boolean }) => {
      const token = await getToken();
      const triggers = behavior.triggers.map(formatTrigger);
      const res = await fetch(`${API_BASE}/api/context-engine-testing/behaviors/register`, {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          companion_id: companionId,
          key: behavior.key,
          name: behavior.name,
          description: behavior.description,
          source_code: behavior.source_code,
          triggers,
          priority: behavior.priority,
          enabled,
          classifier_hint: behavior.classifier_hint,
        }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || 'Failed to update behavior');
      }
      return res.json();
    },
    onMutate: async ({ behavior }) => {
      setTogglingBehaviorId(behavior.id);
    },
    onSettled: () => {
      setTogglingBehaviorId(null);
      queryClient.invalidateQueries({ queryKey: ['companion-behaviors', companionId] });
    },
  });

  // Delete behavior with optimistic update
  const deleteBehavior = useCallback(async () => {
    if (!behaviorToDelete || !companionId) return;

    const behaviorKey = behaviorToDelete.key;
    const behaviorId = behaviorToDelete.id;

    // Close modal immediately
    setBehaviorToDelete(null);

    // Optimistically remove from cache
    const previousBehaviors = queryClient.getQueryData<BehaviorInfo[]>(['companion-behaviors', companionId]);
    queryClient.setQueryData<BehaviorInfo[]>(
      ['companion-behaviors', companionId],
      (old) => old?.filter((b) => b.id !== behaviorId) ?? []
    );

    try {
      const token = await getToken();
      const res = await fetch(
        `${API_BASE}/api/context-engine-testing/behaviors/${behaviorKey}?companion_id=${companionId}`,
        {
          method: 'DELETE',
          headers: { Authorization: `Bearer ${token}` },
        }
      );
      if (!res.ok) {
        throw new Error('Delete failed');
      }
    } catch (err) {
      console.error('Failed to delete behavior:', err);
      // Rollback on error
      queryClient.setQueryData(['companion-behaviors', companionId], previousBehaviors);
    }
  }, [behaviorToDelete, companionId, getToken, queryClient]);

  const behaviorLayerEnabled = useMemo(() => {
    const layers = companionConfig?.layers || [];
    const behaviorLayer = layers.find((layer) => {
      const key = (layer.key || '').toLowerCase();
      const category = (layer.category || '').toLowerCase();
      return key === 'actions' || key === 'behaviors' || category === 'actions' || category === 'behaviors';
    });
    return !!behaviorLayer?.enabled;
  }, [companionConfig]);

  const handleBehaviorLayerToggle = useCallback(
    async (enabled: boolean) => {
      if (!companionId || !companionConfig) return;

      const layers = [...(companionConfig.layers || [])];
      let foundLayer = false;

      for (const layer of layers) {
        const key = (layer.key || '').toLowerCase();
        const category = (layer.category || '').toLowerCase();
        if (key === 'actions' || key === 'behaviors' || category === 'actions' || category === 'behaviors') {
          layer.enabled = enabled;
          layer.key = 'actions';
          layer.category = 'actions';
          foundLayer = true;
        }
      }

      if (!foundLayer) {
        layers.push({
          key: 'actions',
          category: 'actions',
          enabled,
          priority: 30,
          params: {},
          timeout_ms: null,
          reserved_tokens: null,
          depends_on: [],
        });
      }

      setIsTogglingBehaviorLayer(true);
      try {
        await updateCompanionConfig({
          id: companionId,
          config: {
            ...companionConfig,
            layers,
          },
        });
      } finally {
        setIsTogglingBehaviorLayer(false);
        queryClient.invalidateQueries({ queryKey: ['companions'] });
      }
    },
    [companionConfig, companionId, queryClient, updateCompanionConfig]
  );

  if (!companionId) {
    return (
      <div className="flex items-center justify-center h-full text-white/40 text-sm">
        Select a companion to manage behaviors
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <FormSection
        title="Behaviors"
        description="Automated actions that run during conversations"
        toggle={{
          checked: behaviorLayerEnabled,
          onCheckedChange: handleBehaviorLayerToggle,
          disabled: isTogglingBehaviorLayer || isUpdatingCompanionConfig,
        }}
      >
        <div className="space-y-2.5">
          <p className="text-[11px] text-white/40">
            Controls whether linked behaviors run during chat turns.
          </p>

          {isLoading ? (
            <div className="flex items-center justify-center py-8">
              <div className="w-5 h-5 border-2 border-white/20 border-t-white/60 rounded-full animate-spin" />
            </div>
          ) : error ? (
            <div className="py-4 px-3 bg-red-500/10 border border-red-500/20 rounded">
              <p className="text-sm text-red-400">
                {error instanceof Error ? error.message : 'Failed to load behaviors'}
              </p>
            </div>
          ) : behaviors.length === 0 ? (
            <p className="text-xs text-white/40">No behaviors registered</p>
          ) : (
            <div className="space-y-2">
              {behaviors.map((behavior) => (
                <div
                  key={behavior.id}
                  className="py-3 px-3 bg-white/5 rounded"
                >
                  <div className="flex items-center justify-between gap-2">
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <p className="text-sm text-white font-medium truncate">
                          {behavior.name}
                        </p>
                        <div className="flex items-center gap-1 shrink-0">
                          {behavior.priority === 'sync' && (
                            <span className="text-[10px] px-1.5 py-0.5 bg-yellow-500/20 text-yellow-400 rounded">
                              Sync
                            </span>
                          )}
                          {behavior.enabled ? (
                            <span className="text-[10px] px-1.5 py-0.5 bg-green-500/20 text-green-400 rounded">
                              On
                            </span>
                          ) : (
                            <span className="text-[10px] px-1.5 py-0.5 bg-white/10 text-white/40 rounded">
                              Off
                            </span>
                          )}
                        </div>
                      </div>
                      <p className="text-xs text-white/40 mt-0.5 font-mono">
                        {behavior.key}
                      </p>
                      {behavior.triggers.length > 0 && (
                        <div className="flex flex-wrap gap-1 mt-1.5">
                          {behavior.triggers.slice(0, 3).map((trigger, i) => (
                            <span
                              key={i}
                              className="text-[10px] px-1.5 py-0.5 bg-white/10 text-white/60 rounded"
                            >
                              {formatTrigger(trigger)}
                            </span>
                          ))}
                          {behavior.triggers.length > 3 && (
                            <span className="text-[10px] text-white/40">
                              +{behavior.triggers.length - 3}
                            </span>
                          )}
                        </div>
                      )}
                    </div>
                    <div className="flex items-center gap-1 shrink-0">
                      <button
                        onClick={() =>
                          toggleBehavior.mutate({
                            behavior,
                            enabled: !behavior.enabled,
                          })
                        }
                        disabled={togglingBehaviorId === behavior.id}
                        className={`p-1.5 disabled:opacity-50 ${
                          behavior.enabled
                            ? 'text-green-400 hover:text-green-300'
                            : 'text-white/40 hover:text-white/70'
                        }`}
                        title={behavior.enabled ? 'Disable' : 'Enable'}
                      >
                        <Icon
                          name={behavior.enabled ? 'check' : 'x'}
                          size={16}
                        />
                      </button>
                      <button
                        onClick={() => setBehaviorToDelete(behavior)}
                        className="p-1.5 text-white/40 hover:text-white/70"
                        title="Delete behavior"
                      >
                        <Icon name="trash" size={16} />
                      </button>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}

          <a
            href={`/test-behaviors${companionId ? `?companion=${companionId}` : ''}`}
            className="w-full py-2 text-xs text-white/50 hover:text-white/80 border border-dashed border-white/20 hover:border-white/40 transition-colors flex items-center justify-center gap-1.5"
          >
            <Icon name="up-right-arrow" size={12} />
            Open Behavior Editor
          </a>
        </div>
      </FormSection>

      <ConfirmModal
        open={!!behaviorToDelete}
        title="Delete Behavior?"
        message={`Delete "${behaviorToDelete?.name}"? This will remove the behavior from your project.`}
        confirmText="Delete"
        cancelText="Cancel"
        destructive
        onConfirm={deleteBehavior}
        onCancel={() => setBehaviorToDelete(null)}
      />
    </div>
  );
}
