import { auth } from '@clerk/nextjs/server';
import AuthLanding from '@/components/auth/auth-landing';
import ContextEngineTesting from '@/components/testing/context-engine-testing';

export default async function TestContextEnginePage() {
  const { userId } = await auth();

  if (!userId) {
    return <AuthLanding />;
  }

  return <ContextEngineTesting />;
}
