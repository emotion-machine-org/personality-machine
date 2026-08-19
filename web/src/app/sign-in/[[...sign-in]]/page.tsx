import CustomSignIn from '@/components/auth/custom-sign-in'
import type { Metadata } from 'next'

export const metadata: Metadata = {
  title: 'Sign In | Personality Machine',
}

export default function Page() {
  return <CustomSignIn />
}
