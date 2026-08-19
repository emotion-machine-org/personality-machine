'use client';

import {SharePanelContent} from '@/components/dashboard/share-panel';
import {useSelectedCompanion} from '@/components/providers';
import {useCompanion} from '@/hooks/useCompanions';

export default function ShareView() {
    const {selectedCompanionId} = useSelectedCompanion();
    const {data: companionConfig} = useCompanion(selectedCompanionId);

    return (
        <SharePanelContent
            companionId={selectedCompanionId}
            companionConfig={companionConfig ?? null}
            variant="page"
        />
    );
}
