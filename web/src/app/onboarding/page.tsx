import { auth } from '@clerk/nextjs/server';
import { redirect } from 'next/navigation';
import type { Metadata } from 'next';
import { OnboardingFlow } from './components';

export const metadata: Metadata = {
  title: 'Onboarding | Personality Machine',
};

export default async function OnboardingPage() {
  const { userId } = await auth();

  if (!userId) {
    redirect('/');
  }

  return <OnboardingFlow />;
}
