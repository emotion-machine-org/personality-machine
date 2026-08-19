import { auth } from '@clerk/nextjs/server';
import AuthLanding from '@/components/auth/auth-landing';
import BehaviorTesting from '@/components/testing/behavior-testing';

export default async function TestBehaviorsPage() {
  const { userId } = await auth();

  if (!userId) {
    return <AuthLanding />;
  }

  return <BehaviorTesting />;
}
