import CustomSignUp from '@/components/auth/custom-sign-up'
import type { Metadata } from 'next'

export const metadata: Metadata = {
  title: 'Sign Up | Personality Machine',
}

export default function Page() {
  return <CustomSignUp />
}
