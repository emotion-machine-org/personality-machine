import { auth } from '@clerk/nextjs/server';
import AuthLanding from '@/components/auth/auth-landing';
import MemoryV2Testing from '@/components/testing/memory-v2-testing';

export default async function TestMemoryV2Page() {
  const { userId } = await auth();

  if (!userId) {
    return <AuthLanding />;
  }

  return <MemoryV2Testing />;
}
