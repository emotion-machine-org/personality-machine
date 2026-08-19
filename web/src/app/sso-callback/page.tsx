import { AuthenticateWithRedirectCallback } from '@clerk/nextjs'

export const dynamic = 'force-dynamic'

export default function SSOCallback() {
  return (
    <div className="grid min-h-screen place-content-center bg-black text-white">
      <AuthenticateWithRedirectCallback />
      <p className="mt-4 text-sm text-white/70">
        Finalizing sign-in…
      </p>
    </div>
  )
}
