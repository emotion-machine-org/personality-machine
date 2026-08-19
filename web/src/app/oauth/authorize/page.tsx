import { Suspense } from 'react'
import OAuthAuthorizeClient from './authorize-client'

export const dynamic = 'force-dynamic'

export default function OAuthAuthorizePage() {
  return (
    <Suspense
      fallback={
        <div className="min-h-screen bg-black text-white flex items-center justify-center">
          <p className="text-white/70 text-sm">Loading authorization…</p>
        </div>
      }
    >
      <OAuthAuthorizeClient />
    </Suspense>
  )
}
