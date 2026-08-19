'use client';

import { useState, useEffect } from 'react';
import { useSelectedCompanion } from '@/components/providers';
import { useCompanions } from '@/hooks/useCompanions';
import RelationshipList from './relationship-list';
import RelationshipDetail from './relationship-detail';
import type { RelationshipSummary } from '@/lib/types';

export default function RelationshipsView() {
  const { selectedCompanionId, setSelectedCompanionId } = useSelectedCompanion();
  const { data: companions } = useCompanions();
  const [selectedRelationship, setSelectedRelationship] = useState<RelationshipSummary | null>(null);

  // Auto-select first companion when none is selected (e.g., after page refresh)
  useEffect(() => {
    if (companions && companions.length > 0 && !selectedCompanionId) {
      const urlParams = new URLSearchParams(window.location.search);
      const companionFromUrl = urlParams.get('companion');
      if (!companionFromUrl) {
        setSelectedCompanionId(companions[0].id);
      }
    }
  }, [companions, selectedCompanionId, setSelectedCompanionId]);

  if (!selectedCompanionId) {
    return (
      <div className="h-full flex items-center justify-center">
        <p className="text-[13px] text-white/40">Select a companion to view relationships</p>
      </div>
    );
  }

  return (
    <div className="h-full flex">
      {/* Left panel: Relationship list */}
      <div className="w-[320px] min-w-[280px] border-r border-white/10 flex flex-col h-full bg-[var(--color-panel-bg)]">
        <div className="px-3 py-2.5 border-b border-white/10">
          <h1 className="text-sm font-book text-[var(--color-title-text)]">Relationships</h1>
        </div>
        <div className="flex-1 min-h-0">
          <RelationshipList
            companionId={selectedCompanionId}
            selectedId={selectedRelationship?.id ?? null}
            onSelect={setSelectedRelationship}
          />
        </div>
      </div>

      {/* Right panel: Relationship detail */}
      <div className="flex-1 min-w-0 h-full">
        {selectedRelationship ? (
          <RelationshipDetail
            companionId={selectedCompanionId}
            relationship={selectedRelationship}
          />
        ) : (
          <div className="h-full flex flex-col items-center justify-center">
            <h2 className="text-xl font-light text-white/20 mb-1">
              Select a relationship
            </h2>
            <p className="text-[13px] text-white/15">
              Choose a user from the list to view their conversation
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
