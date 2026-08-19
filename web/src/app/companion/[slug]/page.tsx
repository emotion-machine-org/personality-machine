import { notFound } from 'next/navigation';
import type { PublicShareMeta } from '@/lib/api';
import { PublicCompanionExperience } from '@/components/public/public-companion-experience';
import { API_CONFIG } from '@/lib/config';

const API_BASE = API_CONFIG.BASE_URL;

async function fetchShareMeta(slug: string): Promise<PublicShareMeta> {
  const res = await fetch(`${API_BASE}/public/companions/${slug}/meta`, {
    cache: 'no-store',
    next: { revalidate: 0 },
  });

  if (res.status === 404) {
    notFound();
  }
  if (!res.ok) {
    throw new Error(`Failed to load share meta (${res.status})`);
  }
  return res.json();
}

type PageParams = { slug: string };

export default async function CompanionSharePage({ params }: { params?: Promise<PageParams> }) {
  const resolvedParams = params ? await params : undefined;

  if (!resolvedParams?.slug) {
    notFound();
  }

  const meta = await fetchShareMeta(resolvedParams.slug);
  return <PublicCompanionExperience slug={resolvedParams.slug} meta={meta} />;
}

export const dynamic = 'force-dynamic';
