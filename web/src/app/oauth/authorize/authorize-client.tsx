'use client'

import { useEffect, useMemo, useRef, useState } from 'react'
import { useAuth } from '@clerk/nextjs'
import { useRouter, useSearchParams } from 'next/navigation'
import { API_CONFIG } from '@/lib/config'

const OOB_REDIRECT_URI = 'urn:ietf:wg:oauth:2.0:oob'

type AuthResponse = {
  code: string
  redirect_uri: string
  state?: string | null
  expires_at: string
}

export default function OAuthAuthorizeClient() {
  const searchParams = useSearchParams()
  const router = useRouter()
  const { isLoaded, isSignedIn, getToken } = useAuth()

  const [status, setStatus] = useState<'idle' | 'authorizing' | 'success' | 'error'>('idle')
  const [error, setError] = useState<string>('')
  const [authResponse, setAuthResponse] = useState<AuthResponse | null>(null)
  const [autoRedirect, setAutoRedirect] = useState(false)
  const [showCodeFallback, setShowCodeFallback] = useState(false)
  const [copyState, setCopyState] = useState<'idle' | 'success'>('idle')
  const fallbackTimer = useRef<NodeJS.Timeout | null>(null)

  const query = useMemo(() => {
    return {
      responseType: searchParams.get('response_type') || 'code',
      clientId: searchParams.get('client_id') || '',
      redirectUri: searchParams.get('redirect_uri') || '',
      scope: searchParams.get('scope') || undefined,
      codeChallenge: searchParams.get('code_challenge') || '',
      codeChallengeMethod: searchParams.get('code_challenge_method') || 'S256',
      state: searchParams.get('state') || undefined,
      mode: searchParams.get('mode') || undefined,
      prompt: searchParams.get('prompt') || undefined,
    }
  }, [searchParams])

  const forceCode = useMemo(() => {
    return query.mode === 'code' || query.prompt === 'manual'
  }, [query.mode, query.prompt])

  useEffect(() => {
    if (!isLoaded) return
    if (!isSignedIn) {
      const currentUrl = `/oauth/authorize?${searchParams.toString()}`
      router.replace(`/sign-in?redirect_url=${encodeURIComponent(currentUrl)}`)
    }
  }, [isLoaded, isSignedIn, router, searchParams])

  const missingParams = useMemo(() => {
    const missing: string[] = []
    if (!query.clientId) missing.push('client_id')
    if (!query.redirectUri) missing.push('redirect_uri')
    if (!query.codeChallenge) missing.push('code_challenge')
    return missing
  }, [query])

  const handleAuthorize = async () => {
    if (missingParams.length) {
      setError(`Missing required parameters: ${missingParams.join(', ')}`)
      setStatus('error')
      return
    }

    setStatus('authorizing')
    setError('')

    try {
      const token = await getToken(
        process.env.NEXT_PUBLIC_CLERK_JWT_TEMPLATE
          ? { template: process.env.NEXT_PUBLIC_CLERK_JWT_TEMPLATE }
          : undefined
      )

      if (!token) {
        throw new Error('Unable to authenticate. Please sign in again.')
      }

      const res = await fetch(`${API_CONFIG.BASE_URL}/api/oauth/authorize`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({
          response_type: query.responseType,
          client_id: query.clientId,
          redirect_uri: query.redirectUri,
          scope: query.scope,
          code_challenge: query.codeChallenge,
          code_challenge_method: query.codeChallengeMethod,
          state: query.state,
        }),
      })

      if (!res.ok) {
        const text = await res.text()
        throw new Error(text || `Authorization failed (${res.status})`)
      }

      const data = (await res.json()) as AuthResponse
      setAuthResponse(data)
      setStatus('success')
      const auto = !forceCode && data.redirect_uri !== OOB_REDIRECT_URI
      setAutoRedirect(auto)
      setShowCodeFallback(!auto || forceCode)
      if (auto) {
        if (fallbackTimer.current) clearTimeout(fallbackTimer.current)
        fallbackTimer.current = setTimeout(() => {
          setShowCodeFallback(true)
        }, 20000)
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Authorization failed'
      setError(message)
      setStatus('error')
    }
  }

  useEffect(() => {
    return () => {
      if (fallbackTimer.current) clearTimeout(fallbackTimer.current)
    }
  }, [])

  const redirectUrl = useMemo(() => {
    if (!authResponse || authResponse.redirect_uri === OOB_REDIRECT_URI) return null
    const next = new URL(authResponse.redirect_uri)
    next.searchParams.set('code', authResponse.code)
    if (authResponse.state) next.searchParams.set('state', authResponse.state)
    return next.toString()
  }, [authResponse])

  const handleCopy = async () => {
    if (!authResponse?.code) return
    try {
      await navigator.clipboard.writeText(authResponse.code)
      setCopyState('success')
      setTimeout(() => setCopyState('idle'), 1500)
    } catch {
      // ignore
    }
  }

  return (
    <div className="min-h-screen bg-black flex items-center justify-center">
      <div className="w-full max-w-md px-6 text-center">
        <h1 className="text-white text-3xl font-normal mb-4">Authorize Emotion Machine</h1>
        <p className="text-white/70 text-sm mb-8">
          Allow the CLI to create an API key for your account.
        </p>

        {missingParams.length > 0 && (
          <div className="mb-6 rounded-xl border border-red-500/30 bg-red-500/10 p-4 text-left">
            <p className="text-red-300 text-sm">Missing parameters: {missingParams.join(', ')}</p>
          </div>
        )}

        {status === 'error' && error && (
          <div className="mb-6 rounded-xl border border-red-500/30 bg-red-500/10 p-4 text-left">
            <p className="text-red-300 text-sm">{error}</p>
          </div>
        )}

        {status === 'success' && authResponse && autoRedirect && (
          <div className="mb-6 rounded-xl border border-white/10 bg-white/5 p-5 text-left">
            <h2 className="text-white text-2xl font-normal mb-2">Let’s get things done</h2>
            <p className="text-white/70 text-sm">
              You’re all set up for Emotion Machine Voice. You can close this window.
            </p>
            <p className="text-white/50 text-xs mt-4">
              If your CLI hasn’t advanced in 20 seconds, copy the code below.
            </p>
            <button
              onClick={() => setShowCodeFallback((prev) => !prev)}
              className="mt-3 text-xs text-white/70 underline hover:text-white"
            >
              {showCodeFallback ? 'Hide code' : 'Show code'}
            </button>
          </div>
        )}

        {status === 'success' && authResponse && showCodeFallback && (
          <div className="mb-6 rounded-xl border border-white/10 bg-white/5 p-4 text-left">
            <p className="text-white/80 text-sm mb-2">Authentication code</p>
            <div className="rounded-lg bg-black/60 px-3 py-2 text-white text-sm break-all">
              {authResponse.code}
            </div>
            <div className="mt-3 flex items-center gap-3">
              <button
                onClick={handleCopy}
                className="inline-flex items-center rounded-full bg-white px-3 py-1.5 text-xs font-medium text-black hover:bg-gray-100 transition-colors"
              >
                {copyState === 'success' ? 'Copied' : 'Copy code'}
              </button>
              <span className="text-white/50 text-xs">
                Paste this into your CLI.
              </span>
            </div>
          </div>
        )}

        <button
          onClick={handleAuthorize}
          disabled={status === 'authorizing' || missingParams.length > 0}
          className="w-full bg-white text-black py-3 rounded-full font-medium hover:bg-gray-100 transition-colors disabled:opacity-50"
        >
          {status === 'authorizing' ? 'Authorizing...' : 'Authorize'}
        </button>

        <p className="text-white/40 text-xs mt-6">
          You can close this window after authorization completes.
        </p>
      </div>
      {status === 'success' && redirectUrl && (
        <iframe title="oauth-redirect" src={redirectUrl} className="hidden" />
      )}
    </div>
  )
}
